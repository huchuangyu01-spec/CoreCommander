# -*- coding: utf-8 -*-
import os
import winreg
import subprocess
import json
import re

from core_commander.utils.logger import logger
from core_commander.core.tweaks.base import BaseTweak, TweakRegistry
from core_commander.core.system_tweaks import SystemTweaksService
from core_commander.core.gpu_smi import GpuSmiService
from core_commander.core.gpu_drs import NvidiaDrsService

try:
    import win32service
    import win32con
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

SERVICE_AUTO_START = 2
SERVICE_DISABLED = 4


@TweakRegistry.register
class GpuPreemptionTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "disable_gpu_preemption"

    def apply(self, enable: bool) -> bool:
        disable = enable
        path = r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers\Scheduler"
        if not disable:
            SystemTweaksService.restore_registry_value_or_default("HKLM", path, "EnablePreemption", 1, winreg.REG_DWORD)
            return True
        logger.info("Disabling GPU Preemption (EnablePreemption = 0)...")
        SystemTweaksService.backup_registry_value("HKLM", path, "EnablePreemption")
        try:
            with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "EnablePreemption", 0, winreg.REG_DWORD, 0)
            logger.info("GPU preemption disabled.")
            return True
        except Exception as e:
            logger.error(f"Failed to disable GPU preemption: {str(e)}")
            raise


@TweakRegistry.register
class GpuFirmwareTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_gpu_firmware_tweak"

    def apply(self, enable: bool) -> bool:
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
                return True
            except Exception as e:
                logger.error(f"Failed to restore GPU firmware tweak: {str(e)}")
                raise

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
            return True
        except Exception as e:
            logger.error(f"Failed to enable GPU firmware tweak: {str(e)}")
            raise


@TweakRegistry.register
class GpuPstateTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_gpu_pstate_tweak"

    def apply(self, enable: bool) -> bool:
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
                return True
            except Exception as e:
                logger.error(f"Failed to restore GPU pstate tweak: {str(e)}")
                raise

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
            return True
        except Exception as e:
            logger.error(f"Failed to enable GPU pstate tweak: {str(e)}")
            raise


@TweakRegistry.register
class GpuIrqTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_gpu_irq_tweak"

    def apply(self, enable: bool) -> bool:
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
                    gpu_ids = [g.PNPDeviceID for g in gpus if g.PNPDeviceID and ("VEN_10DE" in g.PNPDeviceID.upper() or "VEN_1002" in g.PNPDeviceID.upper())]
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
                ps_cmd = (
                    "$gpus = Get-PnpDevice -PresentOnly | Where-Object {$_.Class -eq 'Display' -and ($_.InstanceId -like '*VEN_10DE*' -or $_.InstanceId -like '*VEN_1002*')} | Select-Object -ExpandProperty InstanceId; "
                    "$resources = Get-CimInstance -ClassName Win32_PnPAllocatedResource -ErrorAction SilentlyContinue; "
                    "if (!$resources) { $resources = Get-WmiObject -Class Win32_PnPAllocatedResource -ErrorAction SilentlyContinue }; "
                    "$resources | Where-Object {$_.Antecedent -match 'IRQ'} | ForEach-Object { "
                    "  if ($gpus -contains $_.Dependent.DeviceID) { "
                    "    [PSCustomObject]@{ DeviceID = $_.Dependent.DeviceID; IRQ = $_.Antecedent.IRQNumber } "
                    "  } "
                    "} | ConvertTo-Json"
                )
                process = subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)  # nosec
                try:
                    stdout, stderr = process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate()

                output = stdout.decode("gbk", errors="ignore").strip()
                if output:
                    try:
                        data = json.loads(output)
                        if not isinstance(data, list):
                            data = [data]
                    except Exception as je:
                        logger.debug(f"JSON parsing GPU IRQs failed: {str(je)}")
                        irqs = [int(x) for x in re.findall(r'"IRQ":\s*(\d+)', output)]
                        data = [{"IRQ": x} for x in irqs]

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
            return True
        except Exception as e:
            logger.error(f"Failed to apply GPU IRQ tweak: {str(e)}")
            raise


@TweakRegistry.register
class HagsTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "disable_hags"

    def apply(self, enable: bool) -> bool:
        disable = enable
        path = r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers"
        value_name = "HwSchMode"
        SystemTweaksService.backup_registry_value("HKLM", path, value_name)
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_WRITE) as key:
                val = 1 if disable else 2
                winreg.SetValueEx(key, value_name, 0, winreg.REG_DWORD, val)
            logger.info(f"HAGS set to: {'Disabled' if disable else 'Enabled'}.")
            return True
        except Exception as e:
            logger.error(f"Failed to apply HAGS tweak: {str(e)}")
            raise


@TweakRegistry.register
class NvidiaNipTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_nvidia_nip"

    def apply(self, enable: bool) -> bool:
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


@TweakRegistry.register
class GpuMsiTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_gpu_msi_tweak"

    def apply(self, enable: bool) -> bool:
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
                        if g.PNPDeviceID and g.PNPDeviceID.startswith("PCI\\") and ("VEN_10DE" in g.PNPDeviceID.upper() or "VEN_1002" in g.PNPDeviceID.upper()):
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
                ps_cmd = "Get-PnpDevice -PresentOnly | Where-Object {$_.Class -eq 'Display' -and ($_.InstanceId -like '*VEN_10DE*' -or $_.InstanceId -like '*VEN_1002*')} | Select-Object -ExpandProperty InstanceId | ConvertTo-Json"
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


@TweakRegistry.register
class GameGpuPreferenceTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_game_gpu_preference_tweak"

    def apply(self, enable: bool, game_path: str = "", *args, **kwargs) -> bool:
        if not game_path and args:
            game_path = args[0]
        game_path = kwargs.get("game_path", game_path)
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


@TweakRegistry.register
class GpuOptimizationTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_gpu_optimization"

    def apply(self, enable: bool, gpu_vendor: str = "", *args, **kwargs) -> bool:
        if not gpu_vendor and args:
            gpu_vendor = args[0]
        gpu_vendor = kwargs.get("gpu_vendor", gpu_vendor)

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
            return True

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
        return True


@TweakRegistry.register
class DwmPresentationTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_dwm_presentation_tweak"

    def apply(self, enable: bool) -> bool:
        path = r"Software\Microsoft\DirectX"
        name = "DisableDXMaximizedWindowedMode"
        try:
            if enable:
                SystemTweaksService.backup_registry_value("HKCU", path, name)
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 1)
                logger.info("DWM maximized windowed presentation optimization applied.")
            else:
                SystemTweaksService.restore_registry_value_or_default("HKCU", path, name, 0, winreg.REG_DWORD)
                logger.info("DWM maximized windowed presentation optimization restored to default.")
            return True
        except Exception as e:
            logger.error(f"Failed to apply/revert DwmPresentationTweak: {str(e)}")
            return False

