@echo off
setlocal
call "%~dp0scripts\windows\preflight-local.bat"
exit /b %ERRORLEVEL%
