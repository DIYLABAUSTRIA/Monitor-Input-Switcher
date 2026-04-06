@echo off
title MonitorSwitcher — Probe
cd /d "%~dp0"

if not exist "ControlMyMonitor.exe" (
    echo [ERROR] ControlMyMonitor.exe not found.
    echo Download from: https://www.nirsoft.net/utils/control_my_monitor.html
    pause
    exit /b 1
)

echo.
echo MonitorSwitcher — Probe Utility
echo --------------------------------
echo Detecting monitors...
echo.

ControlMyMonitor.exe /smonitors monitors_probe.txt
type monitors_probe.txt

echo.
echo Reading current input (VCP 0x60) on DISPLAY1...
ControlMyMonitor.exe /GetValue "\\.\DISPLAY1\Monitor0" 60

echo.
echo Reading current input (VCP 0x60) on DISPLAY2...
ControlMyMonitor.exe /GetValue "\\.\DISPLAY2\Monitor0" 60

echo.
echo -----------------------------------------------
echo Copy the "Short Monitor ID" value into config.json as "monitor_id"
echo Switch inputs manually and re-run to find correct vcp_value per input
echo -----------------------------------------------
echo.
pause
