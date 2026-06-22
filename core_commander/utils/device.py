# -*- coding: utf-8 -*-
"""
Device utilities for Core Commander.
Provides hardware device enumeration and querying capabilities.
"""

import subprocess
import winreg
from core_commander.utils.logger import logger

def get_pci_device_ids(device_class: str) -> list:
    """
    Query present PCI devices of a specific class (e.g. 'Display' or 'Net').
    Returns a sorted list of unique PNP Device IDs starting with 'PCI\\'.
    """
    device_ids = []
    
    # Tier 1: WMI COM
    try:
        import pythoncom
        import win32com.client
        try:
            pythoncom.CoInitialize()
        except Exception:
            # Already initialized or concurrency mismatch, safe to ignore
            pass
        
        wmi_cimv2 = win32com.client.GetObject("winmgmts:\\\\.\\root\\cimv2")
        if device_class.lower() == "display":
            devices = wmi_cimv2.ExecQuery("SELECT PNPDeviceID FROM Win32_VideoController")
        else:
            devices = wmi_cimv2.ExecQuery("SELECT PNPDeviceID FROM Win32_NetworkAdapter WHERE PhysicalAdapter=True")
            
        for d in devices:
            if d.PNPDeviceID:
                val = str(d.PNPDeviceID).strip()
                if val.upper().startswith("PCI\\"):
                    device_ids.append(val)
    except Exception as e:
        logger.debug(f"WMI query failed for class {device_class}: {str(e)}")
        
    # Tier 2: PowerShell fallback (robust line-by-line parsing, case-insensitive)
    if not device_ids:
        try:
            cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", 
                   f"Get-PnpDevice -PresentOnly | Where-Object {{$_.Class -eq '{device_class}'}} | Select-Object -ExpandProperty InstanceId"]
            res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            for line in res.stdout.splitlines():
                val = line.strip()
                if val.upper().startswith("PCI\\"):
                    device_ids.append(val)
        except Exception as e:
            logger.debug(f"PowerShell query failed for class {device_class}: {str(e)}")
            
    return list(sorted(set(device_ids)))
