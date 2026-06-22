# -*- coding: utf-8 -*-
import os
import shutil
from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QFileDialog
from qfluentwidgets import (
    ScrollArea, TitleLabel, SubtitleLabel, 
    FluentIcon, InfoBar, PushButton, SettingCard, Slider, BodyLabel, ComboBox, SwitchButton
)
from core_commander.config.settings import AppSettings
from core_commander.utils.logger import logger

class CustomSwitchSettingCard(SettingCard):
    def __init__(self, icon, title, content, parent=None):
        super().__init__(icon, title, content, parent)
        self.switchButton = SwitchButton(parent=self)
        self.hBoxLayout.addStretch(1)
        self.hBoxLayout.addWidget(self.switchButton, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)
        
    def setChecked(self, isChecked):
        self.switchButton.setChecked(isChecked)
        
    @property
    def checkedChanged(self):
        return self.switchButton.checkedChanged

class CustomActionSettingCard(SettingCard):
    def __init__(self, icon, title, content, parent=None):
        super().__init__(icon, title, content, parent)
        self.buttonLayout = QHBoxLayout()
        self.hBoxLayout.addStretch(1)
        self.hBoxLayout.addLayout(self.buttonLayout)
        self.hBoxLayout.addSpacing(16)

class CustomComboBoxSettingCard(SettingCard):
    def __init__(self, icon, title, content, texts, parent=None):
        super().__init__(icon, title, content, parent)
        self.comboBox = ComboBox(self)
        self.comboBox.addItems(texts)
        self.hBoxLayout.addStretch(1)
        self.hBoxLayout.addWidget(self.comboBox, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

class CustomSliderSettingCard(SettingCard):
    def __init__(self, icon, title, content, parent=None):
        super().__init__(icon, title, content, parent)
        self.slider = Slider(Qt.Orientation.Horizontal, self)
        self.val_label = BodyLabel("0", self)
        
        self.hBoxLayout.addStretch(1)
        self.hBoxLayout.addWidget(self.slider, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)
        self.hBoxLayout.addWidget(self.val_label, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)
        
        self.slider.setFixedWidth(150)
        self.slider.valueChanged.connect(lambda val: self.val_label.setText(str(val)))

class CrosshairPage(ScrollArea):
    """
    Settings page for the Crosshair overlay feature.
    """
    def __init__(self, main_win):
        super().__init__()
        self.main_win = main_win
        self.settings = main_win.settings

        self.view = QWidget(self)
        self.layout = QVBoxLayout(self.view)
        
        self.layout.setContentsMargins(30, 20, 30, 20)
        self.layout.setSpacing(15)
        
        # Title
        self.title_label = TitleLabel("屏幕准星", self.view)
        self.layout.addWidget(self.title_label)
        
        # Toggle
        self.card_enable = CustomSwitchSettingCard(
            icon=FluentIcon.GAME,
            title="启用准星",
            content="在屏幕中央显示一个固定的准星，提升FPS游戏瞄准体验（无边框，鼠标穿透）",
            parent=self.view
        )
        self.card_enable.setChecked(self.settings.enable_crosshair)
        self.card_enable.checkedChanged.connect(self.on_enable_changed)
        self.layout.addWidget(self.card_enable)
        
        # Style
        self.card_style = CustomComboBoxSettingCard(
            icon=FluentIcon.PALETTE,
            title="准星样式",
            content="选择准星的形状，或者使用自定义图片",
            texts=["十字 (Cross)", "圆点 (Dot)", "圆圈 (Circle)", "自定义图片 (Custom)"],
            parent=self.view
        )
        
        style_map = {"cross": 0, "dot": 1, "circle": 2, "custom": 3}
        self.card_style.comboBox.setCurrentIndex(style_map.get(self.settings.crosshair_style, 0))
        self.card_style.comboBox.currentIndexChanged.connect(self.on_style_changed)
        self.layout.addWidget(self.card_style)
        
        # Color (Simple combobox for now, qfluentwidgets color picker is more complex)
        self.card_color = CustomComboBoxSettingCard(
            icon=FluentIcon.BRUSH,
            title="准星颜色",
            content="选择预设的准星颜色（自定义图片模式下无效）",
            texts=["绿色 (Green)", "红色 (Red)", "蓝色 (Blue)", "黄色 (Yellow)", "白色 (White)", "青色 (Cyan)", "品红 (Magenta)", "黑色 (Black)"],
            parent=self.view
        )
        
        self.color_map = {
            "#00FF00": 0, "#FF0000": 1, "#0000FF": 2, "#FFFF00": 3,
            "#FFFFFF": 4, "#00FFFF": 5, "#FF00FF": 6, "#000000": 7
        }
        self.color_map_inv = {v: k for k, v in self.color_map.items()}
        current_color = self.settings.crosshair_color
        self.card_color.comboBox.setCurrentIndex(self.color_map.get(current_color, 0))
        self.card_color.comboBox.currentIndexChanged.connect(self.on_color_changed)
        self.layout.addWidget(self.card_color)
        
        # Size
        self.card_size = CustomSliderSettingCard(
            icon=FluentIcon.ZOOM_IN,
            title="准星大小",
            content="调整准星的整体尺寸或图片大小",
            parent=self.view
        )
        self.card_size.slider.setRange(2, 300)
        self.card_size.slider.setValue(self.settings.crosshair_size)
        self.card_size.slider.valueChanged.connect(self.on_size_changed)
        self.layout.addWidget(self.card_size)
        
        # Thickness
        self.card_thickness = CustomSliderSettingCard(
            icon=FluentIcon.EDIT,
            title="线条粗细",
            content="调整准星线条的粗细（自定义图片模式下无效）",
            parent=self.view
        )
        self.card_thickness.slider.setRange(1, 10)
        self.card_thickness.slider.setValue(self.settings.crosshair_thickness)
        self.card_thickness.slider.valueChanged.connect(self.on_thickness_changed)
        self.layout.addWidget(self.card_thickness)
        
        # Opacity
        self.card_opacity = CustomSliderSettingCard(
            icon=FluentIcon.TRANSPARENT,
            title="不透明度",
            content="调整准星的整体不透明度 (10% - 100%)",
            parent=self.view
        )
        self.card_opacity.slider.setRange(10, 100)
        self.card_opacity.slider.setValue(self.settings.crosshair_opacity)
        self.card_opacity.slider.valueChanged.connect(self.on_opacity_changed)
        self.layout.addWidget(self.card_opacity)
        
        # Custom image upload
        self.card_upload = CustomActionSettingCard(
            title="上传自定义准星",
            content="选择本地的 PNG 或 JPG 图片作为准星。推荐使用透明背景的 PNG。",
            icon=FluentIcon.FOLDER,
            parent=self.view
        )
        self.btn_upload = PushButton("选择文件", self.card_upload)
        self.btn_upload.clicked.connect(self.on_upload_clicked)
        self.card_upload.buttonLayout.addWidget(self.btn_upload)
        self.layout.addWidget(self.card_upload)
        
        self.layout.addStretch(1)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setObjectName("CrosshairPageView")
        self.setStyleSheet("QWidget#CrosshairPageView { background: transparent; }")

    def on_enable_changed(self, is_checked: bool):
        self.settings.enable_crosshair = is_checked
        self.notify_overlay()

    def on_style_changed(self, index: int):
        styles = {0: "cross", 1: "dot", 2: "circle", 3: "custom"}
        self.settings.crosshair_style = styles.get(index, "cross")
        self.notify_overlay()

    def on_color_changed(self, index: int):
        self.settings.crosshair_color = self.color_map_inv.get(index, "#00FF00")
        self.notify_overlay()

    def on_size_changed(self, val: int):
        self.settings.crosshair_size = val
        self.notify_overlay()
        
    def on_thickness_changed(self, val: int):
        self.settings.crosshair_thickness = val
        self.notify_overlay()

    def on_opacity_changed(self, val: int):
        self.settings.crosshair_opacity = val
        self.notify_overlay()

    def on_upload_clicked(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择准星图片",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp)"
        )
        if file_path:
            try:
                import sys
                
                # Determine app root dir appropriately whether frozen or not
                if getattr(sys, 'frozen', False):
                    # For Pyinstaller standard extraction path: sys._MEIPASS
                    app_root = sys._MEIPASS
                else:
                    app_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

                # In case we need to save it to an accessible path (not temporary _MEIPASS)
                # It's better to save to the executable directory or AppData to persist
                base_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else app_root
                
                target_dir = os.path.join(base_dir, "assets", "crosshairs")
                os.makedirs(target_dir, exist_ok=True)
                
                filename = os.path.basename(file_path)
                target_path = os.path.join(target_dir, filename)
                
                shutil.copy2(file_path, target_path)
                self.settings.crosshair_custom_path = target_path
                self.settings.crosshair_style = "custom"
                self.card_style.comboBox.setCurrentIndex(3)
                self.notify_overlay()
                
                InfoBar.success("成功", f"准星图片已加载: {filename}", parent=self)
            except Exception as e:
                logger.error(f"Error copying crosshair: {e}")
                InfoBar.error("错误", f"加载图片失败: {str(e)}", parent=self)

    def notify_overlay(self):
        """Notify the main window to refresh the crosshair overlay."""
        if hasattr(self.main_win, 'crosshair_overlay'):
            if self.main_win.crosshair_overlay:
                self.main_win.crosshair_overlay.refresh()

    def retranslate_ui(self):
        # Could add multi-language support here
        pass
