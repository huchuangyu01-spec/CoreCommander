@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo 正在导入生成记录中的 SQL 卡密...
for %%f in ("生成记录\*.sql") do (
    "C:\Users\22179\AppData\Local\Programs\Python\Python311\python.exe" 导入卡密到数据库.py "%%f"
)
echo 导入完成！
pause
