@echo off
title Blazers Scanner - Launcher
color 0A

echo ============================================
echo   Blazers Scanner - Starting up...
echo ============================================
echo.

:: Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Make sure Python is installed and in your PATH.
    pause
    exit /b 1
)

:: Resolve cloudflared — check PATH first, then the default install location
set CLOUDFLARED=cloudflared
cloudflared --version >nul 2>&1
if errorlevel 1 (
    if exist "C:\Program Files (x86)\cloudflared\cloudflared.exe" (
        set "CLOUDFLARED=C:\Program Files (x86)\cloudflared\cloudflared.exe"
    ) else if exist "C:\Program Files\cloudflared\cloudflared.exe" (
        set "CLOUDFLARED=C:\Program Files\cloudflared\cloudflared.exe"
    ) else (
        echo [ERROR] cloudflared not found.
        echo.
        echo Install it with:  winget install Cloudflare.cloudflared
        echo Then re-run this script.
        pause
        exit /b 1
    )
)

:: Move to the script's directory so relative paths work
cd /d "%~dp0"

:: Start Flask app in a separate window
echo [1/2] Starting Flask app on http://localhost:5000 ...
start "Blazers Scanner - Flask App" cmd /k "python app.py"

:: Give Flask a few seconds to initialize before starting the tunnel
echo [2/2] Waiting for Flask to start...
timeout /t 4 /nobreak >nul

:: Start Cloudflare Tunnel in a separate window
echo       Starting Cloudflare Tunnel...
echo.
echo ============================================
echo   Look at the tunnel window for your
echo   public URL (https://xxxx.trycloudflare.com)
echo   Open that URL on any device - phone, web.
echo ============================================
echo.
start "Blazers Scanner - Cloudflare Tunnel" cmd /k "%CLOUDFLARED% tunnel --url http://localhost:5000"

echo Both processes are running in their own windows.
echo Close those windows or run stop.bat to shut everything down.
echo.
pause
