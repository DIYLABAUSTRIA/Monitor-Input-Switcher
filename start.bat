@echo off
title MonitorSwitcher
cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found.
    echo Download from https://python.org — check "Add to PATH" during install.
    pause
    exit /b 1
)

if not exist "ControlMyMonitor.exe" (
    echo [WARNING] ControlMyMonitor.exe not found.
    echo Download from: https://www.nirsoft.net/utils/control_my_monitor.html
    echo Place ControlMyMonitor.exe in this folder, then restart.
    echo.
)

if not exist "config.json" (
    echo [INFO] config.json not found — copying from config.example.json
    copy config.example.json config.json
    echo Edit config.json with your monitor ID and inputs, then restart.
    pause
    exit /b 1
)

start /b cmd /c "timeout /t 2 >nul && start http://localhost:5757"
python server.py
pause
