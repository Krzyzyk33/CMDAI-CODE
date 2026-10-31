@echo off
setlocal
set PARENT_DIR=%~dp0..
cd /d "%~dp0"
title CMDAI EDITOR - Setup
chcp 65001 >nul 2>&1

set ANIM=%PARENT_DIR%\tools\install_anim.py
set REQ=requirements-editor.txt
set STEPFLAG=%TEMP%\cmdai-editor-install-step.done
set PIPLOG=%TEMP%\cmdai-editor-install-pip.log
set PIPFLAG=%TEMP%\cmdai-editor-install-pip.done

set USE_ANIM=0
if exist "%ANIM%" set USE_ANIM=1

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
for /f "tokens=*" %%v in ('python --version 2^>^&1') do set PY_VER=%%v

del "%STEPFLAG%" >nul 2>&1
if "%USE_ANIM%"=="1" (
    start "" /b cmd /c python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" ^>nul 2^>^&1 ^&^& echo OK ^> "%STEPFLAG%" ^|^| echo FAIL ^> "%STEPFLAG%"
    python "%ANIM%" --label "Checking Python" --header "install cmdai editor" --gap 3 --wait-file "%STEPFLAG%" --min-time 1.2 --timeout 60
) else (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
)
if %ERRORLEVEL% neq 0 (
    echo Error: Python 3.10 or newer is required. Detected: %PY_VER%
    call :pause_if_clicked
    exit /b 1
)
del "%STEPFLAG%" >nul 2>&1

del "%PIPFLAG%" >nul 2>&1
del "%PIPLOG%" >nul 2>&1
if "%USE_ANIM%"=="1" (
    start "" /b cmd /c python -m pip install -r %REQ% --quiet --disable-pip-version-check --log "%PIPLOG%" ^>nul 2^>^&1 ^&^& echo OK ^> "%PIPFLAG%" ^|^| echo FAIL ^> "%PIPFLAG%"
    python "%ANIM%" --label "Installing dependencies" --gap 3 --wait-file "%PIPFLAG%" --min-time 1.0 --timeout 900
) else (
    python -m pip install -r %REQ% --disable-pip-version-check
)
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

python -c "import textual, rich, tree_sitter" >nul 2>&1
set IMPERR=%ERRORLEVEL%
if "%USE_ANIM%"=="1" python "%ANIM%" --label "Verifying packages" --gap 3 --fixed 1.2
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

python -c "import shutil,sys; sys.exit(0 if shutil.which('ruff') else 2)" >nul 2>&1
set RUFFERR=%ERRORLEVEL%
if "%USE_ANIM%"=="1" python "%ANIM%" --label "Checking ruff" --gap 3 --fixed 1.0
if %RUFFERR% neq 0 (
    echo.
    echo [WARNING] ruff missing - PROBLEMS will show syntax errors only.
    echo         pip install ruff
    echo.
) else (
    echo [OK] ruff: Python F rules will work.
)
del "%PIPLOG%" >nul 2>&1

if not exist .cmdai_code_project mkdir .cmdai_code_project
if not exist .cmdai_code_project\checkpoints mkdir .cmdai_code_project\checkpoints
if "%USE_ANIM%"=="1" python "%ANIM%" --label "Preparing workspace" --gap 3 --fixed 1.2
if "%USE_ANIM%"=="0" echo [WARNING] %ANIM% missing - animated steps skipped.

echo.
echo   CMDAI EDITOR
echo.
echo   Run:      editor\run.bat
echo   Or:       python -m editor.main
echo.
echo   Remote install, from any directory:
echo   powershell -NoProfile -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/Krzyzyk33/CMDAI-CODE/main/editor/tools/install-remote.ps1 ^| iex"
echo.
call :pause_if_clicked
endlocal
goto :eof

:pause_if_clicked
echo "%CMDCMDLINE%" | findstr /i /c:"/c" >nul 2>&1
if not errorlevel 1 pause
goto :eof
