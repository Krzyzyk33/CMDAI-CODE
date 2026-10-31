@echo off
setlocal
set SCRIPT_DIR=%~dp0
set PARENT_DIR=%SCRIPT_DIR%..
cd /d "%SCRIPT_DIR%"

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
chcp 65001 >nul 2>&1

REM Bez argumentu otwierz folder editor\ - zawiera main.py, wiec zakladka
REM pojawia sie od razu. Wczesniej celem bylo repozytorium, gdzie nie ma
REM main.py ani README.md, wiec edytor wchodzil na ekran powitalny bez
REM zadnych zakladek i wygladal jakby zmiany nie bylo.
REM Uzywamy "%SCRIPT_DIR%." z kropka na koncu: samo "%SCRIPT_DIR%" konczy
REM sie backslashem, a "\" przed cudzyslowem to dla CRT ucieczka.
if "%~1"=="" (set TARGET=%SCRIPT_DIR%.) else (set TARGET=%~1)

if exist "%PARENT_DIR%\.venv\Scripts\python.exe" (
    "%PARENT_DIR%\.venv\Scripts\python.exe" main.py "%TARGET%"
) else if exist "%SCRIPT_DIR%.venv\Scripts\python.exe" (
    "%SCRIPT_DIR%.venv\Scripts\python.exe" main.py "%TARGET%"
) else (
    python main.py "%TARGET%"
)
endlocal
