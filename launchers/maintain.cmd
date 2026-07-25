@echo off
setlocal EnableExtensions
if not defined LOCALAPPDATA (
  echo Maintain could not find the current user's local application folder. 1>&2
  exit /b 2
)
set "INSTALLED_LAUNCHER=%LOCALAPPDATA%\Programs\Maintain\Maintain.cmd"
if not exist "%INSTALLED_LAUNCHER%" (
  echo Maintain is not installed. Run install-or-update-windows.cmd. 1>&2
  exit /b 2
)
call "%INSTALLED_LAUNCHER%" %*
set "MAINTAIN_EXIT=%ERRORLEVEL%"
exit /b %MAINTAIN_EXIT%
