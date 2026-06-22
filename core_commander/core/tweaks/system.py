# -*- coding: utf-8 -*-
import os
import sys
import winreg
import ctypes
import subprocess
from typing import Union

from core_commander.utils.logger import logger
from core_commander.core.system_tweaks import (
    SystemTweaksService,
    SERVICE_DISABLED,
    SERVICE_DEMAND_START,
    SERVICE_AUTO_START
)
from core_commander.core.irq_aff import IrqAffinityService
from core_commander.core.tweaks.base import BaseTweak, TweakRegistry

@TweakRegistry.register
class DisableWindowsVisualEffects(BaseTweak):
    @property
    def id(self) -> str:
        return "disable_windows_visual_effects"

    def apply(self, disable: bool) -> bool:
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
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "VisualFXSetting", 0, winreg.REG_DWORD, 3)
                
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path_metrics, 0, winreg.KEY_WRITE) as key_metrics:
                    winreg.SetValueEx(key_metrics, "MinAnimate", 0, winreg.REG_SZ, "0")
                
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path_adv, 0, winreg.KEY_WRITE) as key_adv:
                    winreg.SetValueEx(key_adv, "TaskbarAnimations", 0, winreg.REG_DWORD, 0)
                    winreg.SetValueEx(key_adv, "IconsOnly", 0, winreg.REG_DWORD, 0)
                    winreg.SetValueEx(key_adv, "ListviewShadow", 0, winreg.REG_DWORD, 1)
                
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Control Panel\Desktop", 0, winreg.KEY_WRITE) as key_desktop:
                    winreg.SetValueEx(key_desktop, "DragFullWindows", 0, winreg.REG_SZ, "1")
                    winreg.SetValueEx(key_desktop, "FontSmoothing", 0, winreg.REG_SZ, "2")
                    winreg.SetValueEx(key_desktop, "FontSmoothingType", 0, winreg.REG_DWORD, 2)
                
                    mask_perf = bytes.fromhex("9012038010000000")
                    winreg.SetValueEx(key_desktop, "UserPreferencesMask", 0, winreg.REG_BINARY, mask_perf)
                
                logger.info("Windows visual effects reduced (aligned to Custom preset).")
            else:
                SystemTweaksService.restore_registry_value_or_default("HKCU", path, "VisualFXSetting", 0, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKCU", path_metrics, "MinAnimate", "1", winreg.REG_SZ)
                SystemTweaksService.restore_registry_value_or_default("HKCU", path_adv, "TaskbarAnimations", 1, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKCU", path_adv, "IconsOnly", 0, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKCU", path_adv, "ListviewShadow", 1, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKCU", "Control Panel\Desktop", "DragFullWindows", "1", winreg.REG_SZ)
                SystemTweaksService.restore_registry_value_or_default("HKCU", "Control Panel\Desktop", "FontSmoothing", "2", winreg.REG_SZ)
                SystemTweaksService.restore_registry_value_or_default("HKCU", "Control Panel\Desktop", "FontSmoothingType", 2, winreg.REG_DWORD)
                
                mask_default = bytes.fromhex("9e1e078012000000")
                SystemTweaksService.restore_registry_value_or_default("HKCU", "Control Panel\Desktop", "UserPreferencesMask", mask_default, winreg.REG_BINARY)
                
                logger.info("Windows visual effects restored to default.")
            return True
        except Exception as e:
            logger.error(f"Failed to apply/restore visual effects tweak: {str(e)}")
            return False

@TweakRegistry.register
class DisableWindowsTransparency(BaseTweak):
    @property
    def id(self) -> str:
        return "disable_windows_transparency"

    def apply(self, disable: bool) -> bool:
        path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        value_name = "EnableTransparency"
        SystemTweaksService.backup_registry_value("HKCU", path, value_name)
        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_WRITE) as key:
                val = 0 if disable else 1
                winreg.SetValueEx(key, value_name, 0, winreg.REG_DWORD, val)
            logger.info(f"Windows transparency set to: {'Disabled' if disable else 'Enabled'}.")
            return True
        except Exception as e:
            logger.error(f"Failed to apply transparency tweak: {str(e)}")
            return False

@TweakRegistry.register
class Win32PrioSep(BaseTweak):
    @property
    def id(self) -> str:
        return "win32_prio_sep"

    def apply(self, val: int) -> bool:
        path = r"SYSTEM\CurrentControlSet\Control\PriorityControl"
        value_name = "Win32PrioritySeparation"
        
        SystemTweaksService.backup_registry_value("HKLM", path, value_name)
        
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, value_name, 0, winreg.REG_DWORD, val)
            logger.info(f"Applied Win32PrioritySeparation: {val}")
            
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
            return True
        except Exception as e:
            logger.error(f"Failed applying Win32PrioritySeparation: {str(e)}")
            return False

@TweakRegistry.register
class DisableHpet(BaseTweak):
    @property
    def id(self) -> str:
        return "disable_hpet"

    def apply(self, disable: bool) -> bool:
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
                with SystemStateScannerWorker._cache_lock:
                    SystemStateScannerWorker._hpet_cache = None
            except Exception:
                pass
            return True
        except Exception as e:
            logger.error(f"Failed applying HPET/Ticks: {str(e)}")
            return False

@TweakRegistry.register
class EnableDwmTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_dwm_tweak"

    def apply(self, enable: bool) -> bool:
        path = r"SOFTWARE\Microsoft\Windows\DWM"
        if not enable:
            SystemTweaksService.restore_registry_value_or_default("HKLM", path, "FrameLatency", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path, "MaxQueuedPresentBuffers", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path, "ForceDirectDrawSync", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path, "OverlayTestMode", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKCU", path, "FrameLatency", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKCU", path, "MaxQueuedPresentBuffers", None, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKCU", path, "ForceDirectDrawSync", None, winreg.REG_DWORD)
            logger.info("Restored DWM low latency parameters and MPO in registry.")
            return True
            
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
                winreg.SetValueEx(key, "FrameLatency", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "MaxQueuedPresentBuffers", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "ForceDirectDrawSync", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "OverlayTestMode", 0, winreg.REG_DWORD, 5)
        except Exception as e:
            logger.error(f"Failed to apply HKLM DWM tweaks: {str(e)}")
            return False
            
        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "FrameLatency", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "MaxQueuedPresentBuffers", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "ForceDirectDrawSync", 0, winreg.REG_DWORD, 0)
            logger.info("Applied DWM latency parameters and disabled MPO in registry.")
            return True
        except Exception as e:
            logger.error(f"Failed to apply HKCU DWM tweaks: {str(e)}")
            return False

@TweakRegistry.register
class EnableDpcLatencyTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_dpc_latency_tweak"

    def apply(self, enable: bool) -> bool:
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
                      "DefaultLatencyToleranceTimerPeriod", "DefaultMemoryRefreshToleranceActivelyUsed", 
                      "DefaultMemoryRefreshLatencyToleranceMonitorOff", "DefaultMemoryRefreshLatencyToleranceNoContext", 
                      "Latency", "MaxIAverageGraphicsLatencyInOneBucket", "MiracastPerfTrackGraphicsLatency", 
                      "MonitorLatencyTolerance", "MonitorRefreshLatencyTolerance", "TransitionLatency"]:
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_gdp, v, None, winreg.REG_DWORD)
            return True
            
        logger.info("Applying DPC kernel and power tolerance latencies...")
        for v in ["IdealDpcRate", "ThreadDpcEnable"]:
            SystemTweaksService.backup_registry_value("HKLM", path_smk, v)
        try:
            with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_smk, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "IdealDpcRate", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "ThreadDpcEnable", 0, winreg.REG_DWORD, 1)
        except Exception as e:
            logger.debug(f"DPC SM kernel write failed: {str(e)}")
            return False

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
            return False

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
            return True
        except Exception as e:
            logger.debug(f"GraphicsDrivers Power write failed: {str(e)}")
            return False

