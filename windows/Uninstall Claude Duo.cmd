@echo off
rem Removes Claude Duo. Profiles (sign-in and conversations) are kept; -RemoveProfiles sends them to the Recycle Bin.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\uninstall.ps1" %*
echo.
pause
