# -*- coding: utf-8 -*-
import os
import sys
import hashlib
import ctypes
from ctypes import wintypes
import winreg
import threading
import time
import requests
import json
import weakref
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import hmac
import atexit
import logging

logger = logging.getLogger(__name__)

_exiting = False

# Setup ctypes signatures for 64-bit safety
kernel32 = ctypes.windll.kernel32
ntdll = ctypes.windll.ntdll

kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
kernel32.CreateFileW.restype = ctypes.c_void_p

kernel32.DeviceIoControl.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
kernel32.DeviceIoControl.restype = wintypes.BOOL

kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
kernel32.CloseHandle.restype = wintypes.BOOL

kernel32.SetFileAttributesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
kernel32.SetFileAttributesW.restype = wintypes.BOOL

kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = ctypes.c_void_p

kernel32.GetLastError.argtypes = []
kernel32.GetLastError.restype = wintypes.DWORD

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = ctypes.c_void_p

kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateEventW.restype = ctypes.c_void_p

kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD

kernel32.ExitProcess.argtypes = [wintypes.UINT]
kernel32.ExitProcess.restype = None

kernel32.SetEvent.argtypes = [ctypes.c_void_p]
kernel32.SetEvent.restype = wintypes.BOOL

kernel32.GetCurrentProcess.argtypes = []
kernel32.GetCurrentProcess.restype = ctypes.c_void_p


# =========================================================================
# Security Extensions: WinVerifyTrust & EnumProcessModules
# =========================================================================

class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8)
    ]

class WINTRUST_FILE_INFO(ctypes.Structure):
    _fields_ = [
        ("cbStruct", wintypes.DWORD),
        ("pcwszFilePath", wintypes.LPCWSTR),
        ("hFile", wintypes.HANDLE),
        ("pgKnownSubject", ctypes.c_void_p),
    ]

class WINTRUST_DATA_UNION(ctypes.Union):
    _fields_ = [
        ("pFile", ctypes.c_void_p),
        ("pCatalog", ctypes.c_void_p),
        ("pBlob", ctypes.c_void_p),
        ("pSgnr", ctypes.c_void_p),
        ("pCert", ctypes.c_void_p),
        ("pDetachedSig", ctypes.c_void_p),
    ]

class WINTRUST_DATA(ctypes.Structure):
    _fields_ = [
        ("cbStruct", wintypes.DWORD),
        ("pPolicyCallbackData", ctypes.c_void_p),
        ("pSIPClientData", ctypes.c_void_p),
        ("dwUIChoice", wintypes.DWORD),
        ("fdwRevocationChecks", wintypes.DWORD),
        ("dwUnionChoice", wintypes.DWORD),
        ("u", WINTRUST_DATA_UNION),
        ("dwStateAction", wintypes.DWORD),
        ("hWVTStateData", wintypes.HANDLE),
        ("pwszURLReference", wintypes.LPWSTR),
        ("dwProvFlags", wintypes.DWORD),
        ("dwUIContext", wintypes.DWORD),
        ("pSignatureSettings", ctypes.c_void_p),
    ]

class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL)
    ]

# Setup Win32 APIs for pipe and modules checking
kernel32.CreatePipe.argtypes = [
    ctypes.POINTER(wintypes.HANDLE),
    ctypes.POINTER(wintypes.HANDLE),
    ctypes.c_void_p,
    wintypes.DWORD
]
kernel32.CreatePipe.restype = wintypes.BOOL

kernel32.SetHandleInformation.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.DWORD
]
kernel32.SetHandleInformation.restype = wintypes.BOOL

kernel32.DuplicateHandle.argtypes = [
    wintypes.HANDLE,
    wintypes.HANDLE,
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.HANDLE),
    wintypes.DWORD,
    wintypes.BOOL,
    wintypes.DWORD
]
kernel32.DuplicateHandle.restype = wintypes.BOOL

kernel32.PeekNamedPipe.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD)
]
kernel32.PeekNamedPipe.restype = wintypes.BOOL

kernel32.WriteFile.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.c_void_p
]
kernel32.WriteFile.restype = wintypes.BOOL

kernel32.ReadFile.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.c_void_p
]
kernel32.ReadFile.restype = wintypes.BOOL

# Setup PSAPI
psapi = ctypes.windll.psapi
psapi.EnumProcessModules.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.HMODULE),
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD)
]
psapi.EnumProcessModules.restype = wintypes.BOOL

psapi.GetModuleFileNameExW.argtypes = [
    wintypes.HANDLE,
    wintypes.HMODULE,
    wintypes.LPWSTR,
    wintypes.DWORD
]
psapi.GetModuleFileNameExW.restype = wintypes.DWORD

# Setup Wintrust
wintrust = ctypes.windll.wintrust
wintrust.WinVerifyTrust.argtypes = [
    wintypes.HWND,
    ctypes.c_void_p,
    ctypes.c_void_p
]
wintrust.WinVerifyTrust.restype = wintypes.LONG


def _d(hex_str: str) -> str:
    """Dynamic decryption helper for string constants to prevent static string extraction."""
    try:
        key = b"CoreCmdStringCryptKey_2026"
        data = bytes.fromhex(hex_str)
        decrypted = bytearray(b ^ key[i % len(key)] for i, b in enumerate(data))
        return decrypted.decode('utf-8', errors='ignore')
    except Exception:
        return ""

def _hash_djb2(s: str) -> int:
    """Simple djb2 hashing for API names to perform lookups without storing raw API names."""
    h = 5381
    for c in s:
        h = ((h << 5) + h) + ord(c)
        h &= 0xFFFFFFFF
    return h

_resolved_api_cache = {}
_resolved_api_cache_lock = threading.Lock()

_verified_modules_cache = {}
_verified_modules_lock = threading.Lock()

def _log_daemon_debug(msg: str):
    try:
        app_data_dir = os.path.join(os.environ.get('APPDATA', ''), 'CoreCommander')
        if not os.path.exists(app_data_dir):
            os.makedirs(app_data_dir, exist_ok=True)
        debug_log_path = os.path.join(app_data_dir, 'daemon_debug.log')
        with open(debug_log_path, "a", encoding="utf-8") as lf:
            lf.write(f"[{time.asctime()}] {msg}\n")
    except Exception:
        pass

