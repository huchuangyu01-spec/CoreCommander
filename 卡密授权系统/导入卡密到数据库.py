import os
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "licenses.db")

if len(sys.argv) != 2:
    print("用法: python 导入卡密到数据库.py <生成记录\\insert_xxx.sql>")
    sys.exit(1)

sql_file = sys.argv[1]

if not os.path.exists(sql_file):
    print(f"找不到 SQL 文件: {sql_file}")
    sys.exit(1)

conn = sqlite3.connect(DB_PATH)
try:
    with conn:
        cursor = conn.cursor()
        with open(sql_file, 'r', encoding='utf-8') as f:
            sql_script = f.read()
        cursor.executescript(sql_script)
    print(f"成功导入: {sql_file}")
except Exception as e:
    print(f"导入失败: {e}")
finally:
    conn.close()

