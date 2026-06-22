# -*- coding: utf-8 -*-
import os
import sys
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QLinearGradient, QPainterPath, QPen
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QWidget, QPushButton, QLabel
from qfluentwidgets import (
    SubtitleLabel, BodyLabel, ProgressBar, setThemeColor, setTheme, Theme,
    CaptionLabel, IconWidget, FluentIcon, InfoBar
)
from core_commander.core.deployment.worker import DeploymentWorker

APP_NAME = "Core Commander"
VERSION = "2.0"
PUBLISHER = "B站 _可燃垃圾"

class DeploymentDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} - 核心依赖部署")
        self.setFixedSize(620, 400)
        self.setObjectName("DeploymentDialog")
        
        # Borderless window flags
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowSystemMenuHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        setTheme(Theme.LIGHT)
        setThemeColor("#0078D4")
        
        self.setStyleSheet("""
            #DeploymentDialog {
                background-color: transparent;
            }
            QLabel {
                color: #0f172a;
                font-family: "Segoe UI", "Segoe UI Variable", "Microsoft YaHei";
            }
            #LeftPanel QLabel {
                color: #ffffff;
            }
            #lbl_left_title {
                color: #ffffff;
                font-weight: bold;
                font-size: 16px;
                letter-spacing: 0.5px;
            }
            #lbl_left_ver {
                color: rgba(255, 255, 255, 0.7);
                font-size: 11px;
            }
            #lbl_left_pub {
                color: rgba(255, 255, 255, 0.4);
                font-size: 10px;
            }
            #page_content {
                background-color: rgba(255, 255, 255, 0.85);
                border: 1px solid rgba(226, 232, 240, 0.8);
                border-radius: 10px;
            }
            QPushButton {
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 6px;
                padding: 6px 18px;
                color: #334155;
                font-size: 13px;
                font-weight: 500;
                font-family: "Segoe UI", "Segoe UI Variable", "Microsoft YaHei";
                min-width: 76px;
                min-height: 26px;
            }
            QPushButton:hover {
                background-color: #f8fafc;
                border-color: #cbd5e1;
                color: #0f172a;
            }
            QPushButton:pressed {
                background-color: #f1f5f9;
            }
            #btn_exit {
                background-color: #ffffff;
                color: #334155;
            }
            #btn_exit:disabled {
                background-color: #e2e8f0;
                color: #94a3b8;
                border: 1px solid #e2e8f0;
            }
        """)
        
        self.worker = None
        self.log_history = []
        self._init_ui()
        self._start_deployment()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            # Allow dragging on the sidebar (x < 180) or top area (y < 45)
            if pos.x() < 180 or pos.y() < 45:
                child = self.childAt(pos)
                if child not in [self.btn_min, self.btn_close]:
                    self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                    event.accept()
                    return
        event.ignore()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # 1. Background
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 12, 12)
        painter.fillPath(path, QColor("#f8fafc"))
        
        # 2. Left gradient
        painter.setClipPath(path)
        left_rect = QRectF(0, 0, 180, self.height())
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0, QColor("#0f172a"))
        grad.setColorAt(0.5, QColor("#1e1b4b"))
        grad.setColorAt(1, QColor("#020617"))
        painter.fillRect(left_rect, grad)
        
        # Separator line
        painter.setPen(QPen(QColor(255, 255, 255, 10), 1))
        painter.drawLine(180, 0, 180, self.height())
        
        # Border
        painter.setClipping(False)
        painter.setPen(QPen(QColor(15, 23, 42, 20), 1.2))
        painter.drawRoundedRect(QRectF(0.6, 0.6, self.width() - 1.2, self.height() - 1.2), 12, 12)

    def _init_ui(self):
        # Main layout
        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # Left Panel (Sidebar)
        self.left_panel = QWidget(self)
        self.left_panel.setObjectName("LeftPanel")
        self.left_panel.setFixedWidth(180)
        self.left_layout = QVBoxLayout(self.left_panel)
        self.left_layout.setContentsMargins(15, 40, 15, 30)
        self.left_layout.setSpacing(12)
        
        self.logo_widget = IconWidget(FluentIcon.SPEED_HIGH, self.left_panel)
        self.logo_widget.setFixedSize(64, 64)
        self.logo_widget.setStyleSheet("QLabel { color: #60a5fa; }")
        
        self.lbl_left_title = QLabel(APP_NAME, self.left_panel)
        self.lbl_left_title.setObjectName("lbl_left_title")
        self.lbl_left_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.lbl_left_ver = QLabel(f"Version {VERSION}", self.left_panel)
        self.lbl_left_ver.setObjectName("lbl_left_ver")
        self.lbl_left_ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.left_layout.addWidget(self.logo_widget, 0, Qt.AlignmentFlag.AlignCenter)
        self.left_layout.addWidget(self.lbl_left_title, 0, Qt.AlignmentFlag.AlignCenter)
        self.left_layout.addWidget(self.lbl_left_ver, 0, Qt.AlignmentFlag.AlignCenter)
        self.left_layout.addStretch(1)
        
        self.lbl_left_pub = CaptionLabel(PUBLISHER, self.left_panel)
        self.lbl_left_pub.setObjectName("lbl_left_pub")
        self.left_layout.addWidget(self.lbl_left_pub, 0, Qt.AlignmentFlag.AlignCenter)

        # Right Panel (Content)
        self.right_panel = QWidget(self)
        self.right_panel.setObjectName("RightPanel")
        self.right_layout = QVBoxLayout(self.right_panel)
        self.right_layout.setContentsMargins(25, 20, 25, 20)
        self.right_layout.setSpacing(15)

        # Top Title Bar (Title + Min/Close buttons)
        self.title_bar_layout = QHBoxLayout()
        self.title_bar_layout.setContentsMargins(0, 0, 0, 0)
        
        self.page_title_label = SubtitleLabel("正在部署 AI 运行环境", self.right_panel)
        self.page_title_label.setStyleSheet("QLabel { font-size: 16px; font-weight: bold; color: #0078D4; }")
        self.title_bar_layout.addWidget(self.page_title_label)
        self.title_bar_layout.addStretch(1)
        
        self.btn_min = QPushButton(self.right_panel)
        self.btn_min.setIcon(FluentIcon.MINIMIZE.icon())
        self.btn_min.setFixedSize(32, 28)
        self.btn_min.setStyleSheet("QPushButton { border: none; background: transparent; } QPushButton:hover { background: rgba(0,0,0,0.06); }")
        self.btn_min.clicked.connect(self.showMinimized)
        
        self.btn_close = QPushButton(self.right_panel)
        self.btn_close.setIcon(FluentIcon.CLOSE.icon())
        self.btn_close.setFixedSize(32, 28)
        self.btn_close.setStyleSheet("QPushButton { border: none; background: transparent; } QPushButton:hover { background: #e81123; color: white; }")
        self.btn_close.clicked.connect(self.reject)
        
        self.title_bar_layout.addWidget(self.btn_min)
        self.title_bar_layout.addWidget(self.btn_close)
        self.right_layout.addLayout(self.title_bar_layout)

        # Content Card Page
        self.page_content = QWidget(self.right_panel)
        self.page_content.setObjectName("page_content")
        self.content_layout = QVBoxLayout(self.page_content)
        self.content_layout.setContentsMargins(15, 15, 15, 15)
        self.content_layout.setSpacing(20)

        # Description Label
        self.desc_label = BodyLabel("首次运行程序需要部署 AI 核心依赖包（如 PyTorch、ONNX Runtime 等），以支持自适应声线转换。部署过程为纯后台自动化，完成后将自动进入主界面。", self.page_content)
        self.desc_label.setWordWrap(True)
        self.content_layout.addWidget(self.desc_label)

        # Status Label
        self.status_label = BodyLabel("正在检测系统硬件环境...", self.page_content)
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("QLabel { font-size: 13px; color: #555555; }")
        self.content_layout.addWidget(self.status_label)

        # Progress Bar
        self.progress_bar = ProgressBar(self.page_content)
        self.progress_bar.setVal(0)
        self.content_layout.addWidget(self.progress_bar)
        
        self.content_layout.addStretch(1)
        self.right_layout.addWidget(self.page_content, 1)

        # Footer Layout with Exit Button
        self.footer_layout = QHBoxLayout()
        self.footer_layout.addStretch(1)
        self.btn_exit = QPushButton("退出", self.right_panel)
        self.btn_exit.setObjectName("btn_exit")
        self.btn_exit.setFixedWidth(100)
        self.btn_exit.setEnabled(False)
        self.btn_exit.clicked.connect(self.reject)
        self.footer_layout.addWidget(self.btn_exit)
        self.right_layout.addLayout(self.footer_layout)

        # Add Panels to Main Layout
        self.main_layout.addWidget(self.left_panel)
        self.main_layout.addWidget(self.right_panel)

    def _start_deployment(self):
        self.worker = DeploymentWorker(strategy="auto")
        self.worker.progress.connect(self._update_progress)
        self.worker.log.connect(self._append_log)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _update_progress(self, value, text):
        self.progress_bar.setValue(value)
        self.status_label.setText(text)

    def _append_log(self, text):
        self.log_history.append(text)

    def _on_finished(self, success):
        if success:
            self.accept()
        else:
            self.status_label.setText("部署失败！请检查您的网络连接并重新启动软件。")
            self.status_label.setStyleSheet("QLabel { font-size: 13px; color: #FF5555; font-weight: bold; }")
            self.progress_bar.setStyleSheet("QProgressBar::chunk { background-color: #FF5555; }")
            self.btn_exit.setEnabled(True)
            self.btn_exit.setText("关闭")
            
            # Show standard Fluent InfoBar for notification
            log_str = "\n".join(self.log_history[-10:])
            InfoBar.error(
                "部署错误", 
                f"运行环境部署失败。最近错误日志：\n{log_str}", 
                duration=15000, 
                parent=self
            )
