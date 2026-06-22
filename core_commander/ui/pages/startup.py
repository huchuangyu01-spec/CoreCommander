# -*- coding: utf-8 -*-
import os
import winreg
import win32com.client
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel
from qfluentwidgets import ScrollArea, TitleLabel, SubtitleLabel, BodyLabel, CaptionLabel, SimpleCardWidget, SwitchButton, InfoBar
from core_commander.utils.i18n import Trans
from core_commander.utils.logger import logger

def is_startup_approved(hive, approved_path, value_name) -> bool:
    """
    Checks if a startup item is enabled in StartupApproved registry key.
    """
    try:
        with winreg.OpenKey(hive, approved_path, 0, winreg.KEY_READ) as key:
            val, val_type = winreg.QueryValueEx(key, value_name)
            if val_type == winreg.REG_BINARY:
                if len(val) > 0:
                    first_byte = val[0]
                    # 02 or 06 means enabled, 03 or others means disabled
                    return first_byte in (2, 6)
    except FileNotFoundError:
        # If not listed in StartupApproved, it's enabled by default
        return True
    except Exception as e:
        logger.debug(f"Error reading StartupApproved for {value_name}: {e}")
        return True
    return True

def set_startup_approved_state(hive, approved_path, value_name, enable: bool):
    """
    Sets the enabled/disabled state of a startup item in StartupApproved registry key.
    Preserves existing binary payload (timestamp, etc.) if possible.
    """
    try:
        key = winreg.CreateKeyEx(hive, approved_path, 0, winreg.KEY_ALL_ACCESS)
    except Exception:
        try:
            key = winreg.CreateKeyEx(hive, approved_path, 0, winreg.KEY_WRITE)
        except Exception as e:
            logger.error(f"Failed to open/create StartupApproved key {approved_path}: {e}")
            raise e

    with key:
        existing_val = None
        try:
            val, val_type = winreg.QueryValueEx(key, value_name)
            if val_type == winreg.REG_BINARY:
                existing_val = bytearray(val)
        except Exception:
            pass

        new_first_byte = 0x02 if enable else 0x03

        if existing_val is not None and len(existing_val) > 0:
            existing_val[0] = new_first_byte
            new_val = bytes(existing_val)
        else:
            # Create a default 12-byte binary value
            new_val = bytes([new_first_byte] + [0] * 11)

        winreg.SetValueEx(key, value_name, 0, winreg.REG_BINARY, new_val)
        logger.info(f"Set startup state for {value_name} in {approved_path} to {'Enabled' if enable else 'Disabled'}")

