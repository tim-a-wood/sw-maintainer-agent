@echo off
rem Install Maintain. A batch file has no script-signing gate,
rem so this works where a managed machine refuses unsigned scripts.
setlocal
set "HERE=%~dp0"
where py >nul 2>&1 && (py -3 "%HERE%install_maintain.py" %* & goto :done)
where python >nul 2>&1 && (python "%HERE%install_maintain.py" %* & goto :done)
echo Python 3.11 or later is required.
echo Install it from https://www.python.org/downloads/windows/ and run this again.
pause
exit /b 1
:done
exit /b %ERRORLEVEL%
