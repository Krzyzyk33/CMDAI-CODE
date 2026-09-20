@echo off
setlocal
cd /d "%~dp0"
title CMDAI CODE - Setup
chcp 65001 >nul 2>&1

python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo.
    echo Error: Python not found in PATH.
    echo Install Python 3.10 or newer from https://www.python.org/downloads/
    echo Check "Add Python to PATH" during installation.
    echo.
    call :pause_if_clicked
    exit /b 1
)

python tools/install_logo.py
if %ERRORLEVEL% neq 0 (
    echo.
    echo   CMDAI CODE
    echo.
)

for /f "tokens=*" %%v in ('python --version 2^>^&1') do set PY_VER=%%v
set STEPFLAG=%TEMP%\cmdai-install-step.done
del "%STEPFLAG%" >nul 2>&1
start "" /b cmd /c python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" ^>nul 2^>^&1 ^&^& echo OK ^> "%STEPFLAG%" ^|^| echo FAIL ^> "%STEPFLAG%"
python tools/install_anim.py --label "Checking Python" --header "install cmdai code" --gap 3 --wait-file "%STEPFLAG%" --min-time 1.2 --timeout 60
if %ERRORLEVEL% neq 0 (
    echo Error: Python 3.10 or newer is required. Detected: %PY_VER%
    call :pause_if_clicked
    exit /b 1
)
del "%STEPFLAG%" >nul 2>&1

set PIPLOG=%TEMP%\cmdai-install-pip.log
set PIPFLAG=%TEMP%\cmdai-install-pip.done
del "%PIPFLAG%" >nul 2>&1
del "%PIPLOG%" >nul 2>&1
start "" /b cmd /c python -m pip install -r requirements.txt --quiet --disable-pip-version-check --log "%PIPLOG%" ^>nul 2^>^&1 ^&^& echo OK ^> "%PIPFLAG%" ^|^| echo FAIL ^> "%PIPFLAG%"
python tools/install_anim.py --label "Installing dependencies" --gap 3 --wait-file "%PIPFLAG%" --min-time 1.0 --timeout 900
if %ERRORLEVEL% neq 0 (
    echo Error: dependency installation failed:
    echo.
    type "%PIPLOG%"
    echo.
    echo Fix the errors above and re-run install.bat.
    echo Full log kept at: %PIPLOG%
    call :pause_if_clicked
    exit /b 1
)
del "%PIPFLAG%" >nul 2>&1

python -c "import textual, rich, requests, pydantic, psutil, gguf" >nul 2>&1
set IMPERR=%ERRORLEVEL%
python tools/install_anim.py --label "Verifying packages" --gap 3 --fixed 1.2
if %IMPERR% neq 0 (
    echo Error: dependency check failed - required packages did not import:
    echo.
    type "%PIPLOG%"
    echo.
    echo Fix the errors above and re-run install.bat.
    echo Full log kept at: %PIPLOG%
    call :pause_if_clicked
    exit /b 1
)
del "%PIPLOG%" >nul 2>&1

if not exist config.json (
    if exist config.example.json (
        copy config.example.json config.json >nul
    )
)
python tools/install_anim.py --label "Writing config" --gap 3 --fixed 1.2
if not exist config.json (
    echo Error: config.json missing and no template found. Re-clone the repository.
    call :pause_if_clicked
    exit /b 1
)

if not exist models mkdir models
if not exist logs mkdir logs
if not exist cache mkdir cache
if not exist app\sessions mkdir app\sessions
python tools/install_anim.py --label "Preparing workspace" --gap 3 --fixed 1.2

set LAUNCHLOG=%TEMP%\cmdai-install-launcher.log
set LAUNCHFLAG=%TEMP%\cmdai-install-launcher.done
del "%LAUNCHFLAG%" >nul 2>&1
del "%LAUNCHLOG%" >nul 2>&1
start "" /b cmd /c python cmdai.py --install-launcher ^> "%LAUNCHLOG%" 2^>^&1 ^&^& echo OK ^> "%LAUNCHFLAG%" ^|^| echo FAIL ^> "%LAUNCHFLAG%"
python tools/install_anim.py --label "Registering global command" --footer "To launch the app, type:  cmdai code" --wait-file "%LAUNCHFLAG%" --min-time 1.2 --timeout 120
if %ERRORLEVEL% neq 0 (
    echo Error: launcher registration failed:
    echo.
    type "%LAUNCHLOG%"
    echo.
    echo You can still run .\cmdai.bat locally.
    call :pause_if_clicked
    exit /b 1
)
del "%LAUNCHFLAG%" >nul 2>&1
del "%LAUNCHLOG%" >nul 2>&1
call :pause_if_clicked
endlocal
goto :eof

:pause_if_clicked
echo "%CMDCMDLINE%" | findstr /i /c:"/c" >nul 2>&1
if not errorlevel 1 pause
goto :eof
