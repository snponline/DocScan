@echo off
cd /d "%~dp0"
echo Starting DocScan server... close this window to stop.
start "" http://127.0.0.1:8780/admin
python -X utf8 server.py
pause
