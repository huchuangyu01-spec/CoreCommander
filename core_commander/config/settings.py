# -*- coding: utf-8 -*-
import threading
import queue
import time
from PySide6.QtCore import QSettings
from core_commander.config.exceptions import ConfigurationError
from core_commander.utils.logger import logger

class SettingsWriteWorker(threading.Thread):
    def __init__(self, qsettings, task_queue):
        super().__init__(daemon=True)
        self.qsettings = qsettings
        self.task_queue = task_queue
        
    def run(self):
        while True:
            try:
                # Wait for task
                item = self.task_queue.get()
                if item is None:
                    break
                key, value = item
                
                # 50ms debouncing window to merge rapid consecutive writes
                pending_updates = {key: value}
                time.sleep(0.05)
                while not self.task_queue.empty():
                    try:
                        next_item = self.task_queue.get_nowait()
                        if next_item is None:
                            break
                        k, v = next_item
                        pending_updates[k] = v
                    except queue.Empty:
                        break
                
                # Perform batch writes to registry/INI
                for k, v in pending_updates.items():
                    try:
                        self.qsettings.setValue(k, v)
                    except Exception as e:
                        logger.error(f"Background settings write error: {e}")
                try:
                    self.qsettings.sync()
                except Exception:
                    pass
            except Exception as e:
                logger.error(f"SettingsWriteWorker loop error: {e}")