@TweakRegistry.register
class KeyboardRepeatDelayLevel(BaseTweak):
    @property
    def id(self) -> str:
        return "keyboard_repeat_delay_level"

    def apply(self, level: int) -> bool:
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
            except Exception:
                pass
            return True
            
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
            return True
        except Exception as e:
            logger.error(f"Failed to execute keyrate: {str(e)}")
            return False

@TweakRegistry.register
class EnableUsbLowLatencyTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_usb_low_latency_tweak"

    def apply(self, enable: bool) -> bool:
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
            return True
            
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
            with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_usb, 0, winreg.KEY_WRITE) as key:
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
            return True
        except Exception as e:
            logger.debug(f"usbccgp write failed: {str(e)}")
            return False

@TweakRegistry.register
class EnableUsbImodTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_usb_imod_tweak"

    def apply(self, enable: bool) -> bool:
        path = r"SYSTEM\CurrentControlSet\Services\USBXHCI\Parameters"
        
        if not enable:
            SystemTweaksService.restore_registry_value_or_default("HKLM", path, "InterruptModeration", None, winreg.REG_DWORD)
            return True
        else:
            logger.info("Applying USB interrupt moderation tweak...")
            SystemTweaksService.backup_registry_value("HKLM", path, "InterruptModeration")
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "InterruptModeration", 0, winreg.REG_DWORD, 0)
                return True
            except Exception as e:
                logger.error(f"Failed to apply USB imod tweak: {str(e)}")
                return False

@TweakRegistry.register
class EnableMouseLatencyTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_mouse_latency_tweak"

    def apply(self, enable: bool) -> bool:
        path = r"Control Panel\Mouse"
        if not enable:
            SystemTweaksService.restore_registry_value_or_default("HKCU", path, "MouseSensitivity", "10", winreg.REG_SZ)
            SystemTweaksService.restore_registry_value_or_default("HKCU", path, "MouseSpeed", "1", winreg.REG_SZ)
            SystemTweaksService.restore_registry_value_or_default("HKCU", path, "MouseThreshold1", "6", winreg.REG_SZ)
            SystemTweaksService.restore_registry_value_or_default("HKCU", path, "MouseThreshold2", "10", winreg.REG_SZ)
            for curve in ["SmoothMouseXCurve", "SmoothMouseYCurve"]:
                SystemTweaksService.restore_registry_value_or_default("HKCU", path, curve, None, winreg.REG_BINARY)
            return True
            
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
            return True
        except Exception as e:
            logger.error(f"Failed to apply mouse delay tweaks: {str(e)}")
            return False

@TweakRegistry.register
class EnableDwmSuperWetTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_dwm_super_wet_tweak"

    def apply(self, enable: bool) -> bool:
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
            logger.info("DWM super wet tweaks restored in registry.")
            return True
            
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
            logger.info("DWM super wet tweaks applied to registry.")
        except Exception as e:
            logger.error(f"Failed to apply DWM super wet tweaks: {str(e)}")
            return False

        SystemTweaksService.backup_registry_value("HKLM", path_mm, "SessionPoolSize")
        SystemTweaksService.backup_registry_value("HKLM", path_mm, "SessionViewSize")
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_mm, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "SessionPoolSize", 0, winreg.REG_DWORD, 6)
                winreg.SetValueEx(key, "SessionViewSize", 0, winreg.REG_DWORD, 0x48)
            return True
        except Exception as e:
            logger.debug(f"Session memory pool sizes write failed: {str(e)}")
            return False

@TweakRegistry.register
class EnableDirectxTweaks(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_directx_tweaks"

    def apply(self, enable: bool) -> bool:
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
            return True
            
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
                return True
            except Exception as e:
                logger.debug(f"Direct3D {d3d_glob} write failed: {str(e)}")
                return False

@TweakRegistry.register
class DisableUselessServices(BaseTweak):
    @property
    def id(self) -> str:
        return "disable_useless_services"

    def apply(self, enable: bool) -> bool:
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
        try:
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
                for svc in ["Sysmain", "DPS", "DiagTrack"]:
                    try:
                        SystemTweaksService.safe_subprocess_call(["sc.exe", "start", svc], timeout=2)
                    except Exception:
                        pass
                return True
                
            logger.info("Disabling unnecessary services and telemetry...")
            for svc in services_to_disable:
                SystemTweaksService.backup_service(svc)
                SystemTweaksService.set_service_start_type(svc, SERVICE_DISABLED)
                try:
                    SystemTweaksService.safe_subprocess_call(["sc.exe", "stop", svc], timeout=2)
                except Exception:
                    pass
                try:
                    SystemTweaksService.safe_subprocess_call(["sc.exe", "triggerinfo", svc, "delete"], timeout=2)
                except Exception:
                    pass
                try:
                    SystemTweaksService.safe_subprocess_call(["sc.exe", "failure", svc, "reset=", "0", "actions= "], timeout=2)
                except Exception:
                    pass
            return True
        except Exception as e:
            logger.error(f"Failed to apply disable_useless_services tweak: {str(e)}")
            return False

@TweakRegistry.register
class DisableWsearchTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "disable_wsearch_tweak"

    def apply(self, enable: bool) -> bool:
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

@TweakRegistry.register
class DisableSpectreMeltdown(BaseTweak):
    @property
    def id(self) -> str:
        return "disable_spectre_meltdown"

    def apply(self, disable: bool) -> bool:
        path_mm = r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management"
        try:
            if not disable:
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_mm, "FeatureSettings", None, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_mm, "FeatureSettingsOverride", 0, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_mm, "FeatureSettingsOverrideMask", 3, winreg.REG_DWORD)
                return True
                
            logger.info("Disabling Meltdown & Spectre CPU mitigations for performance...")
            SystemTweaksService.backup_registry_value("HKLM", path_mm, "FeatureSettings")
            SystemTweaksService.backup_registry_value("HKLM", path_mm, "FeatureSettingsOverride")
            SystemTweaksService.backup_registry_value("HKLM", path_mm, "FeatureSettingsOverrideMask")
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_mm, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "FeatureSettings", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "FeatureSettingsOverride", 0, winreg.REG_DWORD, 3)
                winreg.SetValueEx(key, "FeatureSettingsOverrideMask", 0, winreg.REG_DWORD, 3)
            logger.info("CPU vulnerabilities protection disabled successfully.")
            return True
        except Exception as e:
            logger.error(f"Failed to disable CPU vulnerabilities: {str(e)}")
            return False

