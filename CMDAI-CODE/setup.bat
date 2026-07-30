@echo off
title Setup CMDAI CODE
echo =========================================
echo       Konfiguracja CMDAI CODE
echo =========================================
echo.

:: Uruchomienie skryptu PowerShell do instalacji i konfiguracji PATH
powershell -ExecutionPolicy Bypass -File "%~dp0update_path.ps1"

:: Migracja danych uzytkownika z .cmdai2 do .cmdai_code (jesli dotyczy)
if exist "%USERPROFILE%\.cmdai2" (
    echo.
    echo Migracja danych z .cmdai2 do .cmdai_code...
    if not exist "%USERPROFILE%\.cmdai_code" (
        rename "%USERPROFILE%\.cmdai2" ".cmdai_code"
    )
)

echo.
echo Gotowe! Mozesz wpisac: cmdai-code
pause