def migrate_old_backup_keys():
    """
    One-time silent migration of old disabled backup keys from CoreCommander custom path
    back to the Windows system Run paths, while setting them to disabled under StartupApproved.
    """
    backup_paths = [
        (winreg.HKEY_CURRENT_USER, r"Software\CoreCommander\BackupRun\HKCU", 
         r"Software\Microsoft\Windows\CurrentVersion\Run", 
         r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run", winreg.HKEY_CURRENT_USER),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\CoreCommander\BackupRun\HKLM", 
         r"Software\Microsoft\Windows\CurrentVersion\Run", 
         r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run", winreg.HKEY_LOCAL_MACHINE),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\CoreCommander\BackupRun\HKLM_Wow64", 
         r"Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Run", 
         r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run32", winreg.HKEY_LOCAL_MACHINE)
    ]
    
    for root_hive, backup_path, orig_path, approved_path, approved_hive in backup_paths:
        try:
            with winreg.OpenKey(root_hive, backup_path, 0, winreg.KEY_READ | winreg.KEY_WRITE) as b_key:
                info = winreg.QueryInfoKey(b_key)
                values_to_migrate = []
                for i in range(info[1]):
                    name, val, val_type = winreg.EnumValue(b_key, i)
                    values_to_migrate.append((name, val, val_type))
                
                if values_to_migrate:
                    # Write to original Run key
                    with winreg.CreateKeyEx(root_hive, orig_path, 0, winreg.KEY_WRITE) as orig_key:
                        for name, val, val_type in values_to_migrate:
                            winreg.SetValueEx(orig_key, name, 0, val_type, val)
                            
                    # Set to disabled in StartupApproved
                    for name, _, _ in values_to_migrate:
                        set_startup_approved_state(approved_hive, approved_path, name, enable=False)
                        
                    # Delete from backup key
                    for name, _, _ in values_to_migrate:
                        try:
                            winreg.DeleteValue(b_key, name)
                        except Exception:
                            pass
            # Try to delete the backup key itself
            try:
                winreg.DeleteKey(root_hive, backup_path)
                logger.info(f"Successfully migrated old backup key {backup_path} and cleaned it up.")
            except Exception:
                pass
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.debug(f"Failed to migrate old backup key {backup_path}: {e}")

def get_startup_items():
    # Execute migration first
    migrate_old_backup_keys()

    items = []
    
    # 1. Registry run keys
    paths = [
        # HKCU Run
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", "HKCU_Run",
         r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run", winreg.HKEY_CURRENT_USER),
        # HKLM Run
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run", "HKLM_Run",
         r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run", winreg.HKEY_LOCAL_MACHINE),
        # HKLM Wow6432Node Run
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Run", "HKLM_Run32",
         r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run32", winreg.HKEY_LOCAL_MACHINE)
    ]
    
    for hive, path, location_name, approved_path, approved_hive in paths:
        try:
            with winreg.OpenKey(hive, path, 0, winreg.KEY_READ) as key:
                info = winreg.QueryInfoKey(key)
                for i in range(info[1]):
                    name, val, val_type = winreg.EnumValue(key, i)
                    enabled = is_startup_approved(approved_hive, approved_path, name)
                    items.append({
                        'name': name,
                        'path': val,
                        'type': 'registry',
                        'location': location_name,
                        'hive': hive,
                        'reg_path': path,
                        'approved_hive': approved_hive,
                        'approved_reg_path': approved_path,
                        'enabled': enabled
                    })
        except Exception:
            pass
            
    # 2. Startup folders
    user_startup = os.path.join(os.getenv('APPDATA', ''), r"Microsoft\Windows\Start Menu\Programs\Startup")
    common_startup = os.path.join(os.getenv('ALLUSERSPROFILE', ''), r"Microsoft\Windows\Start Menu\Programs\Startup")
    
    folder_locations = [
        (user_startup, "User_Startup", r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder", winreg.HKEY_CURRENT_USER),
        (common_startup, "Common_Startup", r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder", winreg.HKEY_LOCAL_MACHINE),
    ]

    shell = None
    try:
        shell = win32com.client.Dispatch("WScript.Shell")
    except Exception as e:
        logger.debug(f"Failed to dispatch WScript.Shell: {e}")

    for folder_path, location_name, approved_path, approved_hive in folder_locations:
        if not folder_path or not os.path.exists(folder_path):
            continue
        try:
            for filename in os.listdir(folder_path):
                filepath = os.path.join(folder_path, filename)
                if os.path.isfile(filepath):
                    target_path = filepath
                    if filename.lower().endswith('.lnk') and shell:
                        try:
                            shortcut = shell.CreateShortcut(filepath)
                            target_path = shortcut.TargetPath or filepath
                        except Exception:
                            pass
                    
                    enabled = is_startup_approved(approved_hive, approved_path, filename)
                    display_name = os.path.splitext(filename)[0] if filename.lower().endswith('.lnk') else filename
                    
                    items.append({
                        'name': display_name,
                        'filename': filename,
                        'path': target_path,
                        'type': 'folder',
                        'location': location_name,
                        'shortcut_file': filepath,
                        'approved_hive': approved_hive,
                        'approved_reg_path': approved_path,
                        'enabled': enabled
                    })
        except Exception as e:
            logger.debug(f"Error scanning folder {folder_path}: {e}")
            
    return items

def classify_startup_item(name, path) -> str:
    """
    Classifies a startup item into:
    - 'recommend': 建议关闭 (unnecessary, updaters, telemetry)
    - 'keep': 维持正常 (drivers, security, essential tools)
    - 'optional': 可以关闭 (chat apps, launchers, other software)
    """
    n = name.lower() if isinstance(name, str) else str(name).lower()
    if isinstance(path, bytes):
        try:
            p = path.decode('utf-8', errors='ignore').lower()
        except Exception:
            p = str(path).lower()
    else:
        p = str(path).lower()
    
    # 维持正常 (Drivers, security, etc.)
    keep_keywords = [
        "securityhealth", "nvbackend", "shadowplay", "nvspcaps", "nvidia", 
        "radeon", "amd", "realtek", "intel", "wavessvc", "sound", "audio", 
        "windowsdefender", "antivirus", "ctfmon", "synaptics", "touchpad",
        "hdaudio", "blue", "bluetooth", "wireless", "wifi"
    ]
    for kw in keep_keywords:
        if kw in n or kw in p:
            return 'keep'
            
    # 建议关闭 (Telemetry, bloatware, updaters, etc.)
    recommend_keywords = [
        "telemetry", "update", "updater", "edge", "microsoftedge", "onedrive", 
        "ccleaner", "cortana", "teams", "skype", "feedback", "logitechgupdate", 
        "steamwebhelper", "epicgameslauncher", "wpsupdate", "auto_start_telemetry", 
        "baidu", "tencent", "helper", "daemon", "assistant"
    ]
    for kw in recommend_keywords:
        if kw in n or kw in p:
            return 'recommend'
            
    # Can be closed (default for standard applications)
    return 'optional'

def set_startup_item_state(item: dict, enable: bool):
    """
    Enables or disables a startup item using official Windows StartupApproved mechanism.
    """
    approved_hive = item['approved_hive']
    approved_reg_path = item['approved_reg_path']
    value_name = item.get('filename') if item.get('type') == 'folder' else item['name']
    
    set_startup_approved_state(approved_hive, approved_reg_path, value_name, enable)

class StartupItemRow(QWidget):
    """
    A single row representing a startup item with toggles.
    """
    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self.item = item
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(15)
        
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        
        # Display name
        name_raw = item.get('name', '')
        if isinstance(name_raw, bytes):
            try:
                name_str = name_raw.decode('utf-8', errors='ignore')
            except Exception:
                name_str = str(name_raw)
        else:
            name_str = str(name_raw)
        self.name_label = BodyLabel(name_str)
        self.name_label.setStyleSheet("font-weight: bold;")
        text_layout.addWidget(self.name_label)
        
        # Display executable path (truncated or tooltipped)
        path_raw = item.get('path', '')
        if isinstance(path_raw, bytes):
            try:
                path_str = path_raw.decode('utf-8', errors='ignore')
            except Exception:
                path_str = str(path_raw)
        else:
            path_str = str(path_raw)
        clean_path = path_str.replace('"', '')
        self.path_label = CaptionLabel(clean_path)
        self.path_label.setWordWrap(True)
        text_layout.addWidget(self.path_label)
        
        layout.addLayout(text_layout, 1)
        
        # Switch button
        self.switch_btn = SwitchButton(self)
        self.switch_btn.setChecked(item['enabled'])
        self.switch_btn.checkedChanged.connect(self.on_toggle)
        layout.addWidget(self.switch_btn)
        
    def on_toggle(self, checked: bool):
        try:
            set_startup_item_state(self.item, checked)
            self.item['enabled'] = checked
            
            # Show success banner
            action = "启用" if checked else "禁用"
            InfoBar.success(
                "操作成功",
                f"已成功{action}启动项: {self.item['name']}",
                duration=2000,
                parent=self.window()
            )
        except Exception as e:
            # Restore checked state in UI if failed
            self.switch_btn.blockSignals(True)
            self.switch_btn.setChecked(not checked)
            self.switch_btn.blockSignals(False)
            
            InfoBar.error(
                "操作失败",
                f"修改启动项状态失败: {str(e)}",
                duration=3000,
                parent=self.window()
            )

class StartupPage(ScrollArea):
    """
    Startup Items Manager split into Recommended to disable, Optional, and Keep normal.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.setObjectName("StartupPage")
        
        # Scroll area styling
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setViewportMargins(0, 0, 0, 0)
        self.setStyleSheet("QScrollArea { background-color: transparent; border: none; }")
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        
        # Scroll layout wrapper
        self.view = QWidget()
        self.view.setObjectName("StartupPageView")
        self.view.setStyleSheet("#StartupPageView { background-color: transparent; }")
        self.view.setMaximumWidth(1000)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        
        self.vBoxLayout = QVBoxLayout(self.view)
        self.vBoxLayout.setContentsMargins(30, 30, 30, 30)
        self.vBoxLayout.setSpacing(20)
        self.vBoxLayout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        # Header title
        self.title = TitleLabel("")
        self.title.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.vBoxLayout.addWidget(self.title)
        
        self.subtitle = BodyLabel("")
        self.vBoxLayout.addWidget(self.subtitle)
        
        # Groups Container
        self.recommend_card = self.create_group_card("", "recommend")
        self.optional_card = self.create_group_card("", "optional")
        self.keep_card = self.create_group_card("", "keep")
        
        self.vBoxLayout.addWidget(self.recommend_card)
        self.vBoxLayout.addWidget(self.optional_card)
        self.vBoxLayout.addWidget(self.keep_card)
        
        self.vBoxLayout.addStretch(1)
        
        # Load items & translate
        self.retranslate_ui()

    def create_group_card(self, title_text, category):
        card = SimpleCardWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)
        
        # Header title
        header = SubtitleLabel(title_text)
        layout.addWidget(header)
        card.header_label = header
        
        # Border separation line
        sep = QWidget()
        sep.setFixedHeight(1)
        
        # Separator line style based on theme
        sep.setStyleSheet("background-color: rgba(255,255,255,0.08);")
        layout.addWidget(sep)
        
        # Item list layout
        items_layout = QVBoxLayout()
        items_layout.setSpacing(5)
        layout.addLayout(items_layout)
        
        # Keep references to update later
        card.items_layout = items_layout
        card.category = category
        
        return card

    def retranslate_ui(self):
        self.title.setText(Trans.get("startup_title"))
        self.subtitle.setText(Trans.get("startup_desc"))
        self.recommend_card.header_label.setText(Trans.get("startup_recommend"))
        self.optional_card.header_label.setText(Trans.get("startup_optional"))
        self.keep_card.header_label.setText(Trans.get("startup_keep"))
        # Defer scan to showEvent to prevent synchronous registry reads during main app initialization
        if self.isVisible():
            self.refresh_items()

    def refresh_items(self):
        # Scan startup items
        items = get_startup_items()
        
        # Clear previous layouts
        for card in [self.recommend_card, self.optional_card, self.keep_card]:
            layout = card.items_layout
            while layout.count() > 0:
                child = layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()
                    
        # Group items
        counts = {"recommend": 0, "optional": 0, "keep": 0}
        
        for item in items:
            cat = classify_startup_item(item['name'], item['path'])
            row = StartupItemRow(item, self)
            
            if cat == "recommend":
                self.recommend_card.items_layout.addWidget(row)
                counts["recommend"] += 1
            elif cat == "keep":
                self.keep_card.items_layout.addWidget(row)
                counts["keep"] += 1
            else:
                self.optional_card.items_layout.addWidget(row)
                counts["optional"] += 1
                
        # Display placeholders if empty
        for card in [self.recommend_card, self.optional_card, self.keep_card]:
            if counts[card.category] == 0:
                placeholder = CaptionLabel(Trans.get("startup_empty"))
                placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
                placeholder.setStyleSheet("color: rgba(255,255,255,0.4); padding: 15px;")
                card.items_layout.addWidget(placeholder)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_items()
