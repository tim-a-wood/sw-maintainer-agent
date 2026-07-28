@echo off
setlocal
title Which Maintain
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\which-maintain.ps1"
echo.
pause
