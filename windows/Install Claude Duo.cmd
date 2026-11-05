@echo off
rem Installs (or updates) Claude Duo. Run it again after the official Claude updates.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1" %*
echo.
pause
