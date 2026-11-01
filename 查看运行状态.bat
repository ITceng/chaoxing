@echo off
setlocal
call "%~dp0scripts\windows\status-local.bat"
exit /b %ERRORLEVEL%
