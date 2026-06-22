import logging
from PySide6.QtWidgets import QWidget, QRubberBand, QApplication, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFrame
from PySide6.QtCore import Qt, QRect, QPoint, Signal, QThread
from PySide6.QtGui import QColor, QPainter, QScreen, QImage, QPixmap
from qfluentwidgets import TextEdit, PushButton, PrimaryPushButton, BodyLabel

from core_commander.core.ocr_translator import ocr_engine

# Eagerly import ImageGrab so it is cached during the splash screen, avoiding GIL lock on first hotkey press
import PIL.ImageGrab

logger = logging.getLogger(__name__)

class OCRWorker(QThread):
    ocr_finished = Signal(str)
    
    def __init__(self, image_data):
        super().__init__()
        self.image_data = image_data

    def run(self):
        original = ocr_engine.recognize_text(self.image_data)
        self.ocr_finished.emit(original)


class TranslationWorker(QThread):
    translate_finished = Signal(str)
    
    def __init__(self, text):
        super().__init__()
        self.text = text
        
    def run(self):
        translated = ocr_engine.translate_text(self.text)
        self.translate_finished.emit(translated)


class OCRResultPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        
        self.setStyleSheet("""
            OCRResultPanel {
                background-color: rgba(30, 30, 30, 240);
                border-radius: 8px;
                border: 1px solid rgba(255, 255, 255, 40);
            }
            QLabel {
                color: white;
                font-family: 'Microsoft YaHei', 'Segoe UI';
                font-size: 14px;
            }
        """)
        
        self.layout_main = QVBoxLayout(self)
        self.layout_main.setContentsMargins(15, 15, 15, 15)
        self.layout_main.setSpacing(10)
        
        # Header
        header_layout = QHBoxLayout()
        title = BodyLabel("OCR 识别结果", self)
        title.setStyleSheet("color: white; font-weight: bold;")
        
        self.btn_translate = PrimaryPushButton("翻译", self)
        self.btn_translate.setFixedWidth(60)
        self.btn_translate.clicked.connect(self.on_translate_clicked)
        self.btn_translate.hide()
        
        self.btn_close = PushButton("关闭", self)
        self.btn_close.setFixedWidth(60)
        self.btn_close.clicked.connect(self.hide)
        
        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(self.btn_translate)
        header_layout.addWidget(self.btn_close)
        self.layout_main.addLayout(header_layout)
        
        # Original Text
        self.original_label = QLabel("识别中...", self)
        self.original_label.setWordWrap(True)
        self.original_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.original_label.setStyleSheet("background-color: rgba(0, 0, 0, 100); padding: 10px; border-radius: 5px;")
        self.original_label.setMaximumWidth(800)
        self.layout_main.addWidget(self.original_label)
        
        # Translated Text
        self.translated_label = QLabel("", self)
        self.translated_label.setWordWrap(True)
        self.translated_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.translated_label.setStyleSheet("background-color: rgba(0, 0, 0, 100); padding: 10px; border-radius: 5px; color: #00FFCC;")
        self.translated_label.setMaximumWidth(800)
        self.translated_label.hide()
        self.layout_main.addWidget(self.translated_label)

        # Allow dragging
        self.dragPos = QPoint()
        self.current_orig = ""
        self.trans_worker = None

    def mousePressEvent(self, event):
        self.dragPos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        if hasattr(self, 'dragPos'):
            delta = QPoint(event.globalPosition().toPoint() - self.dragPos)
            self.move(self.x() + delta.x(), self.y() + delta.y())
            self.dragPos = event.globalPosition().toPoint()

    def show_ocr_result(self, x, y, orig):
        self.current_orig = orig
        self.original_label.setText(orig if orig else "未检测到文字")
        self.btn_translate.setVisible(bool(orig and "未检测到" not in orig))
        self.translated_label.hide()
        
        # Dynamic resize
        self.adjustSize()
        
        screen = QApplication.primaryScreen().geometry()
        pos_x = min(x, screen.width() - self.width())
        pos_y = min(y, screen.height() - self.height())
        self.move(pos_x, pos_y)
        self.show()

    def on_translate_clicked(self):
        self.btn_translate.setEnabled(False)
        self.btn_translate.setText("翻译中")
        self.translated_label.setText("请求翻译中...")
        self.translated_label.show()
        self.adjustSize()
        
        self.trans_worker = TranslationWorker(self.current_orig)
        self.trans_worker.translate_finished.connect(self.on_translate_finished)
        self.trans_worker.start()

    def on_translate_finished(self, trans):
        self.translated_label.setText(trans if trans else "翻译失败")
        self.btn_translate.setEnabled(True)
        self.btn_translate.setText("翻译")
        self.adjustSize()


