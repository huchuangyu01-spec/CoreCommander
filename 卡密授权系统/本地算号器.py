import os
import uuid
import secrets
from datetime import datetime

def generate_key():
    """生成 32 位安全随机字符串 (全大写)"""
    # 直接使用密码学安全的 secrets 生成高熵随机码，避免截断导致安全性丧失
    return secrets.token_hex(16).upper()

def create_keys(amount, key_type):
    """
    生成指定数量和类型的卡密。
    key_type: 'trial' (14天) 或 'permanent' (永久)
    """
    keys = [generate_key() for _ in range(amount)]
    
    timestamp = datetime.now().strftime("%Y%md_%H%M%S")
    type_name = "14天测试卡" if key_type == "trial" else "永久正式卡"
    
    # 设置输出目录
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "生成记录")
    os.makedirs(output_dir, exist_ok=True)
    
    txt_filename = os.path.join(output_dir, f"keys_{key_type}_{timestamp}.txt")
    sql_filename = os.path.join(output_dir, f"insert_{key_type}_{timestamp}.sql")
    
    # 1. 导出供代理发货的 TXT 文件
    with open(txt_filename, "w", encoding="utf-8") as f:
        f.write(f"=== Core Commander {type_name} 卡密 ===\n")
        f.write(f"生成数量: {amount} | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        for k in keys:
            f.write(f"{k}\n")
            
    # 2. 导出供服务器导入的 SQL 文件
    with open(sql_filename, "w", encoding="utf-8") as f:
        f.write("-- 导入至 Tencent Cloud SQLite/MySQL 数据库的 licenses 表\n")
        f.write("BEGIN TRANSACTION;\n")
        for k in keys:
            f.write(f"INSERT INTO licenses (key, type, hwid, status) VALUES ('{k}', '{key_type}', '', 'unused');\n")
        f.write("COMMIT;\n")
        
    print(f"✅ 成功生成 {amount} 张【{type_name}】")
    print(f"📦 分发文件 (发给代理): {txt_filename}")
    print(f"💾 数据库文件 (导入服务器): {sql_filename}")

if __name__ == "__main__":
    import sys
    try:
        print("================================")
        print("  Core Commander 卡密算号器 v1.0")
        print("================================")
        
        try:
            if len(sys.argv) == 3:
                choice = sys.argv[1].strip()
                amount = int(sys.argv[2].strip())
                print(f"命令行参数模式: 类型={choice}, 数量={amount}")
            else:
                print("提示: 也可以使用命令直接生成: python 本地算号器.py <类型1或2> <数量>")
                print("1. 生成 14天测试卡")
                print("2. 生成 永久正式卡")
                choice = input("请选择类型 (1/2): ").strip()
                amount_str = input("请输入需要生成的数量 (例如 50): ").strip()
                amount = int(amount_str)
                
            if choice not in ['1', '2']:
                print("选择无效，退出。")
                sys.exit(1)
                
            if amount <= 0:
                print("数量必须大于 0")
                sys.exit(1)
                
            key_type = "trial" if choice == '1' else "permanent"
            create_keys(amount, key_type)
            
        except ValueError:
            print("输入错误，请输入有效数字。")
        except KeyboardInterrupt:
            print("\n已取消。")
    except Exception as e:
        import traceback
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "error.log"), "a", encoding="utf-8") as f:
            f.write(f"\n=== FATAL ERROR AT {datetime.now()} ===\n")
            f.write(traceback.format_exc())
        print(f"发生致命错误，请查看 error.log: {e}")

