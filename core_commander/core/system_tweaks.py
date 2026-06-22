# -*- coding: utf-8 -*-
import os
import sys
import json
import threading
import winreg
import time
import shutil
import tempfile
import subprocess  # nosec
import re
from PySide6.QtCore import QThread, Signal
import psutil
import ctypes
from core_commander.utils.logger import logger
from core_commander.utils.i18n import Trans
from core_commander.utils.device import get_pci_device_ids
from core_commander.core.gpu_drs import NvidiaDrsService
from core_commander.core.gpu_smi import GpuSmiService
from core_commander.core.irq_aff import IrqAffinityService
from core_commander.core.latency_monitor import LatencyMonitorService

# Import win32 libs for direct service query/config
try:
    import win32service
    import win32con
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

# Fallback service constants compatible with win32service
SERVICE_BOOT_START = 0
SERVICE_SYSTEM_START = 1
SERVICE_AUTO_START = 2
SERVICE_DEMAND_START = 3
SERVICE_DISABLED = 4

# Windows Power Management API (Ctypes Powrprof.dll) definitions
class POWER_GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8)
    ]

    def to_py_uuid(self):
        import uuid
        node = int.from_bytes(self.Data4[2:8], byteorder='big')
        return uuid.UUID(fields=(self.Data1, self.Data2, self.Data3, self.Data4[0], self.Data4[1], node))

    @staticmethod
    def from_str(guid_str):
        import uuid
        u = uuid.UUID(guid_str)
        data4 = (ctypes.c_ubyte * 8)(*u.bytes[8:16])
        return POWER_GUID(u.time_low, u.time_mid, u.time_hi_version, data4)

try:
    from ctypes import wintypes
    powrprof = ctypes.WinDLL('powrprof.dll')
    
    powrprof.PowerGetActiveScheme.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.POINTER(POWER_GUID))]
    powrprof.PowerGetActiveScheme.restype = wintypes.DWORD
    
    powrprof.PowerSetActiveScheme.argtypes = [ctypes.c_void_p, ctypes.POINTER(POWER_GUID)]
    powrprof.PowerSetActiveScheme.restype = wintypes.DWORD
    
    powrprof.PowerWriteACValueIndex.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(POWER_GUID), ctypes.POINTER(POWER_GUID), ctypes.POINTER(POWER_GUID), wintypes.DWORD
    ]
    powrprof.PowerWriteACValueIndex.restype = wintypes.DWORD
    
    powrprof.PowerWriteDCValueIndex.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(POWER_GUID), ctypes.POINTER(POWER_GUID), ctypes.POINTER(POWER_GUID), wintypes.DWORD
    ]
    powrprof.PowerWriteDCValueIndex.restype = wintypes.DWORD
    
    powrprof.PowerEnumerate.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_ulong, ctypes.POINTER(POWER_GUID), ctypes.POINTER(wintypes.DWORD)
    ]
    powrprof.PowerEnumerate.restype = wintypes.DWORD
    
    powrprof.PowerReadFriendlyName.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(POWER_GUID), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.POINTER(wintypes.DWORD)
    ]
    powrprof.PowerReadFriendlyName.restype = wintypes.DWORD
    
    powrprof.PowerImportPowerScheme.argtypes = [
        ctypes.c_void_p,            # RootPowerKey (passed as None/0)
        ctypes.c_wchar_p,           # ImportFileNamePath
        ctypes.POINTER(ctypes.POINTER(POWER_GUID)) # DestinationSchemeGuid
    ]
    powrprof.PowerImportPowerScheme.restype = wintypes.DWORD

    powrprof.PowerDeleteScheme.argtypes = [
        ctypes.c_void_p,            # RootPowerKey (passed as None/0)
        ctypes.POINTER(POWER_GUID)  # SchemeGuid
    ]
    powrprof.PowerDeleteScheme.restype = wintypes.DWORD
    
    kernel32 = ctypes.WinDLL('kernel32.dll')
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    
    HAS_POWER_API = True
except Exception:
    HAS_POWER_API = False

try:
    winmm = ctypes.WinDLL('winmm.dll')
    ntdll = ctypes.WinDLL('ntdll.dll')
    
    winmm.timeBeginPeriod.argtypes = [ctypes.c_uint]
    winmm.timeBeginPeriod.restype = ctypes.c_uint
    winmm.timeEndPeriod.argtypes = [ctypes.c_uint]
    winmm.timeEndPeriod.restype = ctypes.c_uint
    
    ntdll.NtSetTimerResolution.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.POINTER(ctypes.c_ulong)]
    ntdll.NtSetTimerResolution.restype = ctypes.c_long
except Exception:
    winmm = None
    ntdll = None