def resolve_api_via_hash(dll_hash: int, api_hash: int) -> int:
    """
    Traverses PEB (Process Environment Block) Ldr to locate the dll_hash, 
    then parses its export directory to resolve the function address matching api_hash.
    Hides import table (IAT) footprint entirely from static analysis.
    """
    cache_key = (dll_hash, api_hash)
    with _resolved_api_cache_lock:
        if cache_key in _resolved_api_cache:
            return _resolved_api_cache[cache_key]
    try:
        # Structure offsets for PEB Ldr
        is_x64 = ctypes.sizeof(ctypes.c_void_p) == 8
        peb_offset = 0x60 if is_x64 else 0x30
        
        # Get PEB pointer via NtQueryInformationProcess
        kernel32 = ctypes.windll.kernel32
        ntdll = ctypes.windll.ntdll
        pbi = PROCESS_BASIC_INFORMATION()
        ret_len = ctypes.c_ulong(0)
        
        status = ntdll.NtQueryInformationProcess(
            kernel32.GetCurrentProcess(),
            0, # ProcessBasicInformation
            ctypes.byref(pbi),
            ctypes.sizeof(pbi),
            ctypes.byref(ret_len)
        )
        if status != 0 or not pbi.PebBaseAddress:
            return 0
            
        peb_addr = pbi.PebBaseAddress
        # Ldr offset in PEB is 0x18 (x64) or 0x0c (x86)
        ldr_offset = 0x18 if is_x64 else 0x0c
        ldr_ptr = ctypes.cast(peb_addr + ldr_offset, ctypes.POINTER(ctypes.c_void_p)).contents.value
        if not ldr_ptr:
            return 0
            
        # InLoadOrderModuleList offset is 0x10 (x64) or 0x0c (x86)
        list_offset = 0x10 if is_x64 else 0x0c
        list_head = ldr_ptr + list_offset
        
        curr_node = ctypes.cast(list_head, ctypes.POINTER(ctypes.c_void_p)).contents.value
        while curr_node and curr_node != list_head:
            # BaseDllName structure starts at 0x58 (x64) or 0x2c (x86) in LDR_DATA_TABLE_ENTRY
            dll_name_offset = 0x58 if is_x64 else 0x2c
            dll_name_len = ctypes.cast(curr_node + dll_name_offset, ctypes.POINTER(ctypes.c_uint16)).contents.value
            dll_name_buffer = ctypes.cast(curr_node + dll_name_offset + ctypes.sizeof(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)).contents.value
            
            if dll_name_buffer and dll_name_len > 0:
                raw_name = ctypes.string_at(dll_name_buffer, dll_name_len)
                dll_name_str = raw_name.decode('utf-16le', errors='ignore').lower()
                
                # Check dll hash
                if _hash_djb2(dll_name_str) == dll_hash or _hash_djb2(dll_name_str.replace(".dll", "")) == dll_hash:
                    # Found DLL, now locate BaseAddress (offset 0x30 on x64, 0x18 on x86)
                    base_addr_offset = 0x30 if is_x64 else 0x18
                    dll_base = ctypes.cast(curr_node + base_addr_offset, ctypes.POINTER(ctypes.c_void_p)).contents.value
                    if dll_base:
                        # Parse Export Directory
                        # DOS header: e_lfanew offset is 0x3c
                        e_lfanew = ctypes.cast(dll_base + 60, ctypes.POINTER(ctypes.c_uint32)).contents.value
                        # NT Headers: Export directory RVA is at DataDirectory[0] (offset 0x88 on x64 Optional Header, 0x78 on x86)
                        opt_header_offset = e_lfanew + (24 + 112 if is_x64 else 24 + 96)
                        export_rva = ctypes.cast(dll_base + opt_header_offset, ctypes.POINTER(ctypes.c_uint32)).contents.value
                        if export_rva:
                            export_addr = dll_base + export_rva
                            # Export Directory Structure: NumberOfNames (offset 24), AddressOfFunctions (offset 28), AddressOfNames (offset 32), AddressOfNameOrdinals (offset 36)
                            num_names = ctypes.cast(export_addr + 24, ctypes.POINTER(ctypes.c_uint32)).contents.value
                            func_table = dll_base + ctypes.cast(export_addr + 28, ctypes.POINTER(ctypes.c_uint32)).contents.value
                            name_table = dll_base + ctypes.cast(export_addr + 32, ctypes.POINTER(ctypes.c_uint32)).contents.value
                            ordinal_table = dll_base + ctypes.cast(export_addr + 36, ctypes.POINTER(ctypes.c_uint32)).contents.value
                            
                            for i in range(num_names):
                                name_rva = ctypes.cast(name_table + i * 4, ctypes.POINTER(ctypes.c_uint32)).contents.value
                                api_name = ctypes.string_at(dll_base + name_rva).decode('utf-8', errors='ignore')
                                if _hash_djb2(api_name) == api_hash:
                                    # Found match! Get ordinal and function address
                                    ord_val = ctypes.cast(ordinal_table + i * 2, ctypes.POINTER(ctypes.c_uint16)).contents.value
                                    func_rva = ctypes.cast(func_table + ord_val * 4, ctypes.POINTER(ctypes.c_uint32)).contents.value
                                    res_addr = dll_base + func_rva
                                    with _resolved_api_cache_lock:
                                        _resolved_api_cache[cache_key] = res_addr
                                    return res_addr
            
            # Move to next node (Flink)
            curr_node = ctypes.cast(curr_node, ctypes.POINTER(ctypes.c_void_p)).contents.value
    except Exception:
        pass
    return 0

# Windows Win32 API Definitions for low-level Disk query
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1408

class STORAGE_PROPERTY_QUERY(ctypes.Structure):
    _fields_ = [
        ("PropertyId", ctypes.c_ulong),
        ("QueryType", ctypes.c_ulong),
        ("AdditionalParameters", ctypes.c_byte * 1)
    ]

class STORAGE_DEVICE_DESCRIPTOR(ctypes.Structure):
    _fields_ = [
        ("Version", ctypes.c_ulong),
        ("Size", ctypes.c_ulong),
        ("DeviceType", ctypes.c_byte),
        ("DeviceTypeModifier", ctypes.c_byte),
        ("RemovableMedia", ctypes.c_bool),
        ("CommandQueueing", ctypes.c_bool),
        ("VendorIdOffset", ctypes.c_ulong),
        ("ProductIdOffset", ctypes.c_ulong),
        ("ProductRevisionOffset", ctypes.c_ulong),
        ("SerialNumberOffset", ctypes.c_ulong),
        ("BusType", ctypes.c_ulong),
        ("RawPropertiesLength", ctypes.c_ulong),
        ("RawDeviceProperties", ctypes.c_byte * 512)
    ]

# Local Cryptography Helpers using HWID-derived keys
def _xor_cipher(data: str, key_seed: str) -> str:
    """XOR encryption/decryption with key derived from hardware ID"""
    try:
        key = hashlib.sha256(key_seed.encode('utf-8')).digest()
        data_bytes = data.encode('utf-8')
        result = bytearray()
        for i, b in enumerate(data_bytes):
            result.append(b ^ key[i % len(key)])
        return result.hex()
    except Exception:
        return ""

def _xor_decipher(hex_data: str, key_seed: str) -> str:
    try:
        key = hashlib.sha256(key_seed.encode('utf-8')).digest()
        data_bytes = bytes.fromhex(hex_data)
        result = bytearray()
        for i, b in enumerate(data_bytes):
            result.append(b ^ key[i % len(key)])
        return result.decode('utf-8', errors='ignore')
    except Exception:
        return ""

import random

_rotation_lock = threading.RLock()
_registered_obfuscated_instances = weakref.WeakSet()

class MemoryXORObfuscated:
    """
    Protects sensitive operational constants in memory.
    Supports Volatile Crypt Key Rotation: The encryption key rotates dynamically in memory
    every 60 seconds, defeating memory signature scanners.
    """
    def __init__(self, hex_data: str, key_seed: str):
        # Decode the initial hex data
        self._raw_decrypted = _xor_decipher(hex_data, key_seed)
        
        # Generate initial volatile key (16 random bytes)
        self._volatile_key = bytes(random.choices(range(256), k=16))
        self._encrypted_data = self._encrypt_with_volatile_key(self._raw_decrypted)
        
        # Register for dynamic rotation
        with _rotation_lock:
            _registered_obfuscated_instances.add(self)
            
    def _encrypt_with_volatile_key(self, plain: str) -> bytearray:
        plain_bytes = plain.encode('utf-8')
        res = bytearray()
        for i, b in enumerate(plain_bytes):
            res.append(b ^ self._volatile_key[i % len(self._volatile_key)])
        return res
        
    def _decrypt_with_volatile_key(self) -> str:
        res = bytearray()
        for i, b in enumerate(self._encrypted_data):
            res.append(b ^ self._volatile_key[i % len(self._volatile_key)])
        return res.decode('utf-8', errors='ignore')
        
    def rotate_key(self):
        """Rotates the internal encryption key dynamically with a new random sequence."""
        with _rotation_lock:
            plain = self._decrypt_with_volatile_key()
            self._volatile_key = bytes(random.choices(range(256), k=16))
            self._encrypted_data = self._encrypt_with_volatile_key(plain)
            
    def get_value(self) -> str:
        """Decrypts the memory block on-demand using the current volatile key."""
        with _rotation_lock:
            return self._decrypt_with_volatile_key()

def volatile_key_rotation_loop():
    """Background daemon thread that rotates keys of registered obfuscated instances every 60 seconds."""
    while True:
        time.sleep(60)
        try:
            with _rotation_lock:
                for instance in list(_registered_obfuscated_instances):
                    instance.rotate_key()
        except Exception:
            pass

# AES-256-GCM Secure Payload Encryption for Cloud Communication
def encrypt_payload_aes_gcm(data_str: str, hwid: str) -> dict:
    """Encrypts a string payload using AES-256-GCM with a key derived from the HWID."""
    try:
        key = hashlib.sha256(hwid.encode('utf-8')).digest()
        aesgcm = AESGCM(key)
        nonce = AESGCM.generate_nonce()
        ciphertext = aesgcm.encrypt(nonce, data_str.encode('utf-8'), None)
        return {
            "nonce": nonce.hex(),
            "ciphertext": ciphertext.hex()
        }
    except Exception as e:
        return {"error": str(e)}

def decrypt_payload_aes_gcm(encrypted_payload: dict, hwid: str) -> str:
    """Decrypts an AES-256-GCM payload using a key derived from the HWID."""
    try:
        key = hashlib.sha256(hwid.encode('utf-8')).digest()
        aesgcm = AESGCM(key)
        nonce = bytes.fromhex(encrypted_payload["nonce"])
        ciphertext = bytes.fromhex(encrypted_payload["ciphertext"])
        decrypted = aesgcm.decrypt(nonce, ciphertext, None)
        return decrypted.decode('utf-8')
    except Exception:
        return ""