@TweakRegistry.register
class DisableGamedvr(BaseTweak):
    @property
    def id(self) -> str:
        return "disable_gamedvr"

    def apply(self, disable: bool) -> bool:
        path1 = r"System\GameConfigStore"
        path2 = r"SOFTWARE\Microsoft\PolicyManager\default\ApplicationManagement\AllowGameDVR"
        path3 = r"Software\Microsoft\Windows\CurrentVersion\GameDVR"
        path4 = r"SOFTWARE\Policies\Microsoft\Windows\GameDVR"
        
        try:
            if not disable:
                SystemTweaksService.restore_registry_value_or_default("HKCU", path1, "GameDVR_Enabled", 1, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKCU", path1, "GameDVR_FSEBehaviorMode", 2, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKCU", path1, "GameDVR_HonorUserFSEBehaviorMode", 0, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKCU", path1, "GameDVR_DXGIHonorFSEWindowsCompatible", 0, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKCU", path1, "GameDVR_EFSEFeatureFlags", 0, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKCU", path1, "GameDVR_FSEBehavior", 0, winreg.REG_DWORD)
                
                SystemTweaksService.restore_registry_value_or_default("HKLM", path2, "value", 1, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path2, "MergeConflictOptions", 0, winreg.REG_DWORD)
                
                SystemTweaksService.restore_registry_value_or_default("HKCU", path3, "AppCaptureEnabled", 1, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKCU", path3, "HistoricalCaptureEnabled", 0, winreg.REG_DWORD)
                
                SystemTweaksService.restore_registry_value_or_default("HKLM", path4, "AllowGameDVR", None, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path4, "AllowAudioCapture", None, winreg.REG_DWORD)
                return True
                
            logger.info("Disabling GameDVR and App Capture...")
            for v in ["GameDVR_Enabled", "GameDVR_FSEBehaviorMode", "GameDVR_HonorUserFSEBehaviorMode", 
                      "GameDVR_DXGIHonorFSEWindowsCompatible", "GameDVR_EFSEFeatureFlags", "GameDVR_FSEBehavior"]:
                SystemTweaksService.backup_registry_value("HKCU", path1, v)
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path1, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "GameDVR_Enabled", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "GameDVR_FSEBehaviorMode", 0, winreg.REG_DWORD, 3)
                winreg.SetValueEx(key, "GameDVR_HonorUserFSEBehaviorMode", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "GameDVR_DXGIHonorFSEWindowsCompatible", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "GameDVR_EFSEFeatureFlags", 0, winreg.REG_DWORD, 3)
                winreg.SetValueEx(key, "GameDVR_FSEBehavior", 0, winreg.REG_DWORD, 2)

            SystemTweaksService.backup_registry_value("HKLM", path2, "value")
            SystemTweaksService.backup_registry_value("HKLM", path2, "MergeConflictOptions")
            with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path2, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "value", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "MergeConflictOptions", 0, winreg.REG_DWORD, 1)

            SystemTweaksService.backup_registry_value("HKCU", path3, "AppCaptureEnabled")
            SystemTweaksService.backup_registry_value("HKCU", path3, "HistoricalCaptureEnabled")
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path3, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "AppCaptureEnabled", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "HistoricalCaptureEnabled", 0, winreg.REG_DWORD, 0)

            SystemTweaksService.backup_registry_value("HKLM", path4, "AllowGameDVR")
            SystemTweaksService.backup_registry_value("HKLM", path4, "AllowAudioCapture")
            with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path4, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "AllowGameDVR", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "AllowAudioCapture", 0, winreg.REG_DWORD, 0)
            return True
        except Exception as e:
            logger.error(f"Failed to disable GameDVR: {str(e)}")
            return False

@TweakRegistry.register
class EnableUacTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_uac_tweak"

    def apply(self, enable: bool) -> bool:
        path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
        if not enable:
            SystemTweaksService.restore_registry_value_or_default("HKLM", path, "EnableLUA", 1, winreg.REG_DWORD)
            return True
            
        logger.info("Disabling User Account Control (UAC)...")
        SystemTweaksService.backup_registry_value("HKLM", path, "EnableLUA")
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "EnableLUA", 0, winreg.REG_DWORD, 0)
            logger.info("UAC disabled successfully.")
            return True
        except Exception as e:
            logger.error(f"Failed to disable UAC: {str(e)}")
            return False

@TweakRegistry.register
class EnableDesktopHeapTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_desktop_heap_tweak"

    def apply(self, enable: bool) -> bool:
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
            return True
            
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
            return True
        except Exception as e:
            logger.error(f"Failed to apply Desktop Heap tweak: {str(e)}")
            return False

@TweakRegistry.register
class EnableDownloadMapsTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_download_maps_tweak"

    def apply(self, enable: bool) -> bool:
        try:
            if not enable:
                SystemTweaksService.restore_service_or_default("MapsBroker", SERVICE_AUTO_START)
                return True
                
            logger.info("Disabling MapsBroker downloaded maps manager service...")
            SystemTweaksService.backup_service("MapsBroker")
            SystemTweaksService.set_service_start_type("MapsBroker", SERVICE_DISABLED)
            return True
        except Exception as e:
            logger.error(f"Failed to apply/restore MapsBroker tweak: {str(e)}")
            return False

@TweakRegistry.register
class EnableAutoshareTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_autoshare_tweak"

    def apply(self, enable: bool) -> bool:
        path = r"SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters"
        if not enable:
            SystemTweaksService.restore_registry_value_or_default("HKLM", path, "AutoShareServer", 1, winreg.REG_DWORD)
            SystemTweaksService.restore_registry_value_or_default("HKLM", path, "AutoShareWks", 1, winreg.REG_DWORD)
            return True
            
        logger.info("Disabling administrative AutoShares...")
        SystemTweaksService.backup_registry_value("HKLM", path, "AutoShareServer")
        SystemTweaksService.backup_registry_value("HKLM", path, "AutoShareWks")
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "AutoShareServer", 0, winreg.REG_DWORD, 0)
                winreg.SetValueEx(key, "AutoShareWks", 0, winreg.REG_DWORD, 0)
            logger.info("Administrative AutoShares disabled.")
            return True
        except Exception as e:
            logger.error(f"Failed to disable AutoShare: {str(e)}")
            return False

@TweakRegistry.register
class EnableAutorunTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_autorun_tweak"

    def apply(self, enable: bool) -> bool:
        path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer"
        if not enable:
            SystemTweaksService.restore_registry_value_or_default("HKCU", path, "NoDriveTypeAutoRun", 0x91, winreg.REG_DWORD)
            return True
            
        logger.info("Disabling drive AutoRun...")
        SystemTweaksService.backup_registry_value("HKCU", path, "NoDriveTypeAutoRun")
        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "NoDriveTypeAutoRun", 0, winreg.REG_DWORD, 0xff)
            logger.info("Drive AutoRun disabled.")
            return True
        except Exception as e:
            logger.error(f"Failed to disable AutoRun: {str(e)}")
            return False

@TweakRegistry.register
class DisableCopilot(BaseTweak):
    @property
    def id(self) -> str:
        return "disable_copilot"

    def apply(self, disable: bool) -> bool:
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
                        except Exception:
                            pass
            
            explorer_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
            SystemTweaksService.backup_registry_value("HKCU", explorer_path, "ShowCopilotButton")
            try:
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, explorer_path, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "ShowCopilotButton", 0, winreg.REG_DWORD, 0 if disable else 1)
            except Exception:
                pass
            logger.info(f"Windows Copilot set to: {'Disabled' if disable else 'Enabled'}.")
            return True
        except Exception as e:
            logger.error(f"Failed to apply Copilot tweak: {str(e)}")
            return False

