#!/bin/bash
cd "$(dirname "$0")"

if ! command -v python3 &>/dev/null; then
    echo "[ERROR] Python 3 not found."
    echo "  macOS: brew install python"
    echo "  Linux: sudo apt install python3"
    exit 1
fi

if [ ! -f "config.json" ]; then
    echo "[INFO] config.json not found — copying from config.example.json"
    cp config.example.json config.json
    echo "Edit config.json with your monitor ID and inputs, then run again."
    exit 1
fi

# Check DDC backend
if [[ "$OSTYPE" == "darwin"* ]]; then
    if ! command -v m1ddc &>/dev/null && ! command -v ddcctl &>/dev/null; then
        echo "[WARNING] No DDC tool found."
        echo "  Apple Silicon: brew install m1ddc"
        echo "  Intel Mac:     brew install ddcctl"
    fi
elif [[ "$OSTYPE" == "linux"* ]]; then
    if ! command -v ddcutil &>/dev/null; then
        echo "[WARNING] ddcutil not found."
        echo "  Install: sudo apt install ddcutil"
        echo "  Then:    sudo usermod -aG i2c \$USER  (re-login after)"
    fi
fi

echo "Starting MonitorSwitcher at http://localhost:5757 ..."
python3 server.py