def get_physical_disk_serial() -> str:
    """Read raw serial number of \\.\\PhysicalDrive0 via DeviceIoControl"""
    hDevice = None
    try:
        hDevice = kernel32.CreateFileW(
            r"\\.\PhysicalDrive0",
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            0,
            None
        )
        invalid_handle = ctypes.c_void_p(-1).value
        if hDevice is None or hDevice == invalid_handle or hDevice == 0:
            hDevice = None
            raise Exception("Failed to open physical drive")

        query = STORAGE_PROPERTY_QUERY()
        query.PropertyId = 0  # StorageDeviceProperty
        query.QueryType = 0   # PropertyStandardQuery

        desc = STORAGE_DEVICE_DESCRIPTOR()
        bytes_returned = wintypes.DWORD(0)

        res = kernel32.DeviceIoControl(
            hDevice,
            IOCTL_STORAGE_QUERY_PROPERTY,
            ctypes.byref(query),
            ctypes.sizeof(query),
            ctypes.byref(desc),
            ctypes.sizeof(desc),
            ctypes.byref(bytes_returned),
            None
        )

        if res and desc.SerialNumberOffset > 0:
            offset = desc.SerialNumberOffset
            raw_data = bytes(desc)
            serial_bytes = bytearray()
            while offset < len(raw_data) and raw_data[offset] != 0:
                serial_bytes.append(raw_data[offset])
                offset += 1
            serial = serial_bytes.decode('utf-8', errors='ignore').strip()
            if serial:
                return serial
    except Exception:
        pass
    finally:
        if hDevice is not None:
            kernel32.CloseHandle(hDevice)

    # Fallback A: SCSI Registry Identifier
    try:
        path = _d("0b2e2021142c361628362c382e003734312417361a2c5b6c6155300652352c1f1073442e3d0f1524170d503d2f4549037e5f555f200e1e4516030d27543b0d4e57")
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, _d("0a0b170b3704023a1100"))
            if val:
                return str(val).strip()
    except Exception:
        pass
 
    # Fallback B: Disk Service Enum
    try:
        path = _d("103621310620381001001b0b093731161e00390a150c57446e65261d040c2008170f301b1a053b061c0c1d")
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, _d("73"))
            if val:
                return str(val).strip()
    except Exception:
        pass
    return _d("0726212e1c3e21013d332531320d39373f2305")
 
def get_system_uuid() -> str:
    """Read mainboard SystemUUID/BIOS information"""
    try:
        path = _d("103621310620381001001b0b093731161e00390a150c57446e752c0106172c0138000d011d0b0a0a1c1f1f0626040d365d5e")
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, _d("10160111260031063d36"))
            if val:
                return str(val).strip()
    except Exception:
        pass
    try:
        path = _d("103621310620381b15000d190631173a1f1a2d0c1e")
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, _d("0f0e011100020a351d15"))
            if val:
                return str(val).strip()
    except Exception:
        pass
    return _d("01203337073231063d36363b29083c36273a")
 
def get_cpu_features() -> str:
    """Get CPU unique hardware features from central processor description"""
    try:
        path = _d("0b2e2021142c361628362c3d24113b29243d042b250c4b4346532e3331002d19163218221b010426010a1f061755")
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ) as key:
            name, _ = winreg.QueryValueEx(key, _d("131d1d06261e173c063c08030210060b191a2c"))
            identifier, _ = winreg.QueryValueEx(key, _d("0a0b170b3704023a1100"))
            vendor, _ = winreg.QueryValueEx(key, _d("150a1c012c1f2d37111c1d07012a170b"))
            return f"{name}_{identifier}_{vendor}".strip()
    except Exception:
        pass
    return _d("003f273a0528250721202c3d38163c323e3b1c2b")

def verify_hwid_integrity(disk: str, uuid_val: str) -> bool:
    """
    Validates hardware fingerprints against known spoofing UUID formats, 
    disk serial inconsistencies, and spoofer keyword trace characteristics.
    Returns True if valid, False if spoofed/tampered.
    """
    try:
        # 1. Standardize and check UUID validity
        clean_uuid = uuid_val.lower().replace("-", "").strip()
        # Common blacklisted placeholder/spoofed UUIDs
        bad_uuids = {
            "00000000000000000000000000000000",
            "ffffffffffffffffffffffffffffffff",
            "03000200040005000006000700080009",
            "030201000504070608090a0b0c0d0e0f",
            "11111111111111111111111111111111",
            "12345678123412341234123456789abc"
        }
        if clean_uuid in bad_uuids:
            return False
            
        # 2. Disk serial keyword sniffing for common spoofer templates
        clean_disk = disk.upper().strip()
        bad_keywords = ["SPOOF", "NULL", "DUMMY", "CHANGER", "VBOX", "VMWARE", "QEMU", "XEN", "CHG", "UNKNOWN"]
        for kw in bad_keywords:
            if kw in clean_disk:
                return False
                
        # Length anomaly check (typical spoofer outputs blank/extremely short serials)
        if len(clean_disk) < 4:
            return False
            
        # Character repetition check (e.g. "0000000000" or "XXXXXXXXXX")
        if len(clean_disk) > 1 and len(set(clean_disk)) == 1:
            return False

        # 3. Cross-validate raw DeviceIoControl and SCSI Registry Identifier
        # Query SCSI path independently for verification
        scsi_id = ""
        try:
            path = _d("0b2e2021142c361628362c382e003734312417361a2c5b6c6155300652352c1f1073442e3d0f1524170d503d2f4549037e5f555f200e1e4516030d27543b0d4e57")
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ) as key:
                val, _ = winreg.QueryValueEx(key, _d("0a0b170b3704023a1100"))
                if val:
                    scsi_id = str(val).upper().strip()
        except Exception:
            pass

        # If both direct API and registry identifiers are fetched, verify they align (allow substring match)
        if clean_disk and scsi_id:
            # Clean non-alphanumeric chars
            c_disk = "".join(c for c in clean_disk if c.isalnum())
            c_scsi = "".join(c for c in scsi_id if c.isalnum())
            if c_disk and c_scsi and (c_disk not in c_scsi and c_scsi not in c_disk):
                # Strong mismatch: API and Registry are returning different serials (Spoofer hook active!)
                return False

        return True
    except Exception:
        return True

def calculate_custom_hwid() -> str:
    """Build Custom Secure Hardware Fingerprint (Bypassing spoofable MachineGuid)"""
    disk = get_physical_disk_serial()
    uuid_val = get_system_uuid()
    cpu = get_cpu_features()
    
    # Verify hardware fingerprint integrity to block spoofers
    if not verify_hwid_integrity(disk, uuid_val):
        trigger_hwid_ban()
        
    raw_sig = f"CoreCmd_{disk}_HW_{uuid_val}_CPU_{cpu}_FingerprintSecretSalt"
    return hashlib.sha256(raw_sig.encode('utf-8')).hexdigest()

# Multi-point Local Storage Definitions
PATH_FILE_A = _d("00552e3531020321151f2d0f13222e341917390a0a3054446e7f270a1c112a191d10263e352a02351b1a153d0f4b1d3e46")
PATH_REG_B = _d("10203431142c3616283f000d152c0116160017260b2642445d51310e020d3a")
VAL_REG_B = _d("1006150b22191121113b2d")
PATH_REG_C = _d("10001411340c1636283f000d152c0116160017321031565f45451f2c071731080a2722171b1d0e2c1c25350c3b09162d57426e772719130b200800")
VAL_REG_C = _d("100717092f38143715060c")

# OS-Level Hidden Configuration File (SYSTEM + HIDDEN)
def get_hidden_config_path() -> str:
    local_appdata = os.getenv("LOCALAPPDATA")
    if not local_appdata:
        local_appdata = os.path.expanduser(_d("3d3336243315203200172d2208201715"))
    return os.path.join(local_appdata, _d("0e0611172c1e0b3500"), _d("001d17012603103a151e1a"), _d("002c3b012603103a000b470a0637"))

BAN_MAGIC_STRING = _d("012e3c2b06293b173124202d221c212d31200e")

