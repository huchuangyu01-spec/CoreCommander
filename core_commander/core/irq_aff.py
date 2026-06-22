# -*- coding: utf-8 -*-
import winreg
from core_commander.utils.logger import logger
from core_commander.utils.device import get_pci_device_ids
from core_commander.core.topology import TopologyEngine

class IrqAffinityService:
    """
    Advanced IRQ Affinity Separation Engine.
    Physically separates display GPU interrupts and network adapter interrupts on different CPU cores
    to eliminate hardware-level interrupt conflicts and latency spikes.
    """

    @classmethod
    def list_to_mask_and_group(cls, cpu_list: list) -> tuple:
        """
        Converts a list of logical processor IDs to (8-byte little-endian bitmask, group_id).
        Determines the group_id dynamically by selecting the group with the most core allocations
        to prevent invalid or zero-mask allocations on 64+ logical processor (multi-group) systems.
        """
        if not cpu_list:
            return (b'\x00' * 8, 0)
            
        # Count core allocations per processor group
        groups = {}
        for cpu in cpu_list:
            if isinstance(cpu, int) and cpu >= 0:
                gid = cpu // 64
                groups[gid] = groups.get(gid, 0) + 1
                
        if not groups:
            return (b'\x00' * 8, 0)
            
        # Select the group with the highest number of requested logical processors
        best_group = max(groups, key=groups.get)
        
        mask = 0
        for cpu in cpu_list:
            if isinstance(cpu, int) and cpu >= 0 and (cpu // 64 == best_group):
                relative_cpu = cpu % 64
                mask |= (1 << relative_cpu)
                
        return mask.to_bytes(8, byteorder='little'), best_group

    @classmethod
    def apply_separated_irq_affinity(cls, enable: bool) -> bool:
        """
        Calculates and applies physically segregated IRQ affinity policies for GPU, Network, and Storage adapters,
        or restores them to defaults if enable is False.
        """
        from core_commander.core.system_tweaks import SystemTweaksService
        try:
            # 1. Query display GPU device PNP IDs
            gpu_ids = get_pci_device_ids("Display")
            # 2. Query network adapter PNP IDs
            net_ids = get_pci_device_ids("Net")
            # 3. Query SCSIAdapter (NVMe controllers) PNP IDs
            storage_ids = get_pci_device_ids("SCSIAdapter")

            all_gpu = list(set(gpu_ids))
            all_net = list(set(net_ids))
            all_storage = list(set(storage_ids))

            if not all_gpu and not all_net and not all_storage:
                logger.warning("No display, network, or storage devices found for IRQ affinity separation.")
                return False

            if not enable:
                # Revert all devices to default
                logger.info("Restoring all GPU, Network, and Storage devices IRQ affinity settings to defaults...")
                reverted_count = 0
                for dev_id in all_gpu + all_net + all_storage:
                    sub_path = f"SYSTEM\\CurrentControlSet\\Enum\\{dev_id}\\Device Parameters\\Interrupt Management\\Affinity Policy"
                    SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, "DevicePolicy", None, winreg.REG_DWORD)
                    SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, "AssignmentSet", None, winreg.REG_BINARY)
                    SystemTweaksService.restore_registry_value_or_default("HKLM", sub_path, "DeviceGroup", None, winreg.REG_DWORD)
                    reverted_count += 1
                logger.info(f"Reverted IRQ affinity settings for {reverted_count} devices.")
                return True

            # Get CPU topology to plan separation
            topology = TopologyEngine.get_topology()
            if not topology:
                logger.error("Failed to query CPU topology. IRQ affinity separation aborted.")
                return False

            p_cores = [c for c in topology if c['type'] == 'P-Core']
            e_cores = [c for c in topology if c['type'] == 'E-Core']

            gpu_cpus = []
            net_storage_cpus = []

            # 1. Intel Hybrid (E-Cores present)
            if e_cores:
                if len(p_cores) > 0:
                    gpu_cpus.extend(p_cores[0]['threads'])
                else:
                    gpu_cpus.extend([0, 1])

                for core in e_cores:
                    net_storage_cpus.extend(core['threads'])
                logger.info(f"Hybrid CPU detected. GPU IRQ -> P-Core ({gpu_cpus}), Net/Storage IRQ -> E-Cores ({net_storage_cpus}).")

            # 2. AMD Dual-CCD (All AMD processors with dual CCDs)
            elif TopologyEngine.is_amd_dual_ccd():
                mid = len(topology) // 2
                ccd0_cores = topology[:mid]
                ccd1_cores = topology[mid:]

                if ccd0_cores:
                    gpu_cpus.extend(ccd0_cores[0]['threads'])
                else:
                    gpu_cpus.extend([0, 1])

                for core in ccd1_cores:
                    net_storage_cpus.extend(core['threads'])
                logger.info(f"AMD Dual-CCD CPU detected. GPU IRQ -> CCD0 ({gpu_cpus}), Net/Storage IRQ -> CCD1 ({net_storage_cpus}).")

            # 3. Other Homogeneous CPUs (Ordinary Intel/AMD)
            else:
                if len(topology) > 0:
                    gpu_cpus.extend(topology[0]['threads'])
                else:
                    gpu_cpus.extend([0, 1])

                if len(topology) > 1:
                    # Bind Network and Storage to the last physical core to keep Core 0 clean
                    net_storage_cpus.extend(topology[-1]['threads'])
                else:
                    net_storage_cpus.extend(gpu_cpus)
                logger.info(f"Homogeneous CPU detected. GPU IRQ -> Core 0 ({gpu_cpus}), Net/Storage IRQ -> Last Core ({net_storage_cpus}).")

            # Convert to masks and groups
            gpu_mask, gpu_group = cls.list_to_mask_and_group(gpu_cpus)
            net_storage_mask, net_storage_group = cls.list_to_mask_and_group(net_storage_cpus)

            applied_count = 0

            # Apply GPU Affinity
            for dev_id in all_gpu:
                sub_path = f"SYSTEM\\CurrentControlSet\\Enum\\{dev_id}\\Device Parameters\\Interrupt Management\\Affinity Policy"
                SystemTweaksService.backup_registry_value("HKLM", sub_path, "DevicePolicy")
                SystemTweaksService.backup_registry_value("HKLM", sub_path, "AssignmentSet")
                SystemTweaksService.backup_registry_value("HKLM", sub_path, "DeviceGroup")
                try:
                    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, sub_path, 0, winreg.KEY_WRITE) as key:
                        winreg.SetValueEx(key, "DevicePolicy", 0, winreg.REG_DWORD, 4)  # IrqPolicySpecifiedProcessors
                        winreg.SetValueEx(key, "AssignmentSet", 0, winreg.REG_BINARY, gpu_mask)
                        winreg.SetValueEx(key, "DeviceGroup", 0, winreg.REG_DWORD, gpu_group)
                    logger.info(f"GPU IRQ Affinity (Cores {gpu_cpus}, Group {gpu_group}) applied for device: {dev_id}")
                    applied_count += 1
                except Exception as e:
                    logger.error(f"Failed to set IRQ affinity registry for GPU device {dev_id}: {str(e)}")

            # Apply Network & Storage Affinity
            if net_storage_cpus:
                for dev_id in all_net + all_storage:
                    sub_path = f"SYSTEM\\CurrentControlSet\\Enum\\{dev_id}\\Device Parameters\\Interrupt Management\\Affinity Policy"
                    SystemTweaksService.backup_registry_value("HKLM", sub_path, "DevicePolicy")
                    SystemTweaksService.backup_registry_value("HKLM", sub_path, "AssignmentSet")
                    SystemTweaksService.backup_registry_value("HKLM", sub_path, "DeviceGroup")
                    try:
                        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, sub_path, 0, winreg.KEY_WRITE) as key:
                            winreg.SetValueEx(key, "DevicePolicy", 0, winreg.REG_DWORD, 4)  # IrqPolicySpecifiedProcessors
                            winreg.SetValueEx(key, "AssignmentSet", 0, winreg.REG_BINARY, net_storage_mask)
                            winreg.SetValueEx(key, "DeviceGroup", 0, winreg.REG_DWORD, net_storage_group)
                        logger.info(f"Net/Storage IRQ Affinity (Cores {net_storage_cpus}, Group {net_storage_group}) applied for device: {dev_id}")
                        applied_count += 1
                    except Exception as e:
                        logger.error(f"Failed to set IRQ affinity registry for Net/Storage device {dev_id}: {str(e)}")
            
            return applied_count > 0

        except Exception as e:
            logger.error(f"Failed to execute separated IRQ affinity separation: {str(e)}")
            return False
