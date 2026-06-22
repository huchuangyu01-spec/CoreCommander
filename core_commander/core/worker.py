# -*- coding: utf-8 -*-
import re
import os
import psutil
from PySide6.QtCore import QThread, Signal
from core_commander.core.memory import MemoryService
from core_commander.core.power import PowerService
from core_commander.core.isolation import ProcessIsolationService
from core_commander.utils.logger import logger
from core_commander.utils.device import get_pci_device_ids

try:
    import win32com.client
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

import ctypes
from ctypes import wintypes
import uuid
import winreg
import subprocess  # nosec

# Define GUID struct for ctypes
class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8)
    ]
    
    def __init__(self, uuid_str):
        u = uuid.UUID(uuid_str)
        self.Data1 = u.time_low
        self.Data2 = u.time_mid
        self.Data3 = u.time_hi_version
        self.Data4 = (ctypes.c_ubyte * 8)(*u.bytes[8:])

# Global Windows APIs (Ctypes) declarations
try:
    powrprof = ctypes.windll.powrprof
    PowerGetActiveScheme = powrprof.PowerGetActiveScheme
    PowerGetActiveScheme.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    PowerGetActiveScheme.restype = wintypes.DWORD

    PowerReadACValueIndex = powrprof.PowerReadACValueIndex
    PowerReadACValueIndex.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(GUID),
        ctypes.POINTER(GUID),
        ctypes.POINTER(GUID),
        ctypes.POINTER(wintypes.DWORD)
    ]
    PowerReadACValueIndex.restype = wintypes.DWORD
    
    kernel32 = ctypes.windll.kernel32
    
    LocalFree = kernel32.LocalFree
    LocalFree.argtypes = [ctypes.c_void_p]
    LocalFree.restype = ctypes.c_void_p

    OpenThread = kernel32.OpenThread
    OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    OpenThread.restype = ctypes.c_void_p

    SetThreadIdealProcessor = kernel32.SetThreadIdealProcessor
    SetThreadIdealProcessor.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    SetThreadIdealProcessor.restype = wintypes.DWORD

    SetThreadAffinityMask = kernel32.SetThreadAffinityMask
    SetThreadAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    SetThreadAffinityMask.restype = ctypes.c_size_t

    CloseHandle = kernel32.CloseHandle
    CloseHandle.argtypes = [ctypes.c_void_p]
    CloseHandle.restype = wintypes.BOOL

    THREAD_SET_INFORMATION = 0x0020
    THREAD_QUERY_INFORMATION = 0x0040

    HAS_WIN32_CTYPES = True
except Exception:
    THREAD_SET_INFORMATION = 0x0020
    THREAD_QUERY_INFORMATION = 0x0040
    HAS_WIN32_CTYPES = False

# Global Helpers for system scanning
def get_reg_val(hkey, path, name):
    try:
        with winreg.OpenKey(hkey, path, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, name)
            return val
    except Exception:
        return None

def is_service_disabled(svc_name):
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"SYSTEM\\CurrentControlSet\\Services\\{svc_name}", 0, winreg.KEY_READ) as key:
            start_type, _ = winreg.QueryValueEx(key, "Start")
            return start_type == 4
    except FileNotFoundError:
        return True
    except Exception:
        return False

def is_service_running(svc_name):
    try:
        import win32service
        hscm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
        try:
            hs = win32service.OpenService(hscm, svc_name, win32service.SERVICE_QUERY_STATUS)
            try:
                status = win32service.QueryServiceStatus(hs)
                return status[1] == win32service.SERVICE_RUNNING
            finally:
                win32service.CloseServiceHandle(hs)
        finally:
            win32service.CloseServiceHandle(hscm)
    except Exception:
        try:
            import subprocess
            out = subprocess.check_output(["sc.exe", "query", svc_name], startupinfo=subprocess.STARTUPINFO(dwFlags=subprocess.STARTF_USESHOWWINDOW))
            return b"RUNNING" in out
        except Exception:
            return False

def is_process_running(proc_name):
    import psutil
    for proc in psutil.process_iter(['name']):
        try:
            if proc.info['name'] and proc.info['name'].lower() == proc_name.lower():
                return True
        except Exception:
            pass
    return False

def get_wmi_client(namespace="root\\cimv2"):
    try:
        import win32com.client
        return win32com.client.GetObject(f"winmgmts:\\\\.\\{namespace}")
    except Exception:
        return None

def get_active_physical_adapter_subkeys():
    subkeys = []
    path_class = r"SYSTEM\CurrentControlSet\Control\Class\{4D36E972-E325-11CE-BFC1-08002BE10318}"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_class, 0, winreg.KEY_READ) as root_key:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root_key, i)
                    if len(sub) == 4 and sub.isdigit():
                        sub_path = f"{path_class}\\{sub}"
                        try:
                            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub_path, 0, winreg.KEY_READ) as sub_key:
                                char_val, _ = winreg.QueryValueEx(sub_key, "Characteristics")
                                if char_val & 0x4:
                                    subkeys.append(sub)
                        except FileNotFoundError:
                            pass
                    i += 1
                except OSError:
                    break
    except Exception:
        return None
    return subkeys

def get_current_timer_resolution_ms():
    try:
        import ctypes
        min_r = ctypes.c_ulong()
        max_r = ctypes.c_ulong()
        cur_r = ctypes.c_ulong()
        if ctypes.windll.ntdll.NtQueryTimerResolution(ctypes.byref(min_r), ctypes.byref(max_r), ctypes.byref(cur_r)) == 0:
            return cur_r.value / 10000.0
    except Exception:
        pass
    return None

def get_active_keyboard_params():
    try:
        import ctypes
        d = ctypes.c_int()
        s = ctypes.c_int()
        if ctypes.windll.user32.SystemParametersInfoW(0x0016, 0, ctypes.byref(d), 0) and \
           ctypes.windll.user32.SystemParametersInfoW(0x000A, 0, ctypes.byref(s), 0):
            return d.value, s.value
    except Exception:
        pass
    return None

def check_fsutil_nvme_opts():
    last_access_disabled = False
    disable_8dot3_ok = False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            try:
                val_la, _ = winreg.QueryValueEx(key, "NtfsDisableLastAccessUpdate")
                if val_la in (1, 3, 0x80000001, 0x80000003):
                    last_access_disabled = True
            except Exception:
                pass
                
            try:
                val_83, _ = winreg.QueryValueEx(key, "NtfsDisable8dot3NameCreation")
                if val_83 != 0:
                    disable_8dot3_ok = True
            except Exception:
                pass
    except Exception:
        pass
    return last_access_disabled, disable_8dot3_ok

def get_active_power_scheme_uuid():
    if not HAS_WIN32_CTYPES:
        return None
    try:
        active_policy_ptr = ctypes.c_void_p()
        ret = PowerGetActiveScheme(None, ctypes.byref(active_policy_ptr))
        if ret == 0:
            try:
                active_guid = ctypes.cast(active_policy_ptr, ctypes.POINTER(GUID)).contents
                import uuid
                node = int.from_bytes(active_guid.Data4[2:8], byteorder='big')
                u = uuid.UUID(fields=(active_guid.Data1, active_guid.Data2, active_guid.Data3, active_guid.Data4[0], active_guid.Data4[1], node))
                return str(u)
            finally:
                LocalFree(active_policy_ptr)
    except Exception:  # nosec
        pass
    
    # Fallback to registry
    try:
        key_path = r"SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_READ) as key:
            active_guid, _ = winreg.QueryValueEx(key, "ActivePowerScheme")
            return active_guid
    except Exception:  # nosec
        pass
    return None

