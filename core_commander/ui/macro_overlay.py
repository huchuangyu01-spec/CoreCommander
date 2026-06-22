# -*- coding: utf-8 -*-
import math
import time
from PySide6.QtCore import Qt, QPoint, QRectF, QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont
from core_commander.config.settings import AppSettings
from core_commander.core.macro_manager import MacroManager
from core_commander.ui.timeline_widget import TimelineWidget
from core_commander.utils.logger import logger

class MacroOverlay(QWidget):
    """
    Floating HUD overlay on top of screen.
    Displays a 1:1 cloned copy of the main timeline editor widget
    along with transparent status text in the header.
    """
    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(None)
        self.settings = settings
        self.macro_manager = MacroManager()
        self.drag_position = QPoint()
        self.locked = settings.get_bool("macro_hud_locked", False)
        self.main_win = parent
        
        # Timing animation state
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self.update)
        
        # Window configuration
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.SubWindow |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        
        # Load and set position
        x = self.settings.get_int("macro_hud_x", 400)
        y = self.settings.get_int("macro_hud_y", 15)
        self.move(x, y)
        self.setFixedSize(650, 310) # Large size to hold the full 1:1 timeline widget
        
        # Main Layout
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(12, 10, 12, 12)
        self.main_layout.setSpacing(6)
        
        # Header Row Layout
        self.header_layout = QHBoxLayout()
        self.header_layout.setContentsMargins(0, 0, 0, 0)
        self.header_layout.setSpacing(8)
        
        # Spacer for the status breathing dot
        self.header_layout.addSpacing(14)
        
        # Header labels
        self.lbl_profile = QLabel("No Profile Loaded")
        self.lbl_status = QLabel("HUD READY")
        
        self.lbl_profile.setStyleSheet("color: #ffffff; font-weight: bold; font-family: 'Segoe UI'; font-size: 11px;")
        self.lbl_status.setStyleSheet("color: #00e676; font-family: 'Segoe UI'; font-size: 9px; font-weight: bold; text-transform: uppercase;")
        
        self.header_layout.addWidget(self.lbl_profile)
        self.header_layout.addStretch()
        self.header_layout.addWidget(self.lbl_status)
        
        self.main_layout.addLayout(self.header_layout)
        
        # 1:1 Timeline Widget Instance
        self.timeline = TimelineWidget(self)
        self.timeline.setFixedHeight(250)
        self.main_layout.addWidget(self.timeline)
        
        # Synchronize edits made in HUD overlay to profile and update main window macro page
        self.timeline.blocksChanged.connect(self.on_overlay_blocks_changed)
        
        self.playback_progress_ms = 0
        
        # Update transparency
        self.update_input_transparency()
        
        # Connect to macro manager state changes & progress
        self.macro_manager.state_changed.connect(self.on_state_changed)
        self.macro_manager.playback_progress.connect(self.on_playback_progress)
        
        # Initial refresh
        self.refresh_ui()

    def update_input_transparency(self):
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, self.locked)
        try:
            import ctypes
            GWL_EXSTYLE = -20
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_LAYERED = 0x00080000
            WS_EX_NOACTIVATE = 0x08000000
            
            hwnd = int(self.winId())
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if self.locked:
                new_style = style | WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_NOACTIVATE
            else:
                new_style = (style & ~WS_EX_TRANSPARENT) & ~WS_EX_NOACTIVATE
            
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
            
            # Force style changes to take effect immediately
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            SWP_NOZORDER = 0x0004
            SWP_FRAMECHANGED = 0x0020
            ctypes.windll.user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED
            )
        except Exception as e:
            logger.error(f"Failed to update input transparency via ctypes: {e}")

    def set_locked(self, locked: bool):
        self.locked = locked
        self.settings.set_value("macro_hud_locked", locked)
        self.update_input_transparency()
        self.update() # Force border redraw

    def on_state_changed(self, state, name):
        if state != "replaying":
            self.playback_progress_ms = 0
            self.timeline.playhead_ms = 0
        self.refresh_ui()
        self.update()

    def on_playback_progress(self, elapsed_ms):
        self.playback_progress_ms = elapsed_ms
        self.timeline.playhead_ms = elapsed_ms
        self.timeline.update()
        
        # Dynamically show playback counter in real time
        state = self.macro_manager.state
        if state == "replaying":
            profile = self.macro_manager.get_current_profile()
            max_duration = 0
            if profile and profile.actions:
                max_duration = profile.actions[-1].time_ms
            curr_sec = elapsed_ms / 1000.0
            tot_sec = max_duration / 1000.0
            self.lbl_status.setText(f"REPLAYING: {curr_sec:.1f}s / {tot_sec:.1f}s")

    def refresh_ui(self):
        profile = self.macro_manager.get_current_profile()
        profile_name = profile.name if profile else "No Profile Loaded"
        
        # Show category and active profile info
        cat_name = self.macro_manager.current_category
        self.lbl_profile.setText(f"{cat_name} » {profile_name}")
        
        state = self.macro_manager.state
        if state == "idle":
            self.lbl_status.setText("HUD READY")
            self.lbl_status.setStyleSheet("color: #00e676; font-family: 'Segoe UI'; font-size: 9px; font-weight: bold;")
        elif state == "recording":
            self.lbl_status.setText("RECORDING INPUT...")
            self.lbl_status.setStyleSheet("color: #ff1744; font-family: 'Segoe UI'; font-size: 9px; font-weight: bold;")
        elif state == "replaying":
            self.lbl_status.setStyleSheet("color: #ff9100; font-family: 'Segoe UI'; font-size: 9px; font-weight: bold;")

        # Populate the 1:1 timeline widget with blocks
        if profile:
            self.timeline.set_actions(profile.actions)
        else:
            self.timeline.set_actions([])
        self.timeline.update()

    def on_overlay_blocks_changed(self):
        """Sync user edits in HUD timeline back to profile and reload in main macro page."""
        profile = self.macro_manager.get_current_profile()
        if profile:
            actions = self.timeline.get_actions(record_mode=profile.record_mode)
            profile.actions = actions
            self.macro_manager.save_profiles()
            
            # Sync to main macro page if it is active
            if self.main_win and hasattr(self.main_win, "macro_page") and self.main_win.macro_page:
                macro_page = self.main_win.macro_page
                # Disconnect blocksChanged temporarily to avoid loop recursion
                try:
                    macro_page.timeline.blocksChanged.disconnect(macro_page.on_timeline_blocks_modified)
                except Exception:
                    pass
                macro_page.timeline.set_actions(profile.actions)
                macro_page.timeline.update()
                macro_page.timeline.blocksChanged.connect(macro_page.on_timeline_blocks_modified)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        w = self.width()
        h = self.height()
        
        # 1. Background Panel (Semi-transparent dark acrylic look)
        if self.locked:
            bg_color = QColor(10, 10, 10, 190)
            painter.setBrush(QBrush(bg_color))
            painter.setPen(Qt.PenStyle.NoPen)
            rect = QRectF(0, 0, w, h)
            painter.drawRoundedRect(rect, 6, 6)
        else:
            bg_color = QColor(15, 15, 15, 230)
            painter.setBrush(QBrush(bg_color))
            # Dash line border when unlocked to signal user can drag
            pen = QPen(QColor(255, 140, 0, 200), 1.5, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            rect = QRectF(0, 0, w, h)
            painter.drawRoundedRect(rect, 6, 6)
            
        # 2. Draw breathing status circle dot (Top left)
        t = time.time() * 4.5
        breath_opacity = int(127 + 128 * math.sin(t))
        
        state = self.macro_manager.state
        if state == "idle":
            dot_color = QColor(0, 230, 118, 255)
        elif state == "recording":
            dot_color = QColor(255, 23, 68, breath_opacity)
        elif state == "replaying":
            dot_color = QColor(255, 145, 0, breath_opacity)
            
        painter.setBrush(QBrush(dot_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(12, 11, 10, 10)
        
        if state in ("recording", "replaying"):
            ring_pen = QPen(dot_color)
            ring_pen.setWidth(1)
            painter.setPen(ring_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            expansion = 3 + 1.5 * math.sin(t)
            painter.drawEllipse(12 - expansion / 2.0, 11 - expansion / 2.0, 10 + expansion, 10 + expansion)

    # --- Mouse Drag Event Handlers for Unlocked Layout Customization ---
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
            self.settings.set_value("macro_hud_x", self.x())
            self.settings.set_value("macro_hud_y", self.y())
            event.accept()
            logger.info(f"Macro HUD position saved to X: {self.x()}, Y: {self.y()}")
            
    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_ui()
        if hasattr(self, 'anim_timer') and not self.anim_timer.isActive():
            self.anim_timer.start(33)

    def hideEvent(self, event):
        super().hideEvent(event)
        if hasattr(self, 'anim_timer') and self.anim_timer.isActive():
            self.anim_timer.stop()

    def cleanup_widget(self):
        if hasattr(self, 'anim_timer') and self.anim_timer:
            try:
                self.anim_timer.stop()
                self.anim_timer.timeout.disconnect()
            except Exception:
                pass
