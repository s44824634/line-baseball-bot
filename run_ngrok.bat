@echo off
cd /d "%~dp0"
"C:\Users\user\AppData\Roaming\kimi-desktop\daimon-share\daimon\runtime\python\.venv\Scripts\ngrok.exe" http 8000 --log=stdout > ngrok.log 2>&1
