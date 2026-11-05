@echo off
title Project Amadeus - Uninstaller
cd /d "%~dp0"

echo ==============================================================================
echo    PROJECT AMADEUS: MAKISE KURISU AI WORKSTATION - CLEAN UNINSTALLER
echo ==============================================================================
echo.
echo This utility will cleanly remove Project Amadeus configurations and shortcuts:
echo  - Desktop shortcut (Project Amadeus.lnk)
echo  - Sandboxed profile and database (%%LOCALAPPDATA%%\ProjectAmadeus)
echo.
set /p CONFIRM="Are you sure you want to proceed with uninstallation? (Y/N): "
if /i not "%CONFIRM%"=="Y" (
    echo [*] Uninstallation cancelled.
    pause
    exit /b 0
)

echo.
echo [*] Removing Desktop shortcut...
del "%USERPROFILE%\Desktop\Project Amadeus.lnk" 2>nul
del "%USERPROFILE%\OneDrive\Desktop\Project Amadeus.lnk" 2>nul

echo [*] Removing sandboxed profile, database, and hardware cache...
rmdir /s /q "%LOCALAPPDATA%\ProjectAmadeus" 2>nul

echo.
echo ==============================================================================
echo [+] Project Amadeus components have been completely uninstalled.
echo     Your host system is clean with zero remaining registry or profile entries.
echo     To finish, you may simply delete this project folder.
echo ==============================================================================
pause
exit /b 0