class SystemTweaksService:
    enable_backup = True
    active_backup_filename = None
    _backup_cache = None
    _backup_cache_path = None
    _backup_lock = threading.RLock()
    _backup_dirty = False
    _gpu_vendor_cache = None
    _drive_type_cache = None

    @staticmethod
    def apply_ifeo_priority(game_exe_name: str, enable: bool) -> bool:
        """
        Registers or unregisters game executable under Image File Execution Options (IFEO)
        PerfOptions registry key to force CPU priority to High (3) at Windows kernel launch.
        """
        if not game_exe_name:
            return False
        # Normalize exe name
        exe_name = game_exe_name.strip()
        if not exe_name.lower().endswith('.exe'):
            exe_name += ".exe"
            
        subkey_parent = f"SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Image File Execution Options\\{exe_name}"
        subkey_perf = f"{subkey_parent}\\PerfOptions"
        value_name = "CpuPriorityClass"
        
        try:
            if enable:
                # 1. Backup if key exists
                SystemTweaksService.backup_registry_value("HKLM", subkey_perf, value_name)
                
                # 2. Write Registry Value
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, subkey_perf, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, value_name, 0, winreg.REG_DWORD, 3) # 3 represents High Priority
                logger.info(f"IFEO Priority hijacking applied successfully: {exe_name} -> High Priority.")
                return True
            else:
                # Restore original or clean up
                SystemTweaksService.restore_registry_value_or_default("HKLM", subkey_perf, value_name, None, winreg.REG_DWORD)
                
                # Clean up empty parent keys if no other values exist to keep registry clean
                try:
                    # Check if PerfOptions has other values, if not, delete PerfOptions subkey
                    try:
                        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey_perf, 0, winreg.KEY_READ) as key:
                            _, num_values, _ = winreg.QueryInfoKey(key)
                        if num_values == 0:
                            winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, subkey_perf)
                    except OSError:
                        pass
                        
                    # Check if IFEO parent key has other values/subkeys, if not, delete it
                    try:
                        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey_parent, 0, winreg.KEY_READ) as key:
                            num_subkeys, num_values, _ = winreg.QueryInfoKey(key)
                        if num_subkeys == 0 and num_values == 0:
                            winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, subkey_parent)
                    except OSError:
                        pass
                except Exception as clean_err:
                    logger.debug(f"Failed to clean up empty IFEO keys: {str(clean_err)}")
                    
                logger.info(f"IFEO Priority hijacking reverted/cleaned for: {exe_name}.")
                return True
        except Exception as e:
            logger.error(f"Failed to apply/revert IFEO priority for {exe_name}: {str(e)}")
            return False

    @staticmethod
    def flush_backup_data():
        """
        Force flush the in-memory backup cache to disk if marked dirty.
        """
        with SystemTweaksService._backup_lock:
            if SystemTweaksService._backup_dirty and SystemTweaksService._backup_cache is not None and SystemTweaksService._backup_cache_path:
                try:
                    backup_path = SystemTweaksService._backup_cache_path
                    with open(backup_path, 'w', encoding='utf-8') as f:
                        json.dump(SystemTweaksService._backup_cache, f, indent=4)
                    SystemTweaksService._backup_dirty = False
                    logger.info(f"Successfully flushed backup data to {backup_path}")
                except Exception as e:
                    logger.error(f"Failed flushing registry backup file: {str(e)}")

    @staticmethod
    def _load_backup_data(backup_path: str) -> dict:
        with SystemTweaksService._backup_lock:
            if SystemTweaksService._backup_cache_path == backup_path and SystemTweaksService._backup_cache is not None:
                return SystemTweaksService._backup_cache
            SystemTweaksService._backup_cache_path = backup_path
            if os.path.exists(backup_path):
                try:
                    with open(backup_path, 'r', encoding='utf-8') as f:
                        SystemTweaksService._backup_cache = json.load(f)
                except Exception:
                    SystemTweaksService._backup_cache = {}
            else:
                SystemTweaksService._backup_cache = {}
            return SystemTweaksService._backup_cache

    @staticmethod
    def _write_backup_data(backup_path: str, data: dict, flush: bool = True):
        with SystemTweaksService._backup_lock:
            SystemTweaksService._backup_cache = data
            SystemTweaksService._backup_cache_path = backup_path
            SystemTweaksService._backup_dirty = True
            if flush:
                try:
                    with open(backup_path, 'w', encoding='utf-8') as f:
                        json.dump(data, f, indent=4)
                    SystemTweaksService._backup_dirty = False
                except Exception as e:
                    logger.error(f"Failed writing registry backup file: {str(e)}")

    @staticmethod
    def _resolve_absolute_cmd_path(cmd):
        """
        Resolves the absolute path for standard Windows tools in the command list to prevent PATH hijacking.
        Supports both string and list inputs.
        """
        if not cmd:
            return cmd
        
        # Build mapping for standard system utilities
        windir = os.environ.get("SystemRoot", "C:\\Windows")
        system32 = os.path.join(windir, "System32")
        wbem = os.path.join(system32, "wbem")
        
        mapping = {
            "powershell": os.path.join(system32, "WindowsPowerShell\\v1.0\\powershell.exe"),
            "powershell.exe": os.path.join(system32, "WindowsPowerShell\\v1.0\\powershell.exe"),
            "netsh": os.path.join(system32, "netsh.exe"),
            "netsh.exe": os.path.join(system32, "netsh.exe"),
            "wmic": os.path.join(wbem, "wmic.exe"),
            "wmic.exe": os.path.join(wbem, "wmic.exe"),
            "pnputil": os.path.join(system32, "pnputil.exe"),
            "pnputil.exe": os.path.join(system32, "pnputil.exe"),
            "sc": os.path.join(system32, "sc.exe"),
            "sc.exe": os.path.join(system32, "sc.exe"),
            "powercfg": os.path.join(system32, "powercfg.exe"),
            "powercfg.exe": os.path.join(system32, "powercfg.exe"),
            "takeown": os.path.join(system32, "takeown.exe"),
            "takeown.exe": os.path.join(system32, "takeown.exe"),
            "icacls": os.path.join(system32, "icacls.exe"),
            "icacls.exe": os.path.join(system32, "icacls.exe"),
            "net": os.path.join(system32, "net.exe"),
            "net.exe": os.path.join(system32, "net.exe"),
            "bcdedit": os.path.join(system32, "bcdedit.exe"),
            "bcdedit.exe": os.path.join(system32, "bcdedit.exe"),
            "ipconfig": os.path.join(system32, "ipconfig.exe"),
            "ipconfig.exe": os.path.join(system32, "ipconfig.exe"),
            "schtasks": os.path.join(system32, "schtasks.exe"),
            "schtasks.exe": os.path.join(system32, "schtasks.exe"),
            "reg": os.path.join(system32, "reg.exe"),
            "reg.exe": os.path.join(system32, "reg.exe")
        }

        is_str = isinstance(cmd, str)
        if is_str:
            parts = cmd.split(maxsplit=1)
            if parts:
                exe = parts[0]
                rest = parts[1] if len(parts) > 1 else ""
                exe_clean = exe.strip('"').strip("'").lower()
                if exe_clean in mapping:
                    resolved = mapping[exe_clean]
                    resolved_quoted = f'"{resolved}"'
                    if rest:
                        cmd = f"{resolved_quoted} {rest}"
                    else:
                        cmd = resolved_quoted
        else:
            cmd_list = list(cmd)
            if cmd_list:
                exe = cmd_list[0]
                exe_clean = exe.strip('"').strip("'").lower()
                if exe_clean in mapping:
                    cmd_list[0] = mapping[exe_clean]
            cmd = cmd_list

        return cmd

    @staticmethod
    def safe_subprocess_call(cmd, timeout=10, **kwargs):
        try:
            cmd = SystemTweaksService._resolve_absolute_cmd_path(cmd)
            if 'creationflags' not in kwargs:
                kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
            return subprocess.call(cmd, timeout=timeout, **kwargs)  # nosec
        except subprocess.TimeoutExpired:
            logger.warning(f"Subprocess call timed out after {timeout}s: {cmd}")
            return -1
        except Exception as e:
            logger.error(f"Subprocess call failed for {cmd}: {str(e)}")
            return -1

    @staticmethod
    def safe_subprocess_check_output(cmd, timeout=10, **kwargs):
        try:
            cmd = SystemTweaksService._resolve_absolute_cmd_path(cmd)
            if 'creationflags' not in kwargs:
                kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
            return subprocess.check_output(cmd, timeout=timeout, **kwargs)  # nosec
        except subprocess.TimeoutExpired:
            logger.warning(f"Subprocess check_output timed out after {timeout}s: {cmd}")
            return b""
        except Exception as e:
            logger.error(f"Subprocess check_output failed for {cmd}: {str(e)}")
            return b""

    @staticmethod
    def decode_output(output_bytes: bytes) -> str:
        """Safely decodes subprocess output bytes using UTF-8 or GBK fallback."""
        if not output_bytes:
            return ""
        try:
            return output_bytes.decode("utf-8").strip()
        except UnicodeDecodeError:
            try:
                return output_bytes.decode("gbk", errors="ignore").strip()
            except Exception:
                return output_bytes.decode("ansi", errors="ignore").strip()

    @staticmethod
    def set_power_setting_value(scheme_guid_str: str, subgroup_guid_str: str, setting_guid_str: str, value: int, activate: bool = False) -> bool:
        """
        Sets AC and DC power setting index values for a power scheme using ctypes with powercfg fallback.
        Only activates/switches the scheme if activate is True.
        """
        # Try Windows Power API using ctypes
        if HAS_POWER_API:
            try:
                # If SCHEME_CURRENT, get the active scheme GUID
                if scheme_guid_str == "SCHEME_CURRENT":
                    p_guid = ctypes.POINTER(POWER_GUID)()
                    ret = powrprof.PowerGetActiveScheme(None, ctypes.byref(p_guid))
                    if ret == 0 and p_guid:
                        try:
                            scheme_guid = POWER_GUID(
                                p_guid.contents.Data1,
                                p_guid.contents.Data2,
                                p_guid.contents.Data3,
                                (ctypes.c_ubyte * 8)(*p_guid.contents.Data4)
                            )
                        finally:
                            kernel32.LocalFree(p_guid)
                    else:
                        raise Exception(f"PowerGetActiveScheme failed with code {ret}")
                else:
                    scheme_guid = POWER_GUID.from_str(scheme_guid_str)
                    
                subgroup_guid = POWER_GUID.from_str(subgroup_guid_str)
                setting_guid = POWER_GUID.from_str(setting_guid_str)
                
                ret_ac = powrprof.PowerWriteACValueIndex(None, ctypes.byref(scheme_guid), ctypes.byref(subgroup_guid), ctypes.byref(setting_guid), value)
                ret_dc = powrprof.PowerWriteDCValueIndex(None, ctypes.byref(scheme_guid), ctypes.byref(subgroup_guid), ctypes.byref(setting_guid), value)
                if activate:
                    powrprof.PowerSetActiveScheme(None, ctypes.byref(scheme_guid))
                
                if ret_ac == 0 and ret_dc == 0:
                    logger.info(f"Successfully configured power setting {setting_guid_str} to {value} via ctypes (activate={activate}).")
                    return True
            except Exception as e:
                logger.warning(f"ctypes Powrprof write failed, falling back to powercfg: {e}")
                
        # Fallback to powercfg
        try:
            ac_cmd = ["powercfg", "/setacvalueindex", scheme_guid_str, subgroup_guid_str, setting_guid_str, str(value)]
            dc_cmd = ["powercfg", "/setdcvalueindex", scheme_guid_str, subgroup_guid_str, setting_guid_str, str(value)]
            SystemTweaksService.safe_subprocess_call(ac_cmd, timeout=5)
            SystemTweaksService.safe_subprocess_call(dc_cmd, timeout=5)
            if activate:
                act_cmd = ["powercfg", "/setactive", scheme_guid_str]
                SystemTweaksService.safe_subprocess_call(act_cmd, timeout=5)
            logger.info(f"Configured power setting {setting_guid_str} to {value} via powercfg fallback (activate={activate}).")
            return True
        except Exception as e:
            logger.error(f"Failed to configure power setting {setting_guid_str}: {str(e)}")
            return False

    @staticmethod
    def get_backup_filepath() -> str:
        appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
        folder = os.path.join(appdata, "CoreCommander")
        if not os.path.exists(folder):
            os.makedirs(folder)
            
        # Create backups directory under CoreCommander if it doesn't exist
        backups_folder = os.path.join(folder, "backups")
        if not os.path.exists(backups_folder):
            os.makedirs(backups_folder)
            
        if SystemTweaksService.active_backup_filename:
            return os.path.join(backups_folder, SystemTweaksService.active_backup_filename)
            
        # Fallback to legacy path for backward compatibility
        legacy_file = os.path.join(folder, "registry_backup.json")
        if os.path.exists(legacy_file):
            return legacy_file
            
        return os.path.join(folder, "registry_backup.json")

    @staticmethod
    def get_all_backups() -> list:
        appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
        folder = os.path.join(appdata, "CoreCommander")
        backups_folder = os.path.join(folder, "backups")
        
        backups = []
        # Check legacy backup
        legacy_file = os.path.join(folder, "registry_backup.json")
        if os.path.exists(legacy_file):
            backups.append({
                "filename": "registry_backup.json",
                "display_name": "系统初始备份 (Legacy)" if Trans.CURRENT_LANG == "zh_CN" else "Initial System Backup (Legacy)",
                "path": legacy_file,
                "time": os.path.getmtime(legacy_file)
            })
            
        if os.path.exists(backups_folder):
            for file in os.listdir(backups_folder):
                if file.startswith("backup_") and file.endswith(".json"):
                    full_path = os.path.join(backups_folder, file)
                    # Parse timestamp from name backup_YYYY-MM-DD_HH-MM-SS.json
                    parts = file.replace("backup_", "").replace(".json", "").split("_")
                    if len(parts) == 2:
                        date_str = parts[0]
                        time_str = parts[1].replace("-", ":")
                        display_name = f"{date_str} {time_str} (自动备份节点)" if Trans.CURRENT_LANG == "zh_CN" else f"{date_str} {time_str} (Auto Backup Node)"
                    else:
                        clean_name = file.replace("backup_", "").replace(".json", "").replace("_", " ").replace("-", ":")
                        display_name = f"{clean_name} (自动备份节点)" if Trans.CURRENT_LANG == "zh_CN" else f"{clean_name} (Auto Backup)"
                        
                    backups.append({
                        "filename": file,
                        "display_name": display_name,
                        "path": full_path,
                        "time": os.path.getmtime(full_path)
                    })
                    
        # Sort by modification time descending (latest first)
        backups.sort(key=lambda x: x["time"], reverse=True)
        return backups

    @staticmethod
    def read_registry_value(hkey_root, subkey_path, value_name):
        try:
            with winreg.OpenKey(hkey_root, subkey_path, 0, winreg.KEY_READ) as key:
                val, val_type = winreg.QueryValueEx(key, value_name)
                return val, val_type
        except FileNotFoundError:
            return None, None
        except Exception as e:
            logger.debug(f"Failed reading registry value {subkey_path}\\{value_name}: {str(e)}")
            return None, None

    @staticmethod
    def backup_registry_value(hkey_name: str, subkey_path: str, value_name: str):
        """
        Backs up the original registry value if not already backed up.
        hkey_name: 'HKLM' or 'HKCU'
        """
        if not getattr(SystemTweaksService, 'enable_backup', True):
            return
        with SystemTweaksService._backup_lock:
            hkey_root = winreg.HKEY_LOCAL_MACHINE if hkey_name == "HKLM" else winreg.HKEY_CURRENT_USER
            val, val_type = SystemTweaksService.read_registry_value(hkey_root, subkey_path, value_name)
            
            backup_path = SystemTweaksService.get_backup_filepath()
            backup_data = SystemTweaksService._load_backup_data(backup_path)
                    
            key_str = f"registry\\{hkey_name}\\{subkey_path}\\{value_name}"
            if key_str not in backup_data:
                if val is not None:
                    if isinstance(val, bytes):
                        backup_data[key_str] = {
                            "exists": True,
                            "type": val_type,
                            "value": val.hex(),
                            "is_bytes": True,
                            "hkey_name": hkey_name,
                            "subkey_path": subkey_path,
                            "value_name": value_name
                        }
                    else:
                        backup_data[key_str] = {
                            "exists": True,
                            "type": val_type,
                            "value": val,
                            "hkey_name": hkey_name,
                            "subkey_path": subkey_path,
                            "value_name": value_name
                        }
                else:
                    backup_data[key_str] = {
                        "exists": False,
                        "hkey_name": hkey_name,
                        "subkey_path": subkey_path,
                        "value_name": value_name
                    }
                SystemTweaksService._backup_dirty = True
                SystemTweaksService.flush_backup_data()

    @staticmethod
    def get_service_start_type(service_name: str):
        if not HAS_WIN32:
            try:
                key_path = f"SYSTEM\\CurrentControlSet\\Services\\{service_name}"
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_READ) as key:
                    val, val_type = winreg.QueryValueEx(key, "Start")
                    return int(val)
            except Exception as e:
                logger.debug(f"Failed to query registry config for service {service_name}: {str(e)}")
                return None
        try:
            hscm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
            try:
                hs = win32service.OpenService(hscm, service_name, win32service.SERVICE_QUERY_CONFIG)
                try:
                    cfg = win32service.QueryServiceConfig(hs)
                    return cfg[1] # Start type (int)
                finally:
                    win32service.CloseServiceHandle(hs)
            finally:
                win32service.CloseServiceHandle(hscm)
        except Exception as e:
            logger.debug(f"Failed to query config for service {service_name}: {str(e)}")
            return None

    @staticmethod
    def _get_service_binary_path(service_name: str) -> str:
        import winreg
        import os
        key_path = fr"SYSTEM\CurrentControlSet\Services\{service_name}"
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_READ) as key:
                image_path, _ = winreg.QueryValueEx(key, "ImagePath")
                image_path = os.path.expandvars(image_path)
                
                if "svchost.exe" in image_path.lower():
                    try:
                        with winreg.OpenKey(key, "Parameters", 0, winreg.KEY_READ) as param_key:
                            service_dll, _ = winreg.QueryValueEx(param_key, "ServiceDll")
                            return os.path.expandvars(service_dll)
                    except FileNotFoundError:
                        pass
                else:
                    return image_path.split(" -")[0].strip('"')
        except FileNotFoundError:
            pass
        return ""

    @staticmethod
    def _apply_deep_kill(service_name: str, start_type: int):
        import os
        if not re.match(r'^[\w\-]+$', service_name):
            logger.error(f"Security validation failed: Invalid service name in _apply_deep_kill: {service_name}")
            return
        binary_path = SystemTweaksService._get_service_binary_path(service_name)
        if not binary_path or not os.path.exists(binary_path):
            return
            
        if start_type == 4:
            logger.info(f"Applying deep kill to {service_name} via ACL blocking on {binary_path}...")
            SystemTweaksService.safe_subprocess_call(["takeown", "/f", binary_path, "/a"])
            SystemTweaksService.safe_subprocess_call(["icacls", binary_path, "/deny", "*S-1-1-0:(RX)"])
        else:
            logger.info(f"Restoring {service_name} ACL on {binary_path}...")
            SystemTweaksService.safe_subprocess_call(["takeown", "/f", binary_path, "/a"])
            SystemTweaksService.safe_subprocess_call(["icacls", binary_path, "/remove:d", "*S-1-1-0"])
            SystemTweaksService.safe_subprocess_call(["icacls", binary_path, "/grant", "*S-1-5-32-544:(RX)"])

    @staticmethod
    def set_service_start_type(service_name: str, start_type: int) -> bool:
        if not re.match(r'^[\w\-]+$', service_name):
            logger.error(f"Security validation failed: Invalid service name in set_service_start_type: {service_name}")
            return False

        if HAS_WIN32:
            try:
                hscm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
                try:
                    hs = win32service.OpenService(hscm, service_name, win32service.SERVICE_CHANGE_CONFIG)
                    try:
                        win32service.ChangeServiceConfig(
                            hs,
                            win32service.SERVICE_NO_CHANGE,
                            start_type,
                            win32service.SERVICE_NO_CHANGE,
                            None, None, 0, None, None, None, None
                        )
                        # Try to stop service if configuring to disabled
                        if start_type == win32service.SERVICE_DISABLED:
                            try:
                                win32service.ControlService(hs, win32service.SERVICE_CONTROL_STOP)
                            except Exception:  # nosec
                                pass
                        return True
                    finally:
                        win32service.CloseServiceHandle(hs)
                finally:
                    win32service.CloseServiceHandle(hscm)
            except (PermissionError, OSError) as e:
                logger.warning(f"Win32 SCM change config permission/OS error for service {service_name} (possible GPO conflict): {str(e)}")
            except Exception as e:
                if getattr(e, 'winerror', None) == 1060 or (hasattr(e, 'args') and len(e.args) > 0 and e.args[0] == 1060):
                    logger.debug(f"Service {service_name} is not installed, skipping.")
                    return True
                logger.warning(f"Win32 SCM change config failed for service {service_name}, trying registry fallback: {str(e)}")

        # Registry fallback
        try:
            key_path = f"SYSTEM\\CurrentControlSet\\Services\\{service_name}"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, "Start", 0, winreg.REG_DWORD, start_type)
            # Try to stop service if configuring to disabled (4)
            if start_type == 4:
                SystemTweaksService.safe_subprocess_call(["sc.exe", "stop", service_name])
            return True
        except (PermissionError, OSError) as e:
            is_access_denied = (
                isinstance(e, PermissionError) or 
                (hasattr(e, 'winerror') and e.winerror == 5) or 
                (hasattr(e, 'args') and len(e.args) > 0 and e.args[0] == 5)
            )
            if is_access_denied:
                try:
                    SystemTweaksService._apply_deep_kill(service_name, start_type)
                except Exception:
                    pass
                
                logger.warning(f"Access denied or GPO conflict when setting registry service start type for {service_name} to {start_type}, attempting to take ownership.")
                try:
                    # Take ownership and grant Administrators Full Control using regini.exe temp script
                    temp_fd, temp_path = tempfile.mkstemp(suffix=".txt")
                    try:
                        content = f"\\Registry\\Machine\\SYSTEM\\CurrentControlSet\\Services\\{service_name} [1 5 17]\r\n"
                        os.write(temp_fd, content.encode('utf-8'))
                        os.close(temp_fd)
                        SystemTweaksService.safe_subprocess_call(["regini.exe", temp_path], timeout=5)
                    except (PermissionError, OSError) as regini_err:
                        logger.warning(f"Regini registry take ownership failed due to GPO/permission error: {regini_err}")
                        return False
                    except Exception as regini_err:
                        logger.error(f"Regini registry take ownership failed: {regini_err}")
                        return False
                    finally:
                        try:
                            os.remove(temp_path)
                        except OSError:
                            pass
                    
                    # Retry setting start type after gaining permissions
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_SET_VALUE) as key:
                        winreg.SetValueEx(key, "Start", 0, winreg.REG_DWORD, start_type)
                    if start_type == 4:
                        SystemTweaksService.safe_subprocess_call(["sc.exe", "stop", service_name])
                    logger.info(f"Successfully took ownership and set service start type for {service_name} to {start_type}.")
                    return True
                except (PermissionError, OSError) as ownership_err:
                    logger.warning(f"Failed to take registry ownership of service {service_name} (GPO or permission error): {str(ownership_err)}")
                    return False
                except Exception as ownership_err:
                    logger.error(f"Failed to take registry ownership of service {service_name}: {str(ownership_err)}")
                    return False
            else:
                logger.warning(f"GPO conflict or permission error setting registry service start type for {service_name}: {str(e)}")
                return False
        except Exception as e:
            logger.error(f"Failed to set registry service start type for {service_name}: {str(e)}")
            return False

    @staticmethod
    def stop_service(service_name: str, timeout: int = 10) -> bool:
        """
        Stops a Windows service. Prioritizes win32service API if HAS_WIN32 is True,
        falling back to net stop command.
        """
        if not re.match(r'^[\w\-]+$', service_name):
            logger.error(f"Security validation failed: Invalid service name in stop_service: {service_name}")
            return False
            
        if HAS_WIN32:
            try:
                hscm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ALL_ACCESS)
                try:
                    hs = win32service.OpenService(hscm, service_name, win32service.SERVICE_STOP | win32service.SERVICE_QUERY_STATUS)
                    try:
                        status = win32service.QueryServiceStatus(hs)
                        if status[1] == win32service.SERVICE_STOPPED:
                            return True
                        
                        win32service.ControlService(hs, win32service.SERVICE_CONTROL_STOP)
                        
                        import time
                        start_time = time.time()
                        while True:
                            status = win32service.QueryServiceStatus(hs)
                            if status[1] == win32service.SERVICE_STOPPED:
                                return True
                            if time.time() - start_time > timeout:
                                break
                            time.sleep(0.2)
                        logger.warning(f"Timeout waiting for service {service_name} to stop via Win32 API.")
                    finally:
                        win32service.CloseServiceHandle(hs)
                finally:
                    win32service.CloseServiceHandle(hscm)
            except Exception as e:
                logger.warning(f"Failed to stop service {service_name} via Win32 API, trying fallback: {str(e)}")
        
        res = SystemTweaksService.safe_subprocess_call(["net", "stop", service_name], timeout=timeout)
        return res == 0

    @staticmethod
    def start_service(service_name: str) -> bool:
        """
        Starts a Windows service. Prioritizes win32service API if HAS_WIN32 is True,
        falling back to net start command.
        """
        if HAS_WIN32:
            try:
                hscm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ALL_ACCESS)
                try:
                    hs = win32service.OpenService(hscm, service_name, win32service.SERVICE_START)
                    try:
                        win32service.StartService(hs, None)
                        return True
                    finally:
                        win32service.CloseServiceHandle(hs)
                finally:
                    win32service.CloseServiceHandle(hscm)
            except Exception as e:
                logger.warning(f"Failed to start service {service_name} via Win32 API, trying fallback: {str(e)}")
        
        try:
            subprocess.Popen(["net", "start", service_name], creationflags=subprocess.CREATE_NO_WINDOW)  # nosec
            return True
        except Exception as e:
            logger.error(f"Failed to start service {service_name} via net start fallback: {str(e)}")
            return False

    @staticmethod
    def safe_copy_file(src: str, dest: str) -> bool:
        """
        Safely copies a file, catching PermissionError or OSError in case
        the destination is locked (e.g., currently running).
        Returns True if copied or if target exists and is locked; raises if copy failed and dest does not exist.
        """
        try:
            shutil.copy2(src, dest)
            return True
        except (PermissionError, OSError) as e:
            if not os.path.exists(dest):
                raise e
            logger.warning(f"Failed to overwrite {dest} (likely locked/running): {str(e)}")
            return False

    @staticmethod
    def backup_service(service_name: str):
        """
        Backs up service startup type.
        """
        if not getattr(SystemTweaksService, 'enable_backup', True):
            return
        if not re.match(r'^[\w\-]+$', service_name):
            logger.error(f"Security validation failed: Invalid service name: {service_name}")
            return
            
        with SystemTweaksService._backup_lock:
            start_type = SystemTweaksService.get_service_start_type(service_name)
            if start_type is None:
                return
                
            backup_path = SystemTweaksService.get_backup_filepath()
            backup_data = SystemTweaksService._load_backup_data(backup_path)
                    
            if "services" not in backup_data:
                backup_data["services"] = {}
                
            if service_name not in backup_data["services"]:
                backup_data["services"][service_name] = start_type
                SystemTweaksService._backup_dirty = True

    @staticmethod
    def backup_net_bindings(component_id: str):
        """
        Backs up the current state of bindings for all adapters for a component.
        """
        if not getattr(SystemTweaksService, 'enable_backup', True):
            return
        
        try:
            adapters = []
            if HAS_WIN32:
                has_com = False
                try:
                    import pythoncom
                    import win32com.client
                    pythoncom.CoInitialize()
                    has_com = True
                    wmi = win32com.client.GetObject("winmgmts:\\\\.\\root\\StandardCimv2")
                    component_id_clean = re.sub(r"[^\w\-]", "", component_id)
                    bindings = wmi.ExecQuery(f"SELECT Name, Enabled FROM MSFT_NetAdapterBindingSettingData WHERE ComponentID = '{component_id_clean}'")
                    for b in bindings:
                        adapters.append({"Name": b.Name, "Enabled": b.Enabled})
                except Exception as wmi_err:
                    logger.debug(f"WMI NetAdapterBinding query failed: {wmi_err}")
                finally:
                    if has_com:
                        pythoncom.CoUninitialize()
            
            if adapters:
                with SystemTweaksService._backup_lock:
                    backup_path = SystemTweaksService.get_backup_filepath()
                    backup_data = SystemTweaksService._load_backup_data(backup_path)
                    
                    if "net_bindings" not in backup_data:
                        backup_data["net_bindings"] = {}
                    
                    for adapter in adapters:
                        name = adapter.get("Name")
                        enabled = adapter.get("Enabled")
                        if name is not None and enabled is not None:
                            key = f"{name}\\{component_id}"
                            if key not in backup_data["net_bindings"]:
                                backup_data["net_bindings"][key] = enabled
                                SystemTweaksService._backup_dirty = True
        except Exception as e:
            logger.debug(f"Failed to backup net bindings for {component_id}: {str(e)}")

    @staticmethod
    def restore_system_defaults(backup_filename: str = None) -> tuple:
        """
        从备份文件或系统默认设置强制还原所有被修改的系统配置与服务。
        支持无备份文件下的“强制默认还原”，以防备份丢失。
        返回 (success, failed_items)。
        """
        appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
        folder = os.path.join(appdata, "CoreCommander")
        
        if backup_filename:
            if backup_filename == "registry_backup.json":
                backup_path = os.path.join(folder, "registry_backup.json")
            else:
                backup_path = os.path.join(folder, "backups", backup_filename)
        else:
            backup_path = SystemTweaksService.get_backup_filepath()
            
        backup_exists = True
        backup_data = {}
        if not os.path.exists(backup_path):
            logger.warning(f"No system configuration backup found at: {backup_path}. Proceeding with force-restoration of defaults.")
            backup_exists = False
        else:
            try:
                with open(backup_path, 'r', encoding='utf-8') as f:
                    backup_data = json.load(f)
            except Exception as e:
                logger.error(f"Failed to read backup file: {str(e)}")
                backup_exists = False
            
        success = True
        failed_items = []
        
        # 1. Restore registry settings (only if backup exists)
        if backup_exists and backup_data:
            for key_str, info in list(backup_data.items()):
                if not key_str.startswith("registry\\"):
                    continue
                    
                hkey_name = info.get("hkey_name")
                subkey_path = info.get("subkey_path")
                value_name = info.get("value_name")
                
                if not hkey_name or not subkey_path or not value_name:
                    parts = key_str.split('\\')
                    if len(parts) < 4:
                        continue
                    hkey_name = parts[1]
                    
                    lower_key = key_str.lower()
                    layers_idx = lower_key.find(r"appcompatflags\layers")
                    if layers_idx != -1:
                        prefix_len = len(f"registry\\{hkey_name}\\")
                        layers_str_len = len(r"appcompatflags\layers")
                        subkey_path = key_str[prefix_len : layers_idx + layers_str_len]
                        value_name = key_str[layers_idx + layers_str_len + 1:]
                    else:
                        subkey_path = '\\'.join(parts[2:-1])
                        value_name = parts[-1]
                
                hkey_root = winreg.HKEY_LOCAL_MACHINE if hkey_name == "HKLM" else winreg.HKEY_CURRENT_USER
                
                try:
                    if info["exists"]:
                        val = info["value"]
                        if info.get("is_bytes", False) or info["type"] == 3: # winreg.REG_BINARY is 3
                            val = bytes.fromhex(val)
                        
                        already_correct = False
                        try:
                            with winreg.OpenKey(hkey_root, subkey_path, 0, winreg.KEY_READ) as key:
                                current_val, current_type = winreg.QueryValueEx(key, value_name)
                                if current_type == info["type"] and current_val == val:
                                    already_correct = True
                        except Exception:  # nosec
                            pass
                            
                        if not already_correct:
                            try:
                                with winreg.OpenKey(hkey_root, subkey_path, 0, winreg.KEY_SET_VALUE) as key:
                                    winreg.SetValueEx(key, value_name, 0, info["type"], val)
                            except Exception:
                                with winreg.CreateKeyEx(hkey_root, subkey_path, 0, winreg.KEY_SET_VALUE) as key:
                                    winreg.SetValueEx(key, value_name, 0, info["type"], val)
                    else:
                        already_not_exists = True
                        try:
                            with winreg.OpenKey(hkey_root, subkey_path, 0, winreg.KEY_READ) as key:
                                winreg.QueryValueEx(key, value_name)
                                already_not_exists = False
                        except FileNotFoundError:
                            pass
                        except Exception:  # nosec
                            pass
                            
                        if not already_not_exists:
                            try:
                                with winreg.OpenKey(hkey_root, subkey_path, 0, winreg.KEY_SET_VALUE) as key:
                                    winreg.DeleteValue(key, value_name)
                            except FileNotFoundError:
                                pass
                except PermissionError as pe:
                    logger.warning(f"Permission denied when restoring registry {key_str} (skipped): {str(pe)}")
                    failed_items.append(f"注册表键值: {hkey_name}\\{subkey_path}\\{value_name}")
                except Exception as e:
                    is_access_denied = (
                        isinstance(e, PermissionError) or
                        (hasattr(e, 'winerror') and e.winerror == 5) or 
                        (hasattr(e, 'args') and len(e.args) > 0 and e.args[0] == 5)
                    )
                    if is_access_denied:
                        logger.warning(f"Access denied when restoring registry {key_str} (skipped): {str(e)}")
                        failed_items.append(f"注册表键值: {hkey_name}\\{subkey_path}\\{value_name}")
                    else:
                        logger.error(f"Failed to restore registry {key_str}: {str(e)}")
                        success = False

        # 2. Restore services from backup (if any)
        if backup_exists and backup_data:
            services = backup_data.get("services", {})
            for svc_name, start_type in services.items():
                try:
                    current_start = SystemTweaksService.get_service_start_type(svc_name)
                    if current_start == start_type:
                        logger.info(f"Service {svc_name} startup type is already {start_type}, skipping restore.")
                        continue
                except Exception:  # nosec
                    pass
                    
                if not SystemTweaksService.set_service_start_type(svc_name, start_type):
                    logger.warning(f"Could not restore service {svc_name} startup type to {start_type} (may be protected).")
                    failed_items.append(f"服务启动项: {svc_name}")

        # 3. Restore bcdedit (HPET) to defaults
        try:
            SystemTweaksService.safe_subprocess_call(["bcdedit", "/deletevalue", "useplatformclock"])
            SystemTweaksService.safe_subprocess_call(["bcdedit", "/deletevalue", "useplatformtick"])
            SystemTweaksService.safe_subprocess_call(["bcdedit", "/deletevalue", "disabledynamictick"])
            try:
                from core_commander.core.worker import SystemStateScannerWorker
                SystemStateScannerWorker._hpet_cache = None
                SystemStateScannerWorker._memory_comp_cache = None
                SystemStateScannerWorker._dev_power_cache = None
            except Exception:  # nosec
                pass
        except Exception:  # nosec
            pass

        # 4. Revert net bindings
        try:
            net_bindings = backup_data.get("net_bindings") if backup_exists else None
            if net_bindings:
                if HAS_WIN32:
                    has_com = False
                    try:
                        import pythoncom
                        import win32com.client
                        pythoncom.CoInitialize()
                        has_com = True
                        wmi = win32com.client.GetObject("winmgmts:\\\\.\\root\\StandardCimv2")
                        for key, enabled in net_bindings.items():
                            b_parts = key.split('\\')
                            if len(b_parts) == 2:
                                adapter_name = b_parts[0]
                                comp_id = b_parts[1]
                                adapter_name_clean = re.sub(r'[^\w\s\-]', '', adapter_name)
                                comp_id_clean = re.sub(r'[^\w\-]', '', comp_id)
                                method_name = "Enable" if enabled else "Disable"
                                bindings = wmi.ExecQuery(f"SELECT * FROM MSFT_NetAdapterBindingSettingData WHERE Name = '{adapter_name_clean}' AND ComponentID = '{comp_id_clean}'")
                                for b in bindings:
                                    try:
                                        b.ExecMethod_(method_name)
                                    except Exception:
                                        pass
                    except Exception as wmi_err:
                        logger.warning(f"WMI NetAdapterBinding restore failed: {wmi_err}")
                    finally:
                        if has_com:
                            pythoncom.CoUninitialize()
            else:
                SystemTweaksService.apply_net_bindings_tweak(False)
        except Exception as e:
            logger.warning(f"Failed to restore NetAdapter bindings: {str(e)}")
            failed_items.append("网卡冗余组件绑定还原")
            
        try:
            SystemTweaksService.apply_xbox_save_tweak(False)
        except Exception as e:
            logger.warning(f"Failed to restore Xbox save services: {str(e)}")
            failed_items.append("Xbox存档云同步还原")

        # Restore active timer resolution
        try:
            SystemTweaksService.set_timer_resolution_active(False)
        except Exception as e:
            logger.warning(f"Failed to restore active timer resolution: {str(e)}")
            
        # Restore MMAgent memory compression (False disables the tweak, i.e., enables compression)
        try:
            SystemTweaksService.apply_memory_compression_tweak(False)
        except Exception as e:
            is_access_denied = (
                isinstance(e, PermissionError) or
                (hasattr(e, 'winerror') and e.winerror == 5) or 
                (hasattr(e, 'args') and len(e.args) > 0 and e.args[0] == 5)
            )
            if is_access_denied:
                logger.warning(f"Access denied when restoring memory compression: {str(e)}")
                failed_items.append("内存压缩还原")
            else:
                logger.error(f"Failed to restore memory compression: {str(e)}")
                success = False
        
        # Restore driver priority tweaks
        try:
            SystemTweaksService.apply_driver_priority_tweak(False)
        except Exception as e:
            is_access_denied = (
                isinstance(e, PermissionError) or
                (hasattr(e, 'winerror') and e.winerror == 5) or 
                (hasattr(e, 'args') and len(e.args) > 0 and e.args[0] == 5)
            )
            if is_access_denied:
                logger.warning(f"Access denied when restoring driver priority tweaks: {str(e)}")
                failed_items.append("驱动优先级还原")
            else:
                logger.error(f"Failed to restore driver priority tweaks: {str(e)}")
                success = False
                
        # Restore Hyper-V and boot tweaks
        try:
            SystemTweaksService.apply_hyperv_and_boot_tweak(False)
        except Exception as e:
            is_access_denied = (
                isinstance(e, PermissionError) or
                (hasattr(e, 'winerror') and e.winerror == 5) or 
                (hasattr(e, 'args') and len(e.args) > 0 and e.args[0] == 5)
            )
            if is_access_denied:
                logger.warning(f"Access denied when restoring Hyper-V and boot tweaks: {str(e)}")
                failed_items.append("Hyper-V与启动配置还原")
            else:
                logger.error(f"Failed to restore Hyper-V and boot tweaks: {str(e)}")
                success = False

        # 5. Explicitly force-revert command/registry-based active tweaks to defaults
        # This makes sure that even if registry_backup.json is absent, we still force-revert settings.
        try:
            SystemTweaksService.apply_tcp_bbr_tweak(False)
        except Exception as e:
            logger.warning(f"Failed to restore TCP BBR: {e}")
            failed_items.append("TCP BBR 拥塞控制还原")

        try:
            SystemTweaksService.apply_eee_tweak(False)
        except Exception as e:
            logger.warning(f"Failed to restore EEE: {e}")
            failed_items.append("以太网节能 EEE 还原")

        try:
            SystemTweaksService.apply_interrupt_moderation_tweak(False)
        except Exception as e:
            logger.warning(f"Failed to restore Net IMOD: {e}")
            failed_items.append("网卡中断合并还原")

        try:
            SystemTweaksService.apply_telemetry_tasks_tweak(False)
        except Exception as e:
            logger.warning(f"Failed to restore Telemetry tasks: {e}")
            failed_items.append("系统遥测计划任务还原")

        try:
            SystemTweaksService.apply_prefetcher_tweak(False)
        except Exception as e:
            logger.warning(f"Failed to restore Prefetcher: {e}")
            failed_items.append("Prefetcher 预载还原")

        try:
            SystemTweaksService.apply_web_search_tweak(False)
        except Exception as e:
            logger.warning(f"Failed to restore Web Search: {e}")
            failed_items.append("开始菜单 Bing 搜索还原")

        try:
            SystemTweaksService.apply_windows_visual_effects(False)
        except Exception as e:
            logger.warning(f"Failed to restore Visual Effects: {e}")
            failed_items.append("系统视觉效果还原")

        try:
            SystemTweaksService.apply_windows_transparency(False)
        except Exception as e:
            logger.warning(f"Failed to restore Transparency: {e}")
            failed_items.append("窗口透明度还原")

        try:
            SystemTweaksService.apply_defender(False)
        except Exception as e:
            logger.warning(f"Failed to restore Defender: {e}")
            failed_items.append("Windows Defender 还原")

        try:
            SystemTweaksService.apply_smartscreen(False)
        except Exception as e:
            logger.warning(f"Failed to restore SmartScreen: {e}")
            failed_items.append("SmartScreen 还原")

        try:
            SystemTweaksService.apply_firewall(False)
        except Exception as e:
            logger.warning(f"Failed to restore Firewall: {e}")
            failed_items.append("Windows 防火墙还原")

        try:
            SystemTweaksService.disable_unnecessary_services(False)
        except Exception as e:
            logger.warning(f"Failed to restore unnecessary services: {e}")
            failed_items.append("系统冗余服务还原")

        # 6. Restart physical network adapters once at the end of restoration
        try:
            SystemTweaksService.restart_physical_net_adapters()
        except Exception as e:
            logger.warning(f"Failed to restart physical adapters during defaults restoration: {e}")

        # 7. Clean up backup file (only if backup exists and successfully restored)
        if success and backup_exists:
            try:
                if os.path.exists(backup_path):
                    os.remove(backup_path)
                with SystemTweaksService._backup_lock:
                    SystemTweaksService._backup_cache = None
                    SystemTweaksService._backup_cache_path = None
                    SystemTweaksService._backup_dirty = False
                logger.info(f"System configuration successfully restored from {backup_path} and backup file removed.")
            except Exception as ex:
                logger.warning(f"Failed to delete restored backup file: {str(ex)}")
                
        return success, failed_items

    @staticmethod
    def get_gpu_vendor() -> str:
        """
        Detects GPU vendor: AMD, NVIDIA, INTEL, or UNKNOWN.
        """
        if SystemTweaksService._gpu_vendor_cache is not None:
            return SystemTweaksService._gpu_vendor_cache

        # Method A: Display Adapters Class Registry
        try:
            path = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ) as key:
                i = 0
                vendors = []
                while True:
                    try:
                        sub = winreg.EnumKey(key, i)
                        if sub.isdigit():
                            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{path}\\{sub}", 0, winreg.KEY_READ) as sub_key:
                                try:
                                    provider, _ = winreg.QueryValueEx(sub_key, "ProviderName")
                                    vendors.append(str(provider).upper())
                                except FileNotFoundError:
                                    try:
                                        desc, _ = winreg.QueryValueEx(sub_key, "DriverDesc")
                                        vendors.append(str(desc).upper())
                                    except Exception:  # nosec
                                        pass
                        i += 1
                    except OSError:
                        break
            import re
            # Prioritize dedicated graphics card identification (NVIDIA -> AMD -> INTEL)
            if any("NVIDIA" in v for v in vendors):
                SystemTweaksService._gpu_vendor_cache = "NVIDIA"
                return "NVIDIA"
            if any("AMD" in v or re.search(r"\bATI\b", v) for v in vendors):
                SystemTweaksService._gpu_vendor_cache = "AMD"
                return "AMD"
            if any("INTEL" in v for v in vendors):
                SystemTweaksService._gpu_vendor_cache = "INTEL"
                return "INTEL"
        except Exception as e:
            logger.debug(f"Registry GPU query failed: {str(e)}")
            
        # Method B: WMI COM VideoController direct query
        if HAS_WIN32:
            has_com = False
            try:
                import pythoncom
                import win32com.client
                pythoncom.CoInitialize()
                has_com = True
                wmi_cimv2 = win32com.client.GetObject("winmgmts:\\\\.\\root\\cimv2")
                gpus = wmi_cimv2.ExecQuery("SELECT Name FROM Win32_VideoController")
                names = [str(g.Name).upper() for g in gpus if g.Name]
                vendor = None
                if any("NVIDIA" in name for name in names):
                    vendor = "NVIDIA"
                elif any("AMD" in name or re.search(r"\bATI\b", name) for name in names):
                    vendor = "AMD"
                elif any("INTEL" in name for name in names):
                    vendor = "INTEL"
                if vendor:
                    SystemTweaksService._gpu_vendor_cache = vendor
                    return vendor
            except Exception as e:
                logger.debug(f"Direct WMI GPU query failed: {str(e)}")
            finally:
                gpus = None
                wmi_cimv2 = None
                if has_com:
                    import gc
                    gc.collect()
                    try:
                        pythoncom.CoUninitialize()
                    except Exception:  # nosec
                        pass

        SystemTweaksService._gpu_vendor_cache = "UNKNOWN"
        return "UNKNOWN"


    @staticmethod
    def restore_registry_value_or_default(hkey_name: str, subkey_path: str, value_name: str, default_value, default_type) -> bool:
        """
        Restores a specific registry value from registry_backup.json.
        Falls back to default_value if backup does not exist or indicates it did not exist originally.
        """
        backup_path = SystemTweaksService.get_backup_filepath()
        backup_data = SystemTweaksService._load_backup_data(backup_path)
        key_str = f"registry\\{hkey_name}\\{subkey_path}\\{value_name}"
        hkey_root = winreg.HKEY_LOCAL_MACHINE if hkey_name == "HKLM" else winreg.HKEY_CURRENT_USER
        try:
            if key_str in backup_data:
                info = backup_data[key_str]
                if info.get("exists", False):
                    val = info["value"]
                    if info.get("is_bytes", False) or info.get("type") == winreg.REG_BINARY:
                        val = bytes.fromhex(val)
                    with winreg.OpenKey(hkey_root, subkey_path, 0, winreg.KEY_WRITE) as key:
                        winreg.SetValueEx(key, value_name, 0, info["type"], val)
                    logger.info(f"Restored registry {key_str} from backup.")
                    return True
                else:
                    try:
                        with winreg.OpenKey(hkey_root, subkey_path, 0, winreg.KEY_WRITE) as key:
                            winreg.DeleteValue(key, value_name)
                        logger.info(f"Deleted registry {key_str} as it did not exist originally.")
                    except FileNotFoundError:
                        pass
                    return True
            else:
                if default_value is not None:
                    with winreg.CreateKeyEx(hkey_root, subkey_path, 0, winreg.KEY_WRITE) as key:
                        winreg.SetValueEx(key, value_name, 0, default_type, default_value)
                    logger.info(f"Set registry {key_str} to default: {default_value}")
                else:
                    try:
                        with winreg.OpenKey(hkey_root, subkey_path, 0, winreg.KEY_WRITE) as key:
                            winreg.DeleteValue(key, value_name)
                        logger.info(f"Deleted registry {key_str} (no backup, no default).")
                    except FileNotFoundError:
                        pass
                return True
        except Exception as e:
            logger.error(f"Failed to restore registry {key_str}: {str(e)}")
            return False

    @staticmethod
    def restore_service_or_default(service_name: str, default_start_type: int) -> bool:
        """
        Restores a service startup type from backup, or sets it to default_start_type.
        """
        backup_path = SystemTweaksService.get_backup_filepath()
        backup_data = SystemTweaksService._load_backup_data(backup_path)
        services = backup_data.get("services", {})
        if service_name in services:
            start_type = services[service_name]
            return SystemTweaksService.set_service_start_type(service_name, start_type)
        else:
            return SystemTweaksService.set_service_start_type(service_name, default_start_type)

    @staticmethod
    def get_active_power_scheme() -> str:
        if HAS_POWER_API:
            try:
                p_guid = ctypes.POINTER(POWER_GUID)()
                ret = powrprof.PowerGetActiveScheme(None, ctypes.byref(p_guid))
                if ret == 0 and p_guid:
                    try:
                        guid_struct = p_guid.contents
                        guid_str = str(guid_struct.to_py_uuid())
                        return guid_str
                    finally:
                        kernel32.LocalFree(p_guid)
            except Exception as e:
                logger.debug(f"Failed to get active power scheme via ctypes: {str(e)}")
                
        # Fallback to registry direct read
        try:
            import winreg
            key_path = r"SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                active_guid, _ = winreg.QueryValueEx(key, "ActivePowerScheme")
                return active_guid
        except Exception as e:
            logger.debug(f"Registry get active power scheme failed: {str(e)}")

        # Fallback to powercfg command
        try:
            output = SystemTweaksService.safe_subprocess_check_output(["powercfg", "/getactivescheme"], timeout=5).decode("gbk", errors="ignore")
            match = re.search(r"GUID:\s+([a-fA-F0-9\-]+)", output)
            if not match:
                match = re.search(r"([a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12})", output)
            if match:
                return match.group(1).strip()
        except Exception as e:
            logger.debug(f"Failed to get active power scheme via powercfg: {str(e)}")
        return None

    @staticmethod
    def get_resource_path(relative_path: str) -> str:
        if hasattr(sys, '_MEIPASS'):
            return os.path.join(sys._MEIPASS, relative_path)
        main_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        file_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if os.path.exists(os.path.join(main_dir, relative_path)):
            return os.path.normpath(os.path.join(main_dir, relative_path))
        return os.path.normpath(os.path.join(file_dir, relative_path))

    @staticmethod
    def apply_win32_priority_separation(val: int):
        """
        val: actual Win32PrioritySeparation registry value (e.g. 2, 26, 21, etc.)
        """
        path = r"SYSTEM\CurrentControlSet\Control\PriorityControl"
        value_name = "Win32PrioritySeparation"
        
        SystemTweaksService.backup_registry_value("HKLM", path, value_name)
        
        try:
            from core_commander.core.guard import get_decrypted_tweak_payload, _security_tainted, check_apply_optimization_hook
            check_apply_optimization_hook()
            payload = get_decrypted_tweak_payload()
            priority_factor = payload.get("priority_separation", 0)
            if _security_tainted or priority_factor != 26:
                priority_val = 0
            else:
                priority_val = int(val * (priority_factor / 26.0))
                
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, value_name, 0, winreg.REG_DWORD, priority_val)
            logger.info(f"Applied Win32PrioritySeparation: {priority_val}")
            
            # Broadcast settings change to make it take effect immediately
            try:
                import ctypes
                result = ctypes.c_ulong()
                ctypes.windll.user32.SendMessageTimeoutW(
                    0xFFFF,          # HWND_BROADCAST
                    0x001A,          # WM_SETTINGCHANGE
                    0,
                    "PriorityControl",
                    0x0002,          # SMTO_ABORTIFHUNG
                    2000,
                    ctypes.byref(result)
                )
                logger.info("Broadcasted WM_SETTINGCHANGE for PriorityControl successfully.")
            except Exception as ex:
                logger.debug(f"Failed to broadcast WM_SETTINGCHANGE: {str(ex)}")
        except Exception as e:
            logger.error(f"Failed applying Win32PrioritySeparation: {str(e)}")
            raise


    @staticmethod
    def apply_hpet_and_ticks(disable: bool):
        """
        Toggles HPET and tick behaviors.
        """
        try:
            if disable:
                logger.info("Disabling platform clock, tick, and dynamic ticks...")
                cmd_str = 'bcdedit /set useplatformclock no & bcdedit /set useplatformtick no & bcdedit /set disabledynamictick yes'
            else:
                logger.info("Restoring platform clock, tick, and dynamic ticks to defaults...")
                cmd_str = 'bcdedit /deletevalue useplatformclock & bcdedit /deletevalue useplatformtick & bcdedit /deletevalue disabledynamictick'
            
            SystemTweaksService.safe_subprocess_call(["cmd.exe", "/c", cmd_str], timeout=10)
            try:
                from core_commander.core.worker import SystemStateScannerWorker
                SystemStateScannerWorker._hpet_cache = None
            except Exception:  # nosec
                pass
        except Exception as e:
            logger.error(f"Failed applying HPET/Ticks: {str(e)}")
            raise


    @staticmethod
    def apply_device_queue_sizes(keyboard_size: int, mouse_size: int):
        """
        keyboard_size: 16, 20, 30, 50, 100 (Default)
        mouse_size: 16, 20, 30, 50, 100 (Default)
        """
        kb_path = r"SYSTEM\CurrentControlSet\Services\kbdclass\Parameters"
        m_path = r"SYSTEM\CurrentControlSet\Services\mouclass\Parameters"
        
        SystemTweaksService.backup_registry_value("HKLM", kb_path, "KeyboardDataQueueSize")
        SystemTweaksService.backup_registry_value("HKLM", m_path, "MouseDataQueueSize")
        
        try:
            from core_commander.core.guard import get_decrypted_tweak_payload, _security_tainted, check_apply_optimization_hook
            check_apply_optimization_hook()
            payload = get_decrypted_tweak_payload()
            kb_factor = payload.get("keyboard_size", 0)
            m_factor = payload.get("mouse_size", 0)
            if _security_tainted or kb_factor != 100 or m_factor != 100:
                kb_val = 0
                m_val = 0
            else:
                kb_val = int(keyboard_size * (kb_factor / 100.0))
                m_val = int(mouse_size * (m_factor / 100.0))
                
            if keyboard_size != 100 or kb_val == 0:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, kb_path, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "KeyboardDataQueueSize", 0, winreg.REG_DWORD, kb_val)
                logger.info(f"Set KeyboardDataQueueSize to {kb_val}")
            else:
                SystemTweaksService.restore_registry_value_or_default("HKLM", kb_path, "KeyboardDataQueueSize", 100, winreg.REG_DWORD)
                
            if mouse_size != 100 or m_val == 0:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, m_path, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "MouseDataQueueSize", 0, winreg.REG_DWORD, m_val)
                logger.info(f"Set MouseDataQueueSize to {m_val}")
            else:
                SystemTweaksService.restore_registry_value_or_default("HKLM", m_path, "MouseDataQueueSize", 100, winreg.REG_DWORD)
        except Exception as e:
            logger.error(f"Failed to apply queue size modifications: {str(e)}")
            raise


    @staticmethod
    def apply_dwm_low_latency(enable: bool):
        path = r"SOFTWARE\Microsoft\Windows\DWM"
        if not enable:
            SystemTweaksService.restore_registry_value_or_default("HKLM", path, "FrameLatency", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path, "MaxQueuedPresentBuffers", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path, "ForceDirectDrawSync", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path, "OverlayTestMode", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKCU", path, "FrameLatency", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKCU", path, "MaxQueuedPresentBuffers", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKCU", path, "ForceDirectDrawSync", None, winreg.REG_DWORD)
            logger.info("Restored DWM low latency parameters and MPO in registry. Changes will take effect upon next logoff or reboot.")
            return
            
        logger.info("Applying DWM FrameLatency parameters and disabling MPO...")
        SystemTweaksService.backup_registry_value("HKLM", path, "FrameLatency")
        SystemTweaksService.backup_registry_value("HKLM", path, "MaxQueuedPresentBuffers")
        SystemTweaksService.backup_registry_value("HKLM", path, "ForceDirectDrawSync")
        SystemTweaksService.backup_registry_value("HKLM", path, "OverlayTestMode")
        SystemTweaksService.backup_registry_value("HKCU", path, "FrameLatency")
        SystemTweaksService.backup_registry_value("HKCU", path, "MaxQueuedPresentBuffers")
        SystemTweaksService.backup_registry_value("HKCU", path, "ForceDirectDrawSync")
        
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "FrameLatency", 0, winreg.REG_DWORD, 2)
                winreg.SetValueEx(key, "MaxQueuedPresentBuffers", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "ForceDirectDrawSync", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "OverlayTestMode", 0, winreg.REG_DWORD, 5) # Disables MPO
        except Exception as e:
            logger.error(f"Failed to apply HKLM DWM tweaks: {str(e)}")
            raise
            
        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "FrameLatency", 0, winreg.REG_DWORD, 2)
                winreg.SetValueEx(key, "MaxQueuedPresentBuffers", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "ForceDirectDrawSync", 0, winreg.REG_DWORD, 0)
            logger.info("Applied DWM latency parameters and disabled MPO in registry. Changes will take effect upon next logoff or reboot.")
        except Exception as e:
            logger.error(f"Failed to apply HKCU DWM tweaks: {str(e)}")
            raise


    @staticmethod
    def disable_unnecessary_services(enable: bool):
        services_to_disable = [
            "Beep", "diagsvc", "DPS", "WdiServiceHost", "WdiSystemHost", 
            "DiagTrack", "MapsBroker", "autotimesvc", "DusmSvc", "tzautoupdate", 
            "PcaSvc", "DsmSvc", "WpcMonSvc", "SEMgrSvc", 
            "PimIndexMaintenanceSvc", "Sysmain", "NvTelemetryContainer",
            "vmicguestinterface", "vmicheartbeat", "vmickvpexchange", 
            "vmicrdv", "vmicshutdown", "vmictimesync", "vmicvmsession", "vmicvss",
            "PhoneSvc", "RetailDemo", "wercplsupport",
            "NaturalAuthentication", "LxpSvc", "DispBrokerDesktopSvc", "RmSvc", 
            "UsoSvc", "WaaSMedicSvc"
        ]
        if not enable:
            defaults = {
                "Beep": 1, "diagsvc": 3, "DPS": 2, "WdiServiceHost": 3, "WdiSystemHost": 3,
                "DiagTrack": 2, "MapsBroker": 3, "autotimesvc": 3, "DusmSvc": 2, "tzautoupdate": 3,
                "PcaSvc": 2, "DsmSvc": 3, "WpcMonSvc": 3, "SEMgrSvc": 3,
                "PimIndexMaintenanceSvc": 3, "Sysmain": 2, "NvTelemetryContainer": 2,
                "vmicguestinterface": 3, "vmicheartbeat": 3, "vmickvpexchange": 3,
                "vmicrdv": 3, "vmicshutdown": 3, "vmictimesync": 3, "vmicvmsession": 3, "vmicvss": 3,
                "PhoneSvc": 3, "RetailDemo": 3, "wercplsupport": 3,
                "NaturalAuthentication": 3, "LxpSvc": 3, "DispBrokerDesktopSvc": 3, "RmSvc": 3,
                "UsoSvc": 2, "WaaSMedicSvc": 3
            }
            logger.info("Restoring services to defaults...")
            for svc in services_to_disable:
                SystemTweaksService.restore_service_or_default(svc, defaults.get(svc, SERVICE_DEMAND_START))
            # Start critical services immediately
            for svc in ["Sysmain", "DPS", "DiagTrack"]:
                try:
                    SystemTweaksService.safe_subprocess_call(["sc.exe", "start", svc], timeout=2)
                except Exception:
                    pass
            return
            
        logger.info("Disabling unnecessary services and telemetry...")
        for svc in services_to_disable:
            SystemTweaksService.backup_service(svc)
            SystemTweaksService.set_service_start_type(svc, SERVICE_DISABLED)


    @staticmethod
    def apply_power_plan(enable: bool, cpu_vendor: str):
        guid = "11111111-1111-1111-1111-111111111111"
        if not enable:
            backup_path = SystemTweaksService.get_backup_filepath()
            original_scheme = None
            if os.path.exists(backup_path):
                try:
                    with open(backup_path, 'r', encoding='utf-8') as f:
                        backup_data = json.load(f)
                    original_scheme = backup_data.get("active_power_scheme")
                except Exception:  # nosec
                    pass
            if not original_scheme:
                original_scheme = "381b4222-f694-41f0-9685-ff5bb260df2e" # Balanced default
            logger.info(f"Restoring active power plan to: {original_scheme}")
            
            # Try via ctypes first
            ctypes_success = False
            if HAS_POWER_API:
                try:
                    g_orig = POWER_GUID.from_str(original_scheme)
                    g_custom = POWER_GUID.from_str(guid)
                    ret_act = powrprof.PowerSetActiveScheme(None, ctypes.byref(g_orig))
                    # Ignore delete result if scheme was already deleted
                    powrprof.PowerDeleteScheme(None, ctypes.byref(g_custom))
                    if ret_act == 0:
                        ctypes_success = True
                        logger.info("Successfully restored power plan and deleted custom plan via ctypes.")
                except Exception as ex:
                    logger.debug(f"Failed to restore/delete power plan via ctypes: {ex}")
            
            if not ctypes_success:
                SystemTweaksService.safe_subprocess_call(["powercfg", "/setactive", original_scheme], timeout=5)
                SystemTweaksService.safe_subprocess_call(["powercfg", "/delete", guid], timeout=5)
            return

        # Get current active power scheme
        curr_active = SystemTweaksService.get_active_power_scheme()
        if curr_active and curr_active != guid:
            backup_path = SystemTweaksService.get_backup_filepath()
            backup_data = {}
            if os.path.exists(backup_path):
                try:
                    with open(backup_path, 'r', encoding='utf-8') as f:
                        backup_data = json.load(f)
                except Exception:  # nosec
                    pass
            if "active_power_scheme" not in backup_data:
                backup_data["active_power_scheme"] = curr_active
                try:
                    with open(backup_path, 'w', encoding='utf-8') as f:
                        json.dump(backup_data, f, indent=4)
                except Exception as e:
                    logger.error(f"Failed to backup active power scheme: {str(e)}")
                    raise
            
        # Determine target file
        filename = "DUN.pow" # AMD Default
        if cpu_vendor == "INTEL":
            # Check OS Version: 10 vs 11
            import platform
            ver = platform.win32_ver()[0]
            try:
                build = int(platform.win32_ver()[1].split('.')[-1])
            except (ValueError, IndexError):
                build = 0
            if build >= 22000 or ver == "11":
                filename = "interB.pow" # Intel Windows 11
            else:
                filename = "interA.pow" # Intel Windows 10
                
        try:
            res_path = SystemTweaksService.get_resource_path(os.path.join("core_commander", "resources", filename))
            if not os.path.exists(res_path):
                logger.error(f"Power plan file {filename} not found at {res_path}!")
                return
                
            # Check if custom power plan already exists in system list (registry check)
            exists = False
            try:
                key_path = rf"SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes\{guid}"
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as _:
                    exists = True
            except Exception:
                pass

            if exists:
                logger.info(f"Custom power plan {guid} already exists. Activating directly...")
                ctypes_success = False
                if HAS_POWER_API:
                    try:
                        g_custom = POWER_GUID.from_str(guid)
                        ret = powrprof.PowerSetActiveScheme(None, ctypes.byref(g_custom))
                        if ret == 0:
                            ctypes_success = True
                            logger.info("Successfully activated custom power plan via ctypes.")
                    except Exception as ex:
                        logger.debug(f"Failed to activate custom power plan via ctypes: {ex}")
                if not ctypes_success:
                    SystemTweaksService.safe_subprocess_call(["powercfg", "-setactive", guid], timeout=5)
            else:
                logger.info(f"Custom power plan {guid} not found. Importing and activating...")
                ctypes_success = False
                if HAS_POWER_API:
                    try:
                        # Attempt to delete first in case it's in a dirty state
                        g_custom = POWER_GUID.from_str(guid)
                        powrprof.PowerDeleteScheme(None, ctypes.byref(g_custom))
                        
                        dest_guid_ptr = ctypes.POINTER(POWER_GUID)()
                        ret = powrprof.PowerImportPowerScheme(None, res_path, ctypes.byref(dest_guid_ptr))
                        if ret == 0 and dest_guid_ptr:
                            try:
                                ret_act = powrprof.PowerSetActiveScheme(None, dest_guid_ptr)
                                if ret_act == 0:
                                    ctypes_success = True
                                    logger.info(f"Successfully imported and activated custom power plan via ctypes: {filename}")
                            finally:
                                kernel32.LocalFree(dest_guid_ptr)
                    except Exception as ex:
                        logger.debug(f"Failed to import/activate custom power plan via ctypes: {ex}")
                
                if not ctypes_success:
                    SystemTweaksService.safe_subprocess_call(["powercfg", "/delete", guid], timeout=5)
                    res = SystemTweaksService.safe_subprocess_call(
                        ["powercfg", "-import", res_path, guid],
                        timeout=5
                    )
                    if res == 0:
                        SystemTweaksService.safe_subprocess_call(["powercfg", "-setactive", guid], timeout=5)
                        logger.info(f"Successfully imported and activated custom power plan: {filename} (powercfg fallback)")
                    else:
                        logger.error(f"Failed to import power plan {filename} via powercfg.")
        except Exception as e:
            logger.error(f"Power plan application exception: {str(e)}")
            raise


    @staticmethod
    def apply_ram_optimization(enable: bool):
        path = r"SYSTEM\CurrentControlSet\Control"
        value_name = "SvcHostSplitThresholdInKB"
        if not enable:
            SystemTweaksService.restore_registry_value_or_default("HKLM", path, value_name, 3670016, winreg.REG_DWORD)
            return
            
        # Get total RAM in GB
        try:
            total_ram_gb = round(psutil.virtual_memory().total / (1024**3))
            
            from core_commander.core.guard import get_decrypted_tweak_payload, _security_tainted, check_apply_optimization_hook
            check_apply_optimization_hook()
            payload = get_decrypted_tweak_payload()
            svc_factor = payload.get("svchost_base", 0)
            if _security_tainted or svc_factor != 1024:
                threshold_kb = 0
            else:
                threshold_kb = total_ram_gb * svc_factor * 1024
                
            logger.info(f"Auto-detected System RAM: {total_ram_gb} GB. Target Split Threshold: {threshold_kb} KB")
            
            SystemTweaksService.backup_registry_value("HKLM", path, value_name)
            
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, value_name, 0, winreg.REG_DWORD, threshold_kb)
            logger.info("Successfully applied RAM svchost threshold optimization.")
        except Exception as e:
            logger.error(f"Failed to apply RAM threshold optimization: {str(e)}")
            raise


    @staticmethod
    def apply_nvme_optimization(enable: bool):
        path = r"SYSTEM\CurrentControlSet\Control\FileSystem"
        
        # Detect if system drive is SSD or HDD
        drive_type = "SSD"  # Default fallback
        sys_drive = os.environ.get("SystemDrive", "C:").upper()
        if not sys_drive.endswith(':'):
            sys_drive += ':'
        sys_drive_letter = sys_drive.strip(':')
        
        if SystemTweaksService._drive_type_cache is not None:
            drive_type = SystemTweaksService._drive_type_cache
        else:
            if HAS_WIN32:
                has_com = False
                wmi_cimv2 = None
                partitions = None
                wmi_storage = None
                physical_disks = None
                try:
                    import pythoncom
                    import win32com.client
                    pythoncom.CoInitialize()
                    has_com = True
                    
                    wmi_cimv2 = win32com.client.GetObject("winmgmts:\\\\.\\root\\cimv2")
                    partitions = wmi_cimv2.ExecQuery(f"ASSOCIATORS OF {{Win32_LogicalDisk.DeviceID='{sys_drive}'}} WHERE AssocClass=Win32_LogicalDiskToPartition")
                    disk_index = None
                    for p in partitions:
                        match = re.search(r"Disk\\s+#(\\d+)", p.DeviceID)
                        if match:
                            disk_index = int(match.group(1))
                            break
                    if disk_index is not None:
                        wmi_storage = win32com.client.GetObject("winmgmts:\\\\.\\Root\\Microsoft\\Windows\\Storage")
                        physical_disks = wmi_storage.ExecQuery(f"SELECT MediaType FROM MSFT_PhysicalDisk WHERE DeviceId='{disk_index}'")  # nosec
                        for d in physical_disks:
                            if d.MediaType == 3:
                                drive_type = "HDD"
                            elif d.MediaType == 4:
                                drive_type = "SSD"
                            break
                except Exception:  # nosec
                    pass
                finally:
                    physical_disks = None
                    wmi_storage = None
                    partitions = None
                    wmi_cimv2 = None
                    if has_com:
                        import gc
                        gc.collect()
                        try:
                            pythoncom.CoUninitialize()
                        except Exception:  # nosec
                            pass
                # If HAS_WIN32 is False, fallback to SSD to bypass powershell overhead
                drive_type = "SSD"
            SystemTweaksService._drive_type_cache = drive_type
            
        if not enable:
            SystemTweaksService.restore_registry_value_or_default("HKLM", path, "NtfsDisableLastAccessUpdate", 2, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path, "NtfsDisable8dot3NameCreation", 2, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path, "DisableDeleteNotify", 0, winreg.REG_DWORD)
            try:
                toggled = False
                if HAS_WIN32:
                    has_com = False
                    scheduler = None
                    folder = None
                    task = None
                    try:
                        import pythoncom
                        import win32com.client
                        pythoncom.CoInitialize()
                        has_com = True
                        scheduler = win32com.client.Dispatch("Schedule.Service")
                        scheduler.Connect()
                        folder = scheduler.GetFolder("\\Microsoft\\Windows\\Defrag")
                        task = folder.GetTask("ScheduledDefrag")
                        task.Enabled = True
                        toggled = True
                        logger.info("ScheduledDefrag enabled successfully via Schedule.Service COM.")
                    except Exception as ex:
                        logger.debug(f"Schedule.Service COM enable task failed: {str(ex)}")
                    finally:
                        task = None
                        folder = None
                        scheduler = None
                        if has_com:
                            import gc
                            gc.collect()
                            try:
                                pythoncom.CoUninitialize()
                            except Exception:  # nosec
                                pass
                if not toggled:
                    SystemTweaksService.safe_subprocess_call(["schtasks", "/change", "/tn", "\\Microsoft\\Windows\\Defrag\\ScheduledDefrag", "/enable"], timeout=5)
            except Exception:  # nosec
                pass
            return
            
        try:
            logger.info(f"Applying drive optimizations. Detected drive type: {drive_type}")
            
            # Global NTFS filesystem optimization (applied to all drives)
            SystemTweaksService.backup_registry_value("HKLM", path, "NtfsDisableLastAccessUpdate")
            SystemTweaksService.backup_registry_value("HKLM", path, "NtfsDisable8dot3NameCreation")
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "NtfsDisableLastAccessUpdate", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(key, "NtfsDisable8dot3NameCreation", 0, winreg.REG_DWORD, 1)
            except Exception as e:
                logger.error(f"Failed to write NTFS registry optimizations: {str(e)}")
                raise
            
            # SSD specific filesystem tweaks
            if drive_type == "SSD":
                # Ensure TRIM is enabled (DisableDeleteNotify = 0)
                SystemTweaksService.backup_registry_value("HKLM", path, "DisableDeleteNotify")
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_WRITE) as key:
                        winreg.SetValueEx(key, "DisableDeleteNotify", 0, winreg.REG_DWORD, 0)
                except Exception as e:
                    logger.error(f"Failed to write DisableDeleteNotify: {str(e)}")
                    raise
                
                # Disable defragmentation service scheduling for SSD to protect drive lifespan
                toggled = False
                if HAS_WIN32:
                    has_com = False
                    scheduler = None
                    folder = None
                    task = None
                    try:
                        import pythoncom
                        import win32com.client
                        pythoncom.CoInitialize()
                        has_com = True
                        scheduler = win32com.client.Dispatch("Schedule.Service")
                        scheduler.Connect()
                        folder = scheduler.GetFolder("\\Microsoft\\Windows\\Defrag")
                        task = folder.GetTask("ScheduledDefrag")
                        task.Enabled = False
                        toggled = True
                        logger.info("SSD ScheduledDefrag disabled successfully via Schedule.Service COM.")
                    except Exception as ex:
                        logger.debug(f"Schedule.Service COM disable task failed: {str(ex)}")
                    finally:
                        task = None
                        folder = None
                        scheduler = None
                        if has_com:
                            import gc
                            gc.collect()
                            try:
                                pythoncom.CoUninitialize()
                            except Exception:  # nosec
                                pass
                if not toggled:
                    SystemTweaksService.safe_subprocess_call(["schtasks", "/change", "/tn", "\\Microsoft\\Windows\\Defrag\\ScheduledDefrag", "/disable"], timeout=5)
                logger.info("SSD TRIM and NTFS short name optimization applied. Defrag scheduling disabled.")
            else:
                # HDD tweaks - Keep defragmentation enabled, and turn off TRIM (not supported)
                # Enable defragmentation schedule for HDD to keep it fast
                toggled = False
                if HAS_WIN32:
                    has_com = False
                    scheduler = None
                    folder = None
                    task = None
                    try:
                        import pythoncom
                        import win32com.client
                        pythoncom.CoInitialize()
                        has_com = True
                        scheduler = win32com.client.Dispatch("Schedule.Service")
                        scheduler.Connect()
                        folder = scheduler.GetFolder("\\Microsoft\\Windows\\Defrag")
                        task = folder.GetTask("ScheduledDefrag")
                        task.Enabled = True
                        toggled = True
                        logger.info("HDD ScheduledDefrag enabled successfully via Schedule.Service COM.")
                    except Exception as ex:
                        logger.debug(f"Schedule.Service COM enable task failed: {str(ex)}")
                    finally:
                        task = None
                        folder = None
                        scheduler = None
                        if has_com:
                            import gc
                            gc.collect()
                            try:
                                pythoncom.CoUninitialize()
                            except Exception:  # nosec
                                pass
                if not toggled:
                    SystemTweaksService.safe_subprocess_call(["schtasks", "/change", "/tn", "\\Microsoft\\Windows\\Defrag\\ScheduledDefrag", "/enable"], timeout=5)
                logger.info("HDD NTFS optimizations applied. Scheduled defragmentation kept enabled.")
        except Exception as e:
            logger.error(f"Failed to apply drive optimizations: {str(e)}")
            raise


    @staticmethod
    def _toggle_nvidia_telemetry_files(rename_to_old: bool):
        # Disabled recursive glob to protect disk I/O and prevent driver signature issues.
        # NVIDIA telemetry is already completely disabled by NvTelemetryContainer service and registry entries.
        logger.debug("Skipping NvTelemetry DLL renaming to protect system disk I/O and driver integrity.")
        return

    @staticmethod
    def apply_gpu_tweaks(enable: bool, gpu_vendor: str):
        amd_services = [
            ("AMD Crash Defender Service", 2),
            ("AMD External Events Utility", 2),
            ("amdfendr", 2),
            ("amdfendrmgr", 2),
            ("amdlog", 2)
        ]
        
        amd_direct_keys = {
            "DisableDMACopy": (1, winreg.REG_DWORD),
            "DisableBlockWrite": (0, winreg.REG_DWORD),
            "PP_ThermalAutoThrottlingEnable": (0, winreg.REG_DWORD),
            "DisableDrmdmaPowerGating": (1, winreg.REG_DWORD),
            "EnableUlps": (0, winreg.REG_DWORD)
        }
        
        amd_umd_keys = {
            "AppGpuId": (bytes.fromhex("300078003000310030003000"), winreg.REG_BINARY),
            "SwapEffect": (bytes.fromhex("30000000"), winreg.REG_BINARY),
            "PowerState": (bytes.fromhex("3000"), winreg.REG_BINARY),
            "AntiStuttering": (bytes.fromhex("3100"), winreg.REG_BINARY),
            "TurboSync": (bytes.fromhex("3000"), winreg.REG_BINARY),
            "SurfaceFormatReplacements": (bytes.fromhex("3100"), winreg.REG_BINARY),
            "EQAA": (bytes.fromhex("3000"), winreg.REG_BINARY),
            "ShaderCache": (bytes.fromhex("3100"), winreg.REG_BINARY),
            "MLF": (bytes.fromhex("3000"), winreg.REG_BINARY),
            "TruformMode_NA": (bytes.fromhex("3100"), winreg.REG_BINARY),
            "Main3D": (bytes.fromhex("3100"), winreg.REG_BINARY),
            "Main3D_DEF": ("1", winreg.REG_SZ)
        }
        
        amd_dxva_keys = {
            "LRTCEnable": (bytes.fromhex("30000000"), winreg.REG_BINARY),
            "3to2Pulldown": (bytes.fromhex("31000000"), winreg.REG_BINARY),
            "MosquitoNoiseRemoval_ENABLE": (bytes.fromhex("30000000"), winreg.REG_BINARY),
            "MosquitoNoiseRemoval": (bytes.fromhex("350030000000"), winreg.REG_BINARY),
            "Deblocking_ENABLE": (bytes.fromhex("30000000"), winreg.REG_BINARY),
            "Deblocking": (bytes.fromhex("350030000000"), winreg.REG_BINARY),
            "DemoMode": (bytes.fromhex("30000000"), winreg.REG_BINARY),
            "OverridePA": (bytes.fromhex("30000000"), winreg.REG_BINARY),
            "DynamicRange": (bytes.fromhex("30000000"), winreg.REG_BINARY),
            "StaticGamma_ENABLE": (bytes.fromhex("30000000"), winreg.REG_BINARY),
            "BlueStretch_ENABLE": (bytes.fromhex("31000000"), winreg.REG_BINARY),
            "BlueStretch": (bytes.fromhex("31000000"), winreg.REG_BINARY),
            "LRTCCoef": (bytes.fromhex("3100300030000000"), winreg.REG_BINARY),
            "DynamicContrast_ENABLE": (bytes.fromhex("30000000"), winreg.REG_BINARY),
            "WhiteBalanceCorrection": (bytes.fromhex("30000000"), winreg.REG_BINARY),
            "Fleshtone_ENABLE": (bytes.fromhex("30000000"), winreg.REG_BINARY),
            "Fleshtone": (bytes.fromhex("350030000000"), winreg.REG_BINARY),
            "ColorVibrance_ENABLE": (bytes.fromhex("31000000"), winreg.REG_BINARY),
            "ColorVibrance": (bytes.fromhex("340030000000"), winreg.REG_BINARY),
            "Detail_ENABLE": (bytes.fromhex("30000000"), winreg.REG_BINARY),
            "Detail": (bytes.fromhex("310030000000"), winreg.REG_BINARY),
            "Denoise_ENABLE": (bytes.fromhex("30000000"), winreg.REG_BINARY),
            "Denoise": (bytes.fromhex("360034000000"), winreg.REG_BINARY),
            "TrueWhite": (bytes.fromhex("30000000"), winreg.REG_BINARY),
            "OvlTheaterMode": (bytes.fromhex("30000000"), winreg.REG_BINARY),
            "StaticGamma": (bytes.fromhex("3100300030000000"), winreg.REG_BINARY),
            "InternetVideo": (bytes.fromhex("30000000"), winreg.REG_BINARY)
        }
        
        gpu_class_path = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
        nv_telemetry_keys = [
            ("HKLM", r"SOFTWARE\NVIDIA Corporation\NvControlPanel2\Client", "OptInOrOutPreference", 0, winreg.REG_DWORD, 1),
            ("HKLM", r"SOFTWARE\NVIDIA Corporation\Global\FTS", "EnableRID44231", 0, winreg.REG_DWORD, None),
            ("HKLM", r"SOFTWARE\NVIDIA Corporation\Global\FTS", "EnableRID64640", 0, winreg.REG_DWORD, None),
            ("HKLM", r"SOFTWARE\NVIDIA Corporation\Global\FTS", "EnableRID66610", 0, winreg.REG_DWORD, None),
            ("HKLM", r"SYSTEM\CurrentControlSet\Services\nvlddmkm\Global\Startup", "SendTelemetryData", 0, winreg.REG_DWORD, None)
        ]
        
        if not enable:
            if gpu_vendor == "AMD":
                amd_path = r"Software\AMD\CN"
                SystemTweaksService.restore_registry_value_or_default("HKCU", amd_path, "AutoUpdateTriggered", 1, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKCU", amd_path, "PowerSaverAutoEnable_CUR", 1, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKCU", amd_path, "AutoUpdate", 1, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", r"System\CurrentControlSet\Services\amdwddmg", "ChillEnabled", 1, winreg.REG_DWORD)
                
                for svc, start in amd_services:
                    SystemTweaksService.restore_service_or_default(svc, start)
                
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, gpu_class_path, 0, winreg.KEY_READ) as root_key:
                        i = 0
                        while True:
                            try:
                                sub = winreg.EnumKey(root_key, i)
                                if sub.isdigit():
                                    sub_path = f"{gpu_class_path}\\{sub}"
                                    for key_name in amd_direct_keys.keys():
                                        default_val = 1 if key_name == "EnableUlps" else None
                                        SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, key_name, default_val, winreg.REG_DWORD)
                                        
                                    umd_path = f"{sub_path}\\UMD"
                                    for key_name, (_, reg_type) in amd_umd_keys.items():
                                        SystemTweaksService.restore_registry_value_or_default("HKLM", umd_path, key_name, None, reg_type)
                                        
                                    dxva_path = f"{sub_path}\\UMD\\DXVA"
                                    for key_name, (_, reg_type) in amd_dxva_keys.items():
                                        SystemTweaksService.restore_registry_value_or_default("HKLM", dxva_path, key_name, None, reg_type)
                                i += 1
                            except OSError:
                                break
                except Exception:  # nosec
                    pass
                logger.info("Restored AMD GPU driver parameters and telemetry services.")
                
            elif gpu_vendor == "NVIDIA":
                SystemTweaksService.restore_service_or_default("NvTelemetryContainer", SERVICE_AUTO_START)
                
                for hkey_name, subkey, name, _, reg_type, default_val in nv_telemetry_keys:
                    SystemTweaksService.restore_registry_value_or_default(hkey_name, subkey, name, default_val, reg_type)
                
                SystemTweaksService._toggle_nvidia_telemetry_files(False)
                logger.info("Restored NVIDIA GPU telemetry services, registry keys, and driver files.")
            return
            
        if gpu_vendor == "AMD":
            try:
                logger.info("Applying AMD Graphic Card driver tweaks...")
                amd_path = r"Software\AMD\CN"
                SystemTweaksService.backup_registry_value("HKCU", amd_path, "AutoUpdateTriggered")
                SystemTweaksService.backup_registry_value("HKCU", amd_path, "PowerSaverAutoEnable_CUR")
                SystemTweaksService.backup_registry_value("HKCU", amd_path, "AutoUpdate")
                
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, amd_path, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "AutoUpdateTriggered", 0, winreg.REG_DWORD, 0)
                    winreg.SetValueEx(key, "PowerSaverAutoEnable_CUR", 0, winreg.REG_DWORD, 0)
                    winreg.SetValueEx(key, "AutoUpdate", 0, winreg.REG_DWORD, 0)
                
                amddrv_path = r"System\CurrentControlSet\Services\amdwddmg"
                SystemTweaksService.backup_registry_value("HKLM", amddrv_path, "ChillEnabled")
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, amddrv_path, 0, winreg.KEY_WRITE) as k:
                        winreg.SetValueEx(k, "ChillEnabled", 0, winreg.REG_DWORD, 0)
                except FileNotFoundError:
                    pass
                
                for svc, _ in amd_services:
                    SystemTweaksService.backup_service(svc)
                    SystemTweaksService.set_service_start_type(svc, SERVICE_DISABLED)
                
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, gpu_class_path, 0, winreg.KEY_READ) as root_key:
                        i = 0
                        while True:
                            try:
                                sub = winreg.EnumKey(root_key, i)
                                if sub.isdigit():
                                    sub_path = f"{gpu_class_path}\\{sub}"
                                    for key_name, (val, reg_type) in amd_direct_keys.items():
                                        SystemTweaksService.backup_registry_value("HKLM", sub_path, key_name)
                                        try:
                                            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub_path, 0, winreg.KEY_WRITE) as k:
                                                winreg.SetValueEx(k, key_name, 0, reg_type, val)
                                        except Exception:  # nosec
                                            pass
                                            
                                    umd_path = f"{sub_path}\\UMD"
                                    for key_name, (val, reg_type) in amd_umd_keys.items():
                                        SystemTweaksService.backup_registry_value("HKLM", umd_path, key_name)
                                        try:
                                            with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, umd_path, 0, winreg.KEY_WRITE) as k:
                                                winreg.SetValueEx(k, key_name, 0, reg_type, val)
                                        except Exception:  # nosec
                                            pass
                                            
                                    dxva_path = f"{sub_path}\\UMD\\DXVA"
                                    for key_name, (val, reg_type) in amd_dxva_keys.items():
                                        SystemTweaksService.backup_registry_value("HKLM", dxva_path, key_name)
                                        try:
                                            with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, dxva_path, 0, winreg.KEY_WRITE) as k:
                                                winreg.SetValueEx(k, key_name, 0, reg_type, val)
                                        except Exception:  # nosec
                                            pass
                                i += 1
                            except OSError:
                                break
                except Exception as e:
                    logger.debug(f"Failed to apply AMD Driver class optimizations: {str(e)}")
                
                logger.info("AMD Graphic Card optimizations completed.")
            except Exception as e:
                logger.error(f"Failed to apply AMD GPU tweaks: {str(e)}")
                raise
                
        elif gpu_vendor == "NVIDIA":
            try:
                logger.info("Applying NVIDIA Telemetry and background services cleanups...")
                SystemTweaksService.backup_service("NvTelemetryContainer")
                SystemTweaksService.set_service_start_type("NvTelemetryContainer", SERVICE_DISABLED)
                
                for hkey_name, subkey, name, val, reg_type, _ in nv_telemetry_keys:
                    SystemTweaksService.backup_registry_value(hkey_name, subkey, name)
                    hkey_root = winreg.HKEY_LOCAL_MACHINE if hkey_name == "HKLM" else winreg.HKEY_CURRENT_USER
                    try:
                        with winreg.CreateKeyEx(hkey_root, subkey, 0, winreg.KEY_WRITE) as key:
                            winreg.SetValueEx(key, name, 0, reg_type, val)
                    except Exception as e:
                        logger.debug(f"Failed to write NVIDIA registry telemetry key {subkey}\\{name}: {str(e)}")
                
                SystemTweaksService._toggle_nvidia_telemetry_files(True)
                logger.info("NVIDIA background telemetry container service and telemetry registry options disabled.")
            except Exception as e:
                logger.error(f"Failed to apply NVIDIA GPU tweaks: {str(e)}")
                raise

    @staticmethod
    def apply_nvidia_nip(enable: bool) -> bool:
        """
        Applies or skips NVIDIA Profile Inspector high performance profile preset.
        """
        if enable:
            logger.info("Attempting programmatic NVIDIA DRS profile overrides...")
            if NvidiaDrsService.apply_gaming_drs_profile(True):
                logger.info("Programmatic NVIDIA DRS profile applied successfully.")
                return True
            logger.warning("Programmatic DRS overrides failed or unavailable. Falling back to Profile Inspector import.")
            return SystemTweaksService.import_nvidia_nip_profile("吨の调 体感延迟低不影响帧数版.nip")
        else:
            logger.info("Reverting NVIDIA DRS profile overrides...")
            return NvidiaDrsService.apply_gaming_drs_profile(False)

    @staticmethod
    def import_nvidia_nip_profile(nip_filename: str) -> bool:
        if not nip_filename:
            return False
        logger.info(f"Importing NVIDIA profile: {nip_filename}...")
        try:
            inspector_name = "nvidiaProfileInspector.exe"
            res_inspector_path = SystemTweaksService.get_resource_path(os.path.join("core_commander", "resources", "nip", inspector_name))
            res_nip_path = SystemTweaksService.get_resource_path(os.path.join("core_commander", "resources", "nip", nip_filename))
            
            if not os.path.exists(res_inspector_path) or not os.path.exists(res_nip_path):
                logger.error(f"Resource files for NIP profile import not found!")
                return False
                
            cmd = [res_inspector_path, "-import", res_nip_path]
            logger.info(f"Executing NIP Profile Inspector: {' '.join(cmd)}")
            res = SystemTweaksService.safe_subprocess_call(cmd, timeout=10, cwd=os.path.dirname(res_inspector_path))
            
            if res == 0:
                logger.info(f"Successfully imported NVIDIA profile {nip_filename}.")
                return True
            else:
                logger.error(f"NVIDIA ProfileInspector returned non-zero code: {res}")
                return False
        except Exception as e:
            logger.error(f"Failed to import NVIDIA Profile: {str(e)}")
            return False

    @staticmethod
    def run_nvidia_profile_inspector() -> bool:
        try:
            inspector_name = "nvidiaProfileInspector.exe"
            res_inspector_path = SystemTweaksService.get_resource_path(os.path.join("core_commander", "resources", "nip", inspector_name))
            if not os.path.exists(res_inspector_path):
                return False
                
            import subprocess
            subprocess.Popen([res_inspector_path], cwd=os.path.dirname(res_inspector_path))
            return True
        except Exception as e:
            logger.error(f"Failed to run ProfileInspector: {str(e)}")
            return False


    @staticmethod
    def apply_qos_policy(game_exe_path: str) -> bool:
        """
        QoS settings to prioritize game execution.
        """
        if not game_exe_path:
            return False
            
        exe_name = os.path.basename(game_exe_path)
        logger.info(f"Setting DSCP 46 QoS traffic prioritization policy for game: {exe_name}")
        
        qos_base_path = r"Software\Policies\Microsoft\Windows\QoS"
        policy_path = f"{qos_base_path}\\{exe_name}"
        
        try:
            # We don't backup QoS policy since it is custom per app and easy to delete. We can delete on stop.
            try:
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, policy_path, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "Application Name", 0, winreg.REG_SZ, exe_name)
                    winreg.SetValueEx(key, "Version", 0, winreg.REG_SZ, "1.0")
                    winreg.SetValueEx(key, "Protocol", 0, winreg.REG_SZ, "*")
                    winreg.SetValueEx(key, "Local Port", 0, winreg.REG_SZ, "*")
                    winreg.SetValueEx(key, "Local IP", 0, winreg.REG_SZ, "*")
                    winreg.SetValueEx(key, "Local IP Prefix Length", 0, winreg.REG_SZ, "*")
                    winreg.SetValueEx(key, "Remote Port", 0, winreg.REG_SZ, "*")
                    winreg.SetValueEx(key, "Remote IP", 0, winreg.REG_SZ, "*")
                    winreg.SetValueEx(key, "Remote IP Prefix Length", 0, winreg.REG_SZ, "*")
                    winreg.SetValueEx(key, "DSCP Value", 0, winreg.REG_SZ, "46")
                    winreg.SetValueEx(key, "Throttle Rate", 0, winreg.REG_SZ, "-1")
            except (PermissionError, OSError) as e:
                logger.warning(f"GPO conflict or permission error writing QoS policy key: {str(e)}. Skipping QoS policy tweak.")
                return False
            
            # Apply AppCompat layer flags for DisableDxMaximizedWindowedMode
            compat_path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"
            try:
                SystemTweaksService.backup_registry_value("HKCU", compat_path, game_exe_path)
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, compat_path, 0, winreg.KEY_WRITE) as k:
                    winreg.SetValueEx(k, game_exe_path, 0, winreg.REG_SZ, "~ DISABLEDXMAXIMIZEDWINDOWEDMODE HIGHDPIAWARE")
            except (PermissionError, OSError) as e:
                logger.warning(f"GPO conflict or permission error writing AppCompat layers key: {str(e)}. Skipping AppCompat tweak.")
                return False
            
            # QoS policy is applied via Registry. Direct powershell call is redundant.
            logger.info("QoS Policy registry keys written.")
            return True
        except (PermissionError, OSError) as e:
            logger.warning(f"GPO conflict or permission error in apply_qos_policy: {str(e)}. Skipping QoS policy tweak.")
            return False
        except Exception as e:
            logger.error(f"Failed to apply QoS Policy: {str(e)}")
            raise

    @staticmethod
    def remove_qos_policy(game_exe_path: str):
        if not game_exe_path:
            return
            
        exe_name = os.path.basename(game_exe_path)
        policy_path = f"Software\Policies\Microsoft\Windows\QoS\\{exe_name}"
        try:
            winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, policy_path)
            logger.info(f"Removed QoS Policy for {exe_name}.")
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.debug(f"Failed to delete QoS key: {str(e)}")
            
        # QoS policy removal done via Registry. Direct powershell call is redundant.
        pass

        # Revert AppCompat layer flag if we have the full path
        if "\\" in game_exe_path or "/" in game_exe_path:
            try:
                compat_path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"
                SystemTweaksService.restore_registry_value_or_default("HKCU", compat_path, game_exe_path, None, winreg.REG_SZ)
                logger.info(f"Restored AppCompat layer flag for {game_exe_path}.")
            except Exception as e:
                logger.debug(f"Failed to restore AppCompat layer flag for {game_exe_path}: {str(e)}")

    @staticmethod
    def apply_spectre_meltdown_mitigation(disable: bool):
        path_mm = r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management"
        if not disable:
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_mm, "FeatureSettings", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_mm, "FeatureSettingsOverride", 0, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_mm, "FeatureSettingsOverrideMask", 3, winreg.REG_DWORD)
            return
            
        logger.info("Disabling Meltdown & Spectre CPU mitigations for performance...")
        SystemTweaksService.backup_registry_value("HKLM", path_mm, "FeatureSettings")
        SystemTweaksService.backup_registry_value("HKLM", path_mm, "FeatureSettingsOverride")
        SystemTweaksService.backup_registry_value("HKLM", path_mm, "FeatureSettingsOverrideMask")
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_mm, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "FeatureSettings", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "FeatureSettingsOverride", 0, winreg.REG_DWORD, 3)
                winreg.SetValueEx(key, "FeatureSettingsOverrideMask", 0, winreg.REG_DWORD, 3)
            logger.info("CPU vulnerabilities protection disabled successfully.")
        except Exception as e:
            logger.error(f"Failed to disable CPU vulnerabilities: {str(e)}")
            raise


    @staticmethod
    def apply_gpu_preemption(disable: bool):
        path = r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers\Scheduler"
        if not disable:
            SystemTweaksService.restore_registry_value_or_default("HKLM", path, "EnablePreemption", 1, winreg.REG_DWORD)
            return
        logger.info("Disabling GPU Preemption (EnablePreemption = 0)...")
        SystemTweaksService.backup_registry_value("HKLM", path, "EnablePreemption")
        try:
            with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "EnablePreemption", 0, winreg.REG_DWORD, 0)
            logger.info("GPU preemption disabled.")
        except Exception as e:
            logger.error(f"Failed to disable GPU preemption: {str(e)}")
            raise


    @staticmethod
    def apply_gamedvr_tweak(disable: bool):
        if not disable:
            path1 = r"System\GameConfigStore"
            SystemTweaksService.restore_registry_value_or_default("HKCU", path1, "GameDVR_Enabled", 1, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKCU", path1, "GameDVR_FSEBehaviorMode", 2, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKCU", path1, "GameDVR_HonorUserFSEBehaviorMode", 0, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKCU", path1, "GameDVR_DXGIHonorFSEWindowsCompatible", 0, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKCU", path1, "GameDVR_EFSEFeatureFlags", 0, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKCU", path1, "GameDVR_FSEBehavior", 0, winreg.REG_DWORD)
            
            path2 = r"SOFTWARE\Microsoft\PolicyManager\default\ApplicationManagement\AllowGameDVR"
            SystemTweaksService.restore_registry_value_or_default("HKLM", path2, "value", 1, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path2, "MergeConflictOptions", 0, winreg.REG_DWORD)
            
            path3 = r"Software\Microsoft\Windows\CurrentVersion\GameDVR"
            SystemTweaksService.restore_registry_value_or_default("HKCU", path3, "AppCaptureEnabled", 1, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKCU", path3, "HistoricalCaptureEnabled", 0, winreg.REG_DWORD)
            
            path4 = r"SOFTWARE\Policies\Microsoft\Windows\GameDVR"
            SystemTweaksService.restore_registry_value_or_default("HKLM", path4, "AllowGameDVR", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path4, "AllowAudioCapture", None, winreg.REG_DWORD)
            return
            
        logger.info("Disabling GameDVR and App Capture...")
        path1 = r"System\GameConfigStore"
        for v in ["GameDVR_Enabled", "GameDVR_FSEBehaviorMode", "GameDVR_HonorUserFSEBehaviorMode", 
                  "GameDVR_DXGIHonorFSEWindowsCompatible", "GameDVR_EFSEFeatureFlags", "GameDVR_FSEBehavior"]:
            SystemTweaksService.backup_registry_value("HKCU", path1, v)
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path1, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "GameDVR_Enabled", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "GameDVR_FSEBehaviorMode", 0, winreg.REG_DWORD, 3)
                winreg.SetValueEx(key, "GameDVR_HonorUserFSEBehaviorMode", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "GameDVR_DXGIHonorFSEWindowsCompatible", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "GameDVR_EFSEFeatureFlags", 0, winreg.REG_DWORD, 3)
                winreg.SetValueEx(key, "GameDVR_FSEBehavior", 0, winreg.REG_DWORD, 2)
        except Exception as e:
            logger.debug(f"GameConfigStore write failed: {str(e)}")

        path2 = r"SOFTWARE\Microsoft\PolicyManager\default\ApplicationManagement\AllowGameDVR"
        SystemTweaksService.backup_registry_value("HKLM", path2, "value")
        SystemTweaksService.backup_registry_value("HKLM", path2, "MergeConflictOptions")
        try:
            with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path2, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "value", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "MergeConflictOptions", 0, winreg.REG_DWORD, 1)
        except Exception as e:
            logger.debug(f"AllowGameDVR Policy write failed: {str(e)}")

        path3 = r"Software\Microsoft\Windows\CurrentVersion\GameDVR"
        SystemTweaksService.backup_registry_value("HKCU", path3, "AppCaptureEnabled")
        SystemTweaksService.backup_registry_value("HKCU", path3, "HistoricalCaptureEnabled")
        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path3, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "AppCaptureEnabled", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "HistoricalCaptureEnabled", 0, winreg.REG_DWORD, 0)
        except Exception as e:
            logger.debug(f"GameDVR HKCU write failed: {str(e)}")

        path4 = r"SOFTWARE\Policies\Microsoft\Windows\GameDVR"
        SystemTweaksService.backup_registry_value("HKLM", path4, "AllowGameDVR")
        SystemTweaksService.backup_registry_value("HKLM", path4, "AllowAudioCapture")
        try:
            with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path4, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "AllowGameDVR", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "AllowAudioCapture", 0, winreg.REG_DWORD, 0)
        except Exception as e:
            logger.debug(f"GameDVR HKLM policy write failed: {str(e)}")


    @staticmethod
    def apply_ultimate_network_tweak(enable: bool):
        path_sp = r"SYSTEM\CurrentControlSet\Services\Tcpip\ServiceProvider"
        path_task = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile\Tasks\Games"
        path_sr = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"
        path_tcp = r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters"
        path_ndis = r"SYSTEM\CurrentControlSet\Services\NDIS\Parameters"
        path_lm = r"SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters"
        path_mm = r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management"
        path_class = r"SYSTEM\CurrentControlSet\Control\Class\{4D36E972-E325-11CE-BFC1-08002BE10318}"
        path_afd = r"SYSTEM\CurrentControlSet\Services\Afd\Parameters"
        path_sysres = r"SYSTEM\CurrentControlSet\Control\SystemResponsiveness"
        
        afd_keys = [
            "DynamicSendBufferDisable", "FastSendDatagramThreshold", "DefaultSendWindow", "DefaultReceiveWindow",
            "MaxFastTransmit", "MaxFastCopyTransmit", "FastCopyReceiveThreshold", "PriorityBoost",
            "EnableDynamicBacklog", "MinimumDynamicBacklog", "MaximumDynamicBacklog", "DynamicBacklogGrowthDelta",
            "SendWindowSize", "ReceiveWindowSize", "ReceivePostsLowWater", "ReceivePostsHighWater",
            "LargeBufferSize", "MediumBufferSize"
        ]
        
        lm_keys = [
            "MaxThreadsPerQueue", "MaxCmds", "MaxFreeConnections", "MinFreeConnections", "MaxWorkItems",
            "MaxRawWorkItems", "MaxFreeWorkItems", "MaxMpxCt", "Smb2CreditsMin", "Smb2CreditsMax",
            "DisableBandwidthThrottling", "MaxSessionTableSize", "EnableOplocks", "MaxPagedMemoryUsage",
            "MaxNonPagedMemoryUsage", "EnableLargeBufferTransfers", "IdleThreadTimeout", "AutoShareServer",
            "DisableLargeMtu"
        ]
        
        mm_keys = [
            "LargeSystemCache", "IOPageLockLimit", "DisablePagingExecutive", "SecondLevelDataCache",
            "ClearPageFileAtShutdown", "LargePageMinimum", "PoolUsageMaximum"
        ]
        
        sysres_keys = [
            "SystemResponsiveness", "NetworkThrottlingIndex", "Background Only Services",
            "TimeCriticalPriority", "LowLatencyPriority", "AllowSchedulerOverride", "DisableDynamicticks"
        ]
        
        sr_keys = [
            "SystemResponsiveness", "NetworkThrottlingIndex", "AlwaysOn", "NoLazyMode", "LazyModeTimeout", "ExecuteQueueBoost"
        ]
        
        if not enable:
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_sp, "Class", 8, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_sp, "DnsPriority", 2000, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_sp, "HostsPriority", 500, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_sp, "LocalPriority", 499, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_sp, "NetbtPriority", 2001, winreg.REG_DWORD)
            
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_task, "GPU Priority", 8, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_task, "Priority", 2, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_task, "Scheduling Category", "Interactive", winreg.REG_SZ)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_task, "SFIO Priority", "Normal", winreg.REG_SZ)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_task, "Latency Sensitive", "True", winreg.REG_SZ)
            
            for v in sr_keys:
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_sr, v, None if v not in ("SystemResponsiveness", "NetworkThrottlingIndex") else (20 if v == "SystemResponsiveness" else 10), winreg.REG_DWORD)
            
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_tcp, "TcpNoDelay", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_tcp, "TcpAckFrequency", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_tcp, "TcpDelAckTicks", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_tcp, "TCPWindowSize", None, winreg.REG_DWORD)
            
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces", 0, winreg.KEY_READ) as root_key:
                    i = 0
                    while True:
                        try:
                            sub = winreg.EnumKey(root_key, i)
                            sub_path = f"SYSTEM\\CurrentControlSet\\Services\\Tcpip\\Parameters\\Interfaces\\{sub}"
                            SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, "TcpNoDelay", None, winreg.REG_DWORD)
                            SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, "TcpAckFrequency", None, winreg.REG_DWORD)
                            SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, "TcpDelAckTicks", None, winreg.REG_DWORD)
                            SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, "TCPWindowSize", None, winreg.REG_DWORD)
                            i += 1
                        except OSError:
                            break
            except Exception:  # nosec
                pass
                
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_ndis, "MaxNumRssCpus", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_ndis, "RssBaseCpu", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_ndis, "RssMaxProcNumber", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_ndis, "EnableRSS", 1, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_ndis, "MaxNumRssQueues", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_ndis, "RssAlgorithm", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_ndis, "EnableTCPChimney", 0, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_ndis, "EnableTCPTaskOffload", 1, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_ndis, "EnableIPsecTaskOffload", 1, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_ndis, "EnableLsoV2IPv4", 1, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_ndis, "EnableLsoV2IPv6", 1, winreg.REG_DWORD)
            
            for v in lm_keys:
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_lm, v, None, winreg.REG_DWORD)
                
            for v in mm_keys:
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_mm, v, 0 if v == "LargeSystemCache" else None, winreg.REG_DWORD)
                
            for v in afd_keys:
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_afd, v, None, winreg.REG_DWORD)
                
            # Removed redundant restoration of path_sysres to prevent registry pollution
            pass
            
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_class, 0, winreg.KEY_READ) as root_key:
                    i = 0
                    while True:
                        try:
                            sub = winreg.EnumKey(root_key, i)
                            if sub.isdigit():
                                sub_path = f"{path_class}\\{sub}"
                                for v in ["*FlowControl", "*InterruptModeration", "*PriorityVLANTag", "*ReceiveBuffers", 
                                          "*TransmitBuffers", "*EEE", "*WakeOnMagicPacket", "*WakeOnPattern", "*RSS", "*NumRssQueues"]:
                                    SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, v, None, winreg.REG_SZ)
                            i += 1
                        except OSError:
                            break
            except Exception:  # nosec
                pass
            return

        logger.info("Applying DNS/Hosts and system responsive profile optimizations...")
        for v in ["Class", "DnsPriority", "HostsPriority", "LocalPriority", "NetbtPriority"]:
            SystemTweaksService.backup_registry_value("HKLM", path_sp, v)
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_sp, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "Class", 0, winreg.REG_DWORD, 8)
                winreg.SetValueEx(key, "DnsPriority", 0, winreg.REG_DWORD, 6)
                winreg.SetValueEx(key, "HostsPriority", 0, winreg.REG_DWORD, 5)
                winreg.SetValueEx(key, "LocalPriority", 0, winreg.REG_DWORD, 4)
                winreg.SetValueEx(key, "NetbtPriority", 0, winreg.REG_DWORD, 7)
        except Exception as e:
            logger.debug(f"Tcpip service provider write failed: {str(e)}")

        for v in ["GPU Priority", "Priority", "Scheduling Category", "SFIO Priority", "Latency Sensitive"]:
            SystemTweaksService.backup_registry_value("HKLM", path_task, v)
        try:
            with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_task, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "GPU Priority", 0, winreg.REG_DWORD, 8)
                winreg.SetValueEx(key, "Priority", 0, winreg.REG_DWORD, 6)
                winreg.SetValueEx(key, "Scheduling Category", 0, winreg.REG_SZ, "High")
                winreg.SetValueEx(key, "SFIO Priority", 0, winreg.REG_SZ, "High")
                winreg.SetValueEx(key, "Latency Sensitive", 0, winreg.REG_SZ, "True")
        except Exception as e:
            logger.debug(f"Multimedia Games task write failed: {str(e)}")

        for v in sr_keys:
            SystemTweaksService.backup_registry_value("HKLM", path_sr, v)
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_sr, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "SystemResponsiveness", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "NetworkThrottlingIndex", 0, winreg.REG_DWORD, 0xffffffff)
                winreg.SetValueEx(key, "AlwaysOn", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "NoLazyMode", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "LazyModeTimeout", 0, winreg.REG_DWORD, 0xffffffff)
                winreg.SetValueEx(key, "ExecuteQueueBoost", 0, winreg.REG_DWORD, 0xffffffff)
        except Exception as e:
            logger.debug(f"SystemProfile response write failed: {str(e)}")

        from core_commander.core.guard import get_decrypted_tweak_payload, _security_tainted, check_apply_optimization_hook
        check_apply_optimization_hook()
        payload = get_decrypted_tweak_payload()
        tcp_nodelay = payload.get("tcp_nodelay", 0)
        tcp_ack = payload.get("tcp_ack_frequency", 9999)
        
        if _security_tainted or tcp_nodelay != 1 or tcp_ack != 1:
            tcp_nodelay_val = 0
            tcp_ack_val = 9999
            tcp_window_val = 0
        else:
            tcp_nodelay_val = 1
            tcp_ack_val = 1
            tcp_window_val = 0x40000

        path_tcp = r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters"
        for v in ["TcpNoDelay", "TcpAckFrequency", "TcpDelAckTicks", "TCPWindowSize"]:
            SystemTweaksService.backup_registry_value("HKLM", path_tcp, v)
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_tcp, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "TcpNoDelay", 0, winreg.REG_DWORD, tcp_nodelay_val)
                winreg.SetValueEx(key, "TcpAckFrequency", 0, winreg.REG_DWORD, tcp_ack_val)
                winreg.SetValueEx(key, "TcpDelAckTicks", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "TCPWindowSize", 0, winreg.REG_DWORD, tcp_window_val)
        except Exception as e:
            logger.debug(f"Tcpip parameters write failed: {str(e)}")

        path_intf = r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces"
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_intf, 0, winreg.KEY_READ) as root_key:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(root_key, i)
                        sub_path = f"{path_intf}\\{sub}"
                        for v in ["TcpNoDelay", "TcpAckFrequency", "TcpDelAckTicks", "TCPWindowSize"]:
                            SystemTweaksService.backup_registry_value("HKLM", sub_path, v)
                        try:
                            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub_path, 0, winreg.KEY_WRITE) as k:
                                winreg.SetValueEx(k, "TcpNoDelay", 0, winreg.REG_DWORD, tcp_nodelay_val)
                                winreg.SetValueEx(k, "TcpAckFrequency", 0, winreg.REG_DWORD, tcp_ack_val)
                                winreg.SetValueEx(k, "TcpDelAckTicks", 0, winreg.REG_DWORD, 0)
                                winreg.SetValueEx(k, "TCPWindowSize", 0, winreg.REG_DWORD, tcp_window_val)
                        except Exception:  # nosec
                            pass
                        i += 1
                    except OSError:
                        break
        except Exception as e:
            logger.debug(f"Tcpip interfaces write failed: {str(e)}")

        path_ndis = r"SYSTEM\CurrentControlSet\Services\NDIS\Parameters"
        for v in ["MaxNumRssCpus", "RssBaseCpu", "RssMaxProcNumber", "EnableRSS", "MaxNumRssQueues", 
                  "RssAlgorithm", "EnableTCPChimney", "EnableTCPTaskOffload", "EnableIPsecTaskOffload", 
                  "EnableLsoV2IPv4", "EnableLsoV2IPv6"]:
            SystemTweaksService.backup_registry_value("HKLM", path_ndis, v)
        try:
            with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_ndis, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "MaxNumRssCpus", 0, winreg.REG_DWORD, 0x20)
                winreg.SetValueEx(key, "RssBaseCpu", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "RssMaxProcNumber", 0, winreg.REG_DWORD, 0x3f)
                winreg.SetValueEx(key, "EnableRSS", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "MaxNumRssQueues", 0, winreg.REG_DWORD, 16)
                winreg.SetValueEx(key, "RssAlgorithm", 0, winreg.REG_DWORD, 2)
                winreg.SetValueEx(key, "EnableTCPChimney", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "EnableTCPTaskOffload", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "EnableIPsecTaskOffload", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "EnableLsoV2IPv4", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "EnableLsoV2IPv6", 0, winreg.REG_DWORD, 1)
        except Exception as e:
            logger.debug(f"NDIS parameters write failed: {str(e)}")

        path_lm = r"SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters"
        for v in lm_keys:
            SystemTweaksService.backup_registry_value("HKLM", path_lm, v)
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_lm, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "MaxThreadsPerQueue", 0, winreg.REG_DWORD, 0x1000)
                winreg.SetValueEx(key, "MaxCmds", 0, winreg.REG_DWORD, 0x10000)
                winreg.SetValueEx(key, "MaxFreeConnections", 0, winreg.REG_DWORD, 0x1000)
                winreg.SetValueEx(key, "MinFreeConnections", 0, winreg.REG_DWORD, 0x100)
                winreg.SetValueEx(key, "MaxWorkItems", 0, winreg.REG_DWORD, 0x8000)
                winreg.SetValueEx(key, "MaxRawWorkItems", 0, winreg.REG_DWORD, 0x4000)
                winreg.SetValueEx(key, "MaxFreeWorkItems", 0, winreg.REG_DWORD, 0x2000)
                winreg.SetValueEx(key, "MaxMpxCt", 0, winreg.REG_DWORD, 0x800)
                winreg.SetValueEx(key, "Smb2CreditsMin", 0, winreg.REG_DWORD, 0x10000)
                winreg.SetValueEx(key, "Smb2CreditsMax", 0, winreg.REG_DWORD, 0x20000)
                winreg.SetValueEx(key, "DisableBandwidthThrottling", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "MaxSessionTableSize", 0, winreg.REG_DWORD, 0x10000)
                winreg.SetValueEx(key, "EnableOplocks", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "MaxPagedMemoryUsage", 0, winreg.REG_DWORD, 0x0FFFFFFF)
                winreg.SetValueEx(key, "MaxNonPagedMemoryUsage", 0, winreg.REG_DWORD, 0x0FFFFFFF)
                winreg.SetValueEx(key, "EnableLargeBufferTransfers", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "IdleThreadTimeout", 0, winreg.REG_DWORD, 0xFF00)
                winreg.SetValueEx(key, "AutoShareServer", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "DisableLargeMtu", 0, winreg.REG_DWORD, 0)
        except Exception as e:
            logger.debug(f"LanmanServer parameters write failed: {str(e)}")

        path_mm = r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management"
        for v in mm_keys:
            SystemTweaksService.backup_registry_value("HKLM", path_mm, v)
        try:
            total_ram_gb = round(psutil.virtual_memory().total / (1024**3))
            disable_paging_val = 1 if total_ram_gb >= 16 else 0
            large_cache_val = 1 if total_ram_gb >= 32 else 0

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_mm, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "LargeSystemCache", 0, winreg.REG_DWORD, large_cache_val)
                winreg.SetValueEx(key, "IOPageLockLimit", 0, winreg.REG_DWORD, 0xf00000)
                winreg.SetValueEx(key, "DisablePagingExecutive", 0, winreg.REG_DWORD, disable_paging_val)
                winreg.SetValueEx(key, "SecondLevelDataCache", 0, winreg.REG_DWORD, 0x400)
                winreg.SetValueEx(key, "ClearPageFileAtShutdown", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "LargePageMinimum", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "PoolUsageMaximum", 0, winreg.REG_DWORD, 0x60)
        except Exception as e:
            logger.debug(f"Memory Management cache write failed: {str(e)}")

        for v in afd_keys:
            SystemTweaksService.backup_registry_value("HKLM", path_afd, v)
        try:
            with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_afd, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "DynamicSendBufferDisable", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "FastSendDatagramThreshold", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "DefaultSendWindow", 0, winreg.REG_DWORD, 0x20000)
                winreg.SetValueEx(key, "DefaultReceiveWindow", 0, winreg.REG_DWORD, 0x20000)
                winreg.SetValueEx(key, "MaxFastTransmit", 0, winreg.REG_DWORD, 0x10)
                winreg.SetValueEx(key, "MaxFastCopyTransmit", 0, winreg.REG_DWORD, 0x10)
                winreg.SetValueEx(key, "FastCopyReceiveThreshold", 0, winreg.REG_DWORD, 0x100)
                winreg.SetValueEx(key, "PriorityBoost", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "EnableDynamicBacklog", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "MinimumDynamicBacklog", 0, winreg.REG_DWORD, 0x20)
                winreg.SetValueEx(key, "MaximumDynamicBacklog", 0, winreg.REG_DWORD, 0x10000)
                winreg.SetValueEx(key, "DynamicBacklogGrowthDelta", 0, winreg.REG_DWORD, 0x10)
                winreg.SetValueEx(key, "SendWindowSize", 0, winreg.REG_DWORD, 0x40000)
                winreg.SetValueEx(key, "ReceiveWindowSize", 0, winreg.REG_DWORD, 0x40000)
                winreg.SetValueEx(key, "ReceivePostsLowWater", 0, winreg.REG_DWORD, 0x400)
                winreg.SetValueEx(key, "ReceivePostsHighWater", 0, winreg.REG_DWORD, 0x1000)
                winreg.SetValueEx(key, "LargeBufferSize", 0, winreg.REG_DWORD, 0x20000)
                winreg.SetValueEx(key, "MediumBufferSize", 0, winreg.REG_DWORD, 0x8000)
            logger.info("AFD packet buffers and dynamic backlog applied successfully.")
        except Exception as e:
            logger.debug(f"AFD parameters write failed: {str(e)}")

        # Removed redundant configuration of path_sysres to prevent registry pollution
        pass

        path_class = r"SYSTEM\CurrentControlSet\Control\Class\{4D36E972-E325-11CE-BFC1-08002BE10318}"
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_class, 0, winreg.KEY_READ) as root_key:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(root_key, i)
                        if sub.isdigit():
                            sub_path = f"{path_class}\\{sub}"
                            for v in ["*FlowControl", "*InterruptModeration", "*PriorityVLANTag", "*ReceiveBuffers", 
                                      "*TransmitBuffers", "*EEE", "*WakeOnMagicPacket", "*WakeOnPattern", "*RSS", "*NumRssQueues"]:
                                SystemTweaksService.backup_registry_value("HKLM", sub_path, v)
                            try:
                                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub_path, 0, winreg.KEY_WRITE) as k:
                                    winreg.SetValueEx(k, "*FlowControl", 0, winreg.REG_SZ, "0")
                                    winreg.SetValueEx(k, "*InterruptModeration", 0, winreg.REG_SZ, "0")
                                    winreg.SetValueEx(k, "*PriorityVLANTag", 0, winreg.REG_SZ, "1")
                                    winreg.SetValueEx(k, "*ReceiveBuffers", 0, winreg.REG_SZ, "4096")
                                    winreg.SetValueEx(k, "*TransmitBuffers", 0, winreg.REG_SZ, "4096")
                                    winreg.SetValueEx(k, "*EEE", 0, winreg.REG_SZ, "0")
                                    winreg.SetValueEx(k, "*WakeOnMagicPacket", 0, winreg.REG_SZ, "0")
                                    winreg.SetValueEx(k, "*WakeOnPattern", 0, winreg.REG_SZ, "0")
                                    winreg.SetValueEx(k, "*RSS", 0, winreg.REG_SZ, "1")
                                    winreg.SetValueEx(k, "*NumRssQueues", 0, winreg.REG_SZ, "16")
                            except Exception:  # nosec
                                pass
                        i += 1
                    except OSError:
                        break
        except Exception as e:
            logger.debug(f"NIC Class adapters write failed: {str(e)}")


    @staticmethod
    def apply_usb_low_latency(enable: bool):
        path_xhci = r"SYSTEM\CurrentControlSet\Services\USBXHCI\Parameters"
        path_hub = r"SYSTEM\CurrentControlSet\Services\usbhub\HubG"
        path_usb = r"SYSTEM\CurrentControlSet\Services\Usb"
        path_stor = r"SYSTEM\CurrentControlSet\Services\USBSTOR"
        path_params = r"SYSTEM\CurrentControlSet\Services\Usb\Parameters"
        path_ccgp = r"SYSTEM\CurrentControlSet\Services\usbccgp\Parameters"
        
        if not enable:
            for v in ["ForceLowLatency", "AsynchronousScheduleEnable", "DisableSelectiveSuspend", 
                      "MaxTransferSize", "InterruptModeration", "ForceHCResetOnResume"]:
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_xhci, v, None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_hub, "IdleTimeout", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_usb, "DisableSelectiveSuspend", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_stor, "TransferBufferLength", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_params, "MaximumTransferSize", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_params, "Timeout", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_ccgp, "HighSpeedEnable", None, winreg.REG_DWORD)
            return
            
        logger.info("Applying USB Low Latency and controller queue overrides...")
        for v in ["ForceLowLatency", "AsynchronousScheduleEnable", "DisableSelectiveSuspend", 
                  "MaxTransferSize", "InterruptModeration", "ForceHCResetOnResume"]:
            SystemTweaksService.backup_registry_value("HKLM", path_xhci, v)
        try:
            with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_xhci, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "ForceLowLatency", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "AsynchronousScheduleEnable", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "DisableSelectiveSuspend", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "MaxTransferSize", 0, winreg.REG_DWORD, 65536)
                winreg.SetValueEx(key, "InterruptModeration", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "ForceHCResetOnResume", 0, winreg.REG_DWORD, 1)
        except Exception as e:
            logger.debug(f"USBXHCI write failed: {str(e)}")

        SystemTweaksService.backup_registry_value("HKLM", path_hub, "IdleTimeout")
        try:
            with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_hub, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "IdleTimeout", 0, winreg.REG_DWORD, 0)
        except Exception as e:
            logger.debug(f"usbhub\\HubG write failed: {str(e)}")

        SystemTweaksService.backup_registry_value("HKLM", path_usb, "DisableSelectiveSuspend")
        try:
            with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_usb, 0, winreg.REG_DWORD, 1) as key:
                pass
        except Exception as e:
            logger.debug(f"Usb service write failed: {str(e)}")

        SystemTweaksService.backup_registry_value("HKLM", path_stor, "TransferBufferLength")
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_stor, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "TransferBufferLength", 0, winreg.REG_DWORD, 65536)
        except Exception as e:
            logger.debug(f"USBSTOR write failed: {str(e)}")

        SystemTweaksService.backup_registry_value("HKLM", path_params, "MaximumTransferSize")
        SystemTweaksService.backup_registry_value("HKLM", path_params, "Timeout")
        try:
            with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_params, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "MaximumTransferSize", 0, winreg.REG_DWORD, 65536)
                winreg.SetValueEx(key, "Timeout", 0, winreg.REG_DWORD, 100)
        except Exception as e:
            logger.debug(f"Usb parameters write failed: {str(e)}")

        SystemTweaksService.backup_registry_value("HKLM", path_ccgp, "HighSpeedEnable")
        try:
            with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_ccgp, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "HighSpeedEnable", 0, winreg.REG_DWORD, 2)
        except Exception as e:
            logger.debug(f"usbccgp write failed: {str(e)}")


    @staticmethod
    def apply_dpc_latency_tweak(enable: bool):
        path_smk = r"SYSTEM\CurrentControlSet\Control\Session Manager\kernel"
        path_pwr = r"SYSTEM\CurrentControlSet\Control\Power"
        path_gdp = r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers\Power"
        
        if not enable:
            for v in ["IdealDpcRate", "ThreadDpcEnable"]:
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_smk, v, None, winreg.REG_DWORD)
            for v in ["ExitLatency", "ExitLatencyCheckEnabled", "Latency", "LatencyToleranceDefault", 
                      "LatencyToleranceFSVP", "LatencyTolerancePerfOverride", "LatencyToleranceScreenOffIR", "RtlCapabilityCheckLatency"]:
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_pwr, v, None, winreg.REG_DWORD)
            for v in ["DefaultD3TransitionLatencyActivelyUsed", "DefaultD3TransitionLatencyIdleLongTime", 
                      "DefaultD3TransitionLatencyIdleMonitorOff", "DefaultD3TransitionLatencyIdleNoContext", 
                      "DefaultD3TransitionLatencyIdleShortTime", "DefaultD3TransitionLatencyIdleVeryLongTime", 
                      "DefaultLatencyToleranceIdle0", "DefaultLatencyToleranceIdle0MonitorOff", 
                      "DefaultLatencyToleranceIdle1", "DefaultLatencyToleranceIdle1MonitorOff", 
                      "DefaultLatencyToleranceMemory", "DefaultLatencyToleranceNoContext", 
                      "DefaultLatencyToleranceNoContextMonitorOff", "DefaultLatencyToleranceOther", 
                      "DefaultLatencyToleranceTimerPeriod", "DefaultMemoryRefreshLatencyToleranceActivelyUsed", 
                      "DefaultMemoryRefreshLatencyToleranceMonitorOff", "DefaultMemoryRefreshLatencyToleranceNoContext", 
                      "Latency", "MaxIAverageGraphicsLatencyInOneBucket", "MiracastPerfTrackGraphicsLatency", 
                      "MonitorLatencyTolerance", "MonitorRefreshLatencyTolerance", "TransitionLatency"]:
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_gdp, v, None, winreg.REG_DWORD)
            return
            
        logger.info("Applying DPC kernel and power tolerance latencies...")
        for v in ["IdealDpcRate", "ThreadDpcEnable"]:
            SystemTweaksService.backup_registry_value("HKLM", path_smk, v)
        try:
            with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_smk, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "IdealDpcRate", 0, winreg.REG_DWORD, 1)
                # Safe and highly effective scheduling tweak: offload DPCs to threaded DPCs
                winreg.SetValueEx(key, "ThreadDpcEnable", 0, winreg.REG_DWORD, 1)
        except Exception as e:
            logger.debug(f"DPC SM kernel write failed: {str(e)}")

        for v in ["ExitLatency", "ExitLatencyCheckEnabled", "Latency", "LatencyToleranceDefault", 
                  "LatencyToleranceFSVP", "LatencyTolerancePerfOverride", "LatencyToleranceScreenOffIR", "RtlCapabilityCheckLatency"]:
            SystemTweaksService.backup_registry_value("HKLM", path_pwr, v)
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_pwr, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "ExitLatency", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "ExitLatencyCheckEnabled", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "Latency", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "LatencyToleranceDefault", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "LatencyToleranceFSVP", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "LatencyTolerancePerfOverride", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "LatencyToleranceScreenOffIR", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "RtlCapabilityCheckLatency", 0, winreg.REG_DWORD, 1)
        except Exception as e:
            logger.debug(f"Power latency write failed: {str(e)}")

        for v in ["DefaultD3TransitionLatencyActivelyUsed", "DefaultD3TransitionLatencyIdleLongTime", 
                  "DefaultD3TransitionLatencyIdleMonitorOff", "DefaultD3TransitionLatencyIdleNoContext", 
                  "DefaultD3TransitionLatencyIdleShortTime", "DefaultD3TransitionLatencyIdleVeryLongTime", 
                  "DefaultLatencyToleranceIdle0", "DefaultLatencyToleranceIdle0MonitorOff", 
                  "DefaultLatencyToleranceIdle1", "DefaultLatencyToleranceIdle1MonitorOff", 
                  "DefaultLatencyToleranceMemory", "DefaultLatencyToleranceNoContext", 
                  "DefaultLatencyToleranceNoContextMonitorOff", "DefaultLatencyToleranceOther", 
                  "DefaultLatencyToleranceTimerPeriod", "DefaultMemoryRefreshLatencyToleranceActivelyUsed", 
                  "DefaultMemoryRefreshLatencyToleranceMonitorOff", "DefaultMemoryRefreshLatencyToleranceNoContext", 
                  "Latency", "MaxIAverageGraphicsLatencyInOneBucket", "MiracastPerfTrackGraphicsLatency", 
                  "MonitorLatencyTolerance", "MonitorRefreshLatencyTolerance", "TransitionLatency"]:
            SystemTweaksService.backup_registry_value("HKLM", path_gdp, v)
        try:
            with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_gdp, 0, winreg.KEY_WRITE) as key:
                for v in ["DefaultD3TransitionLatencyActivelyUsed", "DefaultD3TransitionLatencyIdleLongTime", 
                          "DefaultD3TransitionLatencyIdleMonitorOff", "DefaultD3TransitionLatencyIdleNoContext", 
                          "DefaultD3TransitionLatencyIdleShortTime", "DefaultD3TransitionLatencyIdleVeryLongTime", 
                          "DefaultLatencyToleranceIdle0", "DefaultLatencyToleranceIdle0MonitorOff", 
                          "DefaultLatencyToleranceIdle1", "DefaultLatencyToleranceIdle1MonitorOff", 
                          "DefaultLatencyToleranceMemory", "DefaultLatencyToleranceNoContext", 
                          "DefaultLatencyToleranceNoContextMonitorOff", "DefaultLatencyToleranceOther", 
                          "DefaultLatencyToleranceTimerPeriod", "DefaultMemoryRefreshLatencyToleranceActivelyUsed", 
                          "DefaultMemoryRefreshLatencyToleranceMonitorOff", "DefaultMemoryRefreshLatencyToleranceNoContext", 
                          "Latency", "MaxIAverageGraphicsLatencyInOneBucket", "MiracastPerfTrackGraphicsLatency", 
                          "MonitorLatencyTolerance", "MonitorRefreshLatencyTolerance", "TransitionLatency"]:
                    winreg.SetValueEx(key, v, 0, winreg.REG_DWORD, 1)
        except Exception as e:
            logger.debug(f"GraphicsDrivers Power write failed: {str(e)}")


    @staticmethod
    def apply_dwm_super_wet(enable: bool):
        path_dwm = r"SOFTWARE\Microsoft\Windows\DWM"
        path_mm = r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management"
        
        dwm_keys = [
            "SuperWetEnabled", "SDRBoostPercentOverride", "ResampleInLinearSpace", "OneCoreNoDWMRawGameController", 
            "MPCInputRouterWaitForDebugger", "InteractionOutputPredictionDisabled", "InkGPUAccelOverrideVendorWhitelist", 
            "EnableRenderPathTestMode", "FlattenVirtualSurfaceEffectInput", "EnableCpuClipping", 
            "DisallowNonDrawListRendering", "DisableProjectedShadowsRendering", "DisableProjectedShadows", 
            "DisableLockingMemory", "DisableHologramCompositor", "DisableDeviceBitmaps", "DebugFailFast", 
            "DDisplayTestMode", "UseHWDrawListEntriesOnWARP", "ResampleModeOverride", 
            "RenderThreadWatchdogTimeoutMilliseconds", "ParallelModePolicy", "EnableResizeOptimization", 
            "EnableMegaRects", "EnableFrontBufferRenderChecks", "EnableEffectCaching", "EnableDesktopOverlays", 
            "EnablePrimitiveReordering", "MaxD3DFeatureLevel", "OverlayQualifyCount", "OverlayDisqualifyCount", 
            "ResizeTimeoutModern", "ResizeTimeoutGdi", "HighColor", "DisableDrawListCaching",
            "AnimationsShiftKey", "AnimationAttributionEnabled", "EnableCommonSuperSets", "DisableAdvancedDirectFlip"
        ]
        
        if not enable:
            for v in dwm_keys:
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_dwm, v, None, winreg.REG_DWORD if v != "InkGPUAccelOverrideVendorWhitelist" else winreg.REG_SZ)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_mm, "SessionPoolSize", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_mm, "SessionViewSize", None, winreg.REG_DWORD)
            logger.info("DWM super wet tweaks restored in registry. Changes will take effect upon next logoff or reboot.")
            return
            
        logger.info("Applying high-performance DWM rendering and caching tweaks...")
        for v in dwm_keys:
            SystemTweaksService.backup_registry_value("HKLM", path_dwm, v)
            
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_dwm, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "SuperWetEnabled", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "SDRBoostPercentOverride", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "ResampleInLinearSpace", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "OneCoreNoDWMRawGameController", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "MPCInputRouterWaitForDebugger", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "InteractionOutputPredictionDisabled", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "InkGPUAccelOverrideVendorWhitelist", 0, winreg.REG_SZ, "1")
                winreg.SetValueEx(key, "EnableRenderPathTestMode", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "FlattenVirtualSurfaceEffectInput", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "EnableCpuClipping", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "DisallowNonDrawListRendering", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "DisableProjectedShadowsRendering", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "DisableProjectedShadows", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "DisableLockingMemory", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "DisableHologramCompositor", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "DisableDeviceBitmaps", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "DebugFailFast", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "DDisplayTestMode", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "UseHWDrawListEntriesOnWARP", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "ResampleModeOverride", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "RenderThreadWatchdogTimeoutMilliseconds", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "ParallelModePolicy", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "EnableResizeOptimization", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "EnableMegaRects", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "EnableFrontBufferRenderChecks", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "EnableEffectCaching", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "EnableDesktopOverlays", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "EnablePrimitiveReordering", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "MaxD3DFeatureLevel", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "OverlayQualifyCount", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "OverlayDisqualifyCount", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "ResizeTimeoutModern", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "ResizeTimeoutGdi", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "HighColor", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "DisableDrawListCaching", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "AnimationsShiftKey", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "AnimationAttributionEnabled", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "EnableCommonSuperSets", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "DisableAdvancedDirectFlip", 0, winreg.REG_DWORD, 1)
            logger.info("DWM super wet tweaks applied to registry. Changes will take effect upon next logoff or reboot.")
        except Exception as e:
            logger.error(f"Failed to apply DWM super wet tweaks: {str(e)}")
            raise

        SystemTweaksService.backup_registry_value("HKLM", path_mm, "SessionPoolSize")
        SystemTweaksService.backup_registry_value("HKLM", path_mm, "SessionViewSize")
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_mm, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "SessionPoolSize", 0, winreg.REG_DWORD, 6)
                winreg.SetValueEx(key, "SessionViewSize", 0, winreg.REG_DWORD, 0x48)
        except Exception as e:
            logger.debug(f"Session memory pool sizes write failed: {str(e)}")


    @staticmethod
    def apply_keyboard_rate(level: int):
        path_kb = r"Control Panel\Keyboard"
        path_kbr = r"Control Panel\Accessibility\Keyboard Response"
        
        if level <= 0 or level > 4:
            SystemTweaksService.restore_registry_value_or_default("HKCU", path_kb, "KeyboardDelay", "1", winreg.REG_SZ)
            SystemTweaksService.restore_registry_value_or_default("HKCU", path_kb, "KeyboardSpeed", "31", winreg.REG_SZ)
            for v in ["BounceTime", "DelayBeforeAcceptance", "AutoRepeatDelay", "AutoRepeatRate", "Flags"]:
                SystemTweaksService.restore_registry_value_or_default("HKCU", path_kbr, v, "0" if v == "BounceTime" else ("1000" if v in ["DelayBeforeAcceptance", "AutoRepeatDelay"] else ("500" if v == "AutoRepeatRate" else "126")), winreg.REG_SZ)
            try:
                exe_path = SystemTweaksService.get_resource_path(os.path.join("core_commander", "resources", "keyrate.exe"))
                if os.path.exists(exe_path):
                    SystemTweaksService.safe_subprocess_call([exe_path, "1000", "31"], timeout=5, cwd=os.path.dirname(exe_path))
            except Exception:  # nosec
                pass
            return
            
        SystemTweaksService.backup_registry_value("HKCU", path_kb, "KeyboardDelay")
        SystemTweaksService.backup_registry_value("HKCU", path_kb, "KeyboardSpeed")
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path_kb, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "KeyboardDelay", 0, winreg.REG_SZ, "0")
                winreg.SetValueEx(key, "KeyboardSpeed", 0, winreg.REG_SZ, "48")
        except Exception as e:
            logger.debug(f"Control Panel Keyboard write failed: {str(e)}")

        for v in ["BounceTime", "DelayBeforeAcceptance", "AutoRepeatDelay", "AutoRepeatRate", "Flags"]:
            SystemTweaksService.backup_registry_value("HKCU", path_kbr, v)
        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path_kbr, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "BounceTime", 0, winreg.REG_SZ, "0")
                winreg.SetValueEx(key, "DelayBeforeAcceptance", 0, winreg.REG_SZ, "0")
                winreg.SetValueEx(key, "AutoRepeatDelay", 0, winreg.REG_SZ, "175")
                winreg.SetValueEx(key, "AutoRepeatRate", 0, winreg.REG_SZ, "25")
                winreg.SetValueEx(key, "Flags", 0, winreg.REG_SZ, "3")
        except Exception as e:
            logger.debug(f"Keyboard Response write failed: {str(e)}")

        try:
            exe_path = SystemTweaksService.get_resource_path(os.path.join("core_commander", "resources", "keyrate.exe"))
            if os.path.exists(exe_path):
                args = {
                    1: ["150", "10"],
                    2: ["80", "10"],
                    3: ["10", "10"],
                    4: ["1", "1"]
                }
                cmd_args = args.get(level, ["150", "10"])
                SystemTweaksService.safe_subprocess_call([exe_path] + cmd_args, timeout=5, cwd=os.path.dirname(exe_path))
                logger.info(f"Keyboard repeat speed applied in-session using keyrate {cmd_args[0]} {cmd_args[1]}.")
            else:
                logger.error(f"keyrate.exe not found at {exe_path}!")
        except Exception as e:
            logger.error(f"Failed to execute keyrate: {str(e)}")
            raise


    @staticmethod
    def apply_timer_resolution(enable: bool):
        path = r"SYSTEM\CurrentControlSet\Control\Session Manager\kernel"
        if not enable:
            SystemTweaksService.restore_registry_value_or_default("HKLM", path, "GlobalTimerResolutionRequests", None, winreg.REG_DWORD)
            return
            
        logger.info("Enabling global timer resolution requests...")
        SystemTweaksService.backup_registry_value("HKLM", path, "GlobalTimerResolutionRequests")
        try:
            with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "GlobalTimerResolutionRequests", 0, winreg.REG_DWORD, 1)
            logger.info("Global timer resolution requests enabled.")
        except Exception as e:
            logger.error(f"Failed to enable timer resolution registry: {str(e)}")
            raise


    @staticmethod
    def apply_usb_imod_tweak(enable: bool):
        path = r"SYSTEM\CurrentControlSet\Services\USBXHCI\Parameters"
        rwe_path = r"C:\Program Files\RW-Everything\Rw.exe"
        
        if not enable:
            SystemTweaksService.restore_registry_value_or_default("HKLM", path, "InterruptModeration", None, winreg.REG_DWORD)
            # Restore IMOD to default 4000 (0xFA0) using RW-Everything if installed
            interval_val = "0xFA0"
        else:
            logger.info("Applying USB interrupt moderation tweak...")
            SystemTweaksService.backup_registry_value("HKLM", path, "InterruptModeration")
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "InterruptModeration", 0, winreg.REG_DWORD, 0)
            except Exception as e:
                logger.debug(f"USBXHCI write failed in imod: {str(e)}")
            interval_val = "0x0"

        try:
            if os.path.exists(rwe_path):
                ps_script = f"""
$globalInterval = {interval_val}
$globalHCSPARAMSOffset = 0x4
$globalRTSOFF = 0x18
$rwePath = "{rwe_path}"

function Dec-To-Hex($decimal) {{
    $hexValue = $decimal.ToString("X2")
    return "0x$($hexValue)"
}}

function Get-Value-From-Address($address) {{
    $address = Dec-To-Hex -decimal ([uint64]$address)
    $stdout = & $rwePath /Min /NoLogo /Stdout /Command="R32 $($address)" | Out-String
    $splitString = $stdout -split " "
    return [uint64]$splitString[-1]
}}

function Get-Device-Addresses() {{
    $data = @{{}}
    $resources = Get-CimInstance -ClassName Win32_PNPAllocatedResource -Namespace root\\CIMV2 -ErrorAction SilentlyContinue
    if (!$resources) {{
        $resources = Get-WmiObject -Class Win32_PNPAllocatedResource -ComputerName LocalHost -Namespace root\\CIMV2 -ErrorAction SilentlyContinue
    }}
    foreach ($resource in $resources) {{
        $deviceId = $resource.Dependent.Split("=")[1].Replace('"', '').Replace("\\\\", "\\")
        $physicalAddress = $resource.Antecedent.Split("=")[1].Replace('"', '')
        if (-not $data.ContainsKey($deviceId) -and $deviceId -and $physicalAddress) {{
            $data[$deviceId] = [uint64]$physicalAddress
        }}
    }}
    return $data
}}

Stop-Process -Name "Rw" -ErrorAction SilentlyContinue
$deviceMap = Get-Device-Addresses

$controllers = Get-CimInstance -ClassName Win32_USBController -ErrorAction SilentlyContinue
if (!$controllers) {{
    $controllers = Get-WmiObject Win32_USBController -ErrorAction SilentlyContinue
}}
foreach ($xhciController in $controllers) {{
    if ($xhciController.ConfigManagerErrorCode -eq 22) {{
        continue
    }}
    $deviceId = $xhciController.DeviceID
    if (-not $deviceMap.Contains($deviceId)) {{
        continue
    }}
    $capabilityAddress = $deviceMap[$deviceId]
    $HCSPARAMSValue = Get-Value-From-Address -address ($capabilityAddress + $globalHCSPARAMSOffset)
    $HCSPARAMSBitmask = [Convert]::ToString($HCSPARAMSValue, 2)
    $maxIntrs = [Convert]::ToInt32($HCSPARAMSBitmask.Substring($HCSPARAMSBitmask.Length - 16, 8), 2)
    $RTSOFFValue = Get-Value-From-Address -address ($capabilityAddress + $globalRTSOFF)
    $runtimeAddress = $capabilityAddress + $RTSOFFValue

    for ($i = 0; $i -lt $maxIntrs; $i++) {{
        $interrupterAddress = Dec-To-Hex -decimal ([uint64]($runtimeAddress + 0x24 + (0x20 * $i)))
        & $rwePath /Min /NoLogo /Stdout /Command="W32 $($interrupterAddress) $($globalInterval)" | Out-Null
    }}
}}
"""
                import subprocess
                cmd = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", "-"]
                p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
                try:
                    stdout, stderr = p.communicate(input=ps_script, timeout=15)
                    logger.debug(f"USB IMOD script execution output: {stdout}, error: {stderr}")
                except subprocess.TimeoutExpired:
                    p.kill()
                    logger.error("USB IMOD script execution timed out")
                logger.info(f"USB Hardware Interrupt Moderation (IMOD) configured in memory via RWE to: {interval_val}")
            else:
                logger.warning("RW-Everything is not installed at C:\\Program Files\\RW-Everything\\Rw.exe. Skipping hardware IMOD modification.")
        except Exception as ex:
            logger.error(f"Failed to apply USB hardware IMOD tweak: {str(ex)}")
            raise

    @staticmethod
    def apply_pcipower_tweak(disable_power: bool):
        path_pwr = r"SYSTEM\CurrentControlSet\Control\Power"
        path_pci = r"SYSTEM\CurrentControlSet\Control\Power\PowerSettings\501a4d13-42af-4429-9fd1-a8218c268e20\ee12f906-d277-404b-b6da-e5fa1a576df5"
        path_pci_svc = r"SYSTEM\CurrentControlSet\Services\pci"
        
        pci_svc_keys = ["MaxLinkSpeed", "LinkStatePowerManagement", "PerformanceMode", "SlotPowerManagement", "AsPmEnabled"]
        
        if not disable_power:
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_pwr, "PciPowerManagement", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_pwr, "CsEnabled", 1, winreg.REG_DWORD)
            for v in ["Attributes", "AcSettingIndex", "DcSettingIndex"]:
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_pci, v, None, winreg.REG_DWORD)
            for v in pci_svc_keys:
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_pci_svc, v, None, winreg.REG_DWORD)
            try:
                # Restore active power scheme Link State Power Management to default (1 = Moderate power savings)
                SystemTweaksService.set_power_setting_value("SCHEME_CURRENT", "501a4d13-42af-4429-9fd1-a8218c268e20", "ee12f906-d277-404b-b6da-e5fa1a576df5", 1, activate=True)
            except Exception as e:
                logger.debug(f"Failed to restore active power scheme Link State Power Management: {str(e)}")
            return
            
        logger.info("Disabling PCI Power Management and setting links to high performance...")
        SystemTweaksService.backup_registry_value("HKLM", path_pwr, "PciPowerManagement")
        SystemTweaksService.backup_registry_value("HKLM", path_pwr, "CsEnabled")
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_pwr, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "PciPowerManagement", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "CsEnabled", 0, winreg.REG_DWORD, 0)
        except Exception as e:
            logger.debug(f"Power setting PciPowerManagement write failed: {str(e)}")

        for v in ["Attributes", "AcSettingIndex", "DcSettingIndex"]:
            SystemTweaksService.backup_registry_value("HKLM", path_pci, v)
        try:
            with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_pci, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "Attributes", 0, winreg.REG_DWORD, 2)
                winreg.SetValueEx(key, "AcSettingIndex", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "DcSettingIndex", 0, winreg.REG_DWORD, 0)
            
            # Update active scheme link state power management to Off
            # ee12f906-d277-404b-b6da-e5fa1a576df5 is Link State Power Management
            # 501a4d13-42af-4429-9fd1-a8218c268e20 is PCI Express Subgroup
            SystemTweaksService.set_power_setting_value("SCHEME_CURRENT", "501a4d13-42af-4429-9fd1-a8218c268e20", "ee12f906-d277-404b-b6da-e5fa1a576df5", 0, activate=True)
            logger.info("Link State Power Management disabled and configured successfully.")
        except Exception as e:
            logger.debug(f"Link State Power Management configuration failed: {str(e)}")

        # Configure Services\pci for max speed and maximum slot power
        for v in pci_svc_keys:
            SystemTweaksService.backup_registry_value("HKLM", path_pci_svc, v)
        try:
            with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_pci_svc, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "MaxLinkSpeed", 0, winreg.REG_DWORD, 3)
                winreg.SetValueEx(key, "LinkStatePowerManagement", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "PerformanceMode", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "SlotPowerManagement", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "AsPmEnabled", 0, winreg.REG_DWORD, 0)
            logger.info("PCI services links configured to maximum speed and performance successfully.")
        except Exception as e:
            logger.debug(f"PCI services configuration write failed: {str(e)}")

    @staticmethod
    def apply_directx_tweaks(enable: bool):
        path_dx = r"SOFTWARE\Microsoft\DirectX"
        path_dxg = r"SYSTEM\CurrentControlSet\Services\DXGKrnl"
        draw_paths = [r"SOFTWARE\Microsoft\DirectDraw", r"SOFTWARE\Wow6432Node\Microsoft\DirectDraw"]
        d3d_drvs = [r"SOFTWARE\Microsoft\Direct3D\Drivers", r"SOFTWARE\Wow6432Node\Microsoft\Direct3D\Drivers"]
        d3d_globs = [r"SOFTWARE\Microsoft\Direct3D", r"SOFTWARE\Wow6432Node\Microsoft\Direct3D"]
        
        dx_keys = [
            "DXGI_PREEMPTION_MODE", "DXGI_FRAME_LATENCY_WAITABLE_OBJECT", "DXGI_SWAP_CHAIN_WAITABLE_OBJECT", 
            "DXGI_FORCE_FLIP_DISCARD", "DXGI_SWAP_CHAIN_SCALE", "DXGI_SWAP_CHAIN_ALLOW_MODE_SWITCH", 
            "DXGI_SWAP_CHAIN_FULLSCREEN_FLIP_MODE", "DXGI_DISABLE_DWM_THROTTLING", "DXGI_FORCE_FLIP_SEQUENTIAL", 
            "DXGI_FORCE_FULLSCREEN_FLIP_MODE", "DXGI_MAX_FRAME_LATENCY", "DXGI_USE_OPTIMIZED_SWAP_CHAIN"
        ]
        
        dxg_keys = [
            "CreateGdiPrimaryOnSlaveGPU", "DriverSupportsCddDwmInterop", "DxgkCddSyncDxAccess", 
            "DxgkCddSyncGPUAccess", "DxgkCddWaitForVerticalBlankEvent", "DxgkCreateSwapChain", 
            "DxgkFreeGpuVirtualAddress", "DxgkOpenSwapChain", "DxgkShareSwapChainObject", 
            "DxgkWaitForVerticalBlankEvent", "DxgkWaitForVerticalBlankEvent2", "SwapChainBackBuffer", 
            "TdrResetFromTimeoutAsync"
        ]
        
        if not enable:
            for v in dx_keys:
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_dx, v, None, winreg.REG_DWORD)
            for v in dxg_keys:
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_dxg, v, None, winreg.REG_DWORD)
            for draw_path in draw_paths:
                for v in ["DisableAGPSupport", "UseNonLocalVidMem", "DisableDDSCAPSInDDSD", "EmulatePointSprites", "EmulateStateBlocks"]:
                    SystemTweaksService.restore_registry_value_or_default("HKLM", draw_path, v, None, winreg.REG_DWORD)
            for d3d_drv in d3d_drvs:
                for v in ["ForceRgbRasterizer", "EnumReference", "EnumSeparateMMX", "EnumRamp", "EnumNullDevice", "UseMMXForRGB"]:
                    SystemTweaksService.restore_registry_value_or_default("HKLM", d3d_drv, v, None, winreg.REG_DWORD)
            for d3d_glob in d3d_globs:
                for v in ["UseNonLocalVidMem", "FullDebug", "DisableDM", "EnableMultimonDebugging", 
                          "LoadDebugRuntime", "FewVertices", "DisableMMX", "UseMMXForRGB", "DisableVidMemVBs", "MaxPreRenderedFrames"]:
                    SystemTweaksService.restore_registry_value_or_default("HKLM", d3d_glob, v, None, winreg.REG_DWORD)
            return
            
        logger.info("Applying full DirectX 3D and swap chain latency tweaks...")
        for v in dx_keys:
            SystemTweaksService.backup_registry_value("HKLM", path_dx, v)
        try:
            with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_dx, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "DXGI_PREEMPTION_MODE", 0, winreg.REG_DWORD, 3)
                winreg.SetValueEx(key, "DXGI_FRAME_LATENCY_WAITABLE_OBJECT", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "DXGI_SWAP_CHAIN_WAITABLE_OBJECT", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "DXGI_FORCE_FLIP_DISCARD", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "DXGI_SWAP_CHAIN_SCALE", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "DXGI_SWAP_CHAIN_ALLOW_MODE_SWITCH", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "DXGI_SWAP_CHAIN_FULLSCREEN_FLIP_MODE", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "DXGI_DISABLE_DWM_THROTTLING", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "DXGI_FORCE_FLIP_SEQUENTIAL", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "DXGI_FORCE_FULLSCREEN_FLIP_MODE", 0, winreg.REG_DWORD, 3)
                winreg.SetValueEx(key, "DXGI_MAX_FRAME_LATENCY", 0, winreg.REG_DWORD, 2)
                winreg.SetValueEx(key, "DXGI_USE_OPTIMIZED_SWAP_CHAIN", 0, winreg.REG_DWORD, 1)
        except Exception as e:
            logger.debug(f"DirectX write failed: {str(e)}")

        for v in dxg_keys:
            SystemTweaksService.backup_registry_value("HKLM", path_dxg, v)
        try:
            with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_dxg, 0, winreg.KEY_WRITE) as key:
                for v in dxg_keys:
                    winreg.SetValueEx(key, v, 0, winreg.REG_DWORD, 1)
        except Exception as e:
            logger.debug(f"DXGKrnl write failed: {str(e)}")

        for draw_path in draw_paths:
            for v in ["DisableAGPSupport", "UseNonLocalVidMem", "DisableDDSCAPSInDDSD", "EmulatePointSprites", "EmulateStateBlocks"]:
                SystemTweaksService.backup_registry_value("HKLM", draw_path, v)
            try:
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, draw_path, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "DisableAGPSupport", 0, winreg.REG_DWORD, 0)
                    winreg.SetValueEx(key, "UseNonLocalVidMem", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(key, "DisableDDSCAPSInDDSD", 0, winreg.REG_DWORD, 0)
                    winreg.SetValueEx(key, "EmulatePointSprites", 0, winreg.REG_DWORD, 0)
                    winreg.SetValueEx(key, "EmulateStateBlocks", 0, winreg.REG_DWORD, 0)
            except Exception as e:
                logger.debug(f"DirectDraw {draw_path} write failed: {str(e)}")

        for d3d_drv in d3d_drvs:
            for v in ["ForceRgbRasterizer", "EnumReference", "EnumSeparateMMX", "EnumRamp", "EnumNullDevice", "UseMMXForRGB"]:
                SystemTweaksService.backup_registry_value("HKLM", d3d_drv, v)
            try:
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, d3d_drv, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "ForceRgbRasterizer", 0, winreg.REG_DWORD, 0)
                    winreg.SetValueEx(key, "EnumReference", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(key, "EnumSeparateMMX", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(key, "EnumRamp", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(key, "EnumNullDevice", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(key, "UseMMXForRGB", 0, winreg.REG_DWORD, 1)
            except Exception as e:
                logger.debug(f"Direct3D Drivers {d3d_drv} write failed: {str(e)}")

        for d3d_glob in d3d_globs:
            for v in ["UseNonLocalVidMem", "FullDebug", "DisableDM", "EnableMultimonDebugging", 
                      "LoadDebugRuntime", "FewVertices", "DisableMMX", "UseMMXForRGB", "DisableVidMemVBs", "MaxPreRenderedFrames"]:
                SystemTweaksService.backup_registry_value("HKLM", d3d_glob, v)
            try:
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, d3d_glob, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "UseNonLocalVidMem", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(key, "FullDebug", 0, winreg.REG_DWORD, 0)
                    winreg.SetValueEx(key, "DisableDM", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(key, "EnableMultimonDebugging", 0, winreg.REG_DWORD, 0)
                    winreg.SetValueEx(key, "LoadDebugRuntime", 0, winreg.REG_DWORD, 0)
                    winreg.SetValueEx(key, "FewVertices", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(key, "DisableMMX", 0, winreg.REG_DWORD, 0)
                    winreg.SetValueEx(key, "UseMMXForRGB", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(key, "DisableVidMemVBs", 0, winreg.REG_DWORD, 0)
                    winreg.SetValueEx(key, "MaxPreRenderedFrames", 0, winreg.REG_DWORD, 1)
            except Exception as e:
                logger.debug(f"Direct3D {d3d_glob} write failed: {str(e)}")


    @staticmethod
    def apply_device_power_tweak(enable: bool):
        toggled_via_wmi = False
        if HAS_WIN32:
            has_com = False
            try:
                import pythoncom
                import win32com.client
                pythoncom.CoInitialize()
                has_com = True
                wmi_wmi = win32com.client.GetObject("winmgmts:{impersonationLevel=impersonate}!\\\\.\\root\\wmi")
                devices = wmi_wmi.ExecQuery("SELECT * FROM MSPower_DeviceEnable")
                for d in devices:
                    try:
                        d.Enable = not enable
                        d.Put_()
                    except Exception as ex:
                        logger.debug(f"Failed to set MSPower_DeviceEnable Enable={not enable} for individual device: {str(ex)}")
                toggled_via_wmi = True
                logger.info(f"WMI MSPower_DeviceEnable energy saving set to {not enable} via direct COM.")
            except Exception as e:
                logger.debug(f"Direct WMI MSPower_DeviceEnable toggle failed: {str(e)}")
            finally:
                devices = None
                wmi_wmi = None
                if has_com:
                    import gc
                    gc.collect()
                    try:
                        pythoncom.CoUninitialize()
                    except Exception:  # nosec
                        pass
                
        if not toggled_via_wmi:
            logger.warning("WMI MSPower_DeviceEnable energy saving could not be applied natively.")

        try:
            from core_commander.core.worker import SystemStateScannerWorker
            SystemStateScannerWorker._dev_power_cache = None
        except Exception:  # nosec
            pass

        if not enable:
            enum_props = [
                "EnhancedPowerManagementEnabled", "AllowIdleIrpInD3", "EnableSelectiveSuspend", 
                "DeviceSelectiveSuspended", "SelectiveSuspendEnabled", "SelectiveSuspendOn", 
                "WaitWakeEnabled", "D3ColdSupported", "WdfDirectedPowerTransitionEnable", 
                "EnableIdlePowerManagement", "IdleInWorkingState"
            ]
            
            def scan_and_restore_enum(key_root, key_path):
                try:
                    with winreg.OpenKey(key_root, key_path, 0, winreg.KEY_READ) as key:
                        i = 0
                        while True:
                            try:
                                sub = winreg.EnumKey(key, i)
                                sub_path = f"{key_path}\\{sub}"
                                try:
                                    with winreg.OpenKey(key_root, sub_path, 0, winreg.KEY_READ | winreg.KEY_WRITE) as sub_key:
                                        val_idx = 0
                                        while True:
                                            try:
                                                name, val, val_type = winreg.EnumValue(sub_key, val_idx)
                                                if name in enum_props:
                                                    SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, name, 1, winreg.REG_DWORD)
                                                val_idx += 1
                                            except OSError:
                                                break
                                except Exception:  # nosec
                                    pass
                                scan_and_restore_enum(key_root, sub_path)
                                i += 1
                            except OSError:
                                break
                except Exception:  # nosec
                    pass
            scan_and_restore_enum(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Enum")
            
            class_props = ["WakeEnabled", "WdkSelectiveSuspendEnable"]
            def scan_and_restore_class(key_root, key_path):
                try:
                    with winreg.OpenKey(key_root, key_path, 0, winreg.KEY_READ) as key:
                        i = 0
                        while True:
                            try:
                                sub = winreg.EnumKey(key, i)
                                sub_path = f"{key_path}\\{sub}"
                                try:
                                    with winreg.OpenKey(key_root, sub_path, 0, winreg.KEY_READ | winreg.KEY_WRITE) as sub_key:
                                        val_idx = 0
                                        while True:
                                            try:
                                                name, val, val_type = winreg.EnumValue(sub_key, val_idx)
                                                if name in class_props:
                                                    SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, name, 1, winreg.REG_DWORD)
                                                val_idx += 1
                                            except OSError:
                                                break
                                except Exception:  # nosec
                                    pass
                                scan_and_restore_class(key_root, sub_path)
                                i += 1
                            except OSError:
                                break
                except Exception:  # nosec
                    pass
            scan_and_restore_class(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Class")
            logger.info("Registry hardware driver energy savings restored successfully.")
            return
            
        logger.info("Executing registry hardware driver energy disabling and USB selective suspend tweaks...")
        enum_props = [
            "EnhancedPowerManagementEnabled", "AllowIdleIrpInD3", "EnableSelectiveSuspend", 
            "DeviceSelectiveSuspended", "SelectiveSuspendEnabled", "SelectiveSuspendOn", 
            "WaitWakeEnabled", "D3ColdSupported", "WdfDirectedPowerTransitionEnable", 
            "EnableIdlePowerManagement", "IdleInWorkingState"
        ]
        
        def scan_and_write_enum(key_root, key_path):
            try:
                with winreg.OpenKey(key_root, key_path, 0, winreg.KEY_READ) as key:
                    i = 0
                    while True:
                        try:
                            sub = winreg.EnumKey(key, i)
                            sub_path = f"{key_path}\\{sub}"
                            try:
                                with winreg.OpenKey(key_root, sub_path, 0, winreg.KEY_READ | winreg.KEY_WRITE) as sub_key:
                                    val_idx = 0
                                    while True:
                                        try:
                                            name, val, val_type = winreg.EnumValue(sub_key, val_idx)
                                            if name in enum_props:
                                                SystemTweaksService.backup_registry_value("HKLM", sub_path, name)
                                                winreg.SetValueEx(sub_key, name, 0, winreg.REG_DWORD, 0)
                                            val_idx += 1
                                        except OSError:
                                            break
                            except Exception:  # nosec
                                pass
                        
                            scan_and_write_enum(key_root, sub_path)
                            i += 1
                        except OSError:
                            break
            except Exception:  # nosec
                pass

        scan_and_write_enum(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Enum")

        class_props = ["WakeEnabled", "WdkSelectiveSuspendEnable"]
        def scan_and_write_class(key_root, key_path):
            try:
                with winreg.OpenKey(key_root, key_path, 0, winreg.KEY_READ) as key:
                    i = 0
                    while True:
                        try:
                            sub = winreg.EnumKey(key, i)
                            sub_path = f"{key_path}\\{sub}"
                            try:
                                with winreg.OpenKey(key_root, sub_path, 0, winreg.KEY_READ | winreg.KEY_WRITE) as sub_key:
                                    val_idx = 0
                                    while True:
                                        try:
                                            name, val, val_type = winreg.EnumValue(sub_key, val_idx)
                                            if name in class_props:
                                                SystemTweaksService.backup_registry_value("HKLM", sub_path, name)
                                                winreg.SetValueEx(sub_key, name, 0, winreg.REG_DWORD, 0)
                                            val_idx += 1
                                        except OSError:
                                            break
                            except Exception:  # nosec
                                pass
                            scan_and_write_class(key_root, sub_path)
                            i += 1
                        except OSError:
                            break
            except Exception:  # nosec
                pass

        scan_and_write_class(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Class")
        logger.info("Registry hardware driver energy savings disabled successfully.")


    @staticmethod
    def apply_gpu_irq_tweak(enable: bool):
        try:
            data = []
            queried_via_wmi = False
            if HAS_WIN32:
                has_com = False
                try:
                    import pythoncom
                    import win32com.client
                    pythoncom.CoInitialize()
                    has_com = True
                    wmi_cimv2 = win32com.client.GetObject("winmgmts:\\\\.\\root\\cimv2")
                    gpus = wmi_cimv2.ExecQuery("SELECT PNPDeviceID FROM Win32_VideoController")
                    gpu_ids = [g.PNPDeviceID for g in gpus if g.PNPDeviceID]
                    if gpu_ids:
                        for gid in gpu_ids:
                            gid_escaped = gid.replace("\\", "\\\\")
                            object_path = f'Win32_PnPEntity.DeviceID="{gid_escaped}"'
                            query = f'ASSOCIATORS OF {{{object_path}}} WHERE AssocClass=Win32_PnPAllocatedResource'
                            resources = wmi_cimv2.ExecQuery(query)
                            for r in resources:
                                try:
                                    if r.Path_.Class == "Win32_IRQResource":
                                        irq_num = abs(r.IRQNumber)
                                        data.append({"IRQ": irq_num})
                                except Exception:  # nosec
                                    pass
                            resources = None
                    queried_via_wmi = True
                except Exception as ex:
                    logger.debug(f"Direct WMI GPU IRQ query failed: {str(ex)}")
                finally:
                    gpus = None
                    wmi_cimv2 = None
                    if has_com:
                        import gc
                        gc.collect()
                        try:
                            pythoncom.CoUninitialize()
                        except Exception:  # nosec
                            pass

            if not queried_via_wmi:
                logger.warning("GPU IRQ could not be queried natively via WMI COM.")

            path = r"SYSTEM\CurrentControlSet\Control\PriorityControl"
            applied_count = 0
            for item in data:
                irq_num = item.get("IRQ")
                if irq_num is not None:
                    val_name = f"IRQ{irq_num}Priority"
                    if not enable:
                        SystemTweaksService.restore_registry_value_or_default("HKLM", path, val_name, None, winreg.REG_DWORD)
                        applied_count += 1
                    else:
                        logger.info(f"Active display GPU detected on IRQ: {irq_num}")
                        SystemTweaksService.backup_registry_value("HKLM", path, val_name)
                        try:
                            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_WRITE) as key:
                                winreg.SetValueEx(key, val_name, 0, winreg.REG_DWORD, 1)
                            logger.info(f"Set Priority for IRQ {irq_num} to 1 successfully.")
                            applied_count += 1
                        except Exception as ke:
                            logger.debug(f"Failed setting priority for IRQ {irq_num}: {str(ke)}")
            if applied_count == 0:
                logger.warning("No display device IRQs were found to process.")
        except Exception as e:
            logger.error(f"Failed to apply GPU IRQ tweak: {str(e)}")
            raise

    @staticmethod
    def apply_hags_tweak(disable: bool):
        """
        disable = True: 禁用 HAGS (HwSchMode = 1)
        disable = False: 恢复默认 (HwSchMode = 2)
        """
        path = r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers"
        value_name = "HwSchMode"
        SystemTweaksService.backup_registry_value("HKLM", path, value_name)
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_WRITE) as key:
                val = 1 if disable else 2
                winreg.SetValueEx(key, value_name, 0, winreg.REG_DWORD, val)
            logger.info(f"HAGS set to: {'Disabled' if disable else 'Enabled'}.")
        except Exception as e:
            logger.error(f"Failed to apply HAGS tweak: {str(e)}")
            raise


    @staticmethod
    def run_system_cleanup() -> str:
        logger.info("Launching System Cleanup operations...")
        report = []
        
        temp_dir = tempfile.gettempdir()
        win_temp = os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "Temp")
        
        for name, path in [("User Temp Files", temp_dir), ("System Temp Files", win_temp)]:
            try:
                count = 0
                freed = 0
                for root, dirs, files in os.walk(path):
                    for file in files:
                        fpath = os.path.join(root, file)
                        try:
                            sz = os.path.getsize(fpath)
                            os.remove(fpath)
                            freed += sz
                            count += 1
                        except Exception:  # nosec
                            pass
                report.append(f"Cleaned {name}: deleted {count} files ({(freed/(1024*1024)):.2f} MB freed).")
            except Exception as e:
                report.append(f"Failed cleaning {name}: {str(e)}")

        try:
            SystemTweaksService.stop_service("wuauserv", timeout=10)
            SystemTweaksService.stop_service("UsoSvc", timeout=10)
            try:
                soft_dist = os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "SoftwareDistribution")
                if os.path.exists(soft_dist):
                    shutil.rmtree(soft_dist, ignore_errors=True)
                    os.makedirs(soft_dist, exist_ok=True)
                    report.append("Cleared Windows Update Cache (SoftwareDistribution).")
                else:
                    report.append("Windows Update SoftwareDistribution directory not found.")
            finally:
                SystemTweaksService.start_service("wuauserv")
                SystemTweaksService.start_service("UsoSvc")
        except Exception as e:
            report.append(f"Failed clearing Windows Update Cache: {str(e)}")

        try:
            res = SystemTweaksService.safe_subprocess_call(["ipconfig", "/flushdns"], timeout=10)
            if res == 0:
                report.append("Successfully flushed DNS resolver cache.")
            else:
                report.append("Failed flushing DNS cache.")
        except Exception as e:
            report.append(f"DNS Cache flush error: {str(e)}")

        try:
            res = SystemTweaksService.safe_subprocess_call(["lodctr", "/r"], timeout=20)
            if res == 0:
                report.append("Rebuilt performance counters successfully.")
            else:
                report.append("Rebuilt performance counters.")
        except Exception as e:
            report.append(f"Performance counters rebuild error: {str(e)}")

        final_msg = "\n".join(report)
        logger.info(f"System Cleanup complete:\n{final_msg}")
        return final_msg

    @staticmethod
    def get_interface_ip(interface_name: str) -> str:
        try:
            import socket
            addrs = psutil.net_if_addrs()
            if interface_name in addrs:
                for addr in addrs[interface_name]:
                    if addr.family == socket.AF_INET:
                        return addr.address
            return None
        except Exception:
            return None

    @staticmethod
    def get_interface_gateway(interface_name: str) -> str:
        import re
        # 1. Try WMI first as it is completely language-independent and highly reliable
        try:
            import win32com.client
            import pythoncom
            pythoncom.CoInitialize()
            try:
                wmi = win32com.client.GetObject("winmgmts:")
                adapters = wmi.ExecQuery(f"SELECT Index FROM Win32_NetworkAdapter WHERE NetConnectionID = '{interface_name}'")
                for a in adapters:
                    idx = a.Index
                    configs = wmi.ExecQuery(f"SELECT DefaultIPGateway FROM Win32_NetworkAdapterConfiguration WHERE Index = {idx}")
                    for cfg in configs:
                        if cfg.DefaultIPGateway:
                            for gw in cfg.DefaultIPGateway:
                                if gw and gw != "0.0.0.0":
                                    return gw
            finally:
                pythoncom.CoUninitialize()
        except Exception as e:
            logger.debug(f"WMI gateway query failed: {str(e)}")

        # 2. Try netsh config fallback
        try:
            raw_out = SystemTweaksService.safe_subprocess_check_output(
                ["netsh", "int", "ip", "show", "config", f"name={interface_name}"],
                timeout=5
            )
            try:
                output = raw_out.decode("utf-8")
            except Exception:
                output = raw_out.decode("gbk", errors="ignore")
                
            for line in output.splitlines():
                line_lower = line.lower()
                if any(kw in line_lower for kw in ["gateway", "网关", "puerta", "passerelle", "standardgateway", "default", "默认"]):
                    parts = line.split()
                    for part in parts:
                        if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", part):
                            if part != "0.0.0.0":  # nosec
                                return part
        except Exception as e:
            logger.debug(f"netsh gateway query failed: {str(e)}")

        # 3. Try route print fallback
        try:
            ip = SystemTweaksService.get_interface_ip(interface_name)
            if ip:
                raw_out = SystemTweaksService.safe_subprocess_check_output(
                    ["route", "print", "0.0.0.0"],  # nosec
                    timeout=5
                )
                try:
                    output = raw_out.decode("utf-8")
                except Exception:
                    output = raw_out.decode("gbk", errors="ignore")
                
                for line in output.splitlines():
                    parts = line.split()
                    if len(parts) >= 5 and parts[0] == "0.0.0.0" and parts[1] == "0.0.0.0":
                        if parts[3] == ip:
                            gateway = parts[2]
                            if gateway != "0.0.0.0":
                                return gateway
        except Exception as e:
            logger.debug(f"route print gateway query failed: {str(e)}")
            
        return None

    @staticmethod
    def get_network_interfaces() -> list:
        try:
            interfaces = []
            import win32com.client
            import pythoncom
            pythoncom.CoInitialize()
            try:
                wmi = win32com.client.GetObject("winmgmts:")
                adapters = wmi.ExecQuery("SELECT NetConnectionID FROM Win32_NetworkAdapter WHERE NetConnectionID IS NOT NULL")
                for a in adapters:
                    if a.NetConnectionID:
                        interfaces.append(a.NetConnectionID)
                a = None
                adapters = None
                wmi = None
                import gc
                gc.collect()
            finally:
                pythoncom.CoUninitialize()
            if not interfaces:
                import psutil
                interfaces = list(psutil.net_if_addrs().keys())
            return interfaces
        except Exception:
            try:
                import psutil
                return list(psutil.net_if_addrs().keys())
            except Exception:
                return []

    @staticmethod
    def get_network_interfaces_details() -> list:
        details = []
        try:
            import win32com.client
            import pythoncom
            pythoncom.CoInitialize()
            try:
                wmi = win32com.client.GetObject("winmgmts:")
                adapters = wmi.ExecQuery("SELECT Index, NetConnectionID, Description, NetConnectionStatus FROM Win32_NetworkAdapter WHERE NetConnectionID IS NOT NULL")
                configs = wmi.ExecQuery("SELECT Index, SettingID FROM Win32_NetworkAdapterConfiguration")
                
                cfg_map = {c.Index: c.SettingID for c in configs if c.SettingID}
                
                for a in adapters:
                    if a.NetConnectionID:
                        guid = cfg_map.get(a.Index, "")
                        is_connected = (a.NetConnectionStatus == 2)
                        details.append({
                            "name": a.NetConnectionID,
                            "description": a.Description if a.Description else "",
                            "guid": guid,
                            "is_connected": is_connected
                        })
                a = None
                c = None
                adapters = None
                configs = None
                wmi = None
                import gc
                gc.collect()
            finally:
                pythoncom.CoUninitialize()
            return details
        except Exception as e:
            logger.error(f"Error in get_network_interfaces_details: {str(e)}")
            try:
                import psutil
                return [{"name": name, "description": "", "guid": "", "is_connected": True} for name in psutil.net_if_addrs().keys()]
            except Exception:
                return []

    @staticmethod
    def get_interface_mtu(interface_name: str) -> int:
        try:
            raw_output = SystemTweaksService.safe_subprocess_check_output(
                ["netsh", "int", "ipv4", "show", "subinterfaces"],
                timeout=5
            )
            try:
                decoded = raw_output.decode("utf-8")
            except Exception:
                decoded = raw_output.decode("gbk", errors="ignore")
                
            for line in decoded.splitlines():
                parts = line.split()
                if len(parts) >= 5:
                    name = " ".join(parts[4:])
                    if name == interface_name:
                        return int(parts[0])
            return 1500
        except Exception:
            return 1500

    @staticmethod
    def run_mtu_optimization(interface_name: str) -> str:
        interfaces = SystemTweaksService.get_network_interfaces()
        if interface_name not in interfaces:
            err_msg = f"网络诊断提示：未找到指定的物理网卡设备 '{interface_name}'。"
            logger.error(err_msg)
            return err_msg

        current_mtu = SystemTweaksService.get_interface_mtu(interface_name)
        optimal_mtu = current_mtu if current_mtu else 1500

        guid = ""
        desc = ""
        try:
            import win32com.client
            import pythoncom
            pythoncom.CoInitialize()
            try:
                wmi = win32com.client.GetObject("winmgmts:")
                adapters = wmi.ExecQuery(f"SELECT Index, Description FROM Win32_NetworkAdapter WHERE NetConnectionID = '{interface_name}'")
                for a in adapters:
                    desc = a.Description
                    configs = wmi.ExecQuery(f"SELECT SettingID FROM Win32_NetworkAdapterConfiguration WHERE Index = {a.Index}")
                    for c in configs:
                        guid = c.SettingID
                        break
                    break
                a = None
                c = None
                adapters = None
                configs = None
                wmi = None
                import gc
                gc.collect()
            finally:
                pythoncom.CoUninitialize()
        except Exception as ex:
            logger.warning(f"Failed to fetch interface details during MTU optimization: {str(ex)}")

        desc_lower = desc.lower() if desc else ""
        if "wlan" in desc_lower or "wi-fi" in desc_lower or "wireless" in desc_lower or "无线" in interface_name.lower() or "wlan" in interface_name.lower():
            default_optimal = 1500
            adapter_type_desc = "无线网卡 (Wi-Fi)"
        elif "wintun" in desc_lower or "tap" in desc_lower or "tun" in desc_lower or "vpn" in desc_lower or "virtual" in desc_lower or "meta" in desc_lower or "wireguard" in desc_lower:
            default_optimal = 1400
            adapter_type_desc = "虚拟网卡/VPN 隧道"
        else:
            default_optimal = 1500
            adapter_type_desc = "物理以太网卡"

        logger.info(f"Starting MTU size detection for interface: {interface_name} ({adapter_type_desc})")
        
        ip = SystemTweaksService.get_interface_ip(interface_name)
        gateway_ip = SystemTweaksService.get_interface_gateway(interface_name)

        # Build list of potential ping targets, removing duplicates and None
        test_hosts = []
        if gateway_ip:
            test_hosts.append(gateway_ip)
        for host in ["223.5.5.5", "114.114.114.114", "8.8.8.8", "www.baidu.com"]:
            if host not in test_hosts:
                test_hosts.append(host)

        active_target = None
        ping_fail_reason = ""

        # Temporarily elevate local interface MTU to 1500 to allow accurate Path MTU Discovery (PMTUD)
        # Without this, ping -f is artificially constrained by the current local MTU before it even hits the network.
        try:
            SystemTweaksService.safe_subprocess_call(
                ["netsh", "int", "ipv4", "set", "subinterface", interface_name, "mtu=1500", "store=active"],
                timeout=5
            )
            import time
            time.sleep(0.5)  # Give Windows networking stack a moment to apply the active state
        except Exception as e:
            logger.debug(f"Failed to temporarily elevate MTU: {e}")

        if ip:
            # 1. Find a host that responds to ping
            for host in test_hosts:
                try:
                    cmd = ["ping"]
                    if ip:
                        cmd.extend(["-S", ip])
                    cmd.extend(["-l", "64", "-n", "1", host])
                    raw_out = SystemTweaksService.safe_subprocess_check_output(cmd, timeout=2)
                    try:
                        output = raw_out.decode("utf-8")
                    except Exception:
                        output = raw_out.decode("gbk", errors="ignore")

                    if "ttl=" in output.lower():
                        active_target = host
                        logger.info(f"Selected ping target '{host}' for MTU size check.")
                        break
                except Exception as e:
                    logger.debug(f"Target '{host}' ping pre-check failed: {str(e)}")
            
            if not active_target:
                ping_fail_reason = "所测试的全部网关与公网服务器均未响应 ICMP 探测"
        else:
            ping_fail_reason = "网卡处于未连接/媒体断开状态"

        mtu_detected = False
        optimal_size = None

        # 2. Run binary search on active_target
        if active_target:
            low = 1000
            high = 1472
            logger.info("Executing binary search for maximum non-fragmented packet size...")
            while low <= high:
                mid = (low + high) // 2
                try:
                    cmd = ["ping"]
                    if ip:
                        cmd.extend(["-S", ip])
                    cmd.extend(["-l", str(mid), "-f", "-n", "1", active_target])
                    raw_out = SystemTweaksService.safe_subprocess_check_output(cmd, timeout=2)
                    try:
                        output = raw_out.decode("utf-8")
                    except Exception:
                        output = raw_out.decode("gbk", errors="ignore")

                    if "ttl=" in output.lower():
                        logger.debug(f"Ping size {mid} succeeded")
                        optimal_size = mid
                        low = mid + 1
                    else:
                        logger.debug(f"Ping size {mid} failed (needs fragment)")
                        high = mid - 1
                except Exception as e:
                    logger.debug(f"Ping size {mid} encountered error: {str(e)}")
                    high = mid - 1
            
            if optimal_size is not None:
                optimal_mtu = optimal_size + 28
                mtu_detected = True
            else:
                ping_fail_reason = "所有探测大小均未通过不分片测试"

        # 3. Fallback if MTU was not auto-detected
        if not mtu_detected:
            optimal_mtu = default_optimal
            logger.info(f"MTU detection fell back to standard optimal MTU: {optimal_mtu} (Reason: {ping_fail_reason})")

        # 4. Apply MTU using double stack and registry HKLM write
        errors = []
        try:
            cmd_set4 = ["netsh", "int", "ipv4", "set", "subinterface", interface_name, f"mtu={optimal_mtu}", "store=persistent"]
            res = SystemTweaksService.safe_subprocess_call(cmd_set4, timeout=5)
            if res != 0:
                errors.append(f"Netsh IPv4 set failed (exit code {res})")
        except Exception as e:
            errors.append(f"Netsh IPv4 set error: {str(e)}")

        try:
            cmd_set6 = ["netsh", "int", "ipv6", "set", "subinterface", interface_name, f"mtu={optimal_mtu}", "store=persistent"]
            res = SystemTweaksService.safe_subprocess_call(cmd_set6, timeout=5)
            if res != 0:
                logger.debug(f"Netsh IPv6 set returned non-zero: {res}")
        except Exception as e:
            logger.debug(f"Netsh IPv6 set failed: {str(e)}")

        if guid:
            reg_path4 = rf"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces\{guid}"
            try:
                SystemTweaksService.backup_registry_value("HKLM", reg_path4, "MTU")
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path4, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "MTU", 0, winreg.REG_DWORD, optimal_mtu)
                logger.info(f"Registry HKLM\\{reg_path4}\\MTU set to {optimal_mtu}")
            except Exception as e:
                logger.warning(f"Failed to set IPv4 registry MTU: {str(e)}")

            reg_path6 = rf"SYSTEM\CurrentControlSet\Services\Tcpip6\Parameters\Interfaces\{guid}"
            try:
                SystemTweaksService.backup_registry_value("HKLM", reg_path6, "MTU")
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path6, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "MTU", 0, winreg.REG_DWORD, optimal_mtu)
                logger.info(f"Registry HKLM\\{reg_path6}\\MTU set to {optimal_mtu}")
            except Exception as e:
                logger.warning(f"Failed to set IPv6 registry MTU: {str(e)}")

        # 5. Return result message
        if mtu_detected:
            msg = f"网卡 '{interface_name}' MTU 自动探测与配置成功！通过对 {active_target} 进行不断测试，确定最优不分片大小为 {optimal_size} 字节，已成功将 MTU 设定为 {optimal_mtu} 并同步写入 IPv4/IPv6 协议栈与注册表配置。"
        else:
            msg = f"网卡 '{interface_name}' ({adapter_type_desc}) MTU 调优成功！由于{ping_fail_reason}，已自动配置标准最优 MTU ({optimal_mtu}) 并同步写入 IPv4/IPv6 协议栈与注册表配置。"

        if errors and not mtu_detected:
            err_msg = f"设置网卡 '{interface_name}' MTU 失败: " + "; ".join(errors)
            logger.error(err_msg)
            return err_msg

        logger.info(msg)
        return msg

    @staticmethod
    def set_timer_resolution_active(active: bool):
        if not winmm or not ntdll:
            return
        try:
            if active:
                winmm.timeBeginPeriod(1)
                min_r = ctypes.c_ulong()
                max_r = ctypes.c_ulong()
                cur_r = ctypes.c_ulong()
                # max_r actually represents the minimum possible timer interval (i.e. highest resolution)
                if ntdll.NtQueryTimerResolution(ctypes.byref(min_r), ctypes.byref(max_r), ctypes.byref(cur_r)) == 0:
                    optimal_res = max_r.value if max_r.value > 0 else 5000
                else:
                    optimal_res = 5000
                
                prev = ctypes.c_ulong()
                ntdll.NtSetTimerResolution(optimal_res, True, ctypes.byref(prev))
                logger.info(f"Set active system timer resolution to optimal: {optimal_res / 10000.0}ms")
            else:
                winmm.timeEndPeriod(1)
                prev = ctypes.c_ulong()
                ntdll.NtSetTimerResolution(156250, False, ctypes.byref(prev))
                logger.info("Restored system timer resolution to default")
        except Exception as e:
            logger.debug(f"Failed to adjust active timer resolution: {str(e)}")

    @staticmethod
    def apply_dns_tweak(enable: bool):
        path = r"SYSTEM\CurrentControlSet\Services\Tcpip\ServiceProvider"
        if not enable:
            SystemTweaksService.restore_registry_value_or_default("HKLM", path, "Class", 8, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path, "DnsPriority", 2000, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path, "HostsPriority", 500, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path, "LocalPriority", 499, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path, "NetbtPriority", 2001, winreg.REG_DWORD)
            return
            
        logger.info("Applying DNS/Hosts resolution prioritization...")
        for v in ["Class", "DnsPriority", "HostsPriority", "LocalPriority", "NetbtPriority"]:
            SystemTweaksService.backup_registry_value("HKLM", path, v)
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "Class", 0, winreg.REG_DWORD, 8)
                winreg.SetValueEx(key, "DnsPriority", 0, winreg.REG_DWORD, 6)
                winreg.SetValueEx(key, "HostsPriority", 0, winreg.REG_DWORD, 5)
                winreg.SetValueEx(key, "LocalPriority", 0, winreg.REG_DWORD, 4)
                winreg.SetValueEx(key, "NetbtPriority", 0, winreg.REG_DWORD, 7)
            logger.info("DNS/Hosts resolution prioritization applied.")
        except Exception as e:
            logger.error(f"Failed to apply DNS priority tweak: {str(e)}")
            raise


    @staticmethod
    def apply_feeds_and_tips_tweak(enable: bool):
        path_feeds = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Feeds"
        path_tips = r"SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"
        if not enable:
            SystemTweaksService.restore_registry_value_or_default("HKCU", path_feeds, "ShellFeedsTaskbarEnabled", 0, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKCU", path_tips, "SoftLandingEnabled", 1, winreg.REG_DWORD)
            return
            
        logger.info("Disabling Windows Feeds and SoftLanding tips...")
        SystemTweaksService.backup_registry_value("HKCU", path_feeds, "ShellFeedsTaskbarEnabled")
        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path_feeds, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "ShellFeedsTaskbarEnabled", 0, winreg.REG_DWORD, 2)
        except Exception as e:
            logger.debug(f"Failed to disable Feeds: {str(e)}")

        SystemTweaksService.backup_registry_value("HKCU", path_tips, "SoftLandingEnabled")
        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path_tips, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "SoftLandingEnabled", 0, winreg.REG_DWORD, 0)
            logger.info("Windows Feeds and SoftLanding tips disabled.")
        except Exception as e:
            logger.debug(f"Failed to disable tips: {str(e)}")


    @staticmethod
    def apply_desktop_heap_tweak(enable: bool):
        path = r"SYSTEM\CurrentControlSet\Control\Session Manager\SubSystems"
        if not enable:
            val, val_type = SystemTweaksService.read_registry_value(winreg.HKEY_LOCAL_MACHINE, path, "Windows")
            if val and "SharedSection=" in val:
                parts = val.split("SharedSection=")
                subparts = parts[1].split()
                shared_section_val = subparts[0]
                new_shared_section = "1024,20480,768"
                new_val = val.replace(f"SharedSection={shared_section_val}", f"SharedSection={new_shared_section}")
                SystemTweaksService.restore_registry_value_or_default("HKLM", path, "Windows", new_val, val_type)
            return
            
        logger.info("Increasing Desktop Heap SharedSection limits...")
        SystemTweaksService.backup_registry_value("HKLM", path, "Windows")
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
                val, val_type = winreg.QueryValueEx(key, "Windows")
                if "SharedSection=" in val:
                    parts = val.split("SharedSection=")
                    subparts = parts[1].split()
                    shared_section_val = subparts[0]
                    new_shared_section = "4096,8192,4096"
                    new_val = val.replace(f"SharedSection={shared_section_val}", f"SharedSection={new_shared_section}")
                    winreg.SetValueEx(key, "Windows", 0, val_type, new_val)
                    logger.info(f"Updated SharedSection to {new_shared_section} in HKLM\\{path}\\Windows")
        except Exception as e:
            logger.error(f"Failed to apply Desktop Heap tweak: {str(e)}")
            raise


    @staticmethod
    def apply_uac_tweak(enable: bool):
        path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
        if not enable:
            SystemTweaksService.restore_registry_value_or_default("HKLM", path, "EnableLUA", 1, winreg.REG_DWORD)
            return
            
        logger.info("Disabling User Account Control (UAC)...")
        SystemTweaksService.backup_registry_value("HKLM", path, "EnableLUA")
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "EnableLUA", 0, winreg.REG_DWORD, 0)
            logger.info("UAC disabled successfully.")
        except Exception as e:
            logger.error(f"Failed to disable UAC: {str(e)}")
            raise


    @staticmethod
    def apply_maps_download_tweak(enable: bool):
        if not enable:
            SystemTweaksService.restore_service_or_default("MapsBroker", SERVICE_AUTO_START)
            return
            
        logger.info("Disabling MapsBroker downloaded maps manager service...")
        SystemTweaksService.backup_service("MapsBroker")
        SystemTweaksService.set_service_start_type("MapsBroker", SERVICE_DISABLED)


    @staticmethod
    def apply_bg_apps_and_updates_tweak(enable: bool):
        path_bg = r"Software\Microsoft\Windows\CurrentVersion\BackgroundAccessApplications"
        path_search = r"Software\Microsoft\Windows\CurrentVersion\Search"
        path_maps = r"SOFTWARE\Policies\Microsoft\Windows\Maps"
        
        if not enable:
            SystemTweaksService.restore_registry_value_or_default("HKCU", path_bg, "GlobalUserDisabled", 0, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKCU", path_search, "BackgroundAppGlobalToggle", 1, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_maps, "AutoDownloadAndUpdateMapData", None, winreg.REG_DWORD)
            return
            
        logger.info("Disabling background app execution and map updates...")
        SystemTweaksService.backup_registry_value("HKCU", path_bg, "GlobalUserDisabled")
        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path_bg, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "GlobalUserDisabled", 0, winreg.REG_DWORD, 1)
        except Exception as e:
            logger.debug(f"Failed to disable HKCU background access: {str(e)}")

        SystemTweaksService.backup_registry_value("HKCU", path_search, "BackgroundAppGlobalToggle")
        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path_search, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "BackgroundAppGlobalToggle", 0, winreg.REG_DWORD, 0)
        except Exception as e:
            logger.debug(f"Failed to disable HKCU Search background toggle: {str(e)}")

        SystemTweaksService.backup_registry_value("HKLM", path_maps, "AutoDownloadAndUpdateMapData")
        try:
            with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_maps, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "AutoDownloadAndUpdateMapData", 0, winreg.REG_DWORD, 0)
            logger.info("Background apps and maps auto-updates disabled.")
        except Exception as e:
            logger.debug(f"Failed to disable AutoDownloadAndUpdateMapData policy: {str(e)}")


    @staticmethod
    def apply_autoshare_tweak(enable: bool):
        path = r"SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters"
        if not enable:
            SystemTweaksService.restore_registry_value_or_default("HKLM", path, "AutoShareServer", 1, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path, "AutoShareWks", 1, winreg.REG_DWORD)
            return
            
        logger.info("Disabling administrative AutoShares...")
        SystemTweaksService.backup_registry_value("HKLM", path, "AutoShareServer")
        SystemTweaksService.backup_registry_value("HKLM", path, "AutoShareWks")
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "AutoShareServer", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "AutoShareWks", 0, winreg.REG_DWORD, 0)
            logger.info("Administrative AutoShares disabled.")
        except Exception as e:
            logger.error(f"Failed to disable AutoShare: {str(e)}")
            raise


    @staticmethod
    def apply_autorun_tweak(enable: bool):
        path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer"
        if not enable:
            SystemTweaksService.restore_registry_value_or_default("HKCU", path, "NoDriveTypeAutoRun", 0x91, winreg.REG_DWORD)
            return
            
        logger.info("Disabling drive AutoRun...")
        SystemTweaksService.backup_registry_value("HKCU", path, "NoDriveTypeAutoRun")
        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "NoDriveTypeAutoRun", 0, winreg.REG_DWORD, 0xff)
            logger.info("Drive AutoRun disabled.")
        except Exception as e:
            logger.error(f"Failed to disable AutoRun: {str(e)}")
            raise


    @staticmethod
    def apply_mouse_latency_tweak(enable: bool):
        path = r"Control Panel\Mouse"
        if not enable:
            SystemTweaksService.restore_registry_value_or_default("HKCU", path, "MouseSensitivity", "10", winreg.REG_SZ)
            SystemTweaksService.restore_registry_value_or_default("HKCU", path, "MouseSpeed", "1", winreg.REG_SZ)
            SystemTweaksService.restore_registry_value_or_default("HKCU", path, "MouseThreshold1", "6", winreg.REG_SZ)
            SystemTweaksService.restore_registry_value_or_default("HKCU", path, "MouseThreshold2", "10", winreg.REG_SZ)
            for curve in ["SmoothMouseXCurve", "SmoothMouseYCurve"]:
                SystemTweaksService.restore_registry_value_or_default("HKCU", path, curve, None, winreg.REG_BINARY)
            return
            
        logger.info("Applying mouse delay reduction and 1-1 smooth curves...")
        for v in ["MouseSensitivity", "MouseSpeed", "MouseThreshold1", "MouseThreshold2", "SmoothMouseXCurve", "SmoothMouseYCurve"]:
            SystemTweaksService.backup_registry_value("HKCU", path, v)
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "MouseSensitivity", 0, winreg.REG_SZ, "10")
                winreg.SetValueEx(key, "MouseSpeed", 0, winreg.REG_SZ, "0")
                winreg.SetValueEx(key, "MouseThreshold1", 0, winreg.REG_SZ, "0")
                winreg.SetValueEx(key, "MouseThreshold2", 0, winreg.REG_SZ, "0")
            
                x_curve = bytes.fromhex("0000000000000000c0cc0c000000000000001a0000000000000038000000000000005c000000000000008c0000000000")
                y_curve = bytes.fromhex("000000000000000000000a00000000000000280000000000000050000000000000007c00000000000000b00000000000")
            
                winreg.SetValueEx(key, "SmoothMouseXCurve", 0, winreg.REG_BINARY, x_curve)
                winreg.SetValueEx(key, "SmoothMouseYCurve", 0, winreg.REG_BINARY, y_curve)
            logger.info("Mouse response tweaks applied successfully.")
        except Exception as e:
            logger.error(f"Failed to apply mouse delay tweaks: {str(e)}")
            raise


    @staticmethod
    def apply_config_alloc_tweak(enable: bool):
        path = r"SYSTEM\CurrentControlSet\Control\FileSystem"
        if not enable:
            SystemTweaksService.restore_registry_value_or_default("HKLM", path, "ConfigFileAllocSize", None, winreg.REG_DWORD)
            return
            
        logger.info("Increasing ConfigFileAllocSize registry buffer...")
        SystemTweaksService.backup_registry_value("HKLM", path, "ConfigFileAllocSize")
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "ConfigFileAllocSize", 0, winreg.REG_DWORD, 0x1f4)
            logger.info("ConfigFileAllocSize increased.")
        except Exception as e:
            logger.error(f"Failed to apply config alloc tweak: {str(e)}")
            raise


    @staticmethod
    def apply_gpu_firmware_tweak(enable: bool):
        path_class = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
        if not enable:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_class, 0, winreg.KEY_READ) as root_key:
                    i = 0
                    while True:
                        try:
                            sub = winreg.EnumKey(root_key, i)
                            if sub.isdigit():
                                sub_path = f"{path_class}\\{sub}"
                                SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, "EnableGpuFirmware", None, winreg.REG_DWORD)
                            i += 1
                        except OSError:
                            break
                logger.info("GPU Firmware DSP acceleration disabled.")
            except Exception as e:
                logger.error(f"Failed to restore GPU firmware tweak: {str(e)}")
                raise
            return
            
        logger.info("Enabling GPU Firmware DSP acceleration...")
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_class, 0, winreg.KEY_READ) as root_key:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(root_key, i)
                        if sub.isdigit():
                            sub_path = f"{path_class}\\{sub}"
                            SystemTweaksService.backup_registry_value("HKLM", sub_path, "EnableGpuFirmware")
                            try:
                                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub_path, 0, winreg.KEY_WRITE) as k:
                                    winreg.SetValueEx(k, "EnableGpuFirmware", 0, winreg.REG_DWORD, 1)
                            except Exception:  # nosec
                                pass
                        i += 1
                    except OSError:
                        break
            logger.info("GPU Firmware DSP acceleration enabled.")
        except Exception as e:
            logger.error(f"Failed to enable GPU firmware tweak: {str(e)}")
            raise


    @staticmethod
    def apply_memory_compression_tweak(disable: bool):
        action = "Disable" if disable else "Enable"
        try:
            total_ram_gb = round(psutil.virtual_memory().total / (1024**3))
            if disable:
                if total_ram_gb < 16:
                    logger.warning(f"系统运行内存较小 ({total_ram_gb}GB < 16GB)，为防止大负荷下发生内存溢出(OOM)，强制保持内存压缩开启。")
                    return
                logger.info(f"检测到系统内存为 {total_ram_gb}GB，符合高内存配置，正在禁用内存压缩...")
            else:
                logger.info("正在启用系统内存压缩机制...")
                
            wmi_success = False
            if HAS_WIN32:
                try:
                    import pythoncom
                    import win32com.client
                    pythoncom.CoInitialize()
                    try:
                        wmi_mma = win32com.client.GetObject("winmgmts:{impersonationLevel=impersonate}!\\\\.\\root\\Microsoft\\Windows\\PS_MMAgent")
                        cls = wmi_mma.Get("PS_MMAgent")
                        method_name = "Disable" if disable else "Enable"
                        in_params = cls.Methods_(method_name).InParameters.SpawnInstance_()
                        in_params.Properties_("MemoryCompression").Value = True
                        cls.ExecMethod_(method_name, in_params)
                        wmi_success = True
                        logger.info(f"内存压缩机制已通过 direct WMI COM 成功应用设定: {action}")
                    finally:
                        pythoncom.CoUninitialize()
                except Exception as wmi_err:
                    err_str = str(wmi_err)
                    if "SWbemObjectEx" in err_str or "2147217407" in err_str or "0x8004101f" in err_str.lower():
                        logger.debug(f"Direct WMI memory compression interface is not supported on this Windows SKU (expected, falling back to powershell): {err_str}")
                    else:
                        logger.warning(f"无法通过 direct WMI COM 配置内存压缩，将尝试 PowerShell 降级方案: {err_str}")
            
            # PowerShell fallback
            if not wmi_success:
                try:
                    logger.info(f"正在尝试使用 PowerShell {action}-MMAgent -MemoryCompression 降级方案...")
                    cmd = [
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        f"{action}-MMAgent -MemoryCompression -ErrorAction Stop"
                    ]
                    res = SystemTweaksService.safe_subprocess_call(cmd)
                    if res == 0:
                        wmi_success = True
                        logger.info(f"内存压缩机制已通过 PowerShell 成功应用设定: {action}")
                    else:
                        logger.warning(f"PowerShell 内存压缩配置执行未成功，返回值: {res}")
                except Exception as ps_err:
                    logger.warning(f"PowerShell 内存压缩配置执行遇到异常: {str(ps_err)}")

            if not wmi_success:
                logger.warning("当前 Windows 系统 SKU 不支持或未启用 MMAgent 内存压缩机制，已忽略此项优化。")
                
            try:
                from core_commander.core.worker import SystemStateScannerWorker
                SystemStateScannerWorker._memory_comp_cache = None
            except Exception:  # nosec
                pass
        except Exception as e:
            logger.warning(f"内存压缩优化设定未完全应用: {str(e)}")

    @staticmethod
    def apply_game_priority_tweak(enable: bool, target_exe_name: str = None):
        games = ["NarakaBladepoint.exe", "Naraka.exe"]
        if target_exe_name:
            exe_clean = os.path.basename(target_exe_name)
            if exe_clean and exe_clean.endswith(".exe") and exe_clean not in games:
                games.append(exe_clean)
                
        if not enable:
            for game in games:
                path = f"SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Image File Execution Options\\{game}\\PerfOptions"
                SystemTweaksService.restore_registry_value_or_default("HKLM", path, "CpuPriorityClass", None, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path, "IoPriority", None, winreg.REG_DWORD)
            return
            
        logger.info(f"Registering target games high CPU and IO priority PerfOptions: {games}...")
        for game in games:
            path = f"SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Image File Execution Options\\{game}\\PerfOptions"
            SystemTweaksService.backup_registry_value("HKLM", path, "CpuPriorityClass")
            SystemTweaksService.backup_registry_value("HKLM", path, "IoPriority")
            try:
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "CpuPriorityClass", 0, winreg.REG_DWORD, 3)
                    winreg.SetValueEx(key, "IoPriority", 0, winreg.REG_DWORD, 3)
                logger.info(f"Registered priority PerfOptions for {game}.")
            except Exception as e:
                logger.debug(f"Failed to register PerfOptions for {game}: {str(e)}")

    @staticmethod
    def apply_gpu_pstate_tweak(enable: bool):
        path_class = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
        if not enable:
            try:
                GpuSmiService.lock_gpu_clocks(False)
            except Exception as e:
                logger.warning(f"Failed to reset GPU clocks: {e}")
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_class, 0, winreg.KEY_READ) as root_key:
                    i = 0
                    while True:
                        try:
                            sub = winreg.EnumKey(root_key, i)
                            if sub.isdigit():
                                sub_path = f"{path_class}\\{sub}"
                                SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, "DisableDynamicPstate", None, winreg.REG_DWORD)
                            i += 1
                        except OSError:
                            break
                logger.info("NVIDIA GPU Force PState 0 and clock lock disabled.")
            except Exception as e:
                logger.error(f"Failed to restore GPU pstate tweak: {str(e)}")
                raise
            return
            
        logger.info("Enabling NVIDIA GPU Force PState 0 (DisableDynamicPstate = 1) and locking clocks...")
        try:
            GpuSmiService.lock_gpu_clocks(True)
            GpuSmiService.optimize_vram()
        except Exception as e:
            logger.warning(f"Failed to lock GPU clocks: {e}")
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_class, 0, winreg.KEY_READ) as root_key:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(root_key, i)
                        if sub.isdigit():
                            sub_path = f"{path_class}\\{sub}"
                            SystemTweaksService.backup_registry_value("HKLM", sub_path, "DisableDynamicPstate")
                            try:
                                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub_path, 0, winreg.KEY_WRITE) as k:
                                    winreg.SetValueEx(k, "DisableDynamicPstate", 0, winreg.REG_DWORD, 1)
                            except Exception:  # nosec
                                pass
                        i += 1
                    except OSError:
                        break
            logger.info("NVIDIA GPU Force PState 0 and clock lock enabled.")
        except Exception as e:
            logger.error(f"Failed to enable GPU pstate tweak: {str(e)}")
            raise

    @staticmethod
    def apply_windows_visual_effects(disable: bool):
        """
        disable = True: 减少视觉效果 (对齐图一自定义预设)
        disable = False: 恢复默认
        """
        path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects"
        path_metrics = r"Control Panel\Desktop\WindowMetrics"
        path_adv = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
        
        SystemTweaksService.backup_registry_value("HKCU", path, "VisualFXSetting")
        SystemTweaksService.backup_registry_value("HKCU", "Control Panel\Desktop", "UserPreferencesMask")
        SystemTweaksService.backup_registry_value("HKCU", path_metrics, "MinAnimate")
        SystemTweaksService.backup_registry_value("HKCU", path_adv, "TaskbarAnimations")
        SystemTweaksService.backup_registry_value("HKCU", path_adv, "IconsOnly")
        SystemTweaksService.backup_registry_value("HKCU", path_adv, "ListviewShadow")
        SystemTweaksService.backup_registry_value("HKCU", "Control Panel\Desktop", "DragFullWindows")
        SystemTweaksService.backup_registry_value("HKCU", "Control Panel\Desktop", "FontSmoothing")
        SystemTweaksService.backup_registry_value("HKCU", "Control Panel\Desktop", "FontSmoothingType")
        
        try:
            if disable:
                # Set VisualFXSetting to 3 (Custom)
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "VisualFXSetting", 0, winreg.REG_DWORD, 3)
                
                # Disable window minimization/maximization animations
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path_metrics, 0, winreg.KEY_WRITE) as key_metrics:
                    winreg.SetValueEx(key_metrics, "MinAnimate", 0, winreg.REG_SZ, "0")
                
                # Disable taskbar animations, but keep Thumbnails and Drop Shadow enabled
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path_adv, 0, winreg.KEY_WRITE) as key_adv:
                    winreg.SetValueEx(key_adv, "TaskbarAnimations", 0, winreg.REG_DWORD, 0)
                    winreg.SetValueEx(key_adv, "IconsOnly", 0, winreg.REG_DWORD, 0) # 显示缩略图，而不是显示图标
                    winreg.SetValueEx(key_adv, "ListviewShadow", 0, winreg.REG_DWORD, 1) # 在桌面上为图标标签使用阴影
                
                # DragFullWindows=1, FontSmoothing=2, FontSmoothingType=2
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Control Panel\Desktop", 0, winreg.KEY_WRITE) as key_desktop:
                    winreg.SetValueEx(key_desktop, "DragFullWindows", 0, winreg.REG_SZ, "1") # 拖动时显示窗口内容
                    winreg.SetValueEx(key_desktop, "FontSmoothing", 0, winreg.REG_SZ, "2") # 平滑屏幕字体边缘
                    winreg.SetValueEx(key_desktop, "FontSmoothingType", 0, winreg.REG_DWORD, 2)
                
                    # UserPreferencesMask (leaves ClearType font smoothing enabled and disables other window/menu transitions/fades)
                    mask_perf = bytes.fromhex("9012038010000000")
                    winreg.SetValueEx(key_desktop, "UserPreferencesMask", 0, winreg.REG_BINARY, mask_perf)
                
                logger.info("Windows visual effects reduced (aligned to Custom preset in Image 1).")
            else:
                SystemTweaksService.restore_registry_value_or_default("HKCU", path, "VisualFXSetting", 0, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKCU", path_metrics, "MinAnimate", "1", winreg.REG_SZ)
                SystemTweaksService.restore_registry_value_or_default("HKCU", path_adv, "TaskbarAnimations", 1, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKCU", path_adv, "IconsOnly", 0, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKCU", path_adv, "ListviewShadow", 1, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKCU", "Control Panel\Desktop", "DragFullWindows", "1", winreg.REG_SZ)
                SystemTweaksService.restore_registry_value_or_default("HKCU", "Control Panel\Desktop", "FontSmoothing", "2", winreg.REG_SZ)
                SystemTweaksService.restore_registry_value_or_default("HKCU", "Control Panel\Desktop", "FontSmoothingType", 2, winreg.REG_DWORD)
                
                # Restore original UserPreferencesMask
                mask_default = bytes.fromhex("9e1e078012000000")
                SystemTweaksService.restore_registry_value_or_default("HKCU", "Control Panel\Desktop", "UserPreferencesMask", mask_default, winreg.REG_BINARY)
                
                logger.info("Windows visual effects restored to default.")
        except Exception as e:
            logger.error(f"Failed to apply/restore visual effects tweak: {str(e)}")
            raise

    @staticmethod
    def apply_windows_transparency(disable: bool):
        """
        disable = True: 禁用透明度
        disable = False: 恢复默认
        """
        path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        value_name = "EnableTransparency"
        SystemTweaksService.backup_registry_value("HKCU", path, value_name)
        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_WRITE) as key:
                val = 0 if disable else 1
                winreg.SetValueEx(key, value_name, 0, winreg.REG_DWORD, val)
            logger.info(f"Windows transparency set to: {'Disabled' if disable else 'Enabled'}.")
        except Exception as e:
            logger.error(f"Failed to apply transparency tweak: {str(e)}")
            raise

    @staticmethod
    def apply_copilot(disable: bool):
        """
        disable = True: 禁用 Copilot
        disable = False: 恢复默认
        """
        paths = [
            ("HKCU", r"Software\Policies\Microsoft\Windows\WindowsCopilot"),
            ("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\WindowsCopilot"),
            ("HKCU", r"Software\Policies\Microsoft\Windows\WindowsAI"),
            ("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\WindowsAI")
        ]
        value_name = "TurnOffWindowsCopilot"
        
        for hkey_name, path in paths:
            SystemTweaksService.backup_registry_value(hkey_name, path, value_name)
            
        try:
            for hkey_name, path in paths:
                hkey_root = winreg.HKEY_LOCAL_MACHINE if hkey_name == "HKLM" else winreg.HKEY_CURRENT_USER
                if disable:
                    with winreg.CreateKeyEx(hkey_root, path, 0, winreg.KEY_WRITE) as key:
                        winreg.SetValueEx(key, value_name, 0, winreg.REG_DWORD, 1)
                else:
                    try:
                        with winreg.OpenKey(hkey_root, path, 0, winreg.KEY_WRITE) as key:
                            winreg.DeleteValue(key, value_name)
                    except FileNotFoundError:
                        pass
                    except PermissionError:
                        try:
                            with winreg.OpenKey(hkey_root, path, 0, winreg.KEY_WRITE) as key:
                                winreg.SetValueEx(key, value_name, 0, winreg.REG_DWORD, 0)
                        except Exception:  # nosec
                            pass
            
            # Hide/Show Copilot taskbar icon for the current user
            explorer_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
            SystemTweaksService.backup_registry_value("HKCU", explorer_path, "ShowCopilotButton")
            try:
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, explorer_path, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "ShowCopilotButton", 0, winreg.REG_DWORD, 0 if disable else 1)
            except Exception:
                pass
            logger.info(f"Windows Copilot set to: {'Disabled' if disable else 'Enabled'}.")
        except Exception as e:
            logger.error(f"Failed to apply Copilot tweak: {str(e)}")
            raise

    @staticmethod
    def apply_security_notifications(disable: bool):
        """
        disable = True: 禁用烦人的安全通知
        disable = False: 恢复默认
        """
        path_sec = r"SOFTWARE\Policies\Microsoft\Windows Defender Security Center\Notifications"
        path_toast = r"Software\Microsoft\Windows\CurrentVersion\Notifications\Settings\Windows.SystemToast.SecurityAndMaintenance"
        
        SystemTweaksService.backup_registry_value("HKLM", path_sec, "DisableNotifications")
        SystemTweaksService.backup_registry_value("HKLM", path_sec, "DisableEnhancedNotifications")
        SystemTweaksService.backup_registry_value("HKCU", path_toast, "Enabled")
        
        try:
            if disable:
                # Disable Defender notifications
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_sec, 0, winreg.KEY_WRITE) as key_sec:
                    winreg.SetValueEx(key_sec, "DisableNotifications", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(key_sec, "DisableEnhancedNotifications", 0, winreg.REG_DWORD, 1)
                
                # Disable Toast notifications for Security and Maintenance
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path_toast, 0, winreg.KEY_WRITE) as key_toast:
                    winreg.SetValueEx(key_toast, "Enabled", 0, winreg.REG_DWORD, 0)
                logger.info("Windows Security and Maintenance notifications disabled.")
            else:
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_sec, "DisableNotifications", None, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_sec, "DisableEnhancedNotifications", None, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKCU", path_toast, "Enabled", 1, winreg.REG_DWORD)
                logger.info("Windows Security notifications restored to default.")
        except Exception as e:
            logger.error(f"Failed to apply security notifications tweak: {str(e)}")
            raise

    @staticmethod
    def apply_defender(disable: bool):
        """
        disable = True: 禁用 Windows Defender (反病毒)
        disable = False: 恢复默认
        """
        path_policy = r"SOFTWARE\Policies\Microsoft\Windows Defender"
        path_rt = r"SOFTWARE\Policies\Microsoft\Windows Defender\Real-Time Protection"
        
        SystemTweaksService.backup_registry_value("HKLM", path_policy, "DisableAntiSpyware")
        SystemTweaksService.backup_registry_value("HKLM", path_policy, "DisableRealtimeMonitoring")
        SystemTweaksService.backup_registry_value("HKLM", path_rt, "DisableRealtimeMonitoring")
        
        try:
            if disable:
                # 1. Update Registry Policies
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_policy, 0, winreg.KEY_WRITE) as key_policy:
                    winreg.SetValueEx(key_policy, "DisableAntiSpyware", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(key_policy, "DisableRealtimeMonitoring", 0, winreg.REG_DWORD, 1)
                
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_rt, 0, winreg.KEY_WRITE) as key_rt:
                    winreg.SetValueEx(key_rt, "DisableRealtimeMonitoring", 0, winreg.REG_DWORD, 1)
                
                # 2. Run PowerShell commands in one consolidated call to turn off monitoring
                try:
                    cmd = "Set-MpPreference -DisableRealtimeMonitoring $true -DisableBehaviorMonitoring $true -DisableIOAVProtection $true -SubmitSamplesConsent 2 -MAPSReporting 0"
                    SystemTweaksService.safe_subprocess_call(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", cmd], timeout=5)
                except Exception as ps_err:
                    logger.warning(f"Consolidated Defender Disable command failed: {str(ps_err)}")
                
                logger.info("Windows Defender Antivirus policies and real-time monitoring disabled.")
            else:
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_policy, "DisableAntiSpyware", None, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_policy, "DisableRealtimeMonitoring", None, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_rt, "DisableRealtimeMonitoring", None, winreg.REG_DWORD)
                
                # Restore default Defender settings in one consolidated call
                try:
                    cmd = "Set-MpPreference -DisableRealtimeMonitoring $false -DisableBehaviorMonitoring $false -DisableIOAVProtection $false -SubmitSamplesConsent 0 -MAPSReporting 2"
                    SystemTweaksService.safe_subprocess_call(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", cmd], timeout=5)
                except Exception as ps_err:
                    logger.warning(f"Consolidated Defender Restore command failed: {str(ps_err)}")
                        
                logger.info("Windows Defender Antivirus settings restored to default.")
            return True
        except Exception as e:
            logger.error(f"Failed to apply Windows Defender tweak: {str(e)}")
            raise

    @staticmethod
    def apply_smartscreen(disable: bool):
        """
        disable = True: 禁用智能屏幕 SmartScreen
        disable = False: 恢复默认
        """
        path_explorer = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer"
        path_sys = r"SOFTWARE\Policies\Microsoft\Windows\System"
        path_apphost = r"Software\Microsoft\Windows\CurrentVersion\AppHost"
        
        SystemTweaksService.backup_registry_value("HKLM", path_explorer, "SmartScreenEnabled")
        SystemTweaksService.backup_registry_value("HKLM", path_sys, "EnableSmartScreen")
        SystemTweaksService.backup_registry_value("HKLM", path_sys, "ShellSmartScreenLevel")
        SystemTweaksService.backup_registry_value("HKCU", path_apphost, "EnableWebContentEvaluation")
        
        try:
            if disable:
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_explorer, 0, winreg.KEY_WRITE) as key_exp:
                    winreg.SetValueEx(key_exp, "SmartScreenEnabled", 0, winreg.REG_SZ, "Off")
                
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_sys, 0, winreg.KEY_WRITE) as key_sys:
                    winreg.SetValueEx(key_sys, "EnableSmartScreen", 0, winreg.REG_DWORD, 0)
                    winreg.SetValueEx(key_sys, "ShellSmartScreenLevel", 0, winreg.REG_SZ, "Off")
                
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path_apphost, 0, winreg.KEY_WRITE) as key_app:
                    winreg.SetValueEx(key_app, "EnableWebContentEvaluation", 0, winreg.REG_DWORD, 0)
                
                # Also invoke Set-MpPreference in PowerShell to disable SmartScreen in Defender policies
                try:
                    import subprocess
                    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", "Set-MpPreference -EnableSmartScreen $false"], capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
                except Exception as e:
                    logger.debug(f"Failed to set MpPreference EnableSmartScreen to false: {e}")
                
                logger.info("Windows SmartScreen security scanners disabled.")
            else:
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_explorer, "SmartScreenEnabled", "RequireAdmin", winreg.REG_SZ)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_sys, "EnableSmartScreen", None, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_sys, "ShellSmartScreenLevel", None, winreg.REG_SZ)
                SystemTweaksService.restore_registry_value_or_default("HKCU", path_apphost, "EnableWebContentEvaluation", 1, winreg.REG_DWORD)
                logger.info("Windows SmartScreen restored to default.")
            return True
        except Exception as e:
            logger.error(f"Failed to apply SmartScreen tweak: {str(e)}")
            raise

    @staticmethod
    def apply_firewall(disable: bool):
        """
        disable = True: 禁用 Windows 防火墙
        disable = False: 恢复默认
        """
        profiles = [
            r"SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters\FirewallPolicy\StandardProfile",
            r"SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters\FirewallPolicy\PublicProfile",
            r"SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters\FirewallPolicy\DomainProfile"
        ]
        for p in profiles:
            SystemTweaksService.backup_registry_value("HKLM", p, "EnableFirewall")
            
        try:
            # 1. Update Registry Policies
            for p in profiles:
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, p, 0, winreg.KEY_WRITE) as key:
                    val = 0 if disable else 1
                    winreg.SetValueEx(key, "EnableFirewall", 0, winreg.REG_DWORD, val)
            
            # 2. Run netsh command to immediately toggle state
            state_str = "off" if disable else "on"
            SystemTweaksService.safe_subprocess_call(["netsh.exe", "advfirewall", "set", "allprofiles", "state", state_str])
            logger.info(f"Windows Defender Firewall successfully configured to: {'Disabled' if disable else 'Enabled'} via netsh.")
            return True
        except Exception as e:
            logger.error(f"Failed to apply Firewall tweak: {str(e)}")
            raise

    @staticmethod
    def apply_driver_priority_tweak(enable: bool):
        drivers = [
            (r"SYSTEM\CurrentControlSet\Services\usbxhci\Parameters", "ThreadPriority"),
            (r"SYSTEM\CurrentControlSet\Services\USBHUB3\Parameters", "ThreadPriority"),
            (r"SYSTEM\CurrentControlSet\Services\NDIS\Parameters", "ThreadPriority"),
            (r"SYSTEM\CurrentControlSet\Services\nvlddmkm\Parameters", "ThreadPriority")
        ]
        
        gpu_energy_path = r"SYSTEM\CurrentControlSet\Services\GpuEnergyDrv"
        cppc_path = r"SYSTEM\CurrentControlSet\Control\Power\PowerSettings\54533251-82be-4824-96c1-47b60b740d00\943c8cb6-6f93-4227-ad87-e9a3feec08d1"
        
        if not enable:
            for path, name in drivers:
                SystemTweaksService.restore_registry_value_or_default("HKLM", path, name, None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", gpu_energy_path, "Start", 3, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", cppc_path, "Attributes", 1, winreg.REG_DWORD)
            logger.info("Restored system driver thread priorities, GPU energy driver service, and CPPC configuration.")
            return

        for path, name in drivers:
            SystemTweaksService.backup_registry_value("HKLM", path, name)
        SystemTweaksService.backup_registry_value("HKLM", gpu_energy_path, "Start")
        SystemTweaksService.backup_registry_value("HKLM", cppc_path, "Attributes")

        try:
            for path, name in drivers:
                try:
                    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_WRITE) as key:
                        winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 0xf)
                except Exception as e:
                    logger.debug(f"Failed setting ThreadPriority in {path}: {str(e)}")
            
            try:
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, gpu_energy_path, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "Start", 0, winreg.REG_DWORD, 4)
            except Exception as e:
                logger.debug(f"Failed disabling GpuEnergyDrv: {str(e)}")
                
            try:
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, cppc_path, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "Attributes", 0, winreg.REG_DWORD, 2)
            except Exception as e:
                logger.debug(f"Failed setting CPPC Attributes: {str(e)}")
                
            logger.info("Applied system driver ThreadPriority (0xf), disabled GpuEnergyDrv, and enabled CPPC Advanced settings.")
        except Exception as e:
            logger.error(f"Failed applying driver priority tweaks: {str(e)}")
            raise

    @staticmethod
    def apply_hyperv_and_boot_tweak(disable_hyperv: bool):
        try:
            path_dg = r"SYSTEM\CurrentControlSet\Control\DeviceGuard"
            path_hvci = r"SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity"
            path_lsa = r"SYSTEM\CurrentControlSet\Control\Lsa"
            path_dg_policy = r"SOFTWARE\Policies\Microsoft\Windows\DeviceGuard"

            if disable_hyperv:
                # 1. Back up registry keys
                SystemTweaksService.backup_registry_value("HKLM", path_dg, "EnableVirtualizationBasedSecurity")
                SystemTweaksService.backup_registry_value("HKLM", path_hvci, "Enabled")
                SystemTweaksService.backup_registry_value("HKLM", path_lsa, "LsaCfgFlags")
                SystemTweaksService.backup_registry_value("HKLM", path_dg_policy, "EnableVirtualizationBasedSecurity")
                SystemTweaksService.backup_registry_value("HKLM", path_dg_policy, "RequirePlatformSecurityFeatures")

                # 2. Write disabling values
                try:
                    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_dg, 0, winreg.KEY_WRITE) as key:
                        winreg.SetValueEx(key, "EnableVirtualizationBasedSecurity", 0, winreg.REG_DWORD, 0)
                except Exception:
                    pass

                try:
                    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_hvci, 0, winreg.KEY_WRITE) as key:
                        winreg.SetValueEx(key, "Enabled", 0, winreg.REG_DWORD, 0)
                        winreg.SetValueEx(key, "Locked", 0, winreg.REG_DWORD, 0)
                except Exception:
                    pass

                try:
                    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\CI\Config", 0, winreg.KEY_WRITE) as key:
                        winreg.SetValueEx(key, "HVCIMCTEnabled", 0, winreg.REG_DWORD, 0)
                except Exception:
                    pass

                try:
                    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_lsa, 0, winreg.KEY_WRITE) as key:
                        winreg.SetValueEx(key, "LsaCfgFlags", 0, winreg.REG_DWORD, 0)
                except Exception:
                    pass

                # Delete policy overrides if any to allow disabling to take effect
                for val_name in ["EnableVirtualizationBasedSecurity", "RequirePlatformSecurityFeatures"]:
                    try:
                        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_dg_policy, 0, winreg.KEY_SET_VALUE) as key:
                            winreg.DeleteValue(key, val_name)
                    except Exception:
                        pass

                # 3. bcdedit commands
                cmd_str = (
                    "bcdedit /set tscsyncpolicy default & "
                    "bcdedit /set hypervisorlaunchtype off & "
                    "bcdedit /set hypervisoriommupolicy Disable & "
                    "bcdedit /set vsmlaunchtype Off & "
                    "bcdedit /set vm No & "
                    "bcdedit /set MSI Default & "
                    "bcdedit /set isolatedcontext No & "
                    "bcdedit /set tpmbootentropy ForceDisable & "
                    "bcdedit /set forcelegacyplatform No & "
                    "bcdedit /event off & "
                    "bcdedit /ems off & "
                    "bcdedit /set ems off & "
                    "bcdedit /timeout 1"
                )
                SystemTweaksService.safe_subprocess_call(["cmd.exe", "/c", cmd_str], timeout=15)
                logger.info("Disabled Hyper-V, VBS registry values, debug events, and set fast boot timeout via bcdedit.")
            else:
                 # 1. Restore registry keys
                 SystemTweaksService.restore_registry_value_or_default("HKLM", path_dg, "EnableVirtualizationBasedSecurity", 0, winreg.REG_DWORD)
                 SystemTweaksService.restore_registry_value_or_default("HKLM", path_hvci, "Enabled", 0, winreg.REG_DWORD)
                 SystemTweaksService.restore_registry_value_or_default("HKLM", path_hvci, "Locked", 0, winreg.REG_DWORD)
                 SystemTweaksService.restore_registry_value_or_default("HKLM", r"SYSTEM\CurrentControlSet\Control\CI\Config", "HVCIMCTEnabled", 0, winreg.REG_DWORD)
                 SystemTweaksService.restore_registry_value_or_default("HKLM", path_lsa, "LsaCfgFlags", 0, winreg.REG_DWORD)
                 SystemTweaksService.restore_registry_value_or_default("HKLM", path_dg_policy, "EnableVirtualizationBasedSecurity", 0, winreg.REG_DWORD)
                 SystemTweaksService.restore_registry_value_or_default("HKLM", path_dg_policy, "RequirePlatformSecurityFeatures", 0, winreg.REG_DWORD)
 
                 # 2. bcdedit commands
                 cmd_str = (
                     "bcdedit /set hypervisorlaunchtype auto & "
                     "bcdedit /deletevalue hypervisoriommupolicy & "
                     "bcdedit /deletevalue vsmlaunchtype & "
                     "bcdedit /deletevalue vm & "
                     "bcdedit /deletevalue isolatedcontext & "
                     "bcdedit /deletevalue tpmbootentropy & "
                     "bcdedit /deletevalue forcelegacyplatform & "
                     "bcdedit /event on & "
                     "bcdedit /set ems on & "
                     "bcdedit /timeout 30 & "
                     "bcdedit /deletevalue tscsyncpolicy"
                 )
                 SystemTweaksService.safe_subprocess_call(["cmd.exe", "/c", cmd_str], timeout=15)
                 logger.info("Restored Hyper-V and boot debugging defaults via bcdedit.")
        except Exception as e:
            logger.error(f"Failed to configure Hyper-V and boot tweaks: {str(e)}")
            raise


    @staticmethod
    def lock_process_memory(pid: int, min_mb: int, max_mb: int) -> bool:
        """
        通过 SetProcessWorkingSetSizeEx 锁定/释放物理工作集。
        min_mb == -1 且 max_mb == -1 时释放锁定。
        """
        PROCESS_SET_QUOTA = 0x0100
        PROCESS_QUERY_INFORMATION = 0x0400
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION, False, pid)
        if not handle:
            logger.error(f"OpenProcess failed for PID {pid} to lock/unlock working set.")
            return False
            
        try:
            kernel32.SetProcessWorkingSetSizeEx.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_ulong]
            kernel32.SetProcessWorkingSetSizeEx.restype = ctypes.c_bool
            
            if min_mb == -1 and max_mb == -1:
                # 释放锁定
                QUOTA_LIMITS_HARDWS_MIN_DISABLE = 0x00000002
                QUOTA_LIMITS_HARDWS_MAX_DISABLE = 0x00000008
                res = kernel32.SetProcessWorkingSetSizeEx(handle, -1, -1, QUOTA_LIMITS_HARDWS_MIN_DISABLE | QUOTA_LIMITS_HARDWS_MAX_DISABLE)
                if res:
                    logger.info(f"Successfully released memory working set lock for PID {pid}")
                    return True
                else:
                    logger.warning(f"Failed to release working set lock for PID {pid}: {kernel32.GetLastError()}")
                    return False
            else:
                min_bytes = min_mb * 1024 * 1024
                max_bytes = max_mb * 1024 * 1024
                QUOTA_LIMITS_HARDWS_MIN_ENABLE = 0x00000001
                QUOTA_LIMITS_HARDWS_MAX_DISABLE = 0x00000008
                res = kernel32.SetProcessWorkingSetSizeEx(handle, min_bytes, max_bytes, QUOTA_LIMITS_HARDWS_MIN_ENABLE | QUOTA_LIMITS_HARDWS_MAX_DISABLE)
                if res:
                    logger.info(f"Successfully locked working set for PID {pid} (Min: {min_mb}MB, Max: {max_mb}MB)")
                    return True
                else:
                    logger.error(f"SetProcessWorkingSetSizeEx failed for PID {pid}, Error: {kernel32.GetLastError()}")
                    return False
        finally:
            kernel32.CloseHandle(handle)

    @staticmethod
    def apply_interrupt_moderation_tweak(enable: bool) -> bool:
        """
        禁用/启用网卡硬件中断合并。
        """
        path_class = r"SYSTEM\CurrentControlSet\Control\Class\{4D36E972-E325-11CE-BFC1-08002BE10318}"
        hkey = winreg.HKEY_LOCAL_MACHINE
        applied = 0
        try:
            with winreg.OpenKey(hkey, path_class, 0, winreg.KEY_READ) as root_key:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(root_key, i)
                        sub_path = f"{path_class}\\{sub}"
                        has_imod = False
                        try:
                            with winreg.OpenKey(hkey, sub_path, 0, winreg.KEY_READ) as sub_key:
                                winreg.QueryValueEx(sub_key, "*InterruptModeration")
                                has_imod = True
                        except FileNotFoundError:
                            pass
                            
                        if has_imod:
                            if enable:
                                SystemTweaksService.backup_registry_value("HKLM", sub_path, "*InterruptModeration")
                                with winreg.OpenKey(hkey, sub_path, 0, winreg.KEY_WRITE) as sub_key:
                                    winreg.SetValueEx(sub_key, "*InterruptModeration", 0, winreg.REG_SZ, "0")
                            else:
                                SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, "*InterruptModeration", "1", winreg.REG_SZ)
                            applied += 1
                        i += 1
                    except OSError:
                        break
            logger.info(f"Interrupt moderation tweak applied/reverted for {applied} adapters.")
            return True
        except Exception as e:
            logger.error(f"Failed to apply/revert interrupt moderation tweak: {str(e)}")
            return False

    @staticmethod
    def apply_net_bindings_tweak(enable: bool) -> bool:
        """
        使用 WMI 禁用/启用网卡冗余组件（ms_msclient, ms_server, ms_pacer, ms_lldp, ms_tcpip6）。
        """
        components = ["ms_msclient", "ms_server", "ms_pacer", "ms_lldp", "ms_tcpip6"]
        if enable:
            for comp in components:
                SystemTweaksService.backup_net_bindings(comp)
        
        if HAS_WIN32:
            has_com = False
            try:
                import pythoncom
                import win32com.client
                pythoncom.CoInitialize()
                has_com = True
                wmi = win32com.client.GetObject("winmgmts:\\\\.\\root\\StandardCimv2")
                method_name = "Disable" if enable else "Enable"
                for comp in components:
                    comp_clean = re.sub(r'[^\w\-]', '', comp)
                    bindings = wmi.ExecQuery(f"SELECT * FROM MSFT_NetAdapterBindingSettingData WHERE ComponentID = '{comp_clean}'")
                    for b in bindings:
                        try:
                            b.ExecMethod_(method_name)
                        except Exception:
                            pass
                logger.info(f"NetAdapter bindings tweak Applied={enable} executed successfully via WMI COM.")
                return True
            except Exception as e:
                logger.error(f"Failed to apply net bindings tweak via WMI COM: {str(e)}")
            finally:
                if has_com:
                    pythoncom.CoUninitialize()
        return False

    @staticmethod
    def apply_widgets_tweak(enable: bool) -> bool:
        """
        禁用/启用 Win11 小部件面板。
        """
        path_dsh = r"SOFTWARE\Policies\Microsoft\Dsh"
        path_adv = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
        if enable:
            SystemTweaksService.backup_registry_value("HKLM", path_dsh, "AllowNewsAndInterests")
            SystemTweaksService.backup_registry_value("HKCU", path_adv, "TaskbarDa")
            try:
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_dsh, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "AllowNewsAndInterests", 0, winreg.REG_DWORD, 0)
            except Exception:  # nosec
                pass
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path_adv, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "TaskbarDa", 0, winreg.REG_DWORD, 0)
            except Exception:  # nosec
                pass
        else:
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_dsh, "AllowNewsAndInterests", 1, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKCU", path_adv, "TaskbarDa", 1, winreg.REG_DWORD)
        return True

    @staticmethod
    def apply_sticky_keys_tweak(enable: bool) -> bool:
        """
        禁用/启用粘滞键与热键。
        """
        path_sticky = r"Control Panel\Accessibility\StickyKeys"
        path_filter = r"Control Panel\Accessibility\Keyboard Response"
        path_toggle = r"Control Panel\Accessibility\ToggleKeys"
        if enable:
            SystemTweaksService.backup_registry_value("HKCU", path_sticky, "Flags")
            SystemTweaksService.backup_registry_value("HKCU", path_filter, "Flags")
            SystemTweaksService.backup_registry_value("HKCU", path_toggle, "Flags")
            
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path_sticky, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "Flags", 0, winreg.REG_SZ, "506")
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path_filter, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "Flags", 0, winreg.REG_SZ, "122")
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path_toggle, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "Flags", 0, winreg.REG_SZ, "58")
        else:
            SystemTweaksService.restore_registry_value_or_default("HKCU", path_sticky, "Flags", "510", winreg.REG_SZ)
            SystemTweaksService.restore_registry_value_or_default("HKCU", path_filter, "Flags", "126", winreg.REG_SZ)
            SystemTweaksService.restore_registry_value_or_default("HKCU", path_toggle, "Flags", "62", winreg.REG_SZ)
        return True

    @staticmethod
    def apply_startup_delay_tweak(enable: bool) -> bool:
        """
        消除/恢复应用自启动延迟。
        """
        path_serialize = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Serialize"
        if enable:
            SystemTweaksService.backup_registry_value("HKCU", path_serialize, "StartupDelayInMSec")
            SystemTweaksService.backup_registry_value("HKCU", path_serialize, "WaitForIdleState")
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path_serialize, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "StartupDelayInMSec", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "WaitForIdleState", 0, winreg.REG_DWORD, 0)
        else:
            SystemTweaksService.restore_registry_value_or_default("HKCU", path_serialize, "StartupDelayInMSec", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKCU", path_serialize, "WaitForIdleState", None, winreg.REG_DWORD)
        return True

    @staticmethod
    def apply_menu_delay_tweak(enable: bool) -> bool:
        """
        消除/恢复经典右键菜单延迟。
        """
        path_desktop = r"Control Panel\Desktop"
        if enable:
            SystemTweaksService.backup_registry_value("HKCU", path_desktop, "MenuShowDelay")
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path_desktop, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "MenuShowDelay", 0, winreg.REG_SZ, "0")
        else:
            SystemTweaksService.restore_registry_value_or_default("HKCU", path_desktop, "MenuShowDelay", "400", winreg.REG_SZ)
        return True

    @staticmethod
    def apply_settings_sync_tweak(enable: bool) -> bool:
        """
        禁用/启用 Microsoft 账户设置同步并停止 MobSync.exe。
        """
        path_sync_policy = r"SOFTWARE\Policies\Microsoft\Windows\SettingSync"
        path_netcache_policy = r"SOFTWARE\Policies\Microsoft\Windows\NetCache"
        path_sync_user = r"Software\Microsoft\Windows\CurrentVersion\SettingSync\Groups"
        sync_groups = ["Personalization", "BrowserSettings", "Credentials", "LanguageSettings", "AppSync", "Windows"]
        
        if enable:
            SystemTweaksService.backup_registry_value("HKLM", path_sync_policy, "DisableSettingSync")
            SystemTweaksService.backup_registry_value("HKLM", path_sync_policy, "DisableSettingSyncUserOverride")
            SystemTweaksService.backup_registry_value("HKLM", path_netcache_policy, "Enabled")
            for group in sync_groups:
                SystemTweaksService.backup_registry_value("HKCU", f"{path_sync_user}\\{group}", "Enabled")
            SystemTweaksService.backup_service("CscService")
            
            try:
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_sync_policy, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "DisableSettingSync", 0, winreg.REG_DWORD, 2)
                    winreg.SetValueEx(key, "DisableSettingSyncUserOverride", 0, winreg.REG_DWORD, 1)
            except Exception:  # nosec
                pass
            try:
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_netcache_policy, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "Enabled", 0, winreg.REG_DWORD, 0)
            except Exception:  # nosec
                pass
            for group in sync_groups:
                try:
                    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, f"{path_sync_user}\\{group}", 0, winreg.KEY_WRITE) as key:
                        winreg.SetValueEx(key, "Enabled", 0, winreg.REG_DWORD, 0)
                except Exception:  # nosec
                    pass
            SystemTweaksService.set_service_start_type("CscService", SERVICE_DISABLED)
            SystemTweaksService.stop_service("CscService")
        else:
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_sync_policy, "DisableSettingSync", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_sync_policy, "DisableSettingSyncUserOverride", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_netcache_policy, "Enabled", None, winreg.REG_DWORD)
            for group in sync_groups:
                SystemTweaksService.restore_registry_value_or_default("HKCU", f"{path_sync_user}\\{group}", "Enabled", 1, winreg.REG_DWORD)
            SystemTweaksService.restore_service_or_default("CscService", SERVICE_DEMAND_START)
        return True

    @staticmethod
    def apply_dynamic_lighting_tweak(enable: bool) -> bool:
        """
        禁用/启用 Win11 原生外设动态照明 (RGB)。
        """
        path_lighting = r"Software\Microsoft\Lighting"
        if enable:
            SystemTweaksService.backup_registry_value("HKCU", path_lighting, "AmbientLightingEnabled")
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path_lighting, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "AmbientLightingEnabled", 0, winreg.REG_DWORD, 0)
        else:
            SystemTweaksService.restore_registry_value_or_default("HKCU", path_lighting, "AmbientLightingEnabled", 1, winreg.REG_DWORD)
        return True

    @staticmethod
    def apply_gpu_msi_tweak(enable: bool) -> bool:
        """
        开启 GPU MSI 模式并设置中断优先级为 High (Priority=3)。
        """
        try:
            gpu_ids = []
            if HAS_WIN32:
                import pythoncom
                import win32com.client
                has_com = False
                try:
                    pythoncom.CoInitialize()
                    has_com = True
                    wmi_cimv2 = win32com.client.GetObject("winmgmts:\\\\.\\root\\cimv2")
                    gpus = wmi_cimv2.ExecQuery("SELECT PNPDeviceID FROM Win32_VideoController")
                    for g in gpus:
                        if g.PNPDeviceID and g.PNPDeviceID.startswith("PCI\\"):
                            gpu_ids.append(g.PNPDeviceID)
                except Exception as ex:
                    logger.debug(f"WMI query for GPU PNPDeviceID failed: {str(ex)}")
                finally:
                    wmi_cimv2 = None
                    if has_com:
                        try:
                            pythoncom.CoUninitialize()
                        except Exception:  # nosec
                            pass
            
            if not gpu_ids:
                ps_cmd = "Get-PnpDevice -PresentOnly | Where-Object {$_.Class -eq 'Display'} | Select-Object -ExpandProperty InstanceId | ConvertTo-Json"
                process = subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)  # nosec
                stdout, _ = process.communicate(timeout=10)
                output = stdout.decode("gbk", errors="ignore").strip()
                if output:
                    try:
                        import json
                        parsed = json.loads(output)
                        if isinstance(parsed, list):
                            gpu_ids = [x for x in parsed if x.startswith("PCI\\")]
                        elif isinstance(parsed, str) and parsed.startswith("PCI\\"):
                            gpu_ids = [parsed]
                    except Exception:
                        import re
                        gpu_ids = re.findall(r'PCI\\\\[^\s"]+', output)
            
            if not gpu_ids:
                logger.warning("No display GPU device paths found for MSI tweak.")
                return False
                
            applied_count = 0
            for gid in gpu_ids:
                sub_path = f"SYSTEM\\CurrentControlSet\\Enum\\{gid}\\Device Parameters\\Interrupt Management\\MessageSignaledInterruptProperties"
                if enable:
                    SystemTweaksService.backup_registry_value("HKLM", sub_path, "MSISupported")
                    SystemTweaksService.backup_registry_value("HKLM", sub_path, "Priority")
                    try:
                        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, sub_path, 0, winreg.KEY_WRITE) as key:
                            winreg.SetValueEx(key, "MSISupported", 0, winreg.REG_DWORD, 1)
                            winreg.SetValueEx(key, "Priority", 0, winreg.REG_DWORD, 3)
                        logger.info(f"GPU {gid} MSI mode and High priority applied.")
                        applied_count += 1
                    except Exception as e:
                        logger.error(f"Failed to set MSI registry for GPU {gid}: {str(e)}")
                else:
                    SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, "MSISupported", 1, winreg.REG_DWORD)
                    SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, "Priority", None, winreg.REG_DWORD)
                    applied_count += 1
            return applied_count > 0
        except Exception as e:
            logger.error(f"Failed to apply GPU MSI tweak: {str(e)}")
            return False

    @staticmethod
    def apply_xbox_save_tweak(enable: bool) -> bool:
        """
        禁用/启用 Xbox Live 存档服务及其计划任务。
        """
        try:
            if enable:
                SystemTweaksService.backup_service("XblGameSave")
                SystemTweaksService.set_service_start_type("XblGameSave", SERVICE_DISABLED)
                SystemTweaksService.stop_service("XblGameSave")
                action = "/disable"
            else:
                SystemTweaksService.restore_service_or_default("XblGameSave", SERVICE_DEMAND_START)
                action = "/enable"
            
            cmd1 = ["schtasks.exe", "/change", "/tn", "\\Microsoft\\XblGameSave\\XblGameSaveTask", action]
            cmd2 = ["schtasks.exe", "/change", "/tn", "\\Microsoft\\XblGameSave\\XblGameSaveTaskLogon", action]
            SystemTweaksService.safe_subprocess_call(cmd1)
            SystemTweaksService.safe_subprocess_call(cmd2)
            return True
        except Exception as e:
            logger.error(f"Failed to apply Xbox Save Tweak: {str(e)}")
            return False

    @staticmethod
    def apply_store_auto_update_tweak(enable: bool) -> bool:
        """
        禁用/启用 Microsoft Store 自动下载与应用静默推广。
        """
        path_store = r"SOFTWARE\Policies\Microsoft\WindowsStore"
        path_cloud = r"SOFTWARE\Policies\Microsoft\Windows\CloudContent"
        path_cdm = r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"
        if enable:
            SystemTweaksService.backup_registry_value("HKLM", path_store, "AutoDownload")
            SystemTweaksService.backup_registry_value("HKLM", path_store, "DisableAutoInstall")
            SystemTweaksService.backup_registry_value("HKLM", path_cloud, "DisableWindowsConsumerFeatures")
            SystemTweaksService.backup_registry_value("HKCU", path_cdm, "SilentInstalledAppsEnabled")
            
            try:
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_store, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "AutoDownload", 0, winreg.REG_DWORD, 2)
                    winreg.SetValueEx(key, "DisableAutoInstall", 0, winreg.REG_DWORD, 1)
            except Exception:  # nosec
                pass
            try:
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_cloud, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "DisableWindowsConsumerFeatures", 0, winreg.REG_DWORD, 1)
            except Exception:  # nosec
                pass
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path_cdm, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "SilentInstalledAppsEnabled", 0, winreg.REG_DWORD, 0)
            except Exception:  # nosec
                pass
        else:
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_store, "AutoDownload", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_store, "DisableAutoInstall", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_cloud, "DisableWindowsConsumerFeatures", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKCU", path_cdm, "SilentInstalledAppsEnabled", 1, winreg.REG_DWORD)
        return True

    @staticmethod
    def apply_vulnerable_driver_blocklist_tweak(enable: bool) -> bool:
        """
        禁用/启用 Win11 易受攻击的驱动程序黑名单。
        """
        path_ci = r"SYSTEM\CurrentControlSet\Control\CI\Config"
        path_wd = r"SOFTWARE\Microsoft\Windows Defender\Threat Sharing"
        if enable:
            SystemTweaksService.backup_registry_value("HKLM", path_ci, "VulnerableDriverBlocklistEnable")
            SystemTweaksService.backup_registry_value("HKLM", path_wd, "EnableVulnerableDriverBlocklist")
            try:
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_ci, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "VulnerableDriverBlocklistEnable", 0, winreg.REG_DWORD, 0)
            except Exception:  # nosec
                pass
            try:
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_wd, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "EnableVulnerableDriverBlocklist", 0, winreg.REG_DWORD, 0)
            except Exception:  # nosec
                pass
            logger.info("Vulnerable driver blocklist disabled via Registry.")
        else:
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_ci, "VulnerableDriverBlocklistEnable", 1, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_wd, "EnableVulnerableDriverBlocklist", 1, winreg.REG_DWORD)
            logger.info("Vulnerable driver blocklist settings restored via Registry.")
        return True

    @staticmethod
    def apply_prevent_device_encryption_tweak(enable: bool) -> bool:
        """
        禁用/启用自动 BitLocker 磁盘加密。
        """
        path_bl = r"SYSTEM\CurrentControlSet\Control\BitLocker"
        if enable:
            SystemTweaksService.backup_registry_value("HKLM", path_bl, "PreventDeviceEncryption")
            try:
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_bl, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "PreventDeviceEncryption", 0, winreg.REG_DWORD, 1)
            except Exception:  # nosec
                pass
        else:
            SystemTweaksService.restore_registry_value_or_default("HKLM", path_bl, "PreventDeviceEncryption", None, winreg.REG_DWORD)
        return True

    @staticmethod
    def apply_spotlight_tweak(enable: bool) -> bool:
        """
        禁用/启用 Windows 聚焦与锁屏广告建议。
        """
        path_cdm = r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"
        if enable:
            SystemTweaksService.backup_registry_value("HKCU", path_cdm, "SubscribedContent-338387Enabled")
            SystemTweaksService.backup_registry_value("HKCU", path_cdm, "RotatingLockScreenOverlayEnabled")
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path_cdm, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "SubscribedContent-338387Enabled", 0, winreg.REG_DWORD, 0)
                    winreg.SetValueEx(key, "RotatingLockScreenOverlayEnabled", 0, winreg.REG_DWORD, 0)
            except Exception:  # nosec
                pass
        else:
            SystemTweaksService.restore_registry_value_or_default("HKCU", path_cdm, "SubscribedContent-338387Enabled", 1, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKCU", path_cdm, "RotatingLockScreenOverlayEnabled", 1, winreg.REG_DWORD)
        return True


    @staticmethod
    def lock_process_memory(pid: int, min_mb: int, max_mb: int) -> bool:
        """
        通过 SetProcessWorkingSetSizeEx 锁定/释放物理工作集。
        min_mb == -1 且 max_mb == -1 时释放锁定。
        """
        PROCESS_SET_QUOTA = 0x0100
        PROCESS_QUERY_INFORMATION = 0x0400
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION, False, pid)
        if not handle:
            logger.error(f"OpenProcess failed for PID {pid} to lock/unlock working set.")
            return False
            
        try:
            kernel32.SetProcessWorkingSetSizeEx.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_ulong]
            kernel32.SetProcessWorkingSetSizeEx.restype = ctypes.c_bool
            
            if min_mb == -1 and max_mb == -1:
                # 释放锁定
                QUOTA_LIMITS_HARDWS_MIN_DISABLE = 0x00000002
                QUOTA_LIMITS_HARDWS_MAX_DISABLE = 0x00000008
                res = kernel32.SetProcessWorkingSetSizeEx(handle, -1, -1, QUOTA_LIMITS_HARDWS_MIN_DISABLE | QUOTA_LIMITS_HARDWS_MAX_DISABLE)
                if res:
                    logger.info(f"Successfully released memory working set lock for PID {pid}")
                    return True
                else:
                    logger.warning(f"Failed to release working set lock for PID {pid}: {kernel32.GetLastError()}")
                    return False
            else:
                min_bytes = min_mb * 1024 * 1024
                max_bytes = max_mb * 1024 * 1024
                QUOTA_LIMITS_HARDWS_MIN_ENABLE = 0x00000001
                QUOTA_LIMITS_HARDWS_MAX_DISABLE = 0x00000008
                res = kernel32.SetProcessWorkingSetSizeEx(handle, min_bytes, max_bytes, QUOTA_LIMITS_HARDWS_MIN_ENABLE | QUOTA_LIMITS_HARDWS_MAX_DISABLE)
                if res:
                    logger.info(f"Successfully locked working set for PID {pid} (Min: {min_mb}MB, Max: {max_mb}MB)")
                    return True
                else:
                    logger.error(f"SetProcessWorkingSetSizeEx failed for PID {pid}, Error: {kernel32.GetLastError()}")
                    return False
        finally:
            kernel32.CloseHandle(handle)

    @staticmethod
    def apply_global_fse_tweak(enable: bool) -> bool:
        """
        全局禁用/开启全屏优化 (FSE) 并设置 GameBar 提示策略。
        """
        path_gcs = r"System\GameConfigStore"
        path_gamebar = r"Software\Microsoft\GameBar"
        if enable:
            SystemTweaksService.backup_registry_value("HKCU", path_gcs, "GameDVR_FSEBehaviorMode")
            SystemTweaksService.backup_registry_value("HKCU", path_gamebar, "ShowEToast")
            
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path_gcs, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "GameDVR_FSEBehaviorMode", 0, winreg.REG_DWORD, 2)
            try:
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path_gamebar, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "ShowEToast", 0, winreg.REG_DWORD, 0)
            except Exception:  # nosec
                pass
        else:
            SystemTweaksService.restore_registry_value_or_default("HKCU", path_gcs, "GameDVR_FSEBehaviorMode", 0, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKCU", path_gamebar, "ShowEToast", 1, winreg.REG_DWORD)
        return True

    @staticmethod
    def apply_game_fse_tweak(enable: bool, game_path: str) -> bool:
        """
        针对指定的可执行程序路径禁用/恢复全屏优化。
        注册表项: HKCU\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers
        值名称: 游戏 EXE 的绝对路径
        值数据: ~ DISABLEDXMAXIMIZEDWINDOWEDMODE
        """
        if game_path:
            game_path = os.path.normpath(game_path)
        if not game_path or not os.path.exists(game_path):
            logger.warning(f"Game path '{game_path}' does not exist. Skipping game FSE tweak.")
            return False

        path_layers = r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"
        flag = "~ DISABLEDXMAXIMIZEDWINDOWEDMODE"
        
        try:
            if enable:
                SystemTweaksService.backup_registry_value("HKCU", path_layers, game_path)
                current_val = ""
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path_layers, 0, winreg.KEY_READ) as key:
                        current_val, _ = winreg.QueryValueEx(key, game_path)
                except FileNotFoundError:
                    pass
                
                if flag not in current_val:
                    new_val = (current_val + " " + flag).strip()
                    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path_layers, 0, winreg.KEY_WRITE) as key:
                        winreg.SetValueEx(key, game_path, 0, winreg.REG_SZ, new_val)
                    logger.info(f"FSE disabled for game: {game_path} (Value: {new_val})")
            else:
                current_val = ""
                has_key = False
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path_layers, 0, winreg.KEY_READ) as key:
                        current_val, _ = winreg.QueryValueEx(key, game_path)
                        has_key = True
                except FileNotFoundError:
                    pass
                
                if has_key:
                    new_val = current_val.replace(flag, "").strip()
                    new_val = " ".join(new_val.split())
                    
                    if new_val:
                        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path_layers, 0, winreg.KEY_WRITE) as key:
                            winreg.SetValueEx(key, game_path, 0, winreg.REG_SZ, new_val)
                    else:
                        try:
                            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path_layers, 0, winreg.KEY_WRITE) as key:
                                winreg.DeleteValue(key, game_path)
                        except Exception:  # nosec
                            pass
                logger.info(f"FSE setting reverted for game: {game_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to apply game FSE tweak for {game_path}: {str(e)}")
            return False

    @staticmethod
    def apply_game_gpu_preference_tweak(enable: bool, game_path: str) -> bool:
        r"""
        针对指定的可执行程序路径设置 DirectX 高性能独显分配策略。
        注册表项: HKCU\Software\Microsoft\DirectX\UserGpuPreferences
        值名称: 游戏 EXE 的绝对路径
        值数据: GpuPreference=2; (REG_SZ)
        """
        if game_path:
            game_path = os.path.normpath(game_path)
        if not game_path or not os.path.exists(game_path):
            logger.warning(f"Game path '{game_path}' does not exist. Skipping game GPU preference tweak.")
            return False

        path_gpu_pref = r"Software\Microsoft\DirectX\UserGpuPreferences"
        val_data = "GpuPreference=2;"
        
        try:
            if enable:
                SystemTweaksService.backup_registry_value("HKCU", path_gpu_pref, game_path)
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path_gpu_pref, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, game_path, 0, winreg.REG_SZ, val_data)
                logger.info(f"Forced high-performance GPU preference for game: {game_path}")
            else:
                SystemTweaksService.restore_registry_value_or_default("HKCU", path_gpu_pref, game_path, None, winreg.REG_SZ)
                logger.info(f"GPU preference reverted for game: {game_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to apply game GPU preference tweak for {game_path}: {str(e)}")
            return False

    @staticmethod
    def apply_irq_affinity_tweak(enable: bool) -> bool:
        """
        显卡与网卡硬件中断亲和性绑定 (GPU & Network to CPU 0-1)
        """
        return IrqAffinityService.apply_separated_irq_affinity(enable)

    @staticmethod
    def apply_power_throttling_tweak(enable: bool) -> bool:
        """
        全局禁用/启用 Windows 电源节流 (Power Throttling)
        """
        path_pt = r"SYSTEM\CurrentControlSet\Control\Power\PowerThrottling"
        try:
            if enable:
                SystemTweaksService.backup_registry_value("HKLM", path_pt, "PowerThrottlingOff")
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_pt, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "PowerThrottlingOff", 0, winreg.REG_DWORD, 1)
                logger.info("Power throttling globally disabled.")
            else:
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_pt, "PowerThrottlingOff", 0, winreg.REG_DWORD)
                logger.info("Power throttling globally restored to default.")
            return True
        except Exception as e:
            logger.error(f"Failed to apply/revert power throttling tweak: {str(e)}")
            return False

    @staticmethod
    def apply_tcp_bbr_tweak(enable: bool) -> bool:
        """
        启用/禁用 TCP BBR 拥塞控制提供程序，并自动处理 Windows 11 上的 Loopback Large MTU 冲突问题
        """
        try:
            if enable:
                # 禁用 Loopback Large MTU 以修复 Windows 11 上启用 BBR 破坏 localhost 流量（导致 Clash/代理失效）的内核 Bug
                SystemTweaksService.safe_subprocess_call(["netsh", "int", "ipv4", "set", "gl", "loopbacklargemtu=disable"], timeout=5)
                SystemTweaksService.safe_subprocess_call(["netsh", "int", "ipv6", "set", "gl", "loopbacklargemtu=disable"], timeout=5)
                
                cmd = ["netsh", "int", "tcp", "set", "supplemental", "template=internet", "congestionprovider=bbr"]
                res = SystemTweaksService.safe_subprocess_call(cmd, timeout=5)
                if res == 0:
                    logger.info("TCP BBR congestion provider applied successfully with loopback MTU bugfix.")
                    return True
                else:
                    logger.error(f"Netsh set bbr failed with code: {res}")
                    return False
            else:
                # 恢复 Loopback Large MTU 为默认启用，并将拥塞算法恢复为 cubic
                SystemTweaksService.safe_subprocess_call(["netsh", "int", "ipv4", "set", "gl", "loopbacklargemtu=enable"], timeout=5)
                SystemTweaksService.safe_subprocess_call(["netsh", "int", "ipv6", "set", "gl", "loopbacklargemtu=enable"], timeout=5)
                
                cmd = ["netsh", "int", "tcp", "set", "supplemental", "template=internet", "congestionprovider=cubic"]
                res = SystemTweaksService.safe_subprocess_call(cmd, timeout=5)
                if res == 0:
                    logger.info("TCP congestion provider restored to cubic, loopback MTU restored to default.")
                    return True
                else:
                    logger.error(f"Netsh restore cubic failed with code: {res}")
                    return False
        except Exception as e:
            logger.error(f"Failed to apply/revert TCP BBR tweak: {str(e)}")
            return False

    @staticmethod
    def apply_eee_tweak(enable: bool) -> bool:
        """
        禁用/启用物理网卡的以太网节能 (EEE), Green Ethernet, GigaLite, PowerSavingMode
        """
        path_class = r"SYSTEM\CurrentControlSet\Control\Class\{4D36E972-E325-11CE-BFC1-08002BE10318}"
        hkey = winreg.HKEY_LOCAL_MACHINE
        applied = 0
        try:
            with winreg.OpenKey(hkey, path_class, 0, winreg.KEY_READ) as root_key:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(root_key, i)
                        if sub.isdigit():
                            sub_path = f"{path_class}\\{sub}"
                            
                            is_physical = False
                            try:
                                with winreg.OpenKey(hkey, f"{sub_path}\\Ndi\\Interfaces", 0, winreg.KEY_READ) as intf_key:
                                    lower_range, _ = winreg.QueryValueEx(intf_key, "LowerRange")
                                    if "ethernet" in str(lower_range).lower():
                                        is_physical = True
                            except FileNotFoundError:
                                pass
                                
                            if is_physical:
                                keys_to_tweak = ["*EEE", "EEELink", "*EEELink", "*GigaLite", "*PowerSavingMode", "GreenEthernet", "GreenFeedback"]
                                for key_name in keys_to_tweak:
                                    exists = False
                                    val_type = winreg.REG_SZ
                                    try:
                                        with winreg.OpenKey(hkey, sub_path, 0, winreg.KEY_READ) as sub_key:
                                            _, val_type = winreg.QueryValueEx(sub_key, key_name)
                                            exists = True
                                    except FileNotFoundError:
                                        pass
                                    
                                    if exists:
                                        if enable:
                                            SystemTweaksService.backup_registry_value("HKLM", sub_path, key_name)
                                            with winreg.OpenKey(hkey, sub_path, 0, winreg.KEY_WRITE) as sub_key:
                                                if val_type == winreg.REG_DWORD:
                                                    winreg.SetValueEx(sub_key, key_name, 0, winreg.REG_DWORD, 0)
                                                else:
                                                    winreg.SetValueEx(sub_key, key_name, 0, winreg.REG_SZ, "0")
                                        else:
                                            default_val = "1"
                                            if val_type == winreg.REG_DWORD:
                                                SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, key_name, 1, winreg.REG_DWORD)
                                            else:
                                                SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, key_name, default_val, winreg.REG_SZ)
                                        applied += 1
                        i += 1
                    except OSError:
                        break
            logger.info(f"Ethernet power saving tweak applied/reverted for {applied} settings.")
            return True
        except Exception as e:
            logger.error(f"Failed to apply/revert Ethernet power saving tweak: {str(e)}")
            return False

    @staticmethod
    def apply_wsearch_tweak(enable: bool) -> bool:
        """
        禁用/启用 Windows Search 文件索引服务 (WSearch)
        """
        try:
            if enable:
                SystemTweaksService.backup_service("WSearch")
                SystemTweaksService.set_service_start_type("WSearch", SERVICE_DISABLED)
                logger.info("Windows Search indexing service disabled.")
            else:
                SystemTweaksService.restore_service_or_default("WSearch", 2)
                try:
                    SystemTweaksService.safe_subprocess_call(["sc.exe", "start", "WSearch"], timeout=2)
                except Exception:
                    pass
                logger.info("Windows Search indexing service restored and started.")
            return True
        except Exception as e:
            logger.error(f"Failed to apply/revert Windows Search indexing tweak: {str(e)}")
            return False

    @staticmethod
    def apply_web_search_tweak(enable: bool) -> bool:
        """
        禁用/启用开始菜单 Bing 网络搜索结果及 Cortana
        """
        path_search_policy = r"SOFTWARE\Policies\Microsoft\Windows\Windows Search"
        path_search_current = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Search"
        try:
            if enable:
                SystemTweaksService.backup_registry_value("HKLM", path_search_policy, "DisableWebSearch")
                SystemTweaksService.backup_registry_value("HKLM", path_search_policy, "ConnectedSearchUseWeb")
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_search_policy, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "DisableWebSearch", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(key, "ConnectedSearchUseWeb", 0, winreg.REG_DWORD, 0)
                
                SystemTweaksService.backup_registry_value("HKCU", path_search_current, "BingSearchEnabled")
                SystemTweaksService.backup_registry_value("HKCU", path_search_current, "CortanaConsent")
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path_search_current, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "BingSearchEnabled", 0, winreg.REG_DWORD, 0)
                    winreg.SetValueEx(key, "CortanaConsent", 0, winreg.REG_DWORD, 0)
                logger.info("Bing Web Search and Cortana disabled in Windows Search.")
            else:
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_search_policy, "DisableWebSearch", None, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_search_policy, "ConnectedSearchUseWeb", None, winreg.REG_DWORD)
                
                SystemTweaksService.restore_registry_value_or_default("HKCU", path_search_current, "BingSearchEnabled", None, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKCU", path_search_current, "CortanaConsent", None, winreg.REG_DWORD)
                logger.info("Bing Web Search and Cortana settings restored to default.")
            
            # Restart SearchHost.exe to apply changes immediately
            try:
                SystemTweaksService.safe_subprocess_call(["taskkill", "/F", "/IM", "SearchHost.exe"], timeout=2)
            except Exception:
                pass
                
            return True
        except Exception as e:
            logger.error(f"Failed to apply/revert web search tweak: {str(e)}")
            return False

    @staticmethod
    def apply_telemetry_tasks_tweak(enable: bool) -> bool:
        """
        禁用/启用系统遥测与客户体验改善计划任务
        """
        try:
            action = "/disable" if enable else "/enable"
            tasks = [
                "\\Microsoft\\Windows\\Application Experience\\Microsoft Compatibility Appraiser",
                "\\Microsoft\\Windows\\Application Experience\\ProgramDataUpdater",
                "\\Microsoft\\Windows\\Application Experience\\StartupAppTask",
                "\\Microsoft\\Windows\\Customer Experience Improvement Program\\Consolidator",
                "\\Microsoft\\Windows\\Customer Experience Improvement Program\\UsbCeip"
            ]
            for t in tasks:
                cmd = ["schtasks.exe", "/change", "/tn", t, action]
                SystemTweaksService.safe_subprocess_call(cmd)
            if enable:
                logger.info("System telemetry and customer experience scheduled tasks disabled via schtasks.")
            else:
                logger.info("System telemetry and customer experience scheduled tasks restored to enabled via schtasks.")
            return True
        except Exception as e:
            logger.error(f"Failed to apply/revert telemetry tasks tweak: {str(e)}")
            return False

    @staticmethod
    def apply_prefetcher_tweak(enable: bool) -> bool:
        """
        禁用/启用 Prefetcher 预取服务缓存预载机制
        """
        path_pf = r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management\PrefetchParameters"
        try:
            if enable:
                SystemTweaksService.backup_registry_value("HKLM", path_pf, "EnablePrefetcher")
                SystemTweaksService.backup_registry_value("HKLM", path_pf, "EnableSuperfetch")
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_pf, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "EnablePrefetcher", 0, winreg.REG_DWORD, 0)
                    winreg.SetValueEx(key, "EnableSuperfetch", 0, winreg.REG_DWORD, 0)
                logger.info("Windows Prefetcher and Superfetch preloading disabled.")
            else:
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_pf, "EnablePrefetcher", 3, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_pf, "EnableSuperfetch", 3, winreg.REG_DWORD)
                logger.info("Windows Prefetcher and Superfetch preloading restored to default (3).")
                
            # Restart SysMain service to apply changes immediately (only if running)
            try:
                cmd_status = ["sc.exe", "query", "SysMain"]
                p = subprocess.Popen(cmd_status, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW) # nosec
                stdout, _ = p.communicate(timeout=2)
                status = stdout.decode("utf-8", errors="ignore").strip().lower()
                if "running" in status:
                    SystemTweaksService.safe_subprocess_call(["sc.exe", "stop", "SysMain"], timeout=5)
                    time.sleep(1)
                    SystemTweaksService.safe_subprocess_call(["sc.exe", "start", "SysMain"], timeout=5)
            except Exception:
                pass
                
            return True
        except Exception as e:
            logger.error(f"Failed to apply/revert prefetcher tweak: {str(e)}")
            return False

    @staticmethod
    def get_physical_adapter_names() -> list:
        """
        获取系统所有物理网卡的接口名称，使用 WMI, PowerShell, netsh 多重灾备检索机制
        """
        names = []
        # Method A: WMI COM
        if HAS_WIN32:
            has_com = False
            try:
                import pythoncom
                import win32com.client
                pythoncom.CoInitialize()
                has_com = True
                wmi = win32com.client.GetObject("winmgmts:\\\\.\\root\\StandardCimv2")
                adapters = wmi.ExecQuery("SELECT Name, InterfaceName FROM MSFT_NetAdapter WHERE ConnectorPresent = True")
                for a in adapters:
                    val = getattr(a, "Name", None) or getattr(a, "InterfaceName", None)
                    if val:
                        names.append(str(val))
            except Exception as e:
                logger.debug(f"Failed to query physical adapters via WMI COM: {e}")
            finally:
                if has_com:
                    pythoncom.CoUninitialize()
        
        if names:
            return list(set(names))
            
        # Method B: PowerShell fallback
        try:
            cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                   "Get-NetAdapter -Physical | Select-Object -ExpandProperty Name"]
            cmd = SystemTweaksService._resolve_absolute_cmd_path(cmd)
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
            if res.returncode == 0 and res.stdout.strip():
                for line in res.stdout.splitlines():
                    n = line.strip()
                    if n:
                        names.append(n)
        except Exception as e:
            logger.debug(f"Failed to query physical adapters via PowerShell: {e}")
            
        if names:
            return list(set(names))
            
        # Method C: netsh fallback
        try:
            cmd = ["netsh", "interface", "show", "interface"]
            cmd = SystemTweaksService._resolve_absolute_cmd_path(cmd)
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    if "Dedicated" in line:
                        parts = line.split("Dedicated", 1)
                        if len(parts) > 1:
                            n = parts[1].strip()
                            if n:
                                names.append(n)
        except Exception as e:
            logger.debug(f"Failed to query physical adapters via netsh: {e}")
            
        return list(set(names))

    @staticmethod
    def restart_physical_net_adapters() -> bool:
        """
        重启所有物理网卡以使 EEE, 中断合并, 冗余协议绑定微调立即生效
        使用单网卡串行重启 + 强制状态校验，彻底杜绝网卡断网且无法重新启用的 race condition
        """
        from core_commander.core.device_manager import DeviceManager
        
        names = SystemTweaksService.get_physical_adapter_names()
        if not names:
            logger.warning("No physical network adapters found to restart.")
            return False
            
        logger.info(f"Found physical adapters to restart: {names}")
        success = True
        for name in names:
            try:
                if not DeviceManager.restart_network_adapter(name):
                    success = False
                    logger.error(f"Failed to restart physical adapter: {name}")
                else:
                    logger.info(f"Successfully restarted physical adapter: {name}")
            except Exception as ex:
                success = False
                logger.error(f"Exception during restart of physical adapter {name}: {ex}")
        return success


