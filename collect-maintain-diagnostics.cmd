@echo off
setlocal EnableExtensions
set "OUTPUT=%CD%\maintain-diagnostics.zip"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass ^
  -File "%~dp0scripts\collect-maintain-diagnostics.ps1" -OutputPath "%OUTPUT%"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Diagnostic collection failed with exit code %EXIT_CODE%.
    pause
    exit /b %EXIT_CODE%
)

echo.
echo Review this support bundle before sharing it:
echo "%OUTPUT%"
pause
exit /b 0
