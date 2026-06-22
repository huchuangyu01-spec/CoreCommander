# -*- coding: utf-8 -*-
import sys
def is_windows_11() -> bool:
    import sys
    if sys.platform == "win32":
        try:
            ver = sys.getwindowsversion()
            return ver.major > 10 or (ver.major == 10 and ver.build >= 22000)
        except Exception:  # nosec
            pass
    return False

import os
import winreg
import psutil
import shutil
import tempfile
from PySide6.QtCore import Qt, QThread, Signal, QPropertyAnimation, QEasingCurve, QEvent, QRectF
from PySide6.QtGui import QIcon, QColor, QPainter, QPainterPath, QDoubleValidator
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFrame, QScrollArea, QLabel, QApplication, QPushButton, QStackedWidget

from qfluentwidgets import (
    ScrollArea, TitleLabel, SubtitleLabel, SimpleCardWidget, 
    LineEdit, PushButton, PrimaryPushButton, ListWidget, InfoBar, FluentIcon,
    BodyLabel, CaptionLabel, SwitchSettingCard, SettingCardGroup,
    SettingCard, ComboBox, SwitchButton, ExpandSettingCard, isDarkTheme, IconWidget,
    qconfig, CheckBox, Slider, SpinBox, Pivot, SegmentedWidget, ProgressBar
)
from qfluentwidgets.common.style_sheet import FluentStyleSheet

from core_commander.utils.logger import logger
from core_commander.core.system_tweaks import SystemTweaksService
from core_commander.utils.i18n import Trans

class CleanupWorker(QThread):
    finished_signal = Signal(str)
    
    def run(self):
        result = SystemTweaksService.run_system_cleanup()
        self.finished_signal.emit(result)

class MtuWorker(QThread):
    finished_signal = Signal(str)
    
    def __init__(self, interface_name):
        super().__init__()
        self.interface_name = interface_name
        
    def run(self):
        result = SystemTweaksService.run_mtu_optimization(self.interface_name)
        self.finished_signal.emit(result)

class AutostartCheckWorker(QThread):
    finished_signal = Signal(bool)

    def run(self):
        try:
            import subprocess
            cmd = ["schtasks", "/query", "/tn", "CoreCommanderAutostart"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=3, creationflags=subprocess.CREATE_NO_WINDOW)
            self.finished_signal.emit(res.returncode == 0)
        except Exception:
            self.finished_signal.emit(False)

class ShortcutEdit(QPushButton):
    """
    A custom button that records key combinations when clicked.
    """
    shortcutChanged = Signal(str)

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.recording = False
        self.original_text = text
        self.setCheckable(True)
        self.clicked.connect(self.start_recording)
        
        # Apply premium styling to match Fluent UI visual styles
        from qfluentwidgets import isDarkTheme
        color = "#f1f5f9" if isDarkTheme() else "#0f172a"
        bg = "rgba(255, 255, 255, 0.06)" if isDarkTheme() else "rgba(0, 0, 0, 0.05)"
        border = "rgba(255, 255, 255, 0.1)" if isDarkTheme() else "rgba(0, 0, 0, 0.15)"
        hover_bg = "rgba(255, 255, 255, 0.12)" if isDarkTheme() else "rgba(0, 0, 0, 0.08)"
        pressed_bg = "rgba(255, 255, 255, 0.08)" if isDarkTheme() else "rgba(0, 0, 0, 0.12)"
        
        self.setStyleSheet(f"""
            QPushButton {{
                background: {bg};
                border: 1px solid {border};
                border-radius: 5px;
                color: {color};
                padding: 5px 12px;
                font-family: 'Segoe UI', 'Microsoft YaHei';
                font-size: 13px;
                min-width: 120px;
            }}
            QPushButton:checked {{
                background: rgba(234, 88, 12, 0.15);
                border: 1px solid #ea580c;
                color: #ea580c;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: {hover_bg};
            }}
            QPushButton:pressed {{
                background: {pressed_bg};
            }}
        """)

    def start_recording(self):
        if self.isChecked():
            self.recording = True
            self.original_text = self.text()
            self.setText("请按键...")
            self.grabKeyboard()
            
            # Temporarily unregister the global hotkeys to prevent Windows from swallowing them
            try:
                curr = self.parent()
                while curr:
                    called = False
                    if hasattr(curr, "unregister_global_hotkey"):
                        curr.unregister_global_hotkey()
                        called = True
                    if hasattr(curr, "unregister_ocr_hotkey"):
                        curr.unregister_ocr_hotkey()
                        called = True
                    if called:
                        break
                    curr = curr.parent()
            except Exception:
                pass
        else:
            self.stop_recording()

    def stop_recording(self):
        self.recording = False
        self.releaseKeyboard()
        self.setChecked(False)
        
        # Restore the global hotkeys
        try:
            curr = self.parent()
            while curr:
                called = False
                if hasattr(curr, "register_global_hotkey"):
                    curr.register_global_hotkey()
                    called = True
                if hasattr(curr, "register_ocr_hotkey"):
                    curr.register_ocr_hotkey(force=True)
                    called = True
                if called:
                    break
                curr = curr.parent()
        except Exception:
            pass

    def keyPressEvent(self, event):
        if not self.recording:
            super().keyPressEvent(event)
            return

        key = event.key()
        
        # Ignore standalone modifier key presses
        try:
            key_val = int(key)
        except (ValueError, TypeError):
            key_val = key

        if key_val in (int(Qt.Key.Key_Control), int(Qt.Key.Key_Shift), int(Qt.Key.Key_Alt), int(Qt.Key.Key_Meta)):
            return

        modifiers = event.modifiers()
        parts = []
        
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            parts.append("Ctrl")
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            parts.append("Shift")
        if modifiers & Qt.KeyboardModifier.AltModifier:
            parts.append("Alt")
        if modifiers & Qt.KeyboardModifier.MetaModifier:
            parts.append("Win")

        key_str = ""
        if int(Qt.Key.Key_A) <= key_val <= int(Qt.Key.Key_Z):
            key_str = chr(key_val)
        elif int(Qt.Key.Key_0) <= key_val <= int(Qt.Key.Key_9):
            key_str = chr(key_val)
        elif int(Qt.Key.Key_F1) <= key_val <= int(Qt.Key.Key_F12):
            key_str = f"F{key_val - int(Qt.Key.Key_F1) + 1}"
        elif key_val == int(Qt.Key.Key_Escape):
            self.setText(self.original_text)
            self.stop_recording()
            return
        elif key_val == int(Qt.Key.Key_Space):
            key_str = "Space"
        elif key_val in (int(Qt.Key.Key_Return), int(Qt.Key.Key_Enter)):
            key_str = "Enter"
        elif key_val == int(Qt.Key.Key_Tab):
            key_str = "Tab"
        elif key_val == int(Qt.Key.Key_Backspace):
            key_str = "Backspace"
        elif key_val == int(Qt.Key.Key_Delete):
            key_str = "Delete"
        elif key_val == int(Qt.Key.Key_Insert):
            key_str = "Insert"
        elif key_val == int(Qt.Key.Key_Home):
            key_str = "Home"
        elif key_val == int(Qt.Key.Key_End):
            key_str = "End"
        elif key_val == int(Qt.Key.Key_PageUp):
            key_str = "PageUp"
        elif key_val == int(Qt.Key.Key_PageDown):
            key_str = "PageDown"
        elif key_val == int(Qt.Key.Key_Left):
            key_str = "Left"
        elif key_val == int(Qt.Key.Key_Right):
            key_str = "Right"
        elif key_val == int(Qt.Key.Key_Up):
            key_str = "Up"
        elif key_val == int(Qt.Key.Key_Down):
            key_str = "Down"
        elif key_val == int(Qt.Key.Key_Minus):
            key_str = "-"
        elif key_val == int(Qt.Key.Key_Equal):
            key_str = "="
        elif key_val == int(Qt.Key.Key_BracketLeft):
            key_str = "["
        elif key_val == int(Qt.Key.Key_BracketRight):
            key_str = "]"
        elif key_val == int(Qt.Key.Key_Semicolon):
            key_str = ";"
        elif key_val == int(Qt.Key.Key_Apostrophe):
            key_str = "'"
        elif key_val == int(Qt.Key.Key_Comma):
            key_str = ","
        elif key_val == int(Qt.Key.Key_Period):
            key_str = "."
        elif key_val == int(Qt.Key.Key_Slash):
            key_str = "/"
        elif key_val == int(Qt.Key.Key_Backslash):
            key_str = "\\"
        elif key_val == int(Qt.Key.Key_QuoteLeft):
            key_str = "~"

        if key_str:
            all_parts = parts + [key_str]
            hotkey_str = "+".join(all_parts)
            self.setText(hotkey_str)
            self.shortcutChanged.emit(hotkey_str)
            self.stop_recording()
            
    def focusOutEvent(self, event):
        super().focusOutEvent(event)

class CollapsibleSettingCard(ExpandSettingCard):
    """ Custom collapsible setting card that fixes the height / viewport exposure layout bug in QFluentWidgets """
    def __init__(self, icon, title, content=None, parent=None):
        super().__init__(icon, title, content, parent)
        self.expandAni.finished.connect(self.__onAniFinished)

    def __onAniFinished(self):
        if not self.isExpand:
            self.setFixedHeight(self.card.height())

    def setExpand(self, isExpand: bool):
        if self.isExpand == isExpand:
            return

        self._adjustViewSize()

        self.isExpand = isExpand
        self.setProperty('isExpand', isExpand)
        self.setStyle(QApplication.style())

        if isExpand:
            h = self.viewLayout.sizeHint().height()
            self.verticalScrollBar().setValue(h)
            self.expandAni.setStartValue(h)
            self.expandAni.setEndValue(0)
        else:
            # Force the scrollWidget to resize to its true sizeHint height to update the scrollbar range correctly
            self.scrollWidget.resize(self.width(), self.scrollWidget.sizeHint().height())
            self.expandAni.setStartValue(0)
            self.expandAni.setEndValue(self.verticalScrollBar().maximum())

        self.expandAni.start()
        self.card.expandButton.setExpand(isExpand)

    def resizeEvent(self, e):
        self.card.resize(self.width(), self.card.height())
        self.scrollWidget.resize(self.width(), self.scrollWidget.sizeHint().height())

    def setTitle(self, title: str):
        if hasattr(self, 'card') and hasattr(self.card, 'titleLabel'):
            self.card.titleLabel.setText(title)
            self.card.titleLabel.adjustSize()
        if hasattr(self, 'badge'):
            self.badge.update_style()

    def setContent(self, content: str):
        if hasattr(self, 'descLabel'):
            self.descLabel.setText(content)
            self.descLabel.adjustSize()

class SettingBadge(QLabel):
    def __init__(self, is_immediate: bool, parent=None):
        super().__init__(parent)
        self.is_immediate = is_immediate
        self.update_style()
        qconfig.themeChanged.connect(self.update_style)

    def destroy(self, destroyWindow=True, destroySubWindows=True):
        try:
            qconfig.themeChanged.disconnect(self.update_style)
        except Exception:
            pass
        super().destroy(destroyWindow, destroySubWindows)

    def update_style(self):
        is_dark = isDarkTheme()
        if self.is_immediate:
            txt = Trans.get("badge_immediate", "即时生效")
            bg = "rgba(46, 204, 113, 0.15)"
            fg = "#2ECC71" if is_dark else "#27AE60"
        else:
            txt = Trans.get("badge_reboot", "需重启")
            bg = "rgba(230, 126, 34, 0.15)"
            fg = "#E67E22" if is_dark else "#D35400"
        
        self.setText(f" {txt} ")
        self.setStyleSheet(f"""
            QLabel {{
                background-color: {bg};
                color: {fg};
                border-radius: 4px;
                padding: 3px 6px;
                font-size: 11px;
                font-weight: bold;
            }}
        """)

class CollapsibleSwitchSettingCard(CollapsibleSettingCard):
    """ Collapsible setting card with switch button and dynamic status label """
    checkedChanged = Signal(bool)

    def __init__(self, icon, title, content=None, parent=None, is_immediate: bool = False):
        super().__init__(icon, title, None, parent)
        self.is_immediate = is_immediate
        
        # Detail description in expanded view layout
        self.descLabel = BodyLabel(self.view)
        if content:
            self.descLabel.setText(content)
        self.descLabel.setWordWrap(True)
        opacity = "0.6"
        self.descLabel.setStyleSheet(f"color: rgba(255, 255, 255, {opacity});" if isDarkTheme() else f"color: rgba(0, 0, 0, {opacity});")
        self.viewLayout.setContentsMargins(20, 10, 20, 10)
        self.viewLayout.addWidget(self.descLabel)
        
        # Create badge
        self.badge = SettingBadge(is_immediate, self.card)
        
        # Create status label
        self.statusLabel = QLabel(self.card)
        
        # Create switch button
        self.switchButton = SwitchButton(parent=self.card)
        self.switchButton.setOnText("")
        self.switchButton.setOffText("")
        self.switchButton.setText("")
        
        # Add widgets to card header
        self.card.addWidget(self.badge)
        self.card.addWidget(self.statusLabel)
        self.card.addWidget(self.switchButton)
        
        self.switchButton.setFocusPolicy(Qt.NoFocus)
        self.switchButton.checkedChanged.connect(self.__onCheckedChanged)
        self.title_str = title
        self.applied_state = None
        self.custom_status_type = None
        self.update_status(self.switchButton.isChecked(), is_pending=False)
 
    def __onCheckedChanged(self, isChecked: bool):
        if getattr(self, 'custom_status_type', None) == "gpu_msi":
            expected = 2 if isChecked else 0
            is_pending = (self.applied_state is not None and self.applied_state != expected)
        else:
            is_pending = (self.applied_state is not None and isChecked != self.applied_state)
            
        if getattr(self, 'is_immediate', False):
            is_pending = False
        self.update_status(isChecked, is_pending=is_pending)
        self.checkedChanged.emit(isChecked)
        
        # Real-time badge/confirm button count update
        p = self.parent()
        while p:
            if hasattr(p, 'update_pending_status'):
                p.update_pending_status()
                # Clear preset highlights if manual change
                win = getattr(p, 'parent_window', None) or p
                if win and not getattr(win, 'is_loading_preset', False):
                    if hasattr(win, 'clear_preset_highlights'):
                        win.clear_preset_highlights()
                break
            p = p.parent()

    def setChecked(self, isChecked: bool):
        win = None
        p = self.parent()
        while p:
            if hasattr(p, 'is_loading_settings'):
                win = p
                break
            p = p.parent()
        if not (win and getattr(win, 'is_loading_settings', False)):
            if getattr(self, 'custom_status_type', None) == "gpu_msi":
                self.applied_state = 2 if isChecked else 0
            else:
                self.applied_state = isChecked
        self.switchButton.blockSignals(True)
        self.switchButton.setChecked(isChecked)
        self.switchButton.blockSignals(False)
        self.update_status(isChecked, is_pending=False)

    def isChecked(self) -> bool:
        return self.switchButton.isChecked()

    def update_status(self, isChecked: bool, is_pending = False):
        if getattr(self, 'is_immediate', False):
            is_pending = False
            
        status_text = ""
        is_reboot_pending = (is_pending == "reboot_pending")
        if is_reboot_pending:
            is_pending = False

        if "开机自动启动" in self.title_str:
            if is_pending:
                status_text = "即将开启" if isChecked else "即将关闭"
            else:
                status_text = "已开启" if isChecked else "已关闭"
        elif "显示所有厂商" in self.title_str:
            if is_pending:
                status_text = "即将显示" if isChecked else "即将隐藏"
            else:
                status_text = "已显示" if isChecked else "已隐藏"
        elif getattr(self, 'custom_status_type', None) == "gpu_msi":
            if is_pending:
                status_text = "即将启用" if isChecked else "即将禁用"
            else:
                if isChecked:
                    status_text = "已启用"
                else:
                    if self.applied_state == 1:
                        status_text = Trans.get("status_gpu_msi_only", "仅开启MSI")
                    else:
                        status_text = "已禁用"
        else:
            if is_pending:
                status_text = "即将启用" if isChecked else "即将禁用"
            else:
                status_text = "已启用" if isChecked else "已禁用"
            
        if is_reboot_pending:
            status_text = "已部署 (重启后生效)"

        self.statusLabel.setText(status_text)
        
        if is_pending:
            self.statusLabel.setStyleSheet("color: #E2B13C; font-weight: bold; font-size: 14px;")
        elif is_reboot_pending:
            self.statusLabel.setStyleSheet("color: #2ECC71; font-weight: bold; font-size: 14px;")
        else:
            accent = "#0078D4"
            if isChecked:
                self.statusLabel.setStyleSheet(f"color: {accent}; font-weight: bold; font-size: 14px;")
            else:
                if getattr(self, 'custom_status_type', None) == "gpu_msi" and self.applied_state == 1:
                    self.statusLabel.setStyleSheet("color: #2ECC71; font-weight: bold; font-size: 14px;")
                else:
                    self.statusLabel.setStyleSheet("color: #FF4D4F; font-weight: bold; font-size: 14px;")

    def setTitle(self, title: str):
        self.title_str = title
        super().setTitle(title)
            
    def setContent(self, content: str):
        if hasattr(self, 'descLabel'):
            self.descLabel.setText(content)
            self.descLabel.adjustSize()

class CollapsibleActionSettingCard(CollapsibleSettingCard):
    """ Collapsible setting card with a push button """
    clicked = Signal()

    def __init__(self, icon, title, button_text, content=None, parent=None, is_immediate: bool = False):
        super().__init__(icon, title, None, parent)
        self.is_immediate = is_immediate
        
        # Detail description in expanded view layout
        self.descLabel = BodyLabel(self.view)
        if content:
            self.descLabel.setText(content)
        self.descLabel.setWordWrap(True)
        opacity = "0.6"
        self.descLabel.setStyleSheet(f"color: rgba(255, 255, 255, {opacity});" if isDarkTheme() else f"color: rgba(0, 0, 0, {opacity});")
        self.viewLayout.setContentsMargins(20, 10, 20, 10)
        self.viewLayout.addWidget(self.descLabel)
        
        # Create badge
        self.badge = SettingBadge(is_immediate, self.card)
        
        # Create action button
        self.button = PushButton(button_text, parent=self.card)
        self.button.setFocusPolicy(Qt.NoFocus)
        self.button.clicked.connect(self.clicked)
        
        # Add widgets to card header
        self.card.addWidget(self.badge)
        self.card.addWidget(self.button)
        
    def setButtonText(self, text: str):
        self.button.setText(text)
        self.button.adjustSize()

class CollapsibleComboSettingCard(CollapsibleSettingCard):
    """ Collapsible setting card with combo box """
    def __init__(self, icon, title, content=None, texts=None, parent=None, is_immediate: bool = False):
        super().__init__(icon, title, None, parent)
        self.is_immediate = is_immediate
        
        self.descLabel = BodyLabel(self.view)
        if content:
            self.descLabel.setText(content)
        self.descLabel.setWordWrap(True)
        opacity = "0.6"
        self.descLabel.setStyleSheet(f"color: rgba(255, 255, 255, {opacity});" if isDarkTheme() else f"color: rgba(0, 0, 0, {opacity});")
        self.viewLayout.setContentsMargins(20, 10, 20, 10)
        self.viewLayout.addWidget(self.descLabel)
        
        # Create badge
        self.badge = SettingBadge(is_immediate, self.card)
        self.card.addWidget(self.badge)
        
        self.comboBox = ComboBox(self.card)
        if texts:
            self.comboBox.addItems(texts)
            
        self.card.addWidget(self.comboBox)
        self.comboBox.setFocusPolicy(Qt.NoFocus)
        self.applied_state = None
        self.comboBox.currentIndexChanged.connect(self.__onIndexChanged)

    def __onIndexChanged(self, index: int):
        p = self.parent()
        while p:
            if hasattr(p, 'update_pending_status'):
                p.update_pending_status()
                # Clear preset highlights if manual change
                win = getattr(p, 'parent_window', None) or p
                if win and not getattr(win, 'is_loading_preset', False):
                    if hasattr(win, 'clear_preset_highlights'):
                        win.clear_preset_highlights()
                break
            p = p.parent()

    def setCurrentIndex(self, index: int):
        win = None
        p = self.parent()
        while p:
            if hasattr(p, 'is_loading_settings'):
                win = p
                break
            p = p.parent()
        if not (win and getattr(win, 'is_loading_settings', False)):
            self.applied_state = index
        self.comboBox.blockSignals(True)
        self.comboBox.setCurrentIndex(index)
        self.comboBox.blockSignals(False)


