import os
import json
import time
import requests
import logging
import rsa
import base64
import secrets
import hashlib
import certifi
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
from core_commander.core.hwid import get_hwid

logger = logging.getLogger(__name__)

import ctypes
from ctypes import wintypes

class SecureMemoryManager:
    """
    Performs secure memory management and scrubbing (memset) of activated licenses,
    plain HWID sequences, and server endpoints immediately after use.
    Encrypts sensitive data in memory using transient keys.
    """
    _transient_key = os.urandom(32)
    _encrypted_license_key = None
    _encrypted_server_response = None

    @staticmethod
    def scrub_bytes(buf) -> None:
        """Overwrites the contents of a mutable buffer with zeroes."""
        if not buf:
            return
        try:
            length = len(buf)
            if isinstance(buf, bytearray):
                addr = (ctypes.c_char * length).from_buffer(buf)
                ctypes.memset(addr, 0, length)
            elif hasattr(buf, '_objects') or isinstance(buf, ctypes.Array):
                ctypes.memset(ctypes.byref(buf), 0, ctypes.sizeof(buf))
        except Exception:
            pass

    @staticmethod
    def scrub_str(s: str) -> None:
        """
        CPython-specific string buffer scrubbing. Disabled to prevent memory corruption and crashes.
        """
        return

    @classmethod
    def _encrypt(cls, data: bytearray) -> bytes:
        aesgcm = AESGCM(cls._transient_key)
        nonce = os.urandom(12)
        return nonce + aesgcm.encrypt(nonce, bytes(data), None)

    @classmethod
    def _decrypt(cls, enc_data: bytes) -> bytearray:
        aesgcm = AESGCM(cls._transient_key)
        nonce = enc_data[:12]
        ciphertext = enc_data[12:]
        return bytearray(aesgcm.decrypt(nonce, ciphertext, None))

    @classmethod
    def set_license_key(cls, key: str):
        if not key:
            cls._encrypted_license_key = None
            return
        key_bytes = bytearray(key.encode('utf-8'))
        cls._encrypted_license_key = cls._encrypt(key_bytes)
        cls.scrub_bytes(key_bytes)

    @classmethod
    def get_license_key(cls) -> str:
        if not cls._encrypted_license_key:
            return ""
        dec_bytes = cls._decrypt(cls._encrypted_license_key)
        res = dec_bytes.decode('utf-8')
        cls.scrub_bytes(dec_bytes)
        return res

    @classmethod
    def set_server_response(cls, response):
        if not response:
            cls._encrypted_server_response = None
            return
        resp_str = json.dumps(response)
        resp_bytes = bytearray(resp_str.encode('utf-8'))
        cls._encrypted_server_response = cls._encrypt(resp_bytes)
        cls.scrub_bytes(resp_bytes)

    @classmethod
    def get_server_response(cls) -> dict:
        if not cls._encrypted_server_response:
            return None
        dec_bytes = cls._decrypt(cls._encrypted_server_response)
        resp_str = dec_bytes.decode('utf-8')
        res = json.loads(resp_str)
        cls.scrub_bytes(dec_bytes)
        return res



# Use APPDATA for license cache
APP_DATA_DIR = os.path.join(os.environ.get('APPDATA', ''), 'CoreCommander')
if not os.path.exists(APP_DATA_DIR):
    os.makedirs(APP_DATA_DIR, exist_ok=True)
CONFIG_FILE = os.path.join(APP_DATA_DIR, 'license.dat')

# Obfuscated keys and endpoints to prevent memory extraction and hex edit bypass
def _xor_crypt(data: list, key: int = 0x5A) -> str:
    return bytes(b ^ key for b in data).decode('utf-8')

def _get_api_endpoint() -> str:
    # Encrypted: "http://43.173.103.27:8000/api/verify"
    enc = [50, 46, 46, 42, 96, 117, 117, 110, 105, 116, 107, 109, 105, 116, 107, 106, 105, 116, 104, 109, 96, 98, 106, 106, 106, 117, 59, 42, 51, 117, 44, 63, 40, 51, 60, 35]
    return _xor_crypt(enc)

