@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_douyin_opinion.ps1" -PromptKeywords
set "crawlerExitCode=%ERRORLEVEL%"

if not "%crawlerExitCode%"=="0" (
    echo.
    echo The crawler stopped with exit code %crawlerExitCode%.
)

echo.
pause
exit /b %crawlerExitCode%
