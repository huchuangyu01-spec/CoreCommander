# -*- coding: utf-8 -*-
import time
import os
import psutil
from PySide6.QtCore import QThread, Signal
from core_commander.utils.logger import logger
from core_commander.core.memory import MemoryService
from core_commander.core.gpu_drs import NvidiaDrsService
from core_commander.core.gpu_smi import GpuSmiService
from core_commander.core.irq_aff import IrqAffinityService

class GameWatchdogService(QThread):
    """
    Background watchdog service to monitor the launch and exit of target game processes.
    Enables zero-latency automatic switching between Daily Mode and Game Mode.
    Monitors memory pressure and runs targeted memory purges during gaming sessions.
    """
    game_detected_signal = Signal(int, str)  # (pid, name)
    game_exited_signal = Signal(str)         # (name)
    memory_warning_signal = Signal(float)    # (available_percent)

    def __init__(self, target_exe_name: str, target_path: str = "", settings_dict: dict = None, parent=None):
        super().__init__(parent)
        self.target_exe = target_exe_name.lower().strip()
        self.target_path = os.path.normpath(target_path).lower().strip() if target_path else ""
        self.settings_dict = settings_dict or {}
        self.running = False
        self.active_game_pid = None
        self.original_power_scheme = None
        self.last_memory_cleanup_time = 0
        self.last_gpu_check_time = 0
        self.gpu_throttled = False

    def stop(self):
        self.running = False

    def safe_sleep(self, seconds: float):
        """
        Sleeps in small increments to allow responsive thread stopping.
        """
        steps = int(seconds / 0.1)
        for _ in range(steps):
            if not self.running:
                break
            time.sleep(0.1)

    def find_game_process(self) -> tuple:
        """
        Scans running processes using lightweight Win32 API snapshotting to keep CPU usage at ~0%.
        """
        if os.name != 'nt':
            # Fallback for non-Windows platforms
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    name_raw = proc.info.get('name')
                    name = name_raw.lower() if name_raw else ""
                    if name == self.target_exe:
                        pid = proc.info.get('pid')
                        exe_raw = proc.exe()
                        exe = exe_raw.lower() if isinstance(exe_raw, str) else ""
                        if not self.target_path or exe == self.target_path:
                            return pid, name
                except Exception:
                    continue
            return None

        import ctypes
        from ctypes import wintypes

        # Define Toolhelp32 structures and constants
        TH32CS_SNAPPROCESS = 0x00000002
        MAX_PATH = 260

        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ('dwSize', wintypes.DWORD),
                ('cntUsage', wintypes.DWORD),
                ('th32ProcessID', wintypes.DWORD),
                ('th32DefaultHeapID', ctypes.c_void_p),
                ('th32ModuleID', wintypes.DWORD),
                ('cntThreads', wintypes.DWORD),
                ('th32ParentProcessID', wintypes.DWORD),
                ('pcPriClassBase', wintypes.LONG),
                ('dwFlags', wintypes.DWORD),
                ('szExeFile', ctypes.c_char * MAX_PATH)
            ]

        CreateToolhelp32Snapshot = ctypes.windll.kernel32.CreateToolhelp32Snapshot
        CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        CreateToolhelp32Snapshot.restype = wintypes.HANDLE

        Process32First = ctypes.windll.kernel32.Process32First
        Process32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
        Process32First.restype = wintypes.BOOL

        Process32Next = ctypes.windll.kernel32.Process32Next
        Process32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
        Process32Next.restype = wintypes.BOOL

        CloseHandle = ctypes.windll.kernel32.CloseHandle
        CloseHandle.argtypes = [wintypes.HANDLE]
        CloseHandle.restype = wintypes.BOOL

        OpenProcess = ctypes.windll.kernel32.OpenProcess
        OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        OpenProcess.restype = wintypes.HANDLE

        QueryFullProcessImageNameA = ctypes.windll.kernel32.QueryFullProcessImageNameA
        QueryFullProcessImageNameA.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_char_p, ctypes.POINTER(wintypes.DWORD)]
        QueryFullProcessImageNameA.restype = wintypes.BOOL

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

        h_snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if h_snapshot == wintypes.HANDLE(-1).value or not h_snapshot:
            return None

        try:
            pe = PROCESSENTRY32()
            pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
            if Process32First(h_snapshot, ctypes.byref(pe)):
                while True:
                    exe_name = pe.szExeFile.decode('ansi', errors='ignore').lower()
                    if exe_name == self.target_exe:
                        pid = pe.th32ProcessID
                        
                        # Fetch full path if target_path is specified
                        if self.target_path:
                            h_proc = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                            if h_proc:
                                try:
                                    buf = ctypes.create_string_buffer(MAX_PATH)
                                    size = wintypes.DWORD(MAX_PATH)
                                    if QueryFullProcessImageNameA(h_proc, 0, buf, ctypes.byref(size)):
                                        full_path = buf.value.decode('ansi', errors='ignore').lower()
                                        if os.path.normpath(full_path) == self.target_path:
                                            return pid, exe_name
                                finally:
                                    CloseHandle(h_proc)
                        else:
                            return pid, exe_name
                    if not Process32Next(h_snapshot, ctypes.byref(pe)):
                        break
        finally:
            CloseHandle(h_snapshot)
        return None

    def run(self):
        self.running = True
        logger.info(f"Game Watchdog started. Monitoring for target game: {self.target_exe}")

        # Self-heal WLAN state on cold start
        try:
            self.check_and_heal_wlan_autostatic()
        except Exception as e:
            logger.error(f"Error executing cold-start WLAN self-healing: {e}")

        # Try WMI event subscription for process start
        watcher = None
        c = None
        com_initialized = False
        wmi_consecutive_failures = 0
        try:
            import wmi
            import pythoncom
            pythoncom.CoInitialize()
            com_initialized = True
            c = wmi.WMI()
            # Win32_ProcessStartTrace triggers when any process starts
            watcher = c.watch_for(raw_wql="SELECT * FROM Win32_ProcessStartTrace")
            logger.info("WMI Win32_ProcessStartTrace listener registered.")
        except Exception as e:
            logger.warning(f"Could not register WMI event listener: {str(e)}. Falling back to polling.")

        try:
            while self.running:
                try:
                    # If game is not active, wait for launch
                    if self.active_game_pid is None:
                        # Re-initialize WMI process trace listener if COM was uninitialized during game mode
                        if not com_initialized:
                            try:
                                import wmi
                                import pythoncom
                                pythoncom.CoInitialize()
                                com_initialized = True
                                c = wmi.WMI()
                                watcher = c.watch_for(raw_wql="SELECT * FROM Win32_ProcessStartTrace")
                                logger.info("Successfully re-registered WMI process listener for launch detection.")
                                wmi_consecutive_failures = 0
                            except Exception as reinit_err:
                                logger.warning(f"Could not re-initialize WMI listener: {reinit_err}. Falling back to polling.")
                                watcher = None
                                c = None

                        game_info = None
                        if watcher:
                            try:
                                # Wait for a process start event (250ms timeout)
                                process_event = watcher(timeout_ms=250)
                                proc_name = process_event.ProcessName.lower()
                                if proc_name == self.target_exe:
                                    game_info = (process_event.ProcessID, proc_name)
                                wmi_consecutive_failures = 0  # Reset on successful wait
                            except wmi.x_wmi_timed_out:
                                # Keep thread alive/responsive on timeout
                                time.sleep(0.1)
                                import pythoncom
                                pythoncom.PumpWaitingMessages()
                            except Exception as wmi_err:
                                err_str = str(wmi_err)
                                # Only log non-transient COM errors (ignore expected 'SWbemPropertySet' not found errors)
                                if 'SWbemPropertySet' not in err_str:
                                    logger.debug(f"WMI event processing failed: {err_str}")
                                    wmi_consecutive_failures += 1
                                    if wmi_consecutive_failures >= 3:
                                        logger.warning("WMI event listener suffered consecutive failures. Demoting WMI watcher to polling.")
                                        watcher = None
                                        c = None
                                # Do not kill the watcher for transient COM errors
                                import pythoncom
                                pythoncom.PumpWaitingMessages()
                        
                        if not game_info:
                            # Polling fallback
                            game_info = self.find_game_process()
 
                        if game_info:
                            pid, name = game_info
                            self.active_game_pid = pid
                            logger.info(f"Target game detected: {name} (PID: {pid}). Activating Game Mode optimizations.")
                            self.game_detected_signal.emit(pid, name)
                            
                            # Apply dynamic driver and hardware optimizations
                            self.apply_game_mode_optimizations(pid)
                            
                            # Deregister WMI event listener and uninitialize COM immediately to eliminate telemetry overhead
                            if watcher or c or com_initialized:
                                try:
                                    watcher = None
                                    c = None
                                    if com_initialized:
                                        pythoncom.CoUninitialize()
                                        com_initialized = False
                                    logger.info("Deregistered WMI process listener and uninitialized COM for zero-telemetry during gameplay.")
                                except Exception as wmi_free_err:
                                    logger.debug(f"Failed to release WMI listener on game launch: {wmi_free_err}")
 
                            # Trigger upfront memory optimization for background processes to free space for the game
                            try:
                                logger.info(f"Triggering upfront background memory optimization for game launch (PID: {pid})...")
                                MemoryService.clean_memory_smart(pid)
                                self.last_memory_cleanup_time = time.time()
                            except Exception as mem_err:
                                logger.warning(f"Failed to execute upfront memory optimization: {mem_err}")
 
                            # Warm up network throttler cache and pre-install rules synchronously
                            try:
                                from core_commander.core.tweaks.throttler import NetworkThrottlerService
                                NetworkThrottlerService.pre_create_rules(pid, name)
                            except Exception as ce:
                                logger.warning(f"Failed to pre-create throttler rules on game launch: {ce}")
                        else:
                            # Sleep 2 seconds responsively
                            self.safe_sleep(2)

                    # If game is active, monitor its state and system memory pressure
                    else:
                        if not psutil.pid_exists(self.active_game_pid):
                            logger.info(f"Game process (PID {self.active_game_pid}) exited. Restoring Daily Mode settings.")
                            self.game_exited_signal.emit(self.target_exe)
                            self.revert_game_mode_optimizations()
                            self.active_game_pid = None
                        else:
                            # Memory pressure sensing
                            mem = psutil.virtual_memory()
                            available_percent = (mem.available / mem.total) * 100.0
                            if available_percent < 8.0:  # Free RAM drops below 8%
                                now = time.time()
                                if now - self.last_memory_cleanup_time > 300: # 5 minutes rate limit
                                    logger.warning(f"Low memory alert ({available_percent:.1f}% free). Triggering smart RAM purge.")
                                    self.memory_warning_signal.emit(available_percent)
                                    # Run smart memory cleaning (shielding the game)
                                    MemoryService.clean_memory_smart(self.active_game_pid)
                                    self.last_memory_cleanup_time = now
                                else:
                                    logger.debug("Low memory alert bypassed due to 5-minute rate limit constraint.")
                            
                            # Periodically refresh network throttler cache (active ports, etc.)
                            try:
                                from core_commander.core.tweaks.throttler import NetworkThrottlerService
                                NetworkThrottlerService.cache_target_info(self.active_game_pid, self.target_exe)
                            except Exception as ce:
                                logger.debug(f"Failed to refresh throttler cache: {ce}")

                            # GPU Thermal and TDR monitoring (every 10 seconds)
                            now = time.time()
                            if now - self.last_gpu_check_time >= 10:
                                self.last_gpu_check_time = now
                                try:
                                    from core_commander.core.gpu_oc import GpuOverclockService
                                    from core_commander.config.settings import AppSettings
                                    
                                    app_settings = AppSettings()
                                    gpu_info = GpuOverclockService.get_gpu_oc_info()
                                    if gpu_info.get("supported", False):
                                        core_temp = gpu_info.get("live_temp", 0)
                                        vram_temp = gpu_info.get("live_vram_temp", 0)
                                        actual_core_offset = gpu_info.get("core_offset", 0)
                                        actual_mem_offset = gpu_info.get("mem_offset", 0)
                                        
                                        # 1. TDR Detection (actual offsets reset to 0 while overclock_applied is True)
                                        if GpuOverclockService.overclock_applied and actual_core_offset == 0 and actual_mem_offset == 0:
                                            logger.warning("Watchdog: GPU Driver Reset (TDR) detected! Reverting all overclocking settings to protect the system.")
                                            GpuOverclockService.restore_defaults()
                                            
                                            # Reset config settings to default to prevent boot loop crashes
                                            app_settings.set_value("gpu_core_offset", 0)
                                            app_settings.set_value("gpu_mem_offset", 0)
                                            app_settings.set_value("gpu_power_limit", 100.0)
                                            app_settings.set_value("gpu_temp_limit", 83)
                                            app_settings.set_value("gpu_voltage", 0)
                                            app_settings.set_value("gpu_apply_on_startup", False)
                                            
                                        # 2. Thermal Protection Throttling
                                        else:
                                            # Throttling Trigger (Core > 84°C or VRAM > 95°C)
                                            if (core_temp > 84 or vram_temp > 95) and not self.gpu_throttled:
                                                logger.warning(f"Watchdog: GPU Temperature too high (Core: {core_temp}°C, VRAM: {vram_temp}°C). Activating safety throttle.")
                                                self.gpu_throttled = True
                                                GpuOverclockService.apply_overclock(
                                                    core_offset=0,
                                                    mem_offset=0,
                                                    power_limit_pct=100.0,
                                                    temp_limit=83,
                                                    voltage_pct=0
                                                )
                                                try:
                                                    GpuSmiService.lock_gpu_clocks(False)
                                                except Exception:
                                                    pass
                                                    
                                            # Recovery Trigger (Core < 75°C and VRAM < 85°C)
                                            elif self.gpu_throttled and core_temp < 75 and vram_temp < 85:
                                                logger.info(f"Watchdog: GPU Temperature normalized (Core: {core_temp}°C, VRAM: {vram_temp}°C).")
                                                self.gpu_throttled = False
                                                if GpuOverclockService.overclock_applied:
                                                    logger.info("Restoring user-applied manual overclock settings.")
                                                    core_offset = app_settings.get_int("gpu_core_offset", 0)
                                                    mem_offset = app_settings.get_int("gpu_mem_offset", 0)
                                                    power_limit = app_settings.get_float("gpu_power_limit", 100.0)
                                                    temp_limit = app_settings.get_int("gpu_temp_limit", 83)
                                                    voltage = app_settings.get_int("gpu_voltage", 0)
                                                    
                                                    GpuOverclockService.apply_overclock(
                                                        core_offset=core_offset,
                                                        mem_offset=mem_offset,
                                                        power_limit_pct=power_limit,
                                                        temp_limit=temp_limit,
                                                        voltage_pct=voltage
                                                    )
                                except Exception as gpu_err:
                                    logger.debug(f"Watchdog: Error in GPU dynamic monitor: {gpu_err}")

                            # Delay before next polling cycle
                            self.safe_sleep(2)
                except Exception as loop_err:
                    logger.error(f"Error in Watchdog loop: {str(loop_err)}")
                    self.safe_sleep(2)
        finally:
            # Clean up COM references and watcher before CoUninitialize
            watcher = None
            c = None
            if com_initialized:
                try:
                    import pythoncom
                    pythoncom.CoUninitialize()
                except Exception:
                    pass

    def apply_game_mode_optimizations(self, game_pid: int):
        """
        Applies gaming-specific low-level tweaks.
        """
        logger.info("Applying dynamic Game Mode overrides...")
        
        # Automatic GPU Overclock and GPU clock locking triggers removed to ensure hardware safety.
        # Overclocking and clock locking can only be adjusted manually by the user via the UI.
        pass

        # 2. NVIDIA DRS settings
        if self.settings_dict.get("enable_nvidia_drs", True):
            NvidiaDrsService.apply_gaming_drs_profile(True)

        # 3. IRQ Interrupt Affinity Separation
        if self.settings_dict.get("enable_irq_separation", True):
            IrqAffinityService.apply_separated_irq_affinity(True)

        # 4. Flush VRAM cache initially
        GpuSmiService.optimize_vram()

        # 5. Demote platform background clients (Steam/WeGame/Epic)
        if self.settings_dict.get("enable_client_priority_demote", True):
            try:
                self.tweak_background_clients_priority(True)
            except Exception as e:
                logger.warning(f"Failed to demote background clients: {e}")

    def revert_game_mode_optimizations(self):
        """
        Reverts low-level tweaks back to system defaults.
        """
        logger.info("Reverting Game Mode overrides to system defaults...")
        
        # 1. Unlock GPU Core Clocks
        try:
            GpuSmiService.lock_gpu_clocks(False)
        except Exception:
            pass

        # 2. Revert NVIDIA DRS settings
        try:
            NvidiaDrsService.apply_gaming_drs_profile(False)
        except Exception:
            pass

        # 3. Revert IRQ Interrupt Affinity Separation
        try:
            IrqAffinityService.apply_separated_irq_affinity(False)
        except Exception:
            pass

        # 4. Revert IFEO CPU Priority Class Hijacking
        try:
            from core_commander.core.system_tweaks import SystemTweaksService
            SystemTweaksService.apply_ifeo_priority(self.target_exe, False)
        except Exception as ifeo_err:
            logger.warning(f"Failed to revert IFEO priority: {ifeo_err}")

        # Remove active game mode token file
        try:
            import tempfile
            tmp_file = os.path.join(tempfile.gettempdir(), "core_commander_game_mode.tmp")
            if os.path.exists(tmp_file):
                os.remove(tmp_file)
                logger.info("Cleared Game Mode active token file.")
        except Exception as token_err:
            logger.warning(f"Failed to remove game mode token file: {token_err}")

        # 5. Restore platform background clients priority
        try:
            self.tweak_background_clients_priority(False)
        except Exception as client_err:
            logger.warning(f"Failed to restore background clients priority: {client_err}")

    def check_and_heal_wlan_autostatic(self):
        """
        Self-healing mechanism for WLAN AutoConfig.
        If no game is currently running, checks if WLAN AutoConfig is disabled,
        and automatically restores it to prevent post-crash offline issues.
        """
        if self.active_game_pid is not None:
            return
        try:
            import subprocess
            import re
            p = subprocess.Popen(["netsh", "wlan", "show", "interfaces"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
            out, _ = p.communicate(timeout=5)
            text = out.decode("gbk", errors="ignore")
            
            is_disabled = False
            for line in text.splitlines():
                line = line.strip()
                if re.search(r"(?:Auto config|自动配置)\s*:\s*(?:No|否)", line, re.IGNORECASE):
                    is_disabled = True
                    break
            
            if is_disabled:
                logger.warning("WLAN AutoConfig was found in DISABLED state with no games running. Triggering self-healing recovery...")
                subprocess.run(["netsh", "wlan", "set", "autoconfig", "enabled=yes", "interface=*"], capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
                logger.info("Self-healing: WLAN AutoConfig has been restored successfully.")
        except Exception as e:
            logger.error(f"Failed to check or heal WLAN AutoConfig state: {str(e)}")

    def tweak_background_clients_priority(self, enable: bool):
        """
        One-time Win32 snapshot search to lower priority class of game client helpers (Steam, Epic, WeGame)
        to BELOW_NORMAL_PRIORITY_CLASS to prevent CPU starvation on main gaming cores,
        while avoiding game overlay stalls. Restores back to NORMAL_PRIORITY_CLASS on exit.
        """
        targets = [
            "steamwebhelper.exe", "epicwebhelper.exe", "wegameactive.exe",
            "battle.net.exe", "riotclientux.exe", "crossiesvc.exe",
            "qbrowser.exe", "galaxyclient.exe"
        ]
        
        BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
        NORMAL_PRIORITY_CLASS = 0x00000020
        PROCESS_SET_INFORMATION = 0x0200
        
        target_priority = BELOW_NORMAL_PRIORITY_CLASS if enable else NORMAL_PRIORITY_CLASS
        action_name = "Lowering" if enable else "Restoring"
        
        import os
        if os.name != 'nt':
            return
            
        import ctypes
        from ctypes import wintypes
        
        TH32CS_SNAPPROCESS = 0x00000002
        MAX_PATH = 260

        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ('dwSize', wintypes.DWORD),
                ('cntUsage', wintypes.DWORD),
                ('th32ProcessID', wintypes.DWORD),
                ('th32DefaultHeapID', ctypes.c_void_p),
                ('th32ModuleID', wintypes.DWORD),
                ('cntThreads', wintypes.DWORD),
                ('th32ParentProcessID', wintypes.DWORD),
                ('pcPriClassBase', wintypes.LONG),
                ('dwFlags', wintypes.DWORD),
                ('szExeFile', ctypes.c_char * MAX_PATH)
            ]

        CreateToolhelp32Snapshot = ctypes.windll.kernel32.CreateToolhelp32Snapshot
        CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        CreateToolhelp32Snapshot.restype = wintypes.HANDLE

        Process32First = ctypes.windll.kernel32.Process32First
        Process32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
        Process32First.restype = wintypes.BOOL

        Process32Next = ctypes.windll.kernel32.Process32Next
        Process32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
        Process32Next.restype = wintypes.BOOL

        CloseHandle = ctypes.windll.kernel32.CloseHandle
        CloseHandle.argtypes = [wintypes.HANDLE]
        CloseHandle.restype = wintypes.BOOL

        OpenProcess = ctypes.windll.kernel32.OpenProcess
        OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        OpenProcess.restype = wintypes.HANDLE

        SetPriorityClass = ctypes.windll.kernel32.SetPriorityClass
        SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        SetPriorityClass.restype = wintypes.BOOL

        h_snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if h_snapshot == wintypes.HANDLE(-1).value or not h_snapshot:
            return

        try:
            pe = PROCESSENTRY32()
            pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
            if Process32First(h_snapshot, ctypes.byref(pe)):
                while True:
                    exe_name = pe.szExeFile.decode('ansi', errors='ignore').lower()
                    if exe_name in targets:
                        pid = pe.th32ProcessID
                        h_proc = OpenProcess(PROCESS_SET_INFORMATION, False, pid)
                        if h_proc:
                            try:
                                if SetPriorityClass(h_proc, target_priority):
                                    logger.info(f"{action_name} priority of {exe_name} (PID: {pid}) to {target_priority:#x}")
                                else:
                                    logger.debug(f"Failed to set priority class for PID {pid}")
                            finally:
                                CloseHandle(h_proc)
                    if not Process32Next(h_snapshot, ctypes.byref(pe)):
                        break
        except Exception as e:
            logger.debug(f"Error in tweak_background_clients_priority: {e}")
        finally:
            CloseHandle(h_snapshot)
