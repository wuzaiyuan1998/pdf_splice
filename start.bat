@echo off
setlocal
cd /d "%~dp0"
title PDF Splice Tool

set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PY%" (
    echo [WARN] bundled Python not found - falling back to "python" from PATH
    set "PY=python"
)

"%PY%" -c "import flask, pymupdf" >nul 2>nul
if errorlevel 1 (
    echo.
    echo [ERROR] Cannot start: this Python is missing "flask" or "pymupdf".
    echo         Python used: %PY%
    echo         Fix: "%PY%" -m pip install -r "%~dp0requirements.txt"
    echo.
) else (
    echo Starting PDF splice tool - the browser will open automatically.
    echo.
    "%PY%" "%~dp0app.py" %*
)

echo.
pause