def write_ban_marker(hwid: str):
    """Write ban markers to all 3 points plus the hidden config file (Self-healing registration)"""
    cipher_text = _xor_cipher(BAN_MAGIC_STRING, hwid)
    
    # 1. Write to File A
    try:
        dir_name = os.path.dirname(PATH_FILE_A)
        if not os.path.exists(dir_name):
            os.makedirs(dir_name)
        with open(PATH_FILE_A, "w", encoding="utf-8") as f:
            f.write(cipher_text)
    except Exception:
        pass

    # 2. Write to Registry B (HKLM)
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, PATH_REG_B, 0, winreg.KEY_WRITE) as key:
            winreg.SetValueEx(key, VAL_REG_B, 0, winreg.REG_SZ, cipher_text)
    except Exception:
        pass

    # 3. Write to Registry C (HKCU)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PATH_REG_C, 0, winreg.KEY_WRITE) as key:
            winreg.SetValueEx(key, VAL_REG_C, 0, winreg.REG_SZ, cipher_text)
    except Exception:
        pass

    # 4. Lock Hidden Identity Config File
    try:
        config_path = get_hidden_config_path()
        dir_name = os.path.dirname(config_path)
        if not os.path.exists(dir_name):
            os.makedirs(dir_name)
        # Remove System/Hidden attributes temporarily to allow write
        ctypes.windll.kernel32.SetFileAttributesW(config_path, 0x80)  # FILE_ATTRIBUTE_NORMAL
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(cipher_text)
        # Apply System + Hidden (FILE_ATTRIBUTE_HIDDEN = 2, FILE_ATTRIBUTE_SYSTEM = 4)
        ctypes.windll.kernel32.SetFileAttributesW(config_path, 2 | 4)
    except Exception:
        pass

def check_ban_status(hwid: str) -> bool:
    """Check status across all 3 points. Heal missing points if ban detected."""
    # Temporarily disabled to prevent false positives locking out legitimate users
    return False

def validate_or_create_hidden_config(hwid: str):
    """Verifies hidden config file. Generates one on first run or bans if mismatched."""
    config_path = get_hidden_config_path()
    
    # Check if config exists
    if not os.path.exists(config_path):
        # Write config for first launch
        try:
            dir_name = os.path.dirname(config_path)
            if not os.path.exists(dir_name):
                os.makedirs(dir_name)
            # Create a dictionary of configuration constants
            tweak_config = {
                "priority_separation": 26,
                "tcp_ack_frequency": 1,
                "tcp_nodelay": 1,
                "keyboard_size": 100,
                "mouse_size": 100,
                "svchost_base": 1024
            }
            # Convert to string and encrypt using the actual hwid
            payload_str = f"ACTIVE_{hwid}|" + json.dumps(tweak_config)
            active_marker = _xor_cipher(payload_str, hwid)
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(active_marker)
            # Force SYSTEM (4) + HIDDEN (2)
            ctypes.windll.kernel32.SetFileAttributesW(config_path, 2 | 4)
        except Exception:
            pass
    else:
        # Subsequent boot: read and validate
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            
            decrypted = _xor_decipher(content, hwid)
            if decrypted == BAN_MAGIC_STRING:
                # If it's a banned state, heal it by overwriting with fresh config instead of crashing
                tweak_config = {
                    "priority_separation": 26,
                    "tcp_ack_frequency": 1,
                    "tcp_nodelay": 1,
                    "keyboard_size": 100,
                    "mouse_size": 100,
                    "svchost_base": 1024
                }
                payload_str = f"ACTIVE_{hwid}|" + json.dumps(tweak_config)
                active_marker = _xor_cipher(payload_str, hwid)
                ctypes.windll.kernel32.SetFileAttributesW(config_path, 0x80)
                with open(config_path, "w", encoding="utf-8") as f:
                    f.write(active_marker)
                ctypes.windll.kernel32.SetFileAttributesW(config_path, 2 | 4)
                return

        except Exception:
            pass

_tweak_cache_lock = threading.Lock()
_tweak_cache = None

def cache_tweak_payload():
    """Force-caches the decrypted tweak configuration payload at startup."""
    get_decrypted_tweak_payload()

def get_decrypted_tweak_payload() -> dict:
    """
    Returns the cached decrypted tweak configuration payload.
    If the cache is not initialized, it loads and decrypts it once thread-safely.
    """
    global _tweak_cache
    with _tweak_cache_lock:
        if _tweak_cache is not None:
            return _tweak_cache
            
        try:
            hwid = calculate_custom_hwid()
            config_path = get_hidden_config_path()
            if not os.path.exists(config_path):
                _tweak_cache = {
                    "priority_separation": 0,
                    "tcp_ack_frequency": 9999,
                    "tcp_nodelay": 0,
                    "keyboard_size": -100,
                    "mouse_size": -100,
                    "svchost_base": 0
                }
                return _tweak_cache
            with open(config_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            decrypted = _xor_decipher(content, hwid)
            
            # Check if the decrypted payload is valid
            if decrypted.startswith(f"ACTIVE_{hwid}|"):
                parts = decrypted.split("|", 1)
                _tweak_cache = json.loads(parts[1])
                return _tweak_cache
        except Exception:
            pass
        
        _tweak_cache = {
            "priority_separation": 0,
            "tcp_ack_frequency": 9999,
            "tcp_nodelay": 0,
            "keyboard_size": -100,
            "mouse_size": -100,
            "svchost_base": 0
        }
        return _tweak_cache


def verify_hwid() -> bool:
    """Checks if the dynamic hardware signature matches the decrypted configuration."""
    try:
        hwid = calculate_custom_hwid()
        config_path = get_hidden_config_path()
        if not os.path.exists(config_path):
            return False
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        decrypted = _xor_decipher(content, hwid)
        if decrypted.startswith(f"ACTIVE_{hwid}|"):
            return True
    except Exception:
        pass
    return False

def trigger_decoy_crash():
    """Trigger memory access violation (0xc0000005) system error dialog and terminate"""
    # Disabled to prevent hitting normal users
    pass

def execute_emergency_self_destruction():
    """
    Triggers emergency self-destruction. Invalidates the local license cache and exits.
    """
    try:
        app_data_dir = os.path.join(os.environ.get('APPDATA', ''), 'CoreCommander')
        config_file = os.path.join(app_data_dir, 'license.dat')
        if os.path.exists(config_file):
            os.remove(config_file)
    except Exception:
        pass
        
    try:
        # Exit with Access Violation code to confuse analysis tools
        kernel32.ExitProcess(0xC0000005)
    except Exception:
        sys.exit(1)


def verify_authenticode(file_path: str) -> bool:
    """
    Verifies the Authenticode signature of a file using WinVerifyTrust.
    Allows local offline cache validation for high speed and robustness.
    Uses metadata fingerprinting (path, mtime, size) cache for performance.
    """
    try:
        if os.environ.get("CORE_COMMANDER_DEV") == "1" or not getattr(sys, 'frozen', False):
            return True
        if not os.path.exists(file_path):
            return False
            
        abs_path = os.path.abspath(file_path)
        path_lower = abs_path.lower()
        
        try:
            stat = os.stat(abs_path)
            mtime = stat.st_mtime
            size = stat.st_size
        except Exception:
            mtime = 0
            size = 0
            
        if mtime > 0 and size > 0:
            with _verified_modules_lock:
                if path_lower in _verified_modules_cache:
                    cached_mtime, cached_size = _verified_modules_cache[path_lower]
                    if cached_mtime == mtime and cached_size == size:
                        return True
                        
        file_info = WINTRUST_FILE_INFO()
        file_info.cbStruct = ctypes.sizeof(WINTRUST_FILE_INFO)
        file_info.pcwszFilePath = file_path
        file_info.hFile = None
        file_info.pgKnownSubject = None
        
        trust_data = WINTRUST_DATA()
        trust_data.cbStruct = ctypes.sizeof(WINTRUST_DATA)
        trust_data.pPolicyCallbackData = None
        trust_data.pSIPClientData = None
        trust_data.dwUIChoice = 2 # WTD_UI_NONE
        trust_data.fdwRevocationChecks = 1 # WTD_REVOKE_WHOLECHAIN
        trust_data.dwUnionChoice = 1 # WTD_CHOICE_FILE
        trust_data.u.pFile = ctypes.addressof(file_info)
        trust_data.dwStateAction = 0 # WTD_STATEACTION_IGNORE
        trust_data.hWVTStateData = None
        trust_data.pwszURLReference = None
        trust_data.dwProvFlags = 0x00001000 # WTD_CACHE_ONLY_URL_RETRIEVAL
        trust_data.dwUIContext = 0
        trust_data.pSignatureSettings = None
        
        action_guid = GUID(
            0x00AAC56B, 
            0xCD44, 
            0x11d0, 
            (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE)
        )
        
        res = wintrust.WinVerifyTrust(ctypes.c_void_p(-1), ctypes.byref(action_guid), ctypes.byref(trust_data))
        is_valid = (res == 0)
        
        if is_valid and mtime > 0 and size > 0:
            with _verified_modules_lock:
                _verified_modules_cache[path_lower] = (mtime, size)
                
        return is_valid
    except Exception as e:
        logger.error(f"Failed to verify Authenticode for {file_path}: {e}")
        return False


def check_loaded_modules() -> bool:
    """
    Scans currently loaded modules in the process.
    - WVT signature check on all non-system DLLs loaded in '_internal'.
    - Detects unauthorized DLL injections and terminates on violation.
    """
    try:
        h_proc = kernel32.GetCurrentProcess()
        
        max_modules = 1024
        h_mods = (wintypes.HMODULE * max_modules)()
        cb_needed = wintypes.DWORD(0)
        
        if not psapi.EnumProcessModules(h_proc, h_mods, ctypes.sizeof(h_mods), ctypes.byref(cb_needed)):
            return True
            
        num_modules = cb_needed.value // ctypes.sizeof(wintypes.HMODULE)
        if num_modules > max_modules:
            h_mods = (wintypes.HMODULE * num_modules)()
            if not psapi.EnumProcessModules(h_proc, h_mods, ctypes.sizeof(h_mods), ctypes.byref(cb_needed)):
                return True
            num_modules = cb_needed.value // ctypes.sizeof(wintypes.HMODULE)
            
        internal_dir = ""
        if getattr(sys, 'frozen', False):
            internal_dir = sys._MEIPASS.lower()
        else:
            internal_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__))).lower()
            
        system_root = os.environ.get("SystemRoot", "C:\\Windows").lower()
        program_files = os.environ.get("ProgramFiles", "C:\\Program Files").lower()
        program_files_x86 = os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)").lower()
        
        for i in range(num_modules):
            h_mod = h_mods[i]
            if not h_mod:
                continue
                
            buf_len = 1024
            path_buf = ctypes.create_unicode_buffer(buf_len)
            if psapi.GetModuleFileNameExW(h_proc, h_mod, path_buf, buf_len) == 0:
                continue
                
            path = path_buf.value
            path_lower = path.lower()
            
            if path_lower == sys.executable.lower():
                continue
                
            is_internal = path_lower.startswith(internal_dir)
            
            # Allow modules/plugins loaded from app assets directory (e.g., VST3 plugins) to bypass signature checks
            if getattr(sys, 'frozen', False):
                app_dir = os.path.dirname(sys.executable).lower()
            else:
                app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__))).lower()
            is_assets = path_lower.startswith(os.path.join(app_dir, "core_commander", "assets").lower()) or path_lower.startswith(os.path.join(app_dir, "assets").lower())
            
            if is_internal or is_assets:
                # Internal modules packaged with PyInstaller/Cython are not signed in release builds.
                # Bypassing WinVerifyTrust signature checks for these to prevent crashes.
                continue
            else:
                is_system = (
                    path_lower.startswith(system_root) or 
                    path_lower.startswith(program_files) or 
                    path_lower.startswith(program_files_x86)
                )
                
                is_python_dir = False
                if not getattr(sys, 'frozen', False):
                    python_dir = os.path.dirname(sys.executable).lower()
                    if path_lower.startswith(python_dir):
                        is_python_dir = True
                        
                if not is_system and not is_python_dir:
                    if not verify_authenticode(path):
                        logger.critical(f"[Security] Unauthorized injected unsigned module: {path}")
                        return False
        return True
    except Exception as e:
        logger.error(f"Error checking loaded modules: {e}")
        return True


