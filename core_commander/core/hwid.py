# -*- coding: utf-8 -*-
import ctypes
from ctypes import wintypes
import hashlib
import logging
import winreg
import struct

logger = logging.getLogger(__name__)

# ctypes setup for physical drive queries
kernel32 = ctypes.windll.kernel32

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

kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
kernel32.CreateFileW.restype = ctypes.c_void_p
kernel32.DeviceIoControl.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
kernel32.DeviceIoControl.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
kernel32.CloseHandle.restype = wintypes.BOOL

def get_smbios_serial() -> str:
    """Retrieve motherboard SMBIOS serial natively using GetSystemFirmwareTable."""
    try:
        GetSystemFirmwareTable = kernel32.GetSystemFirmwareTable
        GetSystemFirmwareTable.argtypes = [wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        GetSystemFirmwareTable.restype = wintypes.DWORD
        
        sig = 0x52534D42  # 'RSMB' in ASCII
        size = GetSystemFirmwareTable(sig, 0, None, 0)
        if size == 0:
            return _get_smbios_fallback()
            
        buf = ctypes.create_string_buffer(size)
        written = GetSystemFirmwareTable(sig, 0, buf, size)
        if written == 0 or written > size:
            return _get_smbios_fallback()
            
        data = buf.raw
        if len(data) < 8:
            return _get_smbios_fallback()
            
        tbl_len = struct.unpack_from('<I', data, 4)[0]
        tbl_data = data[8:8+tbl_len]
        
        system_serial = "UNKNOWN"
        board_serial = "UNKNOWN"
        
        offset = 0
        while offset < len(tbl_data) - 4:
            st_type = tbl_data[offset]
            st_len = tbl_data[offset+1]
            if st_len < 4 or offset + st_len > len(tbl_data):
                break
                
            str_start = offset + st_len
            str_end = str_start
            while str_end < len(tbl_data) - 1:
                if tbl_data[str_end] == 0 and tbl_data[str_end+1] == 0:
                    str_end += 1
                    break
                str_end += 1
                
            raw_strs = tbl_data[str_start:str_end+1]
            strings = raw_strs.split(b'\0')
            if strings and strings[-1] == b'':
                strings.pop()
                
            if st_type in (1, 2):
                if st_len >= 8:
                    sn_idx = tbl_data[offset + 7]
                    if 0 < sn_idx <= len(strings):
                        serial = strings[sn_idx - 1].decode('utf-8', errors='ignore').strip()
                        if serial:
                            if st_type == 1:
                                system_serial = serial
                            else:
                                board_serial = serial
            offset = str_end + 1
            
        if board_serial and board_serial != "UNKNOWN":
            return board_serial
        if system_serial and system_serial != "UNKNOWN":
            return system_serial
    except Exception as e:
        logger.debug(f"Failed to get SMBIOS serial natively: {e}")
    return _get_smbios_fallback()

def _get_smbios_fallback() -> str:
    try:
        path = r"HARDWARE\Description\System\BIOS"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, "SystemSerialNumber")
            if val:
                return str(val).strip()
    except Exception:
        pass
    try:
        path = r"HARDWARE\Description\System\BIOS"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, "BaseBoardSerialNumber")
            if val:
                return str(val).strip()
    except Exception:
        pass
    return "UNKNOWN"

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
    except Exception as e:
        logger.debug(f"Failed to query disk serial via DeviceIoControl: {e}")
    finally:
        if hDevice is not None:
            kernel32.CloseHandle(hDevice)

    # Fallback A: SCSI Registry Identifier
    try:
        path = r"SYSTEM\CurrentControlSet\Services\disk\Enum"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, "0")
            if val:
                return str(val).strip()
    except Exception:
        pass
    return "UNKNOWN_DISK"

def get_system_uuid() -> str:
    """Read mainboard SystemUUID from registry"""
    try:
        path = r"SYSTEM\CurrentControlSet\Control\SystemInformation"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, "SystemUUID")
            if val:
                return str(val).strip()
    except Exception:
        pass
    try:
        path = r"HARDWARE\Description\System\BIOS"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, "BIOSVersion")
            if val:
                return str(val).strip()
    except Exception:
        pass
    return "UNKNOWN_UUID"

def get_cpu_features() -> str:
    """Get CPU unique hardware features from central processor description"""
    try:
        path = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ) as key:
            name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            identifier, _ = winreg.QueryValueEx(key, "Identifier")
            vendor, _ = winreg.QueryValueEx(key, "VendorIdentifier")
            return f"{name}_{identifier}_{vendor}".strip()
    except Exception:
        pass
    return "UNKNOWN_CPU"

def get_hwid() -> str:
    """
    Generate a highly unique and irreversible Hardware ID based on the motherboard, CPU, and primary disk drive.
    Uses native Win32 calls instead of wmic subprocess to prevent tampering and emulation.
    """
    board_sn = get_smbios_serial()
    cpu_id = get_cpu_features()
    disk_sn = get_physical_disk_serial()

    raw_id = f"BOARD:{board_sn}|CPU:{cpu_id}|DISK:{disk_sn}"
    salt = "CoreCommander_Security_V1_"
    hwid = hashlib.sha256((salt + raw_id).encode('utf-8')).hexdigest()
    short_hash = hwid[:16].upper()
    formatted_hwid = f"CC-{short_hash[:4]}-{short_hash[4:8]}-{short_hash[8:12]}-{short_hash[12:16]}"
    
    return formatted_hwid

def get_hwid_components() -> dict:
    """
    Returns the individual hashes of the hardware components for strict verification.
    """
    board_sn = get_smbios_serial()
    disk_sn = get_physical_disk_serial()
    uuid_val = get_system_uuid()
    cpu_id = get_cpu_features()
    
    return {
        "bios_hash": hashlib.sha256(board_sn.encode('utf-8')).hexdigest() if board_sn and board_sn != "UNKNOWN" else None,
        "disk_hash": hashlib.sha256(disk_sn.encode('utf-8')).hexdigest() if disk_sn and disk_sn != "UNKNOWN_DISK" else None,
        "uuid_hash": hashlib.sha256(uuid_val.encode('utf-8')).hexdigest() if uuid_val and uuid_val != "UNKNOWN_UUID" else None,
        "cpu_hash": hashlib.sha256(cpu_id.encode('utf-8')).hexdigest() if cpu_id and cpu_id != "UNKNOWN_CPU" else None
    }

if __name__ == "__main__":
    print(f"Your HWID is: {get_hwid()}")
