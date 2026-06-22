# -*- coding: utf-8 -*-
import os
import uuid
from PySide6.QtCore import Qt, Signal, Slot, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame
)
from qfluentwidgets import (
    ScrollArea, TitleLabel, SubtitleLabel, BodyLabel, PushButton,
    PrimaryPushButton, CardWidget, SwitchButton, Slider, InfoBar,
    FluentIcon, ToolButton, LineEdit, TransparentToolButton,
    CaptionLabel, IconWidget, isDarkTheme
)
from core_commander.config.settings import AppSettings
from core_commander.core.quick_chat_manager import QuickChatManager
from core_commander.utils.i18n import Trans
from core_commander.utils.logger import logger

class PhraseCard(CardWidget):
    textChanged = Signal(str, str)      # rule_id, new_text
    enableToggled = Signal(str, bool)   # rule_id, enabled
    hotkeyClicked = Signal(str)         # rule_id
    deleteClicked = Signal(str)         # rule_id
    
    def __init__(self, rule, parent=None):
        super().__init__(parent)
        self.rule_id = rule["id"]
        self.setFixedHeight(56)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 6, 16, 6)
        layout.setSpacing(12)
        
        # 1. Enabled Switch
        self.switch_btn = SwitchButton(self)
        self.switch_btn.setChecked(rule.get("enabled", True))
        self.switch_btn.checkedChanged.connect(lambda checked: self.enableToggled.emit(self.rule_id, checked))
        self.switch_btn.setFocusPolicy(Qt.NoFocus)
        
        # 2. Text Input Field (Direct editing)
        self.edit = LineEdit(self)
        self.edit.setText(rule.get("text", ""))
        self.edit.setPlaceholderText("在此输入快捷发言文本...")
        self.edit.editingFinished.connect(self._on_editing_finished)
        
        # 3. Hotkey Button
        hk_name = rule.get("hotkey_name", "无热键")
        self.btn_hk = PushButton(hk_name, self, FluentIcon.SPEED_HIGH)
        self.btn_hk.setFixedWidth(130)
        self.btn_hk.clicked.connect(lambda: self.hotkeyClicked.emit(self.rule_id))
        self.btn_hk.setFocusPolicy(Qt.NoFocus)
        
        # 4. Delete Tool Button
        self.btn_del = TransparentToolButton(FluentIcon.DELETE, self)
        self.btn_del.setFixedSize(32, 32)
        self.btn_del.clicked.connect(lambda: self.deleteClicked.emit(self.rule_id))
        self.btn_del.setFocusPolicy(Qt.NoFocus)
        
        layout.addWidget(self.switch_btn)
        layout.addWidget(self.edit, 1)  # Expand text field
        layout.addWidget(self.btn_hk)
        layout.addWidget(self.btn_del)
        
    def _on_editing_finished(self):
        self.textChanged.emit(self.rule_id, self.edit.text())


