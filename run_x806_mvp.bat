@echo off
cd /d "%~dp0"
py -m pip show pywinusb >nul 2>&1
if errorlevel 1 (
    echo Installing pywinusb...
    py -m pip install pywinusb
)
py x806_configurator_mvp.py
pause
