@echo off
rem Start RoleRadar on Windows. Double-click this file, or run it from PowerShell.
rem The first run creates a private Python environment and installs the packages.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo First run: creating the Python environment...
    python -m venv .venv || goto :nopython
    ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt || goto :failed
)

".venv\Scripts\python.exe" -m roleradar
pause
exit /b 0

:nopython
echo Python was not found. Install Python 3.11+ from python.org and tick "Add to PATH".
pause
exit /b 1

:failed
echo Installing packages failed. Check your internet connection and run start.bat again.
rmdir /s /q .venv
pause
exit /b 1
