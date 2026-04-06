#!/usr/bin/env python3
"""
MonitorSwitcher - Cross-platform web-based DDC/CI monitor input switcher
https://github.com/DIYLABAUSTRIA/Monitor-Input-Switcher/

Supported backends:
  Windows : ControlMyMonitor.exe (NirSoft)
  macOS   : m1ddc (Apple Silicon) or ddcctl (Intel)
  Linux   : ddcutil
"""

from __future__ import annotations  # fix: str | None on Python 3.8/3.9

import http.server
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

# ── Paths ────────────────────────────────────────────────────────────────────

BASE_DIR    = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"

# ── Config loading & validation ───────────────────────────────────────────────

def load_config() -> dict:
    if not CONFIG_FILE.exists():
        _die(
            f"config.json not found at {CONFIG_FILE}\n"
            "        Copy config.example.json to config.json and edit it."
        )
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    except json.JSONDecodeError as e:
        _die(f"config.json is not valid JSON: {e}")

    cfg.pop("notes", None)  # strip comment keys

    # Validate port
    port = cfg.get("port", 5757)
    if not isinstance(port, int) or not (1024 <= port <= 65535):
        _die(f"config.json: 'port' must be an integer between 1024 and 65535, got: {port!r}")

    # Warn if monitor_id is missing or empty
    mid = cfg.get("monitor_id", "")
    if not mid:
        print("[WARNING] config.json: 'monitor_id' is empty — will use first monitor found.")

    # Validate inputs
    inputs = cfg.get("inputs", {})
    if not inputs:
        _die("config.json: 'inputs' is empty — define at least one input.")
    for key, inp in inputs.items():
        if not re.match(r'^[a-zA-Z0-9_-]+$', key):
            _die(f"config.json: input key {key!r} must be alphanumeric (a-z, 0-9, _, -)")
        vcp = inp.get("vcp_value")
        if vcp is None:
            _die(f"config.json: input {key!r} is missing 'vcp_value'")
        if not isinstance(vcp, int) or not (0 <= vcp <= 255):
            _die(f"config.json: input {key!r} vcp_value must be an integer 0–255, got: {vcp!r}")

    return cfg

def _die(msg: str):
    print(f"[ERROR] {msg}")
    sys.exit(1)

CFG              = load_config()
PORT             = CFG.get("port", 5757)
TARGET_MONITOR_ID = CFG.get("monitor_id", "")
INPUTS           = CFG.get("inputs", {})

# ── OS Detection ─────────────────────────────────────────────────────────────

OS = platform.system()  # "Windows", "Darwin", "Linux"

# ── Backend: Windows (ControlMyMonitor) ──────────────────────────────────────

CMM_EXE = str(BASE_DIR / "ControlMyMonitor.exe")

def _cmm_find_monitor() -> str | None:
    if not os.path.exists(CMM_EXE):
        return None
    # Use a proper temp file to avoid collisions
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".txt", prefix="ms_monitors_")
    os.close(tmp_fd)
    tmp = Path(tmp_path)
    try:
        subprocess.run([CMM_EXE, "/smonitors", str(tmp)],
                       capture_output=True, timeout=10)
        if not tmp.exists() or tmp.stat().st_size == 0:
            return None
        raw = tmp.read_bytes()
        try:
            text = raw.decode("utf-16")
        except Exception:
            text = raw.decode("utf-8", errors="ignore")
        blocks = re.split(r"\n\s*\n", text.strip())
        for block in blocks:
            # If monitor_id is set, match it; otherwise take first block
            if not TARGET_MONITOR_ID or TARGET_MONITOR_ID in block:
                m = re.search(r'Monitor Device Name:\s*"([^"]+)"', block)
                if m:
                    return m.group(1)
    except Exception as e:
        print(f"  [cmm_find_monitor] {e}")
    finally:
        tmp.unlink(missing_ok=True)  # always clean up
    return None