class SystemTweakThread(QThread):
    """
    Asynchronously executes system registry/service tweaks in a background thread to prevent GUI freezing.
    Redirects detailed execution logs to the LogsPage in real-time.
    """
    log_signal = Signal(str, str)         # (message, level)
    finished_signal = Signal(bool, str)   # (success, message)
    
    def __init__(self, settings_dict: dict, cpu_vendor: str, gpu_vendor: str, do_backup: bool = True, pending_keys: list = None, parent=None, use_active_backup: bool = True):
        super().__init__(parent)
        self.settings_dict = settings_dict
        self.cpu_vendor = cpu_vendor
        self.gpu_vendor = gpu_vendor
        self.do_backup = do_backup
        self.pending_keys = pending_keys
        self.use_active_backup = use_active_backup
        
    def log(self, message: str, level: str = "info"):
        self.log_signal.emit(message, level)
        if level == "info":
            logger.info(message)
        elif level == "success":
            logger.info(f"[SUCCESS] {message}")
        elif level == "warning":
            logger.warning(message)
        elif level == "error":
            logger.error(message)
        elif level == "critical":
            logger.critical(message)

    def run_tweak_safely(self, tweak_name: str, func, *args, **kwargs) -> bool:
        from core_commander.utils.i18n import Trans
        try:
            func(*args, **kwargs)
            success_str = Trans.get("msg_op_success", "操作成功")
            self.log(f"{tweak_name} {success_str}。", "success")
            return True
        except Exception as e:
            is_access_denied = (
                (hasattr(e, 'winerror') and e.winerror == 5) or 
                (hasattr(e, 'args') and len(e.args) > 0 and e.args[0] == 5) or
                isinstance(e, PermissionError)
            )
            if is_access_denied:
                denied_msgs = {
                    "zh_CN": "[物理拦截] {}未生效: 该项目受Windows系统安全防护限制（如防篡改或只读保护），已跳过此安全项。",
                    "en_US": "[Security Bypass] {} not applied: This setting is restricted by Windows security protections (tamper protection or read-only), skipped.",
                    "ja_JP": "[セキュリティ回避] {}は適用されませんでした: この設定はWindowsのセキュリティ保護によって制限されています（スキップ）。",
                    "ko_KR": "[보안 우회] {}적용되지 않음: 이 설정은 Windows 보안 보호에 의해 제한되어 있으므로 건너뜁니다.",
                    "ru_RU": "[Обход безопасности] {} не применен: Этот параметр ограничен защитой Windows (пропущено).",
                    "de_DE": "[Sicherheitsumgehung] {} nicht angewendet: Diese Einstellung ist durch Windows-Sicherheitseinstellungen eingeschränkt (übersprungen).",
                    "fr_FR": "[Contournement de sécurité] {} non appliqué : ce paramètre est limité par la protection Windows (ignoré).",
                    "es_ES": "[Bypass de seguridad] {} no aplicado: este ajuste está restringido por la protección de Windows (omitido)."
                }
                lang = Trans.CURRENT_LANG
                msg = denied_msgs.get(lang, denied_msgs["zh_CN"]).format(tweak_name)
                self.log(msg, "warning")
            else:
                failed_str = Trans.get("msg_op_failed", "操作失败")
                self.log(f"{tweak_name} {failed_str}: {str(e)}", "error")
            return False
            
    def run(self):
        try:
            import pythoncom
            pythoncom.CoInitialize()
            has_com = True
        except ImportError:
            has_com = False

        from core_commander.utils.i18n import Trans
        lang = Trans.CURRENT_LANG

        # Localized general message keys
        log_messages = {
            "start": {
                "zh_CN": ">>> 开始部署系统级性能调优配置方案...",
                "en_US": ">>> Deploying system performance optimization strategy...",
                "ja_JP": ">>> システムパフォーマンス最適化ポリシーの展開を開始します...",
                "ko_KR": ">>> 시스템 성능 최적화 정책 배포 시작...",
                "ru_RU": ">>> Начало развертывания стратегии оптимизации производительности системы...",
                "de_DE": ">>> Bereitstellung der Systemleistungsoptimierungsstrategie gestartet...",
                "fr_FR": ">>> Déploiement de la stratégie d'optimisation des performances du système...",
                "es_ES": ">>> Iniciando el despliegue de la estrategia de optimización del rendimiento del sistema..."
            },
            "no_changes": {
                "zh_CN": "未检测到任何待变更的系统策略微调项，跳过本次配置部署。",
                "en_US": "No pending system tweaks detected. Skipping deployment.",
                "ja_JP": "変更待ちのシステム微調整項目は検出されませんでした。デプロイをスキップします。",
                "ko_KR": "변경 대기 중인 시스템 미세 조정 항목이 감지되지 않았습니다. 배포를 건너뜁니다.",
                "ru_RU": "Изменения в настройках системы не обнаружены. Пропуск развертывания.",
                "de_DE": "Keine ausstehenden Systemanpassungen erkannt. Bereitstellung übersprungen.",
                "fr_FR": "Aucune modification système détectée. Déploiement ignoré.",
                "es_ES": "No se detectaron ajustes del sistema pendientes. Omitiendo despliegue."
            },
            "no_changes_finished": {
                "zh_CN": "无策略变更需要部署",
                "en_US": "No policy changes to deploy.",
                "ja_JP": "デプロイするポリシーの変更はありません。",
                "ko_KR": "배포할 정책 변경 사항이 없습니다.",
                "ru_RU": "Нет изменений политики для развертывания.",
                "de_DE": "Keine Richtlinienänderungen bereitzustellen.",
                "fr_FR": "Aucun changement de politique à déployer.",
                "es_ES": "No hay cambios de política para desplegar."
            },
            "sync_general": {
                "zh_CN": "[通用] 正在同步通用调度设置 (后台进程隔离与状态守护看门狗状态)...",
                "en_US": "[General] Syncing general scheduler settings (background isolation & watchdog daemon)...",
                "ja_JP": "[全般] 一般のスケジューラ設定を同期しています (バックグラウンド分离とウォッチドッグ)...",
                "ko_KR": "[일반] 일반 스케줄러 설정 동기화 중 (백그라운드 격리 및 워치독 데몬)...",
                "ru_RU": "[Общие] Синхронизация общих настроек планировщика (изоляция фона и демон-наблюдатель)...",
                "de_DE": "[Allgemein] Allgemeine Scheduler-Einstellungen werden synchronisiert (Hintergrundisolierung & Watchdog)...",
                "fr_FR": "[Général] Synchronisation des paramètres généraux du planificateur (isolation arrière-plan & démon watchdog)...",
                "es_ES": "[General] Sincronizando la configuración general del planificador (aislamiento en segundo plano y watchdog)..."
            },
            "sync_general_success": {
                "zh_CN": "通用进程隔离与状态守护配置已写入参数缓存。",
                "en_US": "General isolation and watchdog settings written to parameter cache.",
                "ja_JP": "一般の分離とウォッチドッグ設定がパラメータキャッシュに書き込まれました。",
                "ko_KR": "일반 격리 및 워치독 설정이 매개변수 캐시에 기록되었습니다.",
                "ru_RU": "Общие настройки изоляции и наблюдателя записаны в кэш параметров.",
                "de_DE": "Allgemeine Isolierungs- und Watchdog-Einstellungen in den Parametercache geschrieben.",
                "fr_FR": "Paramètres d'isolation générale et de watchdog écrits dans le cache des paramètres.",
                "es_ES": "Configuración general de aislamiento y watchdog escrita en la caché de parámetros."
            },
            "restart_net": {
                "zh_CN": "[网络] 检测到网卡微调项变更，正在重启物理网卡以应用新参数...",
                "en_US": "[Network] Network tweaks changed. Restarting physical adapters to apply parameters...",
                "ja_JP": "[ネットワーク] 設定変更を検出しました。新しいパラメータを適用するためネットワークカードを再起動しています...",
                "ko_KR": "[네트워크] 네트워크 설정 변경이 감지되었습니다. 새 매개변수를 적용하기 위해 물리 네트워크 카드를 재부팅하는 중...",
                "ru_RU": "[Сеть] Обнаружены изменения настроек сети. Перезапуск физических адаптеров...",
                "de_DE": "[Netzwerk] Netzwerkänderungen erkannt. Physische Adapter werden neu gestartet...",
                "fr_FR": "[Réseau] Modifications réseau détectées. Redémarrage des adaptateurs physiques...",
                "es_ES": "[Red] Se detectaron cambios en los ajustes de red. Reiniciando adaptadores físicos..."
            },
            "restart_net_success": {
                "zh_CN": "[网络] 物理网卡已成功重置，新配置已立即生效。",
                "en_US": "[Network] Physical network adapters reset successfully. New settings are active.",
                "ja_JP": "[ネットワーク] ネットワークカードが正常にリセットされ、新しい設定が有効になりました。",
                "ko_KR": "[네트워크] 물리 네트워크 카드가 성공적으로 재설정되었으며 새 구성이 즉시 적용되었습니다.",
                "ru_RU": "[Сеть] Физические сетевые адаптеры успешно сброшены. Новые настройки активны.",
                "de_DE": "[Netzwerk] Physische Netzwerkadapter erfolgreich zurückgesetzt. Neue Einstellungen sind aktiv.",
                "fr_FR": "[Réseau] Adaptateurs réseau physiques réinitialisés avec succès. Les nouveaux paramètres sont actifs.",
                "es_ES": "[Red] Adaptadores de red físicos restablecidos con éxito. La nueva configuración está activa."
            },
            "all_success": {
                "zh_CN": ">>> 所有选定的系统级底层调优与进程调度策略已全部部署成功并生效！",
                "en_US": ">>> All selected system tweaks and process scheduler strategies deployed successfully!",
                "ja_JP": ">>> 選択されたすべてのシステム微調整およびプロセススケジューラポリシーが正常に展開され、有効になりました！",
                "ko_KR": ">>> 선택한 모든 시스템 미세 조정 및 프로세스 스케줄러 정책이 성공적으로 배포되어 적용되었습니다!",
                "ru_RU": ">>> Все выбранные системные настройки и стратегии планировщика процессов успешно развернуты!",
                "de_DE": ">>> Alle ausgewählten Systemoptimierungen und Prozess-Scheduler-Strategien erfolgreich bereitgestellt!",
                "fr_FR": ">>> Toutes les optimisations système et stratégies du planificateur de processus sélectionnées ont été déployées avec succès !",
                "es_ES": ">>> ¡Todas las optimizaciones del sistema y estrategias del planificador de procesos seleccionadas se han desplegado con éxito!"
            },
            "all_success_finished": {
                "zh_CN": "底层调优与进程调度策略已全部成功部署且生效。",
                "en_US": "System tweaks and scheduler strategies deployed and active.",
                "ja_JP": "システム微調整とスケジューラポリシーが展開され、有効になりました。",
                "ko_KR": "시스템 미세 조정 및 스케줄러 정책이 배포되어 활성화되었습니다.",
                "ru_RU": "Системные настройки и стратегии планировщика развернуты и активны.",
                "de_DE": "Systemoptimierungen und Scheduler-Strategien bereitgestellt und aktiv.",
                "fr_FR": "Optimisations système et stratégies du planificateur déployées et actives.",
                "es_ES": "Optimizaciones del sistema y estrategias del planificador desplegadas y activas."
            },
            "fatal_error": {
                "zh_CN": "[ERROR] 执行系统调优序列中遭遇致命异常",
                "en_US": "[ERROR] Fatal exception encountered during system tuning execution",
                "ja_JP": "[エラー] システム微調整シーケンスの実行中に致命的な例外が発生しました",
                "ko_KR": "[오류] 시스템 미세 조정 시퀀스 실행 중 치명적인 예외가 발생했습니다",
                "ru_RU": "[ОШИБКА] Критическое исключение при выполнении настройки системы",
                "de_DE": "[FEHLER] Schwerwiegende Ausnahme bei der Ausführung der Systemoptimierung aufgetreten",
                "fr_FR": "[ERREUR] Exception fatale lors de l'exécution de l'optimisation système",
                "es_ES": "[ERROR] Excepción fatal encontrada durante la ejecución de la optimización del sistema"
            },
            "fatal_error_finished": {
                "zh_CN": "系统调优序列部署失败",
                "en_US": "System tuning deployment failed",
                "ja_JP": "システム微調整シーケンスの展開に失敗しました",
                "ko_KR": "시스템 미세 조정 시퀀스 배포 실패",
                "ru_RU": "Ошибка развертывания последовательности настроек системы",
                "de_DE": "Bereitstellung der Systemoptimierungssequenz fehlgeschlagen",
                "fr_FR": "Échec du déploiement de la séquence d'optimisation système",
                "es_ES": "Fallo en el despliegue de la secuencia de optimización del sistema"
            }
        }

        def get_log_msg(msg_key):
            return log_messages.get(msg_key, {}).get(lang, log_messages[msg_key].get("zh_CN"))

        # Mapping tweak IDs to translation titles dynamically
        tweak_key_mapping = {
            "disable_windows_visual_effects": "chk_visual_effects_title",
            "disable_windows_transparency": "chk_transparency_title",
            "win32_prio_sep": "win32_prio_title",
            "disable_hpet": "chk_hpet_title",
            "enable_dwm_tweak": "chk_dwm_title",
            "enable_dpc_latency_tweak": "chk_dpc_title",
            "keyboard_repeat_delay_level": "key_repeat_title",
            "enable_usb_low_latency_tweak": "chk_usb_lat_title",
            "enable_usb_imod_tweak": "chk_imod_title",
            "enable_mouse_latency_tweak": "chk_mouse_lat_title",
            "disable_gpu_preemption": "chk_preemption_title",
            "enable_dwm_super_wet_tweak": "chk_dwm_wet_title",
            "enable_directx_tweaks": "chk_directx_title",
            "enable_gpu_firmware_tweak": "chk_gpu_firmware_title",
            "enable_gpu_pstate_tweak": "chk_gpu_pstate_title",
            "enable_gpu_irq_tweak": "chk_gpu_irq_title",
            "disable_hags": "chk_hags_title",
            "disable_pcipower": "chk_pcipower_title",
            "enable_nvidia_nip": "nip_title",
            "enable_ram_optimization": "chk_ram_opt_title",
            "enable_nvme_optimization": "chk_nvme_opt_title",
            "disable_memory_compression": "chk_memory_comp_title",
            "enable_config_alloc_tweak": "chk_config_alloc_title",
            "disable_useless_services": "chk_services_title",
            "disable_wsearch_tweak": "chk_wsearch_title",
            "disable_spectre_meltdown": "chk_spectre_title",
            "disable_gamedvr": "chk_gamedvr_title",
            "enable_device_power_tweak": "chk_dev_power_title",
            "enable_uac_tweak": "chk_uac_title",
            "enable_desktop_heap_tweak": "chk_desktop_heap_title",
            "enable_download_maps_tweak": "chk_download_maps_title",
            "enable_autoshare_tweak": "chk_autoshare_title",
            "enable_autorun_tweak": "chk_autorun_title",
            "disable_copilot": "chk_copilot_title",
            "disable_security_notifications": "chk_security_notifications_title",
            "disable_defender": "chk_defender_title",
            "disable_smartscreen": "chk_smartscreen_title",
            "disable_firewall": "chk_firewall_title",
            "enable_ultimate_network_tweak": "chk_ult_net_title",
            "enable_dns_tweak": "chk_dns_title",
            "enable_driver_priority_tweak": "chk_driver_prio_title",
            "disable_hyperv_virtualization": "chk_hyperv_title",
            "enable_widgets_tweak": "chk_widgets_title",
            "enable_sticky_keys_tweak": "chk_sticky_keys_title",
            "enable_startup_delay_tweak": "chk_startup_delay_title",
            "enable_menu_delay_tweak": "chk_menu_delay_title",
            "enable_settings_sync_tweak": "chk_settings_sync_title",
            "enable_dynamic_lighting_tweak": "chk_dynamic_lighting_title",
            "enable_gpu_msi_tweak": "chk_gpu_msi_title",
            "enable_xbox_save_tweak": "chk_xbox_save_title",
            "enable_store_auto_update_tweak": "chk_store_auto_update_title",
            "enable_vulnerable_driver_blocklist_tweak": "chk_vulnerable_driver_blocklist_title",
            "enable_prevent_device_encryption_tweak": "chk_prevent_device_encryption_title",
            "enable_spotlight_tweak": "chk_spotlight_title",
            "enable_net_imod_tweak": "chk_net_imod_title",
            "enable_net_bindings_tweak": "chk_net_bindings_title",
            "enable_global_fse_tweak": "chk_global_fse_title",
            "enable_irq_affinity_tweak": "chk_irq_affinity_title",
            "enable_power_throttling_tweak": "chk_power_throttling_title",
            "enable_tcp_bbr_tweak": "chk_tcp_bbr_title",
            "enable_eee_tweak": "chk_eee_title",
            "enable_web_search_tweak": "chk_web_search_title",
            "enable_telemetry_tasks_tweak": "chk_telemetry_tasks_title",
            "enable_prefetcher_tweak": "chk_prefetcher_title",
            "enable_extreme_debloat_tweak": "chk_extreme_debloat_title",
            "enable_consult_interests_tweak": "chk_consult_interests_title",
            "enable_tips_suggestions_tweak": "chk_tips_suggestions_title",
            "enable_bg_apps_tweak": "chk_bg_apps_title",
            "enable_map_updates_tweak": "chk_map_updates_title",
            "enable_timer_resolution_tweak": "chk_timer_res_title",
            "enable_naraka_priority": "chk_naraka_title",
            "keyboard_queue_size": "keyboard_queue_title",
            "mouse_queue_size": "mouse_queue_title",
            "enable_gpu_optimization": "chk_gpu_opt_title_generic",
            "enable_custom_power_plan": "chk_intel_plan_title",
            "enable_core_parking": "chk_parking_title",
            "enable_epp_max": "chk_epp_title",
            "enable_network_tweak": "chk_network_title",
            "enable_game_fse_tweak": "chk_game_fse_title",
            "enable_game_gpu_preference_tweak": "chk_game_gpu_preference_title"
        }

        def get_tweak_display_name(key, default_name):
            trans_key = tweak_key_mapping.get(key)
            if trans_key:
                if key == "enable_custom_power_plan":
                    from core_commander.core.topology import TopologyEngine
                    vendor = TopologyEngine.get_cpu_vendor()
                    if vendor == "AMD":
                        trans_key = "chk_amd_plan_title"
                elif key == "enable_gpu_optimization":
                    vendor = SystemTweaksService.get_gpu_vendor()
                    if vendor == "NVIDIA":
                        trans_key = "chk_gpu_opt_title_nvidia"
                    elif vendor == "AMD":
                        trans_key = "chk_gpu_opt_title_amd"
                return Trans.get(trans_key)
            return default_name

        try:
            SystemTweaksService.enable_backup = self.do_backup
            if self.do_backup and self.use_active_backup:
                import datetime
                now_str = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                SystemTweaksService.active_backup_filename = f"backup_{now_str}.json"
            else:
                SystemTweaksService.active_backup_filename = None
            SystemTweaksService._backup_cache = None
            SystemTweaksService._backup_cache_path = None
            SystemTweaksService._backup_dirty = False
                
            self.log(get_log_msg("start"), "info")
            
            run_all = self.pending_keys is None
            if not run_all and not self.pending_keys:
                self.log(get_log_msg("no_changes"), "success")
                self.finished_signal.emit(True, get_log_msg("no_changes_finished"))
                return
            
            # --- 基础设置 ---
            self.log(get_log_msg("sync_general"), "info")
            self.log(get_log_msg("sync_general_success"), "success")
            
            # 动态导入 TweakRegistry 避免循环依赖
            from core_commander.core.tweaks import TweakRegistry
            
            # 所有的微调 ID 对应的显示名称和默认值映射
            tweak_metadata = {
                "disable_windows_visual_effects": ("系统视觉特效精简策略", False),
                "disable_windows_transparency": ("窗口透明度精简调优策略", False),
                "win32_prio_sep": ("前后台进程时间片轮转权重策略配置", 0),
                "disable_hpet": ("高精度事件计时器 (HPET) 状态配置", False),
                "enable_dwm_tweak": ("桌面窗口管理器 (DWM) 低延迟输入与渲染模式", False),
                "enable_dpc_latency_tweak": ("延迟过程调用 (DPC) 中断与延迟限制配置", False),
                "keyboard_repeat_delay_level": ("键盘重复延迟与输入速率调优策略", 0),
                "enable_usb_low_latency_tweak": ("USB 总线低延迟传输调优策略", False),
                "enable_usb_imod_tweak": ("XHCI 中断裁决与 USB 中断过滤策略配置", False),
                "enable_mouse_latency_tweak": ("鼠标物理平滑曲线调优策略", False),
                "disable_gpu_preemption": ("显卡 GPU 抢占式硬件调度配置", False),
                "enable_dwm_super_wet_tweak": ("桌面窗口管理器 (DWM) 渲染重定向配置", False),
                "enable_directx_tweaks": ("DirectX 图形流水线全局渲染缓存配置", False),
                "enable_gpu_firmware_tweak": ("显卡固件级硬件加速与 PCI 链路调优配置", False),
                "enable_gpu_pstate_tweak": ("显卡高性能 P-State 状态锁定配置", False),
                "enable_gpu_irq_tweak": ("显卡 GPU 中断优先级提权策略配置", False),
                "disable_hags": ("硬件加速 GPU 计划 (HAGS) 调优策略配置", False),
                "disable_pcipower": ("PCIe ASPM 链路电源管理配置", False),
                "enable_nvidia_nip": ("NVIDIA 显卡高性能参数预设", False),
                "enable_ram_optimization": ("SvcHost 自适应拆分内存阈值配置", False),
                "enable_nvme_optimization": ("存储介质 I/O 与文件系统读写调优配置", False),
                "disable_memory_compression": ("内存页面压缩机制调优策略", False),
                "enable_config_alloc_tweak": ("系统内存物理分区与分配池调优策略", False),
                "disable_useless_services": ("后台遥测与冗余服务精简配置", False),
                "disable_wsearch_tweak": ("Windows Search 文件索引服务禁用配置", False),
                "disable_spectre_meltdown": ("侧信道安全补丁缓解配置", False),
                "disable_gamedvr": ("Xbox GameDVR 录制与广播屏蔽配置", False),
                "enable_device_power_tweak": ("设备驱动电源挂起禁用配置", False),
                "enable_uac_tweak": ("UAC 级联安全桌面分配优化配置", False),
                "enable_desktop_heap_tweak": ("桌面堆内存分配调优策略配置", False),
                "enable_download_maps_tweak": ("离线地图下载服务禁用配置", False),
                "enable_autoshare_tweak": ("系统管理型网络共享禁用配置", False),
                "enable_autorun_tweak": ("物理驱动器自动播放策略屏蔽配置", False),
                "disable_copilot": ("Windows Copilot 屏蔽配置", False),
                "disable_security_notifications": ("Windows 安全中心系统通知屏蔽配置", False),
                "disable_defender": ("Windows Defender 监控行为与防病毒服务禁用配置", False),
                "disable_smartscreen": ("Windows SmartScreen 文件安全评估屏蔽配置", False),
                "disable_firewall": ("Windows 防火墙服务与包过滤策略禁用配置", False),
                "enable_ultimate_network_tweak": ("网卡高级多队列接收与吞吐调优策略配置", False),
                "enable_dns_tweak": ("DNS 解析调度优先级与缓存 TTL 权重调优策略配置", False),
                "enable_driver_priority_tweak": ("硬件中断 (IRQ) 与图形渲染线程优先级分配调优", False),
                "disable_hyperv_virtualization": ("Hyper-V 虚拟化架构及内核完整性校验限制禁用配置", False),
                "enable_widgets_tweak": ("Windows 11 小部件面板禁用配置", False),
                "enable_sticky_keys_tweak": ("粘滞键与键盘辅助功能屏蔽配置", False),
                "enable_startup_delay_tweak": ("系统登录后应用自启动延迟消除配置", False),
                "enable_menu_delay_tweak": ("经典上下文右键菜单显示延迟消除配置", False),
                "enable_settings_sync_tweak": ("Microsoft 个人账户设置同步禁用配置", False),
                "enable_dynamic_lighting_tweak": ("Windows 11 原生外设动态照明禁用配置", False),
                "enable_gpu_msi_tweak": ("显卡 MSI 中断模式与 High 优先级调度配置", False),
                "enable_network_msi_tweak": ("网卡 MSI 中断模式与 High 优先级调度配置", False),
                "enable_storage_msi_tweak": ("存储控制器 MSI 中断模式与 High 优先级调度配置", False),
                "enable_dwm_presentation_tweak": ("DWM 窗口化呈现延迟绕过优化配置", False),
                "enable_xbox_save_tweak": ("Xbox Live 游戏存档云同步与后台任务禁用配置", False),
                "enable_store_auto_update_tweak": ("Microsoft Store 自动更新与应用静默安装推广禁用配置", False),
                "enable_vulnerable_driver_blocklist_tweak": ("Windows 11 驱动程序易受攻击模块黑名单禁用配置", False),
                "enable_prevent_device_encryption_tweak": ("Windows 自动 BitLocker 设备加密禁用配置", False),
                "enable_spotlight_tweak": ("Windows 聚焦与锁屏广告建议禁用配置", False),
                "enable_net_imod_tweak": ("网卡硬件中断合并限制 (IMOD) 调优策略配置", False),
                "enable_net_bindings_tweak": ("网卡冗余网络组件与 IPv6 精简微调策略配置", False),
                "enable_global_fse_tweak": ("全局禁用全屏优化 (FSE) 配置", False),
                "enable_irq_affinity_tweak": ("显卡与网卡硬件中断 CPU 亲和性分配调优", False),
                "enable_power_throttling_tweak": ("CPU 全局电源节流禁用配置", False),
                "enable_tcp_bbr_tweak": ("TCP BBR 拥塞算法与延迟优化配置", False),
                "enable_eee_tweak": ("网卡以太网节能与低功耗挂起禁用配置", False),
                "enable_web_search_tweak": ("开始菜单 Bing 网络搜索结果禁用配置", False),
                "enable_telemetry_tasks_tweak": ("系统遥测与客户体验改善计划任务禁用配置", False),
                "enable_prefetcher_tweak": ("Prefetcher 内存预载与预取禁用配置", False),
                "enable_extreme_debloat_tweak": ("深度极限精简策略 (硬核电竞级)", False)
            }

            # 决定哪些微调项需要执行
            active_keys = []
            if run_all:
                active_keys = list(tweak_metadata.keys())
            else:
                active_keys = [k for k in self.pending_keys if k in tweak_metadata]

            # 保证按原始逻辑中标准微调的顺序依次执行
            ordered_keys = [
                "disable_windows_visual_effects", "disable_windows_transparency", "win32_prio_sep", "disable_hpet",
                "enable_dwm_tweak", "enable_dpc_latency_tweak", "keyboard_repeat_delay_level", "enable_usb_low_latency_tweak",
                "enable_usb_imod_tweak", "enable_mouse_latency_tweak", "disable_gpu_preemption", "enable_dwm_super_wet_tweak",
                "enable_directx_tweaks", "enable_gpu_firmware_tweak", "enable_gpu_pstate_tweak", "enable_gpu_irq_tweak",
                "disable_hags", "disable_pcipower", "enable_nvidia_nip", "enable_ram_optimization",
                "enable_nvme_optimization", "disable_memory_compression", "enable_config_alloc_tweak", "disable_useless_services",
                "disable_wsearch_tweak", "disable_spectre_meltdown", "disable_gamedvr", "enable_device_power_tweak",
                "enable_uac_tweak", "enable_desktop_heap_tweak", "enable_download_maps_tweak", "enable_autoshare_tweak",
                "enable_autorun_tweak", "disable_copilot", "disable_security_notifications", "disable_defender",
                "disable_smartscreen", "disable_firewall", "enable_ultimate_network_tweak", "enable_dns_tweak",
                "enable_driver_priority_tweak", "disable_hyperv_virtualization", "enable_widgets_tweak", "enable_sticky_keys_tweak",
                "enable_startup_delay_tweak", "enable_menu_delay_tweak", "enable_settings_sync_tweak", "enable_dynamic_lighting_tweak",
                "enable_gpu_msi_tweak", "enable_network_msi_tweak", "enable_storage_msi_tweak", "enable_dwm_presentation_tweak", "enable_xbox_save_tweak", "enable_store_auto_update_tweak", "enable_vulnerable_driver_blocklist_tweak",
                "enable_prevent_device_encryption_tweak", "enable_spotlight_tweak", "enable_net_imod_tweak", "enable_net_bindings_tweak",
                "enable_global_fse_tweak", "enable_irq_affinity_tweak", "enable_power_throttling_tweak", "enable_tcp_bbr_tweak",
                "enable_eee_tweak", "enable_web_search_tweak", "enable_telemetry_tasks_tweak", "enable_prefetcher_tweak",
                "enable_extreme_debloat_tweak"
            ]

            from concurrent.futures import ThreadPoolExecutor

            def run_single_tweak(key):
                name, default_val = tweak_metadata[key]
                val = self.settings_dict.get(key, default_val)
                tweak = TweakRegistry.get(key)
                if tweak:
                    localized_name = get_tweak_display_name(key, name)
                    self.run_tweak_safely(localized_name, tweak.apply, val)

            to_run = [key for key in ordered_keys if key in active_keys]
            if to_run:
                with ThreadPoolExecutor(max_workers=8) as executor:
                    # Execute tweaks concurrently
                    list(executor.map(run_single_tweak, to_run))

            # 重启物理网卡的条件判断
            restart_net = False
            net_keys = {"enable_eee_tweak", "enable_net_imod_tweak", "enable_net_bindings_tweak"}
            if run_all:
                restart_net = True
            elif self.pending_keys:
                if any(k in self.pending_keys for k in net_keys):
                    restart_net = True
            
            if restart_net:
                self.log(get_log_msg("restart_net"), "info")
                SystemTweaksService.restart_physical_net_adapters()
                self.log(get_log_msg("restart_net_success"), "success")

            # --- 特殊自定义微调逻辑执行 ---

            # CPU 核心停车与维持能源参数
            if run_all or "enable_core_parking" in self.pending_keys or "enable_epp_max" in self.pending_keys:
                enable_parking = self.settings_dict.get("enable_core_parking", False)
                tweak_parking = TweakRegistry.get("enable_core_parking")
                if tweak_parking:
                    localized_name = get_tweak_display_name("enable_core_parking", "CPU核心停车与维持能源优化参数")
                    self.run_tweak_safely(localized_name, tweak_parking.apply, enable_parking)

            # 系统计时器分辨率
            if run_all or "enable_timer_resolution_tweak" in self.pending_keys:
                enable_timer = self.settings_dict.get("enable_timer_resolution_tweak", False)
                tweak_timer = TweakRegistry.get("enable_timer_resolution_tweak")
                if tweak_timer:
                    localized_name = get_tweak_display_name("enable_timer_resolution_tweak", "系统计时器分辨率调优策略")
                    self.run_tweak_safely(localized_name, tweak_timer.apply, enable_timer)

            # 前台关键进程优先级
            if run_all or "enable_naraka_priority" in self.pending_keys:
                enable_naraka = self.settings_dict.get("enable_naraka_priority", False)
                target_exe = self.settings_dict.get("target_process_name", "")
                tweak_priority = TweakRegistry.get("enable_naraka_priority")
                if tweak_priority:
                    localized_name = get_tweak_display_name("enable_naraka_priority", "前台关键进程优先级提权策略")
                    if target_exe:
                        localized_name = f"{localized_name} (Target: {target_exe})"
                    self.run_tweak_safely(localized_name, tweak_priority.apply, enable_naraka, target_process_name=target_exe)

            # HID 缓冲区队列大小
            if run_all or "keyboard_queue_size" in self.pending_keys or "mouse_queue_size" in self.pending_keys:
                kb_size = self.settings_dict.get("keyboard_queue_size", 100)
                m_size = self.settings_dict.get("mouse_queue_size", 100)
                tweak_hid = TweakRegistry.get("keyboard_queue_size") or TweakRegistry.get("mouse_queue_size")
                if tweak_hid:
                    localized_name = get_tweak_display_name("keyboard_queue_size", "人机接口设备 (HID) 输入缓冲区队列配置")
                    self.run_tweak_safely(localized_name, tweak_hid.apply, kb_size, mouse_size=m_size)

            # GPU 特有属性微调
            if run_all or "enable_gpu_optimization" in self.pending_keys:
                enable_gpu_opt = self.settings_dict.get("enable_gpu_optimization", False)
                tweak_gpu = TweakRegistry.get("enable_gpu_optimization")
                if tweak_gpu:
                    localized_name = get_tweak_display_name("enable_gpu_optimization", "显卡驱动遥测净化与消息信号中断 (MSI-Mode) 调优")
                    self.run_tweak_safely(localized_name, tweak_gpu.apply, enable_gpu_opt, gpu_vendor=self.gpu_vendor)

            # 高性能电源配置方案
            if run_all or "enable_custom_power_plan" in self.pending_keys:
                enable_power = self.settings_dict.get("enable_custom_power_plan", False)
                tweak_power = TweakRegistry.get("enable_custom_power_plan")
                if tweak_power:
                    localized_name = get_tweak_display_name("enable_custom_power_plan", "系统专属高性能电源配置方案")
                    self.run_tweak_safely(localized_name, tweak_power.apply, enable_power)

            # 资讯栏与系统建议提示屏蔽
            if run_all or "enable_consult_interests_tweak" in self.pending_keys or "enable_tips_suggestions_tweak" in self.pending_keys:
                enable_consult = self.settings_dict.get("enable_consult_interests_tweak", False)
                enable_tips = self.settings_dict.get("enable_tips_suggestions_tweak", False)
                tweak_feeds = TweakRegistry.get("enable_consult_interests_tweak") or TweakRegistry.get("enable_tips_suggestions_tweak")
                if tweak_feeds:
                    localized_name = get_tweak_display_name("enable_consult_interests_tweak", "资讯栏与系统交互建议提示屏蔽配置")
                    self.run_tweak_safely(localized_name, tweak_feeds.apply, enable_consult or enable_tips)

            # UWP 后台应用与地图服务
            if run_all or "enable_bg_apps_tweak" in self.pending_keys or "enable_map_updates_tweak" in self.pending_keys:
                enable_bg_apps = self.settings_dict.get("enable_bg_apps_tweak", False)
                enable_map_upd = self.settings_dict.get("enable_map_updates_tweak", False)
                tweak_bg = TweakRegistry.get("enable_bg_apps_tweak") or TweakRegistry.get("enable_map_updates_tweak")
                if tweak_bg:
                    localized_name = get_tweak_display_name("enable_bg_apps_tweak", "UWP 后台应用运行与地图更新服务禁用配置")
                    self.run_tweak_safely(localized_name, tweak_bg.apply, enable_bg_apps or enable_map_upd)

            # TCP 协议栈无延迟与 Nagle 限制配置
            if run_all or "enable_network_tweak" in self.pending_keys:
                enable_net = self.settings_dict.get("enable_network_tweak", False)
                tweak_nagle = TweakRegistry.get("enable_network_tweak")
                if tweak_nagle:
                    localized_name = get_tweak_display_name("enable_network_tweak", "TCP 协议栈无延迟与 Nagle 算法限制配置")
                    self.run_tweak_safely(localized_name, tweak_nagle.apply, enable_net)

            # 禁用全屏优化 (FSE)
            if run_all or "enable_game_fse_tweak" in self.pending_keys:
                tweak_fse = TweakRegistry.get("enable_game_fse_tweak")
                if tweak_fse:
                    localized_name = get_tweak_display_name("enable_game_fse_tweak", "当前游戏禁用全屏优化配置")
                    self.run_tweak_safely(localized_name, tweak_fse.apply, self.settings_dict.get("enable_game_fse_tweak", False), game_path=self.settings_dict.get("target_process_path", ""))

            # 强制高性能独显配置
            if run_all or "enable_game_gpu_preference_tweak" in self.pending_keys:
                tweak_gpu_pref = TweakRegistry.get("enable_game_gpu_preference_tweak")
                if tweak_gpu_pref:
                    localized_name = get_tweak_display_name("enable_game_gpu_preference_tweak", "当前游戏强制高性能独显配置")
                    self.run_tweak_safely(localized_name, tweak_gpu_pref.apply, self.settings_dict.get("enable_game_gpu_preference_tweak", False), game_path=self.settings_dict.get("target_process_path", ""))

            self.log(get_log_msg("all_success"), "success")
            self.finished_signal.emit(True, get_log_msg("all_success_finished"))
        except Exception as e:
            self.log(f"{get_log_msg('fatal_error')}: {str(e)}", "critical")
            self.finished_signal.emit(False, f"{get_log_msg('fatal_error_finished')}: {str(e)}")
        finally:
            try:
                SystemTweaksService.flush_backup_data()
            except Exception:  # nosec
                pass
            SystemTweaksService.active_backup_filename = None
            SystemTweaksService._backup_cache = None
            SystemTweaksService._backup_cache_path = None
            SystemTweaksService._backup_dirty = False

            if has_com:
                try:
                    pythoncom.CoUninitialize()
                except Exception:  # nosec
                    pass

