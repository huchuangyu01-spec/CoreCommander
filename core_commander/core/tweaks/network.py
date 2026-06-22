# -*- coding: utf-8 -*-
import winreg
import psutil
from core_commander.core.tweaks.base import BaseTweak, TweakRegistry
from core_commander.core.system_tweaks import SystemTweaksService
from core_commander.utils.logger import logger

@TweakRegistry.register
class UltimateNetworkTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_ultimate_network_tweak"

    def apply(self, enable: bool) -> bool:
        try:
            path_sp = r"SYSTEM\CurrentControlSet\Services\Tcpip\ServiceProvider"
            path_task = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile\Tasks\Games"
            path_sr = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"
            path_tcp = r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters"
            path_ndis = r"SYSTEM\CurrentControlSet\Services\NDIS\Parameters"
            path_lm = r"SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters"
            path_mm = r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management"
            path_class = r"SYSTEM\CurrentControlSet\Control\Class\{4D36E972-E325-11CE-BFC1-08002BE10318}"
            path_afd = r"SYSTEM\CurrentControlSet\Services\Afd\Parameters"
            
            afd_keys = [
                "DynamicSendBufferDisable", "FastSendDatagramThreshold", "DefaultSendWindow", "DefaultReceiveWindow",
                "MaxFastTransmit", "MaxFastCopyTransmit", "FastCopyReceiveThreshold", "PriorityBoost",
                "EnableDynamicBacklog", "MinimumDynamicBacklog", "MaximumDynamicBacklog", "DynamicBacklogGrowthDelta",
                "SendWindowSize", "ReceiveWindowSize", "ReceivePostsLowWater", "ReceivePostsHighWater",
                "LargeBufferSize", "MediumBufferSize"
            ]
            
            lm_keys = [
                "MaxThreadsPerQueue", "MaxCmds", "MaxFreeConnections", "MinFreeConnections", "MaxWorkItems",
                "MaxRawWorkItems", "MaxFreeWorkItems", "MaxMpxCt", "Smb2CreditsMin", "Smb2CreditsMax",
                "DisableBandwidthThrottling", "MaxSessionTableSize", "EnableOplocks", "MaxPagedMemoryUsage",
                "MaxNonPagedMemoryUsage", "EnableLargeBufferTransfers", "IdleThreadTimeout", "AutoShareServer",
                "DisableLargeMtu"
            ]
            
            mm_keys = [
                "LargeSystemCache", "IOPageLockLimit", "DisablePagingExecutive", "SecondLevelDataCache",
                "ClearPageFileAtShutdown", "LargePageMinimum", "PoolUsageMaximum"
            ]
            
            sr_keys = [
                "SystemResponsiveness", "NetworkThrottlingIndex", "AlwaysOn", "NoLazyMode", "LazyModeTimeout", "ExecuteQueueBoost"
            ]
            
            if not enable:
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_sp, "Class", 8, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_sp, "DnsPriority", 2000, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_sp, "HostsPriority", 500, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_sp, "LocalPriority", 499, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_sp, "NetbtPriority", 2001, winreg.REG_DWORD)
                
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_task, "GPU Priority", 8, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_task, "Priority", 2, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_task, "Scheduling Category", "Interactive", winreg.REG_SZ)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_task, "SFIO Priority", "Normal", winreg.REG_SZ)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_task, "Latency Sensitive", "True", winreg.REG_SZ)
                
                for v in sr_keys:
                    SystemTweaksService.restore_registry_value_or_default("HKLM", path_sr, v, None if v not in ("SystemResponsiveness", "NetworkThrottlingIndex") else (20 if v == "SystemResponsiveness" else 10), winreg.REG_DWORD)
                
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_tcp, "TcpNoDelay", None, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_tcp, "TcpAckFrequency", None, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_tcp, "TcpDelAckTicks", None, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_tcp, "TCPWindowSize", None, winreg.REG_DWORD)
                
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces", 0, winreg.KEY_READ) as root_key:
                        i = 0
                        while True:
                            try:
                                sub = winreg.EnumKey(root_key, i)
                                sub_path = f"SYSTEM\\CurrentControlSet\\Services\\Tcpip\\Parameters\\Interfaces\\{sub}"
                                SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, "TcpNoDelay", None, winreg.REG_DWORD)
                                SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, "TcpAckFrequency", None, winreg.REG_DWORD)
                                SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, "TcpDelAckTicks", None, winreg.REG_DWORD)
                                SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, "TCPWindowSize", None, winreg.REG_DWORD)
                                i += 1
                            except OSError:
                                break
                except Exception:  # nosec
                    pass
                    
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_ndis, "MaxNumRssCpus", None, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_ndis, "RssBaseCpu", None, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_ndis, "RssMaxProcNumber", None, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_ndis, "EnableRSS", 1, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_ndis, "MaxNumRssQueues", None, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_ndis, "RssAlgorithm", None, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_ndis, "EnableTCPChimney", 0, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_ndis, "EnableTCPTaskOffload", 1, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_ndis, "EnableIPsecTaskOffload", 1, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_ndis, "EnableLsoV2IPv4", 1, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path_ndis, "EnableLsoV2IPv6", 1, winreg.REG_DWORD)
                
                for v in lm_keys:
                    SystemTweaksService.restore_registry_value_or_default("HKLM", path_lm, v, None, winreg.REG_DWORD)
                    
                for v in mm_keys:
                    SystemTweaksService.restore_registry_value_or_default("HKLM", path_mm, v, 0 if v == "LargeSystemCache" else None, winreg.REG_DWORD)
                    
                for v in afd_keys:
                    SystemTweaksService.restore_registry_value_or_default("HKLM", path_afd, v, None, winreg.REG_DWORD)
                
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_class, 0, winreg.KEY_READ) as root_key:
                        i = 0
                        while True:
                            try:
                                sub = winreg.EnumKey(root_key, i)
                                if sub.isdigit():
                                    sub_path = f"{path_class}\\{sub}"
                                    for v in ["*FlowControl", "*InterruptModeration", "*PriorityVLANTag", "*ReceiveBuffers", 
                                              "*TransmitBuffers", "*EEE", "*WakeOnMagicPacket", "*WakeOnPattern", "*RSS", "*NumRssQueues"]:
                                        SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, v, None, winreg.REG_SZ)
                                i += 1
                            except OSError:
                                break
                except Exception:  # nosec
                    pass
                return True

            logger.info("Applying DNS/Hosts and system responsive profile optimizations...")
            for v in ["Class", "DnsPriority", "HostsPriority", "LocalPriority", "NetbtPriority"]:
                SystemTweaksService.backup_registry_value("HKLM", path_sp, v)
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_sp, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "Class", 0, winreg.REG_DWORD, 8)
                    winreg.SetValueEx(key, "DnsPriority", 0, winreg.REG_DWORD, 6)
                    winreg.SetValueEx(key, "HostsPriority", 0, winreg.REG_DWORD, 5)
                    winreg.SetValueEx(key, "LocalPriority", 0, winreg.REG_DWORD, 4)
                    winreg.SetValueEx(key, "NetbtPriority", 0, winreg.REG_DWORD, 7)
            except Exception as e:
                logger.debug(f"Tcpip service provider write failed: {str(e)}")

            for v in ["GPU Priority", "Priority", "Scheduling Category", "SFIO Priority", "Latency Sensitive"]:
                SystemTweaksService.backup_registry_value("HKLM", path_task, v)
            try:
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_task, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "GPU Priority", 0, winreg.REG_DWORD, 8)
                    winreg.SetValueEx(key, "Priority", 0, winreg.REG_DWORD, 6)
                    winreg.SetValueEx(key, "Scheduling Category", 0, winreg.REG_SZ, "High")
                    winreg.SetValueEx(key, "SFIO Priority", 0, winreg.REG_SZ, "High")
                    winreg.SetValueEx(key, "Latency Sensitive", 0, winreg.REG_SZ, "True")
            except Exception as e:
                logger.debug(f"Multimedia Games task write failed: {str(e)}")

            for v in sr_keys:
                SystemTweaksService.backup_registry_value("HKLM", path_sr, v)
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_sr, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "SystemResponsiveness", 0, winreg.REG_DWORD, 0)
                    winreg.SetValueEx(key, "NetworkThrottlingIndex", 0, winreg.REG_DWORD, 0xffffffff)
                    winreg.SetValueEx(key, "AlwaysOn", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(key, "NoLazyMode", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(key, "LazyModeTimeout", 0, winreg.REG_DWORD, 0xffffffff)
                    winreg.SetValueEx(key, "ExecuteQueueBoost", 0, winreg.REG_DWORD, 0xffffffff)
            except Exception as e:
                logger.debug(f"SystemProfile response write failed: {str(e)}")

            for v in ["TcpNoDelay", "TcpAckFrequency", "TcpDelAckTicks", "TCPWindowSize"]:
                SystemTweaksService.backup_registry_value("HKLM", path_tcp, v)
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_tcp, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "TcpNoDelay", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(key, "TcpAckFrequency", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(key, "TcpDelAckTicks", 0, winreg.REG_DWORD, 0)
                    winreg.SetValueEx(key, "TCPWindowSize", 0, winreg.REG_DWORD, 0x40000)
            except Exception as e:
                logger.debug(f"Tcpip parameters write failed: {str(e)}")

            path_intf = r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces"
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_intf, 0, winreg.KEY_READ) as root_key:
                    i = 0
                    while True:
                        try:
                            sub = winreg.EnumKey(root_key, i)
                            sub_path = f"{path_intf}\\{sub}"
                            for v in ["TcpNoDelay", "TcpAckFrequency", "TcpDelAckTicks", "TCPWindowSize"]:
                                SystemTweaksService.backup_registry_value("HKLM", sub_path, v)
                            try:
                                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub_path, 0, winreg.KEY_WRITE) as k:
                                    winreg.SetValueEx(k, "TcpNoDelay", 0, winreg.REG_DWORD, 1)
                                    winreg.SetValueEx(k, "TcpAckFrequency", 0, winreg.REG_DWORD, 1)
                                    winreg.SetValueEx(k, "TcpDelAckTicks", 0, winreg.REG_DWORD, 0)
                                    winreg.SetValueEx(k, "TCPWindowSize", 0, winreg.REG_DWORD, 0x40000)
                            except Exception:  # nosec
                                pass
                            i += 1
                        except OSError:
                            break
            except Exception as e:
                logger.debug(f"Tcpip interfaces write failed: {str(e)}")

            for v in ["MaxNumRssCpus", "RssBaseCpu", "RssMaxProcNumber", "EnableRSS", "MaxNumRssQueues", 
                      "RssAlgorithm", "EnableTCPChimney", "EnableTCPTaskOffload", "EnableIPsecTaskOffload", 
                      "EnableLsoV2IPv4", "EnableLsoV2IPv6"]:
                SystemTweaksService.backup_registry_value("HKLM", path_ndis, v)
            try:
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_ndis, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "MaxNumRssCpus", 0, winreg.REG_DWORD, 0x20)
                    winreg.SetValueEx(key, "RssBaseCpu", 0, winreg.REG_DWORD, 0)
                    winreg.SetValueEx(key, "RssMaxProcNumber", 0, winreg.REG_DWORD, 0x3f)
                    winreg.SetValueEx(key, "EnableRSS", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(key, "MaxNumRssQueues", 0, winreg.REG_DWORD, 16)
                    winreg.SetValueEx(key, "RssAlgorithm", 0, winreg.REG_DWORD, 2)
                    winreg.SetValueEx(key, "EnableTCPChimney", 0, winreg.REG_DWORD, 0)
                    winreg.SetValueEx(key, "EnableTCPTaskOffload", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(key, "EnableIPsecTaskOffload", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(key, "EnableLsoV2IPv4", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(key, "EnableLsoV2IPv6", 0, winreg.REG_DWORD, 1)
            except Exception as e:
                logger.debug(f"NDIS parameters write failed: {str(e)}")

            for v in lm_keys:
                SystemTweaksService.backup_registry_value("HKLM", path_lm, v)
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_lm, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "MaxThreadsPerQueue", 0, winreg.REG_DWORD, 0x1000)
                    winreg.SetValueEx(key, "MaxCmds", 0, winreg.REG_DWORD, 0x10000)
                    winreg.SetValueEx(key, "MaxFreeConnections", 0, winreg.REG_DWORD, 0x1000)
                    winreg.SetValueEx(key, "MinFreeConnections", 0, winreg.REG_DWORD, 0x100)
                    winreg.SetValueEx(key, "MaxWorkItems", 0, winreg.REG_DWORD, 0x8000)
                    winreg.SetValueEx(key, "MaxRawWorkItems", 0, winreg.REG_DWORD, 0x4000)
                    winreg.SetValueEx(key, "MaxFreeWorkItems", 0, winreg.REG_DWORD, 0x2000)
                    winreg.SetValueEx(key, "MaxMpxCt", 0, winreg.REG_DWORD, 0x800)
                    winreg.SetValueEx(key, "Smb2CreditsMin", 0, winreg.REG_DWORD, 0x10000)
                    winreg.SetValueEx(key, "Smb2CreditsMax", 0, winreg.REG_DWORD, 0x20000)
                    winreg.SetValueEx(key, "DisableBandwidthThrottling", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(key, "MaxSessionTableSize", 0, winreg.REG_DWORD, 0x10000)
                    winreg.SetValueEx(key, "EnableOplocks", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(key, "MaxPagedMemoryUsage", 0, winreg.REG_DWORD, 0x0FFFFFFF)
                    winreg.SetValueEx(key, "MaxNonPagedMemoryUsage", 0, winreg.REG_DWORD, 0x0FFFFFFF)
                    winreg.SetValueEx(key, "EnableLargeBufferTransfers", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(key, "IdleThreadTimeout", 0, winreg.REG_DWORD, 0xFF00)
                    winreg.SetValueEx(key, "AutoShareServer", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(key, "DisableLargeMtu", 0, winreg.REG_DWORD, 0)
            except Exception as e:
                logger.debug(f"LanmanServer parameters write failed: {str(e)}")

            for v in mm_keys:
                SystemTweaksService.backup_registry_value("HKLM", path_mm, v)
            try:
                total_ram_gb = round(psutil.virtual_memory().total / (1024**3))
                disable_paging_val = 1 if total_ram_gb >= 16 else 0
                large_cache_val = 1 if total_ram_gb >= 32 else 0

                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_mm, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "LargeSystemCache", 0, winreg.REG_DWORD, large_cache_val)
                    winreg.SetValueEx(key, "IOPageLockLimit", 0, winreg.REG_DWORD, 0xf00000)
                    winreg.SetValueEx(key, "DisablePagingExecutive", 0, winreg.REG_DWORD, disable_paging_val)
                    winreg.SetValueEx(key, "SecondLevelDataCache", 0, winreg.REG_DWORD, 0x400)
                    winreg.SetValueEx(key, "ClearPageFileAtShutdown", 0, winreg.REG_DWORD, 0)
                    winreg.SetValueEx(key, "LargePageMinimum", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(key, "PoolUsageMaximum", 0, winreg.REG_DWORD, 0x60)
            except Exception as e:
                logger.debug(f"Memory Management cache write failed: {str(e)}")

            for v in afd_keys:
                SystemTweaksService.backup_registry_value("HKLM", path_afd, v)
            try:
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path_afd, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "DynamicSendBufferDisable", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(key, "FastSendDatagramThreshold", 0, winreg.REG_DWORD, 0)
                    winreg.SetValueEx(key, "DefaultSendWindow", 0, winreg.REG_DWORD, 0x20000)
                    winreg.SetValueEx(key, "DefaultReceiveWindow", 0, winreg.REG_DWORD, 0x20000)
                    winreg.SetValueEx(key, "MaxFastTransmit", 0, winreg.REG_DWORD, 0x10)
                    winreg.SetValueEx(key, "MaxFastCopyTransmit", 0, winreg.REG_DWORD, 0x10)
                    winreg.SetValueEx(key, "FastCopyReceiveThreshold", 0, winreg.REG_DWORD, 0x100)
                    winreg.SetValueEx(key, "PriorityBoost", 0, winreg.REG_DWORD, 0)
                    winreg.SetValueEx(key, "EnableDynamicBacklog", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(key, "MinimumDynamicBacklog", 0, winreg.REG_DWORD, 0x20)
                    winreg.SetValueEx(key, "MaximumDynamicBacklog", 0, winreg.REG_DWORD, 0x10000)
                    winreg.SetValueEx(key, "DynamicBacklogGrowthDelta", 0, winreg.REG_DWORD, 0x10)
                    winreg.SetValueEx(key, "SendWindowSize", 0, winreg.REG_DWORD, 0x40000)
                    winreg.SetValueEx(key, "ReceiveWindowSize", 0, winreg.REG_DWORD, 0x40000)
                    winreg.SetValueEx(key, "ReceivePostsLowWater", 0, winreg.REG_DWORD, 0x400)
                    winreg.SetValueEx(key, "ReceivePostsHighWater", 0, winreg.REG_DWORD, 0x1000)
                    winreg.SetValueEx(key, "LargeBufferSize", 0, winreg.REG_DWORD, 0x20000)
                    winreg.SetValueEx(key, "MediumBufferSize", 0, winreg.REG_DWORD, 0x8000)
                logger.info("AFD packet buffers and dynamic backlog applied successfully.")
            except Exception as e:
                logger.debug(f"AFD parameters write failed: {str(e)}")

            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path_class, 0, winreg.KEY_READ) as root_key:
                    i = 0
                    while True:
                        try:
                            sub = winreg.EnumKey(root_key, i)
                            if sub.isdigit():
                                sub_path = f"{path_class}\\{sub}"
                                for v in ["*FlowControl", "*InterruptModeration", "*PriorityVLANTag", "*ReceiveBuffers", 
                                          "*TransmitBuffers", "*EEE", "*WakeOnMagicPacket", "*WakeOnPattern", "*RSS", "*NumRssQueues"]:
                                    SystemTweaksService.backup_registry_value("HKLM", sub_path, v)
                                try:
                                    # Safely determine buffer size bounds
                                    rx_val = "2048"
                                    tx_val = "2048"
                                    try:
                                        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{sub_path}\\Ndi\\params\\*ReceiveBuffers", 0, winreg.KEY_READ) as rx_param_key:
                                            max_rx_str, _ = winreg.QueryValueEx(rx_param_key, "max")
                                            max_rx = int(max_rx_str)
                                            if max_rx < 2048:
                                                rx_val = str(max_rx)
                                    except Exception:
                                        pass
                                    try:
                                        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{sub_path}\\Ndi\\params\\*TransmitBuffers", 0, winreg.KEY_READ) as tx_param_key:
                                            max_tx_str, _ = winreg.QueryValueEx(tx_param_key, "max")
                                            max_tx = int(max_tx_str)
                                            if max_tx < 2048:
                                                tx_val = str(max_tx)
                                    except Exception:
                                        pass

                                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub_path, 0, winreg.KEY_WRITE) as k:
                                        winreg.SetValueEx(k, "*FlowControl", 0, winreg.REG_SZ, "0")
                                        winreg.SetValueEx(k, "*InterruptModeration", 0, winreg.REG_SZ, "0")
                                        winreg.SetValueEx(k, "*PriorityVLANTag", 0, winreg.REG_SZ, "1")
                                        winreg.SetValueEx(k, "*ReceiveBuffers", 0, winreg.REG_SZ, rx_val)
                                        winreg.SetValueEx(k, "*TransmitBuffers", 0, winreg.REG_SZ, tx_val)
                                        winreg.SetValueEx(k, "*EEE", 0, winreg.REG_SZ, "0")
                                        winreg.SetValueEx(k, "*WakeOnMagicPacket", 0, winreg.REG_SZ, "0")
                                        winreg.SetValueEx(k, "*WakeOnPattern", 0, winreg.REG_SZ, "0")
                                        winreg.SetValueEx(k, "*RSS", 0, winreg.REG_SZ, "1")
                                        winreg.SetValueEx(k, "*NumRssQueues", 0, winreg.REG_SZ, "16")
                                except Exception:  # nosec
                                    pass
                            i += 1
                        except OSError:
                            break
            except Exception as e:
                logger.debug(f"NIC Class adapters write failed: {str(e)}")
            return True
        except Exception as e:
            logger.error(f"Failed to apply/revert UltimateNetworkTweak: {str(e)}")
            return False

@TweakRegistry.register
class DnsTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_dns_tweak"

    def apply(self, enable: bool) -> bool:
        path = r"SYSTEM\CurrentControlSet\Services\Tcpip\ServiceProvider"
        try:
            if not enable:
                SystemTweaksService.restore_registry_value_or_default("HKLM", path, "Class", 8, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path, "DnsPriority", 2000, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path, "HostsPriority", 500, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path, "LocalPriority", 499, winreg.REG_DWORD)
                SystemTweaksService.restore_registry_value_or_default("HKLM", path, "NetbtPriority", 2001, winreg.REG_DWORD)
                return True
                
            logger.info("Applying DNS/Hosts resolution prioritization...")
            for v in ["Class", "DnsPriority", "HostsPriority", "LocalPriority", "NetbtPriority"]:
                SystemTweaksService.backup_registry_value("HKLM", path, v)
            
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, "Class", 0, winreg.REG_DWORD, 8)
                winreg.SetValueEx(key, "DnsPriority", 0, winreg.REG_DWORD, 6)
                winreg.SetValueEx(key, "HostsPriority", 0, winreg.REG_DWORD, 5)
                winreg.SetValueEx(key, "LocalPriority", 0, winreg.REG_DWORD, 4)
                winreg.SetValueEx(key, "NetbtPriority", 0, winreg.REG_DWORD, 7)
            logger.info("DNS/Hosts resolution prioritization applied.")
            return True
        except Exception as e:
            logger.error(f"Failed to apply DNS priority tweak: {str(e)}")
            return False

@TweakRegistry.register
class NetImodTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_net_imod_tweak"

    def apply(self, enable: bool) -> bool:
        path_class = r"SYSTEM\CurrentControlSet\Control\Class\{4D36E972-E325-11CE-BFC1-08002BE10318}"
        hkey = winreg.HKEY_LOCAL_MACHINE
        applied = 0
        try:
            with winreg.OpenKey(hkey, path_class, 0, winreg.KEY_READ) as root_key:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(root_key, i)
                        sub_path = f"{path_class}\\{sub}"
                        has_imod = False
                        try:
                            with winreg.OpenKey(hkey, sub_path, 0, winreg.KEY_READ) as sub_key:
                                winreg.QueryValueEx(sub_key, "*InterruptModeration")
                                has_imod = True
                        except FileNotFoundError:
                            pass
                            
                        if has_imod:
                            if enable:
                                SystemTweaksService.backup_registry_value("HKLM", sub_path, "*InterruptModeration")
                                with winreg.OpenKey(hkey, sub_path, 0, winreg.KEY_WRITE) as sub_key:
                                    winreg.SetValueEx(sub_key, "*InterruptModeration", 0, winreg.REG_SZ, "0")
                            else:
                                SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, "*InterruptModeration", "1", winreg.REG_SZ)
                            applied += 1
                        i += 1
                    except OSError:
                        break
            logger.info(f"Interrupt moderation tweak applied/reverted for {applied} adapters.")
            return True
        except Exception as e:
            logger.error(f"Failed to apply/revert interrupt moderation tweak: {str(e)}")
            return False

@TweakRegistry.register
class NetBindingsTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_net_bindings_tweak"

    def apply(self, enable: bool) -> bool:
        components = ["ms_server", "ms_pacer", "ms_lldp"]
        try:
            for comp in components:
                if enable:
                    SystemTweaksService.backup_net_bindings(comp)
            
            # Primary method: WMI COM
            has_com = False
            wmi_success = False
            try:
                import pythoncom
                import win32com.client
                import re
                pythoncom.CoInitialize()
                has_com = True
                wmi = win32com.client.GetObject("winmgmts:\\\\.\\root\\StandardCimv2")
                method_name = "Disable" if enable else "Enable"
                for comp in components:
                    comp_clean = re.sub(r'[^\w\-]', '', comp)
                    bindings = wmi.ExecQuery(f"SELECT * FROM MSFT_NetAdapterBindingSettingData WHERE ComponentID = '{comp_clean}'")
                    for b in bindings:
                        try:
                            b.ExecMethod_(method_name)
                        except Exception:
                            pass
                wmi_success = True
                logger.info(f"NetAdapter bindings tweak Applied={enable} executed successfully via WMI COM.")
            except Exception as wmi_err:
                logger.warning(f"WMI COM NetAdapter bindings tweak failed: {wmi_err}, falling back to PowerShell.")
            finally:
                if has_com:
                    pythoncom.CoUninitialize()

            # Fallback method: PowerShell subprocesses
            if not wmi_success:
                for comp in components:
                    action = "Disable" if enable else "Enable"
                    cmd = f'powershell -NoProfile -Command "{action}-NetAdapterBinding -Name * -ComponentID {comp} -ErrorAction SilentlyContinue"'
                    SystemTweaksService.safe_subprocess_call(cmd, shell=True)  # nosec
                logger.info(f"NetAdapter bindings tweak Applied={enable} executed successfully via PowerShell fallback.")
            return True
        except Exception as e:
            logger.error(f"Failed to apply/revert NetAdapter bindings tweak: {str(e)}")
            return False

@TweakRegistry.register
class TcpBbrTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_tcp_bbr_tweak"

    def apply(self, enable: bool) -> bool:
        try:
            if enable:
                SystemTweaksService.safe_subprocess_call(["netsh", "int", "ipv4", "set", "gl", "loopbacklargemtu=disable"], timeout=5)
                SystemTweaksService.safe_subprocess_call(["netsh", "int", "ipv6", "set", "gl", "loopbacklargemtu=disable"], timeout=5)
                
                cmd = ["netsh", "int", "tcp", "set", "supplemental", "template=internet", "congestionprovider=bbr"]
                res = SystemTweaksService.safe_subprocess_call(cmd, timeout=5)
                if res == 0:
                    logger.info("TCP BBR congestion provider applied successfully with loopback MTU bugfix.")
                    return True
                else:
                    logger.error(f"Netsh set bbr failed with code: {res}")
                    return False
            else:
                SystemTweaksService.safe_subprocess_call(["netsh", "int", "ipv4", "set", "gl", "loopbacklargemtu=enable"], timeout=5)
                SystemTweaksService.safe_subprocess_call(["netsh", "int", "ipv6", "set", "gl", "loopbacklargemtu=enable"], timeout=5)
                
                cmd = ["netsh", "int", "tcp", "set", "supplemental", "template=internet", "congestionprovider=cubic"]
                res = SystemTweaksService.safe_subprocess_call(cmd, timeout=5)
                if res == 0:
                    logger.info("TCP congestion provider restored to cubic, loopback MTU restored to default.")
                    return True
                else:
                    logger.error(f"Netsh restore cubic failed with code: {res}")
                    return False
        except Exception as e:
            logger.error(f"Failed to apply/revert TCP BBR tweak: {str(e)}")
            return False

@TweakRegistry.register
class EeeTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_eee_tweak"

    def apply(self, enable: bool) -> bool:
        path_class = r"SYSTEM\CurrentControlSet\Control\Class\{4D36E972-E325-11CE-BFC1-08002BE10318}"
        hkey = winreg.HKEY_LOCAL_MACHINE
        applied = 0
        try:
            with winreg.OpenKey(hkey, path_class, 0, winreg.KEY_READ) as root_key:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(root_key, i)
                        if sub.isdigit():
                            sub_path = f"{path_class}\\{sub}"
                            
                            is_physical = False
                            try:
                                with winreg.OpenKey(hkey, f"{sub_path}\\Ndi\\Interfaces", 0, winreg.KEY_READ) as intf_key:
                                    lower_range, _ = winreg.QueryValueEx(intf_key, "LowerRange")
                                    if "ethernet" in str(lower_range).lower():
                                        is_physical = True
                            except FileNotFoundError:
                                pass
                                
                            if is_physical:
                                keys_to_tweak = ["*EEE", "EEELink", "*EEELink", "*GigaLite", "*PowerSavingMode", "GreenEthernet", "GreenFeedback"]
                                for key_name in keys_to_tweak:
                                    exists = False
                                    val_type = winreg.REG_SZ
                                    try:
                                        with winreg.OpenKey(hkey, sub_path, 0, winreg.KEY_READ) as sub_key:
                                            _, val_type = winreg.QueryValueEx(sub_key, key_name)
                                            exists = True
                                    except FileNotFoundError:
                                        pass
                                    
                                    if exists:
                                        if enable:
                                            SystemTweaksService.backup_registry_value("HKLM", sub_path, key_name)
                                            with winreg.OpenKey(hkey, sub_path, 0, winreg.KEY_WRITE) as sub_key:
                                                if val_type == winreg.REG_DWORD:
                                                    winreg.SetValueEx(sub_key, key_name, 0, winreg.REG_DWORD, 0)
                                                else:
                                                    winreg.SetValueEx(sub_key, key_name, 0, winreg.REG_SZ, "0")
                                        else:
                                            default_val = "1"
                                            if val_type == winreg.REG_DWORD:
                                                SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, key_name, 1, winreg.REG_DWORD)
                                            else:
                                                SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, key_name, default_val, winreg.REG_SZ)
                                        applied += 1
                        i += 1
                    except OSError:
                        break
            logger.info(f"Ethernet power saving tweak applied/reverted for {applied} settings.")
            return True
        except Exception as e:
            logger.error(f"Failed to apply/revert Ethernet power saving tweak: {str(e)}")
            return False


@TweakRegistry.register
class EnableNetworkTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_network_tweak"

    def apply(self, enable: bool) -> bool:
        try:
            from core_commander.core.power import PowerService
            PowerService.optimize_system_network_latency(enable)
            return True
        except Exception as e:
            logger.error(f"Failed to apply {self.id}: {str(e)}")
            return False


@TweakRegistry.register
class NetworkMsiTweak(BaseTweak):
    @property
    def id(self) -> str:
        return "enable_network_msi_tweak"

    def apply(self, enable: bool) -> bool:
        try:
            from core_commander.utils.device import get_pci_device_ids
            import winreg
            net_ids = get_pci_device_ids("Net")
            if not net_ids:
                logger.warning("No network adapter PCI device paths found for MSI tweak.")
                return False

            applied_count = 0
            for nid in net_ids:
                sub_path = f"SYSTEM\\CurrentControlSet\\Enum\\{nid}\\Device Parameters\\Interrupt Management\\MessageSignaledInterruptProperties"
                if enable:
                    SystemTweaksService.backup_registry_value("HKLM", sub_path, "MSISupported")
                    SystemTweaksService.backup_registry_value("HKLM", sub_path, "Priority")
                    try:
                        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, sub_path, 0, winreg.KEY_WRITE) as key:
                            winreg.SetValueEx(key, "MSISupported", 0, winreg.REG_DWORD, 1)
                            winreg.SetValueEx(key, "Priority", 0, winreg.REG_DWORD, 3)
                        logger.info(f"Network Adapter {nid} MSI mode and High priority applied.")
                        applied_count += 1
                    except Exception as e:
                        logger.error(f"Failed to set MSI registry for Network Adapter {nid}: {str(e)}")
                else:
                    SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, "MSISupported", 1, winreg.REG_DWORD)
                    SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, "Priority", None, winreg.REG_DWORD)
                    applied_count += 1
            return applied_count > 0
        except Exception as e:
            logger.error(f"Failed to apply Network MSI tweak: {str(e)}")
            return False


