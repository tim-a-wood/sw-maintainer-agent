@echo off
setlocal
title Maintain installer
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-windows.ps1"
set "INSTALL_EXIT=%ERRORLEVEL%"
echo.
if not "%INSTALL_EXIT%"=="0" (
  echo Maintain was not installed. Review the error above.
) else (
  echo Maintain is ready.
)
pause
exit /b %INSTALL_EXIT%
