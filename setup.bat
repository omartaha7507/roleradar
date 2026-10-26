@echo off
setlocal EnableDelayedExpansion
rem RoleRadar one-time setup for Windows. Safe to run again: finished steps are skipped.
cd /d "%~dp0"
echo.
echo  RoleRadar setup
echo  ===============
echo.

rem --- 1. Python ------------------------------------------------------------------
where python >nul 2>&1
if errorlevel 1 (
    echo [!] Python was not found.
    echo     Install Python 3.11 or newer from https://www.python.org/downloads/
    echo     and tick "Add python.exe to PATH" in the installer. Then run setup.bat again.
    pause
    exit /b 1
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
    echo [!] RoleRadar needs Python 3.11 or newer. Please update Python and run setup.bat again.
    pause
    exit /b 1
)
echo [1/6] Python found.

rem --- 2. Python packages in a private environment ---------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo [2/6] Creating the Python environment...
    python -m venv .venv || goto :failed
)
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt || goto :failed
echo [2/6] Python packages installed.

rem --- 3. Settings file ---------------------------------------------------------------------
if not exist "settings.env" copy /y "settings.example.env" "settings.env" >nul
echo [3/6] Settings: settings.env (open it to change the AI model or port).

rem --- 4. Ollama and the ranking model ----------------------------------------------------------
for /f "tokens=1,* delims==" %%a in ('findstr /b "ROLERADAR_OLLAMA_MODEL=" settings.env') do set MODEL=%%b
if "!MODEL!"=="" set MODEL=llama3.1:8b
where ollama >nul 2>&1
if errorlevel 1 (
    echo [4/6] Ollama ^(runs the local AI^) is not installed.
    where winget >nul 2>&1
    if not errorlevel 1 (
        set /p INSTALL="      Install it now with winget? [Y/n] "
        if /i not "!INSTALL!"=="n" (
            winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements
            echo       Close this window, open a NEW one, and run setup.bat again so Windows finds Ollama.
            pause
            exit /b 0
        )
    )
    echo       Download it from https://ollama.com, install it, then run setup.bat again.
) else (
    echo [4/6] Ollama found. Downloading the model !MODEL! ^(a few GB the first time^)...
    ollama pull !MODEL!
)

rem --- 5. Claude Code, which writes the CVs ---------------------------------------------------------
where claude >nul 2>&1
if errorlevel 1 (
    echo [5/6] Claude Code ^(writes the CVs^) is not installed.
    where npm >nul 2>&1
    if not errorlevel 1 (
        set /p INSTALL="      Install it now with npm? [Y/n] "
        if /i not "!INSTALL!"=="n" call npm install -g @anthropic-ai/claude-code
    ) else (
        echo       Install it as described in the README ^(section "Claude Code"^).
    )
    echo       Then open a terminal, type: claude   and sign in once.
) else (
    echo [5/6] Claude Code found. If you have never signed in, type: claude   in a terminal once.
)

rem --- 6. Your profile -----------------------------------------------------------------------------------
set HASPROFILE=
for /d %%d in (profiles\*) do (
    if /i not "%%~nxd"=="example" if exist "%%d\profile.yaml" set HASPROFILE=1
)
if not defined HASPROFILE (
    set /p PNAME="[6/6] Your profile name (lowercase, e.g. omar): "
    if not "!PNAME!"=="" ".venv\Scripts\python.exe" -m roleradar new-profile !PNAME!
) else (
    echo [6/6] Profile found.
)

echo.
echo  Almost done. Two things only you can do:
echo.
echo  A. Put your real details in profiles\^<your-name^>\ ^(profile.yaml, identity.md, projects.md^).
echo.
echo  B. Add the Chrome extension:
echo     1. In Chrome, open:  chrome://extensions
echo     2. Turn on "Developer mode" ^(top right^)
echo     3. Click "Load unpacked" and choose this folder:
echo        %~dp0extension
echo.
echo  Checking what is ready:
".venv\Scripts\python.exe" -m roleradar check
echo.
echo  Start RoleRadar any time with start.bat
pause
exit /b 0

:failed
echo [!] Installing the Python packages failed. Check your internet connection and run setup.bat again.
pause
exit /b 1
