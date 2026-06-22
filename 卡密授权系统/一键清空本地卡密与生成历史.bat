@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo 正在清空本地数据库中的卡密并备份生成历史...
"C:\Users\22179\AppData\Local\Programs\Python\Python311\python.exe" 清空本地卡密与生成历史.py
echo 清空完成！
pause
