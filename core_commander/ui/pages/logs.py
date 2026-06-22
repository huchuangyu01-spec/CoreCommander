# -*- coding: utf-8 -*-
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QTextCursor, QColor
from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QTextEdit, QLabel, QApplication
from qfluentwidgets import TitleLabel, SubtitleLabel, PushButton, FluentIcon, isDarkTheme, IndeterminateProgressRing, MessageBox
from core_commander.utils.i18n import Trans

class LogOverlay(QFrame):
    """
    An overlay panel that covers the entire application window to show real-time logs.
    It blocks interactions with the underlying window until the process is complete.
    """
    confirmed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("LogOverlay")
        self.hide() # Hidden by default
        
        # Overlay styling - transparent backing with premium blur / glassmorphism styling
        self.update_style()
        
        # Layout
        self.vBoxLayout = QVBoxLayout(self)
        self.vBoxLayout.setContentsMargins(40, 40, 40, 40)
        self.vBoxLayout.setSpacing(20)
        
        # Header Area
        self.headerLayout = QVBoxLayout()
        self.headerLayout.setSpacing(8)
        self.titleLabel = TitleLabel("正在部署系统内核与进程调度策略...", self)
        self.subtitleLabel = SubtitleLabel("正在调整系统注册表与关键服务以改善前台交互与渲染管线吞吐，请稍候。", self)
        self.headerLayout.addWidget(self.titleLabel)
        self.headerLayout.addWidget(self.subtitleLabel)
        self.vBoxLayout.addLayout(self.headerLayout)
        
        # Monospace Monitored Terminal View
        self.consoleTextEdit = QTextEdit(self)
        self.consoleTextEdit.setReadOnly(True)
        self.consoleTextEdit.setUndoRedoEnabled(False)
        self.consoleTextEdit.setAcceptRichText(True)
        
        # Monospace Font
        font = QFont("Consolas", 10)
        if not font.exactMatch():
            font = QFont("Courier New", 10)
        self.consoleTextEdit.setFont(font)
        self.consoleTextEdit.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.vBoxLayout.addWidget(self.consoleTextEdit)
        
        # Bottom Actions / Loading Indicator Panel
        self.bottomLayout = QHBoxLayout()
        self.bottomLayout.setSpacing(15)
        
        # Left status text
        self.status_label = QLabel("当前状态: 策略部署中...", self)
        self.status_label.setStyleSheet("font-weight: 500; font-size: 13px; color: #E2B13C;")
        self.bottomLayout.addWidget(self.status_label)
        
        self.bottomLayout.addStretch(1)
        
        # Spinner / Loading ring
        self.progress_ring = IndeterminateProgressRing(self)
        self.progress_ring.setFixedSize(24, 24)
        self.bottomLayout.addWidget(self.progress_ring)
        
        # Reboot Button (hidden by default)
        self.btn_reboot = PushButton("立即重启", self)
        self.btn_reboot.setIcon(FluentIcon.POWER_BUTTON)
        self.btn_reboot.setFixedWidth(120)
        self.btn_reboot.clicked.connect(self.on_reboot_clicked)
        self.btn_reboot.hide()
        self.bottomLayout.addWidget(self.btn_reboot)
        
        # Wait / Confirm Action Button
        self.btn_action = PushButton("等待响应", self)
        self.btn_action.setIcon(FluentIcon.STOP_WATCH)
        self.btn_action.setFixedWidth(120)
        self.btn_action.setEnabled(False) # Disabled during waiting
        self.btn_action.clicked.connect(self.on_action_clicked)
        self.bottomLayout.addWidget(self.btn_action)
        
        self.vBoxLayout.addLayout(self.bottomLayout)
        
        # Connect to parent's resize if needed
        if parent:
            parent.installEventFilter(self)
            
    def eventFilter(self, obj, event):
        # Dynamically resize to match parent window size
        if obj == self.parent() and event.type() == event.Type.Resize:
            if self.isVisible():
                self.setGeometry(self.parent().rect())
        return super().eventFilter(obj, event)

    def update_style(self):
        is_dark = isDarkTheme()
        if is_dark:
            bg_color = "rgba(18, 18, 18, 0.95)"
            border_color = "rgba(255, 255, 255, 0.1)"
            text_color = "#E0E0E0"
            console_bg = "rgba(10, 10, 10, 0.8)"
        else:
            bg_color = "rgba(240, 242, 245, 0.95)"
            border_color = "rgba(0, 0, 0, 0.08)"
            text_color = "#2E2E2E"
            console_bg = "rgba(255, 255, 255, 0.8)"
            
        self.setStyleSheet(f"""
            #LogOverlay {{
                background-color: {bg_color};
                border: none;
            }}
            QTextEdit {{
                background-color: {console_bg};
                border: 1px solid {border_color};
                border-radius: 8px;
                color: {text_color};
                padding: 15px;
                line-height: 140%;
            }}
        """)
        
    def start_loading(self, title="正在部署系统内核与进程调度策略...", subtitle="正在调整系统注册表与关键服务以改善前台交互与渲染管线吞吐，请稍候。"):
        self.titleLabel.setText(title)
        self.subtitleLabel.setText(subtitle)
        self.consoleTextEdit.clear()
        
        # State: waiting
        self.status_label.setText("当前状态: 策略部署中...")
        self.status_label.setStyleSheet("font-weight: 500; font-size: 13px; color: #E2B13C;")
        
        self.progress_ring.show()
        
        # Reset buttons to disabled waiting state
        self.btn_reboot.hide()
        self.btn_action.setText("等待响应")
        self.btn_action.setIcon(FluentIcon.STOP_WATCH)
        self.btn_action.setEnabled(False)
        self.btn_action.setStyleSheet("")
        
        # Show and raise overlay
        if self.parent():
            self.setGeometry(self.parent().rect())
        self.show()
        self.raise_()

    def finish_loading(self, success=True, msg="系统调度策略配置完成。", reboot_required=False):
        self.progress_ring.hide()
        
        if success:
            self.titleLabel.setText("策略部署成功生效")
            self.subtitleLabel.setText("所有选定的系统参数与进程调度策略已成功写入底层内核配置。")
            self.status_label.setText("当前状态: 调度策略已生效")
            self.status_label.setStyleSheet("font-weight: bold; font-size: 13px; color: #4CAF50;")
            
            # Change button to enabled green/primary Confirm button
            self.btn_action.setText("确认")
            self.btn_action.setIcon(FluentIcon.COMPLETED.icon(color=QColor("white")))
            self.btn_action.setEnabled(True)
            
            # Style button as a primary success colored button with correct padding to resolve overlap
            self.btn_action.setStyleSheet("""
                PushButton {
                    background-color: #2E7D32;
                    color: white;
                    border: 1px solid #1B5E20;
                    border-radius: 6px;
                    padding: 5px 12px 5px 36px;
                    font-size: 14px;
                    font-weight: bold;
                }
                PushButton:hover {
                    background-color: #388E3C;
                }
                PushButton:pressed {
                    background-color: #1B5E20;
                }
            """)
            
            # Show reboot button if required
            if reboot_required:
                self.btn_reboot.setText("立即重启")
                self.btn_reboot.setIcon(FluentIcon.POWER_BUTTON.icon(color=QColor("white")))
                self.btn_reboot.setStyleSheet("""
                    PushButton {
                        background-color: #D84315;
                        color: white;
                        border: 1px solid #BF360C;
                        border-radius: 6px;
                        padding: 5px 12px 5px 36px;
                        font-size: 14px;
                        font-weight: bold;
                    }
                    PushButton:hover {
                        background-color: #E64A19;
                    }
                    PushButton:pressed {
                        background-color: #BF360C;
                    }
                """)
                self.btn_reboot.show()
            else:
                self.btn_reboot.hide()
        else:
            self.titleLabel.setText("部分策略部署未完成")
            self.subtitleLabel.setText(f"错误信息: {msg}")
            self.status_label.setText("当前状态: 部署失败")
            self.status_label.setStyleSheet("font-weight: bold; font-size: 13px; color: #FF5252;")
            
            # Change button to enabled red/warning Confirm button
            self.btn_action.setText("确认")
            self.btn_action.setIcon(FluentIcon.CLOSE.icon(color=QColor("white")))
            self.btn_action.setEnabled(True)
            self.btn_action.setStyleSheet("""
                PushButton {
                    background-color: #C62828;
                    color: white;
                    border: 1px solid #B71C1C;
                    border-radius: 6px;
                    padding: 5px 12px 5px 36px;
                    font-size: 14px;
                    font-weight: bold;
                }
                PushButton:hover {
                    background-color: #D32F2F;
                }
                PushButton:pressed {
                    background-color: #B71C1C;
                }
            """)
            self.btn_reboot.hide()

    def append_log(self, message: str, level: str = "info"):
        is_dark = isDarkTheme()
        color_map = {
            "success": "#4CAF50" if is_dark else "#2E7D32",
            "warning": "#FFB300" if is_dark else "#F57F17",
            "error": "#FF5252" if is_dark else "#C62828",
            "critical": "#FF1744" if is_dark else "#B71C1C",
            "debug": "#78909C" if is_dark else "#546E7A",
            "info": "#E0E0E0" if is_dark else "#2E2E2E"
        }
        color = color_map.get(level.lower(), color_map["info"])
        html_msg = f"<span style='color: {color};'>{message}</span>"
        self.consoleTextEdit.append(html_msg)
        
        # Auto-scroll to bottom
        cursor = self.consoleTextEdit.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.consoleTextEdit.setTextCursor(cursor)

    def on_action_clicked(self):
        self.hide()
        self.confirmed.emit()

    def on_reboot_clicked(self):
        title = "立即重启系统？" if Trans.CURRENT_LANG == "zh_CN" else "Reboot System Now?"
        content = "为了使新部署的系统优化策略完全生效，建议立即重启计算机。\n\n请确保已保存所有未保存的工作。确定要立即重启吗？" if Trans.CURRENT_LANG == "zh_CN" else "To apply the newly deployed system optimization policies completely, it is recommended to reboot your computer immediately.\n\nPlease save any unsaved work. Are you sure you want to reboot now?"
        
        dialog = MessageBox(title, content, self.window())
        dialog.yesButton.setText("确定" if Trans.CURRENT_LANG == "zh_CN" else "Confirm")
        dialog.cancelButton.setText("取消" if Trans.CURRENT_LANG == "zh_CN" else "Cancel")
        if dialog.exec():
            import subprocess  # nosec
            try:
                subprocess.run(["shutdown", "/r", "/t", "0"], timeout=5)  # nosec
            except Exception:  # nosec
                pass


