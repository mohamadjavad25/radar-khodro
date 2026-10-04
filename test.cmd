@echo off
setlocal enabledelayedexpansion
pushd "%~dp0"

set "PYTHONPATH=%CD%\src;%CD%\tests"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

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
    ) do (
        if not defined PICKED (
            if exist %%P ( %%P -c "import playwright" >nul 2>&1 && set "PICKED=%%~P" )
        )
    )
)
if not defined PICKED (
    for %%P in (python.exe python3.exe) do (
        if not defined PICKED (
            where %%P >nul 2>&1 && (
                for /f "delims=" %%X in ('where %%P') do (
                    if not defined PICKED ( %%X -c "import playwright" >nul 2>&1 && set "PICKED=%%X" )
                )
            )
        )
    )
)
if not defined PICKED set "PICKED=python"

"%PICKED%" -m unittest discover -s "%CD%\tests" -t "%CD%\tests" %*
set "RC=%ERRORLEVEL%"
popd
endlocal & exit /b %RC%
