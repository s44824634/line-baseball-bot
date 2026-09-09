# 開發中啟動 LINE 棒球分析機器人（Windows 雙擊即可）
# 記得先把 .env 裡的兩個 LINE 憑證填好
@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo 正在建立虛擬環境...
    python -m venv .venv
    .venv\Scripts\python.exe -m pip install -r requirements.txt
)
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
pause