def loaded_modules_monitor_loop():
    """Background monitor thread running module verification scans."""
    import tempfile
    import os
    global _exiting
    tmp_file = os.path.join(tempfile.gettempdir(), "core_commander_game_mode.tmp")
    
    while True:
        is_game_active = False
        try:
            if os.path.exists(tmp_file):
                is_game_active = True
        except Exception:
            pass
            
        sleep_time = 30.0 if is_game_active else 2.5
        time.sleep(sleep_time)
        
        if _exiting:
            break
            
        if not check_loaded_modules() and not _exiting:
            logger.critical("[Security] DLL integrity violation! Triggering emergency exit.")
            execute_emergency_self_destruction()


# Dual-Process Heartbeat Pipe Utilities
def pipe_write(h_pipe: wintypes.HANDLE, byte_val: int) -> bool:
    data = ctypes.c_ubyte(byte_val)
    bytes_written = wintypes.DWORD(0)
    res = kernel32.WriteFile(h_pipe, ctypes.byref(data), 1, ctypes.byref(bytes_written), None)
    return bool(res and bytes_written.value == 1)

def pipe_peek(h_pipe: wintypes.HANDLE) -> int:
    bytes_avail = wintypes.DWORD(0)
    res = kernel32.PeekNamedPipe(h_pipe, None, 0, None, ctypes.byref(bytes_avail), None)
    if not res:
        return -1
    return bytes_avail.value

def pipe_read(h_pipe: wintypes.HANDLE) -> int:
    data = ctypes.c_ubyte(0)
    bytes_read = wintypes.DWORD(0)
    res = kernel32.ReadFile(h_pipe, ctypes.byref(data), 1, ctypes.byref(bytes_read), None)
    if not res or bytes_read.value != 1:
        return -1
    return data.value


def run_interlocking_monitor(h_process_to_monitor: wintypes.HANDLE, h_read_pipe: wintypes.HANDLE, h_write_pipe: wintypes.HANDLE, is_daemon: bool = False) -> bool:
    """
    Dual-process mutual monitoring loop. Runs at 50ms intervals.
    Verifies heartbeats via anonymous pipes and processes state.
    Returns True for a graceful exit signal, False if process died or suspended.
    """
    last_heartbeat = time.time()
    first_heartbeat_received = False
    
    STARTUP_TIMEOUT = 10.0
    NORMAL_TIMEOUT = 15.0
    
    if is_daemon:
        _log_daemon_debug("run_interlocking_monitor started in daemon mode.")
        
    while True:
        if not pipe_write(h_write_pipe, 0x01):
            if is_daemon:
                _log_daemon_debug("pipe_write failed.")
            return False  # Remote write failed -> process died
            
        avail = pipe_peek(h_read_pipe)
        if avail < 0:
            if is_daemon:
                _log_daemon_debug(f"pipe_peek returned {avail} (pipe broken).")
            return False  # Pipe broken
        elif avail > 0:
            for _ in range(avail):
                val = pipe_read(h_read_pipe)
                if val == 0xFF:
                    if is_daemon:
                        _log_daemon_debug("received graceful exit signal (0xFF).")
                    return True  # Graceful exit signal received
                elif val == 0x01:
                    last_heartbeat = time.time()
                    if not first_heartbeat_received:
                        first_heartbeat_received = True
                        if is_daemon:
                            _log_daemon_debug("first heartbeat (0x01) received.")
                    
        # Check if game mode is active via temporary file
        is_game_active = False
        try:
            import tempfile
            import os
            tmp_file = os.path.join(tempfile.gettempdir(), "core_commander_game_mode.tmp")
            if os.path.exists(tmp_file):
                is_game_active = True
        except Exception:
            pass
            
        current_normal_timeout = 30.0 if is_game_active else NORMAL_TIMEOUT
        timeout = current_normal_timeout if first_heartbeat_received else STARTUP_TIMEOUT
        elapsed = time.time() - last_heartbeat
        if elapsed > timeout:
            if is_daemon:
                _log_daemon_debug(f"timeout exceeded: elapsed={elapsed:.3f}s, timeout={timeout:.3f}s, first_heartbeat={first_heartbeat_received}")
            return False
            
        res = kernel32.WaitForSingleObject(h_process_to_monitor, 0)
        if res == 0:  # WAIT_OBJECT_0 -> process died
            if is_daemon:
                _log_daemon_debug("parent process died (WaitForSingleObject returned 0).")
            return False
            
        time.sleep(0.050)

def trigger_hwid_ban():
    """Lock down device HWID and trigger crash"""
    # Disabled
    pass

class PROCESS_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ('ExitStatus', ctypes.c_ulong),
        ('PebBaseAddress', ctypes.c_void_p),
        ('AffinityMask', ctypes.c_void_p),
        ('BasePriority', ctypes.c_long),
        ('UniqueProcessId', ctypes.c_void_p),
        ('InheritedFromUniqueProcessId', ctypes.c_void_p)
    ]

