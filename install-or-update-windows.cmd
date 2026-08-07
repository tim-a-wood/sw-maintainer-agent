@echo off
rem Install or update Maintain. This calls scripts\install.cmd, which
rem needs only Python: no script-signing gate, so it works on a
rem machine that refuses unsigned scripts.
call "%~dp0scripts\install.cmd" %*
exit /b %ERRORLEVEL%