class AppSettings:
    """
    Manages application configurations using QSettings with strong validation.
    """
    def __init__(self, organization: str = "CoreCommander", application: str = "FluentConfigs"):
        self._qsettings = QSettings(organization, application)
        self._lock = threading.Lock()
        self._cache = {}
        self._write_queue = queue.Queue()
        self._writer_thread = SettingsWriteWorker(self._qsettings, self._write_queue)
        self._writer_thread.start()
        logger.debug(f"Loaded configuration registry from {organization}/{application}")

    def get_string(self, key: str, default: str = "") -> str:
        with self._lock:
            if key in self._cache:
                return str(self._cache[key])
            val = self._qsettings.value(key, default)
            self._cache[key] = val
            return str(val)

    def get_int(self, key: str, default: int = 0) -> int:
        with self._lock:
            if key in self._cache:
                val = self._cache[key]
            else:
                val = self._qsettings.value(key, default)
                self._cache[key] = val
        try:
            return int(val)
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed parsing key '{key}' value '{val}' as int: {str(e)}")
            return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        with self._lock:
            if key in self._cache:
                val = self._cache[key]
            else:
                val = self._qsettings.value(key, default)
                self._cache[key] = val
        try:
            return float(val)
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed parsing key '{key}' value '{val}' as float: {str(e)}")
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        with self._lock:
            if key in self._cache:
                val = self._cache[key]
            else:
                val = self._qsettings.value(key, default)
                self._cache[key] = val
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.lower() in ("true", "1", "yes")
        if isinstance(val, int):
            return val != 0
        return bool(val)

    def get_list(self, key: str, default: list = None) -> list:
        if default is None:
            default = []
        with self._lock:
            if key in self._cache:
                val = self._cache[key]
            else:
                val = self._qsettings.value(key, default)
                self._cache[key] = val
        if isinstance(val, list):
            return val
        if isinstance(val, str):
            return [x.strip() for x in val.split(",") if x.strip()]
        return default

    def set_value(self, key: str, value) -> None:
        """
        Sets a key-value configuration. Queues the write to a background thread.
        """
        with self._lock:
            if key in self._cache and self._cache[key] == value:
                return
            self._cache[key] = value
            logger.debug(f"Setting config '{key}' = '{value}' (queued)")
            self._write_queue.put((key, value))

    @property
    def target_process_name(self) -> str:
        return self.get_string("proc_name", "")

    @target_process_name.setter
    def target_process_name(self, val: str) -> None:
        self.set_value("proc_name", val)

    @property
    def p1_idx(self) -> int:
        return self.get_int("p1_idx", 0)

    @p1_idx.setter
    def p1_idx(self, val: int) -> None:
        if val < 0:
            val = 0
        self.set_value("p1_idx", val)

    @property
    def p2_idx(self) -> int:
        return self.get_int("p2_idx", 0)

    @p2_idx.setter
    def p2_idx(self, val: int) -> None:
        if val < 0:
            val = 0
        self.set_value("p2_idx", val)

    @property
    def enable_isolation(self) -> bool:
        return self.get_bool("chk_iso", True)

    @enable_isolation.setter
    def enable_isolation(self, val: bool) -> None:
        self.set_value("chk_iso", val)

    @property
    def enable_watchdog(self) -> bool:
        return self.get_bool("chk_dog", True)

    @enable_watchdog.setter
    def enable_watchdog(self, val: bool) -> None:
        self.set_value("chk_dog", val)

    @property
    def enable_auto_mem_clean(self) -> bool:
        return self.get_bool("chk_mem", False)

    @enable_auto_mem_clean.setter
    def enable_auto_mem_clean(self, val: bool) -> None:
        self.set_value("chk_mem", val)

    @property
    def mem_clean_interval(self) -> int:
        # Validate interval is between 1 and 1440 minutes
        interval = self.get_int("mem_interval", 30)
        if interval < 1 or interval > 1440:
            logger.warning(f"Invalid mem_interval value found: {interval}. Reverting to 30.")
            return 30
        return interval

    @mem_clean_interval.setter
    def mem_clean_interval(self, val: int) -> None:
        if val < 1 or val > 1440:
            raise ConfigurationError("Memory clean interval must be between 1 and 1440 minutes.")
        self.set_value("mem_interval", val)

    @property
    def custom_whitelist(self) -> list:
        """
        Retrieves user-customized processes to exclude from background isolation.
        """
        return self.get_list("custom_whitelist", [])

    @custom_whitelist.setter
    def custom_whitelist(self, val: list) -> None:
        if not isinstance(val, list):
            raise ConfigurationError("Whitelist must be a list of strings.")
        clean_list = [str(x).strip() for x in val if str(x).strip()]
        self.set_value("custom_whitelist", clean_list)

    @property
    def enable_core_parking(self) -> bool:
        return self.get_bool("enable_core_parking", False)

    @enable_core_parking.setter
    def enable_core_parking(self, val: bool) -> None:
        self.set_value("enable_core_parking", val)

    @property
    def enable_epp_max(self) -> bool:
        return self.get_bool("enable_epp_max", False)

    @enable_epp_max.setter
    def enable_epp_max(self, val: bool) -> None:
        self.set_value("enable_epp_max", val)

    @property
    def enable_network_tweak(self) -> bool:
        return self.get_bool("enable_network_tweak", False)

    @enable_network_tweak.setter
    def enable_network_tweak(self, val: bool) -> None:
        self.set_value("enable_network_tweak", val)

    @property
    def enable_child_optimization(self) -> bool:
        return self.get_bool("enable_child_opt", True)

    @enable_child_optimization.setter
    def enable_child_optimization(self, val: bool) -> None:
        self.set_value("enable_child_opt", val)

    @property
    def win32_prio_sep(self) -> int:
        return self.get_int("win32_prio_sep", 0)

    @win32_prio_sep.setter
    def win32_prio_sep(self, val: int) -> None:
        self.set_value("win32_prio_sep", val)

    @property
    def disable_hpet(self) -> bool:
        return self.get_bool("disable_hpet", False)

    @disable_hpet.setter
    def disable_hpet(self, val: bool) -> None:
        self.set_value("disable_hpet", val)

    @property
    def keyboard_queue_size(self) -> int:
        return self.get_int("keyboard_queue_size", 100)

    @keyboard_queue_size.setter
    def keyboard_queue_size(self, val: int) -> None:
        self.set_value("keyboard_queue_size", val)

    @property
    def mouse_queue_size(self) -> int:
        return self.get_int("mouse_queue_size", 100)

    @mouse_queue_size.setter
    def mouse_queue_size(self, val: int) -> None:
        self.set_value("mouse_queue_size", val)

    @property
    def enable_dwm_tweak(self) -> bool:
        return self.get_bool("enable_dwm_tweak", False)

    @enable_dwm_tweak.setter
    def enable_dwm_tweak(self, val: bool) -> None:
        self.set_value("enable_dwm_tweak", val)

    @property
    def disable_useless_services(self) -> bool:
        return self.get_bool("disable_useless_services", False)

    @disable_useless_services.setter
    def disable_useless_services(self, val: bool) -> None:
        self.set_value("disable_useless_services", val)

    @property
    def enable_custom_power_plan(self) -> bool:
        return self.get_bool("enable_custom_power_plan", False)

    @enable_custom_power_plan.setter
    def enable_custom_power_plan(self, val: bool) -> None:
        self.set_value("enable_custom_power_plan", val)

    @property
    def enable_ram_optimization(self) -> bool:
        return self.get_bool("enable_ram_optimization", False)

    @enable_ram_optimization.setter
    def enable_ram_optimization(self, val: bool) -> None:
        self.set_value("enable_ram_optimization", val)

    @property
    def enable_nvme_optimization(self) -> bool:
        return self.get_bool("enable_nvme_optimization", False)

    @enable_nvme_optimization.setter
    def enable_nvme_optimization(self, val: bool) -> None:
        self.set_value("enable_nvme_optimization", val)

    @property
    def enable_gpu_optimization(self) -> bool:
        return self.get_bool("enable_gpu_optimization", False)

    @enable_gpu_optimization.setter
    def enable_gpu_optimization(self, val: bool) -> None:
        self.set_value("enable_gpu_optimization", val)

    @property
    def show_all_cpu_options(self) -> bool:
        return self.get_bool("show_all_cpu_options", False)

    @show_all_cpu_options.setter
    def show_all_cpu_options(self, val: bool) -> None:
        self.set_value("show_all_cpu_options", val)

    @property
    def disable_spectre_meltdown(self) -> bool:
        return self.get_bool("disable_spectre_meltdown", False)

    @disable_spectre_meltdown.setter
    def disable_spectre_meltdown(self, val: bool) -> None:
        self.set_value("disable_spectre_meltdown", val)

    @property
    def disable_gpu_preemption(self) -> bool:
        return self.get_bool("disable_gpu_preemption", False)

    @disable_gpu_preemption.setter
    def disable_gpu_preemption(self, val: bool) -> None:
        self.set_value("disable_gpu_preemption", val)

    @property
    def disable_gamedvr(self) -> bool:
        return self.get_bool("disable_gamedvr", False)

    @disable_gamedvr.setter
    def disable_gamedvr(self, val: bool) -> None:
        self.set_value("disable_gamedvr", val)

    @property
    def enable_ultimate_network_tweak(self) -> bool:
        return self.get_bool("enable_ultimate_network_tweak", False)

    @enable_ultimate_network_tweak.setter
    def enable_ultimate_network_tweak(self, val: bool) -> None:
        self.set_value("enable_ultimate_network_tweak", val)

    @property
    def enable_usb_low_latency_tweak(self) -> bool:
        return self.get_bool("enable_usb_low_latency_tweak", False)

    @enable_usb_low_latency_tweak.setter
    def enable_usb_low_latency_tweak(self, val: bool) -> None:
        self.set_value("enable_usb_low_latency_tweak", val)

    @property
    def enable_dpc_latency_tweak(self) -> bool:
        return self.get_bool("enable_dpc_latency_tweak", False)

    @enable_dpc_latency_tweak.setter
    def enable_dpc_latency_tweak(self, val: bool) -> None:
        self.set_value("enable_dpc_latency_tweak", val)

    @property
    def enable_dwm_super_wet_tweak(self) -> bool:
        return self.get_bool("enable_dwm_super_wet_tweak", False)

    @enable_dwm_super_wet_tweak.setter
    def enable_dwm_super_wet_tweak(self, val: bool) -> None:
        self.set_value("enable_dwm_super_wet_tweak", val)

    @property
    def keyboard_repeat_delay_level(self) -> int:
        return self.get_int("keyboard_repeat_delay_level", 0)

    @keyboard_repeat_delay_level.setter
    def keyboard_repeat_delay_level(self, val: int) -> None:
        self.set_value("keyboard_repeat_delay_level", val)

    @property
    def enable_timer_resolution_tweak(self) -> bool:
        return self.get_bool("enable_timer_resolution_tweak", False)

    @enable_timer_resolution_tweak.setter
    def enable_timer_resolution_tweak(self, val: bool) -> None:
        self.set_value("enable_timer_resolution_tweak", val)

    @property
    def enable_usb_imod_tweak(self) -> bool:
        return self.get_bool("enable_usb_imod_tweak", False)

    @enable_usb_imod_tweak.setter
    def enable_usb_imod_tweak(self, val: bool) -> None:
        self.set_value("enable_usb_imod_tweak", val)

    @property
    def disable_pcipower(self) -> bool:
        return self.get_bool("disable_pcipower", False)

    @disable_pcipower.setter
    def disable_pcipower(self, val: bool) -> None:
        self.set_value("disable_pcipower", val)

    @property
    def enable_directx_tweaks(self) -> bool:
        return self.get_bool("enable_directx_tweaks", False)

    @enable_directx_tweaks.setter
    def enable_directx_tweaks(self, val: bool) -> None:
        self.set_value("enable_directx_tweaks", val)

    @property
    def enable_device_power_tweak(self) -> bool:
        return self.get_bool("enable_device_power_tweak", False)

    @enable_device_power_tweak.setter
    def enable_device_power_tweak(self, val: bool) -> None:
        self.set_value("enable_device_power_tweak", val)

    @property
    def enable_dns_tweak(self) -> bool:
        return self.get_bool("enable_dns_tweak", False)

    @enable_dns_tweak.setter
    def enable_dns_tweak(self, val: bool) -> None:
        self.set_value("enable_dns_tweak", val)

    @property
    def enable_consult_interests_tweak(self) -> bool:
        return self.get_bool("enable_consult_interests_tweak", False)

    @enable_consult_interests_tweak.setter
    def enable_consult_interests_tweak(self, val: bool) -> None:
        self.set_value("enable_consult_interests_tweak", val)

    @property
    def enable_tips_suggestions_tweak(self) -> bool:
        return self.get_bool("enable_tips_suggestions_tweak", False)

    @enable_tips_suggestions_tweak.setter
    def enable_tips_suggestions_tweak(self, val: bool) -> None:
        self.set_value("enable_tips_suggestions_tweak", val)

    @property
    def enable_desktop_heap_tweak(self) -> bool:
        return self.get_bool("enable_desktop_heap_tweak", False)

    @enable_desktop_heap_tweak.setter
    def enable_desktop_heap_tweak(self, val: bool) -> None:
        self.set_value("enable_desktop_heap_tweak", val)

    @property
    def enable_uac_tweak(self) -> bool:
        return self.get_bool("enable_uac_tweak", False)

    @enable_uac_tweak.setter
    def enable_uac_tweak(self, val: bool) -> None:
        self.set_value("enable_uac_tweak", val)

    @property
    def enable_download_maps_tweak(self) -> bool:
        return self.get_bool("enable_download_maps_tweak", False)

    @enable_download_maps_tweak.setter
    def enable_download_maps_tweak(self, val: bool) -> None:
        self.set_value("enable_download_maps_tweak", val)

    @property
    def enable_bg_apps_tweak(self) -> bool:
        return self.get_bool("enable_bg_apps_tweak", False)

    @enable_bg_apps_tweak.setter
    def enable_bg_apps_tweak(self, val: bool) -> None:
        self.set_value("enable_bg_apps_tweak", val)

    @property
    def enable_map_updates_tweak(self) -> bool:
        return self.get_bool("enable_map_updates_tweak", False)

    @enable_map_updates_tweak.setter
    def enable_map_updates_tweak(self, val: bool) -> None:
        self.set_value("enable_map_updates_tweak", val)

    @property
    def enable_autoshare_tweak(self) -> bool:
        return self.get_bool("enable_autoshare_tweak", False)

    @enable_autoshare_tweak.setter
    def enable_autoshare_tweak(self, val: bool) -> None:
        self.set_value("enable_autoshare_tweak", val)

    @property
    def enable_autorun_tweak(self) -> bool:
        return self.get_bool("enable_autorun_tweak", False)

    @enable_autorun_tweak.setter
    def enable_autorun_tweak(self, val: bool) -> None:
        self.set_value("enable_autorun_tweak", val)

    @property
    def enable_mouse_latency_tweak(self) -> bool:
        return self.get_bool("enable_mouse_latency_tweak", False)

    @enable_mouse_latency_tweak.setter
    def enable_mouse_latency_tweak(self, val: bool) -> None:
        self.set_value("enable_mouse_latency_tweak", val)

    @property
    def enable_config_alloc_tweak(self) -> bool:
        return self.get_bool("enable_config_alloc_tweak", False)

    @enable_config_alloc_tweak.setter
    def enable_config_alloc_tweak(self, val: bool) -> None:
        self.set_value("enable_config_alloc_tweak", val)

    @property
    def enable_gpu_firmware_tweak(self) -> bool:
        return self.get_bool("enable_gpu_firmware_tweak", False)

    @enable_gpu_firmware_tweak.setter
    def enable_gpu_firmware_tweak(self, val: bool) -> None:
        self.set_value("enable_gpu_firmware_tweak", val)

    @property
    def disable_memory_compression(self) -> bool:
        return self.get_bool("disable_memory_compression", False)

    @disable_memory_compression.setter
    def disable_memory_compression(self, val: bool) -> None:
        self.set_value("disable_memory_compression", val)

    @property
    def enable_naraka_priority(self) -> bool:
        return self.get_bool("enable_naraka_priority", False)

    @enable_naraka_priority.setter
    def enable_naraka_priority(self, val: bool) -> None:
        self.set_value("enable_naraka_priority", val)

    @property
    def enable_gpu_pstate_tweak(self) -> bool:
        return self.get_bool("enable_gpu_pstate_tweak", False)

    @enable_gpu_pstate_tweak.setter
    def enable_gpu_pstate_tweak(self, val: bool) -> None:
        self.set_value("enable_gpu_pstate_tweak", val)

    @property
    def disable_windows_visual_effects(self) -> bool:
        return self.get_bool("disable_windows_visual_effects", False)

    @disable_windows_visual_effects.setter
    def disable_windows_visual_effects(self, val: bool) -> None:
        self.set_value("disable_windows_visual_effects", val)

    @property
    def disable_windows_transparency(self) -> bool:
        return self.get_bool("disable_windows_transparency", False)

    @disable_windows_transparency.setter
    def disable_windows_transparency(self, val: bool) -> None:
        self.set_value("disable_windows_transparency", val)

    @property
    def disable_copilot(self) -> bool:
        return self.get_bool("disable_copilot", False)

    @disable_copilot.setter
    def disable_copilot(self, val: bool) -> None:
        self.set_value("disable_copilot", val)

    @property
    def disable_security_notifications(self) -> bool:
        return self.get_bool("disable_security_notifications", False)

    @disable_security_notifications.setter
    def disable_security_notifications(self, val: bool) -> None:
        self.set_value("disable_security_notifications", val)

    @property
    def disable_defender(self) -> bool:
        return self.get_bool("disable_defender", False)

    @disable_defender.setter
    def disable_defender(self, val: bool) -> None:
        self.set_value("disable_defender", val)

    @property
    def disable_smartscreen(self) -> bool:
        return self.get_bool("disable_smartscreen", False)

    @disable_smartscreen.setter
    def disable_smartscreen(self, val: bool) -> None:
        self.set_value("disable_smartscreen", val)

    @property
    def disable_firewall(self) -> bool:
        return self.get_bool("disable_firewall", False)

    @disable_firewall.setter
    def disable_firewall(self, val: bool) -> None:
        self.set_value("disable_firewall", val)

    @property
    def enable_driver_priority_tweak(self) -> bool:
        return self.get_bool("enable_driver_priority_tweak", False)

    @enable_driver_priority_tweak.setter
    def enable_driver_priority_tweak(self, val: bool) -> None:
        self.set_value("enable_driver_priority_tweak", val)

    @property
    def disable_hyperv_virtualization(self) -> bool:
        return self.get_bool("disable_hyperv_virtualization", False)

    @disable_hyperv_virtualization.setter
    def disable_hyperv_virtualization(self, val: bool) -> None:
        self.set_value("disable_hyperv_virtualization", val)

    @property
    def enable_nvidia_nip(self) -> bool:
        return self.get_bool("enable_nvidia_nip", False)

    @enable_nvidia_nip.setter
    def enable_nvidia_nip(self, val: bool) -> None:
        self.set_value("enable_nvidia_nip", val)

    @property
    def enable_gpu_irq_tweak(self) -> bool:
        return self.get_bool("enable_gpu_irq_tweak", False)

    @enable_gpu_irq_tweak.setter
    def enable_gpu_irq_tweak(self, val: bool) -> None:
        self.set_value("enable_gpu_irq_tweak", val)

    @property
    def disable_hags(self) -> bool:
        return self.get_bool("disable_hags", False)

    @disable_hags.setter
    def disable_hags(self, val: bool) -> None:
        self.set_value("disable_hags", val)

    @property
    def language(self) -> str:
        return self.get_string("language", "zh_CN")

    @language.setter
    def language(self, val: str) -> None:
        self.set_value("language", val)

    @property
    def theme_mode(self) -> str:
        return self.get_string("theme_mode", "auto")

    @theme_mode.setter
    def theme_mode(self, val: str) -> None:
        self.set_value("theme_mode", val)

    @property
    def accent_color(self) -> str:
        return self.get_string("accent_color", "#0078D4")

    @accent_color.setter
    def accent_color(self, val: str) -> None:
        self.set_value("accent_color", val)

    @property
    def affinity_mask_threads(self) -> list:
        val = self.get_list("affinity_mask_threads", None)
        if val is None:
            return []
        try:
            return [int(x) for x in val]
        except Exception:
            return []

    @affinity_mask_threads.setter
    def affinity_mask_threads(self, val: list) -> None:
        str_list = [str(x) for x in val]
        self.set_value("affinity_mask_threads", str_list)

    @property
    def has_saved_affinity(self) -> bool:
        return self._qsettings.contains("affinity_mask_threads")

    @property
    def enable_widgets_tweak(self) -> bool:
        return self.get_bool("enable_widgets_tweak", False)

    @enable_widgets_tweak.setter
    def enable_widgets_tweak(self, val: bool) -> None:
        self.set_value("enable_widgets_tweak", val)

    @property
    def enable_sticky_keys_tweak(self) -> bool:
        return self.get_bool("enable_sticky_keys_tweak", False)

    @enable_sticky_keys_tweak.setter
    def enable_sticky_keys_tweak(self, val: bool) -> None:
        self.set_value("enable_sticky_keys_tweak", val)

    @property
    def enable_startup_delay_tweak(self) -> bool:
        return self.get_bool("enable_startup_delay_tweak", False)

    @enable_startup_delay_tweak.setter
    def enable_startup_delay_tweak(self, val: bool) -> None:
        self.set_value("enable_startup_delay_tweak", val)

    @property
    def enable_menu_delay_tweak(self) -> bool:
        return self.get_bool("enable_menu_delay_tweak", False)

    @enable_menu_delay_tweak.setter
    def enable_menu_delay_tweak(self, val: bool) -> None:
        self.set_value("enable_menu_delay_tweak", val)

    @property
    def enable_settings_sync_tweak(self) -> bool:
        return self.get_bool("enable_settings_sync_tweak", False)

    @enable_settings_sync_tweak.setter
    def enable_settings_sync_tweak(self, val: bool) -> None:
        self.set_value("enable_settings_sync_tweak", val)

    @property
    def enable_dynamic_lighting_tweak(self) -> bool:
        return self.get_bool("enable_dynamic_lighting_tweak", False)

    @enable_dynamic_lighting_tweak.setter
    def enable_dynamic_lighting_tweak(self, val: bool) -> None:
        self.set_value("enable_dynamic_lighting_tweak", val)

    @property
    def enable_gpu_msi_tweak(self) -> bool:
        return self.get_bool("enable_gpu_msi_tweak", False)

    @enable_gpu_msi_tweak.setter
    def enable_gpu_msi_tweak(self, val: bool) -> None:
        self.set_value("enable_gpu_msi_tweak", val)

    @property
    def enable_network_msi_tweak(self) -> bool:
        return self.get_bool("enable_network_msi_tweak", False)

    @enable_network_msi_tweak.setter
    def enable_network_msi_tweak(self, val: bool) -> None:
        self.set_value("enable_network_msi_tweak", val)

    @property
    def enable_storage_msi_tweak(self) -> bool:
        return self.get_bool("enable_storage_msi_tweak", False)

    @enable_storage_msi_tweak.setter
    def enable_storage_msi_tweak(self, val: bool) -> None:
        self.set_value("enable_storage_msi_tweak", val)

    @property
    def enable_dwm_presentation_tweak(self) -> bool:
        return self.get_bool("enable_dwm_presentation_tweak", False)

    @enable_dwm_presentation_tweak.setter
    def enable_dwm_presentation_tweak(self, val: bool) -> None:
        self.set_value("enable_dwm_presentation_tweak", val)

    @property
    def enable_client_priority_demote(self) -> bool:
        return self.get_bool("enable_client_priority_demote", False)

    @enable_client_priority_demote.setter
    def enable_client_priority_demote(self, val: bool) -> None:
        self.set_value("enable_client_priority_demote", val)

    @property
    def enable_xbox_save_tweak(self) -> bool:
        return self.get_bool("enable_xbox_save_tweak", False)

    @enable_xbox_save_tweak.setter
    def enable_xbox_save_tweak(self, val: bool) -> None:
        self.set_value("enable_xbox_save_tweak", val)

    @property
    def enable_store_auto_update_tweak(self) -> bool:
        return self.get_bool("enable_store_auto_update_tweak", False)

    @enable_store_auto_update_tweak.setter
    def enable_store_auto_update_tweak(self, val: bool) -> None:
        self.set_value("enable_store_auto_update_tweak", val)

    @property
    def enable_vulnerable_driver_blocklist_tweak(self) -> bool:
        return self.get_bool("enable_vulnerable_driver_blocklist_tweak", False)

    @enable_vulnerable_driver_blocklist_tweak.setter
    def enable_vulnerable_driver_blocklist_tweak(self, val: bool) -> None:
        self.set_value("enable_vulnerable_driver_blocklist_tweak", val)

    @property
    def enable_prevent_device_encryption_tweak(self) -> bool:
        return self.get_bool("enable_prevent_device_encryption_tweak", False)

    @enable_prevent_device_encryption_tweak.setter
    def enable_prevent_device_encryption_tweak(self, val: bool) -> None:
        self.set_value("enable_prevent_device_encryption_tweak", val)

    @property
    def enable_spotlight_tweak(self) -> bool:
        return self.get_bool("enable_spotlight_tweak", False)

    @enable_spotlight_tweak.setter
    def enable_spotlight_tweak(self, val: bool) -> None:
        self.set_value("enable_spotlight_tweak", val)

    @property
    def enable_hard_working_set(self) -> bool:
        return self.get_bool("enable_hard_working_set", False)

    @enable_hard_working_set.setter
    def enable_hard_working_set(self, val: bool) -> None:
        self.set_value("enable_hard_working_set", val)

    @property
    def enable_net_imod_tweak(self) -> bool:
        return self.get_bool("enable_net_imod_tweak", False)

    @enable_net_imod_tweak.setter
    def enable_net_imod_tweak(self, val: bool) -> None:
        self.set_value("enable_net_imod_tweak", val)

    @property
    def enable_net_bindings_tweak(self) -> bool:
        return self.get_bool("enable_net_bindings_tweak", False)

    @enable_net_bindings_tweak.setter
    def enable_net_bindings_tweak(self, val: bool) -> None:
        self.set_value("enable_net_bindings_tweak", val)

    @property
    def target_process_path(self) -> str:
        return self.get_string("proc_path", "")

    @target_process_path.setter
    def target_process_path(self, val: str) -> None:
        self.set_value("proc_path", val)

    @property
    def enable_global_fse_tweak(self) -> bool:
        return self.get_bool("enable_global_fse_tweak", False)

    @enable_global_fse_tweak.setter
    def enable_global_fse_tweak(self, val: bool) -> None:
        self.set_value("enable_global_fse_tweak", val)

    @property
    def enable_game_fse_tweak(self) -> bool:
        return self.get_bool("enable_game_fse_tweak", False)

    @enable_game_fse_tweak.setter
    def enable_game_fse_tweak(self, val: bool) -> None:
        self.set_value("enable_game_fse_tweak", val)

    @property
    def enable_wifi_tweak(self) -> bool:
        return self.get_bool("enable_wifi_tweak", False)

    @enable_wifi_tweak.setter
    def enable_wifi_tweak(self, val: bool) -> None:
        self.set_value("enable_wifi_tweak", val)

    @property
    def enable_game_gpu_preference_tweak(self) -> bool:
        return self.get_bool("enable_game_gpu_preference_tweak", False)

    @enable_game_gpu_preference_tweak.setter
    def enable_game_gpu_preference_tweak(self, val: bool) -> None:
        self.set_value("enable_game_gpu_preference_tweak", val)

    @property
    def enable_irq_affinity_tweak(self) -> bool:
        return self.get_bool("enable_irq_affinity_tweak", False)

    @enable_irq_affinity_tweak.setter
    def enable_irq_affinity_tweak(self, val: bool) -> None:
        self.set_value("enable_irq_affinity_tweak", val)

    @property
    def enable_power_throttling_tweak(self) -> bool:
        return self.get_bool("enable_power_throttling_tweak", False)

    @enable_power_throttling_tweak.setter
    def enable_power_throttling_tweak(self, val: bool) -> None:
        self.set_value("enable_power_throttling_tweak", val)

    @property
    def enable_tcp_bbr_tweak(self) -> bool:
        return self.get_bool("enable_tcp_bbr_tweak", False)

    @enable_tcp_bbr_tweak.setter
    def enable_tcp_bbr_tweak(self, val: bool) -> None:
        self.set_value("enable_tcp_bbr_tweak", val)

    @property
    def enable_eee_tweak(self) -> bool:
        return self.get_bool("enable_eee_tweak", False)

    @enable_eee_tweak.setter
    def enable_eee_tweak(self, val: bool) -> None:
        self.set_value("enable_eee_tweak", val)

    @property
    def enable_web_search_tweak(self) -> bool:
        return self.get_bool("enable_web_search_tweak", False)

    @enable_web_search_tweak.setter
    def enable_web_search_tweak(self, val: bool) -> None:
        self.set_value("enable_web_search_tweak", val)

    @property
    def enable_telemetry_tasks_tweak(self) -> bool:
        return self.get_bool("enable_telemetry_tasks_tweak", False)

    @enable_telemetry_tasks_tweak.setter
    def enable_telemetry_tasks_tweak(self, val: bool) -> None:
        self.set_value("enable_telemetry_tasks_tweak", val)

    @property
    def enable_prefetcher_tweak(self) -> bool:
        return self.get_bool("enable_prefetcher_tweak", False)

    @enable_prefetcher_tweak.setter
    def enable_prefetcher_tweak(self, val: bool) -> None:
        self.set_value("enable_prefetcher_tweak", val)

    @property
    def enable_extreme_debloat_tweak(self) -> bool:
        return self.get_bool("enable_extreme_debloat_tweak", False)

    @enable_extreme_debloat_tweak.setter
    def enable_extreme_debloat_tweak(self, val: bool) -> None:
        self.set_value("enable_extreme_debloat_tweak", val)

    @property
    def disable_wsearch_tweak(self) -> bool:
        return self.get_bool("disable_wsearch_tweak", False)

    @disable_wsearch_tweak.setter
    def disable_wsearch_tweak(self, val: bool) -> None:
        self.set_value("disable_wsearch_tweak", val)

    # --- OSD Overlay Settings ---
    @property
    def enable_fps_overlay(self) -> bool:
        return self.get_bool("enable_fps_overlay", False)

    @enable_fps_overlay.setter
    def enable_fps_overlay(self, val: bool) -> None:
        self.set_value("enable_fps_overlay", val)

    @property
    def fps_overlay_lock(self) -> bool:
        return self.get_bool("fps_overlay_lock", True)

    @fps_overlay_lock.setter
    def fps_overlay_lock(self, val: bool) -> None:
        self.set_value("fps_overlay_lock", val)

    @property
    def fps_overlay_font_size(self) -> int:
        return self.get_int("fps_overlay_font_size", 14)

    @fps_overlay_font_size.setter
    def fps_overlay_font_size(self, val: int) -> None:
        if val < 8:
            val = 8
        if val > 36:
            val = 36
        self.set_value("fps_overlay_font_size", val)

    @property
    def fps_overlay_opacity(self) -> int:
        return self.get_int("fps_overlay_opacity", 85)

    @fps_overlay_opacity.setter
    def fps_overlay_opacity(self, val: int) -> None:
        if val < 10:
            val = 10
        if val > 100:
            val = 100
        self.set_value("fps_overlay_opacity", val)

    @property
    def fps_overlay_show_cpu_gpu(self) -> bool:
        return self.get_bool("fps_overlay_show_cpu_gpu", True)

    @fps_overlay_show_cpu_gpu.setter
    def fps_overlay_show_cpu_gpu(self, val: bool) -> None:
        self.set_value("fps_overlay_show_cpu_gpu", val)

    @property
    def fps_overlay_show_ram(self) -> bool:
        return self.get_bool("fps_overlay_show_ram", True)

    @fps_overlay_show_ram.setter
    def fps_overlay_show_ram(self, val: bool) -> None:
        self.set_value("fps_overlay_show_ram", val)

    @property
    def fps_overlay_show_frametime(self) -> bool:
        return self.get_bool("fps_overlay_show_frametime", True)

    @fps_overlay_show_frametime.setter
    def fps_overlay_show_frametime(self, val: bool) -> None:
        self.set_value("fps_overlay_show_frametime", val)

    @property
    def fps_overlay_hotkey(self) -> str:
        return self.get_string("fps_overlay_hotkey", "Ctrl+Shift+O")

    @fps_overlay_hotkey.setter
    def fps_overlay_hotkey(self, val: str) -> None:
        self.set_value("fps_overlay_hotkey", val)

    @property
    def ocr_hotkey(self) -> str:
        return self.get_string("ocr_hotkey", "Alt+Q")

    @ocr_hotkey.setter
    def ocr_hotkey(self, val: str) -> None:
        self.set_value("ocr_hotkey", val)

    @property
    def fps_overlay_pos_x(self) -> int:
        return self.get_int("fps_overlay_pos_x", 10)

    @fps_overlay_pos_x.setter
    def fps_overlay_pos_x(self, val: int) -> None:
        self.set_value("fps_overlay_pos_x", val)

    @property
    def fps_overlay_pos_y(self) -> int:
        return self.get_int("fps_overlay_pos_y", 10)

    @fps_overlay_pos_y.setter
    def fps_overlay_pos_y(self, val: int) -> None:
        self.set_value("fps_overlay_pos_y", val)

    @property
    def enable_rate_limiter(self) -> bool:
        return self.get_bool("enable_rate_limiter", False)

    @enable_rate_limiter.setter
    def enable_rate_limiter(self, val: bool) -> None:
        self.set_value("enable_rate_limiter", val)

    @property
    def rate_limiter_download_value(self) -> float:
        try:
            return float(self.get_string("rate_limiter_download_value", "100.0"))
        except Exception:
            return 100.0

    @rate_limiter_download_value.setter
    def rate_limiter_download_value(self, val: float) -> None:
        self.set_value("rate_limiter_download_value", str(val))

    @property
    def rate_limiter_value(self) -> float:
        try:
            return float(self.get_string("rate_limiter_value", "100.0"))
        except Exception:
            return 100.0

    @rate_limiter_value.setter
    def rate_limiter_value(self, val: float) -> None:
        self.set_value("rate_limiter_value", str(val))

    @property
    def rate_limiter_value_ms(self) -> float:
        try:
            return float(self.get_string("rate_limiter_value_ms", "50.0"))
        except Exception:
            return 50.0

    @rate_limiter_value_ms.setter
    def rate_limiter_value_ms(self, val: float) -> None:
        self.set_value("rate_limiter_value_ms", str(val))

    @property
    def rate_limiter_value_kbps(self) -> float:
        try:
            return float(self.get_string("rate_limiter_value_kbps", "100.0"))
        except Exception:
            return 100.0

    @rate_limiter_value_kbps.setter
    def rate_limiter_value_kbps(self, val: float) -> None:
        self.set_value("rate_limiter_value_kbps", str(val))

    @property
    def rate_limiter_unit(self) -> str:
        return self.get_string("rate_limiter_unit", "KB/s")

    @rate_limiter_unit.setter
    def rate_limiter_unit(self, val: str) -> None:
        self.set_value("rate_limiter_unit", val)

    @property
    def rate_limiter_hotkey(self) -> str:
        return self.get_string("rate_limiter_hotkey", "无")

    @rate_limiter_hotkey.setter
    def rate_limiter_hotkey(self, val: str) -> None:
        self.set_value("rate_limiter_hotkey", val)

    @property
    def rate_limiter_hotkey_code(self) -> int:
        return self.get_int("rate_limiter_hotkey_code", 0)

    @rate_limiter_hotkey_code.setter
    def rate_limiter_hotkey_code(self, val: int) -> None:
        self.set_value("rate_limiter_hotkey_code", val)

    @property
    def rate_limiter_hotkey_type(self) -> str:
        return self.get_string("rate_limiter_hotkey_type", "keyboard")

    @rate_limiter_hotkey_type.setter
    def rate_limiter_hotkey_type(self, val: str) -> None:
        self.set_value("rate_limiter_hotkey_type", val)

    @property
    def rate_limiter_mode(self) -> str:
        return self.get_string("rate_limiter_mode", "toggle")

    @rate_limiter_mode.setter
    def rate_limiter_mode(self, val: str) -> None:
        self.set_value("rate_limiter_mode", val)

    @property
    def rate_limiter_direction(self) -> str:
        return self.get_string("rate_limiter_direction", "both")

    @rate_limiter_direction.setter
    def rate_limiter_direction(self, val: str) -> None:
        self.set_value("rate_limiter_direction", val)

    @property
    def rate_limiter_pulse_duration(self) -> float:
        return self.get_float("rate_limiter_pulse_duration", 3000.0)

    @rate_limiter_pulse_duration.setter
    def rate_limiter_pulse_duration(self, val: float) -> None:
        self.set_value("rate_limiter_pulse_duration", val)

    @property
    def rate_limiter_pulse_delay(self) -> float:
        return self.get_float("rate_limiter_pulse_delay", 0.0)

    @rate_limiter_pulse_delay.setter
    def rate_limiter_pulse_delay(self, val: float) -> None:
        self.set_value("rate_limiter_pulse_delay", val)

    @property
    def rate_limiter_type(self) -> str:
        return self.get_string("rate_limiter_type", "firewall")

    @rate_limiter_type.setter
    def rate_limiter_type(self, val: str) -> None:
        self.set_value("rate_limiter_type", val)

    @property
    def enable_crosshair(self) -> bool:
        return self.get_bool("enable_crosshair", False)

    @enable_crosshair.setter
    def enable_crosshair(self, val: bool) -> None:
        self.set_value("enable_crosshair", val)

    @property
    def crosshair_style(self) -> str:
        return self.get_string("crosshair_style", "cross")

    @crosshair_style.setter
    def crosshair_style(self, val: str) -> None:
        self.set_value("crosshair_style", val)

    @property
    def crosshair_color(self) -> str:
        return self.get_string("crosshair_color", "#00FF00")

    @crosshair_color.setter
    def crosshair_color(self, val: str) -> None:
        self.set_value("crosshair_color", val)

    @property
    def crosshair_size(self) -> int:
        return self.get_int("crosshair_size", 20)

    @crosshair_size.setter
    def crosshair_size(self, val: int) -> None:
        self.set_value("crosshair_size", val)

    @property
    def crosshair_opacity(self) -> int:
        return self.get_int("crosshair_opacity", 100)

    @crosshair_opacity.setter
    def crosshair_opacity(self, val: int) -> None:
        self.set_value("crosshair_opacity", val)

    @property
    def crosshair_thickness(self) -> int:
        return self.get_int("crosshair_thickness", 2)

    @crosshair_thickness.setter
    def crosshair_thickness(self, val: int) -> None:
        self.set_value("crosshair_thickness", val)

    @property
    def crosshair_custom_path(self) -> str:
        return self.get_string("crosshair_custom_path", "")

    @crosshair_custom_path.setter
    def crosshair_custom_path(self, val: str) -> None:
        self.set_value("crosshair_custom_path", val)

    @property
    def gpu_core_offset(self) -> int:
        return self.get_int("gpu_core_offset", 0)

    @gpu_core_offset.setter
    def gpu_core_offset(self, val: int) -> None:
        self.set_value("gpu_core_offset", val)

    @property
    def gpu_mem_offset(self) -> int:
        return self.get_int("gpu_mem_offset", 0)

    @gpu_mem_offset.setter
    def gpu_mem_offset(self, val: int) -> None:
        self.set_value("gpu_mem_offset", val)

    @property
    def gpu_power_limit(self) -> float:
        return self.get_float("gpu_power_limit", 100.0)

    @gpu_power_limit.setter
    def gpu_power_limit(self, val: float) -> None:
        self.set_value("gpu_power_limit", val)

    @property
    def gpu_temp_limit(self) -> int:
        return self.get_int("gpu_temp_limit", 83)

    @gpu_temp_limit.setter
    def gpu_temp_limit(self, val: int) -> None:
        self.set_value("gpu_temp_limit", val)

    @property
    def gpu_voltage(self) -> int:
        return self.get_int("gpu_voltage", 0)

    @gpu_voltage.setter
    def gpu_voltage(self, val: int) -> None:
        self.set_value("gpu_voltage", val)

    @property
    def gpu_apply_on_startup(self) -> bool:
        return self.get_bool("gpu_apply_on_startup", False)

    @gpu_apply_on_startup.setter
    def gpu_apply_on_startup(self, val: bool) -> None:
        self.set_value("gpu_apply_on_startup", val)
