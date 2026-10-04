@echo off
setlocal enabledelayedexpansion
pushd "%~dp0"

set "PYTHONPATH=%CD%\src"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

rem ---- pick a Python that actually has playwright ----
set "PICKED="

if defined PYTHON (
    if exist "%PYTHON%" (
        "%PYTHON%" -c "import playwright" >nul 2>&1 && set "PICKED=%PYTHON%"
    )
)

if not defined PICKED (
    for %%P in (
        "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
        "%ProgramFiles%\Python313\python.exe"
        "%ProgramFiles%\Python312\python.exe"
    ) do (
        if not defined PICKED (
            if exist %%P (
                %%P -c "import playwright" >nul 2>&1 && set "PICKED=%%~P"
            )
        )
    )
)

if not defined PICKED (
    for %%P in (python.exe python3.exe) do (
        if not defined PICKED (
            where %%P >nul 2>&1 && (
                for /f "delims=" %%X in ('where %%P') do (
                    if not defined PICKED (
                        %%X -c "import playwright" >nul 2>&1 && set "PICKED=%%X"
                    )
                )
            )
        )
    )
)

if not defined PICKED (
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
        set "PICKED=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
        echo [warn] playwright not found in any interpreter; using Python 3.12.
        echo [warn] run:  pip install playwright
    )
)

if not defined PICKED (
    echo [error] No usable Python interpreter found.
    echo         Set it explicitly:  set PYTHON=C:\path\to\python.exe
    popd
    endlocal
    exit /b 1
)

"%PICKED%" -m pride_radar %*
set "RC=%ERRORLEVEL%"
popd
endlocal & exit /b %RC%
