# -*- coding: utf-8 -*-
from core_commander.core.tweaks.base import BaseTweak, TweakRegistry
from core_commander.core.system_tweaks import SystemTweaksService
from core_commander.core.power import PowerService
from core_commander.core.topology import TopologyEngine
from core_commander.utils.logger import logger

@TweakRegistry.register
class DisablePciPowerTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "disable_pcipower"

    def apply(self, enable: bool) -> bool:
        try:
            SystemTweaksService.apply_pcipower_tweak(enable)
            return True
        except Exception as e:
            logger.error(f"Failed to apply {self.id}: {str(e)}")
            return False

@TweakRegistry.register
class EnableDevicePowerTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_device_power_tweak"

    def apply(self, enable: bool) -> bool:
        try:
            SystemTweaksService.apply_device_power_tweak(enable)
            return True
        except Exception as e:
            logger.error(f"Failed to apply {self.id}: {str(e)}")
            return False

@TweakRegistry.register
class EnablePowerThrottlingTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_power_throttling_tweak"

    def apply(self, enable: bool) -> bool:
        try:
            return SystemTweaksService.apply_power_throttling_tweak(enable)
        except Exception as e:
            logger.error(f"Failed to apply {self.id}: {str(e)}")
            return False

@TweakRegistry.register
class EnableCustomPowerPlanTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_custom_power_plan"

    def apply(self, enable: bool) -> bool:
        try:
            cpu_vendor = TopologyEngine.get_cpu_vendor()
            SystemTweaksService.apply_power_plan(enable, cpu_vendor)
            return True
        except Exception as e:
            logger.error(f"Failed to apply {self.id}: {str(e)}")
            return False

@TweakRegistry.register
class EnableCoreParkingTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_core_parking"

    def apply(self, enable: bool) -> bool:
        try:
            from core_commander.config.settings import AppSettings
            settings = AppSettings()
            enable_epp = settings.enable_epp_max
            return PowerService.tune_cpu_hardware_parameters(enable, enable_epp)
        except Exception as e:
            logger.error(f"Failed to apply {self.id}: {str(e)}")
            return False

@TweakRegistry.register
class EnableEppMaxTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_epp_max"

    def apply(self, enable: bool) -> bool:
        try:
            from core_commander.config.settings import AppSettings
            settings = AppSettings()
            enable_parking = settings.enable_core_parking
            return PowerService.tune_cpu_hardware_parameters(enable_parking, enable)
        except Exception as e:
            logger.error(f"Failed to apply {self.id}: {str(e)}")
            return False
