@echo off
rem ===========================================================================
rem  Car Radar - double-click launcher
rem
rem  Works from any folder and on any Windows machine: it finds a usable
rem  Python, installs what is missing, then starts the interface and opens
rem  the browser. Close this window (or press Ctrl+C) to stop the server.
rem ===========================================================================
setlocal EnableExtensions EnableDelayedExpansion
title Car Radar
cd /d "%~dp0"

set "PYTHONPATH=%CD%\src"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PORT=8765"

echo.
echo   Car Radar
echo   ---------------------------------------------
echo.

rem --------------------------------------------------------------- project
if not exist "config.json" (
  if exist "config.example.json" (
    copy /y "config.example.json" "config.json" >nul
    echo   [ok]   config.json created from the example
  ) else (
    echo   [error] config.example.json is missing - the folder is incomplete.
    goto :fail
  )
)
if not exist "data" mkdir "data" >nul 2>&1
if not exist "src\pride_radar\__main__.py" (
  echo   [error] src\pride_radar is missing - the folder is incomplete.
  goto :fail
)

rem --------------------------------------------------------------- python
rem PICKED is always a real path to python.exe, never a launcher with flags,
rem so that quoting stays correct when the folder path contains spaces.
set "PICKED="
if defined PYTHON if exist "%PYTHON%" call :probe "%PYTHON%"
for %%P in (
  "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python39\python.exe"
  "%ProgramFiles%\Python314\python.exe"
  "%ProgramFiles%\Python313\python.exe"
  "%ProgramFiles%\Python312\python.exe"
  "%ProgramFiles%\Python311\python.exe"
  "%ProgramFiles%\Python310\python.exe"
  "%ProgramFiles%\Python39\python.exe"
) do (
  if not defined PICKED (
    if exist %%P call :probe "%%~P"
  )
)
if not defined PICKED call :probe_py
if not defined PICKED for %%P in (python.exe python3.exe) do (
  if not defined PICKED call :probe_path %%P
)

if not defined PICKED (
  echo   [error] No Python 3.9+ found on this computer.
  echo.
  echo     Install Python from https://www.python.org/downloads/
  echo     Tick "Add python.exe to PATH" during setup, then run this file again.
  goto :fail
)
echo   [ok]   Python: %PICKED%

rem --------------------------------------------------------------- packages
"%PICKED%" -c "import playwright" >nul 2>&1
if errorlevel 1 (
  echo   [need] installing the playwright package ...
  "%PICKED%" -m pip install --disable-pip-version-check -q -r requirements.txt
  if errorlevel 1 (
    echo   [error] pip install failed. Try running this window as Administrator.
    goto :fail
  )
  echo   [ok]   playwright installed
)

rem the browser binary is a separate download and is missing on a fresh machine
if not exist "%LOCALAPPDATA%\ms-playwright" (
  echo   [need] downloading the browser engine, one time only ...
  "%PICKED%" -m playwright install chromium
  if errorlevel 1 (
    echo   [warn] browser download failed; falling back to Chrome or Edge if present.
  )
)

rem --------------------------------------------------------------- free port
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
  echo   [ok]   stopping an old instance on port %PORT%
  taskkill /f /pid %%P >nul 2>&1
  timeout /t 1 /nobreak >nul
)

rem --------------------------------------------------------------- run
echo   [ok]   starting on http://127.0.0.1:%PORT%
echo.
echo   Leave this window open. Press Ctrl+C to stop.
echo.
"%PICKED%" -m pride_radar web --port %PORT%
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo   [error] the server stopped with code %RC%.
  goto :fail
)
popd & endlocal & exit /b 0

rem ================================================================ helpers
:probe
if defined PICKED goto :eof
if not exist "%~1" goto :eof
"%~1" -c "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
if not errorlevel 1 set "PICKED=%~1"
goto :eof

:probe_py
rem the py launcher can exist without python.exe on PATH; ask it where it is
if defined PICKED goto :eof
where py >nul 2>&1
if errorlevel 1 goto :eof
for /f "delims=" %%X in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do (
  if not defined PICKED call :probe "%%X"
)
goto :eof

:probe_path
if defined PICKED goto :eof
for /f "delims=" %%X in ('where %~1 2^>nul') do (
  if not defined PICKED call :probe "%%X"
)
goto :eof

:fail
echo.
pause
popd & endlocal & exit /b 1
