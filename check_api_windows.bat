@echo off
rem Shows raw GMGN fields for the latest migrated tokens (to verify fee units / creator field).
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist "venv\Scripts\python.exe" (
    echo Run run_windows.bat first.
    pause
    goto :eof
)
"venv\Scripts\python.exe" scripts\check_api.py --limit 5 %*
echo.
pause
