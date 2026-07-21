@echo off
where maintain >nul 2>nul
if errorlevel 1 (
  echo Maintain is not installed. Follow the setup steps in README.md. 1>&2
  exit /b 2
)
maintain %*
exit /b %errorlevel%