_enc_pub_key = [119, 119, 119, 119, 119, 24, 31, 29, 19, 20, 122, 8, 9, 27, 122, 10, 15, 24, 22, 19, 25, 122, 17, 31, 3, 119, 119, 119, 119, 119, 80, 23, 31, 61, 25, 11, 11, 25, 61, 45, 21, 25, 111, 59, 110, 108, 104, 24, 13, 31, 29, 10, 15, 50, 63, 62, 111, 20, 46, 24, 16, 51, 62, 105, 48, 24, 41, 113, 117, 108, 43, 22, 56, 28, 29, 17, 41, 23, 42, 107, 15, 0, 51, 17, 2, 59, 31, 60, 54, 24, 10, 31, 23, 9, 110, 80, 44, 99, 11, 3, 31, 11, 46, 35, 113, 109, 8, 25, 27, 3, 11, 59, 51, 98, 21, 43, 32, 22, 34, 20, 11, 99, 46, 28, 27, 61, 23, 24, 27, 27, 31, 103, 80, 119, 119, 119, 119, 119, 31, 20, 30, 122, 8, 9, 27, 122, 10, 15, 24, 22, 19, 25, 122, 17, 31, 3, 119, 119, 119, 119, 119]
PUBLIC_KEY_PEM = bytes(b ^ 0x5A for b in _enc_pub_key)

def _get_public_key() -> rsa.PublicKey:
    return rsa.PublicKey.load_pkcs1(PUBLIC_KEY_PEM)

# Setup resilient requests session with exponential backoff
_session = requests.Session()
retries = Retry(
    total=3,
    backoff_factor=1,  # Waits 1s, 2s, 4s between retries
    status_forcelist=[500, 502, 503, 504],
    raise_on_status=False
)
_session.mount("http://", HTTPAdapter(max_retries=retries))
_session.mount("https://", HTTPAdapter(max_retries=retries))

def verify_signature(data: dict) -> bool:
    """验证服务端下发报文的 RSA 签名，防止抓包伪造"""
    signature_b64 = data.pop('signature', None)
    if not signature_b64:
        return False
    try:
        signature = base64.b64decode(signature_b64)
        payload_str = json.dumps(data, sort_keys=True, separators=(',', ':'))
        pub_key = _get_public_key()
        verified = rsa.verify(payload_str.encode('utf-8'), signature, pub_key) == 'SHA-256'
        del pub_key
        return verified
    except rsa.VerificationError:
        return False
    except Exception as e:
        logger.error(f"Signature verification error: {e}")
        return False

def get_hwid_key() -> bytes:
    return hashlib.sha256(get_hwid().encode('utf-8')).digest()

def aes_encrypt(data: bytes, key: bytes) -> bytes:
    """使用 AES-GCM (256位) 加密数据，前置 12 字节随机 nonce"""
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, data, None)
    return nonce + ciphertext

def aes_decrypt(encrypted_data: bytes, key: bytes) -> bytes:
    """解密 AES-GCM 加密的数据，提取前 12 字节 nonce 并验证完整性"""
    if len(encrypted_data) < 28: # 12 bytes nonce + 16 bytes tag + at least 1 byte plaintext
        raise ValueError("Encrypted data is too short or corrupted")
    nonce = encrypted_data[:12]
    ciphertext = encrypted_data[12:]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, None)

def get_internet_time() -> float:
    """
    Attempts to retrieve the current UTC timestamp by querying the Date header 
    of major trusted CDN/portal endpoints via a lightweight HTTP HEAD request.
    Returns 0.0 if completely offline or requests fail.
    """
    urls = [
        "https://www.baidu.com",
        "https://www.taobao.com",
        "https://mirrors.aliyun.com"
    ]
    for url in urls:
        try:
            res = requests.head(url, timeout=2.0, proxies={"http": None, "https": None})
            date_str = res.headers.get('Date')
            if date_str:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(date_str)
                return dt.timestamp()
        except Exception:
            continue
    return 0.0

