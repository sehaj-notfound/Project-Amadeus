@echo off
setlocal enabledelayedexpansion
title Project Amadeus - Automated Installer
cd /d "%~dp0"

echo ==============================================================================
echo    PROJECT AMADEUS: MAKISE KURISU AI WORKSTATION - INSTALLER BOOTSTRAP
echo ==============================================================================
echo.

:: 1. Check if virtual environment already exists
if exist ".venv\Scripts\python.exe" (
    echo [+] Existing .venv detected. Validating Python environment...
    goto :run_installer
)

:: 2. Detect existing Python 3.10 installation (without touching newer Python versions)
echo [*] Checking for Python 3.10...
set "PY_CMD="

:: Check Windows py launcher for 3.10
py -3.10 --version >nul 2>&1
if %errorlevel% equ 0 (
    set "PY_CMD=py -3.10"
    goto :create_venv
)

:: Check default python command
set "PY_VER="
for /f "tokens=2" %%v in ('python --version 2^>nul') do set "PY_VER=%%v"
if defined PY_VER (
    echo !PY_VER! | findstr /R "^3\.10\." >nul 2>&1
    if !errorlevel! equ 0 (
        set "PY_CMD=python"
        goto :create_venv
    )
)

:: Check standard per-user Python 3.10 location
if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" (
    set "PY_CMD=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    goto :create_venv
)

:: Check standard system-wide Python 3.10 location
if exist "C:\Program Files\Python310\python.exe" (
    set "PY_CMD=C:\Program Files\Python310\python.exe"
    goto :create_venv
)

:: 3. If Python 3.10 is not found, install it side-by-side automatically (zero prompt, zero downgrade)
echo [!] Python 3.10 not found. Installing Python 3.10 side-by-side...
echo     (This installs isolated alongside your existing Python without downgrading anything).

:: Try winget first if available
winget install Python.Python.3.10 --silent --accept-source-agreements --accept-package-agreements >nul 2>&1
if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" (
    set "PY_CMD=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    echo [+] Python 3.10 installed successfully via winget.
    goto :create_venv
)

:: Fallback: Direct download from python.org via PowerShell
echo [*] Downloading official Python 3.10 installer from python.org...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; " ^
    "$url = 'https://www.python.org/ftp/python/3.10.11/python-3.10.11-amd64.exe'; " ^
    "$out = Join-Path $env:TEMP 'python-3.10.11-amd64.exe'; " ^
    "Write-Host '[*] Downloading installer...'; " ^
    "(New-Object System.Net.WebClient).DownloadFile($url, $out); " ^
    "Write-Host '[*] Running silent side-by-side installation...'; " ^
    "Start-Process -FilePath $out -ArgumentList '/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_pip=1' -Wait; " ^
    "Remove-Item $out -Force;"

if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" (
    set "PY_CMD=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    echo [+] Python 3.10 installed successfully!
    goto :create_venv
)

echo.
echo [ERROR] Could not auto-install Python 3.10. Please check your internet connection.
pause
exit /b 1

:create_venv
echo [*] Creating virtual environment (.venv) using %PY_CMD%...
%PY_CMD% -m venv .venv

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo [ERROR] Failed to create virtual environment with %PY_CMD%.
    pause
    exit /b 1
)
echo [+] Virtual environment (.venv) created successfully.
echo [*] Upgrading pip inside virtual environment...
.venv\Scripts\python.exe -m pip install --upgrade pip >nul 2>&1

:run_installer
echo.
echo [*] Launching Amadeus Python Installer...
.venv\Scripts\python.exe install.py
set "EXIT_CODE=%errorlevel%"

if %EXIT_CODE% neq 0 (
    echo.
    echo [ERROR] Installation failed or was aborted with exit code %EXIT_CODE%.
    pause
    exit /b %EXIT_CODE%
)

echo.
echo ==============================================================================
echo [+] Project Amadeus Installation Complete!
echo ==============================================================================
pause
exit /b 0