class ScreenshotWorker(QThread):
    finished = Signal(object)

    def __init__(self, bbox):
        super().__init__()
        self.bbox = bbox

    def run(self):
        try:
            from PySide6.QtGui import QImage
            img = PIL.ImageGrab.grab(bbox=self.bbox, all_screens=True)
            qimg = QImage(img.tobytes(), img.width, img.height, QImage.Format.Format_RGB888)
            self.finished.emit(qimg)
        except Exception as e:
            from loguru import logger
            logger.error(f"ImageGrab failed: {e}")
            self.finished.emit(None)

class OCROverlayWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
        
        self.rubberBand = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self.origin = QPoint()
        
        self.result_panel = OCRResultPanel()
        self.worker = None

    def show_overlay(self):
        screen = QApplication.primaryScreen()
        geo = screen.geometry()
        bbox = (geo.x(), geo.y(), geo.x() + geo.width(), geo.y() + geo.height())
        
        self.screenshot_worker = ScreenshotWorker(bbox)
        self.screenshot_worker.finished.connect(self._on_screenshot_ready)
        self.screenshot_worker.start()

    def _on_screenshot_ready(self, qimg):
        screen = QApplication.primaryScreen()
        if qimg:
            from PySide6.QtGui import QPixmap
            self.bg_pixmap = QPixmap.fromImage(qimg)
        else:
            self.bg_pixmap = screen.grabWindow(0)
            
        self.setGeometry(screen.geometry())
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.show()
        self.raise_()

    def paintEvent(self, event):
        painter = QPainter(self)
        if hasattr(self, 'bg_pixmap') and self.bg_pixmap:
            painter.drawPixmap(self.rect(), self.bg_pixmap)
        
        # Draw a semi-transparent dark overlay over the captured screen
        painter.fillRect(self.rect(), QColor(0, 0, 0, 100))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.origin = event.position().toPoint()
            self.rubberBand.setGeometry(QRect(self.origin, self.origin))
            self.rubberBand.show()
        elif event.button() == Qt.MouseButton.RightButton:
            # Right click to cancel
            self.hide()

    def mouseMoveEvent(self, event):
        if not self.origin.isNull():
            self.rubberBand.setGeometry(QRect(self.origin, event.position().toPoint()).normalized())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            rect = self.rubberBand.geometry()
            self.rubberBand.hide()
            self.hide()
            
            if rect.width() < 10 or rect.height() < 10:
                return # Too small to be valid

            # Grab cropped region from our cached screenshot instead of querying DWM
            if hasattr(self, 'bg_pixmap') and self.bg_pixmap:
                pixmap = self.bg_pixmap.copy(rect)
            else:
                screen = QApplication.primaryScreen()
                QApplication.processEvents()
                pixmap = screen.grabWindow(0, rect.x(), rect.y(), rect.width(), rect.height())
            
            from PySide6.QtCore import QBuffer, QIODevice
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.ReadWrite)
            pixmap.save(buffer, "BMP")
            img_bytes = buffer.data().data()
            
            # Show "loading" panel
            self.result_panel.original_label.setText("识别中...")
            self.result_panel.translated_label.hide()
            self.result_panel.btn_translate.hide()
            self.result_panel.adjustSize()
            self.result_panel.show_ocr_result(rect.x(), rect.bottom() + 10, "识别中...")
            
            self.last_ocr_pos = (rect.x(), rect.bottom() + 10)
            self.worker = OCRWorker(img_bytes)
            self.worker.ocr_finished.connect(self._on_ocr_finished)
            self.worker.start()

    def _on_ocr_finished(self, text):
        if hasattr(self, 'last_ocr_pos'):
            x, y = self.last_ocr_pos
            self.result_panel.show_ocr_result(x, y, text)
