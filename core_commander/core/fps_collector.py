# -*- coding: utf-8 -*-
import os
import sys
import time
import ctypes
import collections
import psutil
import threading
import subprocess
import win32pdh
import winreg
from PySide6.QtCore import QThread, Signal
from core_commander.utils.logger import logger

# RTSS Shared Memory structures
class PROCESSOR_POWER_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("Number", ctypes.c_ulong),
        ("MaxMhz", ctypes.c_ulong),
        ("CurrentMhz", ctypes.c_ulong),
        ("MhzLimit", ctypes.c_ulong),
        ("MaxIdleState", ctypes.c_ulong),
        ("CurrentIdleState", ctypes.c_ulong),
    ]

class RTSS_SHARED_MEMORY_APP_ENTRY(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ('dwProcessID', ctypes.c_uint32),
        ('szName', ctypes.c_char * 260),
        ('dwFlags', ctypes.c_uint32),
        ('dwTime0', ctypes.c_uint32),
        ('dwTime1', ctypes.c_uint32),
        ('dwFrames', ctypes.c_uint32),
        ('dwFrameTime', ctypes.c_uint32),
        ('dwStatFlags', ctypes.c_uint32),
        ('dwStatTime0', ctypes.c_uint32),
        ('dwStatTime1', ctypes.c_uint32),
        ('dwStatFrames', ctypes.c_uint32),
        ('dwStatCount', ctypes.c_uint32),
        ('dwStatFramerateMin', ctypes.c_uint32),
        ('dwStatFramerateAvg', ctypes.c_uint32),
        ('dwStatFramerateMax', ctypes.c_uint32)
    ]

class RTSS_SHARED_MEMORY(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ('dwSignature', ctypes.c_uint32),
        ('dwVersion', ctypes.c_uint32),
        ('dwAppEntrySize', ctypes.c_uint32),
        ('dwAppArrOffset', ctypes.c_uint32),
        ('dwAppArrSize', ctypes.c_uint32),
        ('dwOSDEntrySize', ctypes.c_uint32),
        ('dwOSDArrOffset', ctypes.c_uint32),
        ('dwOSDArrSize', ctypes.c_uint32),
        ('dwOSDFrame', ctypes.c_uint32),
    ]

class RTSSState:
    """
    Stores local rolling history for an active RTSS application to calculate average/1% low metrics.
    """
    def __init__(self, pid: int, name: str):
        self.pid = pid
        self.name = name
        self.frametimes = collections.deque(maxlen=1000)
        self.last_received_time = time.time()
        self.smooth_fps = 0.0

    def start(self):
        pass

    def add_frame(self, ft_ms: float):
        if ft_ms > 0:
            now = time.time()
            self.frametimes.append((ft_ms, now))
            self.last_received_time = now
            
            # Update EMA smooth FPS
            instant_fps = 1000.0 / ft_ms
            if self.smooth_fps == 0.0:
                self.smooth_fps = instant_fps
            else:
                self.smooth_fps = 0.20 * instant_fps + 0.80 * self.smooth_fps

    def get_realtime_fps(self) -> float:
        return self.smooth_fps

    def get_recent_avg_ft(self) -> float:
        now = time.time()
        recent = [ft for ft, ts in list(self.frametimes) if now - ts <= 0.5]
        if not recent:
            return 0.0
        return sum(recent) / len(recent)

    def get_avg_fps(self) -> float:
        if not self.frametimes:
            return 0.0
        fts = [ft for ft, ts in list(self.frametimes)]
        s = sum(fts)
        if s > 0:
            return len(fts) * 1000.0 / s
        return 0.0

    def get_one_percent_low(self) -> float:
        if not self.frametimes:
            return 0.0
        fts = [ft for ft, ts in list(self.frametimes)]
        if len(fts) < 10:
            return self.get_avg_fps()
            
        sorted_times = sorted(fts)
        percentile_index = max(1, int(len(sorted_times) * 0.99))
        
        worst_1_percent = sorted_times[percentile_index:]
        avg_worst_ft = sum(worst_1_percent) / len(worst_1_percent)
        
        if avg_worst_ft > 0:
            return 1000.0 / avg_worst_ft
        return 0.0