@TweakRegistry.register
class DisableSecurityNotifications(BaseTweak):
    @property
    def id(self) -> str:
        return "disable_security_notifications"

    def apply(self, disable: bool) -> bool:
        path_sec = r"SOFTWARE\Policies\Microsoft\Windows Defender Security Center\Notifications"
        path_toast = r"Software\Microsoft\Windows\CurrentVersion\Notifications\Settings\Windows.SystemToast.SecurityAndMaintenance"
        
        SystemTweaksService.backup_registry_value("HKLM", path_sec, "DisableNotifications")
        SystemTweaksService.backup_registry_value("HKLM", path_sec, "DisableEnhancedNotifications")
        SystemTweaksService.backup_registry_value("HKCU", path_toast, "Enabled")
        
        try:
            if disable:
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_sec, 0, winreg.KEY_WRITE) as key_sec:
                    winreg.SetValueEx(key_sec, "DisableNotifications", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(key_sec, "DisableEnhancedNotifications", 0, winreg.REG_DWORD, 1)
                
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path_toast, 0, winreg.KEY_WRITE) as key_toast:
                    winreg.SetValueEx(key_toast, "Enabled", 0, winreg.REG_DWORD, 0)
                logger.info("Windows Security and Maintenance notifications disabled.")
            else:
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_sec, "DisableNotifications", None, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_sec, "DisableEnhancedNotifications", None, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKCU", path_toast, "Enabled", 1, winreg.REG_DWORD)
                logger.info("Windows Security notifications restored to default.")
            return True
        except Exception as e:
            logger.error(f"Failed to apply security notifications tweak: {str(e)}")
            return False

@TweakRegistry.register
class DisableDefender(BaseTweak):
    @property
    def id(self) -> str:
        return "disable_defender"

    def apply(self, disable: bool) -> bool:
        path_policy = r"SOFTWARE\Policies\Microsoft\Windows Defender"
        path_rt = r"SOFTWARE\Policies\Microsoft\Windows Defender\Real-Time Protection"
        
        SystemTweaksService.backup_registry_value("HKLM", path_policy, "DisableAntiSpyware")
        SystemTweaksService.backup_registry_value("HKLM", path_policy, "DisableRealtimeMonitoring")
        SystemTweaksService.backup_registry_value("HKLM", path_rt, "DisableRealtimeMonitoring")
        
        try:
            if disable:
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_policy, 0, winreg.KEY_WRITE) as key_policy:
                    winreg.SetValueEx(key_policy, "DisableAntiSpyware", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(key_policy, "DisableRealtimeMonitoring", 0, winreg.REG_DWORD, 1)
                
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_rt, 0, winreg.KEY_WRITE) as key_rt:
                    winreg.SetValueEx(key_rt, "DisableRealtimeMonitoring", 0, winreg.REG_DWORD, 1)
                
                ps_cmds = [
                    "Set-MpPreference -DisableRealtimeMonitoring $true",
                    "Set-MpPreference -DisableBehaviorMonitoring $true",
                    "Set-MpPreference -DisableIOAVProtection $true",
                    "Set-MpPreference -SubmitSamplesConsent 2",
                    "Set-MpPreference -MAPSReporting 0"
                ]
                for cmd in ps_cmds:
                    try:
                        SystemTweaksService.safe_subprocess_call(["powershell.exe", "-Command", cmd], timeout=3)
                    except Exception as ps_err:
                        logger.warning(f"PowerShell command '{cmd}' failed: {str(ps_err)}")
                
                logger.info("Windows Defender Antivirus policies and real-time monitoring disabled.")
            else:
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_policy, "DisableAntiSpyware", None, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_policy, "DisableRealtimeMonitoring", None, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_rt, "DisableRealtimeMonitoring", None, winreg.REG_DWORD)
                
                ps_cmds = [
                    "Set-MpPreference -DisableRealtimeMonitoring $false",
                    "Set-MpPreference -DisableBehaviorMonitoring $false",
                    "Set-MpPreference -DisableIOAVProtection $false",
                    "Set-MpPreference -SubmitSamplesConsent 0",
                    "Set-MpPreference -MAPSReporting 2"
                ]
                for cmd in ps_cmds:
                    try:
                        SystemTweaksService.safe_subprocess_call(["powershell.exe", "-Command", cmd], timeout=3)
                    except Exception as ps_err:
                        logger.warning(f"PowerShell command '{cmd}' failed: {str(ps_err)}")
                        
                logger.info("Windows Defender Antivirus settings restored to default.")
            return True
        except Exception as e:
            logger.error(f"Failed to apply Windows Defender tweak: {str(e)}")
            return False

@TweakRegistry.register
class DisableSmartscreen(BaseTweak):
    @property
    def id(self) -> str:
        return "disable_smartscreen"

    def apply(self, disable: bool) -> bool:
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
                
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path_apphost, 0, winreg.REG_WRITE) as key_app:
                    winreg.SetValueEx(key_app, "EnableWebContentEvaluation", 0, winreg.REG_DWORD, 0)
                
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
            return False

@TweakRegistry.register
class DisableFirewall(BaseTweak):
    @property
    def id(self) -> str:
        return "disable_firewall"

    def apply(self, disable: bool) -> bool:
        profiles = [
            r"SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters\FirewallPolicy\StandardProfile",
            r"SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters\FirewallPolicy\PublicProfile",
            r"SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters\FirewallPolicy\DomainProfile"
        ]
        for p in profiles:
            SystemTweaksService.backup_registry_value("HKLM", p, "EnableFirewall")
            
        try:
            for p in profiles:
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, p, 0, winreg.KEY_WRITE) as key:
                    val = 0 if disable else 1
                    winreg.SetValueEx(key, "EnableFirewall", 0, winreg.REG_DWORD, val)
            
            state_str = "off" if disable else "on"
            SystemTweaksService.safe_subprocess_call(["netsh.exe", "advfirewall", "set", "allprofiles", "state", state_str])
            logger.info(f"Windows Defender Firewall successfully configured to: {'Disabled' if disable else 'Enabled'} via netsh.")
            return True
        except Exception as e:
            logger.error(f"Failed to apply Firewall tweak: {str(e)}")
            return False

@TweakRegistry.register
class EnableDriverPriorityTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_driver_priority_tweak"

    def apply(self, enable: bool) -> bool:
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
            return True

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
            return True
        except Exception as e:
            logger.error(f"Failed applying driver priority tweaks: {str(e)}")
            return False

