@echo off
rem Generates the Ed25519 key pair for creating a GMGN API key, copies the public key
rem to the clipboard and opens https://gmgn.ai/ai. Run run_windows.bat first.
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist "venv\Scripts\python.exe" (
    echo Run run_windows.bat first.
    pause
    goto :eof
)
"venv\Scripts\python.exe" -m pip install -q cryptography
"venv\Scripts\python.exe" scripts\gen_gmgn_keypair.py
echo.
echo Next: paste the public key (Ctrl+V) into the API Key form on the GMGN page that just opened,
echo create the key, then put the gmgn_... value into .env as GMGN_API_KEY and run run_windows.bat.
echo.
pause