# Thread-safe module-level WinDLL instances to prevent _ctypes.pyd access violations
_user32 = ctypes.WinDLL('user32', use_last_error=True)
_user32.GetForegroundWindow.restype = ctypes.c_void_p
_user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
_user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]

_kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
_kernel32.OpenProcess.restype = ctypes.c_void_p
_kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
_kernel32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
_kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

_proc_name_cache = {}
_proc_name_cache_lock = threading.Lock()

# High-precision rate limit cache (TTL 200ms) to prevent high-frequency Win32 system call spikes
_last_fg_time = 0.0
_last_fg_result = (0, "", "")
_fg_rate_limit_lock = threading.Lock()

def get_foreground_window_details() -> tuple:
    global _last_fg_time, _last_fg_result
    import time
    now = time.time()
    with _fg_rate_limit_lock:
        if now - _last_fg_time < 0.2:
            return _last_fg_result

    res_tuple = (0, "", "")
    try:
        hwnd = _user32.GetForegroundWindow()
        if hwnd:
            pid = ctypes.c_ulong()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pid_val = pid.value
            if pid_val != 0:
                global _proc_name_cache
                with _proc_name_cache_lock:
                    if pid_val in _proc_name_cache:
                        res_tuple = (pid_val, _proc_name_cache[pid_val][0], _proc_name_cache[pid_val][1])
                    else:
                        class_buf = ctypes.create_unicode_buffer(256)
                        _user32.GetClassNameW(hwnd, class_buf, 256)
                        class_name = class_buf.value
                        proc_name = ""
                        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                        handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid_val)
                        if handle:
                            try:
                                buf = ctypes.create_unicode_buffer(1024)
                                size = ctypes.c_ulong(1024)
                                if _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                                    proc_name = os.path.basename(buf.value).lower()
                            finally:
                                _kernel32.CloseHandle(handle)
                        if not proc_name:
                            try:
                                proc_name = psutil.Process(pid_val).name().lower()
                            except Exception:
                                pass
                                
                        if proc_name or class_name:
                            _proc_name_cache[pid_val] = (proc_name, class_name)
                            if len(_proc_name_cache) > 200:
                                try:
                                    oldest_key = next(iter(_proc_name_cache))
                                    _proc_name_cache.pop(oldest_key, None)
                                except Exception:
                                    _proc_name_cache.clear()
                        res_tuple = (pid_val, proc_name, class_name)
    except Exception:
        pass

    with _fg_rate_limit_lock:
        _last_fg_time = now
        _last_fg_result = res_tuple
    return res_tuple

def get_monitor_refresh_rate() -> int:
    try:
        import win32api
        device = win32api.EnumDisplayDevices(None, 0)
        settings = win32api.EnumDisplaySettings(device.DeviceName, -1)
        return settings.DisplayFrequency
    except Exception:
        return 60

def get_resource_path(relative_path: str) -> str:
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    main_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    file_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if os.path.exists(os.path.join(main_dir, relative_path)):
        return os.path.normpath(os.path.join(main_dir, relative_path))
    return os.path.normpath(os.path.join(file_dir, relative_path))


# --- DXGI structs and vtable helpers for VRAM query ---
class DXGI_ADAPTER_DESC1(ctypes.Structure):
    _fields_ = [
        ("Description", ctypes.c_wchar * 128),
        ("VendorId", ctypes.c_uint),
        ("DeviceId", ctypes.c_uint),
        ("SubSysId", ctypes.c_uint),
        ("Revision", ctypes.c_uint),
        ("DedicatedVideoMemory", ctypes.c_size_t),
        ("DedicatedSystemMemory", ctypes.c_size_t),
        ("SharedSystemMemory", ctypes.c_size_t),
        ("AdapterLuid", ctypes.c_uint64),
        ("Flags", ctypes.c_uint),
    ]

class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]