@TweakRegistry.register
class DisableHypervVirtualization(BaseTweak):
    @property
    def id(self) -> str:
        return "disable_hyperv_virtualization"

    def apply(self, disable_hyperv: bool) -> bool:
        try:
            path_dg = r"SYSTEM\CurrentControlSet\Control\DeviceGuard"
            path_hvci = r"SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity"
            path_lsa = r"SYSTEM\CurrentControlSet\Control\Lsa"
            path_dg_policy = r"SOFTWARE\Policies\Microsoft\Windows\DeviceGuard"

            if disable_hyperv:
                SystemTweaksService.backup_registry_value("HKLM", path_dg, "EnableVirtualizationBasedSecurity")
                SystemTweaksService.backup_registry_value("HKLM", path_hvci, "Enabled")
                SystemTweaksService.backup_registry_value("HKLM", path_lsa, "LsaCfgFlags")
                SystemTweaksService.backup_registry_value("HKLM", path_dg_policy, "EnableVirtualizationBasedSecurity")
                SystemTweaksService.backup_registry_value("HKLM", path_dg_policy, "RequirePlatformSecurityFeatures")

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

                for val_name in ["EnableVirtualizationBasedSecurity", "RequirePlatformSecurityFeatures"]:
                    try:
                        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_dg_policy, 0, winreg.KEY_SET_VALUE) as key:
                            winreg.DeleteValue(key, val_name)
                    except Exception:
                        pass

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
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_dg, "EnableVirtualizationBasedSecurity", 0, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_hvci, "Enabled", 0, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_hvci, "Locked", 0, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", r"SYSTEM\CurrentControlSet\Control\CI\Config", "HVCIMCTEnabled", 0, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_lsa, "LsaCfgFlags", 0, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_dg_policy, "EnableVirtualizationBasedSecurity", 0, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_dg_policy, "RequirePlatformSecurityFeatures", 0, winreg.REG_DWORD)

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
            return True
        except Exception as e:
            logger.error(f"Failed to configure Hyper-V and boot tweaks: {str(e)}")
            return False

@TweakRegistry.register
class EnableWidgetsTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_widgets_tweak"

    def apply(self, enable: bool) -> bool:
        path_dsh = r"SOFTWARE\Policies\Microsoft\Dsh"
        path_adv = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
        try:
            if enable:
                SystemTweaksService.backup_registry_value("HKLM", path_dsh, "AllowNewsAndInterests")
                SystemTweaksService.backup_registry_value("HKCU", path_adv, "TaskbarDa")
                try:
                    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_dsh, 0, winreg.KEY_WRITE) as key:
                        winreg.SetValueEx(key, "AllowNewsAndInterests", 0, winreg.REG_DWORD, 0)
                except Exception:
                    pass
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path_adv, 0, winreg.KEY_WRITE) as key:
                        winreg.SetValueEx(key, "TaskbarDa", 0, winreg.REG_DWORD, 0)
                except Exception:
                    pass
            else:
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_dsh, "AllowNewsAndInterests", 1, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKCU", path_adv, "TaskbarDa", 1, winreg.REG_DWORD)
            return True
        except Exception as e:
            logger.error(f"Failed to apply widgets tweak: {str(e)}")
            return False

@TweakRegistry.register
class EnableStickyKeysTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_sticky_keys_tweak"

    def apply(self, enable: bool) -> bool:
        path_sticky = r"Control Panel\Accessibility\StickyKeys"
        path_filter = r"Control Panel\Accessibility\Keyboard Response"
        path_toggle = r"Control Panel\Accessibility\ToggleKeys"
        try:
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
        except Exception as e:
            logger.error(f"Failed to apply sticky keys tweak: {str(e)}")
            return False

@TweakRegistry.register
class EnableStartupDelayTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_startup_delay_tweak"

    def apply(self, enable: bool) -> bool:
        path_serialize = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Serialize"
        try:
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
        except Exception as e:
            logger.error(f"Failed to apply startup delay tweak: {str(e)}")
            return False

@TweakRegistry.register
class EnableMenuDelayTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_menu_delay_tweak"

    def apply(self, enable: bool) -> bool:
        path_desktop = r"Control Panel\Desktop"
        try:
            if enable:
                SystemTweaksService.backup_registry_value("HKCU", path_desktop, "MenuShowDelay")
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path_desktop, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "MenuShowDelay", 0, winreg.REG_SZ, "0")
            else:
                SystemTweaksService.restore_registry_value_or_default("HKCU", path_desktop, "MenuShowDelay", "400", winreg.REG_SZ)
            return True
        except Exception as e:
            logger.error(f"Failed to apply menu delay tweak: {str(e)}")
            return False

@TweakRegistry.register
class EnableSettingsSyncTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_settings_sync_tweak"

    def apply(self, enable: bool) -> bool:
        path_sync_policy = r"SOFTWARE\Policies\Microsoft\Windows\SettingSync"
        path_netcache_policy = r"SOFTWARE\Policies\Microsoft\Windows\NetCache"
        path_sync_user = r"Software\Microsoft\Windows\CurrentVersion\SettingSync\Groups"
        sync_groups = ["Personalization", "BrowserSettings", "Credentials", "LanguageSettings", "AppSync", "Windows"]
        
        try:
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
                except Exception:
                    pass
                try:
                    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_netcache_policy, 0, winreg.KEY_WRITE) as key:
                        winreg.SetValueEx(key, "Enabled", 0, winreg.REG_DWORD, 0)
                except Exception:
                    pass
                for group in sync_groups:
                    try:
                        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, f"{path_sync_user}\\{group}", 0, winreg.KEY_WRITE) as key:
                            winreg.SetValueEx(key, "Enabled", 0, winreg.REG_DWORD, 0)
                    except Exception:
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
        except Exception as e:
            logger.error(f"Failed to apply settings sync tweak: {str(e)}")
            return False

@TweakRegistry.register
class EnableDynamicLightingTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_dynamic_lighting_tweak"

    def apply(self, enable: bool) -> bool:
        path_lighting = r"Software\Microsoft\Lighting"
        try:
            if enable:
                SystemTweaksService.backup_registry_value("HKCU", path_lighting, "AmbientLightingEnabled")
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path_lighting, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "AmbientLightingEnabled", 0, winreg.REG_DWORD, 0)
            else:
                SystemTweaksService.restore_registry_value_or_default("HKCU", path_lighting, "AmbientLightingEnabled", 1, winreg.REG_DWORD)
            return True
        except Exception as e:
            logger.error(f"Failed to apply dynamic lighting tweak: {str(e)}")
            return False

@TweakRegistry.register
class EnableXboxSaveTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_xbox_save_tweak"

    def apply(self, enable: bool) -> bool:
        try:
            if enable:
                SystemTweaksService.backup_service("XblGameSave")
                SystemTweaksService.set_service_start_type("XblGameSave", SERVICE_DISABLED)
                SystemTweaksService.stop_service("XblGameSave")
                cmd1 = 'powershell -NoProfile -Command "Disable-ScheduledTask -TaskName XblGameSaveTask -TaskPath \\Microsoft\\XblGameSave\\ -ErrorAction SilentlyContinue"'
                cmd2 = 'powershell -NoProfile -Command "Disable-ScheduledTask -TaskName XblGameSaveTaskLogon -TaskPath \\Microsoft\\XblGameSave\\ -ErrorAction SilentlyContinue"'
                SystemTweaksService.safe_subprocess_call(cmd1, shell=True)
                SystemTweaksService.safe_subprocess_call(cmd2, shell=True)
            else:
                SystemTweaksService.restore_service_or_default("XblGameSave", SERVICE_DEMAND_START)
                cmd1 = 'powershell -NoProfile -Command "Enable-ScheduledTask -TaskName XblGameSaveTask -TaskPath \\Microsoft\\XblGameSave\\ -ErrorAction SilentlyContinue"'
                cmd2 = 'powershell -NoProfile -Command "Enable-ScheduledTask -TaskName XblGameSaveTaskLogon -TaskPath \\Microsoft\\XblGameSave\\ -ErrorAction SilentlyContinue"'
                SystemTweaksService.safe_subprocess_call(cmd1, shell=True)
                SystemTweaksService.safe_subprocess_call(cmd2, shell=True)
            return True
        except Exception as e:
            logger.error(f"Failed to apply Xbox Save Tweak: {str(e)}")
            return False

