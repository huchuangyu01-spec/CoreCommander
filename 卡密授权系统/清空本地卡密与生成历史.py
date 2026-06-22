import os
import shutil
import sqlite3
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "licenses.db")
RECORDS_DIR = os.path.join(BASE_DIR, "生成记录")

def clear_db():
    if not os.path.exists(DB_PATH):
        print(f"数据库文件不存在: {DB_PATH}")
        return
    
    conn = sqlite3.connect(DB_PATH)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM licenses;")
            cursor.execute("DELETE FROM hwid_components;")
        print("✅ 本地数据库卡密及绑机记录已清空！")
    except Exception as e:
        print(f"❌ 清空本地数据库失败: {e}")
    finally:
        conn.close()

def archive_records():
    if not os.path.exists(RECORDS_DIR):
        print("生成记录文件夹不存在，无需清理。")
        return
        
    # 获取生成记录目录下的所有 .sql 和 .txt 文件
    files = [f for f in os.listdir(RECORDS_DIR) if f.endswith(('.sql', '.txt'))]
    if not files:
        print("没有找到需要备份的历史卡密记录。")
        return
        
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(RECORDS_DIR, "历史备份", f"备份_{timestamp}")
    os.makedirs(backup_dir, exist_ok=True)
    
    for f in files:
        src = os.path.join(RECORDS_DIR, f)
        dst = os.path.join(backup_dir, f)
        shutil.move(src, dst)
        
    print(f"✅ 已将 {len(files)} 个历史生成文件备份至: {backup_dir}")

if __name__ == "__main__":
    print("==================================")
    print("  清空本地卡密与生成历史工具 v1.0  ")
    print("==================================")
    clear_db()
    archive_records()
    print("==================================")
