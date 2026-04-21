@echo off
title Nuway - Retail Manager API
color 0A

echo ============================================================
echo   NUWAY - Retail Manager Lookup API
echo ============================================================
echo.

:: Check Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo   ERROR: Python is not installed or not in PATH
    echo.
    echo   Download Python from: https://python.org/downloads
    echo   IMPORTANT: Check "Add Python to PATH" during install
    echo.
    pause
    exit /b 1
)

:: Install dependencies if not already installed
echo   Checking dependencies...
pip show flask >nul 2>&1
if errorlevel 1 (
    echo   Installing required packages...
    pip install flask flask-cors pyodbc
    echo.
)

:: Start the API server
echo   Starting Nuway RM API server...
echo   Press Ctrl+C to stop
echo ============================================================
echo.

python "%~dp0nuway_rm_api.py"

:: If it exits, pause so they can see the error
echo.
echo   Server stopped. Press any key to close.
pause >nul
