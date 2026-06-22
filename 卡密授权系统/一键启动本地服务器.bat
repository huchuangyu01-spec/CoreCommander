@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo 正在启动本地鉴权服务器...
"C:\Users\22179\AppData\Local\Programs\Python\Python311\python.exe" -m uvicorn 云端鉴权服务端:app --host 127.0.0.1 --port 8000
pause
