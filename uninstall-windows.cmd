@echo off
setlocal
title Maintain uninstaller
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\uninstall-windows.ps1"
set "UNINSTALL_EXIT=%ERRORLEVEL%"
echo.
if not "%UNINSTALL_EXIT%"=="0" (
  echo Maintain could not be fully removed. Review the error above.
) else (
  echo Maintain was removed.
)
pause
exit /b %UNINSTALL_EXIT%
