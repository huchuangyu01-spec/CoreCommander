# -*- coding: utf-8 -*-
"""
Process Utilities for Core Commander.
Provides robust process path resolution for protected or standard Windows processes.
"""

import subprocess
import psutil
from core_commander.utils.logger import logger

def get_process_path_by_pid(pid: int) -> str:
    """
    Retrieves the absolute executable path of a running process by its PID.
    Uses a multi-tier fallback strategy (psutil -> ctypes API -> WMI COM -> PowerShell)
    to guarantee successful lookup even for anti-cheat protected or system processes.
    """
    if not pid or pid <= 0:
        return ""

    # Tier 1: Standard psutil lookup
    try:
        p = psutil.Process(pid)
        path = p.exe()
        if path:
            logger.debug(f"Tier 1 (psutil) resolved process path for PID {pid}: {path}")
            return path
    except Exception as e:
        logger.debug(f"Tier 1 (psutil) failed for PID {pid}: {str(e)}")

    # Tier 2: ctypes Win32 QueryFullProcessImageNameW (handles standard AccessDenied on standard OpenProcess)
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h_process = kernel32.OpenProcess(0x1000, False, pid)
        if h_process:
            try:
                buf = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(1024)
                if kernel32.QueryFullProcessImageNameW(h_process, 0, buf, ctypes.byref(size)):
                    path = buf.value
                    if path:
                        logger.debug(f"Tier 2 (ctypes) resolved process path for PID {pid}: {path}")
                        return path
            finally:
                kernel32.CloseHandle(h_process)
    except Exception as e:
        logger.debug(f"Tier 2 (ctypes) failed for PID {pid}: {str(e)}")

    # Tier 3: WMI COM query (retrieves from OS system table, bypassing process-handle security)
    try:
        import pythoncom
        import win32com.client
        has_com_init = False
        try:
            pythoncom.CoInitialize()
            has_com_init = True
            wmi_obj = win32com.client.GetObject("winmgmts:\\\\.\\root\\cimv2")
            proc_list = wmi_obj.ExecQuery(f"SELECT ExecutablePath FROM Win32_Process WHERE ProcessId = {pid}")
            for p in proc_list:
                if p.ExecutablePath:
                    path = p.ExecutablePath
                    logger.debug(f"Tier 3 (WMI COM) resolved process path for PID {pid}: {path}")
                    return path
        finally:
            if has_com_init:
                pythoncom.CoUninitialize()
    except Exception as e:
        logger.debug(f"Tier 3 (WMI COM) failed for PID {pid}: {str(e)}")

    # Tier 4: PowerShell query as a final fallback
    try:
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue).Path"]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=3, creationflags=subprocess.CREATE_NO_WINDOW)
        path = res.stdout.strip()
        if path:
            logger.debug(f"Tier 4 (PowerShell) resolved process path for PID {pid}: {path}")
            return path
    except Exception as e:
        logger.debug(f"Tier 4 (PowerShell) failed for PID {pid}: {str(e)}")

    logger.warning(f"All process path resolution methods failed for PID {pid}")
    return ""

def find_game_path(exe_name: str) -> str:
    """
    Proactively searches for the game's executable path on the disk
    by scanning Steam library paths, registry settings, and common directories.
    """
    import os
    import re
    import winreg
    
    if not exe_name:
        return ""

    # 1. Search Steam libraries
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam_path = winreg.QueryValueEx(key, "SteamPath")[0]
            steam_path = os.path.abspath(steam_path)
            
            vdf_path = os.path.join(steam_path, "steamapps", "libraryfolders.vdf")
            paths = [steam_path]
            if os.path.exists(vdf_path):
                with open(vdf_path, encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                # Find all "path" "..."
                for m in re.finditer(r'"path"\s*"([^"]+)"', content):
                    p = m.group(1).replace("\\\\", "\\")
                    if os.path.exists(p) and p not in paths:
                        paths.append(p)
            
            for p in paths:
                common = os.path.join(p, "steamapps", "common")
                if os.path.exists(common):
                    # Quick search first level subdirectories to avoid full walk
                    for sub in os.listdir(common):
                        sub_path = os.path.join(common, sub)
                        if os.path.isdir(sub_path):
                            target = os.path.join(sub_path, exe_name)
                            if os.path.exists(target):
                                logger.info(f"Located game {exe_name} in Steam library: {target}")
                                return target
    except Exception as e:
        logger.debug(f"Steam path search failed: {str(e)}")

    # 2. Search common locations
    common_roots = [
        os.environ.get("ProgramFiles", "C:\\Program Files"),
        os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"),
    ]
    # Add gaming drives if they exist
    for drive in ["D", "E", "F", "G"]:
        for folder in ["Games", "SteamLibrary", "Epic Games"]:
            path = f"{drive}:\\{folder}"
            if os.path.exists(path):
                common_roots.append(path)

    for r in common_roots:
        if os.path.exists(r):
            # Try to walk but limit depth
            for root, dirs, files in os.walk(r):
                if exe_name in files:
                    target = os.path.join(root, exe_name)
                    logger.info(f"Located game {exe_name} in common directory: {target}")
                    return target
                # limit depth to 3
                depth = root.replace(r, "").count(os.path.sep)
                if depth >= 3:
                    dirs.clear()

    return ""