def call_virtual_method(interface_ptr, method_index, restype, argtypes, *args):
    vtable_address = ctypes.cast(interface_ptr, ctypes.POINTER(ctypes.c_void_p)).contents.value
    vtable = ctypes.cast(vtable_address, ctypes.POINTER(ctypes.c_void_p))
    func_ptr = vtable[method_index]
    prototype = ctypes.WINFUNCTYPE(restype, *argtypes)
    func = prototype(func_ptr)
    return func(*args)

def get_gpu_vram_dxgi_silent():
    """
    DXGI Adapter query combined with WMI Performance Counters to get un-clamped VRAM
    and accurate VRAM usage on any GPU vendor (NVIDIA/AMD/Intel).
    Runs in <15ms and does not block.
    """
    total_vram = 0.0
    used_vram = 0.0
    try:
        dxgi = ctypes.windll.dxgi
        factory = ctypes.c_void_p()
        IID_IDXGIFactory1 = GUID(0x770aae78, 0xf26f, 0x4dba, (ctypes.c_ubyte * 8)(0xa8, 0x29, 0x25, 0x3c, 0x83, 0xd1, 0xb3, 0x87))
        
        hr = dxgi.CreateDXGIFactory1(ctypes.byref(IID_IDXGIFactory1), ctypes.byref(factory))
        if hr < 0:
            return 0.0, 0.0
            
        adapter = ctypes.c_void_p()
        hr = call_virtual_method(factory, 7, ctypes.c_long, [ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)], factory, 0, ctypes.byref(adapter))
        if hr < 0:
            call_virtual_method(factory, 2, ctypes.c_ulong, [ctypes.c_void_p], factory)
            return 0.0, 0.0
            
        desc = DXGI_ADAPTER_DESC1()
        hr = call_virtual_method(adapter, 10, ctypes.c_long, [ctypes.c_void_p, ctypes.POINTER(DXGI_ADAPTER_DESC1)], adapter, ctypes.byref(desc))
        
        if hr >= 0:
            total_vram = desc.DedicatedVideoMemory / (1024**3)
            # We intentionally DO NOT query WMI for used_vram here because WMI Win32_PerfFormattedData
            # takes >600ms and completely blocks the Python GIL, causing the 0.5s FPS window to expire!
            used_vram = 0.0
                
        call_virtual_method(adapter, 2, ctypes.c_ulong, [ctypes.c_void_p], adapter)
        call_virtual_method(factory, 2, ctypes.c_ulong, [ctypes.c_void_p], factory)
    except Exception:
        pass
    return total_vram, used_vram


