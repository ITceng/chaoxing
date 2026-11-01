@echo off
setlocal
call "%~dp0scripts\windows\stop-local.bat"
exit /b %ERRORLEVEL%
