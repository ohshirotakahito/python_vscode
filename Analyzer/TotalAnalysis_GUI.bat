@echo off
rem Total Analysis (Python) GUI
set "PYW=%USERPROFILE%\anaconda3\pythonw.exe"
if not exist "%PYW%" (
    echo pythonw.exe not found: %PYW%
    pause
    exit /b 1
)
start "" "%PYW%" "%~dp0python\gui_app.py"
