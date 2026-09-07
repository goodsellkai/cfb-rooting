@echo off
title Push cfbroot to GitHub
cd /d "%~dp0"
set GH="C:\Program Files\GitHub CLI\gh.exe"

if not exist %GH% (
    echo GitHub CLI not found at %GH%
    echo Install it with:  winget install --id GitHub.cli
    pause
    exit /b 1
)

echo ============================================================
echo  Step 1 of 2 - sign in to GitHub
echo ============================================================
%GH% auth status >nul 2>&1
if errorlevel 1 (
    echo A browser window will open. Approve the device code shown here.
    echo.
    %GH% auth login --web --git-protocol https
    if errorlevel 1 (
        echo Sign-in failed or was cancelled.
        pause
        exit /b 1
    )
) else (
    echo Already signed in.
)

echo.
echo ============================================================
echo  Step 2 of 2 - create the repository and push
echo ============================================================
%GH% repo view cfb-rooting >nul 2>&1
if errorlevel 1 (
    %GH% repo create cfb-rooting --public --source=. --remote=origin --push ^
        --description "Monte Carlo college football rooting guide: which results this week actually help your team, with confidence intervals."
) else (
    echo Repository already exists - pushing to it.
    git push -u origin main
)

if errorlevel 1 (
    echo.
    echo Push failed. See the message above.
    pause
    exit /b 1
)

echo.
echo Done. Opening the repository...
%GH% repo view --web
pause