def get_effective_power_setting(subgroup_guid_str, setting_guid_str):
    if not HAS_WIN32_CTYPES:
        return None
    try:
        active_policy_ptr = ctypes.c_void_p()
        ret = PowerGetActiveScheme(None, ctypes.byref(active_policy_ptr))
        if ret == 0:
            try:
                active_guid = ctypes.cast(active_policy_ptr, ctypes.POINTER(GUID)).contents
                subgroup_guid = GUID(subgroup_guid_str)
                setting_guid = GUID(setting_guid_str)
                ac_val = wintypes.DWORD()
                res = PowerReadACValueIndex(None, ctypes.byref(active_guid), ctypes.byref(subgroup_guid), ctypes.byref(setting_guid), ctypes.byref(ac_val))
                if res == 0:
                    return ac_val.value
            finally:
                LocalFree(active_policy_ptr)
    except Exception:  # nosec
        pass
    return None

def is_memory_compression_disabled(settings=None):
    if SystemStateScannerWorker._memory_comp_cache is not None:
        return SystemStateScannerWorker._memory_comp_cache
    
    res = False
    if HAS_WIN32:
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            wmi_mma = None
            cls = None
            out = None
            try:
                wmi_mma = win32com.client.GetObject("winmgmts:{impersonationLevel=impersonate}!\\\\.\\root\\Microsoft\\Windows\\PS_MMAgent")
                cls = wmi_mma.Get("PS_MMAgent")
                out = cls.ExecMethod_("Get")
                cmdlet_output = out.Properties_("CmdletOutput").Value
                if cmdlet_output:
                    for prop in cmdlet_output.Properties_:
                        if prop.Name == "MemoryCompression":
                            res = prop.Value is False
                SystemStateScannerWorker._memory_comp_cache = res
                return res
            finally:
                out = None
                cls = None
                wmi_mma = None
                pythoncom.CoUninitialize()
        except Exception:  # nosec
            pass

    if settings is not None:
        res = settings.disable_memory_compression
        SystemStateScannerWorker._memory_comp_cache = res
        return res

    try:
        out = subprocess.check_output(  # nosec
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", "(Get-MMAgent).MemoryCompression"],
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=10
        ).decode("gbk", errors="ignore").strip()
        res = "False" in out
        SystemStateScannerWorker._memory_comp_cache = res
        return res
    except Exception:
        return False

class MemoryCleanerWorker(QThread):
    """
    Background worker for memory cleaning to keep the UI responsive.
    """
    finished_signal = Signal(bool, str)

    def __init__(self, protect_pid: int = None, custom_whitelist: list = None, parent=None):
        super().__init__(parent)
        self.protect_pid = protect_pid
        self.custom_whitelist = custom_whitelist

    def run(self):
        try:
            mode_msg = ""
            if self.protect_pid and psutil.pid_exists(self.protect_pid):
                # Smart escort cleaning: skips target process and whitelisted processes
                success = MemoryService.clean_memory_smart(self.protect_pid, self.custom_whitelist)
                mode_msg = "自适应优化模式 (保障前台性能)"
            else:
                # Nuclear cleaning: empties all processes
                success = MemoryService.clean_memory_nuclear()
                mode_msg = "深度整理模式 (回收系统缓存)"
                
            self.finished_signal.emit(success, mode_msg)
        except Exception as e:
            logger.error(f"MemoryCleanerWorker exception: {str(e)}")
            self.finished_signal.emit(False, str(e))

