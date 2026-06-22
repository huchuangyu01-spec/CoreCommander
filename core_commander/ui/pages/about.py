# -*- coding: utf-8 -*-
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QPen, QColor, QBrush
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QSizePolicy, QApplication
from qfluentwidgets import ScrollArea, TitleLabel, SubtitleLabel, BodyLabel, CaptionLabel, ElevatedCardWidget, isDarkTheme, PushButton, ProgressBar
from core_commander.utils.i18n import Trans

class ChipLogoWidget(QWidget):
    """
    Custom widget rendering a futuristic glowing microchip vector icon using QPainter.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(100, 100)
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        is_dark = isDarkTheme()
        accent_color = QColor("#00F2FE") if is_dark else QColor("#0082C8")
        
        # Outer rounded core body
        pen = QPen(accent_color, 2)
        painter.setPen(pen)
        painter.setBrush(QBrush(QColor(accent_color.red(), accent_color.green(), accent_color.blue(), 15 if is_dark else 8)))
        
        rect = QRectF(20, 20, 60, 60)
        painter.drawRoundedRect(rect, 8, 8)
        
        # Inner core CPU die
        core_rect = QRectF(40, 40, 20, 20)
        painter.drawRect(core_rect)
        
        # Draw CPU pin connections
        # Top
        painter.drawLine(35, 8, 35, 20)
        painter.drawLine(50, 8, 50, 20)
        painter.drawLine(65, 8, 65, 20)
        # Bottom
        painter.drawLine(35, 80, 35, 92)
        painter.drawLine(50, 80, 50, 92)
        painter.drawLine(65, 80, 65, 92)
        # Left
        painter.drawLine(8, 35, 20, 35)
        painter.drawLine(8, 50, 20, 50)
        painter.drawLine(8, 65, 20, 65)
        # Right
        painter.drawLine(80, 35, 92, 35)
        painter.drawLine(80, 50, 92, 50)
        painter.drawLine(80, 65, 92, 65)

class AboutPage(ScrollArea):
    """
    Displays structured metadata and credits for Core Commander with high-tech visual design.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("AboutPage")
        self.parent_window = parent
        
        # Scroll area styling
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setViewportMargins(0, 0, 0, 0)
        self.setStyleSheet("QScrollArea { background-color: transparent; border: none; }")
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        
        # Scroll layout wrapper
        self.view = QWidget()
        self.view.setObjectName("AboutPageView")
        self.view.setStyleSheet("#AboutPageView { background-color: transparent; }")
        self.view.setMaximumWidth(700) # Comfortable maximum width for about info
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        
        self.vBoxLayout = QVBoxLayout(self.view)
        self.vBoxLayout.setContentsMargins(30, 30, 30, 30)
        self.vBoxLayout.setSpacing(20)
        self.vBoxLayout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        self.title_label = TitleLabel("关于本软件")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.vBoxLayout.addWidget(self.title_label)
        
        card = ElevatedCardWidget()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(30, 30, 30, 30)
        card_layout.setSpacing(20)
        card_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Chip Vector Logo
        logo = ChipLogoWidget()
        card_layout.addWidget(logo, 0, Qt.AlignmentFlag.AlignCenter)
        
        self.app_name = SubtitleLabel("Core Commander")
        self.app_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.app_name)
        
        self.subtitle = BodyLabel("内核调度与系统级性能调优控制台")
        self.subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.subtitle)
        
        # Separation line or space
        card_layout.addSpacing(10)
        
        # System parameters list layout
        self.info_layout = QVBoxLayout()
        self.info_layout.setSpacing(16)
        self.info_layout.setContentsMargins(20, 0, 20, 0)
        
        # We will hold references to name/value labels in details
        self.detail_labels = []
        
        for idx in range(8):
            row_layout = QHBoxLayout()
            row_layout.setSpacing(15)
            row_layout.setContentsMargins(0, 0, 0, 0)
            
            lbl_name = CaptionLabel("")
            lbl_val = BodyLabel("")
            lbl_val.setWordWrap(True)
            lbl_val.setOpenExternalLinks(True)
            lbl_name.setFixedWidth(120)
            lbl_name.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
            lbl_val.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            
            row_layout.addWidget(lbl_name)
            row_layout.addWidget(lbl_val, 1)
            
            self.info_layout.addLayout(row_layout)
            self.detail_labels.append((lbl_name, lbl_val))
            
        # Add layout to container
        grid_container = QWidget()
        grid_container.setLayout(self.info_layout)
        card_layout.addWidget(grid_container)
        
        # Elegant Divider Line
        self.divider = QWidget()
        self.divider.setFixedHeight(1)
        card_layout.addWidget(self.divider)

        # Update Section Container
        self.update_container = QWidget()
        update_vbox = QVBoxLayout(self.update_container)
        update_vbox.setContentsMargins(20, 10, 20, 10)
        update_vbox.setSpacing(10)

        # Horizontal Row for Status Text & Button
        update_row = QHBoxLayout()
        self.lbl_update_status = BodyLabel("", self.update_container)
        self.lbl_update_status.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.btn_check_update = PushButton("检查更新", self.update_container)
        self.btn_check_update.setFixedWidth(120)
        
        update_row.addWidget(self.lbl_update_status, 1)
        update_row.addWidget(self.btn_check_update)
        update_vbox.addLayout(update_row)

        # Smooth 4px height Progress Bar
        self.update_progress = ProgressBar(self.update_container)
        self.update_progress.setVisible(False)
        self.update_progress.setRange(0, 100)
        self.update_progress.setFixedHeight(4)
        update_vbox.addWidget(self.update_progress)

        card_layout.addWidget(self.update_container)
        
        self.vBoxLayout.addWidget(card)
        self.vBoxLayout.addStretch(1)
        
        self.btn_check_update.clicked.connect(self.check_for_updates)
        
        self.retranslate_ui()
 
    def retranslate_ui(self):
        # Dark theme check styling
        opacity_style = "color: rgba(255, 255, 255, 0.6);" if isDarkTheme() else "color: rgba(0, 0, 0, 0.6);"
        self.subtitle.setStyleSheet(opacity_style)
        
        self.title_label.setText(Trans.get("about_title"))
        self.subtitle.setText(Trans.get("about_desc"))
        
        cpu_name_str = self.parent_window.cpu_name if self.parent_window else "Intel/AMD Processor"
        ram_gb_str = f"{self.parent_window.ram_gb} GB" if self.parent_window else "Memory Size"
        
        details = [
            (Trans.get("about_ver"), "v2.0"),
            (Trans.get("about_topo"), cpu_name_str),
            (Trans.get("about_mem"), ram_gb_str),
            (Trans.get("about_author"), "B站 _可燃垃圾"),
            (Trans.get("about_github"), "github.com/huchuangyu01-spec/CoreCommander"),
            (Trans.get("about_recruitment"), Trans.get("about_recruitment_desc")),
            (Trans.get("about_feedback"), "2217965124@qq.com"),
            (Trans.get("about_group"), '<a href="https://qm.qq.com/q/zDzd9IYn1C" style="color: #0078D4; text-decoration: none;">684082185</a>')
        ]
        
        for idx, (label, val) in enumerate(details):
            if idx < len(self.detail_labels):
                lbl_name, lbl_val = self.detail_labels[idx]
                lbl_name.setText(label)
                lbl_val.setText(val)

        # Divider color dynamic update
        divider_color = "rgba(255, 255, 255, 0.08)" if isDarkTheme() else "rgba(0, 0, 0, 0.08)"
        if hasattr(self, 'divider'):
            self.divider.setStyleSheet(f"background-color: {divider_color};")

        # Update controls text
        if hasattr(self, 'btn_check_update'):
            is_working = False
            if hasattr(self, 'check_worker') and self.check_worker and self.check_worker.isRunning():
                is_working = True
            if hasattr(self, 'download_worker') and self.download_worker and self.download_worker.isRunning():
                is_working = True
                
            if not is_working:
                if hasattr(self, 'update_metadata') and self.update_metadata and self.update_metadata.get("has_update"):
                    latest_version = self.update_metadata.get("latest_version")
                    self.lbl_update_status.setText(Trans.get("update_status_available").format(version=latest_version))
                    self.btn_check_update.setText(Trans.get("update_btn_download"))
                else:
                    self.lbl_update_status.setText(Trans.get("update_status_latest").format(version="2.0"))
                    self.btn_check_update.setText(Trans.get("update_btn_check"))

    def check_for_updates(self):
        self.btn_check_update.setEnabled(False)
        self.lbl_update_status.setText(Trans.get("update_status_checking"))
        
        from core_commander.core.updater import UpdateCheckWorker
        self.check_worker = UpdateCheckWorker(self)
        self.check_worker.checked.connect(self.on_update_checked)
        self.check_worker.finished.connect(self.check_worker.deleteLater)
        self.check_worker.start()

    def on_update_checked(self, success, result):
        self.btn_check_update.setEnabled(True)
        if not success:
            err = result.get("error", "Unknown error")
            self.lbl_update_status.setText(Trans.get("update_status_failed").format(error=err))
            return
            
        self.update_metadata = result
        if result.get("has_update"):
            latest_version = result.get("latest_version")
            self.lbl_update_status.setText(Trans.get("update_status_available").format(version=latest_version))
            self.btn_check_update.setText(Trans.get("update_btn_download"))
            
            try:
                self.btn_check_update.clicked.disconnect()
            except Exception:
                pass
            self.btn_check_update.clicked.connect(self.start_downloading_update)
        else:
            self.lbl_update_status.setText(Trans.get("update_status_latest").format(version="2.0"))
            try:
                self.btn_check_update.clicked.disconnect()
            except Exception:
                pass
            self.btn_check_update.clicked.connect(self.check_for_updates)

    def start_downloading_update(self):
        self.btn_check_update.setEnabled(False)
        self.update_progress.setVisible(True)
        self.update_progress.setValue(0)
        
        from core_commander.core.updater import UpdateDownloadWorker
        self.download_worker = UpdateDownloadWorker(self.update_metadata)
        self.download_worker.progress.connect(self.on_download_progress)
        self.download_worker.status.connect(self.on_download_status)
        self.download_worker.finished.connect(self.on_download_finished)
        self.download_worker.finished.connect(self.download_worker.deleteLater)
        self.download_worker.start()

    def on_download_progress(self, percent, speed_mbps):
        self.update_progress.setValue(percent)
        self.lbl_update_status.setText(Trans.get("update_status_downloading").format(percent=percent, speed=f"{speed_mbps:.2f}"))

    def on_download_status(self, status_key):
        self.lbl_update_status.setText(Trans.get(status_key))

    def on_download_finished(self, success, result):
        self.update_progress.setVisible(False)
        self.btn_check_update.setEnabled(True)
        
        if not success:
            self.lbl_update_status.setText(Trans.get("update_status_failed").format(error=result))
            self.btn_check_update.setText(Trans.get("update_btn_check"))
            try:
                self.btn_check_update.clicked.disconnect()
            except Exception:
                pass
            self.btn_check_update.clicked.connect(self.check_for_updates)
            return

        self.lbl_update_status.setText(Trans.get("update_status_success"))
        
        try:
            import ctypes
            ctypes.windll.shell32.ShellExecuteW(None, "runas", result, "/S", None, 1)
            QApplication.quit()
        except Exception as e:
            self.lbl_update_status.setText(Trans.get("update_status_failed").format(error=str(e)))