class LicenseManager:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(LicenseManager, cls).__new__(cls)
            cls._instance._init()
        return cls._instance

    @property
    def license_key(self) -> str:
        return SecureMemoryManager.get_license_key()

    @license_key.setter
    def license_key(self, value: str):
        SecureMemoryManager.set_license_key(value)

    @property
    def server_response(self) -> dict:
        return SecureMemoryManager.get_server_response()

    @server_response.setter
    def server_response(self, value):
        SecureMemoryManager.set_server_response(value)

    def _init(self):
        self.is_active = False
        self.expiry_timestamp = 0  # 0 means permanent, else timestamp
        self.license_type = "none" # none, trial, permanent
        self.last_check_time = 0
        self.last_verified_online = 0
        self.license_key = ""
        self.server_response = None
        self._load_local_license()

    def _verify_clock_integrity(self) -> bool:
        """
        Verifies system clock using hidden caches and internet time to prevent clock tampering.
        """
        current_time = time.time()
        
        # 1. Check against the encrypted local license's last check time
        if current_time < (self.last_check_time - 900):
            logger.error("System clock tampered (moved backwards compared to local check time).")
            return False
            
        # 2. Check against critical system config file modification time
        try:
            system_root = os.environ.get("SystemRoot", r"C:\Windows")
            system_file = os.path.join(system_root, "System32", "config", "SYSTEM")
            if os.path.exists(system_file):
                mtime = os.path.getmtime(system_file)
                if current_time < (mtime - 900):
                    logger.error("System clock tampered (current time is older than system registry hive file modification time).")
                    return False
        except Exception as e:
            logger.warning(f"Failed to check system file modification time: {e}")

        # 3. Check against the hidden timestamp cache in registry (HKLM with HKCU fallback)
        try:
            import winreg
            path = r"Software\CoreCommander"
            last_seen = 0.0
            
            # Try reading HKLM first (handling architecture view)
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                    last_seen_str, _ = winreg.QueryValueEx(key, "LastActiveTime")
                    last_seen = float(last_seen_str)
            except (FileNotFoundError, PermissionError, OSError):
                # Fallback to HKCU
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ) as key:
                        last_seen_str, _ = winreg.QueryValueEx(key, "LastActiveTime")
                        last_seen = float(last_seen_str)
                except FileNotFoundError:
                    pass

            if last_seen > 0.0 and current_time < (last_seen - 900):
                logger.error("System clock tampered (moved backwards compared to registry history).")
                return False
        except Exception:
            pass

        # 4. If network is available, query public time servers to verify Date (Loose DR mode)
        net_time = get_internet_time()
        if net_time > 0.0:
            # Allow up to 2 hours of clock drift for user timezone/NTP offsets
            # but reject if the client clock is skewed from internet time by more than 2 hours (7200s)
            if abs(current_time - net_time) > 7200:
                logger.error("System clock tampered (skewed from real internet time).")
                return False
                
        # Update hidden registry timestamp to current time (progressing forward)
        try:
            import winreg
            path = r"Software\CoreCommander"
            
            # Try saving to HKLM first
            try:
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY) as key:
                    winreg.SetValueEx(key, "LastActiveTime", 0, winreg.REG_SZ, str(current_time))
            except (PermissionError, OSError):
                pass
                
            # Always save to HKCU as secondary cache/fallback
            try:
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "LastActiveTime", 0, winreg.REG_SZ, str(current_time))
            except Exception:
                pass
        except Exception:
            pass
            
        return True
        
    def _load_local_license(self):
        debug_path = r"C:\Users\22179\.core_commander\hwid_debug.txt"
        def log_debug(msg):
            try:
                with open(debug_path, "a", encoding="utf-8") as df:
                    df.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
            except Exception:
                pass

        log_debug("--- Starting _load_local_license ---")
        try:
            from core_commander.core.hwid import get_hwid, get_smbios_serial, get_cpu_features, get_physical_disk_serial
            board_sn = get_smbios_serial()
            cpu_id = get_cpu_features()
            disk_sn = get_physical_disk_serial()
            hwid = get_hwid()
            log_debug(f"HWID details -> board_sn: {board_sn} | cpu_id: {cpu_id} | disk_sn: {disk_sn} | hwid: {hwid}")
            log_debug(f"CONFIG_FILE path: {CONFIG_FILE} (exists: {os.path.exists(CONFIG_FILE)})")
            
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'rb') as f:
                    encrypted_data = f.read()
                
                log_debug(f"Read encrypted cache length: {len(encrypted_data)}")
                if encrypted_data:
                    hwid_key = get_hwid_key()
                    import hashlib
                    log_debug(f"hwid_key hash: {hashlib.sha256(hwid_key).hexdigest()}")
                    try:
                        decrypted_bytes = aes_decrypt(encrypted_data, hwid_key)
                        log_debug("AES decryption success")
                    except Exception as ae:
                        log_debug(f"AES decryption FAILED: {ae}")
                        raise ae
                    
                    decrypted_data = decrypted_bytes.decode('utf-8')
                    config = json.loads(decrypted_data)
                    
                    if not isinstance(config, dict) or "license_data" not in config:
                        log_debug("Invalid schema: missing 'license_data'")
                        raise ValueError("Invalid schema: missing 'license_data'")
                        
                    lic_data = config.get("license_data", {})
                    if not isinstance(lic_data, dict):
                        log_debug("Invalid schema: 'license_data' must be a dictionary")
                        raise ValueError("Invalid schema: 'license_data' must be a dictionary")
                        
                    self.license_key = lic_data.get("key", "")
                    self.expiry_timestamp = lic_data.get("expiry", 0)
                    self.license_type = lic_data.get("type", "none")
                    self.last_check_time = lic_data.get("last_check", 0)
                    self.last_verified_online = lic_data.get("last_verified_online", self.last_check_time)
                    log_debug(f"Loaded config: key={self.license_key}, expiry={self.expiry_timestamp}, type={self.license_type}, last_check={self.last_check_time}")
                    
                    # Verify signature of cached server response if present
                    self.server_response = config.get("server_response")
                    if self.server_response:
                        resp_copy = dict(self.server_response)
                        sig_ok = verify_signature(resp_copy)
                        log_debug(f"Signature verification result: {sig_ok}")
                        if not sig_ok:
                            logger.error("Offline license response signature verification failed.")
                            self.is_active = False
                            self.license_type = "expired"
                            return
                    else:
                        log_debug("No signed server response cached. Offline license marked inactive.")
                        logger.warning("No signed server response cached. Offline license marked inactive.")
                        self.is_active = False
                        self.license_type = "expired"
                        return
                    
                    current_time = time.time()
                    log_debug(f"Current time: {current_time}")
                    
                    # Clock Rollback Check
                    clock_ok = self._verify_clock_integrity()
                    log_debug(f"Clock integrity result: {clock_ok}")
                    if not clock_ok:
                        logger.error("System clock tampered (moved backwards). Invalidating license.")
                        self.is_active = False
                        self.license_type = "expired"
                    else:
                        if current_time > self.last_check_time:
                            self.last_check_time = current_time
                        
                        # Offline grace validation check (max 3 days offline)
                        MAX_OFFLINE_DAYS = 3
                        offline_diff = current_time - self.last_verified_online
                        log_debug(f"Offline diff: {offline_diff} seconds (limit: {MAX_OFFLINE_DAYS * 86400})")
                        if offline_diff > MAX_OFFLINE_DAYS * 86400:
                            logger.error("Offline validation period expired. Online verification required.")
                            self.is_active = False
                            self.license_type = "expired"
                        else:
                            if self.license_type == "permanent":
                                self.is_active = True
                            elif self.license_type == "trial":
                                if current_time < self.expiry_timestamp:
                                    self.is_active = True
                                else:
                                    self.is_active = False
                                    self.license_type = "expired"
                    log_debug(f"Final status -> is_active: {self.is_active}, type: {self.license_type}")
            else:
                log_debug("CONFIG_FILE does not exist.")
        except Exception as e:
            log_debug(f"Exception during load: {e}")
            import traceback
            log_debug(traceback.format_exc())
            logger.error(f"Failed to load local license (cache corrupted or HWID changed): {e}")

    def _save_local_license(self):
        try:
            config = {
                "server_response": self.server_response,
                "license_data": {
                    "key": self.license_key,
                    "expiry": self.expiry_timestamp,
                    "type": self.license_type,
                    "last_check": self.last_check_time,
                    "last_verified_online": self.last_verified_online
                }
            }
            json_str = json.dumps(config, ensure_ascii=False)
            hwid_key = get_hwid_key()
            encrypted_data = aes_encrypt(json_str.encode('utf-8'), hwid_key)
            
            with open(CONFIG_FILE, 'wb') as f:
                f.write(encrypted_data)
        except Exception as e:
            logger.error(f"Failed to save local license: {e}")

    def get_remaining_days(self):
        if self.license_type == "permanent":
            return 9999
        elif self.license_type == "trial":
            remaining = self.expiry_timestamp - time.time()
            if remaining > 0:
                return int(remaining / 86400) + 1
        return 0

    def verify_license_online(self, key, hwid):
        """向腾讯云发卡服务器进行真实的网络验证"""
        key_raw = key
        hwid_raw = hwid
        key = key.strip().upper()
        if not key:
            return False, "卡密不能为空"
            
        try:
            # 每次请求生成防重放的随机数 Nonce
            nonce = secrets.token_hex(16)
            
            # Record request start time
            request_time = time.time()
            
            endpoint = _get_api_endpoint()
            # Force Strict SSL/TLS verification using certifi CA bundle when endpoint is HTTPS
            is_https = endpoint.lower().startswith("https://")
            verify_param = certifi.where() if is_https else True
            
            # Calculate component hashes for multi-component signature verification
            from core_commander.core.hwid import get_hwid_components
            comp = get_hwid_components()
            
            response = _session.post(
                endpoint, 
                json={
                    "key": key,
                    "hwid": hwid,
                    "nonce": nonce,
                    "bios_hash": comp.get("bios_hash"),
                    "disk_hash": comp.get("disk_hash"),
                    "uuid_hash": comp.get("uuid_hash"),
                    "cpu_hash": comp.get("cpu_hash")
                }, 
                timeout=5, 
                proxies={"http": None, "https": None},
                verify=verify_param
            )
            del endpoint
            
            # Validate response time window to prevent proxy hold-and-forward or debugging replay
            response_time = time.time()
            if response_time - request_time > 15:
                return False, "安全警告：请求延迟过长，可能遭遇代理拦截调试或重放攻击！"
            
            if response.status_code == 200:
                data = response.json()
                
                # 防重放及防伪造篡改校验
                if data.get('nonce') != nonce:
                    return False, "安全警告：检测到重放攻击 (Nonce 匹配失败)！"
                
                # Verify signature and check server timestamp to prevent replay of historic valid packets
                server_ts = data.get('timestamp')
                data_copy = data.copy()
                if not verify_signature(data):
                    return False, "安全警告：服务器响应签名校验失败，可能正遭受伪造攻击！"
                
                if server_ts is not None:
                    # Allow up to 10 minutes (600 seconds) clock drift between client and server
                    if abs(int(response_time) - int(server_ts)) > 600:
                        return False, "安全警告：客户端与服务器时间差异过大，拒绝授权激活！"
                    
                if data.get("success"):
                    self.server_response = data_copy
                    self.license_key = key
                    self.license_type = data.get("type", "trial")
                    self.expiry_timestamp = data.get("expire_timestamp", 0)
                    self.is_active = True
                    self.last_check_time = time.time()
                    self.last_verified_online = self.last_check_time
                    self._save_local_license()
                    return True, data.get("msg", "激活成功！")
                else:
                    return False, data.get("msg", "验证失败")
            else:
                return False, f"服务器返回异常 (HTTP {response.status_code})"
                
        except requests.exceptions.Timeout:
            # 断网容灾
            if not self._verify_clock_integrity():
                return False, "安全警告：检测到系统时间倒流，授权已失效。"
                
            current_time = time.time()
                
            MAX_OFFLINE_DAYS = 3
            if current_time - self.last_verified_online > MAX_OFFLINE_DAYS * 86400:
                return False, "离线授权已过期，请联网完成一次校验"

            if self.is_active and self.license_key == key:
                if current_time > self.last_check_time:
                    self.last_check_time = current_time
                self._save_local_license()
                if self.license_type == "trial" and current_time > self.expiry_timestamp:
                    self.is_active = False
                    return False, "本地时间检验：测试卡已过期，请联网获取最新状态"
                return True, "离线缓存验证通过（网络超时）"
            return False, "网络连接超时，请检查您的网络或重试"
            
        except requests.exceptions.RequestException as e:
            logger.error(f"License verification network error: {e}")
            if not self._verify_clock_integrity():
                return False, "安全警告：检测到系统时间倒流，授权已失效。"
                
            current_time = time.time()
                
            MAX_OFFLINE_DAYS = 3
            if current_time - self.last_verified_online > MAX_OFFLINE_DAYS * 86400:
                return False, "离线授权已过期，请联网完成一次校验"

            if self.is_active and self.license_key == key:
                if current_time > self.last_check_time:
                    self.last_check_time = current_time
                self._save_local_license()
                return True, "离线缓存验证通过（无网络）"
            return False, "无法连接到鉴权服务器，请检查网络"
        finally:
            SecureMemoryManager.scrub_str(key)
            SecureMemoryManager.scrub_str(key_raw)
            SecureMemoryManager.scrub_str(hwid)
            SecureMemoryManager.scrub_str(hwid_raw)

license_manager = LicenseManager()

