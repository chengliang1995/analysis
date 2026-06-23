@echo off
chcp 65001 >nul
cd /d "%~dp0.."

for /f "tokens=5" %%p in ('netstat -aon ^| findstr ":5050" ^| findstr "LISTENING"') do (
  echo 停止旧进程 PID=%%p
  taskkill /F /PID %%p >nul 2>&1
)

set PYTHONWARNINGS=ignore::UserWarning
set PYTHONIOENCODING=utf-8

echo 启动 Web 仪表盘: http://127.0.0.1:5050
echo 请用 Ctrl+C 停止
".venv\Scripts\python.exe" web_app.py --port 5050
