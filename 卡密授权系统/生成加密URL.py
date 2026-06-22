import sys

def encrypt_url(url: str, key: int = 0x5A):
    # 使用 XOR 0x5A 加密 URL，防止破解者在内存中直接搜索明文字符串
    encrypted = [b ^ key for b in url.encode('utf-8')]
    print("==================================================")
    print(f"🔒 原始 URL: {url}")
    print("==================================================")
    print("请复制以下字节列表，并替换到 `license.py` 的第 118 行 `enc = [...]` 中:")
    print(encrypted)
    print("==================================================")
    return encrypted

if __name__ == "__main__":
    print("==================================")
    print("  Core Commander 验证地址加密工具  ")
    print("==================================")
    if len(sys.argv) > 1:
        url = sys.argv[1].strip()
    else:
        url = input("请输入您要指向的新验证 URL (例如 http://1.1.1.1:8000/api/verify 或 https://api.xxx.com/api/verify): ").strip()
    encrypt_url(url)