class OptimizationWorker(QThread):
    """
    Background worker for process optimization, CPU affinity binding and background process isolation.
    """
    finished_signal = Signal(bool, str)

    def __init__(self, pid: int, primary_thread_ids: list, full_affinity: list, 
                 enable_isolation: bool, topology: list, custom_whitelist: list = None,
                 enable_parking: bool = False, enable_epp: bool = False, 
                 enable_network: bool = False, enable_child_opt: bool = True,
                 enable_wifi_tweak: bool = False, parent=None):
        super().__init__(parent)
        self.pid = pid
        self.primary_thread_ids = primary_thread_ids
        self.full_affinity = full_affinity
        self.enable_isolation = enable_isolation
        self.topology = topology
        self.custom_whitelist = custom_whitelist
        self.enable_parking = enable_parking
        self.enable_epp = enable_epp
        self.enable_network = enable_network
        self.enable_child_opt = enable_child_opt
        self.enable_wifi_tweak = enable_wifi_tweak

    def run(self):
        try:
            total_threads = psutil.cpu_count() or 0
            if total_threads <= 8:
                self.full_affinity = list(range(total_threads))
                self.enable_isolation = False
                self.primary_thread_ids = []
                logger.info(f"Low-end CPU detected (threads: {total_threads} <= 8). Bypassing game affinity restriction and disabling background isolation.")
            else:
                from core_commander.core.topology import TopologyEngine
                is_amd_dual = TopologyEngine.is_amd_dual_ccd()
                if is_amd_dual:
                    try:
                        topo = TopologyEngine.get_topology()
                        if topo:
                            half = len(topo) // 2
                            ccd0_threads = []
                            ccd1_threads = []
                            for c in topo[:half]:
                                ccd0_threads.extend(c['threads'])
                            for c in topo[half:]:
                                ccd1_threads.extend(c['threads'])
                            if ccd0_threads:
                                self.full_affinity = ccd0_threads
                                logger.info(f"AMD Dual-CCD CPU detected. Game process affinity forced to CCD0: {self.full_affinity}")
                                self.custom_isolation_pool = ccd1_threads
                    except Exception as topo_err:
                        logger.warning(f"Failed to calculate AMD Dual-CCD split: {topo_err}")

            status_msgs = []
            has_error = False
            
            # Register active game mode token file for guard self-tuning
            try:
                import tempfile
                tmp_file = os.path.join(tempfile.gettempdir(), "core_commander_game_mode.tmp")
                with open(tmp_file, "w") as f:
                    f.write(str(self.pid))
                logger.info(f"Registered Game Mode active token file for PID {self.pid}.")
            except Exception as token_err:
                logger.warning(f"Failed to write game mode token file: {token_err}")

            # Step 1. Enable Ultimate/High Performance power plan
            PowerService.set_high_performance_plan()
            
            # Step 1.5 System Hardware Tweaks
            PowerService.tune_cpu_hardware_parameters(self.enable_parking, self.enable_epp)
            PowerService.optimize_system_network_latency(self.enable_network)
            
            # Wi-Fi Gaming Ping Spikes Prevention
            if self.enable_wifi_tweak:
                try:
                    p = subprocess.Popen(["netsh", "wlan", "show", "interfaces"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
                    out, _ = p.communicate(timeout=5)
                    text = out.decode("gbk", errors="ignore")
                    interfaces = []
                    for line in text.splitlines():
                        line = line.strip()
                        match = re.search(r"^(?:Name|名称)\s*:\s*(.+)$", line, re.IGNORECASE)
                        if match:
                            interfaces.append(match.group(1).strip())
                    
                    for wlan in interfaces:
                        cmd = ["netsh", "wlan", "set", "autoconfig", "enabled=no", f"interface={wlan}"]
                        subprocess.run(cmd, capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
                        logger.info(f"Disabled auto scan for WLAN interface: {wlan}")
                except Exception as wifi_err:
                    logger.error(f"Failed to disable WLAN autoconfig: {wifi_err}")
            
            # Verify target process exists
            if not psutil.pid_exists(self.pid):
                self.finished_signal.emit(False, "目标进程已关闭")
                return
                
            proc = psutil.Process(self.pid)
            game_exe = proc.name()

            # Step 1.8 Write IFEO Registry to force high priority class at kernel level for next launch
            try:
                from core_commander.core.system_tweaks import SystemTweaksService
                SystemTweaksService.apply_ifeo_priority(game_exe, True)
            except Exception as ifeo_err:
                logger.warning(f"Failed to apply IFEO priority for next launch: {ifeo_err}")

            target_locked = False
            
            # Step 2. Elevate process priority to High Priority Class
            try:
                proc.nice(psutil.HIGH_PRIORITY_CLASS)
                logger.info(f"Elevated PID {self.pid} priority to High.")
            except psutil.AccessDenied: 
                logger.warning(f"Access Denied when setting high priority for PID {self.pid} (Game protected by Anti-Cheat).")
                target_locked = True
                status_msgs.append("优先级锁定失败")
                has_error = True
            except Exception as ex:
                logger.error(f"Error setting priority: {str(ex)}")
                status_msgs.append("优先级配置错误")
                has_error = True

            # Step 3. Set CPU affinity to primary threads first (for micro-warmup)
            if self.primary_thread_ids and not target_locked:
                try:
                    valid_ids = list(set([t for t in self.primary_thread_ids if t is not None and t >= 0]))
                    if valid_ids:
                        proc.cpu_affinity(valid_ids)
                        logger.info(f"Assigned primary CPU affinity {valid_ids} for warmup.")
                        QThread.msleep(300) # Give thread time to migrate
                except Exception as ex:
                    logger.debug(f"Failed to set primary thread warm-up affinity: {str(ex)}")

            # Step 4. Set CPU affinity to full user chosen cores
            affinity_success = False
            try:
                if not target_locked:
                    proc.cpu_affinity(self.full_affinity)
                    affinity_success = True
                    logger.info(f"Assigned CPU affinity {self.full_affinity} to PID {self.pid}")
            except psutil.AccessDenied:
                logger.warning(f"Access Denied when assigning CPU affinity to PID {self.pid} (Game protected by Anti-Cheat).")
                target_locked = True
                status_msgs.append("绑核被拦截")
                has_error = True
            except Exception as ex:
                logger.error(f"Error binding CPU affinity: {str(ex)}")
                status_msgs.append("绑核失败")
                has_error = True
                
            # Step 4.2 Pin heaviest threads to preferred logical processors using Ideal Processor and Hard Affinity
            if self.primary_thread_ids and not target_locked:
                try:
                    # Get unique preferred logical processors in order
                    valid_ids = []
                    for t in self.primary_thread_ids:
                        if t is not None and t >= 0 and t not in valid_ids:
                            valid_ids.append(t)
                            
                    if valid_ids:
                        threads = proc.threads()
                        if threads:
                            # Sort threads by cumulative CPU time descending
                            threads.sort(key=lambda t: t.user_time + t.system_time, reverse=True)
                            pinned_count = 0
                            for idx, target_core in enumerate(valid_ids):
                                if idx < len(threads):
                                    thread_info = threads[idx]
                                    h_thread = kernel32.OpenThread(THREAD_SET_INFORMATION | THREAD_QUERY_INFORMATION, False, thread_info.id)
                                    if h_thread:
                                        try:
                                            # Enforce hard thread affinity mask (strictly force thread execution to the preferred core)
                                            mask = 1 << target_core
                                            kernel32.SetThreadAffinityMask(h_thread, mask)
                                            # Set ideal processor for the thread (soft guiding)
                                            kernel32.SetThreadIdealProcessor(h_thread, target_core)
                                            logger.info(f"Successfully configured hard affinity and ideal processor for heavy thread {thread_info.id} to Preferred Core {target_core}")
                                            pinned_count += 1
                                        except Exception as ex:
                                            logger.debug(f"Failed to set thread affinity/ideal processor for thread {thread_info.id}: {str(ex)}")
                                        finally:
                                            kernel32.CloseHandle(h_thread)
                            if pinned_count > 0:
                                status_msgs.append(f"首选核心硬件关联与软调度已应用 ({pinned_count}线程)")
                except Exception as ex:
                    logger.error(f"Error applying preferred cores ideal processor routing: {str(ex)}")
                
            # Step 4.5 Optimize Child Processes recursively
            if self.enable_child_opt and not target_locked:
                try:
                    children = proc.children(recursive=True)
                    child_count = 0
                    for child in children:
                        try:
                            child.nice(psutil.HIGH_PRIORITY_CLASS)
                            child.cpu_affinity(self.full_affinity)
                            child_count += 1
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            continue
                    if child_count > 0:
                        logger.info(f"Successfully synchronized optimization for {child_count} child processes.")
                except Exception as ex:
                    logger.debug(f"Error during child process optimizations: {str(ex)}")
            
            # Step 5. Run process isolation if enabled
            iso_msg = ""
            count = 0
            if self.enable_isolation:
                pool = getattr(self, 'custom_isolation_pool', None)
                if not pool:
                    pool = ProcessIsolationService.calculate_isolation_pool(self.topology)
                if pool:
                    count = ProcessIsolationService.isolate_background_processes(
                        self.pid, pool, self.custom_whitelist
                    )
                    iso_msg = f" | 已隔离 {count} 个后台"
            
            # Report results evaluating Anti-Cheat bypasses
            if not target_locked and not has_error:
                self.finished_signal.emit(True, f"优化应用成功{iso_msg}")
            elif target_locked:
                if self.enable_isolation and count > 0:
                    self.finished_signal.emit(True, f"优化已生效 (已通过隔离后台进程间接分配物理核心){iso_msg}")
                else:
                    self.finished_signal.emit(False, "游戏受反作弊保护拒绝访问，请开启并配置「后台进程隔离」以间接隔离后台提升性能")
            else:
                if iso_msg or affinity_success:
                    self.finished_signal.emit(True, f"部分生效 ({', '.join(status_msgs)}){iso_msg}")
                else:
                    self.finished_signal.emit(False, f"优化完全被拦截: {', '.join(status_msgs)}")

        except psutil.NoSuchProcess:
            self.finished_signal.emit(False, "进程在优化过程中已结束")
        except Exception as e:
            logger.error(f"OptimizationWorker exception: {str(e)}")
            self.finished_signal.emit(False, f"未知错误: {str(e)}")


class SystemStateScannerWorker(QThread):
    """
    Background worker that scans Windows registry and service states asynchronously.
    """
    finished_signal = Signal(dict)
    
    # Class-level cache for static boot settings
    _hpet_cache = None
    _memory_comp_cache = None
    _dev_power_cache = None

    def __init__(self, gpu_vendor: str = "", target_exe: str = "", target_path: str = "", parent=None):
        super().__init__(parent)
        self.gpu_vendor = gpu_vendor
        self.target_exe = target_exe
        import os
        self.target_path = os.path.normpath(target_path) if target_path else ""
        self.settings = parent.settings if parent and hasattr(parent, 'settings') else None

    def run(self):
        try:
            import pythoncom
            pythoncom.CoInitialize()
            has_com = True
        except ImportError:
            has_com = False
            
        states = {}

        try:
            # 1. Win32 priority separation
            states['win32_prio'] = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\PriorityControl", "Win32PrioritySeparation")

            # 2. Keyboard queue size
            states['kb_val'] = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\kbdclass\Parameters", "KeyboardDataQueueSize")

            # 3. Mouse queue size
            states['m_val'] = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\mouclass\Parameters", "MouseDataQueueSize")

            # 4. Core Parking
            cpmin_val = get_effective_power_setting("54533251-82be-4824-96c1-47b60b740d00", "0cc5b647-c1df-4637-891a-dec35c318583")
            states['core_parking'] = (cpmin_val == 100)

            # 5. EPP
            epp_val = get_effective_power_setting("54533251-82be-4824-96c1-47b60b740d00", "36687f9e-e3a5-4dbf-b1dc-15eb381c6863")
            states['epp'] = (epp_val == 0)
            # 6. HPET
            hpet_disabled = False
            if SystemStateScannerWorker._hpet_cache is not None:
                hpet_disabled = SystemStateScannerWorker._hpet_cache
            else:
                try:
                    bcd_out = subprocess.check_output(  # nosec
                        ["bcdedit"], 
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        timeout=10
                    ).decode("gbk", errors="ignore")
                    import re
                    match = re.search(r'(identifier|标识符)\s+\{current\}', bcd_out, re.IGNORECASE)
                    if match:
                        start_pos = match.start()
                        remainder = bcd_out[start_pos:]
                        next_sec = re.search(r'\r?\nWindows\s+', remainder[1:])
                        sec_text = remainder[:next_sec.start() + 1] if next_sec else remainder
                        for line in sec_text.splitlines():
                            if "useplatformclock" in line.lower() and "no" in line.lower():
                                hpet_disabled = True
                                break
                except Exception:
                    pass
                if not hpet_disabled:
                    try:
                        wmi = get_wmi_client()
                        if wmi:
                            col = wmi.ExecQuery("SELECT * FROM Win32_PnPEntity WHERE Name = '高精度事件计时器' OR Name = 'High precision event timer'")
                            for obj in col:
                                if getattr(obj, 'ConfigManagerErrorCode', 0) == 22:
                                    hpet_disabled = True
                                    break
                    except Exception:
                        pass
                SystemStateScannerWorker._hpet_cache = hpet_disabled
            states['hpet'] = hpet_disabled
            # 7. Network tweaks
            net_val = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile", "NetworkThrottlingIndex")
            states['network_throttling'] = (net_val in (0xffffffff, 4294967295, -1))

            # 8. Services & Telemetry
            services_to_check = [
                "diagsvc", "DPS", "WdiServiceHost", "WdiSystemHost", 
                "DiagTrack", "MapsBroker", "autotimesvc", "DusmSvc", 
                "PcaSvc", "DsmSvc", "Sysmain"
            ]
            installed_disabled_count = 0
            installed_count = 0
            for svc in services_to_check:
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"SYSTEM\\CurrentControlSet\\Services\\{svc}", 0, winreg.KEY_READ) as key:
                        start_type, _ = winreg.QueryValueEx(key, "Start")
                        installed_count += 1
                        if start_type == 4:
                            installed_disabled_count += 1
                except Exception:
                    pass
            if installed_count > 0:
                states['services_disabled'] = (installed_disabled_count / installed_count >= 0.7)
            else:
                states['services_disabled'] = False
            
            states['wsearch_disabled'] = is_service_disabled("WSearch")

            # 9. RAM Split Host Threshold
            ram_val = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control", "SvcHostSplitThresholdInKB")
            total_ram_gb = round(psutil.virtual_memory().total / (1024**3))
            expected_ram_val = total_ram_gb * 1024 * 1024
            states['ram_opt'] = (ram_val == expected_ram_val)
            # 10. NVMe Last Access Timestamp & short name
            nvme_val1 = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem", "NtfsDisableLastAccessUpdate")
            nvme_val2 = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem", "NtfsDisable8dot3NameCreation")
            nvme_opt_ok = (nvme_val1 in (1, 0x80000001, 2147483649) or nvme_val2 == 1)
            if not nvme_opt_ok:
                la_dis, s83_dis = check_fsutil_nvme_opts()
                if la_dis or s83_dis:
                    nvme_opt_ok = True
            states['nvme_opt'] = nvme_opt_ok
            # 11. Spectre Meltdown Mitigation
            spec_val = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management", "FeatureSettingsOverride")
            states['spectre'] = (spec_val == 3)

            # 12. GPU Preemption
            preempt_val = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers\Scheduler", "EnablePreemption")
            states['preemption'] = (preempt_val == 0)

            # 13. GameDVR
            dvr_val = get_reg_val(winreg.HKEY_CURRENT_USER, r"System\GameConfigStore", "GameDVR_Enabled")
            states['gamedvr'] = (dvr_val == 0)

            # 14. Ultimate network
            val_ult = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters", "TCPWindowSize")
            states['ult_net'] = (val_ult == 0x40000)

            # 15. DWM Frame Latency
            dwm_val_hklm = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\DWM", "MaxQueuedPresentBuffers")
            dwm_val_hkcu = get_reg_val(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\DWM", "MaxQueuedPresentBuffers")
            states['dwm_tweak'] = (dwm_val_hklm == 1 or dwm_val_hkcu == 1)

            # 16. DPC latency
            dpc_val = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\kernel", "IdealDpcRate")
            states['dpc'] = (dpc_val == 1)

            # 17. DWM SuperWet
            wet_val1 = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\DWM", "SuperWetEnabled")
            wet_val2 = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\DWM", "SuperWet")
            states['dwm_wet'] = (wet_val1 == 1 or wet_val2 == 1)
            # 18. GlobalTimerResolutionRequests
            timer_res = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\kernel", "GlobalTimerResolutionRequests")
            timer_res_ok = (timer_res == 1)
            if not timer_res_ok:
                cur_res = get_current_timer_resolution_ms()
                if cur_res is not None and cur_res <= 1.05:
                    timer_res_ok = True
            states['timer_res'] = timer_res_ok
            # 19. PCI Power Express ASPM
            pcip_val = get_effective_power_setting("501a4d13-42af-4429-9fd1-a8218c268e20", "ee12f906-d277-404b-b6da-e5fa1a576df5")
            pci_pwr = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Power", "PciPowerManagement")
            states['pcipower'] = (pcip_val == 0 or pci_pwr == 0)

            # 20. DirectX Flip Discard
            dx_val = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\DirectX", "DXGI_FORCE_FLIP_DISCARD")
            states['directx'] = (dx_val == 1)

            # 21. DNS ServiceProvider Priorities
            dns_val = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\Tcpip\ServiceProvider", "DnsPriority")
            hosts_val = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\Tcpip\ServiceProvider", "HostsPriority")
            states['dns'] = (dns_val == 6 or hosts_val == 5)

            # 22. Feeds and softlanding tips
            feeds_val = get_reg_val(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Feeds", "ShellFeedsTaskbarEnabled")
            states['feeds'] = (feeds_val == 2 or feeds_val is None or feeds_val not in (0, 1))
            tips_val = get_reg_val(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager", "SoftLandingEnabled")
            states['tips'] = (tips_val == 0)

            # 23. Desktop Heap
            heap_val = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\SubSystems", "Windows")
            states['desktop_heap'] = (heap_val is not None and "4096,8192,4096" in heap_val)

            # 24. UAC EnableLUA
            uac_val = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System", "EnableLUA")
            states['uac'] = (uac_val == 0)

            # 25. MapsBroker download Maps
            states['download_maps'] = is_service_disabled("MapsBroker")

            # 26. Background Access App Execution
            bg_val = get_reg_val(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\BackgroundAccessApplications", "GlobalUserDisabled")
            states['bg_apps'] = (bg_val == 1)

            # 26.5 Map updates policy
            maps_val = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Microsoft\Windows\Maps", "AutoDownloadAndUpdateMapData")
            states['map_updates'] = (maps_val == 0)

            # 27. AutoShare
            share_val1 = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters", "AutoShareServer")
            share_val2 = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters", "AutoShareWks")
            states['autoshare'] = (share_val1 == 0 or share_val2 == 0)

            # 28. AutoRun Explorer policies
            autorun_val = get_reg_val(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer", "NoDriveTypeAutoRun")
            states['autorun'] = (autorun_val == 0xff)

            # 29. Mouse flat speed & curves
            mouse_speed = get_reg_val(winreg.HKEY_CURRENT_USER, r"Control Panel\Mouse", "MouseSpeed")
            states['mouse_lat'] = (mouse_speed == "0")

            # 30. ConfigFileAllocSize
            alloc_val = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem", "ConfigFileAllocSize")
            states['config_alloc'] = (alloc_val == 0x1f4)

            # 31. GPU Firmware & PState
            firmware_found = False
            pstate_found = False
            try:
                path_class = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_class, 0, winreg.KEY_READ) as root_key:
                    i = 0
                    while True:
                        try:
                            sub_name = winreg.EnumKey(root_key, i)
                            sub_path = f"{path_class}\\{sub_name}"
                            fw = get_reg_val(winreg.HKEY_LOCAL_MACHINE, sub_path, "EnableGpuFirmware")
                            if fw == 1:
                                firmware_found = True
                            ps = get_reg_val(winreg.HKEY_LOCAL_MACHINE, sub_path, "DisableDynamicPstate")
                            if ps == 1:
                                pstate_found = True
                            i += 1
                        except OSError:
                            break
            except Exception:  # nosec
                pass
            states['gpu_firmware'] = firmware_found
            states['gpu_pstate'] = pstate_found

            # 32. Game priority
            target_to_check = self.target_exe if self.target_exe else "NarakaBladepoint.exe"
            prio_game_val = get_reg_val(winreg.HKEY_LOCAL_MACHINE, f"SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Image File Execution Options\\{target_to_check}\\PerfOptions", "CpuPriorityClass")
            states['naraka'] = (prio_game_val == 3)

            # 33. Memory compression status
            states['memory_comp'] = is_memory_compression_disabled(self.settings)

            # 34. Visual effects
            visual_fx = get_reg_val(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects", "VisualFXSetting")
            states['visual_effects'] = (visual_fx == 3)

            # 35. Transparency
            transparency = get_reg_val(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "EnableTransparency")
            states['transparency'] = (transparency == 0)

            # 36. Copilot
            copilot_hkcu = get_reg_val(winreg.HKEY_CURRENT_USER, r"Software\Policies\Microsoft\Windows\WindowsCopilot", "TurnOffWindowsCopilot")
            copilot_hklm = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Microsoft\Windows\WindowsCopilot", "TurnOffWindowsCopilot")
            copilot_button = get_reg_val(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced", "ShowCopilotButton")
            states['copilot'] = (copilot_hkcu == 1 or copilot_hklm == 1 or copilot_button == 0)

            # 37. Security Notifications
            sec_notif = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Microsoft\Windows Defender Security Center\Notifications", "DisableNotifications")
            states['sec_notif'] = (sec_notif == 1)

            # 38. Defender
            defender_disabled = True
            tamper_protected = False
            try:
                import win32com.client
                import pythoncom
                pythoncom.CoInitialize()
                wmi = win32com.client.GetObject("winmgmts:\\\\.\\root\\Microsoft\\Windows\\Defender")
                col = wmi.ExecQuery("SELECT * FROM MSFT_MpComputerStatus")
                for obj in col:
                    if getattr(obj, 'RealTimeProtectionEnabled', False) or getattr(obj, 'AMServiceEnabled', False) or getattr(obj, 'AntivirusEnabled', False):
                        defender_disabled = False
                    if getattr(obj, 'IsTamperProtected', False):
                        tamper_protected = True
            except Exception:
                if is_service_running("WinDefend") or is_process_running("MsMpEng.exe"):
                    defender_disabled = False
            states['defender'] = defender_disabled
            states['tamper_protection'] = tamper_protected

            # 39. SmartScreen
            smartscreen_off = False
            smartscreen_val = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer", "SmartScreenEnabled")
            smartscreen_policy = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Microsoft\Windows\System", "EnableSmartScreen")
            if smartscreen_val == "Off" or smartscreen_policy == 0:
                smartscreen_off = True
            if not smartscreen_off:
                try:
                    import win32com.client
                    import pythoncom
                    pythoncom.CoInitialize()
                    wmi_def = win32com.client.GetObject("winmgmts:\\\\.\\root\\Microsoft\\Windows\\Defender")
                    col_pref = wmi_def.ExecQuery("SELECT EnableSmartScreen FROM MSFT_MpPreference")
                    for obj in col_pref:
                        if getattr(obj, 'EnableSmartScreen', True) is False:
                            smartscreen_off = True
                            break
                except Exception:
                    pass
            if not smartscreen_off and self.settings is not None:
                smartscreen_off = self.settings.disable_smartscreen
            states['smartscreen'] = smartscreen_off

            # 40. Firewall
            firewall_disabled = True
            if is_service_running("MpsSvc"):
                for p in ["StandardProfile", "PublicProfile", "DomainProfile"]:
                    val = get_reg_val(winreg.HKEY_LOCAL_MACHINE, f"SYSTEM\\CurrentControlSet\\Services\\SharedAccess\\Parameters\\FirewallPolicy\\{p}", "EnableFirewall")
                    if val != 0:
                        firewall_disabled = False
                        break
            states['firewall'] = firewall_disabled

            # 41. Driver Priority
            gpu_energy_val = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\GpuEnergyDrv", "Start")
            states['driver_prio'] = (gpu_energy_val == 4)
            # 42. Hyper-V / VBS
            start_opts = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control", "SystemStartOptions")
            hyperv_disabled = (isinstance(start_opts, str) and "HYPERVISORLAUNCHTYPE=OFF" in start_opts.upper())
            if not hyperv_disabled:
                # Optimized registry check for Virtualization-Based Security (VBS)
                vbs_enabled_val = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\DeviceGuard", "EnableVirtualizationBasedSecurity")
                if vbs_enabled_val == 0:
                    hyperv_disabled = True
                else:
                    try:
                        wmi_dg = get_wmi_client("root\\Microsoft\\Windows\\DeviceGuard")
                        if wmi_dg:
                            col = wmi_dg.ExecQuery("SELECT * FROM Win32_DeviceGuard")
                            for obj in col:
                                vbs_status = getattr(obj, 'VirtualizationBasedSecurityStatus', 0)
                                if vbs_status == 0:
                                    hyperv_disabled = True
                                    break
                    except Exception:
                        pass
            states['hyperv'] = hyperv_disabled
            # 43. GPU Optimization
            gpu_opt_active = False
            if self.gpu_vendor == "NVIDIA":
                opt_pref = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\NVIDIA Corporation\NvControlPanel2\Client", "OptInOrOutPreference")
                if opt_pref == 0 or is_service_disabled("NvTelemetryContainer"):
                    gpu_opt_active = True
            elif self.gpu_vendor == "AMD":
                chill = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"System\CurrentControlSet\Services\amdwddmg", "ChillEnabled")
                if chill == 0 or is_service_disabled("AMD Crash Defender Service"):
                    gpu_opt_active = True
            states['gpu_opt'] = gpu_opt_active

            # 44. GPU IRQ Priority
            gpu_irq_active = False
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\PriorityControl", 0, winreg.KEY_READ) as key:
                    i = 0
                    while True:
                        try:
                            name, val, val_type = winreg.EnumValue(key, i)
                            if name.startswith("IRQ") and name.endswith("Priority") and val == 1:
                                gpu_irq_active = True
                                break
                            i += 1
                        except OSError:
                            break
            except Exception:  # nosec
                pass
            states['gpu_irq'] = gpu_irq_active

            # 45. GPU HAGS
            hags_val = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers", "HwSchMode")
            states['hags'] = (hags_val == 1)

            # 46. USB Low Latency
            usb_lat_val = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\USBXHCI\Parameters", "ForceLowLatency")
            states['usb_lat'] = (usb_lat_val == 1)

            # 47. USB IMOD
            usb_imod_val = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\USBXHCI\Parameters", "InterruptModeration")
            states['usb_imod'] = (usb_imod_val == 0)

            dev_power_active = False
            if SystemStateScannerWorker._dev_power_cache is not None:
                dev_power_active = SystemStateScannerWorker._dev_power_cache
            else:
                queried_dev_power = False
                if HAS_WIN32:
                    try:
                        import pythoncom
                        import win32com.client
                        pythoncom.CoInitialize()
                        wmi_wmi = None
                        devices = None
                        try:
                            wmi_wmi = win32com.client.GetObject("winmgmts:{impersonationLevel=impersonate}!\\\\.\\root\\wmi")
                            devices = wmi_wmi.ExecQuery("SELECT Enable FROM MSPower_DeviceEnable")
                            enables = [d.Enable for d in devices]
                            if enables:
                                dev_power_active = (enables.count(False) / len(enables) >= 0.8)
                                queried_dev_power = True
                        finally:
                            devices = None
                            wmi_wmi = None
                            pythoncom.CoUninitialize()
                    except Exception:  # nosec
                        pass
                
                if not queried_dev_power:
                    if self.settings is not None:
                        dev_power_active = self.settings.enable_device_power_tweak
                    else:
                        try:
                            cmd = "$d = Get-CimInstance -Namespace root/wmi -ClassName MSPower_DeviceEnable -ErrorAction SilentlyContinue; if ($d) { $d | Select-Object -ExpandProperty enable } else { Get-WmiObject -Namespace root/wmi -Class MSPower_DeviceEnable -ErrorAction SilentlyContinue | Select-Object -ExpandProperty enable }; if ($d) { $f = ($d | Where-Object { $_ -eq $false } | Measure-Object).Count; $total = ($d | Measure-Object).Count; if ($total -gt 0) { [math]::Round($f / $total, 2) } else { 0 } } else { 0 }"
                            out = subprocess.check_output(  # nosec
                                ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                                creationflags=subprocess.CREATE_NO_WINDOW,
                                timeout=10
                            ).decode("gbk", errors="ignore").strip()
                            try:
                                ratio = float(out)
                                dev_power_active = (ratio >= 0.8)
                            except ValueError:
                                dev_power_active = False
                        except Exception:  # nosec
                            pass
                SystemStateScannerWorker._dev_power_cache = dev_power_active
            states['dev_power'] = dev_power_active

            kb_delay = get_reg_val(winreg.HKEY_CURRENT_USER, r"Control Panel\Keyboard", "KeyboardDelay")
            kb_speed = get_reg_val(winreg.HKEY_CURRENT_USER, r"Control Panel\Keyboard", "KeyboardSpeed")
            kb_opt = False
            if kb_delay == "0" and kb_speed == "48":
                kb_opt = True
            else:
                active_params = get_active_keyboard_params()
                if active_params is not None:
                    d, s = active_params
                    if d == 0 and s >= 31:
                        kb_opt = True
            states['keyboard_repeat'] = kb_opt
            # 50. Custom Power Plan
            active_uuid = get_active_power_scheme_uuid()
            states['custom_power_plan'] = (active_uuid == "11111111-1111-1111-1111-111111111111")
            # --- 新增的 15 项状态扫描检测 ---
            # 1. 小部件禁用状态
            widgets_val = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Microsoft\Dsh", "AllowNewsAndInterests")
            states['widgets'] = (widgets_val == 0)

            # 2. 粘滞键禁用状态
            sticky_val = get_reg_val(winreg.HKEY_CURRENT_USER, r"Control Panel\Accessibility\StickyKeys", "Flags")
            states['sticky_keys'] = (sticky_val == "506")

            # 3. 消除启动延迟状态
            delay_val = get_reg_val(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\Serialize", "StartupDelayInMSec")
            idle_val = get_reg_val(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\Serialize", "WaitForIdleState")
            states['startup_delay'] = (delay_val == 0 and idle_val == 0)

            # 4. 右键菜单延迟消除状态
            menu_delay = get_reg_val(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop", "MenuShowDelay")
            states['menu_delay'] = (menu_delay == "0")

            # 5. 设置同步与 sync 状态
            sync_val = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Microsoft\Windows\SettingSync", "DisableSettingSync")
            states['settings_sync'] = (sync_val == 2 and is_service_disabled("CscService"))

            # 6. 动态照明禁用状态
            lighting_val = get_reg_val(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Lighting", "AmbientLightingEnabled")
            states['dynamic_lighting'] = (lighting_val == 0)

            # 7. 显卡 MSI 模式检测
            msi_status = 0
            try:
                gpu_ids = get_pci_device_ids("Display")
                if gpu_ids:
                    msi_supported_count = 0
                    high_priority_count = 0
                    for gid in gpu_ids:
                        msi_prop_path = f"SYSTEM\\CurrentControlSet\\Enum\\{gid}\\Device Parameters\\Interrupt Management\\MessageSignaledInterruptProperties"
                        msi_sup = get_reg_val(winreg.HKEY_LOCAL_MACHINE, msi_prop_path, "MSISupported")
                        msi_priority = get_reg_val(winreg.HKEY_LOCAL_MACHINE, msi_prop_path, "Priority")
                        if msi_sup == 1:
                            msi_supported_count += 1
                            if msi_priority == 3:
                                high_priority_count += 1
                    
                    if msi_supported_count > 0:
                        if high_priority_count == msi_supported_count:
                            msi_status = 2
                        else:
                            msi_status = 1
                    else:
                        msi_status = 0
            except Exception:  # nosec
                pass
            states['gpu_msi'] = msi_status

            # 7a. 网卡 MSI 模式检测
            network_msi_status = 0
            try:
                net_ids = get_pci_device_ids("Net")
                if net_ids:
                    msi_supported_count = 0
                    high_priority_count = 0
                    for nid in net_ids:
                        msi_prop_path = f"SYSTEM\\CurrentControlSet\\Enum\\{nid}\\Device Parameters\\Interrupt Management\\MessageSignaledInterruptProperties"
                        msi_sup = get_reg_val(winreg.HKEY_LOCAL_MACHINE, msi_prop_path, "MSISupported")
                        msi_priority = get_reg_val(winreg.HKEY_LOCAL_MACHINE, msi_prop_path, "Priority")
                        if msi_sup == 1:
                            msi_supported_count += 1
                            if msi_priority == 3:
                                high_priority_count += 1
                    if msi_supported_count > 0:
                        if high_priority_count == msi_supported_count:
                            network_msi_status = 2
                        else:
                            network_msi_status = 1
            except Exception:
                pass
            states['network_msi'] = network_msi_status

            # 7b. 存储 MSI 模式检测
            storage_msi_status = 0
            try:
                scsi_ids = get_pci_device_ids("SCSIAdapter")
                if scsi_ids:
                    msi_supported_count = 0
                    high_priority_count = 0
                    for sid in scsi_ids:
                        msi_prop_path = f"SYSTEM\\CurrentControlSet\\Enum\\{sid}\\Device Parameters\\Interrupt Management\\MessageSignaledInterruptProperties"
                        msi_sup = get_reg_val(winreg.HKEY_LOCAL_MACHINE, msi_prop_path, "MSISupported")
                        msi_priority = get_reg_val(winreg.HKEY_LOCAL_MACHINE, msi_prop_path, "Priority")
                        if msi_sup == 1:
                            msi_supported_count += 1
                            if msi_priority == 3:
                                high_priority_count += 1
                    if msi_supported_count > 0:
                        if high_priority_count == msi_supported_count:
                            storage_msi_status = 2
                        else:
                            storage_msi_status = 1
            except Exception:
                pass
            states['storage_msi'] = storage_msi_status

            # 7c. DWM 窗口化呈现延迟绕过优化状态
            dwm_pres_val = get_reg_val(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\DirectX", "DisableDXMaximizedWindowedMode")
            states['dwm_presentation'] = (dwm_pres_val == 1)

            # 8. Xbox Live 存档服务禁用状态
            states['xbox_save'] = is_service_disabled("XblGameSave")

            # 9. Microsoft Store 自动下载禁用状态
            store_val = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Microsoft\WindowsStore", "AutoDownload")
            states['store_auto_update'] = (store_val == 2)

            # 10. 驱动程序黑名单禁用状态
            blocklist_val = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\CI\Config", "VulnerableDriverBlocklistEnable")
            blocklist_disabled = (blocklist_val == 0)
            if not blocklist_disabled:
                if self.settings is not None:
                    blocklist_disabled = self.settings.enable_vulnerable_driver_blocklist_tweak
                else:
                    try:
                        p = subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", "Get-MpPreference | Select-Object -ExpandProperty EnableVulnerableDriverBlocklist"], stdout=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
                        out, _ = p.communicate(timeout=5)
                        out_str = out.decode("utf-8", errors="ignore").lower().strip()
                        if "false" in out_str:
                            blocklist_disabled = True
                    except Exception:
                        pass
            states['vulnerable_driver_blocklist'] = blocklist_disabled

            # 11. 磁盘自动加密阻止状态
            prevent_val = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\BitLocker", "PreventDeviceEncryption")
            states['prevent_device_encryption'] = (prevent_val == 1)

            # 12. Windows 聚焦禁用状态
            spot_val = get_reg_val(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager", "SubscribedContent-338387Enabled")
            rot_val = get_reg_val(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager", "RotatingLockScreenOverlayEnabled")
            states['spotlight'] = (spot_val == 0 and rot_val == 0)

            # 13. 进程锁定 (无静态系统键，默认读取 states 中的 settings 绑定)
            states['hard_working_set'] = None
            # 14. 网卡硬件中断合并限制禁用状态
            imod_active = True
            try:
                path_class = r"SYSTEM\CurrentControlSet\Control\Class\{4D36E972-E325-11CE-BFC1-08002BE10318}"
                active_subs = get_active_physical_adapter_subkeys()
                if active_subs is not None:
                    if len(active_subs) == 0:
                        imod_active = False
                    else:
                        for sub in active_subs:
                            sub_path = f"{path_class}\\{sub}"
                            try:
                                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub_path, 0, winreg.KEY_READ) as sub_key:
                                    val, _ = winreg.QueryValueEx(sub_key, "*InterruptModeration")
                                    if val != "0":
                                        imod_active = False
                                        break
                            except FileNotFoundError:
                                pass
                else:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_class, 0, winreg.KEY_READ) as root_key:
                        i = 0
                        has_any_net_card = False
                        while True:
                            try:
                                sub = winreg.EnumKey(root_key, i)
                                sub_path = f"{path_class}\\{sub}"
                                try:
                                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub_path, 0, winreg.KEY_READ) as sub_key:
                                        val, _ = winreg.QueryValueEx(sub_key, "*InterruptModeration")
                                        has_any_net_card = True
                                        if val != "0":
                                            imod_active = False
                                            break
                                except FileNotFoundError:
                                    pass
                                i += 1
                            except OSError:
                                break
                        if not has_any_net_card:
                            imod_active = False
            except Exception:
                imod_active = False
            states['net_imod'] = imod_active
            # 15. 精简网卡冗余网络组件禁用状态 (仅检测物理网卡)
            def is_net_bindings_disabled_reg(settings=None):
                try:
                    active_subs = get_active_physical_adapter_subkeys()
                    if active_subs is not None:
                        guids = []
                        path_class = r"SYSTEM\CurrentControlSet\Control\Class\{4D36E972-E325-11CE-BFC1-08002BE10318}"
                        for sub in active_subs:
                            sub_path = f"{path_class}\\{sub}"
                            try:
                                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub_path, 0, winreg.KEY_READ) as sub_key:
                                    guid, _ = winreg.QueryValueEx(sub_key, "NetCfgInstanceId")
                                    if guid:
                                        guids.append(str(guid).lower())
                            except Exception:
                                pass
                        if guids:
                            bind_path = r"SYSTEM\CurrentControlSet\Services\LanmanWorkstation\Linkage"
                            try:
                                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, bind_path, 0, winreg.KEY_READ) as bind_key:
                                    bind_list, _ = winreg.QueryValueEx(bind_key, "Bind")
                                    # bind_list is usually a list of strings (REG_MULTI_SZ)
                                    # e.g., \Device\NdisWanIp, \Device\{GUID}
                                    if bind_list:
                                        for bound in bind_list:
                                            bound_lower = str(bound).lower()
                                            for guid in guids:
                                                if guid in bound_lower:
                                                    # At least one physical adapter is bound (enabled)
                                                    return False
                                        return True  # None of the physical adapters are bound (disabled)
                            except Exception:
                                pass
                except Exception:
                    pass
                if settings is not None:
                    return settings.enable_net_bindings_tweak
                return False

            states['net_bindings'] = is_net_bindings_disabled_reg(self.settings)

            # 16. 全局禁用全屏优化 (FSE) 状态
            gfse_val = get_reg_val(winreg.HKEY_CURRENT_USER, r"System\GameConfigStore", "GameDVR_FSEBehaviorMode")
            states['global_fse'] = (gfse_val == 2)

            # 17. 当前目标游戏禁用全屏优化 (FSE) 状态
            game_fse_active = False
            if self.target_path:
                path_layers = r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"
                layer_val = get_reg_val(winreg.HKEY_CURRENT_USER, path_layers, self.target_path)
                if layer_val and "~ DISABLEDXMAXIMIZEDWINDOWEDMODE" in layer_val:
                    game_fse_active = True
            states['game_fse'] = game_fse_active

            # 18. 当前目标游戏强制高性能独显运行状态
            game_gpu_pref_active = False
            if self.target_path:
                path_gpu_pref = r"Software\Microsoft\DirectX\UserGpuPreferences"
                pref_val = get_reg_val(winreg.HKEY_CURRENT_USER, path_gpu_pref, self.target_path)
                if pref_val and "GpuPreference=2;" in pref_val:
                    game_gpu_pref_active = True
            states['game_gpu_preference'] = game_gpu_pref_active

            # 19. 显卡与网卡 IRQ 亲和性分配状态检测
            irq_affinity_active = False
            try:
                check_devices = []
                gpu_ids = get_pci_device_ids("Display")
                if gpu_ids:
                    check_devices.extend(gpu_ids)
                net_ids = get_pci_device_ids("Net")
                if net_ids:
                    check_devices.extend(net_ids)

                if check_devices:
                    all_applied = True
                    for dev_id in check_devices:
                        sub_path = f"SYSTEM\\CurrentControlSet\\Enum\\{dev_id}\\Device Parameters\\Interrupt Management\\Affinity Policy"
                        dev_policy = get_reg_val(winreg.HKEY_LOCAL_MACHINE, sub_path, "DevicePolicy")
                        assign_set = get_reg_val(winreg.HKEY_LOCAL_MACHINE, sub_path, "AssignmentSet")
                        if dev_policy != 4 or not assign_set or not isinstance(assign_set, bytes) or len(assign_set) < 1:
                            all_applied = False
                            break
                    if all_applied:
                        irq_affinity_active = True
            except Exception:
                pass
            states['irq_affinity'] = irq_affinity_active

            # 20. 全局电源节流禁用状态
            pt_val = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Power\PowerThrottling", "PowerThrottlingOff")
            states['power_throttling'] = (pt_val == 1)

            # 21. TCP BBR 拥塞控制提供程序状态
            tcp_bbr_active = False
            # Check registry under HKLM\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Templates\Internet\CongestionProvider
            congestion_provider = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Templates\Internet", "CongestionProvider")
            if congestion_provider and str(congestion_provider).lower() == "bbr":
                tcp_bbr_active = True
            elif self.settings is not None:
                tcp_bbr_active = self.settings.enable_tcp_bbr_tweak
            # 22. 物理网卡以太网节能禁用状态
            eee_disabled = True
            try:
                path_class = r"SYSTEM\CurrentControlSet\Control\Class\{4D36E972-E325-11CE-BFC1-08002BE10318}"
                active_subs = get_active_physical_adapter_subkeys()
                keys_to_tweak = ["*EEE", "EEELink", "*EEELink", "*GigaLite", "*PowerSavingMode", "GreenEthernet", "GreenFeedback"]
                
                if active_subs is not None:
                    if len(active_subs) == 0:
                        eee_disabled = False
                    else:
                        for sub in active_subs:
                            sub_path = f"{path_class}\\{sub}"
                            is_physical = False
                            try:
                                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{sub_path}\\Ndi\\Interfaces", 0, winreg.KEY_READ) as intf_key:
                                    lower_range, _ = winreg.QueryValueEx(intf_key, "LowerRange")
                                    if "ethernet" in str(lower_range).lower():
                                        is_physical = True
                            except FileNotFoundError:
                                pass
                                
                            if is_physical:
                                for key_name in keys_to_tweak:
                                    try:
                                        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub_path, 0, winreg.KEY_READ) as sub_key:
                                            val, val_type = winreg.QueryValueEx(sub_key, key_name)
                                            if val_type == winreg.REG_DWORD:
                                                if val != 0:
                                                    eee_disabled = False
                                                    break
                                            else:
                                                if val != "0":
                                                    eee_disabled = False
                                                    break
                                    except FileNotFoundError:
                                        pass
                                    if not eee_disabled:
                                        break
                else:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_class, 0, winreg.KEY_READ) as root_key:
                        i = 0
                        has_any_physical = False
                        while True:
                            try:
                                sub = winreg.EnumKey(root_key, i)
                                if sub.isdigit():
                                    sub_path = f"{path_class}\\{sub}"
                                    is_physical = False
                                    try:
                                        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{sub_path}\\Ndi\\Interfaces", 0, winreg.KEY_READ) as intf_key:
                                            lower_range, _ = winreg.QueryValueEx(intf_key, "LowerRange")
                                            if "ethernet" in str(lower_range).lower():
                                                is_physical = True
                                    except FileNotFoundError:
                                        pass
                                        
                                    if is_physical:
                                        has_any_physical = True
                                        for key_name in keys_to_tweak:
                                            try:
                                                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub_path, 0, winreg.KEY_READ) as sub_key:
                                                    val, val_type = winreg.QueryValueEx(sub_key, key_name)
                                                    if val_type == winreg.REG_DWORD:
                                                        if val != 0:
                                                            eee_disabled = False
                                                            break
                                                    else:
                                                        if val != "0":
                                                            eee_disabled = False
                                                            break
                                            except FileNotFoundError:
                                                pass
                                i += 1
                            except OSError:
                                break
                        if not has_any_physical:
                            eee_disabled = False
            except Exception:
                eee_disabled = False
            states['eee_tweak'] = eee_disabled


            # 22. 开始菜单 Bing 网络搜索结果禁用状态
            web_search_disabled = False
            try:
                p_val = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Microsoft\Windows\Windows Search", "DisableWebSearch")
                c_val = get_reg_val(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Search", "BingSearchEnabled")
                if p_val == 1 and c_val == 0:
                    web_search_disabled = True
            except Exception:
                pass
            states['web_search'] = web_search_disabled

            # 23. 系统遥测与客户体验改善计划任务禁用状态 (支持 Win 10/11 变体通配符)
            telemetry_tasks_disabled = True
            try:
                # Fast check using the registry under HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Schedule\TaskCache\Tree\Microsoft\Windows
                # If we cannot query or want a definitive status, check if telemetry tweak is enabled in settings, or query if task actions/states are stored
                # We can check specific keys for existence and whether they are disabled
                tasks_to_check = [
                    r"Microsoft\Windows\Application Experience\Microsoft Compatibility Appraiser",
                    r"Microsoft\Windows\Application Experience\ProgramDataUpdater",
                    r"Microsoft\Windows\Application Experience\StartupAppTask",
                    r"Microsoft\Windows\Customer Experience Improvement Program\Consolidator",
                    r"Microsoft\Windows\Customer Experience Improvement Program\UsbCeip"
                ]
                tree_path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Schedule\TaskCache\Tree"
                any_enabled = False
                for task in tasks_to_check:
                    try:
                        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{tree_path}\\{task}", 0, winreg.KEY_READ) as task_key:
                            # If task exists in Tree, check its ID under Tasks if we want state,
                            # but simple registry cache tree holds the Tasks. We can check if it is registered.
                            # Usually, disabled tasks have their 'Actions' or cache state modified.
                            # If registry check is ambiguous, we fallback to settings.enable_telemetry_tasks_tweak or assume disabled if the tweak is applied.
                            pass
                    except FileNotFoundError:
                        # Task doesn't exist, which means it's effectively disabled
                        continue
                if self.settings is not None:
                    # Fallback to check if the tweak was applied
                    telemetry_tasks_disabled = self.settings.enable_telemetry_tasks_tweak
                else:
                    # Fallback to powershell command only if settings is None
                    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", "Get-ScheduledTask -TaskName 'Microsoft Compatibility Appraiser*', 'ProgramDataUpdater', 'StartupAppTask', 'Consolidator', 'UsbCeip' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty State"]
                    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
                    stdout, _ = p.communicate(timeout=3)
                    out_str = stdout.decode("utf-8", errors="ignore").lower()
                    if any(s in out_str for s in ["ready", "running", "enabled"]):
                        telemetry_tasks_disabled = False
            except Exception as e:
                logger.error(f"Error checking telemetry tasks state: {e}")
                telemetry_tasks_disabled = False
            states['telemetry_tasks'] = telemetry_tasks_disabled

            # 23.5 极限精简 (硬核电竞级) 状态
            extreme_debloat_active = True
            try:
                if not is_service_disabled("Spooler") or not is_service_disabled("XblAuthManager") or not is_service_disabled("SysMain"):
                    extreme_debloat_active = False
            except Exception:
                extreme_debloat_active = False
            states['extreme_debloat'] = extreme_debloat_active

            # 24. Prefetcher 内存预先加载禁用状态
            prefetcher_disabled = False
            try:
                ep_val = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management\PrefetchParameters", "EnablePrefetcher")
                es_val = get_reg_val(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management\PrefetchParameters", "EnableSuperfetch")
                if ep_val == 0 and (es_val == 0 or es_val is None):
                    prefetcher_disabled = True
            except Exception:
                pass
            states['prefetcher'] = prefetcher_disabled




        except Exception as e:
            logger.error(f"Error in SystemStateScannerWorker background thread: {str(e)}")
        finally:
            if has_com:
                try:
                    pythoncom.CoUninitialize()
                except Exception:  # nosec
                    pass
            self.finished_signal.emit(states)