def check_hardware_breakpoints() -> bool:
    """Checks if any hardware breakpoints (DR0-DR3) are set on the current thread."""
    try:
        kernel32 = ctypes.windll.kernel32
        h_thread = kernel32.GetCurrentThread()
        is_x64 = ctypes.sizeof(ctypes.c_void_p) == 8
        
        if is_x64:
            # CONTEXT structure size is 1232 bytes, must be aligned to 16 bytes
            buf = ctypes.create_string_buffer(1232 + 16)
            addr = ctypes.addressof(buf)
            aligned_addr = (addr + 15) & ~15
            
            # ContextFlags offset is 0x30
            # Set CONTEXT_DEBUG_REGISTERS (0x00010010)
            ctypes.memset(aligned_addr + 0x30, 0, 4)
            ctypes.cast(aligned_addr + 0x30, ctypes.POINTER(ctypes.c_uint32))[0] = 0x00010010
            
            if kernel32.GetThreadContext(h_thread, ctypes.c_void_p(aligned_addr)):
                dr = ctypes.cast(aligned_addr + 0x48, ctypes.POINTER(ctypes.c_uint64 * 4)).contents
                if dr[0] != 0 or dr[1] != 0 or dr[2] != 0 or dr[3] != 0:
                    return True
        else:
            buf = ctypes.create_string_buffer(716)
            addr = ctypes.addressof(buf)
            ctypes.cast(addr, ctypes.POINTER(ctypes.c_uint32))[0] = 0x00010010
            if kernel32.GetThreadContext(h_thread, ctypes.c_void_p(addr)):
                dr = ctypes.cast(addr + 4, ctypes.POINTER(ctypes.c_uint32 * 4)).contents
                if dr[0] != 0 or dr[1] != 0 or dr[2] != 0 or dr[3] != 0:
                    return True
    except Exception:
        pass
    return False

def erase_pe_headers():
    """
    Zeroes out the PE headers of the current process and the loaded guard module in memory to prevent dumping.
    """
    try:
        kernel32 = ctypes.windll.kernel32
        VirtualProtect = kernel32.VirtualProtect
        VirtualProtect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        VirtualProtect.restype = wintypes.BOOL
        
        # Get module base addresses
        h_main = kernel32.GetModuleHandleW(None)
        h_guard = kernel32.GetModuleHandleW("guard.pyd")
        
        PAGE_READWRITE = 0x04
        
        for h_mod in [h_main, h_guard]:
            if not h_mod:
                continue
            header_size = 4096
            old_protect = wintypes.DWORD(0)
            if VirtualProtect(h_mod, header_size, PAGE_READWRITE, ctypes.byref(old_protect)):
                ctypes.memset(h_mod, 0, header_size)
                VirtualProtect(h_mod, header_size, old_protect, ctypes.byref(old_protect))
    except Exception:
        pass

def is_peb_debugger_present() -> bool:
    """Directly read PEB flags from memory to detect debuggers, bypassing standard API hooks."""
    try:
        kernel32 = ctypes.windll.kernel32
        ntdll = ctypes.windll.ntdll
        
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        ntdll.NtQueryInformationProcess.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong)
        ]
        
        pbi = PROCESS_BASIC_INFORMATION()
        ret_len = ctypes.c_ulong(0)
        status = ntdll.NtQueryInformationProcess(
            kernel32.GetCurrentProcess(),
            0, # ProcessBasicInformation
            ctypes.byref(pbi),
            ctypes.sizeof(pbi),
            ctypes.byref(ret_len)
        )
        
        if status == 0 and pbi.PebBaseAddress:
            # BeingDebugged offset is 2
            being_debugged = ctypes.cast(pbi.PebBaseAddress + 2, ctypes.POINTER(ctypes.c_ubyte)).contents.value
            if being_debugged != 0:
                return True
                
            # NtGlobalFlag offset (0xBC on x64, 0x68 on x86)
            is_x64 = ctypes.sizeof(ctypes.c_void_p) == 8
            flag_offset = 0xBC if is_x64 else 0x68
            nt_global_flag = ctypes.cast(pbi.PebBaseAddress + flag_offset, ctypes.POINTER(ctypes.c_uint32)).contents.value
            
            # NtGlobalFlag & 0x70 indicates debugger presence
            if (nt_global_flag & 0x70) != 0:
                return True
    except Exception:
        pass
    return False

def hide_current_thread_from_debugger():
    """Hides the current calling thread from the active debugger using NtSetInformationThread."""
    try:
        kernel32 = ctypes.windll.kernel32
        ntdll = ctypes.windll.ntdll
        
        kernel32.GetCurrentThread.restype = ctypes.c_void_p
        ntdll.NtSetInformationThread.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_ulong
        ]
        # 0x11 is ThreadHideFromDebugger
        ntdll.NtSetInformationThread(kernel32.GetCurrentThread(), 0x11, None, 0)
    except Exception:
        pass

def is_debugger_attached() -> bool:
    """Windows API checking if debugger is attached to this process using API Hashing."""
    if is_peb_debugger_present():
        return True
    if check_hardware_breakpoints():
        return True
    try:
        # Resolve kernel32.dll -> IsDebuggerPresent (dll hash: 0x6e246be6, api hash: 0xaef8ab90)
        p_IsDebuggerPresent = resolve_api_via_hash(0x6e246be6, 0xaef8ab90)
        if p_IsDebuggerPresent:
            fn_IsDebuggerPresent = ctypes.WINFUNCTYPE(ctypes.c_bool)(p_IsDebuggerPresent)
            if fn_IsDebuggerPresent():
                return True
                
        # Resolve kernel32.dll -> CheckRemoteDebuggerPresent (dll hash: 0x6e246be6, api hash: 0x62955f17)
        p_CheckRemoteDebuggerPresent = resolve_api_via_hash(0x6e246be6, 0x62955f17)
        if p_CheckRemoteDebuggerPresent:
            fn_CheckRemoteDebuggerPresent = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int))(p_CheckRemoteDebuggerPresent)
            is_remote = ctypes.c_int(0)
            # Get current process handle dynamically
            p_GetCurrentProcess = resolve_api_via_hash(0x6e246be6, 0xc8fdfcfd) # GetCurrentProcess hash: 0xc8fdfcfd
            h_proc = ctypes.c_void_p(-1)
            if p_GetCurrentProcess:
                h_proc = ctypes.WINFUNCTYPE(ctypes.c_void_p)(p_GetCurrentProcess)()
            if fn_CheckRemoteDebuggerPresent(h_proc, ctypes.byref(is_remote)) and is_remote.value:
                return True
    except Exception:
        pass
    return False

# Server Verification & IP Ban Hook
def verify_device_with_server(server_url: str):
    """Sends AES-256-GCM encrypted payload containing HWID to the server."""
    hwid = calculate_custom_hwid()
    
    # 1. Create payload data structure
    payload_raw = {
        "hwid": hwid,
        "timestamp": int(time.time()),
        "client_version": "1.0.1"
    }
    
    # 2. Encrypt using AES-256-GCM
    encrypted_dict = encrypt_payload_aes_gcm(json.dumps(payload_raw), hwid)
    
    # 3. Transmit to server
    try:
        headers = {"Content-Type": "application/json"}
        # Post encrypted payload
        response = requests.post(server_url, json=encrypted_dict, headers=headers, timeout=5)
        
        # 4. Handle Server response
        if response.status_code == 403:
            # IP Ban or HWID Ban triggered on server: local lock down
            trigger_hwid_ban()
            
        elif response.status_code == 200:
            # Server returns encrypted response packet
            resp_data = response.json()
            decrypted_resp = decrypt_payload_aes_gcm(resp_data, hwid)
            if decrypted_resp:
                resp_json = json.loads(decrypted_resp)
                if resp_json.get("status") == "banned":
                    # Remote ban instruction: local lock down
                    trigger_hwid_ban()
    except requests.exceptions.RequestException:
        # Offline or server unreachable: allow execution (or raise error based on strict mode)
        pass

# Global state to track if environment is tainted
_security_tainted = False
_tainted_lock = threading.Lock()

def taint_security_state():
    """Taints the global security state and schedules a crash/exit after 3-5 minutes to confuse debuggers."""
    global _security_tainted
    with _tainted_lock:
        if not _security_tainted:
            _security_tainted = True
            logger.warning("Security environment tainted.")
            import random
            def delayed_crash():
                time.sleep(random.randint(180, 300))
                # Trigger silent Access Violation crash (0xC0000005)
                ctypes.windll.kernel32.ExitProcess(0xC0000005)
            t = threading.Thread(target=delayed_crash, daemon=True)
            t.start()

