@echo off
title cfbroot
REM Double-click this to start the app and open it in your browser.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Could not find .venv - run these two commands once, from this folder:
    echo     python -m venv .venv
    echo     .venv\Scripts\python -m pip install -e .
    echo.
    pause
    exit /b 1
)

if not exist ".env" (
    echo WARNING: no .env file found, so the app will show fabricated demo data.
    echo Create a file called .env in this folder containing:
    echo     CFBD_API_KEY=your_key_here
    echo.
)

echo Starting cfbroot on http://127.0.0.1:8000
echo Your browser will open in a few seconds.
echo.
echo KEEP THIS WINDOW OPEN while you use the app. Press Ctrl+C to stop it.
echo.

REM Open the browser from a helper window a few seconds later, so the page
REM isn't requested before the server is listening.
start "" /min cmd /c "timeout /t 5 /nobreak >nul & start http://127.0.0.1:8000"

".venv\Scripts\python.exe" -m cfbroot.cli serve --port 8000

echo.
echo Server stopped.
pause
