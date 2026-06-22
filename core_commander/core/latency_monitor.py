# -*- coding: utf-8 -*-
import time
import os
import ctypes
from ctypes import wintypes
import pythoncom
import win32com.client
from core_commander.utils.logger import logger

# DWM Composition Timing Info Struct
class DWM_TIMING_INFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rateComposition", ctypes.c_int64), # composition rate
        ("qpcComposition", ctypes.c_int64),
        ("cFrame", ctypes.c_uint64),
        ("cCompleted", ctypes.c_uint64),
        ("cRefreshes", ctypes.c_uint64),
        ("cActiveFrame", ctypes.c_uint64),
        ("cFrameFrame", ctypes.c_uint64),
        ("cRefreshFrame", ctypes.c_uint64),
        ("cFramePresented", ctypes.c_uint64),
        ("cRefreshPresented", ctypes.c_uint64),
        ("cFrameConfirmed", ctypes.c_uint64),
        ("cRefreshConfirmed", ctypes.c_uint64),
        ("cFrameFlushed", ctypes.c_uint64),
        ("cRefreshFlushed", ctypes.c_uint64),
        ("cFrameTracker", ctypes.c_uint64),
        ("cRefreshTracker", ctypes.c_uint64),
        ("cOverallRefreshRate", ctypes.c_int64), # overall refresh rate
        ("cRefreshesPerFrame", ctypes.c_uint32),
        ("qpcFrameRange", ctypes.c_uint64),
        ("qpcFrameAtPresent", ctypes.c_uint64),
        ("cFramePresentTarget", ctypes.c_uint64),
        ("cRefreshPresentTarget", ctypes.c_uint64),
        ("cFrameRefreshSubmit", ctypes.c_uint64),
        ("cRefreshRefreshSubmit", ctypes.c_uint64),
        ("cFrameRefreshComplete", ctypes.c_uint64),
        ("cRefreshRefreshComplete", ctypes.c_uint64),
        ("qpcFramePending", ctypes.c_uint64),
        ("cFramePending", ctypes.c_uint64),
        ("qpcFrameComplete", ctypes.c_uint64),
        ("cFrameComplete", ctypes.c_uint64),
        ("qpcFrameSubmit", ctypes.c_uint64),
        ("cFrameSubmit", ctypes.c_uint64),
        ("qpcFrameConfirmed", ctypes.c_uint64),
        ("cFrameConfirmed", ctypes.c_uint64),
        ("qpcFrameSubmitTarget", ctypes.c_uint64),
        ("cFrameSubmitTarget", ctypes.c_uint64),
        ("qpcFrameSubmitTargetComplete", ctypes.c_uint64),
        ("cFrameSubmitTargetComplete", ctypes.c_uint64),
        ("qpcFramePendingSubmit", ctypes.c_uint64),
        ("cFramePendingSubmit", ctypes.c_uint64),
        ("qpcFrameCompleteTarget", ctypes.c_uint64),
        ("cFrameCompleteTarget", ctypes.c_uint64),
        ("qpcFramePendingTarget", ctypes.c_uint64),
        ("cFramePendingTarget", ctypes.c_uint64),
        ("qpcFramePresentTargetComplete", ctypes.c_uint64),
        ("cFramePresentTargetComplete", ctypes.c_uint64),
        ("qpcFramePendingTargetComplete", ctypes.c_uint64),
        ("cFramePendingTargetComplete", ctypes.c_uint64),
        ("qpcFrameSubmitComplete", ctypes.c_uint64),
        ("cFrameSubmitComplete", ctypes.c_uint64),
        ("qpcFrameSubmitCompleteTarget", ctypes.c_uint64),
        ("cFrameSubmitCompleteTarget", ctypes.c_uint64),
        ("qpcFrameSubmitCompleteTargetComplete", ctypes.c_uint64),
        ("cFrameSubmitCompleteTargetComplete", ctypes.c_uint64),
        ("qpcFramePresentedTargetComplete", ctypes.c_uint64),
        ("cFramePresentedTargetComplete", ctypes.c_uint64),
        ("qpcFramePresentedTargetCompleteComplete", ctypes.c_uint64),
        ("cFramePresentedTargetCompleteComplete", ctypes.c_uint64),
    ]