def _cmm_set(monitor_device: str, vcp_value: int) -> dict:
    cmd = [CMM_EXE, "/SetValue", monitor_device, "60", str(vcp_value)]
    print(f"  CMD: {' '.join(cmd)}")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        out = (r.stdout + r.stderr).strip()
        print(f"  OUT: {repr(out)}")
        return {"ok": True, "output": out}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Command timed out"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def _cmm_get(monitor_device: str) -> dict:
    cmd = [CMM_EXE, "/GetValue", monitor_device, "60"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return {"ok": True, "output": (r.stdout + r.stderr).strip()}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ── Backend: macOS (m1ddc / ddcctl) ──────────────────────────────────────────

def _macos_tool() -> str | None:
    for tool in ("m1ddc", "ddcctl"):
        if shutil.which(tool):
            return tool
    return None

def _macos_find_monitor() -> str | None:
    tool = _macos_tool()
    if not tool:
        return None
    try:
        if tool == "m1ddc":
            r = subprocess.run(["m1ddc", "display", "list"],
                                capture_output=True, text=True, timeout=10)
            first_match = None
            for line in r.stdout.splitlines():
                m = re.search(r"Display\s+(\d+)", line, re.IGNORECASE)
                if m:
                    if first_match is None:
                        first_match = m.group(1)  # fallback
                    if TARGET_MONITOR_ID and TARGET_MONITOR_ID.lower() in line.lower():
                        return m.group(1)
            return first_match  # None if no displays at all
        else:  # ddcctl: 0-based index, no list command
            return "0"
    except Exception as e:
        print(f"  [macos_find_monitor] {e}")
    return None

def _macos_set(display_id: str, vcp_value: int) -> dict:
    tool = _macos_tool()
    if not tool:
        return {"ok": False, "error": "No DDC tool found. Install m1ddc (Apple Silicon) or ddcctl (Intel). See README."}
    try:
        if tool == "m1ddc":
            cmd = ["m1ddc", "display", display_id, "set", "input", str(vcp_value)]
        else:
            cmd = ["ddcctl", "-d", display_id, "-i", str(vcp_value)]
        print(f"  CMD: {' '.join(cmd)}")
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        out = (r.stdout + r.stderr).strip()
        print(f"  OUT: {repr(out)}")
        return {"ok": True, "output": out}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Command timed out"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def _macos_get(display_id: str) -> dict:
    tool = _macos_tool()
    if not tool:
        return {"ok": False, "error": "No DDC tool found"}
    try:
        if tool == "m1ddc":
            cmd = ["m1ddc", "display", display_id, "get", "input"]
        else:
            cmd = ["ddcctl", "-d", display_id, "-i", "?"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return {"ok": True, "output": (r.stdout + r.stderr).strip()}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ── Backend: Linux (ddcutil) ──────────────────────────────────────────────────

def _linux_find_monitor() -> str | None:
    if not shutil.which("ddcutil"):
        return None
    try:
        r = subprocess.run(["ddcutil", "detect", "--brief"],
                            capture_output=True, text=True, timeout=10)
        lines = r.stdout.splitlines()
        current_display = None
        first_display   = None
        for line in lines:
            m = re.match(r"\s*Display\s+(\d+)", line, re.IGNORECASE)
            if m:
                current_display = m.group(1)
                if first_display is None:
                    first_display = current_display
            if TARGET_MONITOR_ID and TARGET_MONITOR_ID.lower() in line.lower():
                if current_display:
                    return current_display
        return first_display  # None if no displays detected
    except Exception as e:
        print(f"  [linux_find_monitor] {e}")
    return None

def _linux_set(display_id: str, vcp_value: int) -> dict:
    if not shutil.which("ddcutil"):
        return {"ok": False, "error": "ddcutil not found. Install: sudo apt install ddcutil"}
    cmd = ["ddcutil", "--display", display_id, "setvcp", "60", str(vcp_value)]
    print(f"  CMD: {' '.join(cmd)}")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        out = (r.stdout + r.stderr).strip()
        print(f"  OUT: {repr(out)}")
        return {"ok": True, "output": out}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Command timed out"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def _linux_get(display_id: str) -> dict:
    if not shutil.which("ddcutil"):
        return {"ok": False, "error": "ddcutil not found"}
    try:
        r = subprocess.run(["ddcutil", "--display", display_id, "getvcp", "60"],
                            capture_output=True, text=True, timeout=10)
        return {"ok": True, "output": (r.stdout + r.stderr).strip()}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ── Unified API ───────────────────────────────────────────────────────────────

_monitor_handle: str | None = None

def find_monitor() -> str | None:
    if OS == "Windows": return _cmm_find_monitor()
    if OS == "Darwin":  return _macos_find_monitor()
    if OS == "Linux":   return _linux_find_monitor()
    return None

def get_monitor_handle() -> str | None:
    global _monitor_handle
    if _monitor_handle is None:
        _monitor_handle = find_monitor()
        if _monitor_handle:
            print(f"  Monitor handle: {_monitor_handle} (OS: {OS})")
        else:
            print(f"  [WARNING] Monitor not found")
    return _monitor_handle

def invalidate_monitor_cache():
    global _monitor_handle
    _monitor_handle = None

def set_input(vcp_value: int) -> dict:
    handle = get_monitor_handle()
    if not handle:
        # Re-detect once — display numbering may have changed
        invalidate_monitor_cache()
        handle = get_monitor_handle()
    if not handle:
        return {"ok": False, "error": f"Monitor not found (target: {TARGET_MONITOR_ID or 'any'})"}
    if OS == "Windows": result = _cmm_set(handle, vcp_value)
    elif OS == "Darwin": result = _macos_set(handle, vcp_value)
    elif OS == "Linux":  result = _linux_set(handle, vcp_value)
    else: result = {"ok": False, "error": f"Unsupported OS: {OS}"}
    # On failure, invalidate so next call re-detects
    if not result.get("ok"):
        invalidate_monitor_cache()
    return result

def get_input() -> dict:
    handle = get_monitor_handle()
    if not handle:
        return {"ok": False, "error": "Monitor not found"}
    if OS == "Windows": return _cmm_get(handle)
    if OS == "Darwin":  return _macos_get(handle)
    if OS == "Linux":   return _linux_get(handle)
    return {"ok": False, "error": f"Unsupported OS: {OS}"}

def switch_input(key: str) -> dict:
    inp = INPUTS.get(key)
    if not inp:
        return {"ok": False, "error": f"Unknown input: {key!r}"}
    vcp = inp.get("vcp_value")  # already validated as int at startup
    return set_input(vcp)

# ── UI caching ────────────────────────────────────────────────────────────────

_ui_cache: str | None = None

def build_ui() -> str:
    global _ui_cache
    if _ui_cache is not None:
        return _ui_cache
    ui_file = BASE_DIR / "index.html"
    if not ui_file.exists():
        return "<h1>index.html not found — check your installation</h1>"
    template = ui_file.read_text(encoding="utf-8")
    colors = ["#00e5ff", "#ff6b35", "#a259ff", "#00ff88", "#ffcc00"]
    buttons = [
        {
            "key":      key,
            "label":    inp.get("label", key),
            "subtitle": inp.get("subtitle", ""),
            "icon":     inp.get("icon", "🖥"),
            "tag":      inp.get("tag", key.upper()),
            "color":    colors[i % len(colors)],
            "shortcut": str(i + 1),
        }
        for i, (key, inp) in enumerate(INPUTS.items())
    ]
    _ui_cache = template.replace("__BUTTONS_JSON__", json.dumps(buttons))
    return _ui_cache

# ── HTTP Handler ──────────────────────────────────────────────────────────────

# Allowlist for input keys — defense in depth, keys are already validated at
# config load time, but also sanitize here so route parsing is never surprising.
_KEY_RE = re.compile(r'^[a-zA-Z0-9_-]{1,32}$')

class SwitcherHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, format, *args):  # noqa: A002
        print(f"  {self.address_string()} {format % args}")

    def send_json(self, data: dict, status: int = 200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, content: str, status: int = 200):
        body = content.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _path(self) -> str:
        """Return path component only, stripping query string and fragment."""
        return urlparse(self.path).path

    def _drain_body(self):
        """Read and discard request body to prevent connection issues."""
        length = int(self.headers.get("Content-Length", 0) or 0)
        if 0 < length <= 8192:
            self.rfile.read(length)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = self._path()
        if path in ("/", "/index.html"):
            self.send_html(build_ui())
        elif path == "/status":
            self.send_json({**get_input(), "os": OS, "monitor": get_monitor_handle()})
        elif path == "/config":
            self.send_json({"inputs": INPUTS, "monitor_id": TARGET_MONITOR_ID, "os": OS})
        elif path == "/detect":
            invalidate_monitor_cache()
            handle = get_monitor_handle()
            self.send_json({"monitor_handle": handle, "os": OS, "target": TARGET_MONITOR_ID})
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        path = self._path()
        self._drain_body()
        if path.startswith("/switch/"):
            raw_key = path[len("/switch/"):]
            # Sanitize: only allow safe key characters
            if not _KEY_RE.match(raw_key):
                self.send_json({"error": "Invalid input key"}, 400)
                return
            self.send_json(switch_input(raw_key.lower()))
        else:
            self.send_json({"error": "Not found"}, 404)

# ── Startup helpers ───────────────────────────────────────────────────────────

def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"

def check_backend():
    if OS == "Windows":
        if not os.path.exists(CMM_EXE):
            print("  [WARNING] ControlMyMonitor.exe not found in folder.")
            print("            Download: https://www.nirsoft.net/utils/control_my_monitor.html")
    elif OS == "Darwin":
        if not _macos_tool():
            print("  [WARNING] No DDC tool found.")
            print("            Apple Silicon: brew install m1ddc")
            print("            Intel Mac:     brew install ddcctl")
    elif OS == "Linux":
        if not shutil.which("ddcutil"):
            print("  [WARNING] ddcutil not found.")
            print("            Install: sudo apt install ddcutil")
            print("            Then:    sudo usermod -aG i2c $USER  (re-login after)")

def main():
    ip = get_local_ip()
    check_backend()
    url_local   = f"http://localhost:{PORT}"
    url_network = f"http://{ip}:{PORT}"
    print(f"""
╔══════════════════════════════════════════════╗
║           MonitorSwitcher v1.0               ║
╠══════════════════════════════════════════════╣
║  Local:   {url_local:<36}║
║  Network: {url_network:<36}║
╠══════════════════════════════════════════════╣
║  OS:      {OS:<36}║
║  Monitor: {(TARGET_MONITOR_ID or "(any — first found)"):<36}║
╠══════════════════════════════════════════════╣
║  ⚠  Accessible to all devices on your LAN   ║
║     Do not run on untrusted networks         ║
╚══════════════════════════════════════════════╝
""")
    handle = get_monitor_handle()
    if handle:
        print(f"  ✓ Monitor found: {handle}\n")
    else:
        print(f"  ✗ Monitor not found — will retry on first switch\n")

    server = http.server.HTTPServer(("0.0.0.0", PORT), SwitcherHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopping...")
    finally:
        server.server_close()
        print("  Stopped.")

if __name__ == "__main__":
    main()