@TweakRegistry.register
class EnableStoreAutoUpdateTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_store_auto_update_tweak"

    def apply(self, enable: bool) -> bool:
        path_store = r"SOFTWARE\Policies\Microsoft\WindowsStore"
        path_cloud = r"SOFTWARE\Policies\Microsoft\Windows\CloudContent"
        path_cdm = r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"
        try:
            if enable:
                SystemTweaksService.backup_registry_value("HKLM", path_store, "AutoDownload")
                SystemTweaksService.backup_registry_value("HKLM", path_store, "DisableAutoInstall")
                SystemTweaksService.backup_registry_value("HKLM", path_cloud, "DisableWindowsConsumerFeatures")
                SystemTweaksService.backup_registry_value("HKCU", path_cdm, "SilentInstalledAppsEnabled")
                
                try:
                    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_store, 0, winreg.KEY_WRITE) as key:
                        winreg.SetValueEx(key, "AutoDownload", 0, winreg.REG_DWORD, 2)
                        winreg.SetValueEx(key, "DisableAutoInstall", 0, winreg.REG_DWORD, 1)
                except Exception:
                    pass
                try:
                    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_cloud, 0, winreg.KEY_WRITE) as key:
                        winreg.SetValueEx(key, "DisableWindowsConsumerFeatures", 0, winreg.REG_DWORD, 1)
                except Exception:
                    pass
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path_cdm, 0, winreg.KEY_WRITE) as key:
                        winreg.SetValueEx(key, "SilentInstalledAppsEnabled", 0, winreg.REG_DWORD, 0)
                except Exception:
                    pass
            else:
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_store, "AutoDownload", None, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_store, "DisableAutoInstall", None, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_cloud, "DisableWindowsConsumerFeatures", None, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKCU", path_cdm, "SilentInstalledAppsEnabled", 1, winreg.REG_DWORD)
            return True
        except Exception as e:
            logger.error(f"Failed to apply Store Auto Update tweak: {str(e)}")
            return False

