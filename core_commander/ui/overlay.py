# -*- coding: utf-8 -*-
from PySide6.QtCore import Qt, QPoint, QRectF, QSize
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont, QPolygonF
from core_commander.config.settings import AppSettings
from core_commander.utils.logger import logger

class FpsHistoryGraph(QWidget):
    """
    Custom widget that paints a real-time FPS history graph with dynamic Y-axis scaling
    and reference gridlines. Includes a secondary curve for 1% Low FPS.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.fps_history = []
        self.low_history = []
        self.setMinimumHeight(45)
        self.setMaximumHeight(80)

    def update_data(self, fps_history: list, low_history: list = None):
        self.fps_history = fps_history
        self.low_history = low_history if low_history is not None else []
        self.update()

    def paintEvent(self, event):
        parent_locked = getattr(self.parent(), "locked", False)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        if not self.fps_history:
            if not parent_locked:
                painter.fillRect(0, 0, w, h, QColor(0, 0, 0, 40))
                painter.setPen(QPen(QColor(255, 255, 255, 30), 1))
                painter.drawRect(0, 0, w - 1, h - 1)
            return

        # Dynamic Y scaling limit (minimum 60 FPS upper limit) based on both curves
        all_vals = self.fps_history + self.low_history
        max_fps = float(max(60, max(all_vals) if all_vals else 60))

        if not parent_locked:
            # Draw background grid
            painter.fillRect(0, 0, w, h, QColor(0, 0, 0, 40))
            
            # Draw border
            painter.setPen(QPen(QColor(255, 255, 255, 30), 1))
            painter.drawRect(0, 0, w - 1, h - 1)

            # Determine horizontal grid reference lines based on max_fps
            grid_lines = [30, 60]
            if max_fps > 120:
                grid_lines.append(120)
            if max_fps > 240:
                grid_lines.append(240)

            # Draw grid lines
            painter.setPen(QPen(QColor(255, 255, 255, 20), 1, Qt.PenStyle.DashLine))
            for line in grid_lines:
                y = h - int((line / max_fps) * h)
                if 0 < y < h:
                    painter.drawLine(0, y, w, y)

        # Map data points for main FPS curve to coordinates
        points = []
        count = len(self.fps_history)
        dx = w / max(1, count - 1)

        for i, val in enumerate(self.fps_history):
            val = max(0.0, min(max_fps, float(val)))
            # Y coordinate (0 is at top, so invert)
            y = h - int((val / max_fps) * h)
            y = max(1, min(h - 2, y))
            x = int(i * dx)
            points.append(QPoint(x, y))

        # Map data points for 1% Low FPS curve to coordinates
        low_points = []
        low_count = len(self.low_history)
        low_dx = w / max(1, low_count - 1)

        for i, val in enumerate(self.low_history):
            val = max(0.0, min(max_fps, float(val)))
            y = h - int((val / max_fps) * h)
            y = max(1, min(h - 2, y))
            x = int(i * low_dx)
            low_points.append(QPoint(x, y))

        # Paint filled area below the main FPS curve
        if len(points) >= 2:
            poly = QPolygonF()
            poly.append(QPoint(0, h - 1))
            for p in points:
                poly.append(p)
            poly.append(QPoint(points[-1].x(), h - 1))

            # Gradient fill (Orange theme)
            gradient_brush = QBrush(QColor(255, 140, 0, 40)) # Orange semi-transparent
            painter.setBrush(gradient_brush)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPolygon(poly)

            # Paint the main FPS curve line (bright orange)
            pen = QPen(QColor(255, 140, 0, 200), 1.5)
            painter.setPen(pen)
            for i in range(len(points) - 1):
                painter.drawLine(points[i], points[i+1])

        # Paint the 1% Low FPS curve line (complementary bright cyan/teal, no fill)
        if len(low_points) >= 2:
            low_pen = QPen(QColor(0, 191, 255, 200), 1.5)
            painter.setPen(low_pen)
            for i in range(len(low_points) - 1):
                painter.drawLine(low_points[i], low_points[i+1])


class GameOverlay(QWidget):
    """
    OSD Overlay window styled with high contrast white text and bright orange highlights.
    Can be locked (click-through, borderless, zero background) or unlocked (draggable, bordered).
    """
    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(None)
        self.main_win = parent
        self.settings = settings
        self.drag_position = QPoint()
        self.locked = settings.fps_overlay_lock

        # Window Flags configuration
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.SubWindow |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.update_input_transparency()

        # Position setting
        x = self.settings.fps_overlay_pos_x
        y = self.settings.fps_overlay_pos_y
        self.move(x, y)
        self.setMinimumWidth(350)

        # Initialize layout
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(15, 12, 15, 12)
        self.layout.setSpacing(6)

        # Header Game Name Label (Pure high contrast white text, orange indicator)
        self.lbl_game_title = QLabel("<font color='#ff8c00'>●</font> PERFORMANCE")
        self.lbl_game_title.setStyleSheet("font-weight: bold; text-transform: uppercase; color: #ffffff;")
        
        # Grid layout for metrics
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(15)
        self.grid.setVerticalSpacing(4)

        # Labels definitions (high contrast white/orange)
        self.lbl_fps_val = QLabel("0")
        self.lbl_fps_unit = QLabel("FPS")
        self.lbl_low_avg = QLabel("<font color='#ffffff'>1% LOW:</font> <font color='#ff8c00'>0</font> <font color='#ff8c00'>|</font> <font color='#ffffff'>AVG:</font> <font color='#ff8c00'>0</font>")
        self.lbl_cpu_gpu = QLabel("<font color='#ffffff'>CPU:</font> <font color='#ff8c00'>0% 0.00GHz</font> <font color='#ff8c00'>|</font> <font color='#ffffff'>GPU:</font> <font color='#ff8c00'>0%</font>")
        self.lbl_ram_vram = QLabel("<font color='#ffffff'>RAM:</font> <font color='#ff8c00'>0.0G/16.0G</font> <font color='#ff8c00'>|</font> <font color='#ffffff'>VRAM:</font> <font color='#ff8c00'>0.0G/8.0G</font>")
        self.lbl_ram_vram.setWordWrap(False)
        self.lbl_frametime_text = QLabel("<font color='#ffffff'>FT:</font> <font color='#ff8c00'>0.0 ms</font>")

        # Styling (Text color now managed by HTML, font weight and base styles here)
        self.lbl_fps_val.setStyleSheet("color: #ff8c00; font-weight: 900;")
        self.lbl_fps_unit.setStyleSheet("color: #ffffff; font-weight: bold;")
        self.lbl_low_avg.setStyleSheet("background: transparent;")
        self.lbl_cpu_gpu.setStyleSheet("background: transparent;")
        self.lbl_ram_vram.setStyleSheet("background: transparent;")
        self.lbl_frametime_text.setStyleSheet("background: transparent;")

        # Custom Graph
        self.graph = FpsHistoryGraph(self)

        # Add components to Layout
        self.layout.addWidget(self.lbl_game_title)
        
        # Assemble FPS row
        fps_layout = QHBoxLayout()
        fps_layout.addWidget(self.lbl_fps_val)
        fps_layout.addWidget(self.lbl_fps_unit)
        fps_layout.addStretch()
        fps_layout.addWidget(self.lbl_frametime_text)
        self.layout.addLayout(fps_layout)

        self.layout.addWidget(self.lbl_low_avg)
        self.layout.addWidget(self.lbl_cpu_gpu)
        self.layout.addWidget(self.lbl_ram_vram)
        self.layout.addWidget(self.graph)

        self.apply_theme_settings()

    def update_input_transparency(self):
        """
        Dynamically applies or removes click-through (transparent for input) window property.
        """
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, self.locked)

    def apply_theme_settings(self):
        """
        Applies font sizes, visibilities, and opacity from current AppSettings.
        """
        font_size = self.settings.fps_overlay_font_size
        self.setWindowOpacity(1.0)

        # Apply fonts
        font_name = "Consolas" # Monospace font prevents jittering numbers
        
        font_title = QFont("Segoe UI", font_size - 3, QFont.Weight.Bold)
        self.lbl_game_title.setFont(font_title)
        
        font_fps = QFont(font_name, font_size + 10, QFont.Weight.Black)
        self.lbl_fps_val.setFont(font_fps)
        
        font_fps_unit = QFont("Segoe UI", font_size - 1, QFont.Weight.Bold)
        self.lbl_fps_unit.setFont(font_fps_unit)
        
        font_ft = QFont(font_name, font_size - 1, QFont.Weight.Bold)
        self.lbl_frametime_text.setFont(font_ft)
        
        font_small = QFont(font_name, font_size - 1)
        self.lbl_low_avg.setFont(font_small)
        self.lbl_cpu_gpu.setFont(font_small)
        self.lbl_ram_vram.setFont(font_small)

        # Adjust visibilities
        self.lbl_cpu_gpu.setVisible(self.settings.fps_overlay_show_cpu_gpu)
        self.lbl_ram_vram.setVisible(self.settings.fps_overlay_show_ram)
        self.graph.setVisible(self.settings.fps_overlay_show_frametime)

        # Re-layout and size adjustment
        base_width = 360 + (font_size - 12) * 18
        self.setFixedWidth(base_width)
        self.adjustSize()

    def update_stats(self, data: dict):
        """
        Update the labels and graphs with fresh telemetry stats.
        """
        should_show = data.get("should_show", False)

        if not should_show:
            if self.isVisible():
                self.hide()
            return
        else:
            if not self.isVisible() and self.settings.enable_fps_overlay:
                self.show()

        self.lbl_fps_val.setText(str(data.get("fps", 0)))
        
        low_fps = data.get('one_percent_low', 0)
        avg_fps = data.get('avg_fps', 0)
        self.lbl_low_avg.setText(f"<font color='#ffffff'>1% LOW:</font> <font color='#ff8c00'>{low_fps}</font> <font color='#ff8c00'>|</font> <font color='#ffffff'>AVG:</font> <font color='#ff8c00'>{avg_fps}</font>")
        
        cpu_util = data.get('cpu_util', 0)
        cpu_freq = data.get('cpu_freq', 0.0)
        gpu_util = data.get('gpu_util', 0)
        self.lbl_cpu_gpu.setText(f"<font color='#ffffff'>CPU:</font> <font color='#ff8c00'>{cpu_util}% {cpu_freq:.2f}GHz</font> <font color='#ff8c00'>|</font> <font color='#ffffff'>GPU:</font> <font color='#ff8c00'>{gpu_util}%</font>")
        
        # RAM/VRAM formatting
        ram_u = data.get('ram_used_gb', 0.0)
        ram_t = data.get('ram_total_gb', 16.0)
        vram_u = data.get('vram_used_gb', 0.0)
        vram_t = data.get('vram_total_gb', 8.0)
        self.lbl_ram_vram.setText(f"<font color='#ffffff'>RAM:</font> <font color='#ff8c00'>{ram_u}G/{ram_t}G</font> <font color='#ff8c00'>|</font> <font color='#ffffff'>VRAM:</font> <font color='#ff8c00'>{vram_u}G/{vram_t}G</font>")
        
        frametime = data.get('frametime', 0.0)
        self.lbl_frametime_text.setText(f"<font color='#ffffff'>FT:</font> <font color='#ff8c00'>{frametime} ms</font>")
        
        # Update graph data points with both main FPS and 1% Low FPS history
        fps_history = data.get("fps_history", [])
        low_history = data.get("low_history", [])
        self.graph.update_data(fps_history, low_history)

    def set_locked(self, locked: bool):
        """
        Sets locked mode.
        """
        self.locked = locked
        self.update_input_transparency()
        self.update() # Force border redraw

    def paintEvent(self, event):
        if self.locked:
            return  # No background panel or border when locked!
            
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Semi-transparent dark background panel (only when unlocked for layout adjustment)
        opacity_val = int(self.settings.fps_overlay_opacity / 100.0 * 255)
        bg_color = QColor(10, 10, 10, opacity_val)
        painter.setBrush(QBrush(bg_color))

        # Edit Mode: Draw distinct highlighted boundary with orange dashed border
        pen = QPen(QColor(255, 140, 0, 225), 1.5, Qt.PenStyle.DashLine)
        painter.setPen(pen)

        rect = QRectF(0, 0, self.width(), self.height())
        painter.drawRoundedRect(rect, 8, 8)

    # --- Mouse Drag-Position Events for Edit Mode ---
    def mousePressEvent(self, event):
        if not self.locked and event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if not self.locked and event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def mouseReleaseEvent(self, event):
        if not self.locked and event.button() == Qt.MouseButton.LeftButton:
            # Persist positions
            self.settings.fps_overlay_pos_x = self.x()
            self.settings.fps_overlay_pos_y = self.y()
            
            # Update settings page spinboxes if available
            main_win = self.main_win
            if main_win and hasattr(main_win, "gpu_page"):
                gpu_page = main_win.gpu_page
                if hasattr(gpu_page, "spin_osd_x") and hasattr(gpu_page, "spin_osd_y"):
                    # Block signals to prevent triggering save_settings loop recursively
                    gpu_page.spin_osd_x.blockSignals(True)
                    gpu_page.spin_osd_y.blockSignals(True)
                    gpu_page.spin_osd_x.setValue(self.x())
                    gpu_page.spin_osd_y.setValue(self.y())
                    gpu_page.spin_osd_x.blockSignals(False)
                    gpu_page.spin_osd_y.blockSignals(False)
            
            event.accept()
            logger.info(f"OSD position updated to X: {self.x()}, Y: {self.y()}")
