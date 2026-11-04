@echo off
title Blazers Scanner - Stopping
color 0C

echo ============================================
echo   Blazers Scanner - Shutting down...
echo ============================================
echo.

:: Kill the Flask / Python process
taskkill /FI "WINDOWTITLE eq Blazers Scanner - Flask App" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Blazers Scanner - Cloudflare Tunnel" /F >nul 2>&1

:: Also kill by process name as a fallback (only kills python/cloudflared started by this app)
taskkill /IM cloudflared.exe /F >nul 2>&1

echo All Blazers Scanner processes stopped.
echo.
timeout /t 2 /nobreak >nul