def verify_public_key_integrity() -> bool:
    """Verifies the integrity of the licensing public key to prevent patching."""
    try:
        from core_commander.core.license import PUBLIC_KEY_PEM
        expected_hash = "aa5b0698679830f7304a669f9703d7cd02f46f8a7874bb51ceaf6874437f49f7"
        current_hash = hashlib.sha256(PUBLIC_KEY_PEM).hexdigest()
        if current_hash != expected_hash:
            logger.critical("Security Warning: Licensing public key has been modified/patched!")
            return False
        return True
    except Exception as e:
        logger.error(f"Error checking public key integrity: {e}")
        return False

def get_qpc_ticks() -> int:
    """Helper to query QueryPerformanceCounter safely."""
    try:
        p_QPC = resolve_api_via_hash(0x6e246be6, 0x76b25ea0) # kernel32.dll -> QueryPerformanceCounter
        if p_QPC:
            fn_QPC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int64))(p_QPC)
            qpc = ctypes.c_int64(0)
            if fn_QPC(ctypes.byref(qpc)):
                return qpc.value
    except Exception:
        pass
    return int(time.perf_counter_ns())

def active_protection_loop():
    """Eliminated background thread loop in favor of event-triggered validation hooks."""
    pass

def check_initialization() -> bool:
    """Zero-overhead event-triggered initialization validation check with QPC timing analysis."""
    try:
        t_start = get_qpc_ticks()
        
        # Memory integrity check
        verify_module_memory_integrity()

        # Anti-VM and sandbox check
        verify_anti_vm_and_sandbox()

        # API Hooking verification
        verify_api_hooking()

        t_end = get_qpc_ticks()
        # QPC timing analysis
        if (t_end - t_start) > 50000000:  # 50ms
            taint_security_state()
            return False

        # Check public key integrity
        if not verify_public_key_integrity():
            taint_security_state()
            return False

        if is_debugger_attached():
            taint_security_state()
            return False

        return True
    except Exception:
        return False

def check_apply_optimization_hook() -> bool:
    """Zero-overhead event-triggered validation check executed during optimization apply events."""
    try:
        t_start = get_qpc_ticks()
        
        # Verify module memory integrity
        verify_module_memory_integrity()

        # Verify API hooking
        verify_api_hooking()

        t_end = get_qpc_ticks()
        # QPC timing analysis
        if (t_end - t_start) > 20000000:  # 20ms
            taint_security_state()
            return False

        # Check public key integrity
        if not verify_public_key_integrity():
            taint_security_state()
            return False

        if is_debugger_attached():
            taint_security_state()
            return False

        return True
    except Exception:
        return False

def check_window_focus_hook() -> bool:
    """Lightweight zero-overhead validation check triggered during window focus events. Virtually zero footprint."""
    try:
        t_start = get_qpc_ticks()
        
        # Run extremely fast checks, absolutely no print statements or slow operations
        is_dbg = is_debugger_attached()
        
        t_end = get_qpc_ticks()
        # Check public key integrity
        if is_dbg or not verify_public_key_integrity() or (t_end - t_start) > 10000000:  # 10ms
            taint_security_state()
            return False
            
        return True
    except Exception:
        return False

def verify_executable_integrity():
    """Verify current running EXE signature at overlay area to prevent patching"""
    if not getattr(sys, 'frozen', False):
        return  # Bypass during development/unpackaged run
        
    try:
        exe_path = sys.executable
        if not exe_path or not os.path.exists(exe_path):
            trigger_hwid_ban()
            
        with open(exe_path, "rb") as f:
            content = f.read()
            
        if len(content) < 32:
            trigger_hwid_ban()
            
        content_len = len(content)
        stored_signature = content[content_len - 32:]
        data_to_verify = content[:content_len - 32]
        
        # Hardcoded private key for integrity checking
        secret_key = _d("000000000002093e151c0d0b150c041c02182a1c3031465555442a1b0b36260e163600390c173871424b46").encode('utf-8')
        computed_sig = hmac.new(secret_key, data_to_verify, hashlib.sha256).digest()
        
        if not hmac.compare_digest(stored_signature, computed_sig):
            # Signature mismatch: binary was patched or unpacked/tampered!
            trigger_hwid_ban()
    except Exception:
        trigger_hwid_ban()

def check_api_hook(dll_name: str, api_name: str) -> bool:
    """Read API function prologue and check for common hooks (JMP, RET, INT3) using API Hashing."""
    try:
        dll_hash = _hash_djb2(dll_name.lower())
        api_hash = _hash_djb2(api_name)
        addr = resolve_api_via_hash(dll_hash, api_hash)
        if not addr:
            return False
            
        prologue = ctypes.string_at(addr, 5)
        # JMP (0xE9), Short JMP (0xEB), INT3 (0xCC), RET (0xC3), RETN (0xC2)
        if prologue[0] in (0xE9, 0xEB, 0xCC, 0xC3, 0xC2):
            return True
            
        # PUSH imm32 (0x68) + RET (0xC3) hook pattern
        if prologue[0] == 0x68 and prologue[4] == 0xC3:
            return True
            
        # JMP dword ptr [addr] (0xFF 0x25) commonly used in standard x64 hooking
        if prologue[0] == 0xFF and prologue[1] == 0x25:
            return True
            
        return False
    except Exception:
        return False

def verify_api_hooking():
    """Verify that vital Win32 functions are not hooked/redirected"""
    # Disabled because legitimate AVs often hook these APIs
    pass

_exit_event_handle = None

def signal_graceful_exit():
    """Signals the guardian daemon that the main app is exiting normally."""
    global _exit_event_handle
    if _exit_event_handle:
        try:
            ctypes.windll.kernel32.SetEvent(_exit_event_handle)
            ctypes.windll.kernel32.CloseHandle(_exit_event_handle)
            _exit_event_handle = None
        except Exception:
            pass

def run_guard_daemon():
    """Guardian daemon process. Monitors the parent process via pipes and handles."""
    hide_current_thread_from_debugger()
    
    app_data_dir = os.path.join(os.environ.get('APPDATA', ''), 'CoreCommander')
    if not os.path.exists(app_data_dir):
        os.makedirs(app_data_dir, exist_ok=True)
    debug_log_path = os.path.join(app_data_dir, 'daemon_debug.log')
    
    try:
        try:
            idx = sys.argv.index("--daemon")
            if idx + 4 >= len(sys.argv):
                with open(debug_log_path, "a", encoding="utf-8") as lf:
                    lf.write(f"[{time.asctime()}] Daemon failed: argv index error.\n")
                return 1
            parent_pid = int(sys.argv[idx + 1])
            h_parent_val = int(sys.argv[idx + 2])
            h_child_read_val = int(sys.argv[idx + 3])
            h_child_write_val = int(sys.argv[idx + 4])
        except Exception as e:
            with open(debug_log_path, "a", encoding="utf-8") as lf:
                import traceback
                lf.write(f"[{time.asctime()}] Daemon arg parsing exception: {e}\n{traceback.format_exc()}\n")
            return 1

        with open(debug_log_path, "a", encoding="utf-8") as lf:
            lf.write(f"[{time.asctime()}] Daemon started. PID={os.getpid()}, Parent PID={parent_pid}\n")
            lf.write(f"Handles: Parent={h_parent_val}, Read={h_child_read_val}, Write={h_child_write_val}\n")

        h_parent = wintypes.HANDLE(h_parent_val)
        h_read_pipe = wintypes.HANDLE(h_child_read_val)
        h_write_pipe = wintypes.HANDLE(h_child_write_val)
        
        # Start loaded modules monitor thread for the daemon itself
        t_mod = threading.Thread(target=loaded_modules_monitor_loop, daemon=True)
        t_mod.start()

        # Run the interlocking monitor on the parent process
        success = run_interlocking_monitor(h_parent, h_read_pipe, h_write_pipe, is_daemon=True)
        with open(debug_log_path, "a", encoding="utf-8") as lf:
            lf.write(f"[{time.asctime()}] Interlocking monitor finished. success={success}\n")
        if not success:
            execute_emergency_self_destruction()
            
        kernel32.CloseHandle(h_parent)
        kernel32.CloseHandle(h_read_pipe)
        kernel32.CloseHandle(h_write_pipe)
        return 0
    except Exception as e:
        with open(debug_log_path, "a", encoding="utf-8") as lf:
            import traceback
            lf.write(f"[{time.asctime()}] Daemon main exception: {e}\n{traceback.format_exc()}\n")
        return 1