class LatencyMonitorService:
    """
    Diagnostics service to monitor system DPC latencies, CPU interrupt rates,
    and DWM composition frame times to diagnose micro-stuttering.
    """

    @staticmethod
    def measure_dpc_stutter(samples: int = 100, interval_ms: float = 1.0) -> float:
        """
        Estimates real-time micro-stuttering by checking high-precision sleep timing deviation.
        Returns the maximum stutter spike in milliseconds.
        """
        max_stutter_ms = 0.0
        target_ns = interval_ms * 1_000_000.0
        
        has_timer_override = False
        try:
            winmm = ctypes.windll.winmm
            winmm.timeBeginPeriod(1)
            has_timer_override = True
        except Exception:
            pass

        try:
            for _ in range(samples):
                start = time.perf_counter_ns()
                # High-precision sleep (using ctypes or time.sleep if OS timer resolution is 0.5ms)
                time.sleep(interval_ms / 1000.0)
                end = time.perf_counter_ns()
                
                elapsed_ns = end - start
                deviation_ms = max(0.0, (elapsed_ns - target_ns) / 1_000_000.0)
                if deviation_ms > max_stutter_ms:
                    max_stutter_ms = deviation_ms
        finally:
            if has_timer_override:
                try:
                    winmm.timeEndPeriod(1)
                except Exception:
                    pass

        return max_stutter_ms

    @staticmethod
    def query_interrupt_performance() -> dict:
        """
        Queries CPU performance counters via NtQuerySystemInformation to obtain DPC and Interrupt time percentages.
        Corrects DPC/Interrupt rate skew by utilizing high-precision time.perf_counter() for exact elapsed time.
        """
        results = {"dpc_percent": 0.0, "interrupt_percent": 0.0, "dpc_rate": 0}
        
        class SYSTEM_PROCESSOR_PERFORMANCE_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("IdleTime", ctypes.c_int64),
                ("KernelTime", ctypes.c_int64),
                ("UserTime", ctypes.c_int64),
                ("DpcTime", ctypes.c_int64),
                ("InterruptTime", ctypes.c_int64),
                ("InterruptCount", ctypes.c_uint32),
            ]
            
        num_cpus = os.cpu_count() or 1
        info_array_1 = (SYSTEM_PROCESSOR_PERFORMANCE_INFORMATION * num_cpus)()
        info_array_2 = (SYSTEM_PROCESSOR_PERFORMANCE_INFORMATION * num_cpus)()
        
        try:
            ntdll = ctypes.windll.ntdll
            
            # Snapshot 1 and start tracking time
            t1 = time.perf_counter()
            status1 = ntdll.NtQuerySystemInformation(8, ctypes.byref(info_array_1), ctypes.sizeof(info_array_1), None)
            if status1 != 0:
                return results
                
            # Quick sleep using high precision
            try:
                winmm = ctypes.windll.winmm
                winmm.timeBeginPeriod(1)
                time.sleep(0.015)
                winmm.timeEndPeriod(1)
            except Exception:
                time.sleep(0.015)
                
            # Snapshot 2 and end tracking time
            status2 = ntdll.NtQuerySystemInformation(8, ctypes.byref(info_array_2), ctypes.sizeof(info_array_2), None)
            t2 = time.perf_counter()
            
            elapsed = t2 - t1
            if elapsed <= 0.0:
                elapsed = 0.015  # Avoid division by zero or negative time
                
            total_dpc = 0
            total_interrupt = 0
            total_time = 0
            total_interrupt_count = 0
            
            for i in range(num_cpus):
                dpc_delta = info_array_2[i].DpcTime - info_array_1[i].DpcTime
                int_delta = info_array_2[i].InterruptTime - info_array_1[i].InterruptTime
                kernel_delta = info_array_2[i].KernelTime - info_array_1[i].KernelTime
                user_delta = info_array_2[i].UserTime - info_array_1[i].UserTime
                int_count_delta = info_array_2[i].InterruptCount - info_array_1[i].InterruptCount
                
                cpu_total = kernel_delta + user_delta
                if cpu_total > 0:
                    total_dpc += dpc_delta
                    total_interrupt += int_delta
                    total_time += cpu_total
                    total_interrupt_count += int_count_delta
                    
            if total_time > 0:
                results["dpc_percent"] = min(100.0, max(0.0, (total_dpc / total_time) * 100.0))
                results["interrupt_percent"] = min(100.0, max(0.0, (total_interrupt / total_time) * 100.0))
                results["dpc_rate"] = int(total_interrupt_count / elapsed)
        except Exception as e:
            logger.debug(f"Failed to query NtQuerySystemInformation: {str(e)}")
            
        return results

    @staticmethod
    def check_problematic_drivers() -> list:
        """
        Scans active system drivers and flags those known to cause high DPC latency.
        """
        problematic = []
        drivers_to_check = {
            "nvlddmkm.sys": "NVIDIA Graphics Kernel Driver (Possible conflict / DPC overload)",
            "amdkmdap.sys": "AMD Graphics Kernel Driver (Possible conflict / DPC overload)",
            "ndis.sys": "Network Driver Interface Specification (Possible network adapter interrupt throttling)",
            "tcpip.sys": "TCP/IP Protocol Driver (High network throughput packet handling delay)",
            "wdf01000.sys": "Windows Driver Framework (Generic system DPC/ISR wrapper latency)",
            "dxgkrnl.sys": "DirectX Graphics Kernel (DirectX presentation queue congestion)"
        }
        
        loaded_drivers = set()
        try:
            psapi = ctypes.windll.psapi
            needed = ctypes.c_ulong(0)
            psapi.EnumDeviceDrivers(None, 0, ctypes.byref(needed))
            
            if needed.value > 0:
                array_size = needed.value // ctypes.sizeof(ctypes.c_void_p)
                drivers_array = (ctypes.c_void_p * array_size)()
                if psapi.EnumDeviceDrivers(ctypes.byref(drivers_array), needed.value, ctypes.byref(needed)):
                    for base_addr in drivers_array:
                        if not base_addr:
                            continue
                        name_buf = ctypes.create_unicode_buffer(260)
                        if psapi.GetDeviceDriverBaseNameW(base_addr, name_buf, 260) > 0:
                            loaded_drivers.add(name_buf.value.lower())
        except Exception as e:
            logger.debug(f"EnumDeviceDrivers failed: {e}")
            
        # Check system32/drivers folder
        sys_dir = os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32", "drivers")
        for drv_file, desc in drivers_to_check.items():
            if drv_file in loaded_drivers:
                drv_path = os.path.join(sys_dir, drv_file)
                problematic.append({
                    "name": drv_file,
                    "desc": desc,
                    "path": drv_path if os.path.exists(drv_path) else "Kernel Memory"
                })
        return problematic

    @staticmethod
    def get_dwm_frametime_stats() -> dict:
        """
        Queries DwmGetCompositionTimingInfo to compute composition refresh rate and frame time jitter.
        """
        stats = {"refresh_rate": 60.0, "jitter_ms": 0.0}
        try:
            dwmapi = ctypes.windll.dwmapi
            info = DWM_TIMING_INFO()
            info.cbSize = ctypes.sizeof(DWM_TIMING_INFO)
            
            ret = dwmapi.DwmGetCompositionTimingInfo(0, ctypes.byref(info))
            if ret == 0:
                # rateComposition.rateNumerator / rateComposition.rateDenominator
                # rates are represented as fractions in some SDKs, or directly in rateComposition
                if info.cOverallRefreshRate > 0:
                    stats["refresh_rate"] = float(info.cOverallRefreshRate) / 1000.0 # standard mapping
                else:
                    stats["refresh_rate"] = 60.0
                
                # Check frame completed refreshes jitter
                stats["jitter_ms"] = float(info.qpcFrameRange) / 10000.0 if info.qpcFrameRange > 0 else 0.0
        except Exception as e:
            logger.debug(f"Failed to query DWM timing info: {str(e)}")
        return stats

    @classmethod
    def run_diagnostics(cls) -> dict:
        """
        Runs a full suite of DPC latency, CPU interrupt, and DWM composition frame diagnostics.
        """
        stutter = cls.measure_dpc_stutter()
        perf = cls.query_interrupt_performance()
        drivers = cls.check_problematic_drivers()
        dwm = cls.get_dwm_frametime_stats()

        diag_report = {
            "dpc_stutter_ms": stutter,
            "dpc_percent": perf["dpc_percent"],
            "interrupt_percent": perf["interrupt_percent"],
            "dpc_rate_per_sec": perf["dpc_rate"],
            "dwm_refresh_rate": dwm["refresh_rate"],
            "dwm_jitter_ms": dwm["jitter_ms"],
            "flagged_drivers": drivers,
            "status": "Healthy"
        }

        # Threshold evaluation
        if stutter > 1.5 or perf["dpc_percent"] > 5.0:
            diag_report["status"] = "Warning (High DPC Latency detected - Micro-stuttering likely)"
        elif stutter > 3.0 or perf["dpc_percent"] > 10.0:
            diag_report["status"] = "Critical (Severe DPC Latency - Extreme frame dropping)"

        logger.info(f"System diagnostics complete. Status: {diag_report['status']}. DPC Stutter: {stutter:.2f}ms")
        return diag_report
