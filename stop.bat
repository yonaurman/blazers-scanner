@echo off
title Blazers Scanner - Stopping
color 0C

echo ============================================
echo   Blazers Scanner - Shutting down...
echo ============================================
echo.

:: Kill the two windows started by start.bat, including their child processes (/T)
taskkill /FI "WINDOWTITLE eq Blazers Scanner - Flask App*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Blazers Scanner - Cloudflare Tunnel*" /T /F >nul 2>&1

echo All Blazers Scanner processes stopped.
echo.
timeout /t 2 /nobreak >nul
