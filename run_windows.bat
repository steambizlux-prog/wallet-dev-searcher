@echo off
rem Quick start on Windows: creates venv, installs deps, runs the bot.
rem Requires Python 3.10+ from https://www.python.org/downloads/ ("Add python.exe to PATH" checked).
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY python --version >nul 2>nul && set "PY=python"
if not defined PY goto :nopython

if not exist "venv\Scripts\python.exe" (
    echo [1/3] Creating virtual environment...
    %PY% -m venv venv || goto :err
)

echo [2/3] Installing dependencies...
"venv\Scripts\python.exe" -m pip install -q --upgrade pip
"venv\Scripts\python.exe" -m pip install -q -r requirements.txt || goto :err

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo.
    echo Created .env - fill in TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GMGN_API_KEY, then run this file again.
    notepad ".env"
    goto :end
)

echo [3/3] Starting bot. Press Ctrl+C to stop.
echo.
"venv\Scripts\python.exe" -m devsearcher
goto :end

:nopython
echo Python not found. Install Python 3.10+ from https://www.python.org/downloads/
echo and check "Add python.exe to PATH" in the installer.
goto :end

:err
echo.
echo Something failed, see the message above.

:end
echo.
pause