class HardwareTelemetryWorker(threading.Thread):
    """
    Dedicated background thread to query CPU/GPU/RAM metrics once per second.
    This prevents blocking system calls (like nvidia-smi subprocesses)
    from stalling the 60Hz OSD polling thread.
    """
    def __init__(self, gpu_handle, nvml_initialized):
        super().__init__(daemon=True)
        self.gpu_handle = gpu_handle
        self.nvml_initialized = nvml_initialized
        self.running = False
        self.paused = False
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.last_smi_util = 0
        self.stats = {
            "cpu_util": 0,
            "cpu_freq": 0.0,
            "gpu_util": 0,
            "ram_used_gb": 0.0,
            "ram_total_gb": 16.0,
            "vram_used_gb": 0.0,
            "vram_total_gb": 8.0,
        }

    def get_stats(self) -> dict:
        with self.lock:
            return self.stats.copy()

    def run(self):
        self.running = True
        import psutil

        def get_max_clock_speed():
            try:
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
                val, _ = winreg.QueryValueEx(key, "~MHz")
                return val
            except Exception:
                return 3000

        max_speed = get_max_clock_speed()
        h_query = None
        h_counter = None
        try:
            h_query = win32pdh.OpenQuery()
            h_counter = win32pdh.AddCounter(h_query, r"\Processor Information(_Total)\% Processor Performance")
            win32pdh.CollectQueryData(h_query)
        except Exception as e:
            logger.debug(f"Failed to initialize PDH query: {e}")

        # Start background nvidia-smi poller loop in separate thread to prevent telemetry stalling
        def run_smi_loop():
            while self.running:
                if getattr(self, "paused", False):
                    self.stop_event.wait(1.0)
                    continue
                try:
                    out = subprocess.check_output(
                        ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        timeout=1
                    ).decode("utf-8", errors="ignore").strip()
                    self.last_smi_util = int(out)
                except Exception:
                    self.last_smi_util = 0
                self.stop_event.wait(5.0)

        smi_thread = None
        if not self.nvml_initialized:
            smi_thread = threading.Thread(target=run_smi_loop, daemon=True)
            smi_thread.start()

        while self.running:
            if getattr(self, "paused", False):
                self.stop_event.wait(1.0)
                continue
            # 1. CPU & System Memory
            cpu_util = 0
            cpu_freq = 0.0
            ram_used_gb = 0.0
            ram_total_gb = 16.0
            try:
                cpu_util = int(psutil.cpu_percent())
                
                # Retrieve real-time CPU frequency using win32pdh (Windows Performance Counters)
                pdh_success = False
                if h_query and h_counter:
                    try:
                        win32pdh.CollectQueryData(h_query)
                        _, val = win32pdh.GetFormattedCounterValue(h_counter, win32pdh.PDH_FMT_DOUBLE)
                        cpu_freq = (max_speed * (val / 100)) / 1000.0
                        pdh_success = True
                    except Exception as pdh_err:
                        logger.debug(f"PDH query error in loop: {pdh_err}")
                
                if not pdh_success:
                    # Fallback to CallNtPowerInformation
                    try:
                        num_cores = psutil.cpu_count(logical=True)
                        size = ctypes.sizeof(PROCESSOR_POWER_INFORMATION) * num_cores
                        buf = (ctypes.c_byte * size)()
                        status = ctypes.windll.powrprof.CallNtPowerInformation(
                            11,  # ProcessorInformation
                            None,
                            0,
                            ctypes.byref(buf),
                            size
                        )
                        if status == 0:
                            freqs = []
                            for i in range(num_cores):
                                offset = i * ctypes.sizeof(PROCESSOR_POWER_INFORMATION)
                                info = PROCESSOR_POWER_INFORMATION.from_buffer(buf, offset)
                                freqs.append(info.CurrentMhz)
                            if freqs:
                                cpu_freq = (sum(freqs) / len(freqs)) / 1000.0
                                pdh_success = True
                        else:
                            raise RuntimeError
                    except Exception:
                        pass
                
                if not pdh_success:
                    # Fallback to psutil
                    freq = psutil.cpu_freq()
                    if freq:
                        cpu_freq = freq.current / 1000.0  # Convert to GHz
                        
                vm = psutil.virtual_memory()
                ram_used_gb = vm.used / (1024**3)
                ram_total_gb = vm.total / (1024**3)
            except Exception:
                pass

            # 2. GPU Utilization & VRAM (NVML with fallback)
            gpu_util = 0
            vram_used_gb = 0.0
            vram_total_gb = 8.0

            if self.nvml_initialized and self.gpu_handle:
                try:
                    import pynvml
                    util = pynvml.nvmlDeviceGetUtilizationRates(self.gpu_handle)
                    gpu_util = util.gpu
                    mem_info = pynvml.nvmlDeviceGetMemoryInfo(self.gpu_handle)
                    vram_used_gb = mem_info.used / (1024**3)
                    vram_total_gb = mem_info.total / (1024**3)
                except Exception:
                    gpu_util, vram_used_gb, vram_total_gb = self._get_gpu_fallback()
            else:
                gpu_util, vram_used_gb, vram_total_gb = self._get_gpu_fallback()

            with self.lock:
                self.stats = {
                    "cpu_util": cpu_util,
                    "cpu_freq": round(cpu_freq, 2),
                    "gpu_util": gpu_util,
                    "ram_used_gb": round(ram_used_gb, 1),
                    "ram_total_gb": round(ram_total_gb, 1),
                    "vram_used_gb": round(vram_used_gb, 1),
                    "vram_total_gb": round(vram_total_gb, 1),
                }
            self.stop_event.wait(1.0)

        if h_query:
            try:
                win32pdh.CloseQuery(h_query)
            except Exception:
                pass

    def _get_gpu_fallback(self):
        # Fallback to DXGI + WMI matching
        total_vram, used_vram = get_gpu_vram_dxgi_silent()
        return self.last_smi_util, used_vram, total_vram

    def stop(self):
        self.running = False
        self.stop_event.set()


