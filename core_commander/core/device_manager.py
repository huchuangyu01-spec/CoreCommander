# -*- coding: utf-8 -*-
import os
import subprocess
from core_commander.utils.logger import logger
from core_commander.core.system_tweaks import SystemTweaksService

class DeviceManager:
    """
    Handles hardware-level device stack restarts (PnP reloads) to force immediately applying registry adjustments
    (IRQ affinity, MSI mode, and network adapter tweaks) without requiring a system reboot.
    """

    @staticmethod
    def restart_device(device_instance_id: str) -> bool:
        """
        Restarts a hardware device by its PnP Instance ID using pnputil utility.
        """
        if not device_instance_id:
            return False
        try:
            logger.info(f"Triggering hardware device restart for: {device_instance_id}")
            # Use safe list execution to prevent command injection
            cmd = ["pnputil", "/restart-device", device_instance_id]
            cmd = SystemTweaksService._resolve_absolute_cmd_path(cmd)
            
            res = subprocess.run(
                cmd,
                capture_output=True,
                timeout=12,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            if res.returncode == 0:
                logger.info(f"Successfully restarted device {device_instance_id}")
                return True
            else:
                out = ""
                if res.stdout:
                    try:
                        out = res.stdout.decode('utf-8').strip()
                    except UnicodeDecodeError:
                        out = res.stdout.decode('gbk', errors='ignore').strip()
                logger.warning(f"Failed to restart device {device_instance_id}, returncode: {res.returncode}. Output: {out}")
                return False
        except subprocess.TimeoutExpired:
            logger.error(f"Timeout expired while restarting device: {device_instance_id}")
            return False
        except Exception as e:
            logger.error(f"Error occurred while restarting device {device_instance_id}: {str(e)}")
            return False

    @staticmethod
    def is_adapter_enabled(adapter_name: str) -> bool:
        """
        Checks if a network adapter is enabled using WMI COM, PowerShell, or netsh.
        """
        if not adapter_name:
            return False
        escaped_name = adapter_name.replace("'", "''")

        # 1. Try WMI COM (locale-agnostic, structured query)
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            try:
                wmi = win32com.client.GetObject("winmgmts:\\\\.\\root\\StandardCimv2")
                adapters = wmi.ExecQuery(f"SELECT Enabled FROM MSFT_NetAdapter WHERE Name = '{escaped_name}'")
                for a in adapters:
                    enabled = getattr(a, "Enabled", None)
                    if enabled is not None:
                        return bool(enabled)
            finally:
                pythoncom.CoUninitialize()
        except Exception:
            pass

        # 2. Try PowerShell (locale-agnostic)
        try:
            cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                   "param($name); (Get-NetAdapter -Name $name).Enabled", "-name", adapter_name]
            cmd = SystemTweaksService._resolve_absolute_cmd_path(cmd)
            res = subprocess.run(cmd, capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
            if res.returncode == 0 and res.stdout:
                try:
                    val = res.stdout.decode('utf-8').strip().lower()
                except UnicodeDecodeError:
                    val = res.stdout.decode('gbk', errors='ignore').strip().lower()
                if "true" in val:
                    return True
                if "false" in val:
                    return False
        except Exception:
            pass

        # 3. Try netsh (fallback, fast but localized)
        try:
            cmd = ["netsh", "interface", "show", "interface", "name=" + adapter_name]
            cmd = SystemTweaksService._resolve_absolute_cmd_path(cmd)
            res = subprocess.run(cmd, capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
            if res.returncode == 0 and res.stdout:
                try:
                    out = res.stdout.decode('utf-8').lower()
                except UnicodeDecodeError:
                    out = res.stdout.decode('gbk', errors='ignore').lower()
                enabled_keywords = ["enabled", "已启用", "启用", "activé", "active", "aktiviert", "habilitado"]
                disabled_keywords = ["disabled", "已禁用", "禁用", "désactivé", "deaktiviert", "deshabilitado"]
                if any(kw in out for kw in enabled_keywords):
                    return True
                if any(kw in out for kw in disabled_keywords):
                    return False
        except Exception:
            pass

        # Fallback to True to prevent infinite enabling loops in case of error
        return True

    @staticmethod
    def restart_network_adapter(adapter_name: str) -> bool:
        """
        Restarts a network adapter by its interface name, ensuring it is always re-enabled.
        """
        if not adapter_name:
            return False
            
        import time
        logger.info(f"Starting robust restart sequence for network adapter: {adapter_name}")
        escaped_name = adapter_name.replace("'", "''")
        
        # Method: Robust manual Disable -> Enable sequence with safety verification
        try:
            disabled_ok = False
            # Try PowerShell first to disable
            cmd_dis_ps = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                          "param($name); Disable-NetAdapter -Name $name -Confirm:$false", "-name", adapter_name]
            cmd_dis_ps = SystemTweaksService._resolve_absolute_cmd_path(cmd_dis_ps)
            res_dis = subprocess.run(cmd_dis_ps, capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            if res_dis.returncode == 0:
                disabled_ok = True
            else:
                # Try netsh
                cmd_dis_netsh = ["netsh", "interface", "set", "interface", "name=" + adapter_name, "admin=disabled"]
                cmd_dis_netsh = SystemTweaksService._resolve_absolute_cmd_path(cmd_dis_netsh)
                res_dis_netsh = subprocess.run(cmd_dis_netsh, capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
                if res_dis_netsh.returncode == 0:
                    disabled_ok = True
            
            # Allow the OS state transition to finish
            time.sleep(2.0)
            
            # Force Enable with retries and verification
            for attempt in range(5):
                logger.info(f"Attempting to enable network adapter {adapter_name} (attempt {attempt + 1}/5)...")
                # Try PowerShell Enable-NetAdapter
                cmd_en_ps = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                             "param($name); Enable-NetAdapter -Name $name -Confirm:$false", "-name", adapter_name]
                cmd_en_ps = SystemTweaksService._resolve_absolute_cmd_path(cmd_en_ps)
                subprocess.run(cmd_en_ps, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
                
                # Try netsh Enable
                cmd_en_netsh = ["netsh", "interface", "set", "interface", "name=" + adapter_name, "admin=enabled"]
                cmd_en_netsh = SystemTweaksService._resolve_absolute_cmd_path(cmd_en_netsh)
                subprocess.run(cmd_en_netsh, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
                
                time.sleep(1.5)
                if DeviceManager.is_adapter_enabled(adapter_name):
                    logger.info(f"Successfully re-enabled network adapter: {adapter_name}")
                    return True
                    
            # If still disabled, run a final emergency enable attempt using WMI COM
            logger.error(f"Adapter {adapter_name} remains disabled after 5 attempts! Running emergency WMI enable.")
            try:
                import pythoncom
                import win32com.client
                pythoncom.CoInitialize()
                try:
                    wmi = win32com.client.GetObject("winmgmts:\\\\.\\root\\StandardCimv2")
                    adapters = wmi.ExecQuery(f"SELECT * FROM MSFT_NetAdapter WHERE Name = '{escaped_name}'")
                    for a in adapters:
                        a.Enable()
                finally:
                    pythoncom.CoUninitialize()
            except Exception as e:
                logger.error(f"Emergency WMI COM Enable failed: {e}")
                
            time.sleep(2.0)
            
            if DeviceManager.is_adapter_enabled(adapter_name):
                logger.info(f"Emergency WMI successfully re-enabled adapter: {adapter_name}")
                return True
                
        except Exception as e:
            logger.error(f"Error during Disable-Enable sequence for {adapter_name}: {e}")
            
        # Final safety fallback: try to enable it one more time to avoid leaving it disabled
        try:
            cmd_en_netsh = ["netsh", "interface", "set", "interface", "name=" + adapter_name, "admin=enabled"]
            cmd_en_netsh = SystemTweaksService._resolve_absolute_cmd_path(cmd_en_netsh)
            subprocess.run(cmd_en_netsh, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception:
            pass
            
        return DeviceManager.is_adapter_enabled(adapter_name)
