# -*- coding: utf-8 -*-
from core_commander.core.tweaks.base import BaseTweak, TweakRegistry
from core_commander.core.system_tweaks import SystemTweaksService
from core_commander.utils.logger import logger

@TweakRegistry.register
class NvmeOptimizationTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_nvme_optimization"

    def apply(self, enable: bool) -> bool:
        try:
            SystemTweaksService.apply_nvme_optimization(enable)
            return True
        except Exception as e:
            logger.error(f"Failed to apply {self.id}: {str(e)}")
            return False

@TweakRegistry.register
class RamOptimizationTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_ram_optimization"

    def apply(self, enable: bool) -> bool:
        try:
            SystemTweaksService.apply_ram_optimization(enable)
            return True
        except Exception as e:
            logger.error(f"Failed to apply {self.id}: {str(e)}")
            return False

@TweakRegistry.register
class MemoryCompressionTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "disable_memory_compression"

    def apply(self, enable: bool) -> bool:
        try:
            SystemTweaksService.apply_memory_compression_tweak(enable)
            return True
        except Exception as e:
            logger.error(f"Failed to apply {self.id}: {str(e)}")
            return False

@TweakRegistry.register
class ConfigAllocTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_config_alloc_tweak"

    def apply(self, enable: bool) -> bool:
        try:
            SystemTweaksService.apply_config_alloc_tweak(enable)
            return True
        except Exception as e:
            logger.error(f"Failed to apply {self.id}: {str(e)}")
            return False


@TweakRegistry.register
class StorageMsiTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_storage_msi_tweak"

    def apply(self, enable: bool) -> bool:
        try:
            from core_commander.utils.device import get_pci_device_ids
            import winreg
            scsi_ids = get_pci_device_ids("SCSIAdapter")
            if not scsi_ids:
                logger.warning("No storage controller PCI device paths found for MSI tweak.")
                return False

            applied_count = 0
            for sid in scsi_ids:
                sub_path = f"SYSTEM\\CurrentControlSet\\Enum\\{sid}\\Device Parameters\\Interrupt Management\\MessageSignaledInterruptProperties"
                if enable:
                    SystemTweaksService.backup_registry_value("HKLM", sub_path, "MSISupported")
                    SystemTweaksService.backup_registry_value("HKLM", sub_path, "Priority")
                    try:
                        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, sub_path, 0, winreg.KEY_WRITE) as key:
                            winreg.SetValueEx(key, "MSISupported", 0, winreg.REG_DWORD, 1)
                            winreg.SetValueEx(key, "Priority", 0, winreg.REG_DWORD, 3)
                        logger.info(f"Storage Controller {sid} MSI mode and High priority applied.")
                        applied_count += 1
                    except Exception as e:
                        logger.error(f"Failed to set MSI registry for Storage Controller {sid}: {str(e)}")
                else:
                    SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, "MSISupported", 1, winreg.REG_DWORD)
                    SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, "Priority", None, winreg.REG_DWORD)
                    applied_count += 1
            return applied_count > 0
        except Exception as e:
            logger.error(f"Failed to apply Storage MSI tweak: {str(e)}")
            return False