class QuickChatPage(ScrollArea):
    """
    GUI Page for configuring Quick Chat / Quick Speech phrases, binding individual triggering hotkeys,
    and running "Spam Mode" (轰炸模式) with looping intervals.
    Refactored using card stream layout instead of traditional tables for a premium dashboard aesthetic.
    """
    @property
    def input_hook(self):
        return self.main_win.input_hook_thread

    def __init__(self, main_win):
        super().__init__()
        self.setObjectName("quickChatPage")
        self.main_win = main_win
        self.settings = main_win.settings
        self.manager = QuickChatManager()

        # Binding state
        self.binding_mode = None  # "individual" or "spam"
        self.binding_rule_id = None
        self.phrase_cards = {}    # Maps rule_id -> PhraseCard

        self.view = QWidget(self)
        self.layout = QVBoxLayout(self.view)
        self.layout.setContentsMargins(30, 20, 30, 20)
        self.layout.setSpacing(16)

        # 1. Page Title
        self.title_label = TitleLabel("快捷发言与文字轰炸", self.view)
        self.layout.addWidget(self.title_label)

        # 2. Spam Mode Card (文字轰炸设置)
        self.init_spam_card()

        # 3. Spacing
        self.layout.addSpacing(10)

        # 4. List Title
        self.lbl_list_title = SubtitleLabel("发言内容与热键映射列表", self.view)
        self.lbl_list_title.setStyleSheet("font-size: 15px; font-weight: bold;")
        self.layout.addWidget(self.lbl_list_title)

        # 5. Phrase Cards Container
        self.list_widget = QWidget(self.view)
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(10)
        self.layout.addWidget(self.list_widget)

        # 6. Add Button
        self.btn_add = PrimaryPushButton("添加新发言", self.view, FluentIcon.ADD)
        self.btn_add.setFixedWidth(160)
        self.btn_add.clicked.connect(self.on_add_rule)
        self.layout.addWidget(self.btn_add)

        # 7. Bottom Stretch
        self.layout.addStretch(1)

        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setStyleSheet("QScrollArea { background-color: transparent; border: none; }")

        # Listen to manager updates
        self.manager.spam_state_changed.connect(self.on_spam_state_changed)

        # Initial Load
        self.populate_list()

    def init_spam_card(self):
        is_dark = isDarkTheme()
        self.card_spam = CardWidget(self.view)
        spam_layout = QVBoxLayout(self.card_spam)
        spam_layout.setContentsMargins(20, 20, 20, 20)
        spam_layout.setSpacing(16)

        lbl_spam_title = SubtitleLabel("文字轰炸全局设置 (Text Spam Mode)", self.card_spam)
        lbl_spam_title.setStyleSheet("font-size: 15px; font-weight: bold;")
        spam_layout.addWidget(lbl_spam_title)

        # Row 1: Enable & Hotkey
        row_1 = QHBoxLayout()
        icon_1 = IconWidget(FluentIcon.SPEED_HIGH, self.card_spam)
        icon_1.setFixedSize(20, 20)
        
        info_layout_1 = QVBoxLayout()
        info_layout_1.setSpacing(2)
        lbl_title_1 = BodyLabel("启用文字轰炸", self.card_spam)
        lbl_title_1.setStyleSheet("font-weight: bold;")
        lbl_desc_1 = CaptionLabel("按绑定的全局热键启动或停止自动发言队列", self.card_spam)
        info_layout_1.addWidget(lbl_title_1)
        info_layout_1.addWidget(lbl_desc_1)
        
        self.switch_spam = SwitchButton(self.card_spam)
        self.switch_spam.setChecked(self.manager.is_spam_active())
        self.switch_spam.checkedChanged.connect(self.on_switch_spam_changed)
        self.switch_spam.setFocusPolicy(Qt.NoFocus)
        
        self.btn_spam_hotkey = PushButton(
            f"触发热键: {self.manager.spam_hotkey_name}", 
            self.card_spam, 
            FluentIcon.SPEED_HIGH
        )
        self.btn_spam_hotkey.setFixedWidth(150)
        self.btn_spam_hotkey.clicked.connect(self.on_bind_spam_hotkey)
        self.btn_spam_hotkey.setFocusPolicy(Qt.NoFocus)
        
        row_1.addWidget(icon_1)
        row_1.addLayout(info_layout_1)
        row_1.addStretch()
        row_1.addWidget(self.btn_spam_hotkey)
        row_1.addSpacing(10)
        row_1.addWidget(self.switch_spam)
        spam_layout.addLayout(row_1)

        # Divider 1
        div_1 = QFrame(self.card_spam)
        div_1.setFixedHeight(1)
        div_1.setStyleSheet("background-color: rgba(255, 255, 255, 0.08);" if is_dark else "background-color: rgba(0, 0, 0, 0.06);")
        spam_layout.addWidget(div_1)

        # Row 2: Loop Toggle
        row_2 = QHBoxLayout()
        icon_2 = IconWidget(FluentIcon.SYNC, self.card_spam)
        icon_2.setFixedSize(20, 20)
        
        info_layout_2 = QVBoxLayout()
        info_layout_2.setSpacing(2)
        lbl_title_2 = BodyLabel("循环发送模式", self.card_spam)
        lbl_title_2.setStyleSheet("font-weight: bold;")
        lbl_desc_2 = CaptionLabel("开启后，自动发言队列将无限循环发送，直至再次触发热键终止", self.card_spam)
        info_layout_2.addWidget(lbl_title_2)
        info_layout_2.addWidget(lbl_desc_2)
        
        self.switch_loop = SwitchButton(self.card_spam)
        self.switch_loop.setChecked(self.manager.spam_loop)
        self.switch_loop.checkedChanged.connect(self.on_switch_loop_changed)
        self.switch_loop.setFocusPolicy(Qt.NoFocus)
        
        row_2.addWidget(icon_2)
        row_2.addLayout(info_layout_2)
        row_2.addStretch()
        row_2.addWidget(self.switch_loop)
        spam_layout.addLayout(row_2)

        # Divider 2
        div_2 = QFrame(self.card_spam)
        div_2.setFixedHeight(1)
        div_2.setStyleSheet("background-color: rgba(255, 255, 255, 0.08);" if is_dark else "background-color: rgba(0, 0, 0, 0.06);")
        spam_layout.addWidget(div_2)

        # Row 3: Delay Slider
        row_3 = QHBoxLayout()
        icon_3 = IconWidget(FluentIcon.HISTORY, self.card_spam)
        icon_3.setFixedSize(20, 20)
        
        info_layout_3 = QVBoxLayout()
        info_layout_3.setSpacing(2)
        lbl_title_3 = BodyLabel("发送延迟间隔", self.card_spam)
        lbl_title_3.setStyleSheet("font-weight: bold;")
        lbl_desc_3 = CaptionLabel("队列中每条发言消息模拟回车打字发送的时间间隔 (ms)", self.card_spam)
        info_layout_3.addWidget(lbl_title_3)
        info_layout_3.addWidget(lbl_desc_3)
        
        self.slider_delay = Slider(Qt.Orientation.Horizontal, self.card_spam)
        self.slider_delay.setRange(100, 5000)
        self.slider_delay.setValue(self.manager.spam_interval_ms)
        self.slider_delay.setFixedWidth(200)
        self.lbl_delay_val = BodyLabel(f"{self.manager.spam_interval_ms} ms", self.card_spam)
        self.lbl_delay_val.setFixedWidth(60)
        self.slider_delay.valueChanged.connect(self.on_delay_changed)
        
        row_3.addWidget(icon_3)
        row_3.addLayout(info_layout_3)
        row_3.addStretch()
        row_3.addWidget(self.slider_delay)
        row_3.addWidget(self.lbl_delay_val)
        spam_layout.addLayout(row_3)

        self.layout.addWidget(self.card_spam)

    def populate_list(self):
        # Clear existing
        while self.list_layout.count():
            child = self.list_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
                
        self.phrase_cards = {}

        # Build cards
        for rule in self.manager.rules:
            card = PhraseCard(rule, self.list_widget)
            card.textChanged.connect(self.on_rule_text_changed)
            card.enableToggled.connect(self.on_rule_enable_toggled)
            card.hotkeyClicked.connect(self.on_bind_individual_hotkey)
            card.deleteClicked.connect(self.on_delete_rule)
            
            self.list_layout.addWidget(card)
            self.phrase_cards[rule["id"]] = card

    def on_switch_spam_changed(self, checked):
        if checked:
            from core_commander.ui.activation_dialog import require_license
            if not require_license(self, "快捷发言与文字轰炸"):
                self.switch_spam.blockSignals(True)
                self.switch_spam.setChecked(False)
                self.switch_spam.blockSignals(False)
                return

            # 1. Check if there are enabled rules
            enabled_rules = [r for r in self.manager.rules if r.get("enabled", True)]
            if not enabled_rules:
                self.switch_spam.blockSignals(True)
                self.switch_spam.setChecked(False)
                self.switch_spam.blockSignals(False)
                InfoBar.warning(
                    "无法开启轰炸",
                    "当前发言列表中没有任何启用的文本，请先勾选启用至少一条发言。",
                    parent=self.main_win
                )
                return

            # 2. Minimize main window if our own app is currently focused
            minimized = False
            try:
                import win32gui
                import win32process
                hwnd = win32gui.GetForegroundWindow()
                if hwnd:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    if pid == os.getpid():
                        self.main_win.showMinimized()
                        minimized = True
            except Exception as e:
                logger.error(f"Error minimizing window before spam: {e}")

            if checked != self.manager.is_spam_active():
                if minimized:
                    QTimer.singleShot(600, lambda: self.manager.start_spam_mode())
                else:
                    self.manager.start_spam_mode()
        else:
            if checked != self.manager.is_spam_active():
                self.manager.stop_spam_mode()

    def on_spam_state_changed(self, active):
        self.switch_spam.blockSignals(True)
        self.switch_spam.setChecked(active)
        self.switch_spam.blockSignals(False)
        if active:
            InfoBar.success("轰炸启动", "已开启文字轰炸队列，在游戏内将会连续发送发言文本。", parent=self.main_win)
        else:
            InfoBar.warning("轰炸终止", "已关闭文字轰炸队列。", parent=self.main_win)

    def on_switch_loop_changed(self, checked):
        self.manager.spam_loop = checked
        self.manager.save_rules()

    def on_delay_changed(self, val):
        self.lbl_delay_val.setText(f"{val} ms")
        self.manager.spam_interval_ms = val
        self.manager.save_rules()

    def on_add_rule(self):
        from core_commander.ui.activation_dialog import require_license
        if not require_license(self, "快捷发言与文字轰炸"):
            return
        new_id = str(uuid.uuid4())
        new_rule = {
            "id": new_id,
            "text": "在这里输入发言...",
            "hotkey_code": 0,
            "hotkey_type": "keyboard",
            "hotkey_name": "未设置",
            "enabled": True
        }
        self.manager.rules.append(new_rule)
        self.manager.save_rules()
        self.populate_list()
        InfoBar.success("添加成功", "新发言已成功创建，点击文本框即可进行直接编辑。", parent=self.main_win)

    def on_delete_rule(self, r_id):
        self.manager.rules = [r for r in self.manager.rules if r["id"] != r_id]
        self.manager.save_rules()
        self.populate_list()
        InfoBar.warning("已删除", "发言文本已成功删除。", parent=self.main_win)

    def on_rule_enable_toggled(self, r_id, checked):
        for rule in self.manager.rules:
            if rule["id"] == r_id:
                rule["enabled"] = checked
                break
        self.manager.save_rules()

    def on_rule_text_changed(self, r_id, new_text):
        for rule in self.manager.rules:
            if rule["id"] == r_id:
                rule["text"] = new_text
                break
        self.manager.save_rules()

    # --- Hotkey Binding ---
    def on_bind_spam_hotkey(self):
        if not self.input_hook:
            InfoBar.error("错误", "全局输入驱动钩子尚未就绪，请稍后重试。", parent=self.main_win)
            return
        self.binding_mode = "spam"
        self.btn_spam_hotkey.setText("请按下按键...")
        self.btn_spam_hotkey.setEnabled(False)
        self.input_hook.key_bind_captured.connect(self.on_hotkey_captured)
        self.input_hook.set_binding_mode(True)

    def on_bind_individual_hotkey(self, r_id):
        if not self.input_hook:
            InfoBar.error("错误", "全局输入驱动钩子尚未就绪，请稍后重试。", parent=self.main_win)
            return
        self.binding_mode = "individual"
        self.binding_rule_id = r_id
        
        # Find card to update button text
        card = self.phrase_cards.get(r_id)
        if card:
            card.btn_hk.setText("请按下按键...")
            card.btn_hk.setEnabled(False)
                
        self.input_hook.key_bind_captured.connect(self.on_hotkey_captured)
        self.input_hook.set_binding_mode(True)

    def on_hotkey_captured(self, name, code, key_type):
        try:
            self.input_hook.key_bind_captured.disconnect(self.on_hotkey_captured)
        except Exception:
            pass
            
        self.input_hook.set_binding_mode(False)

        if self.binding_mode == "spam":
            self.manager.spam_hotkey_name = name
            self.manager.spam_hotkey_code = code
            self.manager.spam_hotkey_type = "keyboard" if key_type == "keyboard" else "mouse"
            self.btn_spam_hotkey.setText(f"触发热键: {name}")
            self.btn_spam_hotkey.setEnabled(True)
            self.manager.save_rules()
            InfoBar.success("绑定成功", f"文字轰炸触发热键已成功修改为: {name}", parent=self.main_win)
        elif self.binding_mode == "individual":
            r_id = self.binding_rule_id
            for rule in self.manager.rules:
                if rule["id"] == r_id:
                    rule["hotkey_name"] = name
                    rule["hotkey_code"] = code
                    rule["hotkey_type"] = "keyboard" if key_type == "keyboard" else "mouse"
                    break
            self.manager.save_rules()
            InfoBar.success("绑定成功", f"当前发言触发热键已成功修改为: {name}", parent=self.main_win)
            self.populate_list()

        self.binding_mode = None
        self.binding_rule_id = None

    def closeEvent(self, event):
        try:
            self.manager.spam_state_changed.disconnect(self.on_spam_state_changed)
        except Exception:
            pass
        super().closeEvent(event)

    def destroy(self, destroyWindow=True, destroySubWindows=True):
        try:
            self.manager.spam_state_changed.disconnect(self.on_spam_state_changed)
        except Exception:
            pass
        super().destroy(destroyWindow, destroySubWindows)

    def retranslate_ui(self):
        # Placeholder to support language and theme translation changes
        pass