class PresetPanel(QWidget):
    """ One-Click Preset Optimization Panel """
    def __init__(self, parent_page, parent_window=None):
        super().__init__(parent_page)
        self.parent_page = parent_page
        self.parent_window = parent_window
        
        self.hLayout = QHBoxLayout(self)
        self.hLayout.setContentsMargins(0, 5, 0, 10)
        self.hLayout.setSpacing(12)
        
        # Default preset button toggle pair
        self.btn_default = PushButton("", self)
        self.btn_default.setIcon(FluentIcon.HISTORY)
        self.btn_default_primary = PrimaryPushButton("", self)
        self.btn_default_primary.setIcon(FluentIcon.HISTORY)
        self.btn_default_primary.hide()
        
        # Optimal preset button toggle pair
        self.btn_optimal = PushButton("", self)
        self.btn_optimal.setIcon(FluentIcon.SPEED_HIGH)
        self.btn_optimal_primary = PrimaryPushButton("", self)
        self.btn_optimal_primary.setIcon(FluentIcon.SPEED_HIGH)
        self.btn_optimal_primary.hide()
        
        # Maximum preset button toggle pair
        self.btn_maximum = PushButton("", self)
        self.btn_maximum.setIcon(FluentIcon.DEVELOPER_TOOLS)
        self.btn_maximum_primary = PrimaryPushButton("", self)
        self.btn_maximum_primary.setIcon(FluentIcon.DEVELOPER_TOOLS)
        self.btn_maximum_primary.hide()
        
        # Sync button
        self.btn_sync = PushButton("", self)
        self.btn_sync.setIcon(FluentIcon.SYNC)
        
        # Prevent spacebar triggering focused buttons
        for btn in [self.btn_default, self.btn_default_primary, 
                    self.btn_optimal, self.btn_optimal_primary, 
                    self.btn_maximum, self.btn_maximum_primary, 
                    self.btn_sync]:
            btn.setFocusPolicy(Qt.NoFocus)
            
        # Add all to layout
        self.hLayout.addWidget(self.btn_default)
        self.hLayout.addWidget(self.btn_default_primary)
        self.hLayout.addWidget(self.btn_optimal)
        self.hLayout.addWidget(self.btn_optimal_primary)
        self.hLayout.addWidget(self.btn_maximum)
        self.hLayout.addWidget(self.btn_maximum_primary)
        self.hLayout.addWidget(self.btn_sync)
        self.hLayout.addStretch(1)
        
        # Connect signals
        self.btn_default.clicked.connect(lambda: self.load_preset("default"))
        self.btn_default_primary.clicked.connect(lambda: self.load_preset("default"))
        
        self.btn_optimal.clicked.connect(lambda: self.load_preset("optimal"))
        self.btn_optimal_primary.clicked.connect(lambda: self.load_preset("optimal"))
        
        self.btn_maximum.clicked.connect(lambda: self.load_preset("maximum"))
        self.btn_maximum_primary.clicked.connect(lambda: self.load_preset("maximum"))
        
        self.btn_sync.clicked.connect(self.sync_system)
        
        self.retranslate_ui()
        
    def highlight_preset(self, preset_type: str):
        # Reset visibility
        if preset_type == "default":
            self.btn_default.hide()
            self.btn_default_primary.show()
        else:
            self.btn_default.show()
            self.btn_default_primary.hide()
            
        if preset_type == "optimal":
            self.btn_optimal.hide()
            self.btn_optimal_primary.show()
        else:
            self.btn_optimal.show()
            self.btn_optimal_primary.hide()
            
        if preset_type == "maximum":
            self.btn_maximum.hide()
            self.btn_maximum_primary.show()
        else:
            self.btn_maximum.show()
            self.btn_maximum_primary.hide()
        
    def retranslate_ui(self):
        self.btn_default.setText(Trans.get("preset_restore"))
        self.btn_default_primary.setText(Trans.get("preset_restore"))
        
        self.btn_optimal.setText(Trans.get("preset_optimal"))
        self.btn_optimal_primary.setText(Trans.get("preset_optimal"))
        
        self.btn_maximum.setText(Trans.get("preset_max"))
        self.btn_maximum_primary.setText(Trans.get("preset_max"))
        
        self.btn_sync.setText(Trans.get("preset_sync"))
        
    def load_preset(self, preset_type):
        if self.parent_window:
            self.parent_window.load_preset(preset_type)
        else:
            # Fallback if window reference is set on parent page
            win = getattr(self.parent_page, 'parent_window', None)
            if win:
                win.load_preset(preset_type)
            
    def sync_system(self):
        win = self.parent_window or getattr(self.parent_page, 'parent_window', None)
        if win:
            win.detect_and_sync_system_states(force_sync=True)
            # Clear preset highlights on sync
            for page in [win.general_page, win.cpu_page, win.peripheral_page, 
                         win.gpu_page, win.memory_page, win.privacy_page, 
                         win.network_page, win.tools_page]:
                if hasattr(page, 'presetPanel'):
                    page.presetPanel.highlight_preset("")
            InfoBar.success("系统检测同步", "已成功同步系统注册表/服务实际状态至UI界面！", parent=self.parent_page)

class BaseSettingsPage(ScrollArea):
    """ Base ScrollArea page structure representing a single settings route """
    def __init__(self, title_text, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.setObjectName(title_text + "Page")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setViewportMargins(0, 0, 0, 0)
        self.setStyleSheet("QScrollArea { background-color: transparent; border: none; }")
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        
        self.view = QWidget()
        self.view.setObjectName("view")
        self.view.setStyleSheet("#view { background-color: transparent; }")
        self.view.setMaximumWidth(1000)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        
        self.vBoxLayout = QVBoxLayout(self.view)
        self.vBoxLayout.setContentsMargins(30, 20, 30, 90)  # Reserve space at the bottom for the floating button
        self.vBoxLayout.setSpacing(12)
        
        # Title Label
        self.titleLabel = TitleLabel(title_text, self.view)
        self.vBoxLayout.addWidget(self.titleLabel)
        
        # Add Preset Panel
        self.presetPanel = PresetPanel(self, parent_window=self.parent_window)
        self.vBoxLayout.addWidget(self.presetPanel)

        # Floating Confirm Changes Button
        self.apply_btn = PrimaryPushButton("确认生效", self)
        self.apply_btn.setFocusPolicy(Qt.NoFocus)
        self.apply_btn.setIcon(FluentIcon.COMPLETED)
        self.apply_btn.resize(130, 40)
        self.apply_btn.clicked.connect(self.on_apply_clicked)
        
        self.retranslate_ui()

    def retranslate_ui(self):
        self.apply_btn.setText(Trans.get("apply_btn"))
        self.presetPanel.retranslate_ui()
        
        title_keys = {
            "SettingsGeneralPage": "nav_general",
            "SettingsCpuPage": "nav_cpu",
            "SettingsPeripheralPage": "nav_peripheral",
            "SettingsGpuPage": "nav_gpu",
            "SettingsMemoryPage": "nav_memory",
            "SettingsPrivacyPage": "nav_privacy",
            "SettingsNetworkPage": "nav_network",
            "SettingsToolsPage": "nav_tools"
        }
        class_name = self.__class__.__name__
        if class_name in title_keys:
            self.titleLabel.setText(Trans.get(title_keys[class_name]))

    def on_apply_clicked(self):
        win = self.parent_window
        if win and hasattr(win, 'apply_system_tweaks'):
            win.apply_system_tweaks()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, 'apply_btn'):
            btn_w = 130
            btn_h = 40
            margin = 30
            self.apply_btn.raise_()
            self.apply_btn.setGeometry(
                self.width() - btn_w - margin,
                self.height() - btn_h - margin,
                btn_w,
                btn_h
            )

    def _on_rl_switch_changed(self, checked):
        win = self.parent_window or (self.parent() if isinstance(self.parent(), QWidget) else None)
        is_loading = False
        if win and (getattr(win, 'is_loading_preset', False) or getattr(win, 'is_loading_settings', False)):
            is_loading = True
            
        if checked:
            from core_commander.ui.activation_dialog import require_license
            if not require_license(self, "目标进程网卡限速控制与按键绑定", silent=is_loading):
                self.rl_switch.blockSignals(True)
                self.rl_switch.setChecked(False)
                self.rl_switch.blockSignals(False)
                return
                
        if is_loading:
            return
            
        self.save_settings_immediately()

    def save_settings_immediately(self):
        parent_win = getattr(self, 'parent_window', None)
        win = parent_win or (self.parent() if isinstance(self.parent(), QWidget) else None)
        if win and (getattr(win, 'is_loading_preset', False) or getattr(win, 'is_loading_settings', False)):
            return
            
        sender = self.sender()
        if sender and win:
            attr_name = None
            for name, attr in self.__dict__.items():
                if (attr is sender or 
                    getattr(attr, 'comboBox', None) is sender or 
                    getattr(attr, 'switchButton', None) is sender or 
                    getattr(attr, 'shortcutButton', None) is sender or 
                    getattr(attr, 'slider', None) is sender or 
                    getattr(attr, 'spinBox', None) is sender):
                    attr_name = name
                    break
            if attr_name and hasattr(win, 'register_changed_immediate_key'):
                win.register_changed_immediate_key(attr_name)

        if hasattr(win, 'trigger_debounced_save_settings'):
            win.trigger_debounced_save_settings()
        elif hasattr(win, 'save_settings'):
            win.save_settings()
            if hasattr(win, 'apply_immediate_tweaks_silently'):
                win.apply_immediate_tweaks_silently()
        elif parent_win and hasattr(parent_win, 'trigger_debounced_save_settings'):
            parent_win.trigger_debounced_save_settings()
        elif parent_win and hasattr(parent_win, 'save_settings'):
            parent_win.save_settings()
            if hasattr(self.parent_window, 'apply_immediate_tweaks_silently'):
                self.parent_window.apply_immediate_tweaks_silently()

    def save_settings_only(self):
        win = self.parent_window or (self.parent() if isinstance(self.parent(), QWidget) else None)
        if win and (getattr(win, 'is_loading_preset', False) or getattr(win, 'is_loading_settings', False)):
            return
        if hasattr(win, 'trigger_debounced_save_settings_no_tweaks'):
            win.trigger_debounced_save_settings_no_tweaks()
        elif hasattr(win, 'save_settings'):
            win.save_settings()
        elif hasattr(self.parent_window, 'trigger_debounced_save_settings_no_tweaks'):
            self.parent_window.trigger_debounced_save_settings_no_tweaks()
        elif hasattr(self.parent_window, 'save_settings'):
            self.parent_window.save_settings()

    def get_pending_changes_count(self) -> int:
        count = 0
        parent_win = getattr(self, 'parent_window', None)
        win = parent_win or (self.parent() if isinstance(self.parent(), QWidget) else None)
        settings = getattr(win, 'settings', None) if win else None

        for card in self.findChildren(CollapsibleSwitchSettingCard):
            if not card.isHidden():
                if getattr(card, 'is_immediate', False):
                    continue
                # If we have settings loaded, match the checkbox state against settings to determine pending changes
                # because reboot-required settings are considered successfully written once they match settings.json
                attr_name = None
                for name, attr in self.__dict__.items():
                    if attr is card:
                        attr_name = name
                        break
                
                # If settings are mapped, compare with local settings to determine if UI changes are unsaved/unapplied
                if settings and attr_name:
                    mapping = {
                        "chk_iso": "enable_isolation",
                        "chk_dog": "enable_watchdog",
                        "chk_parking": "enable_core_parking",
                        "chk_epp": "enable_epp_max",
                        "chk_dwm": "enable_dwm_tweak",
                        "chk_timer_res": "enable_timer_resolution_tweak",
                        "chk_naraka_priority": "enable_naraka_priority",
                        "chk_child": "enable_child_optimization",
                        "chk_client_priority_demote": "enable_client_priority_demote",
                        "chk_dwm_wet": "enable_dwm_super_wet_tweak",
                        "chk_intel_plan": "enable_custom_power_plan",
                        "chk_amd_plan": "enable_custom_power_plan",
                        "chk_gpu_opt": "enable_gpu_optimization",
                        "chk_gpu_nip": "enable_nvidia_nip",
                        "chk_hard_working_set": "enable_hard_working_set",
                        "chk_prefetcher": "enable_prefetcher_tweak",
                        "chk_net_imod": "enable_net_imod_tweak",
                        "chk_net_bindings": "enable_net_bindings_tweak",
                        "chk_tcp_bbr": "enable_tcp_bbr_tweak",
                        "chk_eee": "enable_eee_tweak",
                        "chk_services": "disable_useless_services",
                        "chk_wsearch": "disable_wsearch_tweak",
                        "chk_gamedvr": "disable_gamedvr",
                        "chk_dev_power": "enable_device_power_tweak",
                        "chk_download_maps": "enable_download_maps_tweak",
                        "chk_bg_apps": "enable_bg_apps_tweak",
                        "chk_map_updates": "enable_map_updates_tweak",
                        "chk_autorun": "enable_autorun_tweak",
                        "chk_widgets": "enable_widgets_tweak",
                        "chk_sticky_keys": "enable_sticky_keys_tweak",
                        "chk_startup_delay": "enable_startup_delay_tweak",
                        "chk_menu_delay": "enable_menu_delay_tweak",
                        "chk_settings_sync": "enable_settings_sync_tweak",
                        "chk_dynamic_lighting": "enable_dynamic_lighting_tweak",
                        "chk_gpu_msi": "enable_gpu_msi_tweak",
                        "chk_xbox_save": "enable_xbox_save_tweak",
                        "chk_store_auto_update": "enable_store_auto_update_tweak",
                        "chk_vulnerable_driver_blocklist": "enable_vulnerable_driver_blocklist_tweak",
                        "chk_prevent_device_encryption": "enable_prevent_device_encryption_tweak",
                        "chk_spotlight": "enable_spotlight_tweak",
                        "chk_wifi_tweak": "enable_wifi_tweak",
                        "chk_game_gpu_preference": "enable_game_gpu_preference_tweak",
                        "chk_irq_affinity": "enable_irq_affinity_tweak",
                        "chk_power_throttling": "enable_power_throttling_tweak",
                        "chk_web_search": "enable_web_search_tweak",
                        "chk_telemetry_tasks": "enable_telemetry_tasks_tweak",
                        "chk_extreme_debloat": "enable_extreme_debloat_tweak",
                        "chk_security_notifications": "disable_security_notifications",
                        "chk_defender": "disable_defender",
                        "chk_smartscreen": "disable_smartscreen",
                        "chk_firewall": "disable_firewall",
                        "chk_visual_effects": "disable_windows_visual_effects",
                        "chk_transparency": "disable_windows_transparency",
                        "chk_consult_interests": "enable_consult_interests_tweak",
                        "chk_tips_suggestions": "enable_tips_suggestions_tweak",
                        "chk_usb_lat": "enable_usb_low_latency_tweak",
                        "chk_imod": "enable_usb_imod_tweak",
                        "chk_mouse_lat": "enable_mouse_latency_tweak",
                        "chk_global_fse": "enable_global_fse_tweak",
                        "chk_game_fse": "enable_game_fse_tweak",
                        "chk_dwm_presentation": "enable_dwm_presentation_tweak",
                        "chk_gpu_irq": "enable_gpu_irq_tweak",
                        "chk_hags": "disable_hags",
                        "chk_preemption": "disable_gpu_preemption",
                        "chk_gpu_firmware": "enable_gpu_firmware_tweak",
                        "chk_gpu_pstate": "enable_gpu_pstate_tweak",
                        "chk_pcipower": "disable_pcipower",
                        "chk_ram_opt": "enable_ram_optimization",
                        "chk_nvme_opt": "enable_nvme_optimization",
                        "chk_memory_comp": "disable_memory_compression",
                        "chk_config_alloc": "enable_config_alloc_tweak",
                        "chk_spectre": "disable_spectre_meltdown",
                        "chk_copilot": "disable_copilot",
                        "chk_desktop_heap": "enable_desktop_heap_tweak",
                        "chk_uac": "enable_uac_tweak",
                        "chk_download_maps": "enable_download_maps_tweak",
                        "chk_bg_apps": "enable_bg_apps_tweak",
                        "chk_map_updates": "enable_map_updates_tweak",
                        "chk_autoshare": "enable_autoshare_tweak",
                        "chk_autorun": "enable_autorun_tweak",
                        "chk_hyperv": "disable_hyperv_virtualization",
                        "chk_network": "enable_network_tweak",
                        "chk_ult_net": "enable_ultimate_network_tweak",
                        "chk_dns": "enable_dns_tweak",
                        "chk_net_imod": "enable_net_imod_tweak",
                        "chk_net_bindings": "enable_net_bindings_tweak",
                        "chk_wifi_tweak": "enable_wifi_tweak",
                        "chk_network_msi": "enable_network_msi_tweak",
                        "chk_storage_msi": "enable_storage_msi_tweak"
                    }
                    setting_attr = mapping.get(attr_name)
                    if setting_attr and hasattr(settings, setting_attr):
                        current_val = getattr(settings, setting_attr)
                        # MSI status holds integer options 0, 1, 2. Toggle holds bools
                        if getattr(card, 'custom_status_type', None) == "gpu_msi":
                            ui_val = card.isChecked()
                        else:
                            ui_val = card.isChecked()
                        
                        if ui_val != current_val:
                            count += 1
                        continue

                if getattr(card, 'custom_status_type', None) == "gpu_msi":
                    expected = 2 if card.isChecked() else 0
                    is_pending = (card.applied_state is not None and card.applied_state != expected)
                else:
                    is_pending = (card.applied_state is not None and card.isChecked() != card.applied_state)
                if is_pending:
                    count += 1

        for card in self.findChildren(CollapsibleComboSettingCard):
            if not card.isHidden():
                if getattr(card, 'is_immediate', False):
                    continue
                
                # Try matching combos against saved settings
                attr_name = None
                for name, attr in self.__dict__.items():
                    if attr is card:
                        attr_name = name
                        break
                
                if settings and attr_name:
                    mapping = {
                        "win32_prio_card": "win32_prio_sep",
                        "keyboard_queue_card": "keyboard_queue_size",
                        "mouse_queue_card": "mouse_queue_size",
                        "keyboard_repeat_rate_card": "keyboard_repeat_delay_level"
                    }
                    setting_attr = mapping.get(attr_name)
                    if setting_attr and hasattr(settings, setting_attr):
                        current_val = getattr(settings, setting_attr)
                        if setting_attr == "win32_prio_sep":
                            PRIO_SEP_VALUES = [2, 20, 21, 22, 24, 25, 26, 36, 37, 38, 40, 41, 42]
                            ui_val = PRIO_SEP_VALUES[card.comboBox.currentIndex()] if 0 <= card.comboBox.currentIndex() < len(PRIO_SEP_VALUES) else 26
                        elif setting_attr == "keyboard_queue_size":
                            kb_list = [100, 50, 30, 20, 16, 12, 10, 8, 6]
                            ui_val = kb_list[card.comboBox.currentIndex()] if 0 <= card.comboBox.currentIndex() < len(kb_list) else 100
                        elif setting_attr == "mouse_queue_size":
                            m_list = [100, 50, 30, 20, 16, 12, 10, 8]
                            ui_val = m_list[card.comboBox.currentIndex()] if 0 <= card.comboBox.currentIndex() < len(m_list) else 100
                        else:
                            ui_val = card.comboBox.currentIndex()
                            
                        if ui_val != current_val:
                            count += 1
                        continue

                is_pending = (card.applied_state is not None and card.comboBox.currentIndex() != card.applied_state)
                if is_pending:
                    count += 1
        return count

