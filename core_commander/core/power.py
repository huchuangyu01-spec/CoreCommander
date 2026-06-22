# -*- coding: utf-8 -*-
import subprocess  # nosec
import re
from core_commander.utils.logger import logger
from core_commander.core.system_tweaks import SystemTweaksService

class PowerService:
    _original_scheme_guid = None

    @staticmethod
    def backup_active_power_scheme():
        """
        Backs up the currently active power scheme GUID.
        """
        # 1. Try ctypes API
        try:
            from core_commander.core.system_tweaks import HAS_POWER_API, powrprof, POWER_GUID, kernel32
            import ctypes
            if HAS_POWER_API:
                p_guid = ctypes.POINTER(POWER_GUID)()
                ret = powrprof.PowerGetActiveScheme(None, ctypes.byref(p_guid))
                if ret == 0 and p_guid:
                    try:
                        guid = str(p_guid.contents.to_py_uuid())
                        PowerService._original_scheme_guid = guid
                        logger.info(f"Successfully backed up active power plan: {guid} via ctypes")
                    finally:
                        kernel32.LocalFree(p_guid)
                    return
        except Exception as e:
            logger.debug(f"Ctypes backup active power scheme failed: {str(e)}")

        # 2. Try registry direct read
        try:
            import winreg
            key_path = r"SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                active_guid, _ = winreg.QueryValueEx(key, "ActivePowerScheme")
                PowerService._original_scheme_guid = active_guid
                logger.info(f"Successfully backed up active power plan: {active_guid} via registry")
                return
        except Exception as e:
            logger.debug(f"Registry backup active power scheme failed: {str(e)}")

        # 3. Fallback to powercfg command
        try:
            output_bytes = SystemTweaksService.safe_subprocess_check_output(
                ["powercfg", "/getactivescheme"], 
                timeout=10
            )
            output = SystemTweaksService.decode_output(output_bytes)
            match = re.search(r"([a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12})", output)
            if match:
                guid = match.group(1).strip()
                PowerService._original_scheme_guid = guid
                logger.info(f"Successfully backed up active power plan: {guid} via powercfg")
        except Exception as e:
            logger.error(f"Failed to backup active power scheme: {str(e)}")

    @staticmethod
    def restore_original_power_scheme() -> bool:
        """
        Restores the active power scheme to the backed-up GUID, falling back to Balanced.
        """
        guid = PowerService._original_scheme_guid or "381b4222-f694-41f0-9685-ff5bb260df2e"
        logger.info(f"Restoring active power plan back to: {guid}")
        
        # 1. Try ctypes API
        try:
            from core_commander.core.system_tweaks import HAS_POWER_API, powrprof, POWER_GUID
            import ctypes
            if HAS_POWER_API:
                g = POWER_GUID.from_str(guid)
                ret = powrprof.PowerSetActiveScheme(None, ctypes.byref(g))
                if ret == 0:
                    logger.info(f"Active power plan switched to {guid} via ctypes.")
                    return True
        except Exception as e:
            logger.warning(f"Ctypes PowerSetActiveScheme failed: {str(e)}")
            
        # 2. Fallback to powercfg
        try:
            res = SystemTweaksService.safe_subprocess_call(
                ["powercfg", "/setactive", guid], 
                timeout=10
            )
            return res == 0
        except Exception as e:
            logger.error(f"Failed to restore original power plan: {str(e)}")
        return False

    @staticmethod
    def get_existing_power_plans() -> list:
        """
        Queries system power schemes and returns a list of tuples (guid_str, name).
        Uses ctypes API with a fallback to powercfg command.
        """
        plans = []
        # 1. Try ctypes API
        try:
            from core_commander.core.system_tweaks import HAS_POWER_API, powrprof, POWER_GUID
            import ctypes
            if HAS_POWER_API:
                index = 0
                ACCESS_SCHEME = 16
                while True:
                    g = POWER_GUID()
                    size = ctypes.c_ulong(ctypes.sizeof(POWER_GUID))
                    ret = powrprof.PowerEnumerate(None, None, None, ACCESS_SCHEME, index, ctypes.byref(g), ctypes.byref(size))
                    if ret == 259: # ERROR_NO_MORE_ITEMS
                        break
                    elif ret == 0:
                        guid_str = str(g.to_py_uuid())
                        name = ""
                        name_size = ctypes.c_ulong(0)
                        powrprof.PowerReadFriendlyName(None, ctypes.byref(g), None, None, None, ctypes.byref(name_size))
                        if name_size.value > 0:
                            buf = ctypes.create_unicode_buffer(name_size.value)
                            if powrprof.PowerReadFriendlyName(None, ctypes.byref(g), None, None, buf, ctypes.byref(name_size)) == 0:
                                name = buf.value
                        plans.append((guid_str, name))
                        logger.debug(f"Found plan via ctypes: {name} (GUID: {guid_str})")
                        index += 1
                    else:
                        break
        except Exception as enum_err:
            logger.debug(f"PowerEnumerate ctypes failed: {str(enum_err)}")
            plans = []

        # 2. Fallback to powercfg if ctypes failed or returned nothing
        if not plans:
            try:
                logger.info("Querying system power plans via powercfg...")
                output_bytes = SystemTweaksService.safe_subprocess_check_output(
                    ["powercfg", "/list"], 
                    timeout=10
                )
                output = SystemTweaksService.decode_output(output_bytes)
                
                # Extract GUIDs and names via language-independent regex
                pattern = re.compile(r"([a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12})\s+\((.+)\)")
                for line in output.split('\n'):
                    match = pattern.search(line)
                    if match:
                        guid = match.group(1).strip()
                        name = match.group(2).strip()
                        plans.append((guid, name))
                        logger.debug(f"Found plan via powercfg: {name} (GUID: {guid})")
            except Exception as e:
                logger.error(f"Fallback powercfg /list failed: {str(e)}")
                
        return plans

    @staticmethod
    def set_high_performance_plan() -> bool:
        """
        Queries system power schemes and sets the active plan to "Ultimate Performance" 
        (卓越性能) or "High Performance" (高性能).
        Returns True if a performance plan was set active, False otherwise.
        """
        # Backup active scheme before modifications
        PowerService.backup_active_power_scheme()
        try:
            plans = PowerService.get_existing_power_plans()
            target_guid = None
            target_name = None
            
            # 1. First choice: standard Ultimate Performance GUID (e9a42b02-d5df-448d-aa00-03f14749eb61)
            for guid, name in plans:
                if guid.lower() == "e9a42b02-d5df-448d-aa00-03f14749eb61":
                    target_guid = guid
                    target_name = name
                    break
                    
            # 2. Second choice: standard High Performance GUID (8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c)
            if not target_guid:
                for guid, name in plans:
                    if guid.lower() == "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c":
                        target_guid = guid
                        target_name = name
                        break
                        
            # 3. Third choice: fallback name matching for custom/OEM plans
            if not target_guid:
                for guid, name in plans:
                    name_upper = name.upper()
                    if "卓越性能" in name or "ULTIMATE" in name_upper:
                        target_guid = guid
                        target_name = name
                        break
            if not target_guid:
                for guid, name in plans:
                    name_upper = name.upper()
                    if "高性能" in name or "HIGH PERFORMANCE" in name_upper or "HÖCHSTLEISTUNG" in name_upper or "RENDIMIENTO" in name_upper:
                        target_guid = guid
                        target_name = name
                        break
                        
            if target_guid:
                logger.info(f"Setting active power plan to: {target_name} ({target_guid})")
                
                # Try ctypes to switch active plan
                switched = False
                from core_commander.core.system_tweaks import HAS_POWER_API, powrprof, POWER_GUID
                import ctypes
                if HAS_POWER_API:
                    try:
                        g = POWER_GUID.from_str(target_guid)
                        ret = powrprof.PowerSetActiveScheme(None, ctypes.byref(g))
                        if ret == 0:
                            logger.info("Successfully activated performance power plan via ctypes.")
                            switched = True
                    except Exception as set_err:
                        logger.warning(f"Ctypes PowerSetActiveScheme failed: {str(set_err)}")
                        
                if not switched:
                    res = SystemTweaksService.safe_subprocess_call(
                        ["powercfg", "/setactive", target_guid], 
                        timeout=10
                    )
                    if res == 0:
                        logger.info("Successfully activated performance power plan via powercfg.")
                        switched = True
                    else:
                        logger.error(f"powercfg returned non-zero code {res} when switching plan.")
                
                return switched
            else:
                logger.warning("No performance power plans (Ultimate/High Performance) found on this machine.")
                
        except Exception as e:
            logger.error(f"Failed to set high performance power plan: {str(e)}")
            
        return False

    @staticmethod
    def tune_cpu_hardware_parameters(disable_parking: bool, max_performance_epp: bool) -> bool:
        """
        Tunes core parking and EPP (Energy Performance Preference) parameters.
        Applies changes to current active scheme as well as standard Power Scheme GUIDs for persistence.
        """
        try:
            changed = False
            # Get existing power plans to avoid writing to non-existent ones
            existing_plans = PowerService.get_existing_power_plans()
            existing_guids = {guid.lower() for guid, _ in existing_plans}
            
            schemes = ["SCHEME_CURRENT"]
            for target in ["381b4222-f694-41f0-9685-ff5bb260df2e", "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c", "e9a42b02-d5df-448d-aa00-03f14749eb61"]:
                if target.lower() in existing_guids:
                    schemes.append(target)
            
            logger.info(f"Tuning CPU power parameters for schemes: {schemes}")
            
            subgroup = "54533251-82be-4824-96c1-47b60b740d00" # SUB_PROCESSOR
            cpmincores = "0cc5b647-c1df-4637-891a-dec35c318583"
            cpmaxcores = "ea062031-0e34-4ff1-9b6d-eb1059334028"
            perfepp = "36687f9e-e3a5-4dbf-b1dc-15eb381c6863"
            
            # 1. Core Parking
            from core_commander.core.topology import TopologyEngine
            if disable_parking:
                if TopologyEngine.is_amd_dual_ccd_vcache():
                    logger.info("AMD Dual-CCD 3D V-Cache CPU detected. Forcing Core Parking to remain enabled (disable_parking=False) for optimal scheduling.")
                    disable_parking = False

            if disable_parking:
                for scheme in schemes:
                    SystemTweaksService.set_power_setting_value(scheme, subgroup, cpmincores, 100)
                    SystemTweaksService.set_power_setting_value(scheme, subgroup, cpmaxcores, 100)
                changed = True
            else:
                # Restore default minimum cores (Balanced = 5%, High Perf = 10%, Ultimate = 100%)
                if "381b4222-f694-41f0-9685-ff5bb260df2e" in schemes:
                    SystemTweaksService.set_power_setting_value("381b4222-f694-41f0-9685-ff5bb260df2e", subgroup, cpmincores, 5)
                    SystemTweaksService.set_power_setting_value("381b4222-f694-41f0-9685-ff5bb260df2e", subgroup, cpmaxcores, 100)
                if "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c" in schemes:
                    SystemTweaksService.set_power_setting_value("8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c", subgroup, cpmincores, 10)
                    SystemTweaksService.set_power_setting_value("8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c", subgroup, cpmaxcores, 100)
                if "e9a42b02-d5df-448d-aa00-03f14749eb61" in schemes:
                    SystemTweaksService.set_power_setting_value("e9a42b02-d5df-448d-aa00-03f14749eb61", subgroup, cpmincores, 100)
                    SystemTweaksService.set_power_setting_value("e9a42b02-d5df-448d-aa00-03f14749eb61", subgroup, cpmaxcores, 100)
                changed = True

            # 2. EPP (Energy Performance Preference)
            if max_performance_epp:
                for scheme in schemes:
                    SystemTweaksService.set_power_setting_value(scheme, subgroup, perfepp, 0)
                changed = True
            else:
                # Restore default Energy Performance Preference (Balanced = 33, High Perf = 33, Ultimate = 0)
                if "381b4222-f694-41f0-9685-ff5bb260df2e" in schemes:
                    SystemTweaksService.set_power_setting_value("381b4222-f694-41f0-9685-ff5bb260df2e", subgroup, perfepp, 33)
                if "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c" in schemes:
                    SystemTweaksService.set_power_setting_value("8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c", subgroup, perfepp, 33)
                if "e9a42b02-d5df-448d-aa00-03f14749eb61" in schemes:
                    SystemTweaksService.set_power_setting_value("e9a42b02-d5df-448d-aa00-03f14749eb61", subgroup, perfepp, 0)
                changed = True
                
            if changed:
                # Reload active scheme by calling PowerSetActiveScheme or powercfg /setactive
                switched = False
                from core_commander.core.system_tweaks import HAS_POWER_API, powrprof, POWER_GUID, kernel32
                import ctypes
                if HAS_POWER_API:
                    try:
                        p_guid = ctypes.POINTER(POWER_GUID)()
                        ret = powrprof.PowerGetActiveScheme(None, ctypes.byref(p_guid))
                        if ret == 0 and p_guid:
                            try:
                                ret_act = powrprof.PowerSetActiveScheme(None, p_guid)
                                if ret_act == 0:
                                    switched = True
                            finally:
                                kernel32.LocalFree(p_guid)
                    except Exception as e:
                        logger.warning(f"Ctypes active scheme reload failed: {e}")
                
                if not switched:
                    SystemTweaksService.safe_subprocess_call(["powercfg", "/setactive", "SCHEME_CURRENT"], timeout=10)
                
                logger.info("CPU power parameters applied successfully.")
                return True
        except Exception as e:
            logger.error(f"Failed to tune CPU power parameters: {str(e)}")
        return False

    @staticmethod
    def optimize_system_network_latency(enable_network_tweak: bool) -> bool:
        """
        Disables Nagle's algorithm and network throttling via Windows Registry,
        or restores defaults if enable_network_tweak is False.
        """
        import winreg
        from core_commander.core.system_tweaks import SystemTweaksService

        profile_path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"
        interfaces_path = r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces"

        if not enable_network_tweak:
            try:
                logger.info("Restoring system network latency parameters to defaults...")
                
                # 1. SystemProfile Multimedia defaults
                try:
                    SystemTweaksService.restore_registry_value_or_default("HKLM", profile_path, "NetworkThrottlingIndex", 10, winreg.REG_DWORD)
                    SystemTweaksService.restore_registry_value_or_default("HKLM", profile_path, "SystemResponsiveness", 20, winreg.REG_DWORD)
                    logger.info("SystemProfile throttling and responsiveness settings restored.")
                except Exception as ex:
                    logger.warning(f"Failed to restore SystemProfile registry settings: {str(ex)}")
                    
                # 2. Tcpip Interfaces Low-Latency Nagle tweaks removal
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, interfaces_path, 0, winreg.KEY_READ) as parent_key:
                        i = 0
                        while True:
                            try:
                                subkey_name = winreg.EnumKey(parent_key, i)
                                sub_path = f"{interfaces_path}\\{subkey_name}"
                                SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, "TcpAckFrequency", None, winreg.REG_DWORD)
                                SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, "TCPNoDelay", None, winreg.REG_DWORD)
                                i += 1
                            except OSError:
                                break
                    logger.info(f"TCP latency settings reverted for {i} interfaces.")
                    return True
                except Exception as ex:
                    logger.warning(f"Failed to restore Tcpip interfaces registry settings: {str(ex)}")
            except Exception as e:
                logger.error(f"Failed to restore network latency defaults: {str(e)}")
            return False
            
        try:
            logger.info("Applying system network latency optimizations to registry...")
            
            # 1. SystemProfile Multimedia tweaks
            try:
                # Backup first
                SystemTweaksService.backup_registry_value("HKLM", profile_path, "NetworkThrottlingIndex")
                SystemTweaksService.backup_registry_value("HKLM", profile_path, "SystemResponsiveness")
                
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, profile_path, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "NetworkThrottlingIndex", 0, winreg.REG_DWORD, 0xffffffff)
                    winreg.SetValueEx(key, "SystemResponsiveness", 0, winreg.REG_DWORD, 0)
                logger.info("SystemProfile throttling and responsiveness settings optimized.")
            except Exception as ex:
                logger.warning(f"Failed to write SystemProfile registry settings: {str(ex)}")
                
            # 2. Tcpip Interfaces Low-Latency Nagle tweaks
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, interfaces_path, 0, winreg.KEY_READ) as parent_key:
                    i = 0
                    while True:
                        try:
                            subkey_name = winreg.EnumKey(parent_key, i)
                            sub_path = f"{interfaces_path}\\{subkey_name}"
                            
                            # Backup first
                            SystemTweaksService.backup_registry_value("HKLM", sub_path, "TcpAckFrequency")
                            SystemTweaksService.backup_registry_value("HKLM", sub_path, "TCPNoDelay")
                            
                            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub_path, 0, winreg.KEY_WRITE) as sub_key:
                                winreg.SetValueEx(sub_key, "TcpAckFrequency", 0, winreg.REG_DWORD, 1)
                                winreg.SetValueEx(sub_key, "TCPNoDelay", 0, winreg.REG_DWORD, 1)
                            i += 1
                        except OSError:
                            break
                logger.info(f"TCP low-latency (Nagle algorithm) settings applied to {i} interfaces.")
                return True
            except Exception as ex:
                logger.warning(f"Failed to write Tcpip interfaces registry settings: {str(ex)}")
                
        except Exception as e:
            logger.error(f"Failed to optimize network latency: {str(e)}")
        return False
