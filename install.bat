@echo off
setlocal
cd /d "%~dp0"
title CMDAI CODE - Setup & Installer

echo.
echo =======================================================================
echo    CMDAI CODE - Autonomous Coding Agent for Windows Terminal
echo =======================================================================
echo.
echo [*] Checking Python environment...

python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] Python interpreter not detected in system PATH!
    echo Please install Python 3.10 or newer from: https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('python --version 2^>^&1') do set PY_VER=%%v
echo [OK] Detected: %PY_VER%
echo.

echo [*] Checking and installing required dependencies...
python -m pip install -r requirements.txt --quiet
if %ERRORLEVEL% neq 0 (
    echo [Notice] Retrying package installation without --quiet...
    python -m pip install -r requirements.txt
)
echo [OK] Required dependencies are installed and ready.
echo.

if not exist config.json (
    if exist config.example.json (
        echo [*] Creating default config.json from template...
        copy config.example.json config.json >nul
        echo [OK] Created config.json successfully.
    )
)

echo.
echo =======================================================================
echo    SUCCESS: CMDAI CODE has been successfully installed!
echo =======================================================================
echo.
echo How to launch the application:
echo   - Type: cmdai.bat (or .\cmdai.bat)
echo   - Or:   python cmdai.py
echo   - Code editor: editor.bat
echo.

set /p RUN_NOW="Do you want to run CMDAI CODE now? [Y/N] (default: Y): "
if /i "%RUN_NOW%"=="N" goto :END
if /i "%RUN_NOW%"=="n" goto :END
if /i "%RUN_NOW%"=="NO" goto :END
if /i "%RUN_NOW%"=="no" goto :END

echo.
echo [*] Launching CMDAI CODE...
python cmdai.py

:END
endlocal
