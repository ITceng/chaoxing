@echo off
chcp 65001 >nul
cd /d "%~dp0"
title ZHS Fucker Panel

set "PY="
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    echo Python 3.10+ was not found. Please install Python and add it to PATH.
    pause
    exit /b 1
)

%PY% -c "import flask, requests, Crypto, PIL, openai, tiktoken" >nul 2>nul
if errorlevel 1 (
    echo Installing dependencies...
    %PY% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Failed to install dependencies.
        pause
        exit /b 1
    )
)

echo Starting panel at http://127.0.0.1:5000
echo Close this window to stop the server.
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:5000"
%PY% panel.py
echo.
echo Panel stopped.
pause
