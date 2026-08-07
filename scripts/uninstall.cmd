@echo off
rem Remove Maintain: the private environment and every shortcut.
setlocal
set "HERE=%~dp0"
where py >nul 2>&1 && (py -3 "%HERE%install_maintain.py" --uninstall %* & goto :done)
where python >nul 2>&1 && (python "%HERE%install_maintain.py" --uninstall %* & goto :done)
echo Python is required to remove Maintain.
pause
exit /b 1
:done
exit /b %ERRORLEVEL%
