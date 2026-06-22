# -*- coding: utf-8 -*-
import math
from PySide6.QtGui import QPainter, QPainterPath, QColor, QLinearGradient, QPen, QFont
from PySide6.QtCore import Qt, QPointF
from qfluentwidgets import ToggleButton, isDarkTheme, qconfig

class CoreButton(ToggleButton):
    """
    Representing an individual CPU logical thread core button with premium wave design.
    """
    def __init__(self, core_data: dict, parent=None):
        self.core_id = core_data['core_id']
        self.core_type = core_data['type']
        self.threads = core_data['threads']
        self.is_primary = False
        self._is_style_ready = True
        
        # Wave states
        self.usage = 0.0
        self.current_level = 0.0
        self.phase = 0.0
        
        super().__init__(parent=parent)
        self.setObjectName("CoreButton")
        
        prefix = "P" if self.core_type == "P-Core" else "E"
        self.setText(f"{prefix}-{self.core_id}")
        self.setFixedSize(90, 50)
        self.setChecked(self.core_type == "P-Core")
        
        # Completely disable default stylesheet background/border handling to allow paintEvent drawing
        super().setStyleSheet("background: transparent; border: none;")
        
        self.update_style()
        qconfig.themeChanged.connect(self.update_style)
        
    def destroy(self, destroyWindow=True, destroySubWindows=True):
        try:
            qconfig.themeChanged.disconnect(self.update_style)
        except Exception:
            pass
        super().destroy(destroyWindow, destroySubWindows)
        
    def set_primary_visual(self, is_primary: bool):
        self.is_primary = is_primary
        self.update_style()
        
    def setStyleSheet(self, styleSheet: str):
        # Override to prevent QFluentWidgets from setting default button styles
        pass
        
    def update_style(self):
        self.update()
        
    def tick_wave(self):
        # Smoothly animate water level towards actual CPU usage
        diff = self.usage - self.current_level
        if abs(diff) > 0.05:
            self.current_level += diff * 0.15
        else:
            self.current_level = self.usage
            
        # Move wave phase
        self.phase = (self.phase + 0.08) % (2 * math.pi)
        
        # Only repaint if the water level is rising/falling, or if we have an active wave (>0%)
        if self.current_level > 0.05 or self.usage > 0.05:
            self.update()

    def paintEvent(self, event):
        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            return
            
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        is_dark = isDarkTheme()
        is_checked = self.isChecked()
        is_hovered = self.underMouse()
        
        # 1. Base background rounded path
        path_bg = QPainterPath()
        path_bg.addRoundedRect(0, 0, w, h, 8, 8)
        
        # Base background fill
        if is_dark:
            bg_color = QColor(255, 255, 255, 10)
        else:
            bg_color = QColor(0, 0, 0, 8)
        painter.fillPath(path_bg, bg_color)
        
        # 2. Draw Waves if water level is above zero
        if self.current_level > 0.05:
            painter.save()
            painter.setClipPath(path_bg)
            
            k = h - (self.current_level / 100.0) * h
            # Amplitude shrinks to 0 at extreme empty/full to look realistic
            amplitude = 3.0 * math.sin(self.current_level / 100.0 * math.pi)
            if amplitude < 0.3 and 0.05 < self.current_level < 99.95:
                amplitude = 0.3
                
            # Wave colors based on checked/active status & core type
            if not is_checked:
                # Dim gray/desaturated waves for disabled cores
                c1 = QColor(128, 128, 128, 40)
                c2 = QColor(100, 100, 100, 60)
                back_c = QColor(128, 128, 128, 20)
            else:
                if self.is_primary:
                    c1 = QColor(255, 185, 0, 65)
                    c2 = QColor(216, 148, 0, 115)
                    back_c = QColor(255, 215, 0, 30)
                elif self.core_type == "P-Core":
                    c1 = QColor(0, 242, 254, 75)
                    c2 = QColor(0, 120, 212, 125)
                    back_c = QColor(0, 180, 255, 35)
                else: # E-Core
                    c1 = QColor(45, 212, 191, 75)
                    c2 = QColor(13, 148, 136, 125)
                    back_c = QColor(94, 234, 212, 35)
            
            # Back Wave (Phase shifted by pi, frequency is slightly different, 4px step size)
            path_back = QPainterPath()
            path_back.moveTo(0, h)
            omega = 2.0 * math.pi / w
            for x in range(0, w + 1, 4):
                y = amplitude * 0.75 * math.sin(omega * x + self.phase + math.pi) + k
                path_back.lineTo(x, y)
            path_back.lineTo(w, h)
            path_back.closeSubpath()
            painter.fillPath(path_back, back_c)
            
            # Front Wave (4px step size)
            path_front = QPainterPath()
            path_front.moveTo(0, h)
            for x in range(0, w + 1, 4):
                y = amplitude * math.sin(omega * x + self.phase) + k
                path_front.lineTo(x, y)
            path_front.lineTo(w, h)
            path_front.closeSubpath()
            
            # Linear gradient for dynamic fluid feel
            grad = QLinearGradient(0, k - amplitude, 0, h)
            grad.setColorAt(0, c1)
            grad.setColorAt(1, c2)
            painter.fillPath(path_front, grad)
            
            painter.restore()
            
        # 3. Draw border
        if is_checked:
            if self.is_primary:
                border_color = QColor(255, 185, 0) if not is_hovered else QColor(255, 215, 0)
            elif self.core_type == "P-Core":
                border_color = QColor(0, 242, 254) if (is_dark or is_hovered) else QColor(0, 120, 212)
            else:
                border_color = QColor(45, 212, 191) if (is_dark or is_hovered) else QColor(13, 148, 136)
            border_width = 2
        else:
            # Unchecked
            if is_hovered:
                if self.is_primary:
                    border_color = QColor(255, 185, 0, 150)
                elif self.core_type == "P-Core":
                    border_color = QColor(0, 242, 254, 150) if is_dark else QColor(0, 120, 212, 150)
                else:
                    border_color = QColor(45, 212, 191, 150) if is_dark else QColor(13, 148, 136, 150)
                border_width = 1.5
            else:
                border_color = QColor(255, 255, 255, 30) if is_dark else QColor(0, 0, 0, 30)
                border_width = 1
                
        painter.save()
        pen = QPen(border_color, border_width)
        painter.setPen(pen)
        offset = border_width / 2.0
        path_border = QPainterPath()
        path_border.addRoundedRect(offset, offset, w - border_width, h - border_width, 8, 8)
        painter.drawPath(path_border)
        painter.restore()
        
        # 4. Text (Core ID & Usage Percentage)
        painter.save()
        
        # Configure fonts
        font_id = QFont("Segoe UI", 9, QFont.Weight.Bold)
        font_val = QFont("Segoe UI", 8, QFont.Weight.DemiBold)
        
        # Primary label text
        text_color = QColor(255, 255, 255, 225) if is_dark else QColor(0, 0, 0, 210)
        painter.setFont(font_id)
        painter.setPen(text_color)
        
        prefix = "P" if self.core_type == "P-Core" else "E"
        core_text = f"{prefix}-{self.core_id}"
        painter.drawText(0, 0, w, int(h * 0.58), Qt.AlignmentFlag.AlignCenter, core_text)
        
        # Percentage value label
        if is_checked:
            if self.is_primary:
                val_color = QColor(255, 195, 20)
            elif self.core_type == "P-Core":
                val_color = QColor(0, 242, 254) if is_dark else QColor(0, 120, 212)
            else:
                val_color = QColor(45, 212, 191) if is_dark else QColor(13, 148, 136)
        else:
            val_color = QColor(255, 255, 255, 140) if is_dark else QColor(0, 0, 0, 130)
            
        usage_text = f"{int(round(self.usage))}%"
        painter.setFont(font_val)
        painter.setPen(val_color)
        painter.drawText(0, int(h * 0.44), w, int(h * 0.52), Qt.AlignmentFlag.AlignCenter, usage_text)
        
        painter.restore()
