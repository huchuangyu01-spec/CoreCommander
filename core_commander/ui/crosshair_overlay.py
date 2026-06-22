# -*- coding: utf-8 -*-
import os
import sys
from PySide6.QtCore import Qt, QPoint, QRectF
from PySide6.QtWidgets import QWidget, QApplication
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QPixmap
from core_commander.config.settings import AppSettings
from core_commander.utils.logger import logger

class CrosshairOverlay(QWidget):
    """
    Always-on-top transparent overlay for rendering the crosshair.
    Locks to the exact center of the screen.
    """
    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.settings = settings

        # Window Flags configuration for click-through and always on top
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowDoesNotAcceptFocus |
            Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self.custom_pixmap = None
        self.scaled_pixmap = None
        self.update_geometry()

    def update_geometry(self):
        """Center the widget on the primary screen."""
        screen = QApplication.primaryScreen().geometry()
        
        # Max reasonable size for a crosshair
        size = 300 
        self.setFixedSize(size, size)
        
        # Exact center of screen
        x = (screen.width() - size) // 2
        y = (screen.height() - size) // 2
        self.move(x, y)

    def load_custom_image(self):
        """Load the custom crosshair image if specified."""
        path = self.settings.crosshair_custom_path
        if path and os.path.exists(path):
            try:
                pixmap = QPixmap(path)
                if not pixmap.isNull():
                    self.custom_pixmap = pixmap
                    self.update_scaled_pixmap()
                    return
            except Exception as e:
                logger.error(f"Failed to load custom crosshair: {e}")
        self.custom_pixmap = None
        self.scaled_pixmap = None

    def update_scaled_pixmap(self):
        """Pre-scale and cache the custom crosshair pixmap to avoid high-frequency paint scaling overhead."""
        if self.custom_pixmap and not self.custom_pixmap.isNull():
            size = self.settings.crosshair_size
            self.scaled_pixmap = self.custom_pixmap.scaled(
                size, size, 
                Qt.AspectRatioMode.KeepAspectRatio, 
                Qt.TransformationMode.SmoothTransformation
            )
        else:
            self.scaled_pixmap = None

    def refresh(self):
        """Called to trigger a repaint when settings change."""
        if not self.settings.enable_crosshair:
            self.hide()
            return
            
        self.update_geometry()
        
        if self.settings.crosshair_style == "custom":
            if not self.custom_pixmap:
                self.load_custom_image()
            else:
                self.update_scaled_pixmap()
        else:
            self.scaled_pixmap = None
            
        self.show()
        self.update()

    def paintEvent(self, event):
        if not self.settings.enable_crosshair:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        style = self.settings.crosshair_style
        size = self.settings.crosshair_size
        thickness = self.settings.crosshair_thickness
        opacity = self.settings.crosshair_opacity / 100.0
        
        color_str = self.settings.crosshair_color
        try:
            color = QColor(color_str)
        except Exception:
            color = QColor("#00FF00")
            
        color.setAlphaF(opacity)
        
        cx = self.width() / 2
        cy = self.height() / 2
        
        if style == "dot":
            painter.setBrush(QBrush(color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPoint(int(cx), int(cy)), size // 2, size // 2)
            
        elif style == "cross":
            pen = QPen(color, thickness, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawLine(int(cx - size // 2), int(cy), int(cx + size // 2), int(cy))
            painter.drawLine(int(cx), int(cy - size // 2), int(cx), int(cy + size // 2))
            
        elif style == "circle":
            pen = QPen(color, thickness, Qt.PenStyle.SolidLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPoint(int(cx), int(cy)), size // 2, size // 2)
            # small center dot
            painter.setBrush(QBrush(color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPoint(int(cx), int(cy)), thickness, thickness)
            
        elif style == "custom":
            if self.scaled_pixmap and not self.scaled_pixmap.isNull():
                px_w = self.scaled_pixmap.width()
                px_h = self.scaled_pixmap.height()
                
                # Apply opacity via painter
                painter.setOpacity(opacity)
                painter.drawPixmap(int(cx - px_w // 2), int(cy - px_h // 2), self.scaled_pixmap)