@TweakRegistry.register
class EnableVulnerableDriverBlocklistTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_vulnerable_driver_blocklist_tweak"

    def apply(self, enable: bool) -> bool:
        path_ci = r"SYSTEM\CurrentControlSet\Control\CI\Config"
        try:
            if enable:
                SystemTweaksService.backup_registry_value("HKLM", path_ci, "VulnerableDriverBlocklistEnable")
                try:
                    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_ci, 0, winreg.KEY_WRITE) as key:
                        winreg.SetValueEx(key, "VulnerableDriverBlocklistEnable", 0, winreg.REG_DWORD, 0)
                except Exception:
                    pass
                
                try:
                    import subprocess
                    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", "Set-MpPreference -EnableVulnerableDriverBlocklist $false"]
                    subprocess.run(cmd, capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
                except Exception as e:
                    logger.error(f"Failed to set MpPreference for vulnerable driver blocklist (false): {str(e)}")
            else:
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_ci, "VulnerableDriverBlocklistEnable", 1, winreg.REG_DWORD)
                try:
                    import subprocess
                    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", "Set-MpPreference -EnableVulnerableDriverBlocklist $true"]
                    subprocess.run(cmd, capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
                except Exception as e:
                    logger.error(f"Failed to set MpPreference for vulnerable driver blocklist (true): {str(e)}")
            return True
        except Exception as e:
            logger.error(f"Failed to apply vulnerable driver blocklist tweak: {str(e)}")
            return False

@TweakRegistry.register
class EnableSpotlightTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_spotlight_tweak"

    def apply(self, enable: bool) -> bool:
        path_cdm = r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"
        try:
            if enable:
                SystemTweaksService.backup_registry_value("HKCU", path_cdm, "SubscribedContent-338387Enabled")
                SystemTweaksService.backup_registry_value("HKCU", path_cdm, "RotatingLockScreenOverlayEnabled")
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path_cdm, 0, winreg.KEY_WRITE) as key:
                        winreg.SetValueEx(key, "SubscribedContent-338387Enabled", 0, winreg.REG_DWORD, 0)
                        winreg.SetValueEx(key, "RotatingLockScreenOverlayEnabled", 0, winreg.REG_DWORD, 0)
                except Exception:
                    pass
            else:
                SystemTweaksService.restore_registry_value_or_default("HKCU", path_cdm, "SubscribedContent-338387Enabled", 1, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKCU", path_cdm, "RotatingLockScreenOverlayEnabled", 1, winreg.REG_DWORD)
            return True
        except Exception as e:
            logger.error(f"Failed to apply spotlight tweak: {str(e)}")
            return False

@TweakRegistry.register
class EnableGlobalFseTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_global_fse_tweak"

    def apply(self, enable: bool) -> bool:
        path_gcs = r"System\GameConfigStore"
        path_gamebar = r"Software\Microsoft\GameBar"
        try:
            if enable:
                SystemTweaksService.backup_registry_value("HKCU", path_gcs, "GameDVR_FSEBehaviorMode")
                SystemTweaksService.backup_registry_value("HKCU", path_gamebar, "ShowEToast")
                
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path_gcs, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "GameDVR_FSEBehaviorMode", 0, winreg.REG_DWORD, 2)
                try:
                    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path_gamebar, 0, winreg.KEY_WRITE) as key:
                        winreg.SetValueEx(key, "ShowEToast", 0, winreg.REG_DWORD, 0)
                except Exception:
                    pass
            else:
                SystemTweaksService.restore_registry_value_or_default("HKCU", path_gcs, "GameDVR_FSEBehaviorMode", 0, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKCU", path_gamebar, "ShowEToast", 1, winreg.REG_DWORD)
            return True
        except Exception as e:
            logger.error(f"Failed to apply global FSE tweak: {str(e)}")
            return False

@TweakRegistry.register
class EnableGameFseTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_game_fse_tweak"

    def apply(self, enable: bool, game_path: str = "") -> bool:
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
                        except Exception:
                            pass
                logger.info(f"FSE setting reverted for game: {game_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to apply game FSE tweak for {game_path}: {str(e)}")
            return False

@TweakRegistry.register
class EnableIrqAffinityTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_irq_affinity_tweak"

    def apply(self, enable: bool) -> bool:
        try:
            return IrqAffinityService.apply_separated_irq_affinity(enable)
        except Exception as e:
            logger.error(f"Failed to apply IRQ affinity tweak: {str(e)}")
            return False

@TweakRegistry.register
class EnableWebSearchTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_web_search_tweak"

    def apply(self, enable: bool) -> bool:
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
            
            try:
                SystemTweaksService.safe_subprocess_call(["taskkill", "/F", "/IM", "SearchHost.exe"], timeout=2)
            except Exception:
                pass
                
            return True
        except Exception as e:
            logger.error(f"Failed to apply/revert web search tweak: {str(e)}")
            return False

@TweakRegistry.register
class EnableTelemetryTasksTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_telemetry_tasks_tweak"

    def apply(self, enable: bool) -> bool:
        try:
            action = "Disable-ScheduledTask" if enable else "Enable-ScheduledTask"
            cmd = f"powershell -NoProfile -NonInteractive -Command \"Get-ScheduledTask -TaskName 'Microsoft Compatibility Appraiser*', 'ProgramDataUpdater', 'StartupAppTask', 'Consolidator', 'UsbCeip' -ErrorAction SilentlyContinue | {action} -ErrorAction SilentlyContinue\""
            SystemTweaksService.safe_subprocess_call(cmd, shell=True)
            if enable:
                logger.info("System telemetry and customer experience scheduled tasks disabled.")
            else:
                logger.info("System telemetry and customer experience scheduled tasks restored to enabled.")
            return True
        except Exception as e:
            logger.error(f"Failed to apply/revert telemetry tasks tweak: {str(e)}")
            return False

@TweakRegistry.register
class EnablePrefetcherTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_prefetcher_tweak"

    def apply(self, enable: bool) -> bool:
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
                
            try:
                cmd_status = ["powershell", "-NoProfile", "-NonInteractive", "-Command", "Get-Service SysMain -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Status"]
                p = subprocess.Popen(cmd_status, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
                stdout, _ = p.communicate(timeout=2)
                status = stdout.decode("utf-8", errors="ignore").strip().lower()
                if "running" in status:
                    SystemTweaksService.safe_subprocess_call(["powershell", "-NoProfile", "-NonInteractive", "-Command", "Restart-Service SysMain -Force"], timeout=5)
            except Exception:
                pass
                
            return True
        except Exception as e:
            logger.error(f"Failed to apply/revert prefetcher tweak: {str(e)}")
            return False

@TweakRegistry.register
class EnableConsultInterestsTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_consult_interests_tweak"

    def apply(self, enable: bool) -> bool:
        try:
            path_feeds = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Feeds"
            path_tips = r"SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"
            if not enable:
                SystemTweaksService.restore_registry_value_or_default("HKCU", path_feeds, "ShellFeedsTaskbarEnabled", 0, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKCU", path_tips, "SoftLandingEnabled", 1, winreg.REG_DWORD)
                return True
                
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
            return True
        except Exception as e:
            logger.error(f"Failed to apply consult interests tweak: {str(e)}")
            return False

@TweakRegistry.register
class EnableTipsSuggestionsTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_tips_suggestions_tweak"

    def apply(self, enable: bool) -> bool:
        try:
            path_feeds = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Feeds"
            path_tips = r"SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"
            if not enable:
                SystemTweaksService.restore_registry_value_or_default("HKCU", path_feeds, "ShellFeedsTaskbarEnabled", 0, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKCU", path_tips, "SoftLandingEnabled", 1, winreg.REG_DWORD)
                return True
                
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
            return True
        except Exception as e:
            logger.error(f"Failed to apply tips suggestions tweak: {str(e)}")
            return False

@TweakRegistry.register
class EnableBgAppsTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_bg_apps_tweak"

    def apply(self, enable: bool) -> bool:
        path_bg = r"Software\Microsoft\Windows\CurrentVersion\BackgroundAccessApplications"
        path_search = r"Software\Microsoft\Windows\CurrentVersion\Search"
        path_maps = r"SOFTWARE\Policies\Microsoft\Windows\Maps"
        
        try:
            if not enable:
                SystemTweaksService.restore_registry_value_or_default("HKCU", path_bg, "GlobalUserDisabled", 0, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKCU", path_search, "BackgroundAppGlobalToggle", 1, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_maps, "AutoDownloadAndUpdateMapData", None, winreg.REG_DWORD)
                return True
                
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
            return True
        except Exception as e:
            logger.error(f"Failed to apply background apps tweak: {str(e)}")
            return False

@TweakRegistry.register
class EnableMapUpdatesTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_map_updates_tweak"

    def apply(self, enable: bool) -> bool:
        path_bg = r"Software\Microsoft\Windows\CurrentVersion\BackgroundAccessApplications"
        path_search = r"Software\Microsoft\Windows\CurrentVersion\Search"
        path_maps = r"SOFTWARE\Policies\Microsoft\Windows\Maps"
        
        try:
            if not enable:
                SystemTweaksService.restore_registry_value_or_default("HKCU", path_bg, "GlobalUserDisabled", 0, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKCU", path_search, "BackgroundAppGlobalToggle", 1, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_maps, "AutoDownloadAndUpdateMapData", None, winreg.REG_DWORD)
                return True
                
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
            return True
        except Exception as e:
            logger.error(f"Failed to apply map updates tweak: {str(e)}")
            return False

@TweakRegistry.register
class EnableTimerResolutionTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_timer_resolution_tweak"

    def apply(self, enable: bool) -> bool:
        path = r"SYSTEM\CurrentControlSet\Control\Session Manager\kernel"
        try:
            if not enable:
                SystemTweaksService.restore_registry_value_or_default("HKLM", path, "GlobalTimerResolutionRequests", None, winreg.REG_DWORD)
            else:
                logger.info("Enabling global timer resolution requests...")
                SystemTweaksService.backup_registry_value("HKLM", path, "GlobalTimerResolutionRequests")
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "GlobalTimerResolutionRequests", 0, winreg.REG_DWORD, 1)
                logger.info("Global timer resolution requests enabled.")
            
            SystemTweaksService.set_timer_resolution_active(enable)
            return True
        except Exception as e:
            logger.error(f"Failed to apply timer resolution tweak: {str(e)}")
            return False

@TweakRegistry.register
class EnableNarakaPriority(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_naraka_priority"

    def apply(self, enable: bool, target_process_name: str = None) -> bool:
        games = ["NarakaBladepoint.exe", "Naraka.exe"]
        if target_process_name:
            exe_clean = os.path.basename(target_process_name)
            if exe_clean and exe_clean.endswith(".exe") and exe_clean not in games:
                games.append(exe_clean)
                
        try:
            if not enable:
                for game in games:
                    path = f"SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Image File Execution Options\\{game}\\PerfOptions"
                    SystemTweaksService.restore_registry_value_or_default("HKLM", path, "CpuPriorityClass", None, winreg.REG_DWORD)
                    SystemTweaksService.restore_registry_value_or_default("HKLM", path, "IoPriority", None, winreg.REG_DWORD)
                return True
                
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
            return True
        except Exception as e:
            logger.error(f"Failed to apply Naraka priority tweak: {str(e)}")
            return False

@TweakRegistry.register
class KeyboardQueueSize(BaseTweak):
    @property
    def id(self) -> str:
        return "keyboard_queue_size"

    def apply(self, val: int, mouse_size: int = 100) -> bool:
        kb_path = r"SYSTEM\CurrentControlSet\Services\kbdclass\Parameters"
        m_path = r"SYSTEM\CurrentControlSet\Services\mouclass\Parameters"
        
        SystemTweaksService.backup_registry_value("HKLM", kb_path, "KeyboardDataQueueSize")
        SystemTweaksService.backup_registry_value("HKLM", m_path, "MouseDataQueueSize")
        
        try:
            if val != 100:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, kb_path, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "KeyboardDataQueueSize", 0, winreg.REG_DWORD, val)
                logger.info(f"Set KeyboardDataQueueSize to {val}")
            else:
                SystemTweaksService.restore_registry_value_or_default("HKLM", kb_path, "KeyboardDataQueueSize", 100, winreg.REG_DWORD)
                
            if mouse_size != 100:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, m_path, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "MouseDataQueueSize", 0, winreg.REG_DWORD, mouse_size)
                logger.info(f"Set MouseDataQueueSize to {mouse_size}")
            else:
                SystemTweaksService.restore_registry_value_or_default("HKLM", m_path, "MouseDataQueueSize", 100, winreg.REG_DWORD)
            return True
        except Exception as e:
            logger.error(f"Failed to apply KeyboardQueueSize: {str(e)}")
            return False

@TweakRegistry.register
class MouseQueueSize(BaseTweak):
    @property
    def id(self) -> str:
        return "mouse_queue_size"

    def apply(self, val: int, keyboard_size: int = 100) -> bool:
        kb_path = r"SYSTEM\CurrentControlSet\Services\kbdclass\Parameters"
        m_path = r"SYSTEM\CurrentControlSet\Services\mouclass\Parameters"
        
        SystemTweaksService.backup_registry_value("HKLM", kb_path, "KeyboardDataQueueSize")
        SystemTweaksService.backup_registry_value("HKLM", m_path, "MouseDataQueueSize")
        
        try:
            if keyboard_size != 100:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, kb_path, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "KeyboardDataQueueSize", 0, winreg.REG_DWORD, keyboard_size)
                logger.info(f"Set KeyboardDataQueueSize to {keyboard_size}")
            else:
                SystemTweaksService.restore_registry_value_or_default("HKLM", kb_path, "KeyboardDataQueueSize", 100, winreg.REG_DWORD)
                
            if val != 100:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, m_path, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "MouseDataQueueSize", 0, winreg.REG_DWORD, val)
                logger.info(f"Set MouseDataQueueSize to {val}")
            else:
                SystemTweaksService.restore_registry_value_or_default("HKLM", m_path, "MouseDataQueueSize", 100, winreg.REG_DWORD)
            return True
        except Exception as e:
            logger.error(f"Failed to apply MouseQueueSize: {str(e)}")
            return False

@TweakRegistry.register
class ExtremeDebloatTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_extreme_debloat_tweak"

    def apply(self, enable: bool) -> bool:
        services_to_disable = [
            "Spooler",
            "XblAuthManager", "XblGameSave", "XboxNetApiSvc", "XboxGipSvc",
            "SysMain", "diagsvc", "DPS", "WdiServiceHost", "WdiSystemHost",
            "DiagTrack", "MapsBroker", "autotimesvc", "DusmSvc", "tzautoupdate",
            "PcaSvc", "DsmSvc", "WpcMonSvc", "SEMgrSvc", "PimIndexMaintenanceSvc",
            "vmicguestinterface", "vmicheartbeat", "vmickvpexchange", "vmicrdv",
            "vmicshutdown", "vmictimesync", "vmicvmsession", "vmicvss",
            "PhoneSvc", "RetailDemo", "wercplsupport", "NaturalAuthentication",
            "LxpSvc", "DispBrokerDesktopSvc", "RmSvc", "UsoSvc", "WaaSMedicSvc"
        ]
        
        task_paths = [
            r"\Microsoft\Windows\Customer Experience Improvement Program",
            r"\Microsoft\Windows\Application Experience",
            r"\Microsoft\Windows\Autochk",
            r"\Microsoft\Windows\DiskDiagnostic",
            r"\Microsoft\Windows\Power Efficiency Diagnostics",
            r"\Microsoft\Windows\Maintenance"
        ]
        
        try:
            if not enable:
                defaults = {
                    "Spooler": 2,
                    "XblAuthManager": 3, "XblGameSave": 3, "XboxNetApiSvc": 3, "XboxGipSvc": 3,
                    "SysMain": 2, "diagsvc": 3, "DPS": 2, "WdiServiceHost": 3, "WdiSystemHost": 3,
                    "DiagTrack": 2, "MapsBroker": 3, "autotimesvc": 3, "DusmSvc": 2, "tzautoupdate": 3,
                    "PcaSvc": 2, "DsmSvc": 3, "WpcMonSvc": 3, "SEMgrSvc": 3, "PimIndexMaintenanceSvc": 3,
                    "vmicguestinterface": 3, "vmicheartbeat": 3, "vmickvpexchange": 3, "vmicrdv": 3,
                    "vmicshutdown": 3, "vmictimesync": 3, "vmicvmsession": 3, "vmicvss": 3,
                    "PhoneSvc": 3, "RetailDemo": 3, "wercplsupport": 3, "NaturalAuthentication": 3,
                    "LxpSvc": 3, "DispBrokerDesktopSvc": 3, "RmSvc": 3, "UsoSvc": 2, "WaaSMedicSvc": 3
                }
                logger.info("Restoring extreme debloat services to defaults...")
                for svc in services_to_disable:
                    SystemTweaksService.restore_service_or_default(svc, defaults.get(svc, SERVICE_DEMAND_START))
                for svc in ["Spooler", "SysMain", "DPS", "DiagTrack", "UsoSvc"]:
                    try:
                        SystemTweaksService.safe_subprocess_call(["sc.exe", "start", svc], timeout=2)
                    except Exception:
                        pass
                
                # Restore scheduled tasks
                action = "Enable-ScheduledTask"
                for path in task_paths:
                    cmd = f"powershell -NoProfile -NonInteractive -Command \"Get-ScheduledTask -TaskPath '{path}\\' -ErrorAction SilentlyContinue | {action} -ErrorAction SilentlyContinue\""
                    try:
                        SystemTweaksService.safe_subprocess_call(cmd, shell=True, timeout=10)
                    except Exception as task_err:
                        logger.warning(f"Failed to enable scheduled tasks in folder {path}: {str(task_err)}")
                
                logger.info("Extreme debloat services and scheduled tasks restored.")
                return True

            logger.info("Applying extreme debloat: disabling services and scheduled tasks...")
            for svc in services_to_disable:
                SystemTweaksService.backup_service(svc)
                SystemTweaksService.set_service_start_type(svc, SERVICE_DISABLED)
                try:
                    SystemTweaksService.safe_subprocess_call(["sc.exe", "stop", svc], timeout=2)
                except Exception:
                    pass
                try:
                    SystemTweaksService.safe_subprocess_call(["sc.exe", "triggerinfo", svc, "delete"], timeout=2)
                except Exception:
                    pass
                try:
                    SystemTweaksService.safe_subprocess_call(["sc.exe", "failure", svc, "reset=", "0", "actions= "], timeout=2)
                except Exception:
                    pass
            
            # Disable scheduled tasks
            action = "Disable-ScheduledTask"
            for path in task_paths:
                cmd = f"powershell -NoProfile -NonInteractive -Command \"Get-ScheduledTask -TaskPath '{path}\\' -ErrorAction SilentlyContinue | {action} -ErrorAction SilentlyContinue\""
                try:
                    SystemTweaksService.safe_subprocess_call(cmd, shell=True, timeout=10)
                except Exception as task_err:
                    logger.warning(f"Failed to disable scheduled tasks in folder {path}: {str(task_err)}")
            
            logger.info("Extreme debloat applied successfully.")
            return True
        except Exception as e:
            logger.error(f"Failed to apply extreme debloat tweak: {str(e)}")
            return False

