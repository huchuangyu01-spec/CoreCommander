# -*- coding: utf-8 -*-
import os
import ctypes
import psutil
import threading
from core_commander.utils.logger import logger
from core_commander.utils import admin

# Global ctypes declarations for kernel32 and ntdll
try:
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.SetProcessWorkingSetSize.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t]
    kernel32.SetProcessWorkingSetSize.restype = ctypes.c_bool
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool

    ntdll = ctypes.WinDLL("ntdll.dll")
    NtSetSystemInformation = ntdll.NtSetSystemInformation
    NtSetSystemInformation.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
    NtSetSystemInformation.restype = ctypes.c_long
    HAS_MEM_CTYPES = True
except Exception:
    HAS_MEM_CTYPES = False

class MemoryService:
    _lock = threading.Lock()
    SystemMemoryListInformation = 80
    MemoryEmptyWorkingSets = 2      
    MemoryPurgeModifiedPageList = 3 
    MemoryPurgeStandbyList = 4      
    
    @staticmethod
    def _purge_common(purge_standby: bool = True) -> bool:
        """
        Purges modified page list and standby list using NtSetSystemInformation.
        """
        if not HAS_MEM_CTYPES:
            return False
        try:
            # Purge modified pages
            c_mod = ctypes.c_int(MemoryService.MemoryPurgeModifiedPageList)
            res_mod = NtSetSystemInformation(
                MemoryService.SystemMemoryListInformation, 
                ctypes.byref(c_mod), 
                ctypes.sizeof(c_mod)
            )
            
            res_sb = None
            if purge_standby:
                # Purge standby pages
                c_standby = ctypes.c_int(MemoryService.MemoryPurgeStandbyList)
                res_sb = NtSetSystemInformation(
                    MemoryService.SystemMemoryListInformation, 
                    ctypes.byref(c_standby), 
                    ctypes.sizeof(c_standby)
                )
            
            logger.info(f"Purge common memory lists. Status codes: Mod={hex(res_mod & 0xffffffff)}, Standby={hex(res_sb & 0xffffffff) if res_sb is not None else 'None'}")
            if res_mod == -1073741790 or (res_sb is not None and res_sb == -1073741790): # STATUS_PRIVILEGE_NOT_HELD (0xC0000022)
                logger.warning("Memory purge failed with STATUS_PRIVILEGE_NOT_HELD. Running as Administrator is required.")
            return True
        except Exception as e:
            logger.error(f"Error purging memory lists: {str(e)}")
            return False

    @staticmethod
    def clean_memory_nuclear() -> bool:
        """
        Nuclear Cleaning: Empty working sets of all running processes and purge lists.
        Suitable when no protected games are running.
        """
        with MemoryService._lock:
            priv_ok = admin.enable_debug_privilege() # Self-elevate privilege for maximum clearing success rate
            if not priv_ok:
                logger.warning("Failed to enable debug privilege. Some memory lists may not be cleaned successfully.")
                
            logger.info("Executing nuclear memory cleaning...")
            if not HAS_MEM_CTYPES:
                return False
            try:
                c_empty = ctypes.c_int(MemoryService.MemoryEmptyWorkingSets)
                res_empty = NtSetSystemInformation(
                    MemoryService.SystemMemoryListInformation, 
                    ctypes.byref(c_empty), 
                    ctypes.sizeof(c_empty)
                )
                
                logger.info(f"Emptied all process working sets. Status: {hex(res_empty & 0xffffffff)}")
                if res_empty == -1073741790:
                    logger.warning("Working sets empty failed: STATUS_PRIVILEGE_NOT_HELD. Administrator rights needed.")
                    
                MemoryService._purge_common()
                return True
            except Exception as e:
                logger.error(f"Nuclear memory cleaning encountered errors: {str(e)}")
                return False

    @staticmethod
    def clean_memory_smart(excluded_pid: int, custom_whitelist=None) -> bool:
        """
        Escort Cleaning: Evades the protected game process (avoiding micro-stutters)
        and empties working sets of all other processes individually.
        """
        with MemoryService._lock:
            priv_ok = admin.enable_debug_privilege() # Self-elevate privilege for maximum clearing success rate
            if not priv_ok:
                logger.warning("Failed to enable debug privilege. Smart memory cleaning might have lower success rates.")
                
            logger.info(f"Executing smart memory cleaning (excluding target PID: {excluded_pid})...")
            if not HAS_MEM_CTYPES:
                return False
            try:
                from core_commander.core.isolation import ProcessIsolationService
                whitelist = ProcessIsolationService.get_whitelist(custom_whitelist)
                
                my_pid = os.getpid()
                cleaned_count = 0
                failed_count = 0
                
                for proc in psutil.process_iter(['pid', 'name']):
                    try:
                        pid = proc.info.get('pid')
                        if pid is None or pid == excluded_pid or pid == my_pid or pid <= 4: 
                            continue
                        
                        # Fast-path cache lookup
                        try:
                            create_time = proc.create_time()
                            if ProcessIsolationService._whitelisted_pids.get(pid) == create_time:
                                continue
                        except Exception:
                            create_time = None

                        name = proc.info.get('name')
                        name = name.lower() if name else ""
                        name_no_ext = name[:-4] if name.endswith('.exe') else name
                        
                        h_name = ProcessIsolationService.djb2_hash(name)
                        h_name_no_ext = ProcessIsolationService.djb2_hash(name_no_ext)
                        if h_name in whitelist or h_name_no_ext in whitelist:
                            if create_time is not None:
                                try:
                                    ProcessIsolationService._whitelisted_pids[pid] = create_time
                                except Exception:
                                    pass
                            continue
                        
                        # OpenProcess with PROCESS_SET_QUOTA
                        h_process = kernel32.OpenProcess(0x0100, False, pid) 
                        if h_process:
                            try:
                                # -1, -1 signifies emptying the working set size
                                res = kernel32.SetProcessWorkingSetSize(h_process, -1, -1)
                                if res:
                                    cleaned_count += 1
                                else:
                                    failed_count += 1
                            finally:
                                kernel32.CloseHandle(h_process)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                    except Exception as ex:
                        logger.debug(f"Smart clean error on PID {pid}: {str(ex)}")
                        continue
                
                logger.info(f"Smart memory clean complete. Cleaned: {cleaned_count}, Ignored/Failed: {failed_count}")
                MemoryService._purge_common(purge_standby=False)
                return True
            except Exception as e:
                logger.error(f"Smart memory cleaning encountered errors: {str(e)}")
                return False

