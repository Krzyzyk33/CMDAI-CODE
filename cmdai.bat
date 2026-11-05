@echo off
setlocal
set SCRIPT_DIR=%~dp0
set "USER_DIR=%CD%"
if "%USER_DIR:~-1%"=="\" set "USER_DIR=%USER_DIR:~0,-1%"
cd /d "%SCRIPT_DIR%"
if exist "%SCRIPT_DIR%.venv\Scripts\python.exe" (
    "%SCRIPT_DIR%.venv\Scripts\python.exe" cmdai.py --workdir "%USER_DIR%" %*
) else (
    python cmdai.py --workdir "%USER_DIR%" %*
)
endlocal
