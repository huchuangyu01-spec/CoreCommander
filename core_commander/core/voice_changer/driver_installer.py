# -*- coding: utf-8 -*-
import os
import sys
import zipfile
import urllib.request
import subprocess
import shutil
import ctypes
from ctypes import wintypes
from core_commander.utils.logger import logger

try:
    import sounddevice as sd
except ImportError:
    sd = None

# Windows Authenticode signature verification structures and constants
class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

class WINTRUST_FILE_INFO(ctypes.Structure):
    _fields_ = [
        ("cbStruct", wintypes.DWORD),
        ("pcwszFilePath", wintypes.LPCWSTR),
        ("hFile", wintypes.HANDLE),
        ("pgKnownSubject", ctypes.c_void_p),
    ]

class WINTRUST_DATA(ctypes.Structure):
    _fields_ = [
        ("cbStruct", wintypes.DWORD),
        ("pPolicyCallbackData", ctypes.c_void_p),
        ("pSIPClientData", ctypes.c_void_p),
        ("dwUIChoice", wintypes.DWORD),
        ("fdwRevocationChecks", wintypes.DWORD),
        ("dwUnionChoice", wintypes.DWORD),
        ("pFile", ctypes.POINTER(WINTRUST_FILE_INFO)),
        ("dwStateAction", wintypes.DWORD),
        ("hWVTStateData", wintypes.HANDLE),
        ("pwszURLReference", wintypes.LPCWSTR),
        ("dwProvFlags", wintypes.DWORD),
        ("dwUIContext", wintypes.DWORD),
    ]

WTD_UI_NONE = 2
WTD_REVOKE_NONE = 0
WTD_CHOICE_FILE = 1
WTD_STATEACTION_IGNORE = 0
WTD_STATEACTION_CLOSE = 2

WINTRUST_ACTION_GENERIC_VERIFY_V2 = GUID(
    0x00AAC56B, 0xCD44, 0x11d0, 
    (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE)
)

def verify_file_signature(file_path: str) -> bool:
    """Uses WinVerifyTrust to verify the Authenticode signature of the executable."""
    if not os.path.exists(file_path):
        logger.error(f"Signature verification failed: File not found {file_path}")
        return False
    try:
        wintrust = ctypes.windll.wintrust
        
        file_info = WINTRUST_FILE_INFO()
        file_info.cbStruct = ctypes.sizeof(WINTRUST_FILE_INFO)
        file_info.pcwszFilePath = file_path
        file_info.hFile = None
        file_info.pgKnownSubject = None
        
        wtd = WINTRUST_DATA()
        wtd.cbStruct = ctypes.sizeof(WINTRUST_DATA)
        wtd.pPolicyCallbackData = None
        wtd.pSIPClientData = None
        wtd.dwUIChoice = WTD_UI_NONE
        wtd.fdwRevocationChecks = WTD_REVOKE_NONE
        wtd.dwUnionChoice = WTD_CHOICE_FILE
        wtd.pFile = ctypes.pointer(file_info)
        wtd.dwStateAction = WTD_STATEACTION_IGNORE
        wtd.hWVTStateData = None
        wtd.pwszURLReference = None
        wtd.dwProvFlags = 0x00000040 # WTD_REVOCATION_CHECK_NONE
        wtd.dwUIContext = 0
        
        wintrust.WinVerifyTrust.argtypes = [wintypes.HWND, ctypes.POINTER(GUID), ctypes.c_void_p]
        wintrust.WinVerifyTrust.restype = wintypes.LONG
        
        result = wintrust.WinVerifyTrust(None, ctypes.byref(WINTRUST_ACTION_GENERIC_VERIFY_V2), ctypes.byref(wtd))
        
        if wtd.hWVTStateData:
            wtd.dwStateAction = WTD_STATEACTION_CLOSE
            wintrust.WinVerifyTrust(None, ctypes.byref(WINTRUST_ACTION_GENERIC_VERIFY_V2), ctypes.byref(wtd))
            
        return result == 0
    except Exception as e:
        logger.error(f"Error during WinVerifyTrust execution for {file_path}: {e}")
        return False

VBCABLE_URL = "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack43.zip"

