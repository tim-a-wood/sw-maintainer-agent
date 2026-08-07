@echo off
rem Remove Maintain. This calls scripts\uninstall.cmd, which needs
rem only Python.
call "%~dp0scripts\uninstall.cmd" %*
exit /b %ERRORLEVEL%
