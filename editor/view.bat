@echo off
REM ============================================================
REM   CMDAI CODE - Editor launcher
REM
REM   Uzycie:
REM     view.bat                 - otwiera folder editor\ (zakladka od razu)
REM     view.bat <sciezka>       - otwiera wskazany plik lub folder
REM     view.bat --check         - tylko sprawdza srodowisko, nie uruchamia
REM
REM   UWAGA: tekst celowo bez polskich znakow diakrytycznych.
REM   Batch czyta plik w biezacej stronie kodowej, a "chcp 65001"
REM   ustawiane w srodku skryptu powoduje krzaczki.
REM ============================================================

setlocal
chcp 65001 >nul 2>&1
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

REM ROOT konczy sie backslashem - uzywany tylko dla %ROOT%.. i dopisywania
REM nazwy pliku ("\" przed zamknieciem cudzyslowu to dla CRT ucieczka "\").
REM DIR jest bez koncowego backslasza - jedyne co trafia do argumentow.
set "ROOT=%~dp0"
set "DIR=%ROOT:~0,-1%"

set "MODE=run"
set "TARGET=%~1"
if /i "%TARGET%"=="--check" set "MODE=check" & set "TARGET="

echo ============================================================
echo   CMDAI CODE  -  Editor
echo ============================================================
echo.

REM --- 1. znajdz interpreter ---------------------------------
set "PY="
if exist "%ROOT%..\.venv\Scripts\python.exe" set "PY=%ROOT%..\.venv\Scripts\python.exe"
if not defined PY if exist "%ROOT%.venv\Scripts\python.exe" set "PY=%ROOT%.venv\Scripts\python.exe"
if not defined PY for /f "delims=" %%p in ('where python 2^>nul') do if not defined PY set "PY=%%p"

if not defined PY goto :no_python

if not defined TARGET set "TARGET=%DIR%"

REM --- 2. weryfikacja ----------------------------------------
if not exist "%DIR%\main.py" goto :no_main

echo   Python     : %PY%
echo   Folder     : %DIR%
echo   Otwarty    : %TARGET%
echo.

if exist "%DIR%\icons\font\cmdai-icons.ttf" goto :font_ok
echo   [INFO] Nie zbudowano fontu ikon (brak cmdai-icons.ttf).
echo          Zbuduj go:  python "%DIR%\icons\build_font.py"
echo.
goto :font_done

:font_ok
echo   [INFO] Font ikon jest ZBUDOWANY, ale NIE zainstalowany w Windows.
echo          Bez instalacji tryb SVG (Ctrl+I) pokazuje puste kwadraty.
echo          Instalacja: dwuklik na
echo                     %DIR%\icons\font\cmdai-icons.ttf
echo          wybierz "Instaluj dla wszystkich uzytkownikow",
echo          potem zamknij i otworz terminal ponownie.
echo.

:font_done
if /i "%MODE%"=="check" goto :check_ok

REM --- 3. uruchom ---------------------------------------------
where wt.exe >nul 2>&1
if errorlevel 1 goto :plain

REM Windows Terminal - lepszy rendering i obsluga myszy
start "" wt.exe -d "%DIR%" --title "CMDAI CODE Editor" "%PY%" "%DIR%\main.py" "%TARGET%"
endlocal
exit /b 0

:plain
cd /d "%DIR%"
"%PY%" "%DIR%\main.py" "%TARGET%"
set "CODE=%errorlevel%"
if not "%CODE%"=="0" goto :crash
endlocal
exit /b 0

:check_ok
echo   [OK] Wszystko gotowe. Uruchom ponownie bez --check.
echo.
pause
endlocal
exit /b 0

:no_python
echo   [BLAD] Nie znaleziono Pythona.
echo          Zainstaluj Python 3.12+ i dodaj go do PATH,
echo          albo utworz .venv w katalogu repozytorium.
echo.
pause
endlocal
exit /b 1

:no_main
echo   [BLAD] Nie znaleziono pliku "%DIR%\main.py"
echo          Uruchom view.bat z katalogu editor\.
echo.
pause
endlocal
exit /b 1

:crash
echo.
echo   [KONIEC] Program zakonczyl sie bledem (kod %CODE%).
echo          Pelny traceback jest powyzej.
echo.
pause
endlocal
exit /b %CODE%
