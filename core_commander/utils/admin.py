# -*- coding: utf-8 -*-
import ctypes
from ctypes import wintypes
from core_commander.utils.logger import logger

# Global ctypes declarations for shell32 to prevent duplicate declarations at runtime
try:
    _shell32 = ctypes.windll.shell32
    _shell32.IsUserAnAdmin.argtypes = []
    _shell32.IsUserAnAdmin.restype = ctypes.c_bool
    
    _shell32.ShellExecuteW.argtypes = [
        ctypes.c_void_p, 
        ctypes.c_wchar_p, 
        ctypes.c_wchar_p, 
        ctypes.c_wchar_p, 
        ctypes.c_wchar_p, 
        ctypes.c_int
    ]
    _shell32.ShellExecuteW.restype = ctypes.c_void_p
    HAS_SHELL32 = True
except Exception:
    HAS_SHELL32 = False

# Win32 structures and constants for privileges adjustment
TOKEN_ADJUST_PRIVILEGES = 0x0020
TOKEN_QUERY = 0x0008
SE_PRIVILEGE_ENABLED = 0x00000002

class LUID(ctypes.Structure):
    _fields_ = [
        ("LowPart", wintypes.DWORD),
        ("HighPart", wintypes.LONG)
    ]

class LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Luid", LUID),
        ("Attributes", wintypes.DWORD)
    ]

class TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [
        ("PrivilegeCount", wintypes.DWORD),
        ("Privileges", LUID_AND_ATTRIBUTES * 1)
    ]

# Global ctypes declarations for advapi32 and kernel32
try:
    _advapi32 = ctypes.windll.advapi32
    _kernel32 = ctypes.windll.kernel32
    
    _advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    _advapi32.OpenProcessToken.restype = wintypes.BOOL
    
    _advapi32.LookupPrivilegeValueW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.POINTER(LUID)]
    _advapi32.LookupPrivilegeValueW.restype = wintypes.BOOL
    
    _advapi32.AdjustTokenPrivileges.argtypes = [
        wintypes.HANDLE, 
        wintypes.BOOL, 
        ctypes.POINTER(TOKEN_PRIVILEGES), 
        wintypes.DWORD, 
        ctypes.c_void_p, 
        ctypes.c_void_p
    ]
    _advapi32.AdjustTokenPrivileges.restype = wintypes.BOOL
    
    _kernel32.GetCurrentProcess.argtypes = []
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    
    _kernel32.GetLastError.argtypes = []
    _kernel32.GetLastError.restype = wintypes.DWORD
    
    HAS_ADVAPI32_CTYPES = True
except Exception:
    HAS_ADVAPI32_CTYPES = False

def is_admin():
    """
    Checks if the current process is running with Administrator privileges.
    """
    if not HAS_SHELL32:
        return False
    try:
        admin = _shell32.IsUserAnAdmin()
        logger.info(f"Admin privilege check: {admin}")
        return admin
    except Exception as e:
        logger.error(f"Failed to check admin privileges: {str(e)}")
        return False

_debug_privilege_enabled = False

def enable_debug_privilege():
    """
    Attempts to enable SeDebugPrivilege, SeIncreaseQuotaPrivilege, and SeProfileSingleProcessPrivilege.
    Requires running as Administrator.
    """
    global _debug_privilege_enabled
    if _debug_privilege_enabled:
        return True

    privileges = [
        "SeDebugPrivilege", 
        "SeIncreaseQuotaPrivilege", 
        "SeProfileSingleProcessPrivilege"
    ]
    
    # Method A: Try using pywin32 (win32security / win32api) with proper handle closure
    try:
        import win32security
        import win32api
        
        flags = win32security.TOKEN_ADJUST_PRIVILEGES | win32security.TOKEN_QUERY
        htoken = None
        success = False
        try:
            htoken = win32security.OpenProcessToken(win32api.GetCurrentProcess(), flags)
            for priv in privileges:
                try:
                    priv_id = win32security.LookupPrivilegeValue(None, priv)
                    win32security.AdjustTokenPrivileges(
                        htoken, 
                        0, 
                        [(priv_id, win32security.SE_PRIVILEGE_ENABLED)]
                    )
                    logger.info(f"Successfully enabled privilege (pywin32): {priv}")
                    success = True
                except Exception as ex:
                    logger.warning(f"Could not adjust privilege {priv} via pywin32: {str(ex)}")
            _debug_privilege_enabled = success
        finally:
            if htoken is not None:
                try:
                    htoken.Close()
                except Exception as ex:
                    logger.debug(f"Failed to close process token handle: {str(ex)}")
        return success
    except ImportError:
        logger.debug("win32security/win32api not available, falling back to ctypes direct advapi32 call.")
    except Exception as e:
        logger.error(f"Error while enabling debug privileges via pywin32: {str(e)}")

    # Method B: Fallback using direct ctypes (advapi32 / kernel32) to enable privileges without pywin32
    if HAS_ADVAPI32_CTYPES:
        htoken = wintypes.HANDLE()
        h_process = _kernel32.GetCurrentProcess()
        if _advapi32.OpenProcessToken(h_process, TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, ctypes.byref(htoken)):
            success = False
            try:
                for priv in privileges:
                    luid = LUID()
                    if _advapi32.LookupPrivilegeValueW(None, priv, ctypes.byref(luid)):
                        tp = TOKEN_PRIVILEGES()
                        tp.PrivilegeCount = 1
                        tp.Privileges[0].Luid = luid
                        tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
                        
                        if _advapi32.AdjustTokenPrivileges(htoken, False, ctypes.byref(tp), ctypes.sizeof(tp), None, None):
                            err = _kernel32.GetLastError()
                            if err == 0:
                                logger.info(f"Successfully enabled privilege (ctypes): {priv}")
                                success = True
                            else:
                                logger.warning(f"AdjustTokenPrivileges for {priv} completed with error code: {err}")
                        else:
                            logger.warning(f"Failed AdjustTokenPrivileges for {priv} via ctypes.")
                    else:
                        logger.warning(f"Failed LookupPrivilegeValueW for {priv} via ctypes.")
                _debug_privilege_enabled = success
            finally:
                _kernel32.CloseHandle(htoken)
            return success
        else:
            logger.error("Failed to OpenProcessToken via ctypes.")
            return False
    else:
        logger.error("Neither pywin32 nor advapi32 ctypes APIs are available. Privileges cannot be adjusted.")
        return False

def request_admin_elevation():
    """
    Helper function to restart the current process with admin elevation if not running as admin.
    Returns True if elevation dialog was spawned, False otherwise.
    """
    import sys
    import os
    if is_admin():
        return False
        
    if not HAS_SHELL32:
        return False
        
    try:
        params = " ".join(sys.argv[1:])
        logger.info("Requesting administrative elevation...")
        
        # Execute runas
        if getattr(sys, 'frozen', False):
            # Frozen (PyInstaller executable)
            ret = _shell32.ShellExecuteW(None, "runas", sys.executable, params, os.path.dirname(sys.executable), 1)
        else:
            # Normal python script
            script = os.path.abspath(sys.argv[0])
            ret = _shell32.ShellExecuteW(None, "runas", sys.executable, f'"{script}" {params}', os.path.dirname(script), 1)
            
        # ShellExecuteW returns a pseudo-handle. A value > 32 indicates success.
        ret_val = ret if ret is not None else 0
        if ret_val <= 32:
            logger.error(f"ShellExecuteW elevation failed with return value: {ret_val}")
            return False
            
        return True
    except Exception as e:
        logger.error(f"Elevation request failed: {str(e)}")
        return False
