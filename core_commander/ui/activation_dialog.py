import os
import sys
import time
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                               QApplication)
from PySide6.QtCore import Qt, QUrl, QThread, Signal
from PySide6.QtGui import QFont, QIcon, QDesktopServices
from qfluentwidgets import MessageBoxBase, SubtitleLabel, BodyLabel, LineEdit, CaptionLabel, InfoBar
from core_commander.core.hwid import get_hwid
from core_commander.core.license import license_manager

class LicenseVerifyWorker(QThread):
    finished_signal = Signal(bool, str)

    def __init__(self, key):
        super().__init__()
        self.key = key

    def run(self):
        try:
            hwid = get_hwid()
            success, msg = license_manager.verify_license_online(self.key, hwid)
            self.finished_signal.emit(success, msg)
        except Exception as e:
            self.finished_signal.emit(False, f"验证出错: {str(e)}")

class ActivationDialog(MessageBoxBase):
    def __init__(self, parent=None, feature_name="该功能"):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel(f"{feature_name} 需要升级", self)
        
        self.key_input = LineEdit(self)
        self.key_input.setPlaceholderText("请输入卡密...")
        self.key_input.setClearButtonEnabled(True)
        
        contact_layout = QHBoxLayout()
        contact_layout.setContentsMargins(0, 0, 0, 0)
        
        self.qq_label = CaptionLabel("没有卡密? 联系QQ：2217965124  |  ", self)
        
        self.group_label = CaptionLabel("QQ群：684082185", self)
        self.group_label.setStyleSheet("color: #0078D7; text-decoration: underline;")
        self.group_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.group_label.mousePressEvent = self._open_qq_group
        
        contact_layout.addWidget(self.qq_label)
        contact_layout.addWidget(self.group_label)
        contact_layout.addStretch(1)
        
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addSpacing(15)
        self.viewLayout.addWidget(self.key_input)
        self.viewLayout.addSpacing(10)
        self.viewLayout.addLayout(contact_layout)
        
        self.yesButton.setText("立即激活")
        self.cancelButton.setText("取消")
        
        self.widget.setMinimumWidth(360)
        self.worker = None
        
    def _open_qq_group(self, event):
        # Open QQ group link
        QDesktopServices.openUrl(QUrl("https://qm.qq.com/q/LAuzn7ZUUE"))
        
    def validate(self):
        if self.worker and self.worker.isRunning():
            return False

        key = self.key_input.text().strip()
        if not key:
            InfoBar.error("提示", "请输入有效的卡密！", parent=self)
            return False
            
        self.yesButton.setEnabled(False)
        self.cancelButton.setEnabled(False)
        self.yesButton.setText("验证中...")
        QApplication.processEvents()
        
        self.worker = LicenseVerifyWorker(key)
        self.worker.finished_signal.connect(self._on_verification_finished)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()
        return False

    def _on_verification_finished(self, success, msg):
        self.yesButton.setEnabled(True)
        self.cancelButton.setEnabled(True)
        self.yesButton.setText("立即激活")
        
        if success:
            # Find the MainWindow
            main_win = None
            p = self.parent()
            while p:
                if hasattr(p, 'update_license_display'):
                    main_win = p
                    break
                p = p.parent()
                
            if not main_win:
                for w in QApplication.topLevelWidgets():
                    if hasattr(w, 'update_license_display') and w != self:
                        main_win = w
                        break
                        
            if main_win:
                main_win.update_license_display()
            
            # Format expiry date for the message box
            if license_manager.expiry_timestamp > 0:
                from datetime import datetime
                expire_date = datetime.fromtimestamp(license_manager.expiry_timestamp).strftime('%Y-%m-%d %H:%M')
                msg += f"\n到期时间: {expire_date}"
                
            InfoBar.success("成功", msg, parent=main_win if main_win else QApplication.activeWindow(), duration=5000)
            self.accept()
        else:
            InfoBar.error("错误", msg, parent=self)

    def reject(self):
        if self.worker:
            try:
                self.worker.finished_signal.disconnect(self._on_verification_finished)
            except (TypeError, RuntimeError):
                pass
        super().reject()

def require_license(parent_widget, feature_name="该功能", silent=False):
    """
    Utility function to wrap license check and popup logic.
    Returns True if licensed, False if not (and optionally shows popup).
    """
    if license_manager.is_active:
        # Check if trial expired while running
        if license_manager.license_type == "trial" and license_manager.get_remaining_days() <= 0:
            license_manager.is_active = False
        else:
            return True
            
    if silent:
        return False
        
    dialog = ActivationDialog(parent_widget, feature_name)
    result = dialog.exec()
    return result == QDialog.Accepted