DOWNLOAD_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "CoreCommander", "drivers")
ZIP_PATH = os.path.join(DOWNLOAD_DIR, "VBCABLE_Driver.zip")
EXTRACT_DIR = os.path.join(DOWNLOAD_DIR, "VBCABLE_Driver")

def is_driver_installed(force_refresh: bool = False) -> bool:
    """
    通过 sounddevice 库查询系统输入输出设备，检查是否存在包含 'CABLE' 或 'VB-Audio' 关键字的虚拟声卡。
    """
    if sd is None:
        logger.warning("sounddevice is not installed, cannot verify driver via sd.")
        return False
    
    try:
        if force_refresh:
            try:
                sd._terminate()
                sd._initialize()
            except Exception:
                pass
        devices = sd.query_devices()
        for dev in devices:
            name = dev.get('name', '')
            if 'CABLE' in name or 'VB-Audio' in name:
                logger.info(f"Detected VB-Cable device: {name}")
                return True
    except Exception as e:
        logger.error(f"Error querying audio devices: {e}")
    return False

def install_driver(progress_callback=None) -> bool:
    """
    静默下载并安装 VB-Cable 虚拟声卡驱动
    """
    if is_driver_installed():
        logger.info("VB-Cable driver is already installed.")
        return True

    try:
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        
        # 1. 下载驱动包
        if not os.path.exists(ZIP_PATH):
            logger.info(f"Downloading VB-Cable driver from {VBCABLE_URL}...")
            if progress_callback:
                progress_callback("正在下载虚拟声卡驱动...", 10)
            
            # 使用 urllib 下载
            urllib.request.urlretrieve(VBCABLE_URL, ZIP_PATH)
            logger.info("Download completed.")

        # 2. 解压驱动包
        if progress_callback:
            progress_callback("正在解压驱动安装程序...", 40)
        
        if os.path.exists(EXTRACT_DIR):
            shutil.rmtree(EXTRACT_DIR)
        
        with zipfile.ZipFile(ZIP_PATH, 'r') as zip_ref:
            # Prevent Zip Slip / Path Traversal
            resolved_dest = os.path.abspath(EXTRACT_DIR)
            for member in zip_ref.infolist():
                target_path = os.path.abspath(os.path.join(resolved_dest, member.filename))
                try:
                    common = os.path.commonpath([resolved_dest, target_path])
                except ValueError:
                    common = ""
                if common != resolved_dest:
                    raise PermissionError(f"Directory traversal attempt detected in ZIP archive: {member.filename}")
            zip_ref.extractall(EXTRACT_DIR)
        logger.info("Extraction completed.")

        # 3. 执行静默安装
        if progress_callback:
            progress_callback("正在安装驱动（可能会有短暂的系统音频重置）...", 70)
        
        setup_exe = os.path.join(EXTRACT_DIR, "VBCABLE_Setup_x64.exe")
        if not os.path.exists(setup_exe):
            setup_exe = os.path.join(EXTRACT_DIR, "VBCABLE_Setup.exe") # 备用32位
            
        if not os.path.exists(setup_exe):
            logger.error("VBCABLE setup executable not found.")
            return False

        if not verify_file_signature(setup_exe):
            logger.error(f"Security error: Digital signature verification failed for VB-Cable setup executable: {setup_exe}!")
            return False

        logger.info(f"Triggering UAC prompt for installation via ShellExecuteW: {setup_exe} -i -h")
        
        # -i 为安装，-h 为隐藏窗口运行（静默）
        import time
        import ctypes
        
        # Use ctypes.ShellExecuteW to trigger native UAC window securely without PowerShell command injection risks
        ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", setup_exe, "-i -h", None, 1)
        if ret <= 32:
            logger.error(f"UAC elevation refused or ShellExecuteW failed with error code: {ret}")
            return False
        logger.info("VB-Cable installer executed successfully via ShellExecuteW.")
            
        logger.info("Installer launched. Polling for driver installation completion...")
        if progress_callback:
            progress_callback("请在弹出的 UAC 提示框中点击“是”允许安装，然后稍候...", 80)
            
        # 轮询等待驱动真正安装完成 (最多 45 秒)
        for _ in range(45):
            time.sleep(1)
            if is_driver_installed():
                return True
        logger.error("Timeout waiting for VB-Cable driver to appear.")
        return False
    except Exception as e:
        logger.error(f"Failed to install VB-Cable driver: {e}")
        return False
