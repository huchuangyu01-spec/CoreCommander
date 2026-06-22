# -*- coding: utf-8 -*-
import os
import threading
import psutil
import ctypes
from ctypes import wintypes
from core_commander.utils.logger import logger

# --- Win32 Structs & API declarations for Toolhelp32 ---
TH32CS_SNAPPROCESS = 0x00000002

class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260)
    ]

is_windows = os.name == 'nt'
kernel32 = None

if is_windows:
    try:
        kernel32 = ctypes.windll.kernel32
        
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE

        kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.Process32FirstW.restype = wintypes.BOOL

        kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.Process32NextW.restype = wintypes.BOOL

        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE

        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME)
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
    except Exception as e:
        logger.debug(f"Failed to load ctypes Win32 functions for process isolation: {e}")
        kernel32 = None


class ProcessIsolationService:
    _lock = threading.Lock()

    # Track isolated processes (mapping PID to creation time) for instant restoration
    _isolated_pids = {}

    # Track failed isolation processes (mapping PID to creation time) to skip repeated attempts
    _failed_pids = {}

    # Track whitelisted processes (mapping PID to creation time) to skip repeated scans/hashes
    _whitelisted_pids = {}

    @staticmethod
    def djb2_hash(s: str) -> int:
        h = 5381
        for char in s:
            h = ((h << 5) + h) + ord(char)
        return h & 0xFFFFFFFF

    # Precomputed djb2 hashes of default whitelist process names
    DEFAULT_WHITELIST = {
        35600660, 86132658, 128876606, 288669753, 365516574, 379612693, 
        427041707, 461529503, 485418122, 568027202, 588895788, 719706648, 
        752611711, 818339548, 818852018, 853844349, 904581667, 907792533, 
        1027968847, 1062873696, 1160511378, 1195229769, 1214617913, 1260542381, 
        1281597551, 1360362393, 1499576374, 1505068174, 1735002039, 1856120270, 
        1963411806, 1987405813, 2004686459, 2018443899, 2075862595, 2133009189, 
        2174540474, 2176581500, 2192930279, 2507426010, 2534556855, 2610827087, 
        2628093711, 2635242096, 2692158082, 2731118066, 2741535998, 2788313496, 
        2808953918, 2810987148, 2812672214, 2989164893, 3016801198, 3100740745, 
        3152076308, 3156181139, 3220883345, 3237478534, 3246068390, 3471518461, 
        3730606492, 3737242070, 3776947820, 3807028394, 3822939640, 3871617901, 
        4159659741, 4218213891
    }

    @staticmethod
    def get_whitelist(custom_whitelist=None) -> set:
        """
        Merges custom user whitelist with default system processes (all stored as djb2 hashes).
        """
        wl = set(ProcessIsolationService.DEFAULT_WHITELIST)
        if custom_whitelist:
            for item in custom_whitelist:
                if isinstance(item, str) and item:
                    cleaned = item.strip().lower()
                    wl.add(ProcessIsolationService.djb2_hash(cleaned))
        return wl

    @staticmethod
    def calculate_isolation_pool(topology: list) -> list:
        """
        Calculates which CPU core threads should receive isolated background tasks.
        Prefers E-Cores. If none are found, isolates to a percentage of the last logical cores.
        """
        total_threads = psutil.cpu_count() or 0
        if total_threads <= 8:
            logger.info(f"Low-end CPU detected (threads: {total_threads} <= 8). Disabling background isolation.")
            return []

        from core_commander.core.topology import TopologyEngine
        if TopologyEngine.is_amd_dual_ccd():
            # For AMD dual CCD processors, isolate background processes to CCD1 (the second half of logical threads)
            half = total_threads // 2
            isolation_affinity = list(range(half, total_threads))
            logger.info(f"AMD Dual-CCD CPU detected. Calculated isolation pool using CCD1: Threads {isolation_affinity}")
        else:
            isolation_affinity = []
            e_cores = [c for c in topology if c['type'] == 'E-Core']
            
            if e_cores:
                for c in e_cores: 
                    isolation_affinity.extend(c['threads'])
                logger.info(f"Calculated isolation pool using E-Cores: Threads {isolation_affinity}")
            else:
                if total_threads >= 8:
                    count = max(2, int(total_threads * 0.25))
                    isolation_affinity = list(range(total_threads - count, total_threads))
                elif total_threads >= 4:
                    isolation_affinity = list(range(total_threads - 1, total_threads))
                else:
                    # 2 or fewer logical processors: cannot effectively isolate background
                    isolation_affinity = list(range(total_threads))
                logger.info(f"Calculated isolation pool on homogeneous CPU: Threads {isolation_affinity}")
            
        # Ensure CPU indices are within [0, 63] to prevent psutil Overflow/ValueError on Windows Multi-Group (>64 logical cores) systems
        isolation_affinity = [cpu for cpu in isolation_affinity if 0 <= cpu < 64]
        if not isolation_affinity:
            # Fallback to last core of Group 0
            isolation_affinity = [min(63, psutil.cpu_count() - 1)]
            
        return isolation_affinity

    @staticmethod
    def _get_process_create_time(pid: int) -> float:
        """
        Helper method to retrieve the creation time of a process in seconds since epoch using ctypes.
        """
        if not kernel32:
            return 0.0
        # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000, fallback to PROCESS_QUERY_INFORMATION = 0x0400
        h_proc = kernel32.OpenProcess(0x1000, False, pid)
        if not h_proc:
            h_proc = kernel32.OpenProcess(0x0400, False, pid)
        if not h_proc:
            return 0.0
        
        creation_time = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()
        
        success = kernel32.GetProcessTimes(
            h_proc,
            ctypes.byref(creation_time),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time)
        )
        kernel32.CloseHandle(h_proc)
        
        if success:
            val = (creation_time.dwHighDateTime << 32) + creation_time.dwLowDateTime
            # Convert 100ns units since 1601 to unix epoch seconds (offset by 11644473600 seconds)
            return (val / 10000000.0) - 11644473600.0
        return 0.0

    @staticmethod
    def restore_foreground_process() -> bool:
        """
        Retrieves the active foreground window PID and checks if it's currently isolated.
        If it is, restores its original CPU affinity immediately to ensure responsiveness.
        Returns True if a process was restored.
        """
        if os.name != 'nt':
            return False
            
        fg_pid = 0
        try:
            import win32gui
            import win32process
            hwnd = win32gui.GetForegroundWindow()
            if hwnd:
                _, fg_pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            pass
            
        if fg_pid <= 0:
            return False
            
        with ProcessIsolationService._lock:
            record = ProcessIsolationService._isolated_pids.get(fg_pid)
            if not record:
                return False
                
        # Found an isolated process in foreground, restore it immediately
        try:
            create_time, original_affinity = record
            proc = psutil.Process(fg_pid)
            if proc.create_time() == create_time:
                proc.cpu_affinity(original_affinity)
                logger.info(f"Foreground switch detected: Restored affinity for {proc.name()} (PID: {fg_pid}) to prevent lag.")
                with ProcessIsolationService._lock:
                    ProcessIsolationService._isolated_pids.pop(fg_pid, None)
                return True
        except Exception as e:
            logger.debug(f"Failed to restore foreground process affinity: {e}")
            
        return False

    @staticmethod
    def isolate_background_processes(target_pid: int, isolation_pool: list, custom_whitelist=None) -> int:
        """
        Sets CPU affinity of background processes to the isolation pool.
        Skips system processes, the target game PID, the current optimizer process, and the active foreground process.
        Optimized with Win32 Toolhelp32 processes snapshot to reduce scanning overhead.
        """
        if not isolation_pool: 
            return 0
            
        whitelist = ProcessIsolationService.get_whitelist(custom_whitelist)
        count = 0
        my_pid = os.getpid()
        to_isolate = []
        
        # Get active foreground PID to exclude from isolation
        fg_pid = 0
        try:
            import win32gui
            import win32process
            hwnd = win32gui.GetForegroundWindow()
            if hwnd:
                _, fg_pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            pass

        exclude_pids = {0, 4, target_pid, my_pid}
        if fg_pid > 0:
            exclude_pids.add(fg_pid)
        
        use_fallback = True
        if kernel32 is not None:
            hSnapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
            if hSnapshot and hSnapshot != wintypes.HANDLE(-1).value and hSnapshot != ctypes.c_void_p(-1).value:
                use_fallback = False
                try:
                    pe = PROCESSENTRY32W()
                    pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
                    if kernel32.Process32FirstW(hSnapshot, ctypes.byref(pe)):
                        while True:
                            pid = pe.th32ProcessID
                            if pid not in exclude_pids:
                                create_time = ProcessIsolationService._get_process_create_time(pid)
                                if create_time > 0.0:
                                    with ProcessIsolationService._lock:
                                        # 1. Check if PID is already cached in whitelist
                                        if ProcessIsolationService._whitelisted_pids.get(pid) == create_time:
                                            if not kernel32.Process32NextW(hSnapshot, ctypes.byref(pe)):
                                                break
                                            continue
                                            
                                        # 2. Check if PID is already isolated or failed
                                        record = ProcessIsolationService._isolated_pids.get(pid)
                                        if record and record[0] == create_time:
                                            if not kernel32.Process32NextW(hSnapshot, ctypes.byref(pe)):
                                                break
                                            continue
                                        elif ProcessIsolationService._failed_pids.get(pid) == create_time:
                                            if not kernel32.Process32NextW(hSnapshot, ctypes.byref(pe)):
                                                break
                                            continue
                                            
                                    name = pe.szExeFile
                                    name_lower = name.lower() if name else ""
                                    name_no_ext = name_lower[:-4] if name_lower.endswith('.exe') else name_lower
                                    h_name = ProcessIsolationService.djb2_hash(name_lower)
                                    h_name_no_ext = ProcessIsolationService.djb2_hash(name_no_ext)
                                    
                                    if h_name in whitelist or h_name_no_ext in whitelist:
                                        with ProcessIsolationService._lock:
                                            ProcessIsolationService._whitelisted_pids[pid] = create_time
                                    else:
                                        to_isolate.append((pid, create_time))
                                        
                            if not kernel32.Process32NextW(hSnapshot, ctypes.byref(pe)):
                                break
                except Exception as e:
                    logger.debug(f"Error traversing Toolhelp32 snapshot: {e}")
                    use_fallback = True
                finally:
                    kernel32.CloseHandle(hSnapshot)
                    
        if use_fallback:
            logger.info("Using psutil fallback for background process scanning.")
            for proc in psutil.process_iter(['pid', 'name', 'create_time']):
                try:
                    pid = proc.info['pid']
                    if pid is None or pid in exclude_pids: 
                        continue
                    create_time = proc.info['create_time']
                    
                    with ProcessIsolationService._lock:
                        if ProcessIsolationService._whitelisted_pids.get(pid) == create_time:
                            continue
                        record = ProcessIsolationService._isolated_pids.get(pid)
                        if record and record[0] == create_time:
                            continue
                        if ProcessIsolationService._failed_pids.get(pid) == create_time:
                            continue
                        
                    name = proc.info['name'].lower() if proc.info['name'] else ""
                    name_no_ext = name[:-4] if name.endswith('.exe') else name
                    h_name = ProcessIsolationService.djb2_hash(name)
                    h_name_no_ext = ProcessIsolationService.djb2_hash(name_no_ext)
                    if h_name in whitelist or h_name_no_ext in whitelist: 
                        with ProcessIsolationService._lock:
                            ProcessIsolationService._whitelisted_pids[pid] = create_time
                        continue
                    
                    to_isolate.append((pid, create_time))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
                    
        # Apply affinity changes to filtered processes
        for pid, create_time in to_isolate:
            try:
                proc = psutil.Process(pid)
                # Re-verify process is still alive and has the same creation time
                if proc.create_time() != create_time:
                    continue
                current_affinity = proc.cpu_affinity()
                if set(current_affinity) != set(isolation_pool):
                    proc.cpu_affinity(isolation_pool)
                    with ProcessIsolationService._lock:
                        ProcessIsolationService._isolated_pids[pid] = (create_time, current_affinity)
                    count += 1
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied:
                with ProcessIsolationService._lock:
                    ProcessIsolationService._failed_pids[pid] = create_time
                continue
            except Exception as ex:
                logger.debug(f"Failed to isolate process (PID {pid}): {str(ex)}")
                continue
                
        # Clean up dead PIDs dynamically based on actual process existence
        with ProcessIsolationService._lock:
            dead_pids = [pid for pid in ProcessIsolationService._isolated_pids if not psutil.pid_exists(pid)]
            for pid in dead_pids:
                ProcessIsolationService._isolated_pids.pop(pid, None)
                    
            dead_failed_pids = [pid for pid in ProcessIsolationService._failed_pids if not psutil.pid_exists(pid)]
            for pid in dead_failed_pids:
                ProcessIsolationService._failed_pids.pop(pid, None)
                
            dead_white_pids = [pid for pid in ProcessIsolationService._whitelisted_pids if not psutil.pid_exists(pid)]
            for pid in dead_white_pids:
                ProcessIsolationService._whitelisted_pids.pop(pid, None)
                    
        logger.info(f"Background isolation cycle complete. Restructured {count} processes.")
        return count

    @staticmethod
    def restore_all_processes(target_pid_to_skip=None, custom_whitelist=None) -> int:
        """
        Restores CPU affinity of tracked background processes to their original configuration.
        """
        whitelist = ProcessIsolationService.get_whitelist(custom_whitelist)
        my_pid = os.getpid()
        count = 0
        
        with ProcessIsolationService._lock:
            if not ProcessIsolationService._isolated_pids:
                logger.info("No tracked isolated processes to restore.")
                return 0
            pids_to_restore = list(ProcessIsolationService._isolated_pids.items())

        logger.info(f"Restoring affinity for {len(pids_to_restore)} tracked isolated processes...")
        for pid, record in pids_to_restore:
            create_time, original_affinity = record
            if pid in [0, 4, my_pid]:
                continue
            if target_pid_to_skip and pid == target_pid_to_skip:
                continue
            try:
                proc = psutil.Process(pid)
                # Verify it's the same process by checking creation time
                if proc.create_time() != create_time:
                    continue
                name = proc.name().lower() if proc.name() else ""
                name_no_ext = name[:-4] if name.endswith('.exe') else name
                h_name = ProcessIsolationService.djb2_hash(name)
                h_name_no_ext = ProcessIsolationService.djb2_hash(name_no_ext)
                if h_name in whitelist or h_name_no_ext in whitelist:
                    continue
                
                current_affinity = proc.cpu_affinity()
                if set(current_affinity) != set(original_affinity):
                    proc.cpu_affinity(original_affinity)
                    count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            except Exception as ex:
                logger.debug(f"Failed to restore process PID {pid}: {str(ex)}")
            
        with ProcessIsolationService._lock:
            ProcessIsolationService._isolated_pids.clear()
            
        logger.info(f"Tracked process affinity restoration complete. Restored {count} processes.")
        return count

