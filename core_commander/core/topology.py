# -*- coding: utf-8 -*-
import ctypes
import subprocess  # nosec
import winreg
from core_commander.utils.logger import logger

class GROUP_AFFINITY(ctypes.Structure):
    _fields_ = [
        ("Mask", ctypes.c_size_t), 
        ("Group", ctypes.c_ushort), 
        ("Reserved", ctypes.c_ushort * 3)
    ]

class PROCESSOR_RELATIONSHIP(ctypes.Structure):
    _fields_ = [
        ("Flags", ctypes.c_byte),
        ("EfficiencyClass", ctypes.c_byte), 
        ("Reserved", ctypes.c_byte * 20),
        ("GroupCount", ctypes.c_ushort),
    ]

class SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX_UNION(ctypes.Union):
    _fields_ = [
        ("Processor", PROCESSOR_RELATIONSHIP)
    ]

class SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX(ctypes.Structure):
    _fields_ = [
        ("Relationship", ctypes.c_int), 
        ("Size", ctypes.c_ulong),
        ("Union", SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX_UNION)
    ]

class TopologyEngine:
    _cpu_info_cache = None
    _cpu_vendor_cache = None

    @staticmethod
    def get_cpu_info() -> str:
        """
        Queries CPU name prioritizing registry lookup, falling back to WMIC.
        """
        if TopologyEngine._cpu_info_cache is not None:
            return TopologyEngine._cpu_info_cache

        # 1. Try registry query first (extremely fast, no subprocess overhead)
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
                if name: 
                    name = name.strip()
                    logger.info(f"Detected CPU name (Registry): {name}")
                    TopologyEngine._cpu_info_cache = name
                    return name
        except Exception as e:
            logger.debug(f"Registry CPU query failed: {str(e)}")
            
        # 2. Try direct WMI COM query next (very fast, no subprocess overhead)
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            wmi = None
            procs = None
            try:
                wmi = win32com.client.GetObject("winmgmts:{impersonationLevel=impersonate}!\\\\.\\root\\cimv2")
                procs = wmi.ExecQuery("SELECT Name FROM Win32_Processor")
                for p in procs:
                    if p.Name:
                        name = p.Name.strip()
                        logger.info(f"Detected CPU name (WMI COM): {name}")
                        TopologyEngine._cpu_info_cache = name
                        return name
            finally:
                procs = None
                wmi = None
                pythoncom.CoUninitialize()
        except Exception as e:
            logger.debug(f"WMI COM CPU query failed: {str(e)}")
            
        # 3. Fall back to PowerShell if registry and WMI queries fail (wmic is deprecated in Win11)
        try:
            cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", "$c = Get-CimInstance Win32_Processor -ErrorAction SilentlyContinue; if (!$c) { $c = Get-WmiObject Win32_Processor -ErrorAction SilentlyContinue }; $c.Name"]
            output = subprocess.check_output(cmd, creationflags=subprocess.CREATE_NO_WINDOW, timeout=5).decode("gbk", errors="ignore").strip()  # nosec
            if output:
                logger.info(f"Detected CPU name (PowerShell): {output}")
                TopologyEngine._cpu_info_cache = output
                return output
        except Exception as e:
            logger.debug(f"PowerShell CPU query failed: {str(e)}")
            
        TopologyEngine._cpu_info_cache = "Generic CPU"
        return "Generic CPU"

    @staticmethod
    def get_cpu_vendor() -> str:
        """
        Determines if CPU is INTEL, AMD, or UNKNOWN.
        """
        if TopologyEngine._cpu_vendor_cache is not None:
            return TopologyEngine._cpu_vendor_cache

        name = TopologyEngine.get_cpu_info().upper()
        vendor = "UNKNOWN"
        if "INTEL" in name: 
            vendor = "INTEL"
        elif "AMD" in name: 
            vendor = "AMD"
            
        TopologyEngine._cpu_vendor_cache = vendor
        return vendor

    @staticmethod
    def is_amd_dual_ccd() -> bool:
        """
        Checks if the CPU is an AMD processor with dual CCDs (logical thread count >= 24 or Ryzen 9/Threadripper).
        """
        vendor = TopologyEngine.get_cpu_vendor()
        if vendor != "AMD":
            return False
        import psutil
        logical_threads = psutil.cpu_count(logical=True) or 0
        if logical_threads >= 24:
            return True
        name = TopologyEngine.get_cpu_info().upper()
        return "RYZEN 9" in name or "THREADRIPPER" in name

    @staticmethod
    def is_amd_dual_ccd_vcache() -> bool:
        """
        Checks if the CPU is an AMD 3D V-Cache processor with dual CCDs (e.g. Ryzen 9 7900X3D/7950X3D).
        """
        if not TopologyEngine.is_amd_dual_ccd():
            return False
        name = TopologyEngine.get_cpu_info().upper()
        return "3D" in name

    @staticmethod
    def get_topology() -> list:
        """
        Uses GetLogicalProcessorInformationEx to build a mapping of physical cores,
        logical threads, and core types (P-Core/E-Core).
        """
        kernel32 = ctypes.windll.kernel32
        
        # Declare GetLogicalProcessorInformationEx signature for 64-bit safety
        kernel32.GetLogicalProcessorInformationEx.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel32.GetLogicalProcessorInformationEx.restype = ctypes.c_bool
        
        length = ctypes.c_ulong(0)
        
        # Determine necessary buffer size
        kernel32.GetLogicalProcessorInformationEx(0, None, ctypes.byref(length))
        if length.value == 0:
            logger.error("Failed to query CPU topology length.")
            return []
            
        buffer = (ctypes.c_byte * length.value)()
        if not kernel32.GetLogicalProcessorInformationEx(0, ctypes.byref(buffer), ctypes.byref(length)): 
            logger.error("Failed to retrieve Logical Processor Information Ex.")
            return []

        offset = 0
        raw_cores = []
        
        while offset < length.value:
            header = ctypes.cast(
                ctypes.byref(buffer, offset), 
                ctypes.POINTER(SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX)
            ).contents
            
            # RelationshipType == 0 (RelationProcessorCore)
            if header.Relationship == 0: 
                proc_rel = header.Union.Processor
                group_count = proc_rel.GroupCount
                
                # Directly calculate pointer from the original C-buffer to avoid copying struct truncation
                buffer_addr = ctypes.addressof(buffer)
                mask_ptr = ctypes.cast(
                    buffer_addr + offset + 32, 
                    ctypes.POINTER(GROUP_AFFINITY)
                )
                
                threads = []
                for g in range(group_count):
                    group_id = mask_ptr[g].Group
                    mask = mask_ptr[g].Mask
                    bit = 0
                    while mask > 0:
                        if mask & 1: 
                            threads.append(group_id * 64 + bit)
                        mask >>= 1
                        bit += 1
                threads.sort()
                raw_cores.append({"eff": proc_rel.EfficiencyClass, "threads": threads})
                
            offset += header.Size

        if not raw_cores:
            logger.warning("No CPU cores detected from topology APIs. Building fallback topology using psutil.")
            try:
                import psutil
                logical_count = psutil.cpu_count(logical=True) or 1
                physical_count = psutil.cpu_count(logical=False) or logical_count
                threads_per_core = max(1, logical_count // physical_count)
                fallback_cores = []
                for i in range(physical_count):
                    threads = list(range(i * threads_per_core, min(logical_count, (i + 1) * threads_per_core)))
                    fallback_cores.append({
                        'core_id': i,
                        'type': 'P-Core',
                        'threads': threads
                    })
                logger.info(f"Successfully mapped fallback CPU topology: {len(fallback_cores)} physical cores.")
                return fallback_cores
            except Exception as ex:
                logger.critical(f"Fallback topology construction failed: {str(ex)}")
                return [{'core_id': 0, 'type': 'P-Core', 'threads': [0]}]
            
        raw_cores.sort(key=lambda x: x["threads"][0])
        eff_classes = sorted(list(set(c["eff"] for c in raw_cores)))
        final_cores = []
        
        # Hybrid architecture check (Intel Alder Lake/Raptor Lake etc.)
        if len(eff_classes) > 1:
            max_eff = max(eff_classes)
            for i, c in enumerate(raw_cores):
                c_type = "P-Core" if c["eff"] == max_eff else "E-Core"
                final_cores.append({
                    'core_id': i, 
                    'type': c_type, 
                    'threads': c["threads"]
                })
        else:
            # Homogeneous cores (All treated as P-Cores)
            for i, c in enumerate(raw_cores): 
                final_cores.append({
                    'core_id': i, 
                    'type': 'P-Core', 
                    'threads': c["threads"]
                })
                
        logger.info(f"Successfully mapped CPU topology: {len(final_cores)} physical cores.")
        for fc in final_cores:
            logger.debug(f"Core #{fc['core_id']} ({fc['type']}): Threads {fc['threads']}")
            
        return final_cores
