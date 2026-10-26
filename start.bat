@echo off
rem Start RoleRadar. Double-click this file, or run it from PowerShell.
rem The dashboard opens in your browser; keep this window open while you use it.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo RoleRadar is not set up yet. Running setup first...
    call setup.bat || exit /b 1
)

".venv\Scripts\python.exe" -m roleradar
pause