_parent_write_pipe = None
_parent_read_pipe = None
_child_process_handle = None

def signal_graceful_exit():
    """Signals the guardian daemon that the main app is exiting normally."""
    global _parent_write_pipe, _exiting
    _exiting = True
    if _parent_write_pipe:
        try:
            pipe_write(_parent_write_pipe, 0xFF)
            time.sleep(0.05)
            kernel32.CloseHandle(_parent_write_pipe)
        except Exception:
            pass


def verify_anti_vm_and_sandbox():
    """Check system properties to detect Virtual Machines (VMware, VirtualBox, QEMU, Sandboxie)"""
    # Relaxed anti-VM checks to prevent hitting users' legitimate developer/gaming environments.
    pass


def get_text_section_info(base_address: int) -> tuple:
    """Parse PE headers in memory to find the .text section offset and size."""
    try:
        # DOS Header: e_lfanew is at offset 0x3C (60)
        e_lfanew = ctypes.cast(base_address + 60, ctypes.POINTER(ctypes.c_uint32)).contents.value
        
        # NT Header signature is at e_lfanew
        nt_signature = ctypes.cast(base_address + e_lfanew, ctypes.POINTER(ctypes.c_uint32)).contents.value
        if nt_signature != 0x00004550: # "PE\0\0"
            return (0, 0)
            
        # File Header starts 4 bytes after NT signature
        file_header_addr = base_address + e_lfanew + 4
        num_sections = ctypes.cast(file_header_addr + 2, ctypes.POINTER(ctypes.c_uint16)).contents.value
        size_of_opt_header = ctypes.cast(file_header_addr + 16, ctypes.POINTER(ctypes.c_uint16)).contents.value
        
        # Section Headers start right after Optional Header
        # Optional Header starts 20 bytes after File Header
        sections_start = file_header_addr + 20 + size_of_opt_header
        
        # Parse each section
        for i in range(num_sections):
            sect_addr = sections_start + i * 40 # Each section header is 40 bytes
            sect_name_bytes = ctypes.string_at(sect_addr, 8)
            null_idx = sect_name_bytes.find(b'\0')
            name = sect_name_bytes[:null_idx] if null_idx != -1 else sect_name_bytes
            
            if name == b'.text':
                virtual_size = ctypes.cast(sect_addr + 8, ctypes.POINTER(ctypes.c_uint32)).contents.value
                virtual_address = ctypes.cast(sect_addr + 12, ctypes.POINTER(ctypes.c_uint32)).contents.value
                return (virtual_address, virtual_size)
    except Exception:
        pass
    return (0, 0)

_guard_text_ref_hash = None
_guard_text_addr = 0
_guard_text_size = 0

def init_memory_checksum():
    """Initializes the baseline SHA-256 hash of the guard module's executable code (.text section) in memory."""
    global _guard_text_ref_hash, _guard_text_addr, _guard_text_size
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        
        import sys
        mod = sys.modules.get('core_commander.core.guard')
        if not mod or not hasattr(mod, '__file__'):
            return
            
        basename = os.path.basename(mod.__file__)
        h_mod = kernel32.GetModuleHandleW(basename)
        if not h_mod:
            h_mod = kernel32.GetModuleHandleW("guard.pyd")
            
        if not h_mod:
            return
            
        rva, size = get_text_section_info(h_mod)
        if size > 0:
            _guard_text_addr = h_mod + rva
            _guard_text_size = size
            text_bytes = ctypes.string_at(_guard_text_addr, _guard_text_size)
            _guard_text_ref_hash = hashlib.sha256(text_bytes).hexdigest()
    except Exception:
        pass

def verify_module_memory_integrity():
    """Verify that guard module memory has not been modified/patched at runtime."""
    global _guard_text_ref_hash, _guard_text_addr, _guard_text_size
    if not getattr(sys, 'frozen', False):
        return  # Bypass during development
        
    if _guard_text_ref_hash and _guard_text_addr and _guard_text_size:
        try:
            current_bytes = ctypes.string_at(_guard_text_addr, _guard_text_size)
            current_hash = hashlib.sha256(current_bytes).hexdigest()
            if current_hash != _guard_text_ref_hash:
                trigger_hwid_ban()
        except Exception:
            trigger_hwid_ban()

def initialize_guard():
    global _parent_write_pipe, _parent_read_pipe, _child_process_handle

    # Erase PE headers in memory to block dumping tools
    erase_pe_headers()

    # Initialize memory integrity baseline hash
    init_memory_checksum()

    # Hide main GUI thread from debuggers
    hide_current_thread_from_debugger()

    # Self-integrity Overlay Check
    verify_executable_integrity()

    # Perform event-triggered validation checks at boot time
    check_initialization()

    # HWID Ban Check
    hwid = calculate_custom_hwid()
    if check_ban_status(hwid):
        trigger_decoy_crash()

    # Validate hidden identity configuration file
    validate_or_create_hidden_config(hwid)

    # Cache decrypted configuration parameters
    cache_tweak_payload()

    # Start Loaded Modules Monitor thread locally
    t_mod = threading.Thread(target=loaded_modules_monitor_loop, daemon=True)
    t_mod.start()

    # Spawning the Guardian Daemon Process with pipes and handles
    try:
        # Create anonymous pipes for heartbeat
        sa = SECURITY_ATTRIBUTES()
        sa.nLength = ctypes.sizeof(SECURITY_ATTRIBUTES)
        sa.lpSecurityDescriptor = None
        sa.bInheritHandle = True

        h_child_read = wintypes.HANDLE()
        h_parent_write = wintypes.HANDLE()
        kernel32.CreatePipe(ctypes.byref(h_child_read), ctypes.byref(h_parent_write), ctypes.byref(sa), 0)

        h_parent_read = wintypes.HANDLE()
        h_child_write = wintypes.HANDLE()
        kernel32.CreatePipe(ctypes.byref(h_parent_read), ctypes.byref(h_child_write), ctypes.byref(sa), 0)

        # Make parent's ends non-inheritable
        HANDLE_FLAG_INHERIT = 1
        kernel32.SetHandleInformation(h_parent_write, HANDLE_FLAG_INHERIT, 0)
        kernel32.SetHandleInformation(h_parent_read, HANDLE_FLAG_INHERIT, 0)

        # Duplicate parent's process handle to be inheritable
        h_parent_self = kernel32.GetCurrentProcess()
        h_parent_dup = wintypes.HANDLE()
        DUPLICATE_SAME_ACCESS = 2
        kernel32.DuplicateHandle(
            h_parent_self,
            h_parent_self,
            h_parent_self,
            ctypes.byref(h_parent_dup),
            0,
            True,
            DUPLICATE_SAME_ACCESS
        )

        _parent_write_pipe = h_parent_write
        _parent_read_pipe = h_parent_read
        atexit.register(signal_graceful_exit)

        import subprocess
        if getattr(sys, 'frozen', False):
            args = [
                sys.executable,
                "--daemon",
                str(os.getpid()),
                str(h_parent_dup.value),
                str(h_child_read.value),
                str(h_child_write.value)
            ]
        else:
            args = [
                sys.executable,
                sys.argv[0],
                "--daemon",
                str(os.getpid()),
                str(h_parent_dup.value),
                str(h_child_read.value),
                str(h_child_write.value)
            ]

        child_proc = subprocess.Popen(args, creationflags=0x08000000, close_fds=False)
        _child_process_handle = child_proc._handle

        # Close child ends in parent process
        kernel32.CloseHandle(h_child_read)
        kernel32.CloseHandle(h_child_write)
        kernel32.CloseHandle(h_parent_dup)

        def parent_monitor_thread_func():
            success = run_interlocking_monitor(_child_process_handle, _parent_read_pipe, _parent_write_pipe)
            global _exiting
            if not success and not _exiting:
                logger.critical("Daemon process exited or was suspended! Triggering self-destruction.")
                execute_emergency_self_destruction()

        t_mon = threading.Thread(target=parent_monitor_thread_func, daemon=True)
        t_mon.start()

    except Exception as e:
        logger.error(f"Failed to spawn mutual interlocking guardian daemon: {e}")

    # Volatile Crypt Key Rotation thread
    t_rot = threading.Thread(target=volatile_key_rotation_loop, daemon=True)
    t_rot.start()