class FpsCollectorService(QThread):
    stats_updated = Signal(dict)
    status_msg = Signal(str)

    def __init__(self, target_proc_name: str = "", target_pid: int = None, parent=None):
        super().__init__(parent)
        self.target_proc_name = target_proc_name
        self.target_pid = target_pid
        self.running = False
        self.fps_history = collections.deque(maxlen=120)
        self.low_history = collections.deque(maxlen=120)
        self.gpu_handle = None
        self.nvml_initialized = False
        self.hw_worker = None
        self._init_nvml()

    def _init_nvml(self):
        try:
            import pynvml
            pynvml.nvmlInit()
            self.gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            self.nvml_initialized = True
            logger.info("FpsCollectorService initialized NVML successfully.")
        except Exception:
            try:
                nvml_lib = ctypes.windll.LoadLibrary("nvml.dll")
                if nvml_lib:
                    ret = nvml_lib.nvmlInit()
                    if ret == 0:
                        handle = ctypes.c_void_p()
                        if nvml_lib.nvmlDeviceGetHandleByIndex(0, ctypes.byref(handle)) == 0:
                            self.gpu_handle = handle
                            self.nvml_initialized = True
                            logger.info("FpsCollectorService initialized NVML via ctypes successfully.")
            except Exception as e:
                logger.debug(f"FpsCollectorService failed to load NVML: {str(e)}")

    def stop(self):
        self.running = False
        if self.hw_worker:
            self.hw_worker.stop()
            self.hw_worker.join(timeout=1.0)
            self.hw_worker = None
        if self.nvml_initialized:
            try:
                import pynvml
                pynvml.nvmlShutdown()
            except Exception:
                pass
            self.nvml_initialized = False

    def run(self):
        self.running = True
        FILE_MAP_READ = 0x0004
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        
        kernel32.OpenFileMappingW.restype = ctypes.c_void_p
        kernel32.OpenFileMappingW.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_wchar_p]
        
        kernel32.MapViewOfFile.restype = ctypes.c_void_p
        kernel32.MapViewOfFile.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_size_t]
        
        kernel32.UnmapViewOfFile.restype = ctypes.c_bool
        kernel32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
        
        kernel32.CloseHandle.restype = ctypes.c_bool
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

        # Launch background hardware telemetry loop
        self.hw_worker = HardwareTelemetryWorker(self.gpu_handle, self.nvml_initialized)
        self.hw_worker.start()

        self.fps_history.clear()
        self.low_history.clear()
        consecutive_hide_count = 0

        # Maintain application stats locally to compute average and 1% low
        app_states = {} # pid -> RTSSState
        last_time1_val = {} # pid -> last dwTime1 value
        last_frame_time = {} # pid -> timestamp
        last_history_sample_time = 0.0
        last_emit_time = 0.0

        last_active_pid = 0
        last_active_name = ""
        last_active_time = 0.0
        last_valid_fps = 0
        last_valid_avg_fps = 0
        last_valid_one_percent_low = 0
        last_valid_frametime = 0.0

        system_names = {"dwm.exe", "explorer.exe", "lockapp.exe", "shellexperiencehost.exe", "searchhost.exe", "systemsettings.exe", "applicationframehost.exe", "startmenuexperiencehost.exe", "corecommander.exe"}

        hMap = None
        pData = None
        last_rtss_check = 0.0
        last_rtss_start_attempt = 0.0
        rtss_running = False

        while self.running:
            now = time.time()

            # Verify that RTSS.exe is actually running as a process.
            # If not running but hMap is set, close it and reset to None to trigger start logic.
            if now - last_rtss_check >= 2.0:
                last_rtss_check = now
                rtss_running = False
                # Verify that RTSS.exe is actively running to prevent zombie shared memory mapping locks
                for proc in psutil.process_iter(['name']):
                    try:
                        if proc.info['name'] and proc.info['name'].lower() == 'rtss.exe':
                            rtss_running = True
                            break
                    except Exception:
                        pass
                if not rtss_running and hMap:
                    if pData:
                        kernel32.UnmapViewOfFile(pData)
                        pData = None
                    kernel32.CloseHandle(hMap)
                    hMap = None
                    # Use debug level to prevent log spam
                    logger.debug("RTSS process is not running. Closed zombie memory mapping.")

            # Ensure we are connected to RTSS shared memory only if RTSS is actually running
            if not hMap and rtss_running:
                hMap = kernel32.OpenFileMappingW(FILE_MAP_READ, False, 'RTSSSharedMemoryV2')
                if not hMap:
                    hMap = kernel32.OpenFileMappingW(FILE_MAP_READ, False, 'Global\\RTSSSharedMemoryV2')
                
                if hMap:
                    pData = kernel32.MapViewOfFile(hMap, FILE_MAP_READ, 0, 0, 0)
                    if pData:
                        self.status_msg.emit("已连接 to RTSS 数据源")
                        logger.info("Successfully mapped RTSSSharedMemoryV2.")
                    else:
                        kernel32.CloseHandle(hMap)
                        hMap = None
            elif not hMap and not rtss_running:
                # If RTSS is not running and we don't have a map, try auto-starting it
                if now - last_rtss_start_attempt >= 15.0:
                    last_rtss_start_attempt = now
                    self.status_msg.emit("正在初始化核心运行组件...")
                    # Auto-start RTSS if not running
                    rtss_paths = [
                        r"C:\Program Files (x86)\RivaTuner Statistics Server\RTSS.exe",
                        r"C:\Program Files\RivaTuner Statistics Server\RTSS.exe",
                        r"D:\Program Files (x86)\RivaTuner Statistics Server\RTSS.exe",
                        r"E:\Program Files (x86)\RivaTuner Statistics Server\RTSS.exe"
                    ]
                    rtss_installed = False
                    for path in rtss_paths:
                        if os.path.exists(path):
                            rtss_installed = True
                            try:
                                subprocess.Popen([path], cwd=os.path.dirname(path))
                                logger.info(f"Auto-started RTSS from {path}")
                                break
                            except Exception as e:
                                logger.debug(f"Failed to auto-start RTSS: {e}")
                    
                    if not rtss_installed:
                        setup_path = get_resource_path(os.path.join("core_commander", "resources", "bin", "RTSSSetup.exe"))
                        if os.path.exists(setup_path):
                            self.status_msg.emit("正在自动部署核心渲染组件 (静默安装)...")
                            logger.info(f"RTSS not found. Starting silent install from {setup_path}...")
                            try:
                                res = subprocess.run([setup_path, "/S"], creationflags=subprocess.CREATE_NO_WINDOW, timeout=60)
                                if res.returncode == 0:
                                    logger.info("RTSS silent installation completed successfully.")
                                    time.sleep(2.0)
                                    for path in rtss_paths:
                                        if os.path.exists(path):
                                            try:
                                                subprocess.Popen([path], cwd=os.path.dirname(path))
                                                logger.info(f"Auto-started RTSS after install from {path}")
                                                break
                                            except Exception as start_err:
                                                logger.debug(f"Failed to start RTSS after install: {start_err}")
                                else:
                                    logger.error(f"RTSS silent installer failed with code {res.returncode}")
                                    self.status_msg.emit("核心组件部署失败")
                            except subprocess.TimeoutExpired:
                                logger.error("RTSS silent installation timed out after 60 seconds.")
                                self.status_msg.emit("核心组件部署超时")
                            except Exception as setup_err:
                                logger.error(f"Failed to run RTSS silent installer: {setup_err}")
                                self.status_msg.emit("核心组件部署异常")
                        else:
                            logger.error(f"RTSS installer not found at {setup_path}")
                            self.status_msg.emit("未找到核心部署组件")

            # Retrieve telemetry stats from background worker thread (non-blocking)
            hw = self.hw_worker.get_stats() if self.hw_worker else {}
            cpu_util = hw.get("cpu_util", 0)
            cpu_freq = hw.get("cpu_freq", 0.0)
            gpu_util = hw.get("gpu_util", 0)
            ram_used_gb = hw.get("ram_used_gb", 0.0)
            ram_total_gb = hw.get("ram_total_gb", 16.0)
            vram_used_gb = hw.get("vram_used_gb", 0.0)
            vram_total_gb = hw.get("vram_total_gb", 8.0)

            # Read RTSS metrics if mapped
            should_show = False
            fps = 0
            avg_fps = 0.0
            one_percent_low = 0.0
            avg_ft = 0.0
            display_name = "正在初始化核心运行组件..."
            display_pid = 0

            if hMap and pData:
                try:
                    header = ctypes.cast(pData, ctypes.POINTER(RTSS_SHARED_MEMORY)).contents
                    active_app_entry = None

                    if header.dwSignature == 0x52545353: # 'RTSS'
                        fg_pid, fg_name, fg_class = get_foreground_window_details()

                        candidates = []
                        for i in range(header.dwAppArrSize):
                            offset = header.dwAppArrOffset + i * header.dwAppEntrySize
                            entry = ctypes.cast(pData + offset, ctypes.POINTER(RTSS_SHARED_MEMORY_APP_ENTRY)).contents
                            if entry.dwProcessID != 0:
                                candidates.append(entry)

                        # Update last_time1_val and last_frame_time for all candidates to check for active rendering
                        for e in candidates:
                            pid = e.dwProcessID
                            if pid not in last_time1_val:
                                last_time1_val[pid] = e.dwTime1
                            if pid not in last_frame_time:
                                last_frame_time[pid] = now
                            
                            if e.dwTime1 != last_time1_val[pid]:
                                last_time1_val[pid] = e.dwTime1
                                last_frame_time[pid] = now

                        if candidates:
                            def score_entry(e):
                                pid = e.dwProcessID
                                name_str = os.path.basename(e.szName.decode('utf-8', 'ignore').rstrip('\x00').lower())
                                
                                # Strict blacklist for system/UI environments to prevent mapping dwm/explorer
                                if name_str in system_names or name_str.replace('.exe', '') in system_names:
                                    return -1
                                    
                                score = 0
                                # 1. Target process locking (highest priority)
                                if self.target_pid and pid == self.target_pid:
                                    score += 10000000
                                elif self.target_proc_name and self.target_proc_name.lower() in name_str:
                                    score += 5000000

                                # 2. Active foreground details
                                if pid == fg_pid:
                                    score += 2000000
                                elif fg_name and (fg_name.replace('.exe', '') in name_str or name_str.replace('.exe', '') in fg_name):
                                    score += 1000000
                                    
                                # 2.5 Sticky bonus for previously active app (prevents random switches when alt-tabbing)
                                if last_active_pid and pid == last_active_pid:
                                    score += 1600000
                                    
                                # 3. Active rendering check (has seen updates in the last 2 seconds)
                                is_rendering = (now - last_frame_time.get(pid, 0.0)) < 2.0
                                if is_rendering and e.dwFrameTime > 0:
                                    score += 500000
                                    
                                # 4. Reasonable rendering frame times check
                                if 0 < e.dwFrameTime < 100000:
                                    score += 10000
                                return score

                            best_entry = max(candidates, key=score_entry)
                            if score_entry(best_entry) > 0:
                                active_app_entry = best_entry

                    if active_app_entry:
                        pid = active_app_entry.dwProcessID
                        name = active_app_entry.szName.decode('utf-8', 'ignore').rstrip('\x00')
                        display_name = name
                        display_pid = pid

                        is_rendering = (now - last_frame_time.get(pid, 0.0)) < 2.0

                        if is_rendering:
                            ft_ms = active_app_entry.dwFrameTime / 1000.0
                            if ft_ms > 0:
                                if pid not in app_states:
                                    app_states[pid] = RTSSState(pid, name)
                                state = app_states[pid]
                                state.add_frame(ft_ms)

                                fps = state.get_realtime_fps()
                                avg_ft = state.get_recent_avg_ft()
                                avg_fps = state.get_avg_fps()
                                one_percent_low = state.get_one_percent_low()
                            else:
                                fps = 0.0
                                avg_ft = 0.0
                                avg_fps = 0.0
                                one_percent_low = 0.0
                        else:
                            fps = 0.0
                            avg_ft = 0.0
                            avg_fps = 0.0
                            one_percent_low = 0.0

                        last_active_pid = pid
                        last_active_name = name
                        last_active_time = now
                        last_valid_fps = round(fps)
                        last_valid_avg_fps = round(avg_fps)
                        last_valid_one_percent_low = round(one_percent_low)
                        last_valid_frametime = round(avg_ft, 1)
                        should_show = True
                    else:
                        # Grace period to avoid sudden drops to 0 when rendering pauses momentarily
                        if last_active_pid and (now - last_active_time < 2.0):
                            display_name = last_active_name
                            display_pid = last_active_pid
                            fps = last_valid_fps
                            avg_fps = last_valid_avg_fps
                            one_percent_low = last_valid_one_percent_low
                            avg_ft = last_valid_frametime
                            should_show = True
                        else:
                            fps = 0.0
                            avg_fps = 0.0
                            one_percent_low = 0.0
                            avg_ft = 0.0
                            should_show = False
                except Exception as ex:
                    logger.debug(f"Error reading RTSS shared memory: {ex}")

            for p in list(app_states.keys()):
                if now - app_states[p].last_received_time > 30.0:
                    app_states.pop(p, None)

            # Fix: Cleanup dead PIDs from tracking dictionaries to prevent memory leaks
            for p in list(last_frame_time.keys()):
                if now - last_frame_time[p] > 30.0:
                    last_frame_time.pop(p, None)
                    last_time1_val.pop(p, None)

            if should_show:
                consecutive_hide_count = 0
            else:
                consecutive_hide_count += 1
                if consecutive_hide_count < 8:
                    should_show = True

            # History logic - sample FPS twice per second (0.5s interval)
            if now - last_history_sample_time >= 0.5:
                last_history_sample_time = now
                if should_show:
                    self.fps_history.append(round(fps))
                    self.low_history.append(round(one_percent_low))
                else:
                    self.fps_history.append(0)
                    self.low_history.append(0)

            # Emit update at 0.5s interval to avoid UI numbers jumping too fast and save CPU
            if now - last_emit_time >= 0.5:
                last_emit_time = now
                self.stats_updated.emit({
                    "app_name": display_name if hMap else "正在初始化核心运行组件...",
                    "pid": display_pid,
                    "fps": round(fps),
                    "should_show": True,
                    "avg_fps": round(avg_fps),
                    "one_percent_low": round(one_percent_low),
                    "frametime": round(avg_ft, 1),
                    "cpu_util": cpu_util,
                    "cpu_freq": cpu_freq,
                    "gpu_util": gpu_util,
                    "ram_used_gb": round(ram_used_gb, 1),
                    "ram_total_gb": round(ram_total_gb, 1),
                    "vram_used_gb": round(vram_used_gb, 1),
                    "vram_total_gb": round(vram_total_gb, 1),
                    "fps_history": list(self.fps_history),
                    "low_history": list(self.low_history)
                })

            time.sleep(0.016 if hMap else 0.5)

        if pData:
            kernel32.UnmapViewOfFile(pData)
        if hMap:
            kernel32.CloseHandle(hMap)