class SettingsGeneralPage(BaseSettingsPage):
    """ 1. Basic/General Settings Route (基础设置) """
    def __init__(self, parent=None):
        super().__init__("基础设置", parent)
        
        # 1. Autostart & ignoring hardware options
        self.chk_boot = CollapsibleSwitchSettingCard(
            FluentIcon.UP,
            "",
            "",
            parent=self.view,
            is_immediate=True
        )
        self.chk_show_all_cpu = CollapsibleSwitchSettingCard(
            FluentIcon.LEAF,
            "",
            "",
            parent=self.view,
            is_immediate=True
        )
        
        # 2. Win32 Priority Separation (Moved from CPU page)
        self.win32_prio_card = CollapsibleComboSettingCard(
            FluentIcon.APPLICATION,
            "",
            None,
            texts=[
                "2  (长时间片 / 固定 / 无前台提升 - 服务器默认)",
                "20 (短时间片 / 可变 / 无前台提升)",
                "21 (短时间片 / 可变 / 中前台提升 - 传统桌面默认)",
                "22 (短时间片 / 可变 / 最大前台提升)",
                "24 (短时间片 / 固定 / 无前台提升)",
                "25 (短时间片 / 固定 / 中前台提升)",
                "26 (短时间片 / 固定 / 最大前台提升 - 高性能游戏推荐)",
                "36 (长时间片 / 可变 / 无前台提升)",
                "37 (长时间片 / 可变 / 中前台提升)",
                "38 (长时间片 / 可变 / 最大前台提升)",
                "40 (长时间片 / 固定 / 无前台提升)",
                "41 (长时间片 / 固定 / 中前台提升)",
                "42 (长时间片 / 固定 / 最大前台提升)"
            ],
            parent=self.view,
            is_immediate=True
        )
        
        # 3. Input buffers (Moved from Peripheral page)
        self.keyboard_queue_card = CollapsibleComboSettingCard(
            FluentIcon.TILES,
            "",
            "",
            texts=["100 (默认)", "50", "30", "20", "16", "12", "10", "8", "6"],
            parent=self.view
        )
        self.mouse_queue_card = CollapsibleComboSettingCard(
            FluentIcon.GAME,
            "",
            "",
            texts=["100 (默认)", "50", "30", "20", "16", "12", "10", "8"],
            parent=self.view
        )
        self.keyboard_repeat_rate_card = CollapsibleComboSettingCard(
            FluentIcon.TILES,
            "",
            "",
            texts=["系统缺省配置", "配置方案 A (延迟 150ms / 速率 10)", "配置方案 B (延迟 80ms / 速率 10)", "配置方案 C (延迟 10ms / 速率 10)", "配置方案 D (极速 - 延迟 1ms / 速率 1)"],
            parent=self.view,
            is_immediate=True
        )
        
        # 4. OSD settings (Moved from GPU page)
        self.chk_osd = CollapsibleSwitchSettingCard(
            FluentIcon.VIEW,
            "",
            "",
            parent=self.view,
            is_immediate=True
        )
        
        osd_options = QWidget(self.chk_osd.view)
        osd_options_layout = QVBoxLayout(osd_options)
        osd_options_layout.setContentsMargins(10, 5, 10, 5)
        osd_options_layout.setSpacing(10)
        
        lock_layout = QHBoxLayout()
        self.lbl_osd_lock = BodyLabel("", osd_options)
        self.switch_osd_lock = SwitchButton(parent=osd_options)
        lock_layout.addWidget(self.lbl_osd_lock)
        lock_layout.addStretch()
        lock_layout.addWidget(self.switch_osd_lock)
        osd_options_layout.addLayout(lock_layout)
        
        font_layout = QHBoxLayout()
        self.lbl_osd_font = BodyLabel("", osd_options)
        self.slider_osd_font = Slider(Qt.Orientation.Horizontal, osd_options)
        self.slider_osd_font.setRange(8, 36)
        self.slider_osd_font.setSingleStep(1)
        self.lbl_osd_font_val = BodyLabel("14 px", osd_options)
        font_layout.addWidget(self.lbl_osd_font)
        font_layout.addWidget(self.slider_osd_font, 1)
        font_layout.addWidget(self.lbl_osd_font_val)
        osd_options_layout.addLayout(font_layout)
        
        checkboxes_layout = QHBoxLayout()
        self.chk_osd_cpu_gpu = CheckBox("", osd_options)
        self.chk_osd_ram = CheckBox("", osd_options)
        self.chk_osd_frametime = CheckBox("", osd_options)
        checkboxes_layout.addWidget(self.chk_osd_cpu_gpu)
        checkboxes_layout.addWidget(self.chk_osd_ram)
        checkboxes_layout.addWidget(self.chk_osd_frametime)
        osd_options_layout.addLayout(checkboxes_layout)
        
        hotkey_layout = QHBoxLayout()
        self.lbl_osd_hotkey = BodyLabel("", osd_options)
        self.btn_osd_hotkey = ShortcutEdit("Ctrl+Shift+O", osd_options)
        hotkey_layout.addWidget(self.lbl_osd_hotkey)
        hotkey_layout.addStretch()
        hotkey_layout.addWidget(self.btn_osd_hotkey)
        osd_options_layout.addLayout(hotkey_layout)

        pos_layout = QHBoxLayout()
        self.lbl_osd_x = BodyLabel("", osd_options)
        self.spin_osd_x = SpinBox(osd_options)
        self.spin_osd_x.setRange(-9999, 9999)
        self.spin_osd_x.setSingleStep(10)
        
        self.lbl_osd_y = BodyLabel("", osd_options)
        self.spin_osd_y = SpinBox(osd_options)
        self.spin_osd_y.setRange(-9999, 9999)
        self.spin_osd_y.setSingleStep(10)
        
        pos_layout.addWidget(self.lbl_osd_x)
        pos_layout.addWidget(self.spin_osd_x)
        pos_layout.addSpacing(20)
        pos_layout.addWidget(self.lbl_osd_y)
        pos_layout.addWidget(self.spin_osd_y)
        pos_layout.addStretch()
        osd_options_layout.addLayout(pos_layout)

        self.chk_osd.viewLayout.addWidget(osd_options)
        
        # 5. OCR Overlay Info Card
        from qfluentwidgets import SimpleCardWidget
        self.ocr_card = SimpleCardWidget(self.view)
        ocr_layout = QVBoxLayout(self.ocr_card)
        ocr_layout.setContentsMargins(20, 20, 20, 20)
        ocr_layout.setSpacing(10)
        self.lbl_ocr_title = SubtitleLabel("屏幕识图翻译", self.ocr_card)
        self.lbl_ocr_desc = CaptionLabel("屏幕 OCR 识别与翻译接口（需联网通讯）。框选屏幕，松开鼠标即可识别。", self.ocr_card)
        
        ocr_hotkey_layout = QHBoxLayout()
        self.lbl_ocr_hotkey = BodyLabel("触发快捷键:", self.ocr_card)
        self.btn_ocr_hotkey = ShortcutEdit("Alt+Q", self.ocr_card)
        ocr_hotkey_layout.addWidget(self.lbl_ocr_hotkey)
        ocr_hotkey_layout.addStretch()
        ocr_hotkey_layout.addWidget(self.btn_ocr_hotkey)
        
        ocr_layout.addWidget(self.lbl_ocr_title)
        ocr_layout.addWidget(self.lbl_ocr_desc)
        ocr_layout.addLayout(ocr_hotkey_layout)
        
        # Add all to layout
        self.vBoxLayout.addWidget(self.chk_boot)
        self.vBoxLayout.addWidget(self.chk_show_all_cpu)
        self.vBoxLayout.addWidget(self.win32_prio_card)
        self.vBoxLayout.addWidget(self.keyboard_queue_card)
        self.vBoxLayout.addWidget(self.mouse_queue_card)
        self.vBoxLayout.addWidget(self.keyboard_repeat_rate_card)
        self.vBoxLayout.addWidget(self.chk_osd)
        self.vBoxLayout.addWidget(self.ocr_card)
        
        # Connections
        self.chk_boot.checkedChanged.connect(self.set_autostart)
        self.chk_show_all_cpu.checkedChanged.connect(self.on_show_all_cpu_changed)
        self.win32_prio_card.comboBox.currentIndexChanged.connect(self.save_settings_immediately)
        self.keyboard_queue_card.comboBox.currentIndexChanged.connect(self.save_settings_immediately)
        self.mouse_queue_card.comboBox.currentIndexChanged.connect(self.save_settings_immediately)
        self.keyboard_repeat_rate_card.comboBox.currentIndexChanged.connect(self.save_settings_immediately)
        
        self.chk_osd.checkedChanged.connect(self.save_settings_immediately)
        self.btn_ocr_hotkey.shortcutChanged.connect(self.save_settings_immediately)
        self.switch_osd_lock.checkedChanged.connect(self.save_settings_immediately)
        self.slider_osd_font.valueChanged.connect(lambda val: self.lbl_osd_font_val.setText(f"{val} px"))
        self.slider_osd_font.valueChanged.connect(lambda: self.save_settings_immediately())
        self.chk_osd_cpu_gpu.stateChanged.connect(self.save_settings_immediately)
        self.chk_osd_ram.stateChanged.connect(self.save_settings_immediately)
        self.chk_osd_frametime.stateChanged.connect(self.save_settings_immediately)
        self.btn_osd_hotkey.shortcutChanged.connect(self.save_settings_immediately)
        self.spin_osd_x.valueChanged.connect(lambda: self.save_settings_immediately())
        self.spin_osd_y.valueChanged.connect(lambda: self.save_settings_immediately())
        
        # Rate Limiter section (Moved from Optimization Page)
        self.rate_limiter_title_header = SubtitleLabel("目标进程网卡限速控制与按键绑定", self.view)
        self.vBoxLayout.addWidget(self.rate_limiter_title_header)

        self.rate_limiter_card = SimpleCardWidget(self.view)
        rl_card_layout = QVBoxLayout(self.rate_limiter_card)
        rl_card_layout.setContentsMargins(20, 20, 20, 20)
        rl_card_layout.setSpacing(15)

        header_layout = QHBoxLayout()
        header_text_layout = QVBoxLayout()
        header_text_layout.setSpacing(4)
        self.rl_title_label = SubtitleLabel("限速与卡网参数配置", self.rate_limiter_card)
        self.rl_desc_label = CaptionLabel("启用全局低级键鼠钩子，按下绑定按键时对目标进程实施上传限制或防火墙卡网拦截", self.rate_limiter_card)
        self.rl_desc_label.setWordWrap(True)
        self.lbl_pacer_warning = CaptionLabel("警告：检测到 QoS 数据包计划程序 (ms_pacer) 绑定已在网卡上禁用，网卡限速可能失效！", self.rate_limiter_card)
        self.lbl_pacer_warning.setStyleSheet("color: #E0A800; font-weight: bold;")
        self.lbl_pacer_warning.setVisible(False)
        header_text_layout.addWidget(self.rl_title_label)
        header_text_layout.addWidget(self.rl_desc_label)
        header_text_layout.addWidget(self.lbl_pacer_warning)
        self.rl_switch = SwitchButton(self.rate_limiter_card)
        header_layout.addLayout(header_text_layout, 1)
        header_layout.addWidget(self.rl_switch)
        rl_card_layout.addLayout(header_layout)

        self.separator = QFrame(self.rate_limiter_card)
        self.separator.setFrameShape(QFrame.Shape.HLine)
        self.separator.setFrameShadow(QFrame.Shadow.Sunken)
        self.separator.setStyleSheet("background-color: rgba(255, 255, 255, 0.08);" if isDarkTheme() else "background-color: rgba(0, 0, 0, 0.06);")
        rl_card_layout.addWidget(self.separator)

        controls_grid = QGridLayout()
        controls_grid.setSpacing(15)
        controls_grid.setContentsMargins(5, 5, 5, 5)
        self.lbl_type = BodyLabel("控制模式:", self.rate_limiter_card)
        self.rl_type_combo = ComboBox(self.rate_limiter_card)
        self.rl_type_combo.setFixedWidth(160)
        self.rl_type_combo.addItem("系统原生 QoS 限速", userData="qos")
        self.rl_type_combo.addItem("内核防火墙卡网", userData="firewall")

        self.lbl_download_limit = BodyLabel("限速:", self.rate_limiter_card)
        self.rl_download_input = LineEdit(self.rate_limiter_card)
        self.rl_download_input.setFixedWidth(80)
        self.rl_download_input.setPlaceholderText("100.0")
        validator_down = QDoubleValidator(0.1, 999999.0, 1, self.rl_download_input)
        validator_down.setNotation(QDoubleValidator.Notation.StandardNotation)
        self.rl_download_input.setValidator(validator_down)
        self.rl_unit_combo = ComboBox(self.rate_limiter_card)
        self.rl_unit_combo.setFixedWidth(80)
        self.rl_unit_combo.addItem("KB/s")
        self.rl_unit_combo.addItem("Mbps")
        self.rl_unit_combo.addItem("ms")

        download_input_layout = QHBoxLayout()
        download_input_layout.setSpacing(6)
        download_input_layout.addWidget(self.rl_download_input)
        download_input_layout.addWidget(self.rl_unit_combo)
        download_input_layout.addStretch()
        self.lbl_mode = BodyLabel("触发方式:", self.rate_limiter_card)
        self.rl_mode_combo = ComboBox(self.rate_limiter_card)
        self.rl_mode_combo.setFixedWidth(160)
        self.rl_mode_combo.addItem("开关切换 (Toggle)", userData="toggle")
        self.rl_mode_combo.addItem("长按生效 (Hold)", userData="hold")
        self.rl_mode_combo.addItem("脉冲生效 (Pulse)", userData="pulse")
        
        self.lbl_direction = BodyLabel("拦截方向:", self.rate_limiter_card)
        self.rl_direction_combo = ComboBox(self.rate_limiter_card)
        self.rl_direction_combo.setFixedWidth(160)
        self.rl_direction_combo.addItem("双向拦截 (Both)", userData="both")
        self.rl_direction_combo.addItem("仅拦截下行/收包 (Inbound)", userData="inbound")
        self.rl_direction_combo.addItem("仅拦截上行/发包 (Outbound)", userData="outbound")

        self.lbl_bind = BodyLabel("触发按键:", self.rate_limiter_card)
        self.btn_rl_bind = PushButton("点击绑定按键", self.rate_limiter_card)
        self.btn_rl_bind.setFixedWidth(160)
        self.lbl_pulse_duration = BodyLabel("生效时长(秒):", self.rate_limiter_card)
        self.rl_pulse_input = LineEdit(self.rate_limiter_card)
        self.rl_pulse_input.setFixedWidth(160)
        pulse_validator = QDoubleValidator(0.01, 60.0, 3, self.rl_pulse_input)
        self.rl_pulse_input.setValidator(pulse_validator)
        
        self.lbl_pulse_delay = BodyLabel("生效前延迟(秒):", self.rate_limiter_card)
        self.rl_pulse_delay_input = LineEdit(self.rate_limiter_card)
        self.rl_pulse_delay_input.setFixedWidth(160)
        pulse_delay_validator = QDoubleValidator(0.0, 10.0, 3, self.rl_pulse_delay_input)
        self.rl_pulse_delay_input.setValidator(pulse_delay_validator)
        
        controls_grid.addWidget(self.lbl_type, 0, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        controls_grid.addWidget(self.rl_type_combo, 0, 1, Qt.AlignmentFlag.AlignLeft)
        controls_grid.addWidget(self.lbl_download_limit, 0, 2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        controls_grid.addLayout(download_input_layout, 0, 3, Qt.AlignmentFlag.AlignLeft)
        
        controls_grid.addWidget(self.lbl_direction, 1, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        controls_grid.addWidget(self.rl_direction_combo, 1, 1, Qt.AlignmentFlag.AlignLeft)
        controls_grid.addWidget(self.lbl_mode, 1, 2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        controls_grid.addWidget(self.rl_mode_combo, 1, 3, Qt.AlignmentFlag.AlignLeft)
        
        controls_grid.addWidget(self.lbl_bind, 2, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        controls_grid.addWidget(self.btn_rl_bind, 2, 1, Qt.AlignmentFlag.AlignLeft)
        controls_grid.addWidget(self.lbl_pulse_duration, 2, 2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        controls_grid.addWidget(self.rl_pulse_input, 2, 3, Qt.AlignmentFlag.AlignLeft)
        
        controls_grid.addWidget(self.lbl_pulse_delay, 3, 2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        controls_grid.addWidget(self.rl_pulse_delay_input, 3, 3, Qt.AlignmentFlag.AlignLeft)
        rl_card_layout.addLayout(controls_grid)
        
        # Prevent spacebar triggering rate limiter controls
        self.rl_switch.setFocusPolicy(Qt.NoFocus)
        self.rl_type_combo.setFocusPolicy(Qt.NoFocus)
        self.rl_unit_combo.setFocusPolicy(Qt.NoFocus)
        self.rl_mode_combo.setFocusPolicy(Qt.NoFocus)
        self.rl_direction_combo.setFocusPolicy(Qt.NoFocus)
        self.btn_rl_bind.setFocusPolicy(Qt.NoFocus)
        
        self.vBoxLayout.addWidget(self.rate_limiter_card)

        # Rate Limiter Connections
        self.rl_switch.checkedChanged.connect(self._on_rl_switch_changed)
        self.rl_type_combo.currentIndexChanged.connect(self.save_settings_immediately)
        self.rl_type_combo.currentIndexChanged.connect(self.update_rate_limiter_controls_state)

        self.rl_download_input.textChanged.connect(lambda: self.save_settings_only())
        self.rl_pulse_input.textChanged.connect(lambda: self.save_settings_only())
        self.rl_pulse_delay_input.textChanged.connect(lambda: self.save_settings_only())
        self.rl_unit_combo.currentIndexChanged.connect(self.on_rl_unit_changed)
        self.rl_mode_combo.currentIndexChanged.connect(self.save_settings_immediately)
        self.rl_direction_combo.currentIndexChanged.connect(self.save_settings_immediately)
        self.btn_rl_bind.clicked.connect(self.start_rl_binding)

        self.chk_boot.setChecked(False)
        self.autostart_checker = AutostartCheckWorker(self)
        self.autostart_checker.finished_signal.connect(self.on_autostart_checked)
        self.autostart_checker.start()
        
        self.check_pacer_status()
        self.vBoxLayout.addStretch(1)

    def on_autostart_checked(self, enabled):
        self.chk_boot.blockSignals(True)
        self.chk_boot.setChecked(enabled)
        self.chk_boot.blockSignals(False)
        self.chk_boot.applied_state = enabled
        self.chk_boot.update_status(enabled, is_pending=False)

    def on_show_all_cpu_changed(self):
        self.save_settings_immediately()
        enable = self.chk_show_all_cpu.isChecked()
        self.chk_show_all_cpu.applied_state = enable
        self.chk_show_all_cpu.update_status(enable, is_pending=False)
        logger.info(f"Show all CPU options changed to: {enable}")
        
        p = self.parent()
        while p:
            if hasattr(p, 'update_pending_status'):
                p.update_pending_status()
                break
            p = p.parent()

        win = self.parent_window
        if win and hasattr(win, 'gpu_page'):
            win.gpu_page.update_hardware_cards_visibility()

    def is_autostart(self) -> bool:
        try:
            import subprocess
            cmd = ["schtasks", "/query", "/tn", "CoreCommanderAutostart"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=3, creationflags=subprocess.CREATE_NO_WINDOW)
            return res.returncode == 0
        except Exception: 
            return False

    def set_autostart(self):
        enable = self.chk_boot.isChecked()
        import subprocess
        try:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_WRITE) as key:
                    winreg.DeleteValue(key, "CoreCommander")
            except Exception:
                pass

            if enable:
                if getattr(sys, 'frozen', False) or hasattr(sys, 'frozen'):
                    exe_path = sys.executable
                else:
                    exe_path = os.path.abspath(sys.argv[0])
                    if not exe_path.lower().endswith(".exe"):
                        exe_path = sys.executable
                        script = os.path.abspath(sys.argv[0])
                        exe_path = f'"{exe_path}" "{script}"'
                
                cmd = ["schtasks", "/create", "/tn", "CoreCommanderAutostart", "/tr", f'"{exe_path}"', "/sc", "onlogon", "/rl", "highest", "/f"]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
                if res.returncode == 0:
                    InfoBar.success("成功" if Trans.CURRENT_LANG == "zh_CN" else "Success", 
                                    "已成功创建开机自动启动任务（以管理员高特权静默启动）" if Trans.CURRENT_LANG == "zh_CN" else "Successfully enabled elevated startup boot.", parent=self)
                    logger.info("Successfully enabled elevated startup boot task via schtasks.")
                else:
                    raise Exception(res.stderr or "schtasks command failed")
            else:
                cmd = ["schtasks", "/delete", "/tn", "CoreCommanderAutostart", "/f"]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
                InfoBar.info("已取消" if Trans.CURRENT_LANG == "zh_CN" else "Disabled", 
                             "已关闭开机自动启动任务" if Trans.CURRENT_LANG == "zh_CN" else "Elevated startup boot task removed.", parent=self)
                logger.info("Successfully disabled elevated startup boot task via schtasks.")
        except Exception as e:
            logger.error(f"Failed to configure autostart task: {str(e)}")
            InfoBar.error("配置失败" if Trans.CURRENT_LANG == "zh_CN" else "Failed", 
                           f"配置自动启动失败: {str(e)}" if Trans.CURRENT_LANG == "zh_CN" else f"Failed to configure autostart task: {str(e)}", parent=self)
            
        self.chk_boot.applied_state = enable
        self.chk_boot.update_status(enable, is_pending=False)
        
        p = self.parent()
        while p:
            if hasattr(p, 'update_pending_status'):
                p.update_pending_status()
                break
            p = p.parent()
            
        self.save_settings_immediately()

    def update_rate_limiter_controls_state(self):
        is_qos = self.rl_type_combo.currentData() == "qos"
        self.rl_download_input.setEnabled(is_qos)
        self.rl_unit_combo.setEnabled(is_qos)

    def on_rl_unit_changed(self):
        self.save_settings_immediately()

    def start_rl_binding(self):
        win = self.parent_window
        if not win or not hasattr(win, 'input_hook_thread') or not win.input_hook_thread:
            return
        self.btn_rl_bind.setEnabled(False)
        self.btn_rl_bind.setText(Trans.get("rate_limiter_btn_binding"))
        try:
            win.input_hook_thread.key_bind_captured.disconnect(self.on_rl_key_captured)
        except Exception:
            pass
        win.input_hook_thread.key_bind_captured.connect(self.on_rl_key_captured)
        win.input_hook_thread.set_binding_mode(True)

    def on_rl_key_captured(self, name, code, key_type):
        win = self.parent_window
        if win:
            win.settings.rate_limiter_hotkey = name
            win.settings.rate_limiter_hotkey_code = code
            win.settings.rate_limiter_hotkey_type = key_type
            if hasattr(win, 'input_hook_thread') and win.input_hook_thread:
                win.input_hook_thread.update_hotkey(code, key_type)
            try:
                win.input_hook_thread.key_bind_captured.disconnect(self.on_rl_key_captured)
            except Exception:
                pass
            self.save_settings_immediately()
        self.btn_rl_bind.setText(f"按键: {name}")
        self.btn_rl_bind.setEnabled(True)
        InfoBar.success("绑定按键成功", f"网卡限速触发按键已成功绑定为: {name}", parent=self)

    def check_pacer_status(self):
        class PacerCheckThread(QThread):
            result_signal = Signal(bool)
            def run(self):
                try:
                    from core_commander.core.tweaks.throttler import NetworkThrottlerService
                    enabled = NetworkThrottlerService.is_pacer_enabled_any()
                    self.result_signal.emit(enabled)
                except Exception:
                    self.result_signal.emit(True)
                    
        self.pacer_thread = PacerCheckThread(self)
        self.pacer_thread.result_signal.connect(self.on_pacer_checked)
        self.pacer_thread.finished.connect(self.pacer_thread.deleteLater)
        self.pacer_thread.start()

    def on_pacer_checked(self, enabled: bool):
        self.lbl_pacer_warning.setVisible(not enabled)

    def retranslate_ui(self):
        super().retranslate_ui()
        if not hasattr(self, 'chk_boot'):
            return
        self.titleLabel.setText(Trans.get("nav_general"))
        self.chk_boot.setTitle(Trans.get("chk_boot_title"))
        self.chk_boot.setContent(Trans.get("chk_boot_desc"))
        self.chk_show_all_cpu.setTitle(Trans.get("chk_show_all_cpu_title"))
        self.chk_show_all_cpu.setContent(Trans.get("chk_show_all_cpu_desc"))
        
        self.win32_prio_card.setTitle(Trans.get("win32_prio_title"))
        self.win32_prio_card.setContent(Trans.get("win32_prio_desc"))
        
        self.keyboard_queue_card.setTitle(Trans.get("keyboard_queue_title"))
        self.keyboard_queue_card.setContent(Trans.get("keyboard_queue_desc"))
        self.mouse_queue_card.setTitle(Trans.get("mouse_queue_title"))
        self.mouse_queue_card.setContent(Trans.get("mouse_queue_desc"))
        self.keyboard_repeat_rate_card.setTitle(Trans.get("key_repeat_title"))
        self.keyboard_repeat_rate_card.setContent(Trans.get("key_repeat_desc"))
        
        self.chk_osd.setTitle("游戏内置 OSD 性能监控悬浮窗" if Trans.CURRENT_LANG == "zh_CN" else "In-Game OSD Performance Overlay")
        self.chk_osd.setContent("在游戏或桌面最上层渲染半透明监控信息，包括 FPS、帧延迟曲线、CPU/GPU占用及内存占用等(需管理员权限)" if Trans.CURRENT_LANG == "zh_CN" else "Renders a translucent overlay on top of games displaying FPS, frametime graphs, CPU/GPU and RAM/VRAM usage (requires Admin).")
        self.lbl_osd_lock.setText("OSD 窗口锁定模式 (鼠标穿透，防止游戏内误触)" if Trans.CURRENT_LANG == "zh_CN" else "OSD Window Lock Mode (mouse click-through to prevent game interference)")
        self.lbl_osd_font.setText("OSD 文本字体大小:" if Trans.CURRENT_LANG == "zh_CN" else "OSD Font Size:")
        self.chk_osd_cpu_gpu.setText("显示 CPU & GPU 使用率" if Trans.CURRENT_LANG == "zh_CN" else "Show CPU & GPU Utilization")
        self.chk_osd_ram.setText("显示 RAM & VRAM 内存占用" if Trans.CURRENT_LANG == "zh_CN" else "Show RAM & VRAM Memory Usage")
        self.chk_osd_frametime.setText("显示 帧时间历史波动曲线图" if Trans.CURRENT_LANG == "zh_CN" else "Show Frametime Jitter Graph")
        self.lbl_osd_hotkey.setText("OSD 显隐全局快捷键 (点击按键设置):" if Trans.CURRENT_LANG == "zh_CN" else "OSD Toggle Global Hotkey (Click to bind):")
        self.lbl_osd_x.setText("OSD X 坐标 (px):" if Trans.CURRENT_LANG == "zh_CN" else "OSD X Coord (px):")
        self.lbl_osd_y.setText("OSD Y 坐标 (px):" if Trans.CURRENT_LANG == "zh_CN" else "OSD Y Coord (px):")
        if hasattr(self, 'lbl_ocr_title'):
            self.lbl_ocr_title.setText(Trans.get("ocr_title", "屏幕识图翻译"))
            self.lbl_ocr_desc.setText(Trans.get("ocr_desc", "屏幕 OCR 识别与翻译接口。框选屏幕，松开鼠标即可识别。"))
            self.lbl_ocr_hotkey.setText(Trans.get("ocr_hotkey_label", "触发快捷键 (点击绑定):"))

        if hasattr(self, 'rate_limiter_title_header'):
            self.rate_limiter_title_header.setText(Trans.get("chk_rate_limiter_title"))
            self.rl_title_label.setText(Trans.get("chk_rate_limiter_title"))
            self.rl_desc_label.setText(Trans.get("chk_rate_limiter_desc"))
            self.lbl_pacer_warning.setText(Trans.get("rate_limiter_warning_pacer"))
            
            win = self.parent_window
            if win and hasattr(win, 'settings') and win.settings.rate_limiter_hotkey != "无":
                self.btn_rl_bind.setText(f"按键: {win.settings.rate_limiter_hotkey}")
            else:
                self.btn_rl_bind.setText(Trans.get("rate_limiter_btn_bind"))
                
            self.rl_mode_combo.setItemText(0, Trans.get("rate_limiter_mode_toggle"))
            self.rl_mode_combo.setItemText(1, Trans.get("rate_limiter_mode_hold"))
            self.rl_mode_combo.setItemText(2, Trans.get("rate_limiter_mode_pulse"))
            self.rl_type_combo.setItemText(0, "系统原生 QoS 限速" if Trans.CURRENT_LANG == "zh_CN" else "System QoS Limit")
            self.rl_type_combo.setItemText(1, "内核防火墙卡网" if Trans.CURRENT_LANG == "zh_CN" else "Firewall Block")
            
            self.lbl_type.setText("控制模式:" if Trans.CURRENT_LANG == "zh_CN" else "Control Mode:")
            self.lbl_download_limit.setText("限速:" if Trans.CURRENT_LANG == "zh_CN" else "Limit:")
            self.lbl_mode.setText("触发方式:" if Trans.CURRENT_LANG == "zh_CN" else "Trigger Mode:")
            self.lbl_bind.setText("触发按键:" if Trans.CURRENT_LANG == "zh_CN" else "Trigger Key:")
            self.lbl_pulse_duration.setText("脉冲时长(秒):" if Trans.CURRENT_LANG == "zh_CN" else "Pulse Duration(s):")
        
        # Block signals when updating combobox items dynamically
        self.keyboard_queue_card.comboBox.blockSignals(True)
        kb_idx = self.keyboard_queue_card.comboBox.currentIndex()
        self.keyboard_queue_card.comboBox.clear()
        kb_default = Trans.get("keyboard_queue_default")
        self.keyboard_queue_card.comboBox.addItems([kb_default, "50", "30", "20", "16", "12", "10", "8", "6"])
        self.keyboard_queue_card.comboBox.setCurrentIndex(kb_idx)
        self.keyboard_queue_card.comboBox.blockSignals(False)
        
        self.mouse_queue_card.comboBox.blockSignals(True)
        m_idx = self.mouse_queue_card.comboBox.currentIndex()
        self.mouse_queue_card.comboBox.clear()
        m_default = Trans.get("mouse_queue_default")
        self.mouse_queue_card.comboBox.addItems([m_default, "50", "30", "20", "16", "12", "10", "8"])
        self.mouse_queue_card.comboBox.setCurrentIndex(m_idx)
        self.mouse_queue_card.comboBox.blockSignals(False)
        
        self.keyboard_repeat_rate_card.comboBox.blockSignals(True)
        kr_idx = self.keyboard_repeat_rate_card.comboBox.currentIndex()
        self.keyboard_repeat_rate_card.comboBox.clear()
        self.keyboard_repeat_rate_card.comboBox.addItems(Trans.get("key_repeat_schemes"))
        self.keyboard_repeat_rate_card.comboBox.setCurrentIndex(kr_idx)
        self.keyboard_repeat_rate_card.comboBox.blockSignals(False)



    _in_getattr = set()
    def __getattr__(self, name):
        if name in ('parent_window', 'optimization_page', 'general_page'):
            raise AttributeError()
        key = (id(self), name)
        if key in self._in_getattr:
            raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")
        self._in_getattr.add(key)
        try:
            if hasattr(self, 'parent_window') and self.parent_window:
                opt_page = getattr(self.parent_window, 'optimization_page', None)
                if opt_page and opt_page is not self:
                    return getattr(opt_page, name)
        finally:
            self._in_getattr.discard(key)
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")


class DynamicStackedWidget(QStackedWidget):
    """ A QStackedWidget that adjusts its height dynamically to the current page's sizeHint to avoid huge blank scrolling areas. """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.currentChanged.connect(self.on_current_changed)

    def on_current_changed(self, index):
        self.updateGeometry()
        p = self.parentWidget()
        while p:
            p.updateGeometry()
            if isinstance(p, QScrollArea):
                p.viewport().update()
                break
            p = p.parentWidget()

    def sizeHint(self):
        current = self.currentWidget()
        if current:
            return current.sizeHint()
        return super().sizeHint()

    def minimumSizeHint(self):
        current = self.currentWidget()
        if current:
            return current.minimumSizeHint()
        return super().minimumSizeHint()


class SettingsOptimizationPage(QWidget):
    """ Consolidated Optimization Page (深度系统优化) with Tabbed Layout """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.setObjectName("SettingsOptimizationPage")
        
        # Main layout
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(30, 20, 30, 20)
        self.main_layout.setSpacing(12)
        
        # Title Label
        self.titleLabel = TitleLabel("深度系统优化", self)
        self.main_layout.addWidget(self.titleLabel)
        
        # Preset Panel
        self.presetPanel = PresetPanel(self, parent_window=self.parent_window)
        self.main_layout.addWidget(self.presetPanel)
        
        # Segmented Navigation
        self.pivot = SegmentedWidget(self)
        self.main_layout.addWidget(self.pivot)
        
        # Scroll Area for content
        self.scroll_area = ScrollArea(self)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setViewportMargins(0, 0, 0, 0)
        self.scroll_area.setStyleSheet("QScrollArea { background-color: transparent; border: none; }")
        self.scroll_area.setWidgetResizable(True)
        
        self.scroll_widget = QWidget()
        self.scroll_widget.setObjectName("scroll_widget")
        self.scroll_widget.setStyleSheet("#scroll_widget { background-color: transparent; }")
        self.scroll_area.setWidget(self.scroll_widget)
        self.scroll_layout = QVBoxLayout(self.scroll_widget)
        self.scroll_layout.setContentsMargins(0, 0, 0, 90)  # Reserve space for apply btn
        self.scroll_layout.setSpacing(12)
        
        self.stackedWidget = DynamicStackedWidget(self.scroll_widget)
        self.scroll_layout.addWidget(self.stackedWidget)
        
        self.main_layout.addWidget(self.scroll_area)
        
        # Floating Confirm Changes Button
        self.apply_btn = PrimaryPushButton("确认生效", self)
        self.apply_btn.setFocusPolicy(Qt.NoFocus)
        self.apply_btn.setIcon(FluentIcon.COMPLETED)
        self.apply_btn.resize(130, 40)
        self.apply_btn.clicked.connect(self.on_apply_clicked)
        
        # Initialize Tabs
        self.cpu_tab = QWidget()
        self.gpu_tab = QWidget()
        self.peripheral_tab = QWidget()
        self.memory_tab = QWidget()
        self.network_tab = QWidget()
        self.privacy_tab = QWidget()
        self.ux_tab = QWidget()
        
        self.cpu_layout = QVBoxLayout(self.cpu_tab)
        self.cpu_layout.setContentsMargins(0, 0, 0, 0)
        self.cpu_layout.setSpacing(12)
        
        self.gpu_layout = QVBoxLayout(self.gpu_tab)
        self.gpu_layout.setContentsMargins(0, 0, 0, 0)
        self.gpu_layout.setSpacing(12)
        
        self.peripheral_layout = QVBoxLayout(self.peripheral_tab)
        self.peripheral_layout.setContentsMargins(0, 0, 0, 0)
        self.peripheral_layout.setSpacing(12)
        
        self.memory_layout = QVBoxLayout(self.memory_tab)
        self.memory_layout.setContentsMargins(0, 0, 0, 0)
        self.memory_layout.setSpacing(12)
        
        self.network_layout = QVBoxLayout(self.network_tab)
        self.network_layout.setContentsMargins(0, 0, 0, 0)
        self.network_layout.setSpacing(12)
        
        self.privacy_layout = QVBoxLayout(self.privacy_tab)
        self.privacy_layout.setContentsMargins(0, 0, 0, 0)
        self.privacy_layout.setSpacing(12)
        
        self.ux_layout = QVBoxLayout(self.ux_tab)
        self.ux_layout.setContentsMargins(0, 0, 0, 0)
        self.ux_layout.setSpacing(12)
        
        self.stackedWidget.addWidget(self.cpu_tab)
        self.stackedWidget.addWidget(self.gpu_tab)
        self.stackedWidget.addWidget(self.peripheral_tab)
        self.stackedWidget.addWidget(self.memory_tab)
        self.stackedWidget.addWidget(self.network_tab)
        self.stackedWidget.addWidget(self.privacy_tab)
        self.stackedWidget.addWidget(self.ux_tab)
        
        # Build sub-sections
        self._init_cpu_cards()
        self._init_gpu_cards()
        self._init_peripheral_cards()
        self._init_memory_cards()
        self._init_network_cards()
        self._init_privacy_cards()
        self._init_ux_cards()
        
        # Add Pivot items
        self.pivot.addItem(routeKey="cpu", text="处理器调度", onClick=lambda: self.stackedWidget.setCurrentWidget(self.cpu_tab))
        self.pivot.addItem(routeKey="gpu", text="显卡加速", onClick=lambda: self.stackedWidget.setCurrentWidget(self.gpu_tab))
        self.pivot.addItem(routeKey="peripheral", text="外设极速", onClick=lambda: self.stackedWidget.setCurrentWidget(self.peripheral_tab))
        self.pivot.addItem(routeKey="memory", text="内存与存储", onClick=lambda: self.stackedWidget.setCurrentWidget(self.memory_tab))
        self.pivot.addItem(routeKey="network", text="网络调优", onClick=lambda: self.stackedWidget.setCurrentWidget(self.network_tab))
        self.pivot.addItem(routeKey="privacy", text="隐私精简", onClick=lambda: self.stackedWidget.setCurrentWidget(self.privacy_tab))
        self.pivot.addItem(routeKey="ux", text="系统视觉与体验", onClick=lambda: self.stackedWidget.setCurrentWidget(self.ux_tab))
        
        self.pivot.setCurrentItem("cpu")
        
        # Prevent keyboard focus on SegmentedWidget items to avoid spacebar trigger
        for child in self.pivot.findChildren(QPushButton):
            child.setFocusPolicy(Qt.NoFocus)
        
        self.retranslate_ui()

    def _init_cpu_cards(self):
        # chk_iso and chk_dog moved from settings general
        self.chk_iso = CollapsibleSwitchSettingCard(FluentIcon.SPEED_HIGH, "", "", parent=self.cpu_tab, is_immediate=True)
        self.chk_dog = CollapsibleSwitchSettingCard(FluentIcon.STOP_WATCH, "", "", parent=self.cpu_tab, is_immediate=True)
        
        self.chk_parking = CollapsibleSwitchSettingCard(FluentIcon.SPEED_HIGH, "", "", parent=self.cpu_tab, is_immediate=True)
        self.chk_epp = CollapsibleSwitchSettingCard(FluentIcon.UPDATE, "", "", parent=self.cpu_tab, is_immediate=True)
        self.chk_hpet = CollapsibleSwitchSettingCard(FluentIcon.STOP_WATCH, "", "", parent=self.cpu_tab)
        self.chk_dwm = CollapsibleSwitchSettingCard(FluentIcon.LEAF, "", "", parent=self.cpu_tab, is_immediate=True)
        self.chk_dpc = CollapsibleSwitchSettingCard(FluentIcon.STOP_WATCH, "", "", parent=self.cpu_tab)
        self.chk_timer_res = CollapsibleSwitchSettingCard(FluentIcon.STOP_WATCH, "", "", parent=self.cpu_tab, is_immediate=True)
        self.chk_naraka_priority = CollapsibleSwitchSettingCard(FluentIcon.GAME, "", "", parent=self.cpu_tab, is_immediate=True)
        self.chk_child = CollapsibleSwitchSettingCard(FluentIcon.TILES, "", "", parent=self.cpu_tab, is_immediate=True)
        self.chk_driver_prio = CollapsibleSwitchSettingCard(FluentIcon.SPEED_HIGH, "", "", parent=self.cpu_tab)
        self.chk_vulnerable_driver_blocklist = CollapsibleSwitchSettingCard(FluentIcon.DELETE, "", "", parent=self.cpu_tab)
        self.chk_power_throttling = CollapsibleSwitchSettingCard(FluentIcon.SPEED_HIGH, "", "", parent=self.cpu_tab)
        self.chk_client_priority_demote = CollapsibleSwitchSettingCard(FluentIcon.TILES, "", "", parent=self.cpu_tab, is_immediate=True)

        self.cpu_layout.addWidget(self.chk_iso)
        self.cpu_layout.addWidget(self.chk_dog)
        self.cpu_layout.addWidget(self.chk_parking)
        self.cpu_layout.addWidget(self.chk_epp)
        self.cpu_layout.addWidget(self.chk_hpet)
        self.cpu_layout.addWidget(self.chk_dwm)
        self.cpu_layout.addWidget(self.chk_dpc)
        self.cpu_layout.addWidget(self.chk_timer_res)
        self.cpu_layout.addWidget(self.chk_naraka_priority)
        self.cpu_layout.addWidget(self.chk_child)
        self.cpu_layout.addWidget(self.chk_driver_prio)
        self.cpu_layout.addWidget(self.chk_vulnerable_driver_blocklist)
        self.cpu_layout.addWidget(self.chk_power_throttling)
        self.cpu_layout.addWidget(self.chk_client_priority_demote)

        self.chk_iso.checkedChanged.connect(self.save_settings_immediately)
        self.chk_dog.checkedChanged.connect(self.save_settings_immediately)
        self.chk_parking.checkedChanged.connect(self.save_settings_immediately)
        self.chk_epp.checkedChanged.connect(self.save_settings_immediately)
        self.chk_hpet.checkedChanged.connect(self.save_settings_only)
        self.chk_dwm.checkedChanged.connect(self.save_settings_immediately)
        self.chk_dpc.checkedChanged.connect(self.save_settings_only)
        self.chk_timer_res.checkedChanged.connect(self.save_settings_immediately)
        self.chk_naraka_priority.checkedChanged.connect(self.save_settings_immediately)
        self.chk_child.checkedChanged.connect(self.save_settings_immediately)
        self.chk_vulnerable_driver_blocklist.checkedChanged.connect(self.save_settings_only)
        self.chk_driver_prio.checkedChanged.connect(self.save_settings_only)
        self.chk_power_throttling.checkedChanged.connect(self.save_settings_only)
        self.chk_client_priority_demote.checkedChanged.connect(self.save_settings_immediately)
        
        self.cpu_layout.addStretch(1)

    def _init_gpu_cards(self):
        self.chk_preemption = CollapsibleSwitchSettingCard(FluentIcon.LEAF, "", "", parent=self.gpu_tab)
        self.chk_dwm_wet = CollapsibleSwitchSettingCard(FluentIcon.LEAF, "", "", parent=self.gpu_tab, is_immediate=True)
        self.chk_directx = CollapsibleSwitchSettingCard(FluentIcon.GAME, "", "", parent=self.gpu_tab)
        self.chk_gpu_firmware = CollapsibleSwitchSettingCard(FluentIcon.DEVELOPER_TOOLS, "", "", parent=self.gpu_tab)
        self.chk_gpu_pstate = CollapsibleSwitchSettingCard(FluentIcon.SPEED_HIGH, "", "", parent=self.gpu_tab)
        self.chk_intel_plan = CollapsibleSwitchSettingCard(FluentIcon.UPDATE, "", "", parent=self.gpu_tab, is_immediate=True)
        self.chk_amd_plan = CollapsibleSwitchSettingCard(FluentIcon.UPDATE, "", "", parent=self.gpu_tab, is_immediate=True)
        self.chk_gpu_opt = CollapsibleSwitchSettingCard(FluentIcon.SPEED_HIGH, "", "", parent=self.gpu_tab, is_immediate=True)
        self.chk_pcipower = CollapsibleSwitchSettingCard(FluentIcon.DEVELOPER_TOOLS, "", "", parent=self.gpu_tab)
        self.chk_gpu_irq = CollapsibleSwitchSettingCard(FluentIcon.DEVELOPER_TOOLS, "", "", parent=self.gpu_tab)
        self.chk_hags = CollapsibleSwitchSettingCard(FluentIcon.SPEED_HIGH, "", "", parent=self.gpu_tab)
        self.chk_gpu_nip = CollapsibleSwitchSettingCard(FluentIcon.DEVELOPER_TOOLS, "", "", parent=self.gpu_tab, is_immediate=True)
        self.chk_gpu_msi = CollapsibleSwitchSettingCard(FluentIcon.DEVELOPER_TOOLS, "", "", parent=self.gpu_tab)
        self.chk_gpu_msi.custom_status_type = "gpu_msi"
        self.chk_global_fse = CollapsibleSwitchSettingCard(FluentIcon.GAME, "", "", parent=self.gpu_tab)
        self.chk_game_fse = CollapsibleSwitchSettingCard(FluentIcon.GAME, "", "", parent=self.gpu_tab)
        self.chk_game_gpu_preference = CollapsibleSwitchSettingCard(FluentIcon.SPEED_HIGH, "", "", parent=self.gpu_tab, is_immediate=True)
        self.chk_irq_affinity = CollapsibleSwitchSettingCard(FluentIcon.DEVELOPER_TOOLS, "", "", parent=self.gpu_tab)
        self.chk_dwm_presentation = CollapsibleSwitchSettingCard(FluentIcon.GAME, "", "", parent=self.gpu_tab)

        self.gpu_layout.addWidget(self.chk_preemption)
        self.gpu_layout.addWidget(self.chk_dwm_wet)
        self.gpu_layout.addWidget(self.chk_directx)
        self.gpu_layout.addWidget(self.chk_gpu_firmware)
        self.gpu_layout.addWidget(self.chk_gpu_pstate)
        self.gpu_layout.addWidget(self.chk_intel_plan)
        self.gpu_layout.addWidget(self.chk_amd_plan)
        self.gpu_layout.addWidget(self.chk_gpu_opt)
        self.gpu_layout.addWidget(self.chk_pcipower)
        self.gpu_layout.addWidget(self.chk_gpu_irq)
        self.gpu_layout.addWidget(self.chk_hags)
        self.gpu_layout.addWidget(self.chk_gpu_nip)
        self.gpu_layout.addWidget(self.chk_gpu_msi)
        self.gpu_layout.addWidget(self.chk_global_fse)
        self.gpu_layout.addWidget(self.chk_game_fse)
        self.gpu_layout.addWidget(self.chk_game_gpu_preference)
        self.gpu_layout.addWidget(self.chk_irq_affinity)
        self.gpu_layout.addWidget(self.chk_dwm_presentation)

        self.chk_preemption.checkedChanged.connect(self.save_settings_only)
        self.chk_dwm_wet.checkedChanged.connect(self.save_settings_immediately)
        self.chk_directx.checkedChanged.connect(self.save_settings_only)
        self.chk_gpu_firmware.checkedChanged.connect(self.save_settings_only)
        self.chk_gpu_pstate.checkedChanged.connect(self.save_settings_only)
        self.chk_intel_plan.checkedChanged.connect(self.save_settings_immediately)
        self.chk_amd_plan.checkedChanged.connect(self.save_settings_immediately)
        self.chk_gpu_opt.checkedChanged.connect(self.save_settings_immediately)
        self.chk_pcipower.checkedChanged.connect(self.save_settings_only)
        self.chk_gpu_irq.checkedChanged.connect(self.save_settings_only)
        self.chk_hags.checkedChanged.connect(self.save_settings_only)
        self.chk_gpu_nip.checkedChanged.connect(self.save_settings_immediately)
        self.chk_gpu_msi.checkedChanged.connect(self.save_settings_only)
        self.chk_global_fse.checkedChanged.connect(self.save_settings_only)
        self.chk_game_fse.checkedChanged.connect(self.save_settings_only)
        self.chk_game_gpu_preference.checkedChanged.connect(self.save_settings_immediately)
        self.chk_irq_affinity.checkedChanged.connect(self.save_settings_only)
        self.chk_dwm_presentation.checkedChanged.connect(self.save_settings_only)
        
        self.gpu_layout.addStretch(1)

    def _init_peripheral_cards(self):
        self.chk_usb_lat = CollapsibleSwitchSettingCard(FluentIcon.SPEED_HIGH, "", "", parent=self.peripheral_tab)
        self.chk_imod = CollapsibleSwitchSettingCard(FluentIcon.SPEED_HIGH, "", "", parent=self.peripheral_tab)
        self.chk_mouse_lat = CollapsibleSwitchSettingCard(FluentIcon.LEAF, "", "", parent=self.peripheral_tab)
        self.chk_sticky_keys = CollapsibleSwitchSettingCard(FluentIcon.APPLICATION, "", "", parent=self.peripheral_tab)
        self.chk_dynamic_lighting = CollapsibleSwitchSettingCard(FluentIcon.DEVELOPER_TOOLS, "", "", parent=self.peripheral_tab)

        self.peripheral_layout.addWidget(self.chk_usb_lat)
        self.peripheral_layout.addWidget(self.chk_imod)
        self.peripheral_layout.addWidget(self.chk_mouse_lat)
        self.peripheral_layout.addWidget(self.chk_sticky_keys)
        self.peripheral_layout.addWidget(self.chk_dynamic_lighting)

        self.chk_usb_lat.checkedChanged.connect(self.save_settings_only)
        self.chk_imod.checkedChanged.connect(self.save_settings_only)
        self.chk_mouse_lat.checkedChanged.connect(self.save_settings_only)
        self.chk_sticky_keys.checkedChanged.connect(self.save_settings_only)
        self.chk_dynamic_lighting.checkedChanged.connect(self.save_settings_only)
        
        self.peripheral_layout.addStretch(1)

    def _init_memory_cards(self):
        self.chk_ram_opt = CollapsibleSwitchSettingCard(FluentIcon.SPEED_HIGH, "", "", parent=self.memory_tab)
        self.chk_nvme_opt = CollapsibleSwitchSettingCard(FluentIcon.DEVELOPER_TOOLS, "", "", parent=self.memory_tab)
        self.chk_memory_comp = CollapsibleSwitchSettingCard(FluentIcon.SPEED_HIGH, "", "", parent=self.memory_tab)
        self.chk_config_alloc = CollapsibleSwitchSettingCard(FluentIcon.DEVELOPER_TOOLS, "", "", parent=self.memory_tab)
        self.chk_hard_working_set = CollapsibleSwitchSettingCard(FluentIcon.SPEED_HIGH, "", "", parent=self.memory_tab, is_immediate=True)
        self.chk_prefetcher = CollapsibleSwitchSettingCard(FluentIcon.SPEED_HIGH, "", "", parent=self.memory_tab, is_immediate=True)
        self.chk_storage_msi = CollapsibleSwitchSettingCard(FluentIcon.DEVELOPER_TOOLS, "", "", parent=self.memory_tab)

        self.memory_layout.addWidget(self.chk_ram_opt)
        self.memory_layout.addWidget(self.chk_nvme_opt)
        self.memory_layout.addWidget(self.chk_memory_comp)
        self.memory_layout.addWidget(self.chk_config_alloc)
        self.memory_layout.addWidget(self.chk_hard_working_set)
        self.memory_layout.addWidget(self.chk_prefetcher)
        self.memory_layout.addWidget(self.chk_storage_msi)

        self.chk_ram_opt.checkedChanged.connect(self.save_settings_only)
        self.chk_nvme_opt.checkedChanged.connect(self.save_settings_only)
        self.chk_memory_comp.checkedChanged.connect(self.save_settings_only)
        self.chk_config_alloc.checkedChanged.connect(self.save_settings_only)
        self.chk_hard_working_set.checkedChanged.connect(self.save_settings_immediately)
        self.chk_prefetcher.checkedChanged.connect(self.save_settings_immediately)
        self.chk_storage_msi.checkedChanged.connect(self.save_settings_only)
        
        self.memory_layout.addStretch(1)

    def _init_network_cards(self):
        self.chk_network = CollapsibleSwitchSettingCard(FluentIcon.GLOBE, "", "", parent=self.network_tab)
        self.chk_ult_net = CollapsibleSwitchSettingCard(FluentIcon.GLOBE, "", "", parent=self.network_tab)
        self.chk_dns = CollapsibleSwitchSettingCard(FluentIcon.GLOBE, "", "", parent=self.network_tab)
        self.chk_net_imod = CollapsibleSwitchSettingCard(FluentIcon.GLOBE, "", "", parent=self.network_tab, is_immediate=True)
        self.chk_net_bindings = CollapsibleSwitchSettingCard(FluentIcon.GLOBE, "", "", parent=self.network_tab, is_immediate=True)
        self.chk_wifi_tweak = CollapsibleSwitchSettingCard(FluentIcon.GLOBE, "", "", parent=self.network_tab)
        self.chk_tcp_bbr = CollapsibleSwitchSettingCard(FluentIcon.GLOBE, "", "", parent=self.network_tab, is_immediate=True)
        self.chk_eee = CollapsibleSwitchSettingCard(FluentIcon.GLOBE, "", "", parent=self.network_tab, is_immediate=True)
        self.chk_network_msi = CollapsibleSwitchSettingCard(FluentIcon.GLOBE, "", "", parent=self.network_tab)

        self.network_layout.addWidget(self.chk_network)
        self.network_layout.addWidget(self.chk_ult_net)
        self.network_layout.addWidget(self.chk_dns)
        self.network_layout.addWidget(self.chk_net_imod)
        self.network_layout.addWidget(self.chk_net_bindings)
        self.network_layout.addWidget(self.chk_wifi_tweak)
        self.network_layout.addWidget(self.chk_tcp_bbr)
        self.network_layout.addWidget(self.chk_eee)
        self.network_layout.addWidget(self.chk_network_msi)

        self.chk_network.checkedChanged.connect(self.save_settings_only)
        self.chk_ult_net.checkedChanged.connect(self.save_settings_only)
        self.chk_dns.checkedChanged.connect(self.save_settings_only)
        self.chk_net_imod.checkedChanged.connect(self.save_settings_immediately)
        self.chk_net_bindings.checkedChanged.connect(self.save_settings_immediately)
        self.chk_wifi_tweak.checkedChanged.connect(self.save_settings_only)
        self.chk_tcp_bbr.checkedChanged.connect(self.save_settings_immediately)
        self.chk_eee.checkedChanged.connect(self.save_settings_immediately)
        self.chk_network_msi.checkedChanged.connect(self.save_settings_only)

        # MTU Optimization section
        self.network_layout.addSpacing(15)
        self.mtu_title_header = SubtitleLabel("网卡最大传输单元 (MTU) 调优", self.network_tab)
        self.network_layout.addWidget(self.mtu_title_header)
        self.mtu_card = SimpleCardWidget(self.network_tab)
        self.mtu_card.setMinimumHeight(110)
        mtu_layout = QHBoxLayout(self.mtu_card)
        mtu_layout.setContentsMargins(20, 15, 20, 15)
        mtu_text_layout = QVBoxLayout()
        self.mtu_title_label = SubtitleLabel("网卡 MTU 状态探测与调优")
        self.mtu_desc_label = CaptionLabel("检测并配置物理适配器的最优最大传输单元 (MTU) 大小，写入 TCP/IP 协议栈并重启适配器以提高吞吐")
        self.lbl_current_mtu = CaptionLabel("当前 MTU: --", self.mtu_card)
        self.lbl_current_mtu.setStyleSheet("font-weight: bold; color: #0078D4;")
        mtu_text_layout.addWidget(self.mtu_title_label)
        mtu_text_layout.addWidget(self.mtu_desc_label)
        mtu_text_layout.addWidget(self.lbl_current_mtu)
        self.combo_adapter = ComboBox(self.mtu_card)
        self.combo_adapter.setFixedWidth(200)
        
        self.combo_adapter.blockSignals(True)
        self.combo_adapter.clear()
        interfaces = SystemTweaksService.get_network_interfaces_details()
        for iface in interfaces:
            name = iface["name"]
            desc = iface["description"]
            is_connected = iface.get("is_connected", True)
            display_name = f"{name} ({desc})" if desc else f"{name}"
            self.combo_adapter.addItem(display_name, userData=name)
        self.combo_adapter.blockSignals(False)
        self.combo_adapter.currentIndexChanged.connect(self.update_current_mtu)
        
        self.btn_mtu = PushButton("自动配置最优 MTU", self.mtu_card)
        self.btn_mtu.setIcon(FluentIcon.GLOBE)
        self.btn_mtu.clicked.connect(self.start_mtu_optimization)
        mtu_layout.addLayout(mtu_text_layout, 1)
        mtu_layout.addWidget(self.combo_adapter)
        mtu_layout.addWidget(self.btn_mtu)
        self.network_layout.addWidget(self.mtu_card)

        self.update_current_mtu()
        self.network_layout.addStretch(1)

    def _init_privacy_cards(self):
        self.chk_services = CollapsibleSwitchSettingCard(FluentIcon.DELETE, "", "", parent=self.privacy_tab, is_immediate=True)
        self.chk_wsearch = CollapsibleSwitchSettingCard(FluentIcon.SEARCH, "", "", parent=self.privacy_tab, is_immediate=True)
        self.chk_spectre = CollapsibleSwitchSettingCard(FluentIcon.SPEED_HIGH, "", "", parent=self.privacy_tab)
        self.chk_copilot = CollapsibleSwitchSettingCard(getattr(FluentIcon, "ROBOT", FluentIcon.APPLICATION), "", "", parent=self.privacy_tab)
        self.chk_gamedvr = CollapsibleSwitchSettingCard(FluentIcon.GAME, "", "", parent=self.privacy_tab, is_immediate=True)
        self.chk_dev_power = CollapsibleSwitchSettingCard(FluentIcon.DEVELOPER_TOOLS, "", "", parent=self.privacy_tab)
        self.chk_uac = CollapsibleSwitchSettingCard(FluentIcon.DELETE, "", "", parent=self.privacy_tab)
        self.chk_desktop_heap = CollapsibleSwitchSettingCard(FluentIcon.TILES, "", "", parent=self.privacy_tab)
        self.chk_download_maps = CollapsibleSwitchSettingCard(FluentIcon.DELETE, "", "", parent=self.privacy_tab, is_immediate=True)
        self.chk_bg_apps = CollapsibleSwitchSettingCard(FluentIcon.DELETE, "", "", parent=self.privacy_tab)
        self.chk_map_updates = CollapsibleSwitchSettingCard(FluentIcon.DELETE, "", "", parent=self.privacy_tab, is_immediate=True)
        self.chk_autoshare = CollapsibleSwitchSettingCard(FluentIcon.DELETE, "", "", parent=self.privacy_tab)
        self.chk_autorun = CollapsibleSwitchSettingCard(FluentIcon.DELETE, "", "", parent=self.privacy_tab)
        self.chk_hyperv = CollapsibleSwitchSettingCard(FluentIcon.SPEED_HIGH, "", "", parent=self.privacy_tab)
        self.chk_settings_sync = CollapsibleSwitchSettingCard(FluentIcon.GLOBE, "", "", parent=self.privacy_tab)
        self.chk_xbox_save = CollapsibleSwitchSettingCard(FluentIcon.GAME, "", "", parent=self.privacy_tab)
        self.chk_store_auto_update = CollapsibleSwitchSettingCard(FluentIcon.APPLICATION, "", "", parent=self.privacy_tab)
        self.chk_web_search = CollapsibleSwitchSettingCard(FluentIcon.SEARCH, "", "", parent=self.privacy_tab, is_immediate=True)
        self.chk_telemetry_tasks = CollapsibleSwitchSettingCard(FluentIcon.DELETE, "", "", parent=self.privacy_tab, is_immediate=True)
        self.chk_extreme_debloat = CollapsibleSwitchSettingCard(FluentIcon.DELETE, "", "", parent=self.privacy_tab, is_immediate=True)

        self.card_uwp_debloat = SimpleCardWidget(self.privacy_tab)
        self.card_uwp_debloat.setMinimumHeight(96)
        uwp_layout = QHBoxLayout(self.card_uwp_debloat)
        uwp_layout.setContentsMargins(20, 15, 20, 15)
        uwp_text_layout = QVBoxLayout()
        uwp_text_layout.setSpacing(6)
        self.uwp_title_label = SubtitleLabel("", self.card_uwp_debloat)
        self.uwp_desc_label = CaptionLabel("", self.card_uwp_debloat)
        self.uwp_desc_label.setWordWrap(True)
        uwp_text_layout.addWidget(self.uwp_title_label)
        uwp_text_layout.addWidget(self.uwp_desc_label)
        self.btn_uwp_debloat = PushButton("", self.card_uwp_debloat)
        self.btn_uwp_debloat.setIcon(FluentIcon.DELETE)
        self.btn_uwp_debloat.clicked.connect(self.show_uwp_debloat_dialog)
        uwp_layout.addLayout(uwp_text_layout, 1)
        uwp_layout.addWidget(self.btn_uwp_debloat)

        self.privacy_layout.addWidget(self.chk_services)
        self.privacy_layout.addWidget(self.chk_wsearch)
        self.privacy_layout.addWidget(self.chk_spectre)
        self.privacy_layout.addWidget(self.chk_copilot)
        self.privacy_layout.addWidget(self.chk_gamedvr)
        self.privacy_layout.addWidget(self.chk_dev_power)
        self.privacy_layout.addWidget(self.chk_uac)
        self.privacy_layout.addWidget(self.chk_desktop_heap)
        self.privacy_layout.addWidget(self.chk_download_maps)
        self.privacy_layout.addWidget(self.chk_bg_apps)
        self.privacy_layout.addWidget(self.chk_map_updates)
        self.privacy_layout.addWidget(self.chk_autoshare)
        self.privacy_layout.addWidget(self.chk_autorun)
        self.privacy_layout.addWidget(self.chk_hyperv)
        self.privacy_layout.addWidget(self.chk_settings_sync)
        self.privacy_layout.addWidget(self.chk_xbox_save)
        self.privacy_layout.addWidget(self.chk_store_auto_update)
        self.privacy_layout.addWidget(self.chk_web_search)
        self.privacy_layout.addWidget(self.chk_telemetry_tasks)
        self.privacy_layout.addWidget(self.chk_extreme_debloat)
        self.privacy_layout.addSpacing(15)
        self.privacy_layout.addWidget(self.card_uwp_debloat)

        self.privacy_layout.addSpacing(15)
        self.security_title_header = SubtitleLabel("安全防护与系统通知调优", self.privacy_tab)
        self.privacy_layout.addWidget(self.security_title_header)

        self.chk_security_notifications = CollapsibleSwitchSettingCard(getattr(FluentIcon, "FEEDBACK", FluentIcon.INFO), "", "", parent=self.privacy_tab, is_immediate=True)
        self.chk_defender = CollapsibleSwitchSettingCard(getattr(FluentIcon, "SHIELD", FluentIcon.SPEED_HIGH), "", "", parent=self.privacy_tab)
        self.chk_smartscreen = CollapsibleSwitchSettingCard(FluentIcon.INFO, "", "", parent=self.privacy_tab, is_immediate=True)
        self.chk_firewall = CollapsibleSwitchSettingCard(FluentIcon.GLOBE, "", "", parent=self.privacy_tab, is_immediate=True)

        self.privacy_layout.addWidget(self.chk_security_notifications)
        self.privacy_layout.addWidget(self.chk_defender)
        self.privacy_layout.addWidget(self.chk_smartscreen)
        self.privacy_layout.addWidget(self.chk_firewall)

        self.chk_services.checkedChanged.connect(self.save_settings_immediately)
        self.chk_wsearch.checkedChanged.connect(self.save_settings_immediately)
        self.chk_spectre.checkedChanged.connect(self.save_settings_only)
        self.chk_copilot.checkedChanged.connect(self.save_settings_only)
        self.chk_gamedvr.checkedChanged.connect(self.save_settings_immediately)
        self.chk_dev_power.checkedChanged.connect(self.save_settings_only)
        self.chk_uac.checkedChanged.connect(self.save_settings_only)
        self.chk_desktop_heap.checkedChanged.connect(self.save_settings_only)
        self.chk_download_maps.checkedChanged.connect(self.save_settings_immediately)
        self.chk_bg_apps.checkedChanged.connect(self.save_settings_only)
        self.chk_map_updates.checkedChanged.connect(self.save_settings_immediately)
        self.chk_autoshare.checkedChanged.connect(self.save_settings_only)
        self.chk_autorun.checkedChanged.connect(self.save_settings_only)
        self.chk_hyperv.checkedChanged.connect(self.save_settings_only)
        self.chk_settings_sync.checkedChanged.connect(self.save_settings_only)
        self.chk_xbox_save.checkedChanged.connect(self.save_settings_only)
        self.chk_store_auto_update.checkedChanged.connect(self.save_settings_only)
        self.chk_web_search.checkedChanged.connect(self.save_settings_immediately)
        self.chk_telemetry_tasks.checkedChanged.connect(self.save_settings_immediately)
        self.chk_extreme_debloat.checkedChanged.connect(self.save_settings_immediately)
        self.chk_security_notifications.checkedChanged.connect(self.save_settings_immediately)
        self.chk_defender.checkedChanged.connect(self.save_settings_only)
        self.chk_smartscreen.checkedChanged.connect(self.save_settings_immediately)
        self.chk_firewall.checkedChanged.connect(self.save_settings_immediately)
        
        self.privacy_layout.addStretch(1)

    def _init_ux_cards(self):
        # UX cards moved from general settings (Image 4 features)
        self.chk_visual_effects = CollapsibleSwitchSettingCard(FluentIcon.SPEED_HIGH, "", "", parent=self.ux_tab, is_immediate=True)
        self.chk_transparency = CollapsibleSwitchSettingCard(FluentIcon.APPLICATION, "", "", parent=self.ux_tab, is_immediate=True)
        self.chk_consult_interests = CollapsibleSwitchSettingCard(FluentIcon.LEAF, "", "", parent=self.ux_tab)
        self.chk_tips_suggestions = CollapsibleSwitchSettingCard(FluentIcon.LEAF, "", "", parent=self.ux_tab)
        self.chk_widgets = CollapsibleSwitchSettingCard(FluentIcon.APPLICATION, "", "", parent=self.ux_tab)
        self.chk_startup_delay = CollapsibleSwitchSettingCard(FluentIcon.STOP_WATCH, "", "", parent=self.ux_tab)
        self.chk_menu_delay = CollapsibleSwitchSettingCard(FluentIcon.ALIGNMENT, "", "", parent=self.ux_tab)
        self.chk_prevent_device_encryption = CollapsibleSwitchSettingCard(FluentIcon.VPN, "", "", parent=self.ux_tab)
        self.chk_spotlight = CollapsibleSwitchSettingCard(FluentIcon.LEAF, "", "", parent=self.ux_tab)

        self.ux_layout.addWidget(self.chk_visual_effects)
        self.ux_layout.addWidget(self.chk_transparency)
        self.ux_layout.addWidget(self.chk_consult_interests)
        self.ux_layout.addWidget(self.chk_tips_suggestions)
        self.ux_layout.addWidget(self.chk_widgets)
        self.ux_layout.addWidget(self.chk_startup_delay)
        self.ux_layout.addWidget(self.chk_menu_delay)
        self.ux_layout.addWidget(self.chk_prevent_device_encryption)
        self.ux_layout.addWidget(self.chk_spotlight)

        self.chk_visual_effects.checkedChanged.connect(self.save_settings_immediately)
        self.chk_transparency.checkedChanged.connect(self.save_settings_immediately)
        self.chk_consult_interests.checkedChanged.connect(self.save_settings_only)
        self.chk_tips_suggestions.checkedChanged.connect(self.save_settings_only)
        self.chk_widgets.checkedChanged.connect(self.save_settings_only)
        self.chk_startup_delay.checkedChanged.connect(self.save_settings_only)
        self.chk_menu_delay.checkedChanged.connect(self.save_settings_only)
        self.chk_prevent_device_encryption.checkedChanged.connect(self.save_settings_only)
        self.chk_spotlight.checkedChanged.connect(self.save_settings_only)
        
        self.ux_layout.addStretch(1)

    def _on_rl_switch_changed(self, checked):
        win = self.parent_window
        is_loading = win and (getattr(win, 'is_loading_preset', False) or getattr(win, 'is_loading_settings', False))
        if checked:
            from core_commander.ui.activation_dialog import require_license
            if not require_license(self, "目标进程网卡限速控制与按键绑定", silent=is_loading):
                self.rl_switch.blockSignals(True)
                self.rl_switch.setChecked(False)
                self.rl_switch.blockSignals(False)
                return
        if is_loading:
            return
        self.save_settings_immediately()

    def save_settings_immediately(self):
        win = self.parent_window
        if win and (getattr(win, 'is_loading_preset', False) or getattr(win, 'is_loading_settings', False)):
            return
            
        sender = self.sender()
        if sender and win:
            attr_name = None
            for name, attr in self.__dict__.items():
                if (attr is sender or 
                    getattr(attr, 'comboBox', None) is sender or 
                    getattr(attr, 'switchButton', None) is sender or 
                    getattr(attr, 'shortcutButton', None) is sender or 
                    getattr(attr, 'slider', None) is sender or 
                    getattr(attr, 'spinBox', None) is sender):
                    attr_name = name
                    break
            if attr_name and hasattr(win, 'register_changed_immediate_key'):
                win.register_changed_immediate_key(attr_name)

        if hasattr(win, 'trigger_debounced_save_settings'):
            win.trigger_debounced_save_settings()
        elif hasattr(win, 'save_settings'):
            win.save_settings()
            if hasattr(win, 'apply_immediate_tweaks_silently'):
                win.apply_immediate_tweaks_silently()

    def save_settings_only(self):
        win = self.parent_window
        if win and (getattr(win, 'is_loading_preset', False) or getattr(win, 'is_loading_settings', False)):
            return
        if hasattr(win, 'trigger_debounced_save_settings_no_tweaks'):
            win.trigger_debounced_save_settings_no_tweaks()
        elif hasattr(win, 'save_settings'):
            win.save_settings()

    def on_apply_clicked(self):
        win = self.parent_window
        if win and hasattr(win, 'apply_system_tweaks'):
            win.apply_system_tweaks()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, 'apply_btn'):
            btn_w = 130
            btn_h = 40
            margin = 30
            self.apply_btn.raise_()
            self.apply_btn.setGeometry(
                self.width() - btn_w - margin,
                self.height() - btn_h - margin,
                btn_w,
                btn_h
            )

    def show_uwp_debloat_dialog(self):
        from core_commander.ui.dialogs import UwpDebloatDialog
        dialog = UwpDebloatDialog(self.window())
        dialog.exec()

    def update_hardware_cards_visibility(self):
        win = self.parent_window
        if not win:
            return
            
        show_all = win.settings.show_all_cpu_options
        cpu_vendor = getattr(win, 'cpu_vendor', "")
        gpu_vendor = getattr(win, 'gpu_vendor', "")
        
        intel_visible = show_all or (cpu_vendor == "INTEL")
        amd_visible = show_all or (cpu_vendor == "AMD")
        
        if hasattr(self, 'chk_intel_plan'):
            self.chk_intel_plan.setVisible(intel_visible)
        if hasattr(self, 'chk_amd_plan'):
            self.chk_amd_plan.setVisible(amd_visible)
        
        if hasattr(self, 'chk_gpu_opt'):
            if gpu_vendor == "AMD":
                self.chk_gpu_opt.setTitle(Trans.get("chk_gpu_opt_title_amd"))
                self.chk_gpu_opt.card.titleLabel.setText(Trans.get("chk_gpu_opt_title_amd"))
                self.chk_gpu_opt.descLabel.setText(Trans.get("chk_gpu_opt_desc_amd"))
                self.chk_gpu_opt.setVisible(True)
            elif gpu_vendor == "NVIDIA":
                self.chk_gpu_opt.setTitle(Trans.get("chk_gpu_opt_title_nvidia"))
                self.chk_gpu_opt.card.titleLabel.setText(Trans.get("chk_gpu_opt_title_nvidia"))
                self.chk_gpu_opt.descLabel.setText(Trans.get("chk_gpu_opt_desc_nvidia"))
                self.chk_gpu_opt.setVisible(True)
            else:
                self.chk_gpu_opt.setTitle(Trans.get("chk_gpu_opt_title_generic"))
                self.chk_gpu_opt.card.titleLabel.setText(Trans.get("chk_gpu_opt_title_generic"))
                self.chk_gpu_opt.descLabel.setText(Trans.get("chk_gpu_opt_desc_generic"))
                self.chk_gpu_opt.setVisible(show_all)
  
        if hasattr(self, 'chk_gpu_pstate'):
            self.chk_gpu_pstate.setVisible(show_all or (gpu_vendor == "NVIDIA"))
        if hasattr(self, 'chk_gpu_nip'):
            self.chk_gpu_nip.setVisible(show_all or (gpu_vendor == "NVIDIA"))

    def update_current_mtu(self):
        if not hasattr(self, 'lbl_current_mtu') or not hasattr(self, 'combo_adapter'):
            return
        adapter = self.combo_adapter.currentData()
        if not adapter:
            self.lbl_current_mtu.setText(Trans.get("mtu_current").format(mtu="--"))
            return
        mtu = SystemTweaksService.get_interface_mtu(adapter)
        self.lbl_current_mtu.setText(Trans.get("mtu_current").format(mtu=str(mtu)))

    def start_mtu_optimization(self):
        adapter = self.combo_adapter.currentData()
        if not adapter:
            InfoBar.warning(Trans.get("mtu_warn_title"), Trans.get("mtu_warn_desc"), parent=self)
            return
            
        self.btn_mtu.setEnabled(False)
        self.btn_mtu.setText(Trans.get("mtu_status_detecting"))
        
        self.mtu_worker = MtuWorker(adapter)
        self.mtu_worker.finished_signal.connect(self.on_mtu_finished)
        self.mtu_worker.finished.connect(self.mtu_worker.deleteLater)
        self.mtu_worker.start()

    def on_mtu_finished(self, msg):
        self.btn_mtu.setEnabled(True)
        self.btn_mtu.setText(Trans.get("mtu_btn"))
        self.update_current_mtu()
        InfoBar.success(Trans.get("mtu_success"), msg, parent=self)

    def get_pending_changes_count(self) -> int:
        count = 0
        parent_win = getattr(self, 'parent_window', None)
        win = parent_win or (self.parent() if isinstance(self.parent(), QWidget) else None)
        settings = getattr(win, 'settings', None) if win else None

        for card in self.findChildren(CollapsibleSwitchSettingCard):
            if getattr(card, 'is_immediate', False):
                continue
            
            attr_name = None
            for name, attr in self.__dict__.items():
                if attr is card:
                    attr_name = name
                    break
            
            if settings and attr_name:
                mapping = {
                    "chk_iso": "enable_isolation",
                    "chk_dog": "enable_watchdog",
                    "chk_parking": "enable_core_parking",
                    "chk_epp": "enable_epp_max",
                    "chk_hpet": "disable_hpet",
                    "chk_dwm": "enable_dwm_tweak",
                    "chk_dpc": "enable_dpc_latency_tweak",
                    "chk_timer_res": "enable_timer_resolution_tweak",
                    "chk_naraka_priority": "enable_naraka_priority",
                    "chk_child": "enable_child_optimization",
                    "chk_driver_prio": "enable_driver_priority_tweak",
                    "chk_vulnerable_driver_blocklist": "enable_vulnerable_driver_blocklist_tweak",
                    "chk_power_throttling": "enable_power_throttling_tweak",
                    "chk_client_priority_demote": "enable_client_priority_demote",
                    
                    "chk_preemption": "disable_gpu_preemption",
                    "chk_dwm_wet": "enable_dwm_super_wet_tweak",
                    "chk_directx": "enable_directx_tweaks",
                    "chk_gpu_firmware": "enable_gpu_firmware_tweak",
                    "chk_gpu_pstate": "enable_gpu_pstate_tweak",
                    "chk_intel_plan": "enable_custom_power_plan",
                    "chk_amd_plan": "enable_custom_power_plan",
                    "chk_gpu_opt": "enable_gpu_optimization",
                    "chk_pcipower": "disable_pcipower",
                    "chk_gpu_irq": "enable_gpu_irq_tweak",
                    "chk_hags": "disable_hags",
                    "chk_gpu_nip": "enable_nvidia_nip",
                    "chk_gpu_msi": "enable_gpu_msi_tweak",
                    "chk_global_fse": "enable_global_fse_tweak",
                    "chk_game_fse": "enable_game_fse_tweak",
                    "chk_game_gpu_preference": "enable_game_gpu_preference_tweak",
                    "chk_irq_affinity": "enable_irq_affinity_tweak",
                    "chk_dwm_presentation": "enable_dwm_presentation_tweak",
                    
                    "chk_usb_lat": "enable_usb_low_latency_tweak",
                    "chk_imod": "enable_usb_imod_tweak",
                    "chk_mouse_lat": "enable_mouse_latency_tweak",
                    "chk_sticky_keys": "enable_sticky_keys_tweak",
                    "chk_dynamic_lighting": "enable_dynamic_lighting_tweak",
                    
                    "chk_ram_opt": "enable_ram_optimization",
                    "chk_nvme_opt": "enable_nvme_optimization",
                    "chk_memory_comp": "disable_memory_compression",
                    "chk_config_alloc": "enable_config_alloc_tweak",
                    "chk_hard_working_set": "enable_hard_working_set",
                    "chk_prefetcher": "enable_prefetcher_tweak",
                    "chk_storage_msi": "enable_storage_msi_tweak",
                    
                    "chk_network": "enable_network_tweak",
                    "chk_ult_net": "enable_ultimate_network_tweak",
                    "chk_dns": "enable_dns_tweak",
                    "chk_net_imod": "enable_net_imod_tweak",
                    "chk_net_bindings": "enable_net_bindings_tweak",
                    "chk_wifi_tweak": "enable_wifi_tweak",
                    "chk_tcp_bbr": "enable_tcp_bbr_tweak",
                    "chk_eee": "enable_eee_tweak",
                    "chk_network_msi": "enable_network_msi_tweak",
                    
                    "chk_services": "disable_useless_services",
                    "chk_wsearch": "disable_wsearch_tweak",
                    "chk_spectre": "disable_spectre_meltdown",
                    "chk_copilot": "disable_copilot",
                    "chk_gamedvr": "disable_gamedvr",
                    "chk_dev_power": "enable_device_power_tweak",
                    "chk_uac": "enable_uac_tweak",
                    "chk_desktop_heap": "enable_desktop_heap_tweak",
                    "chk_download_maps": "enable_download_maps_tweak",
                    "chk_bg_apps": "enable_bg_apps_tweak",
                    "chk_map_updates": "enable_map_updates_tweak",
                    "chk_autoshare": "enable_autoshare_tweak",
                    "chk_autorun": "enable_autorun_tweak",
                    "chk_hyperv": "disable_hyperv_virtualization",
                    "chk_settings_sync": "enable_settings_sync_tweak",
                    "chk_xbox_save": "enable_xbox_save_tweak",
                    "chk_store_auto_update": "enable_store_auto_update_tweak",
                    "chk_web_search": "enable_web_search_tweak",
                    "chk_telemetry_tasks": "enable_telemetry_tasks_tweak",
                    "chk_extreme_debloat": "enable_extreme_debloat_tweak",
                    "chk_security_notifications": "disable_security_notifications",
                    "chk_defender": "disable_defender",
                    "chk_smartscreen": "disable_smartscreen",
                    "chk_firewall": "disable_firewall",
                    
                    "chk_visual_effects": "disable_windows_visual_effects",
                    "chk_transparency": "disable_windows_transparency",
                    "chk_consult_interests": "enable_consult_interests_tweak",
                    "chk_tips_suggestions": "enable_tips_suggestions_tweak",
                    "chk_widgets": "enable_widgets_tweak",
                    "chk_startup_delay": "enable_startup_delay_tweak",
                    "chk_menu_delay": "enable_menu_delay_tweak",
                    "chk_prevent_device_encryption": "enable_prevent_device_encryption_tweak",
                    "chk_spotlight": "enable_spotlight_tweak",
                }
                
                setting_attr = mapping.get(attr_name)
                if setting_attr and hasattr(settings, setting_attr):
                    current_val = getattr(settings, setting_attr)
                    if getattr(card, 'custom_status_type', None) == "gpu_msi":
                        ui_val = card.isChecked()
                    else:
                        ui_val = card.isChecked()
                    
                    if ui_val != current_val:
                        count += 1
                    continue
            
            # Fallback
            if getattr(card, 'custom_status_type', None) == "gpu_msi":
                expected = 2 if card.isChecked() else 0
                is_pending = (card.applied_state is not None and card.applied_state != expected)
            else:
                is_pending = (card.applied_state is not None and card.isChecked() != card.applied_state)
            if is_pending:
                count += 1

        for card in self.findChildren(CollapsibleComboSettingCard):
            if getattr(card, 'is_immediate', False):
                continue
            is_pending = (card.applied_state is not None and card.comboBox.currentIndex() != card.applied_state)
            if is_pending:
                count += 1
        return count

    def retranslate_ui(self):
        # Translate Pivot tabs
        self.pivot.setItemText("cpu", Trans.get("nav_cpu"))
        self.pivot.setItemText("gpu", Trans.get("nav_gpu"))
        self.pivot.setItemText("peripheral", Trans.get("nav_peripheral"))
        self.pivot.setItemText("memory", Trans.get("nav_memory"))
        self.pivot.setItemText("network", Trans.get("nav_network"))
        self.pivot.setItemText("privacy", Trans.get("nav_privacy"))
        
        if Trans.CURRENT_LANG == "zh_CN":
            self.pivot.setItemText("ux", "系统视觉与体验")
        elif Trans.CURRENT_LANG == "ja_JP":
            self.pivot.setItemText("ux", "視覚効果と体験")
        elif Trans.CURRENT_LANG == "ko_KR":
            self.pivot.setItemText("ux", "시각 효과 및 경험")
        else:
            self.pivot.setItemText("ux", "Visuals & Experience")

        self.titleLabel.setText(Trans.get("nav_optimization"))
        self.presetPanel.retranslate_ui()
        self.apply_btn.setText(Trans.get("apply_btn"))

        # Translate CPU cards
        if hasattr(self, 'chk_iso'):
            self.chk_iso.setTitle(Trans.get("chk_iso_title"))
            self.chk_iso.setContent(Trans.get("chk_iso_desc"))
            self.chk_dog.setTitle(Trans.get("chk_dog_title"))
            self.chk_dog.setContent(Trans.get("chk_dog_desc"))
            self.chk_parking.setTitle(Trans.get("chk_parking_title"))
            self.chk_parking.setContent(Trans.get("chk_parking_desc"))
            self.chk_epp.setTitle(Trans.get("chk_epp_title"))
            self.chk_epp.setContent(Trans.get("chk_epp_desc"))
            self.chk_hpet.setTitle(Trans.get("chk_hpet_title"))
            self.chk_hpet.setContent(Trans.get("chk_hpet_desc"))
            self.chk_dwm.setTitle(Trans.get("chk_dwm_title"))
            self.chk_dwm.setContent(Trans.get("chk_dwm_desc"))
            self.chk_dpc.setTitle(Trans.get("chk_dpc_title"))
            self.chk_dpc.setContent(Trans.get("chk_dpc_desc"))
            self.chk_timer_res.setTitle(Trans.get("chk_timer_res_title"))
            self.chk_timer_res.setContent(Trans.get("chk_timer_res_desc"))
            self.chk_naraka_priority.setTitle(Trans.get("chk_naraka_title"))
            self.chk_naraka_priority.setContent(Trans.get("chk_naraka_desc"))
            self.chk_child.setTitle(Trans.get("chk_child_title"))
            self.chk_child.setContent(Trans.get("chk_child_desc"))
            self.chk_driver_prio.setTitle(Trans.get("chk_driver_prio_title"))
            self.chk_driver_prio.setContent(Trans.get("chk_driver_prio_desc"))
            self.chk_vulnerable_driver_blocklist.setTitle(Trans.get("chk_vulnerable_driver_blocklist_title"))
            self.chk_vulnerable_driver_blocklist.setContent(Trans.get("chk_vulnerable_driver_blocklist_desc"))
            self.chk_power_throttling.setTitle(Trans.get("chk_power_throttling_title"))
            self.chk_power_throttling.setContent(Trans.get("chk_power_throttling_desc"))
            self.chk_client_priority_demote.setTitle(Trans.get("chk_client_priority_demote_title"))
            self.chk_client_priority_demote.setContent(Trans.get("chk_client_priority_demote_desc"))
            if not is_windows_11():
                self.chk_vulnerable_driver_blocklist.setEnabled(False)
                self.chk_vulnerable_driver_blocklist.setTitle(Trans.get("chk_vulnerable_driver_blocklist_title") + Trans.get("win11_only_suffix"))
                self.chk_vulnerable_driver_blocklist.setChecked(False)

        # Translate GPU cards
        if hasattr(self, 'chk_preemption'):
            self.chk_preemption.setTitle(Trans.get("chk_preemption_title"))
            self.chk_preemption.setContent(Trans.get("chk_preemption_desc"))
            self.chk_dwm_wet.setTitle(Trans.get("chk_dwm_wet_title"))
            self.chk_dwm_wet.setContent(Trans.get("chk_dwm_wet_desc"))
            self.chk_directx.setTitle(Trans.get("chk_directx_title"))
            self.chk_directx.setContent(Trans.get("chk_directx_desc"))
            self.chk_gpu_firmware.setTitle(Trans.get("chk_gpu_firmware_title"))
            self.chk_gpu_firmware.setContent(Trans.get("chk_gpu_firmware_desc"))
            self.chk_gpu_pstate.setTitle(Trans.get("chk_gpu_pstate_title"))
            self.chk_gpu_pstate.setContent(Trans.get("chk_gpu_pstate_desc"))
            self.chk_intel_plan.setTitle(Trans.get("chk_intel_plan_title"))
            self.chk_intel_plan.setContent(Trans.get("chk_intel_plan_desc"))
            self.chk_amd_plan.setTitle(Trans.get("chk_amd_plan_title"))
            self.chk_amd_plan.setContent(Trans.get("chk_amd_plan_desc"))
            self.chk_pcipower.setTitle(Trans.get("chk_pcipower_title"))
            self.chk_pcipower.setContent(Trans.get("chk_pcipower_desc"))
            self.chk_gpu_irq.setTitle(Trans.get("chk_gpu_irq_title"))
            self.chk_gpu_irq.setContent(Trans.get("chk_gpu_irq_desc"))
            self.chk_hags.setTitle(Trans.get("chk_hags_title"))
            self.chk_hags.setContent(Trans.get("chk_hags_desc"))
            self.chk_gpu_nip.setTitle(Trans.get("nip_title"))
            self.chk_gpu_nip.setContent(Trans.get("nip_desc"))
            self.chk_gpu_msi.setTitle(Trans.get("chk_gpu_msi_title"))
            self.chk_gpu_msi.setContent(Trans.get("chk_gpu_msi_desc"))
            self.chk_global_fse.setTitle(Trans.get("chk_global_fse_title"))
            self.chk_global_fse.setContent(Trans.get("chk_global_fse_desc"))
            self.chk_game_fse.setTitle(Trans.get("chk_game_fse_title"))
            self.chk_game_fse.setContent(Trans.get("chk_game_fse_desc"))
            self.chk_game_gpu_preference.setTitle(Trans.get("chk_game_gpu_preference_title"))
            self.chk_game_gpu_preference.setContent(Trans.get("chk_game_gpu_preference_desc"))
            self.chk_irq_affinity.setTitle(Trans.get("chk_irq_affinity_title"))
            self.chk_irq_affinity.setContent(Trans.get("chk_irq_affinity_desc"))
            self.chk_dwm_presentation.setTitle(Trans.get("chk_dwm_presentation_title"))
            self.chk_dwm_presentation.setContent(Trans.get("chk_dwm_presentation_desc"))
            self.update_hardware_cards_visibility()

        # Translate Peripheral cards
        if hasattr(self, 'chk_usb_lat'):
            self.chk_usb_lat.setTitle(Trans.get("chk_usb_lat_title"))
            self.chk_usb_lat.setContent(Trans.get("chk_usb_lat_desc"))
            self.chk_imod.setTitle(Trans.get("chk_imod_title"))
            self.chk_imod.setContent(Trans.get("chk_imod_desc"))
            self.chk_mouse_lat.setTitle(Trans.get("chk_mouse_lat_title"))
            self.chk_mouse_lat.setContent(Trans.get("chk_mouse_lat_desc"))
            self.chk_sticky_keys.setTitle(Trans.get("chk_sticky_keys_title"))
            self.chk_sticky_keys.setContent(Trans.get("chk_sticky_keys_desc"))
            self.chk_dynamic_lighting.setTitle(Trans.get("chk_dynamic_lighting_title"))
            self.chk_dynamic_lighting.setContent(Trans.get("chk_dynamic_lighting_desc"))
            if not is_windows_11():
                self.chk_dynamic_lighting.setEnabled(False)
                self.chk_dynamic_lighting.setTitle(Trans.get("chk_dynamic_lighting_title") + Trans.get("win11_only_suffix"))
                self.chk_dynamic_lighting.setChecked(False)

        # Translate Memory cards
        if hasattr(self, 'chk_ram_opt'):
            ram_gb = 16
            win = self.parent_window
            if win and hasattr(win, 'ram_gb'):
                ram_gb = win.ram_gb
            self.chk_ram_opt.setTitle(Trans.get("chk_ram_opt_title"))
            self.chk_ram_opt.setContent(Trans.get("chk_ram_opt_desc").format(ram_gb=ram_gb))
            self.chk_nvme_opt.setTitle(Trans.get("chk_nvme_opt_title"))
            self.chk_nvme_opt.setContent(Trans.get("chk_nvme_opt_desc"))
            self.chk_memory_comp.setTitle(Trans.get("chk_memory_comp_title"))
            self.chk_memory_comp.setContent(Trans.get("chk_memory_comp_desc"))
            self.chk_config_alloc.setTitle(Trans.get("chk_config_alloc_title"))
            self.chk_config_alloc.setContent(Trans.get("chk_config_alloc_desc"))
            self.chk_hard_working_set.setTitle(Trans.get("chk_hard_working_set_title"))
            self.chk_hard_working_set.setContent(Trans.get("chk_hard_working_set_desc"))
            self.chk_prefetcher.setTitle(Trans.get("chk_prefetcher_title"))
            self.chk_prefetcher.setContent(Trans.get("chk_prefetcher_desc"))
            self.chk_storage_msi.setTitle(Trans.get("chk_storage_msi_title"))
            self.chk_storage_msi.setContent(Trans.get("chk_storage_msi_desc"))

        # Translate Network cards
        if hasattr(self, 'chk_network'):
            self.chk_network.setTitle(Trans.get("chk_network_title"))
            self.chk_network.setContent(Trans.get("chk_network_desc"))
            self.chk_ult_net.setTitle(Trans.get("chk_ult_net_title"))
            self.chk_ult_net.setContent(Trans.get("chk_ult_net_desc"))
            self.chk_dns.setTitle(Trans.get("chk_dns_title"))
            self.chk_dns.setContent(Trans.get("chk_dns_desc"))
            self.chk_net_imod.setTitle(Trans.get("chk_net_imod_title"))
            self.chk_net_imod.setContent(Trans.get("chk_net_imod_desc"))
            self.chk_net_bindings.setTitle(Trans.get("chk_net_bindings_title"))
            self.chk_net_bindings.setContent(Trans.get("chk_net_bindings_desc"))
            self.chk_wifi_tweak.setTitle(Trans.get("chk_wifi_tweak_title"))
            self.chk_wifi_tweak.setContent(Trans.get("chk_wifi_tweak_desc"))
            self.chk_tcp_bbr.setTitle(Trans.get("chk_tcp_bbr_title"))
            self.chk_tcp_bbr.setContent(Trans.get("chk_tcp_bbr_desc"))
            self.chk_eee.setTitle(Trans.get("chk_eee_title"))
            self.chk_eee.setContent(Trans.get("chk_eee_desc"))
            self.chk_network_msi.setTitle(Trans.get("chk_network_msi_title"))
            self.chk_network_msi.setContent(Trans.get("chk_network_msi_desc"))
            
            self.mtu_title_header.setText(Trans.get("mtu_header"))
            self.mtu_title_label.setText(Trans.get("mtu_card_title"))
            self.mtu_desc_label.setText(Trans.get("mtu_card_desc"))
            if self.btn_mtu.isEnabled():
                self.btn_mtu.setText(Trans.get("mtu_btn"))
            else:
                self.btn_mtu.setText(Trans.get("mtu_status_detecting"))
            self.update_current_mtu()

        # Translate Privacy cards
        if hasattr(self, 'chk_services'):
            self.chk_services.setTitle(Trans.get("chk_services_title"))
            self.chk_services.setContent(Trans.get("chk_services_desc"))
            self.chk_wsearch.setTitle(Trans.get("chk_wsearch_title"))
            self.chk_wsearch.setContent(Trans.get("chk_wsearch_desc"))
            self.chk_spectre.setTitle(Trans.get("chk_spectre_title"))
            self.chk_spectre.setContent(Trans.get("chk_spectre_desc"))
            self.chk_copilot.setTitle(Trans.get("chk_copilot_title"))
            self.chk_copilot.setContent(Trans.get("chk_copilot_desc"))
            self.chk_gamedvr.setTitle(Trans.get("chk_gamedvr_title"))
            self.chk_gamedvr.setContent(Trans.get("chk_gamedvr_desc"))
            self.chk_dev_power.setTitle(Trans.get("chk_dev_power_title"))
            self.chk_dev_power.setContent(Trans.get("chk_dev_power_desc"))
            self.chk_uac.setTitle(Trans.get("chk_uac_title"))
            self.chk_uac.setContent(Trans.get("chk_uac_desc"))
            self.chk_desktop_heap.setTitle(Trans.get("chk_desktop_heap_title"))
            self.chk_desktop_heap.setContent(Trans.get("chk_desktop_heap_desc"))
            self.chk_download_maps.setTitle(Trans.get("chk_download_maps_title"))
            self.chk_download_maps.setContent(Trans.get("chk_download_maps_desc"))
            self.chk_bg_apps.setTitle(Trans.get("chk_bg_apps_title"))
            self.chk_bg_apps.setContent(Trans.get("chk_bg_apps_desc"))
            self.chk_map_updates.setTitle(Trans.get("chk_map_updates_title"))
            self.chk_map_updates.setContent(Trans.get("chk_map_updates_desc"))
            self.chk_autoshare.setTitle(Trans.get("chk_autoshare_title"))
            self.chk_autoshare.setContent(Trans.get("chk_autoshare_desc"))
            self.chk_autorun.setTitle(Trans.get("chk_autorun_title"))
            self.chk_autorun.setContent(Trans.get("chk_autorun_desc"))
            self.chk_hyperv.setTitle(Trans.get("chk_hyperv_title"))
            self.chk_hyperv.setContent(Trans.get("chk_hyperv_desc"))
            self.chk_settings_sync.setTitle(Trans.get("chk_settings_sync_title"))
            self.chk_settings_sync.setContent(Trans.get("chk_settings_sync_desc"))
            self.chk_xbox_save.setTitle(Trans.get("chk_xbox_save_title"))
            self.chk_xbox_save.setContent(Trans.get("chk_xbox_save_desc"))
            self.chk_store_auto_update.setTitle(Trans.get("chk_store_auto_update_title"))
            self.chk_store_auto_update.setContent(Trans.get("chk_store_auto_update_desc"))
            self.chk_web_search.setTitle(Trans.get("chk_web_search_title"))
            self.chk_web_search.setContent(Trans.get("chk_web_search_desc"))
            self.chk_telemetry_tasks.setTitle(Trans.get("chk_telemetry_tasks_title"))
            self.chk_telemetry_tasks.setContent(Trans.get("chk_telemetry_tasks_desc"))
            self.chk_extreme_debloat.setTitle(Trans.get("chk_extreme_debloat_title"))
            self.chk_extreme_debloat.setContent(Trans.get("chk_extreme_debloat_desc"))
            self.uwp_title_label.setText(Trans.get("card_uwp_debloat_title"))
            self.uwp_desc_label.setText(Trans.get("card_uwp_debloat_desc"))
            self.btn_uwp_debloat.setText(Trans.get("btn_uwp_debloat"))
            self.security_title_header.setText(Trans.get("security_header"))
            self.chk_security_notifications.setTitle(Trans.get("chk_security_notifications_title"))
            self.chk_security_notifications.setContent(Trans.get("chk_security_notifications_desc"))
            self.chk_defender.setTitle(Trans.get("chk_defender_title"))
            self.chk_defender.setContent(Trans.get("chk_defender_desc"))
            self.chk_smartscreen.setTitle(Trans.get("chk_smartscreen_title"))
            self.chk_smartscreen.setContent(Trans.get("chk_smartscreen_desc"))
            self.chk_firewall.setTitle(Trans.get("chk_firewall_title"))
            self.chk_firewall.setContent(Trans.get("chk_firewall_desc"))

        # Translate UX cards
        if hasattr(self, 'chk_visual_effects'):
            self.chk_visual_effects.setTitle(Trans.get("chk_visual_effects_title"))
            self.chk_visual_effects.setContent(Trans.get("chk_visual_effects_desc"))
            self.chk_transparency.setTitle(Trans.get("chk_transparency_title"))
            self.chk_transparency.setContent(Trans.get("chk_transparency_desc"))
            self.chk_consult_interests.setTitle(Trans.get("chk_consult_interests_title"))
            self.chk_consult_interests.setContent(Trans.get("chk_consult_interests_desc"))
            self.chk_tips_suggestions.setTitle(Trans.get("chk_tips_suggestions_title"))
            self.chk_tips_suggestions.setContent(Trans.get("chk_tips_suggestions_desc"))
            self.chk_widgets.setTitle(Trans.get("chk_widgets_title"))
            self.chk_widgets.setContent(Trans.get("chk_widgets_desc"))
            self.chk_startup_delay.setTitle(Trans.get("chk_startup_delay_title"))
            self.chk_startup_delay.setContent(Trans.get("chk_startup_delay_desc"))
            self.chk_menu_delay.setTitle(Trans.get("chk_menu_delay_title"))
            self.chk_menu_delay.setContent(Trans.get("chk_menu_delay_desc"))
            self.chk_prevent_device_encryption.setTitle(Trans.get("chk_prevent_device_encryption_title"))
            self.chk_prevent_device_encryption.setContent(Trans.get("chk_prevent_device_encryption_desc"))
            self.chk_spotlight.setTitle(Trans.get("chk_spotlight_title"))
            self.chk_spotlight.setContent(Trans.get("chk_spotlight_desc"))
            if not is_windows_11():
                self.chk_widgets.setEnabled(False)
                self.chk_widgets.setTitle(Trans.get("chk_widgets_title") + Trans.get("win11_only_suffix"))
                self.chk_widgets.setChecked(False)

    _in_getattr = set()
    def __getattr__(self, name):
        if name in ('parent_window', 'optimization_page', 'general_page'):
            raise AttributeError()
        key = (id(self), name)
        if key in self._in_getattr:
            raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")
        self._in_getattr.add(key)
        try:
            if hasattr(self, 'parent_window') and self.parent_window:
                gen_page = getattr(self.parent_window, 'general_page', None)
                if gen_page and gen_page is not self:
                    return getattr(gen_page, name)
        finally:
            self._in_getattr.discard(key)
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")


# Compatibility Stub Classes to prevent ImportError in window.py
class SettingsCpuPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
    def retranslate_ui(self):
        pass

class SettingsPeripheralPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
    def retranslate_ui(self):
        pass

class SettingsGpuPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
    def retranslate_ui(self):
        pass
    def update_hardware_cards_visibility(self):
        pass

class SettingsMemoryPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
    def retranslate_ui(self):
        pass

class SettingsPrivacyPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
    def retranslate_ui(self):
        pass

class SettingsNetworkPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
    def retranslate_ui(self):
        pass

class SettingsToolsPage(BaseSettingsPage):
    """ 8. Auxiliary Tools & System Maintenance Page (辅助工具) """
    def __init__(self, parent=None):
        super().__init__("性能维护与系统高级工具", parent)
        
        # Backup restore section
        self.backup_title_header = SubtitleLabel("系统状态备份与恢复", self.view)
        self.vBoxLayout.addWidget(self.backup_title_header)
        
        self.backup_card = SimpleCardWidget(self.view)
        self.backup_card.setMinimumHeight(135)
        backup_layout = QHBoxLayout(self.backup_card)
        backup_layout.setContentsMargins(20, 15, 20, 15)
        
        backup_text_layout = QVBoxLayout()
        backup_text_layout.setSpacing(6)
        self.backup_title_label = SubtitleLabel("系统备份与恢复管理")
        self.backup_status_label = CaptionLabel("未检测到备份。在您首次应用高级调优方案时，程序将自动对系统原始注册表与服务状态进行备份。", self.backup_card)
        self.backup_status_label.setWordWrap(True)
        
        # ComboBox for choosing backup nodes
        self.backup_combo = ComboBox(self.backup_card)
        self.backup_combo.setFixedWidth(280)
        self.backup_combo.setPlaceholderText("选择要还原的系统备份节点...")
        
        backup_text_layout.addWidget(self.backup_title_label)
        backup_text_layout.addWidget(self.backup_status_label)
        backup_text_layout.addWidget(self.backup_combo)
        
        # Right Side Action Buttons
        self.backup_btn_layout = QHBoxLayout()
        self.backup_btn_layout.setSpacing(10)
        
        self.btn_delete_backup = PushButton("删除选定备份", self.backup_card)
        self.btn_delete_backup.setFocusPolicy(Qt.NoFocus)
        self.btn_delete_backup.setIcon(FluentIcon.DELETE)
        self.btn_delete_backup.clicked.connect(self.delete_selected_backup)
        
        self.btn_restore = PushButton("还原选定备份", self.backup_card)
        self.btn_restore.setFocusPolicy(Qt.NoFocus)
        self.btn_restore.setIcon(FluentIcon.HISTORY)
        self.btn_restore.clicked.connect(self.restore_system_defaults)
        
        self.backup_btn_layout.addWidget(self.btn_delete_backup)
        self.backup_btn_layout.addWidget(self.btn_restore)
        
        backup_layout.addLayout(backup_text_layout, 1)
        backup_layout.addLayout(self.backup_btn_layout)
        self.vBoxLayout.addWidget(self.backup_card)
        
        # System cleanup section
        self.vBoxLayout.addSpacing(15)
        self.cleanup_title_header = SubtitleLabel("系统性能维护", self.view)
        self.vBoxLayout.addWidget(self.cleanup_title_header)
        
        self.cleanup_card = SimpleCardWidget(self.view)
        self.cleanup_card.setMinimumHeight(96)
        cleanup_layout = QHBoxLayout(self.cleanup_card)
        cleanup_layout.setContentsMargins(20, 15, 20, 15)
        cleanup_text_layout = QVBoxLayout()
        self.cleanup_title_label = SubtitleLabel("冗余数据与临时缓存整理")
        self.cleanup_desc_label = CaptionLabel("安全清理系统临时文件、Windows 更新缓存及网络 DNS 解析缓存，并重建 WMI 性能计数器。")
        self.cleanup_desc_label.setWordWrap(True)
        cleanup_text_layout.addWidget(self.cleanup_title_label)
        cleanup_text_layout.addWidget(self.cleanup_desc_label)
        
        self.btn_cleanup = PushButton("执行冗余数据与缓存整理", self.cleanup_card)
        self.btn_cleanup.setFocusPolicy(Qt.NoFocus)
        self.btn_cleanup.setIcon(FluentIcon.DELETE)
        self.btn_cleanup.clicked.connect(self.start_system_cleanup)
        cleanup_layout.addLayout(cleanup_text_layout, 1)
        cleanup_layout.addWidget(self.btn_cleanup)
        self.vBoxLayout.addWidget(self.cleanup_card)
        
        # Whitelist Filter Section
        self.vBoxLayout.addSpacing(15)
        self.wl_title_header = SubtitleLabel("后台调度隔离白名单", self.view)
        self.vBoxLayout.addWidget(self.wl_title_header)
        
        wl_card = SimpleCardWidget(self.view)
        wl_card.setMinimumHeight(320)
        wl_layout = QVBoxLayout(wl_card)
        wl_layout.setContentsMargins(20, 20, 20, 20)
        wl_layout.setSpacing(12)
        
        self.wl_desc_label = CaptionLabel("白名单内的后台进程在执行系统隔离调优时将被予以豁免，不受限制绑定或核心调度隔离限制（系统关键核心进程已默认豁免）：", wl_card)
        self.wl_desc_label.setWordWrap(True)
        wl_layout.addWidget(self.wl_desc_label)
        
        input_layout = QHBoxLayout()
        self.wl_edit = LineEdit(wl_card)
        self.wl_edit.setPlaceholderText("例如: discord.exe 或 steam")
        self.wl_edit.returnPressed.connect(self.add_whitelist_item)
        
        self.btn_add_wl = PushButton("加入白名单", wl_card)
        self.btn_add_wl.setFocusPolicy(Qt.NoFocus)
        self.btn_add_wl.setIcon(FluentIcon.ADD)
        self.btn_add_wl.clicked.connect(self.add_whitelist_item)
        
        input_layout.addWidget(self.wl_edit, 1)
        input_layout.addWidget(self.btn_add_wl)
        wl_layout.addLayout(input_layout)
        
        self.wl_list = ListWidget(wl_card)
        self.wl_list.setFixedHeight(150)
        wl_layout.addWidget(self.wl_list)
        
        action_layout = QHBoxLayout()
        action_layout.addStretch(1)
        self.btn_del_wl = PushButton("移除选定进程", wl_card)
        self.btn_del_wl.setFocusPolicy(Qt.NoFocus)
        self.btn_del_wl.setIcon(FluentIcon.DELETE)
        self.btn_del_wl.clicked.connect(self.remove_whitelist_item)
        action_layout.addWidget(self.btn_del_wl)
        wl_layout.addLayout(action_layout)
        self.vBoxLayout.addWidget(wl_card)
        
        # Diagnostic tools section
        self.tools_title = SubtitleLabel("外部高级系统诊断与微调工具 (交互式调用支持)", self.view)
        self.vBoxLayout.addWidget(self.tools_title)
        
        # Layout tools cards in grid
        self.tools_widget = QWidget(self.view)
        self.tools_layout = QGridLayout(self.tools_widget)
        self.tools_layout.setContentsMargins(0, 5, 0, 5)
        self.tools_layout.setSpacing(12)
        
        tools_data = [
            ("Dism++ 部署映像服务与管理工具", "Dism++x64.exe", "提供 Windows 组件净化、引导项管理及注册表清理，推荐执行首次系统备份归档", FluentIcon.HISTORY),
            ("HiBit 深度卸载与垃圾清理工具", "HiBitUninstaller.exe", "强力清理顽固软件及其注册表冗余残留，净化系统运行环境", FluentIcon.DELETE),
            ("BoosterX 性能深度调配工具", "BoosterX.exe", "面向操作系统的深度定制调配与服务精简集成面板", FluentIcon.SPEED_HIGH),
            ("O&O ShutUp10 遥测与隐私控制工具", "OOSU10.exe", "屏蔽 Windows 遥测数据采集与隐式后台流，降低隐私性资源开销", FluentIcon.DEVELOPER_TOOLS),
            ("WPD 遥测配置与策略过滤工具", "WPD.exe", "微调操作系统本地遥测策略、入站/出站端口过滤及预装 UWP 净化", FluentIcon.GLOBE),
            ("AutoGpuAffinity 显卡亲和性关联", "AutoGpuAffinity.exe", "自动绑定显卡 IRQ 中断至指定的 CPU 物理核心，优化输入响应延迟", FluentIcon.APPLICATION),
            ("Process Lasso 主动式线程平衡器", "processlassosetup64.exe", "实时动态优化 CPU 多线程并发权重，维持前台高优先级程序稳定性", FluentIcon.TILES),
            ("Intelligent Standby List Cleaner (ISLC)", "Intelligent standby list cleaner ISLC.exe", "自动监控并整理 Windows 待机列表 (Standby List) 及物理内存页面分配", FluentIcon.SPEED_HIGH),
            ("DPC 延迟测试分析器 (DPC Checker)", "DPCLatencyChecker_V1.4.exe", "测量系统延迟与内核级延时表现，精确定位驱动程序硬件瓶颈与音频中断异常", FluentIcon.STOP_WATCH),
            ("高精度计时器分辨率调整工具 (TimerResolution)", "SetTimerResolution.exe", "将系统核心时钟中断间隔缩短至 0.5 毫秒极限，降低多媒体与物理外设响应耗时", FluentIcon.STOP_WATCH),
            ("MSI Utility v3 中断亲和性配置", "MSI_util_v3.exe", "配置 PCI 设备的中断亲和性屏蔽，强制切换为消息信号中断 (MSI-Mode) 模式", FluentIcon.DEVELOPER_TOOLS),
            ("UsbTreeView USB 设备拓扑分析仪", "UsbTreeView.exe", "深度剖析主机 USB 根集线器拓扑关联、物理接口传输速率与人机接口连接关系", FluentIcon.DEVELOPER_TOOLS),
        ]
        
        self.tools_cards = []
        for idx, (title, exe_name, desc, icon) in enumerate(tools_data):
            card = SimpleCardWidget(self.tools_widget)
            card.setMinimumHeight(135)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(15, 12, 15, 12)
            card_layout.setSpacing(6)
            
            lbl_title = SubtitleLabel(title, card)
            lbl_desc = CaptionLabel(desc, card)
            lbl_desc.setWordWrap(True)
            lbl_desc.setMinimumHeight(45)
            
            btn_run = PushButton("运行工具", card)
            btn_run.setFocusPolicy(Qt.NoFocus)
            btn_run.setIcon(icon)
            btn_run.clicked.connect(lambda checked=False, name=exe_name: self.run_external_tool(name))
            
            card_layout.addWidget(lbl_title)
            card_layout.addWidget(lbl_desc, 1)
            card_layout.addWidget(btn_run, 0, Qt.AlignmentFlag.AlignRight)
            
            row = idx // 2
            col = idx % 2
            self.tools_layout.addWidget(card, row, col)
            
            self.tools_cards.append({
                'title_lbl': lbl_title,
                'desc_lbl': lbl_desc,
                'btn': btn_run
            })
            
        self.vBoxLayout.addWidget(self.tools_widget)
        self.vBoxLayout.addStretch(1)
        
        # Populate backups list
        self.refresh_backups_list()

    def retranslate_ui(self):
        super().retranslate_ui()
        if not hasattr(self, 'backup_title_header'):
            return
            
        self.backup_title_header.setText(Trans.get("tools_backup_header"))
        self.backup_title_label.setText(Trans.get("tools_backup_title"))
        
        self.refresh_backups_list()
        
        self.btn_restore.setText("还原选定备份" if Trans.CURRENT_LANG == "zh_CN" else "Restore Selected")
        self.btn_delete_backup.setText("删除选定备份" if Trans.CURRENT_LANG == "zh_CN" else "Delete Selected")
        
        self.cleanup_title_header.setText(Trans.get("tools_cleanup_header"))
        self.cleanup_title_label.setText(Trans.get("tools_cleanup_title"))
        self.cleanup_desc_label.setText(Trans.get("tools_cleanup_desc"))
        
        if self.btn_cleanup.isEnabled():
            self.btn_cleanup.setText(Trans.get("tools_cleanup_btn"))
        else:
            self.btn_cleanup.setText("物理缓存与冗余清理中..." if Trans.CURRENT_LANG == "zh_CN" else "Cleaning up Cache & Junk...")
            
        self.wl_title_header.setText(Trans.get("tools_wl_header"))
        self.wl_desc_label.setText(Trans.get("tools_wl_desc"))
        self.wl_edit.setPlaceholderText(Trans.get("tools_wl_placeholder"))
        self.btn_add_wl.setText(Trans.get("tools_wl_add_btn"))
        self.btn_del_wl.setText(Trans.get("tools_wl_del_btn"))
        
        self.tools_title.setText(Trans.get("tools_ext_header"))
        
        # Translate the tools list cards
        tools_list_trans = Trans.get("tools_data")
        if isinstance(tools_list_trans, list) and len(tools_list_trans) == len(self.tools_cards):
            for idx, card_widgets in enumerate(self.tools_cards):
                title_trans, desc_trans = tools_list_trans[idx]
                card_widgets['title_lbl'].setText(title_trans)
                card_widgets['desc_lbl'].setText(desc_trans)
                card_widgets['btn'].setText("运行工具" if Trans.CURRENT_LANG == "zh_CN" else "Run Tool")

    def refresh_backups_list(self):
        self.backup_combo.blockSignals(True)
        self.backup_combo.clear()
        self.backups_list = SystemTweaksService.get_all_backups()
        
        if self.backups_list:
            for item in self.backups_list:
                self.backup_combo.addItem(item["display_name"], item["filename"])
            self.backup_status_label.setText(f"已检测到 {len(self.backups_list)} 个系统备份节点，可随时选择节点并安全撤回还原。" if Trans.CURRENT_LANG == "zh_CN" else f"Detected {len(self.backups_list)} system backup nodes.")
            self.btn_restore.setEnabled(True)
            self.btn_delete_backup.setEnabled(True)
        else:
            self.backup_status_label.setText("未检测到备份。在您首次应用高级调优方案时，程序将自动对系统原始注册表与服务状态进行备份。" if Trans.CURRENT_LANG == "zh_CN" else "No backups found.")
            self.btn_restore.setEnabled(False)
            self.btn_delete_backup.setEnabled(False)
        self.backup_combo.blockSignals(False)

    def restore_system_defaults(self):
        idx = self.backup_combo.currentIndex()
        if idx < 0 or idx >= len(self.backups_list):
            InfoBar.warning("未选择备份节点" if Trans.CURRENT_LANG == "zh_CN" else "No Node Selected", 
                            "请先在下拉列表中选择一个有效的备份节点。" if Trans.CURRENT_LANG == "zh_CN" else "Please select a valid backup node from the list.", parent=self)
            return
            
        selected_backup = self.backups_list[idx]
        filename = selected_backup["filename"]
        display_name = selected_backup["display_name"]
        
        # Double confirmation dialog
        title = "确定还原系统状态？" if Trans.CURRENT_LANG == "zh_CN" else "Restore System State?"
        content = f"确定要将系统还原到备份节点 [{display_name}] 时的状态吗？还原后该备份节点将被移除。" if Trans.CURRENT_LANG == "zh_CN" else f"Are you sure you want to restore your system to [{display_name}]? This node will be removed after restore."
        
        from qfluentwidgets import MessageBox
        dialog = MessageBox(title, content, self.window())
        dialog.yesButton.setText("确定" if Trans.CURRENT_LANG == "zh_CN" else "Confirm")
        dialog.cancelButton.setText("取消" if Trans.CURRENT_LANG == "zh_CN" else "Cancel")
        if not dialog.exec():
            return
            
        self.btn_restore.setEnabled(False)
        self.btn_restore.setText("正在还原系统配置..." if Trans.CURRENT_LANG == "zh_CN" else "Restoring system...")
        
        success, failed_items = SystemTweaksService.restore_system_defaults(filename)
        
        self.btn_restore.setEnabled(True)
        self.btn_restore.setText("还原选定备份" if Trans.CURRENT_LANG == "zh_CN" else "Restore Selected")
        
        if success:
            if failed_items:
                msg = "还原完成。部分系统安全保护项目（受 Windows 物理防篡改保护限制，如 Defender 等）未做更改（当前处于 Windows 默认安全状态）。" if Trans.CURRENT_LANG == "zh_CN" else "Restore complete. Some system protection items were not changed due to Windows Tamper Protection (they remain in their default secure state)."
                InfoBar.warning("部分还原成功" if Trans.CURRENT_LANG == "zh_CN" else "Partially Restored", 
                                msg, parent=self)
            else:
                InfoBar.success("系统状态还原成功" if Trans.CURRENT_LANG == "zh_CN" else "System Restored", 
                                "所选备份节点的系统配置已成功还原生效。" if Trans.CURRENT_LANG == "zh_CN" else "Registry and system parameters successfully restored from selected backup.", parent=self)
            self.refresh_backups_list()
            
            # Reset standard controls on window and sync system state
            win = self.parent_window
            if win:
                win.load_preset("default")
                win.detect_and_sync_system_states(force_sync=True)
        else:
            InfoBar.warning("还原失败" if Trans.CURRENT_LANG == "zh_CN" else "Restore Failed", 
                            "无法还原该备份节点，可能由于部分配置文件不存在或写入被拦截。" if Trans.CURRENT_LANG == "zh_CN" else "Could not restore from the selected backup node.", parent=self)

    def delete_selected_backup(self):
        idx = self.backup_combo.currentIndex()
        if idx < 0 or idx >= len(self.backups_list):
            return
            
        selected_backup = self.backups_list[idx]
        filename = selected_backup["filename"]
        display_name = selected_backup["display_name"]
        path = selected_backup["path"]
        
        title = "确定删除备份节点？" if Trans.CURRENT_LANG == "zh_CN" else "Delete Backup Node?"
        content = f"此操作将永久删除备份节点 [{display_name}]，您将无法以此还原当时的状态。确定要删除吗？" if Trans.CURRENT_LANG == "zh_CN" else f"This will permanently delete the backup node [{display_name}]. You will not be able to restore to this state. Proceed?"
        
        from qfluentwidgets import MessageBox
        dialog = MessageBox(title, content, self.window())
        dialog.yesButton.setText("确定" if Trans.CURRENT_LANG == "zh_CN" else "Confirm")
        dialog.cancelButton.setText("取消" if Trans.CURRENT_LANG == "zh_CN" else "Cancel")
        if not dialog.exec():
            return
            
        try:
            os.remove(path)
            InfoBar.success("删除备份成功" if Trans.CURRENT_LANG == "zh_CN" else "Backup Deleted", 
                            "所选的备份节点已被永久删除。" if Trans.CURRENT_LANG == "zh_CN" else "The selected backup node was successfully deleted.", parent=self)
            self.refresh_backups_list()
        except Exception as e:
            InfoBar.warning("删除失败" if Trans.CURRENT_LANG == "zh_CN" else "Delete Failed", 
                            f"无法删除该备份文件: {str(e)}" if Trans.CURRENT_LANG == "zh_CN" else f"Failed to delete backup: {str(e)}", parent=self)

    def start_system_cleanup(self):
        self.btn_cleanup.setEnabled(False)
        self.btn_cleanup.setText("物理缓存与冗余清理中..." if Trans.CURRENT_LANG == "zh_CN" else "Cleaning up Cache & Junk...")
        
        self.cleanup_worker = CleanupWorker(self)
        self.cleanup_worker.finished_signal.connect(self.on_cleanup_finished)
        self.cleanup_worker.finished.connect(self.cleanup_worker.deleteLater)
        self.cleanup_worker.start()

    def on_cleanup_finished(self, msg):
        self.btn_cleanup.setEnabled(True)
        self.btn_cleanup.setText(Trans.get("tools_cleanup_btn"))
        InfoBar.success("系统冗余数据与缓存整理完成" if Trans.CURRENT_LANG == "zh_CN" else "System Cleanup Completed", msg, parent=self)

    def run_external_tool(self, name):
        try:
            if name == "DPCLatencyChecker_V1.4.exe":
                from qfluentwidgets import MessageBox
                title = "工具兼容性警告" if Trans.CURRENT_LANG == "zh_CN" else "Tool Compatibility Warning"
                content = (
                    "警告：DPC Latency Checker (V1.4) 与 Windows 10/11 系统存在严重的兼容性问题。\n\n"
                    "在现代 Windows 系统上强行加载其过时的内核驱动程序，极有可能导致系统直接蓝屏死机 (BSOD) 或产生错误的延迟数据读数。\n\n"
                    "是否仍要继续运行该工具？（建议使用 LatencyMon 替代）"
                    if Trans.CURRENT_LANG == "zh_CN" else
                    "Warning: DPC Latency Checker (V1.4) has severe compatibility issues with Windows 10/11.\n\n"
                    "Forcing it to load its obsolete kernel driver on modern Windows systems will very likely cause a Blue Screen of Death (BSOD) or yield incorrect latency readouts.\n\n"
                    "Do you still want to run this tool? (We recommend using LatencyMon instead)"
                )
                dialog = MessageBox(title, content, self.window())
                dialog.yesButton.setText("继续运行" if Trans.CURRENT_LANG == "zh_CN" else "Run Anyway")
                dialog.cancelButton.setText("取消" if Trans.CURRENT_LANG == "zh_CN" else "Cancel")
                if not dialog.exec():
                    return

            exe_path = SystemTweaksService.get_resource_path(os.path.join("core_commander", "resources", name))
            if os.path.exists(exe_path):
                import subprocess
                # Launch directly from the resources directory with working directory set to it.
                # This ensures relative dependencies (like bin/ for AutoGpuAffinity, Config/ for Dism++, or UsbTreeView.ini) 
                # are loaded correctly and bypasses SRP / AppLocker execution blocks on the %TEMP% folder.
                subprocess.Popen([exe_path], cwd=os.path.dirname(exe_path))
                InfoBar.success("成功" if Trans.CURRENT_LANG == "zh_CN" else "Success", 
                                f"高级系统工具 {name} 已成功启动运行" if Trans.CURRENT_LANG == "zh_CN" else f"Advanced tool {name} started successfully.", parent=self)
            else:
                InfoBar.error("错误" if Trans.CURRENT_LANG == "zh_CN" else "Error", 
                              f"辅助工具 {name} 资源文件未打包，无法运行！" if Trans.CURRENT_LANG == "zh_CN" else f"Auxiliary tool {name} resource file not found, cannot run!", parent=self)
        except Exception as e:
            logger.error(f"Failed to launch external tool {name}: {str(e)}")
            InfoBar.error("启动失败" if Trans.CURRENT_LANG == "zh_CN" else "Launch Failed", 
                          f"启动辅助工具失败: {str(e)}" if Trans.CURRENT_LANG == "zh_CN" else f"Failed to start tool: {str(e)}", parent=self)

    def load_whitelist_ui(self, items: list):
        self.wl_list.clear()
        for item in items:
            self.wl_list.addItem(item)

    def add_whitelist_item(self):
        item = self.wl_edit.text().strip()
        if not item:
            return
        item_lower = item.lower()
        existing = []
        for i in range(self.wl_list.count()):
            existing.append(self.wl_list.item(i).text().lower())
        if item_lower in existing:
            InfoBar.warning("豁免进程重复" if Trans.CURRENT_LANG == "zh_CN" else "Duplicate Process", 
                            f"进程 {item} 已经存在于白名单列表中。" if Trans.CURRENT_LANG == "zh_CN" else f"Process {item} is already in the whitelist.", parent=self)
            return
        self.wl_list.addItem(item_lower)
        self.wl_edit.clear()
        self.save_whitelist_settings()
        InfoBar.success("豁免进程已添加" if Trans.CURRENT_LANG == "zh_CN" else "Process Added", 
                        f"进程 {item_lower} 已成功分配豁免并加入白名单。" if Trans.CURRENT_LANG == "zh_CN" else f"Process {item_lower} successfully added to whitelist with exclusion.", parent=self)

    def remove_whitelist_item(self):
        selected_item = self.wl_list.currentItem()
        if not selected_item:
            InfoBar.warning("操作被拒" if Trans.CURRENT_LANG == "zh_CN" else "Operation Denied", 
                            "请在列表中选定需要移出的豁免进程。" if Trans.CURRENT_LANG == "zh_CN" else "Please select a process from the list to remove.", parent=self)
            return
        name = selected_item.text()
        self.wl_list.takeItem(self.wl_list.row(selected_item))
        self.save_whitelist_settings()
        InfoBar.info("豁免进程已移出" if Trans.CURRENT_LANG == "zh_CN" else "Process Removed", 
                     f"进程 {name} 已从白名单中移除并恢复默认后台调度规则。" if Trans.CURRENT_LANG == "zh_CN" else f"Process {name} removed from whitelist, default rules restored.", parent=self)

    def get_whitelist_items(self) -> list:
        items = []
        for i in range(self.wl_list.count()):
            items.append(self.wl_list.item(i).text())
        return items

    def save_whitelist_settings(self):
        win = self.parent_window
        if win:
            win.settings.custom_whitelist = self.get_whitelist_items()
            win.save_settings()
