@echo off
echo Konfiguracja CMDAI CODE...

:: Instalacja zaleznosci
echo Instalowanie pakietu cmdai-code...
pip install -e .

:: Dodawanie do PATH
powershell -ExecutionPolicy Bypass -File "%~dp0update_path.ps1"

:: Migracja danych uzytkownika z .cmdai2 do .cmdai_code
if exist "%USERPROFILE%\.cmdai2" (
    echo Migracja starych danych z .cmdai2 do .cmdai_code...
    if not exist "%USERPROFILE%\.cmdai_code" (
        rename "%USERPROFILE%\.cmdai2" ".cmdai_code"
    ) else (
        echo Katalog .cmdai_code juz istnieje, pomijam przenoszenie.
    )
)

echo All set! You can now launch the application by typing:
echo CMDAI CODE
pause
