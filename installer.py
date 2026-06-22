# -*- coding: utf-8 -*-
import os
import sys
class StderrLogger:
    def __init__(self, filename):
        self.filename = filename
    def write(self, text):
        try:
            with open(self.filename, 'a', encoding='utf-8') as f:
                f.write(text)
        except Exception:
            pass
    def flush(self):
        pass

log_path = os.path.join(os.path.expanduser("~"), ".core_commander", "installer_stderr.log")
os.makedirs(os.path.dirname(log_path), exist_ok=True)
sys.stderr = StderrLogger(log_path)
if sys.stdout is None:
    sys.stdout = sys.stderr
import shutil
import zipfile
import winreg
import win32com.client
import subprocess  # nosec
import ctypes
import gc
import stat
import time
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QRectF
from PySide6.QtGui import QColor, QPainter, QLinearGradient, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget, QFileDialog, QPushButton
from qfluentwidgets import (
    TitleLabel, SubtitleLabel, BodyLabel, LineEdit, 
    ProgressBar, CheckBox, InfoBar, setThemeColor, setTheme, Theme,
    CaptionLabel, IconWidget, FluentIcon
)

# Global constants
APP_NAME = "Core Commander"
VERSION = "2.0"
PUBLISHER = "B站 _可燃垃圾"
DEFAULT_INSTALL_DIR = r"C:\Program Files\CoreCommander"

def resolve_system32_path(name):
    system_root = os.path.expandvars("%SystemRoot%") or "C:\\Windows"
    if name.lower() in ("powershell", "powershell.exe"):
        return os.path.join(system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    if not name.lower().endswith(".exe"):
        name += ".exe"
    return os.path.join(system_root, "System32", name)

class ExtractionWorker(QThread):
    progress_signal = Signal(int, str)
    finished_signal = Signal(bool, str)

    def __init__(self, zip_path, dest_dir):
        super().__init__()
        self.zip_path = zip_path
        self.dest_dir = dest_dir

    def run(self):
        try:
            # Terminate any running CoreCommander.exe instances to unlock files
            target_exe = os.path.join(self.dest_dir, "CoreCommander.exe")
            if os.path.exists(target_exe):
                for attempt in range(10):
                    try:
                        subprocess.run([resolve_system32_path("taskkill.exe"), "/f", "/im", "CoreCommander.exe"], capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)  # nosec
                    except Exception:  # nosec
                        pass
                    try:
                        with open(target_exe, 'r+b') as f:
                            pass
                        break
                    except OSError:
                        time.sleep(0.5)
            else:
                try:
                    subprocess.run([resolve_system32_path("taskkill.exe"), "/f", "/im", "CoreCommander.exe"], capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)  # nosec
                except Exception:  # nosec
                    pass
            
            if not os.path.exists(self.dest_dir):
                os.makedirs(self.dest_dir, exist_ok=True)
            try:
                username = os.environ.get("USERNAME")
                if not username:
                    username = os.getlogin()
                if username:
                    subprocess.run([
                        resolve_system32_path("icacls.exe"),
                        self.dest_dir,
                        "/inheritance:r",
                        "/grant:r", "SYSTEM:(OI)(CI)F",
                        "/grant:r", "*S-1-5-32-544:(OI)(CI)F",
                        "/grant:r", f"{username}:(OI)(CI)F"
                    ], capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
            except Exception:
                pass

            with zipfile.ZipFile(self.zip_path, 'r') as zip_ref:
                info_list = zip_ref.infolist()
                total_files = len(info_list)
                
                if total_files == 0:
                    self.finished_signal.emit(False, "压缩文件为空！")
                    return

                for i, file_info in enumerate(info_list):
                    # Skip directory extraction entries
                    if file_info.filename.endswith('/') or file_info.filename.endswith('\\'):
                        continue
                        
                    target_path = os.path.normpath(os.path.join(self.dest_dir, file_info.filename))
                    
                    # Prevent Zip Slip / Path Traversal
                    resolved_dest = os.path.abspath(self.dest_dir)
                    resolved_target = os.path.abspath(target_path)
                    if not resolved_target.startswith(resolved_dest):
                        raise PermissionError(f"Directory traversal attempt detected in ZIP archive: {file_info.filename}")

                    parent_dir = os.path.dirname(target_path)
                    if not os.path.exists(parent_dir):
                        os.makedirs(parent_dir, exist_ok=True)

                    extracted = False
                    if os.path.exists(target_path):
                        # Clear read-only attributes
                        try:
                            os.chmod(target_path, stat.S_IWRITE)
                        except Exception:  # nosec
                            pass
                        
                        # Try standard overwrite
                        try:
                            zip_ref.extract(file_info, self.dest_dir)
                            extracted = True
                        except (PermissionError, OSError):
                            # Try renaming fallback if locked
                            bak_path = f"{target_path}.{int(time.time())}.bak"
                            try:
                                os.rename(target_path, bak_path)
                                # Schedule old locked file for deletion on reboot
                                try:
                                    MOVEFILE_DELAY_UNTIL_REBOOT = 0x00000004
                                    ctypes.windll.kernel32.MoveFileExW(bak_path, None, MOVEFILE_DELAY_UNTIL_REBOOT)
                                except Exception:  # nosec
                                    pass
                                # Re-attempt extraction
                                zip_ref.extract(file_info, self.dest_dir)
                                extracted = True
                            except Exception:  # nosec
                                pass
                    
                    if not extracted:
                        zip_ref.extract(file_info, self.dest_dir)

                    percent = int((i + 1) / total_files * 100)
                    filename = os.path.basename(file_info.filename)
                    if not filename and '/' in file_info.filename:
                        filename = file_info.filename.split('/')[-2] + '/'
                    self.progress_signal.emit(percent, f"正在解压: {filename}")

            self.finished_signal.emit(True, "解压完成")
        except Exception as e:
            self.finished_signal.emit(False, f"解压失败: {str(e)}")

def delete_reg_key_recursive(root_key, subkey_path, view_flag):
    """
    Recursively deletes a registry key and all of its subkeys and values,
    preventing handle leaks and ensuring successful deletion on Windows.
    """
    try:
        hkey = winreg.OpenKey(root_key, subkey_path, 0, winreg.KEY_ALL_ACCESS | view_flag)
    except FileNotFoundError:
        return
    except PermissionError:
        try:
            hkey = winreg.OpenKey(root_key, subkey_path, 0, winreg.KEY_READ | view_flag)
        except Exception:
            return
    except Exception:
        return

    subkeys = []
    try:
        i = 0
        while True:
            try:
                subkeys.append(winreg.EnumKey(hkey, i))
                i += 1
            except OSError:
                break
    finally:
        hkey.Close()

    for subkey in subkeys:
        delete_reg_key_recursive(root_key, f"{subkey_path}\\{subkey}", view_flag)

    try:
        winreg.DeleteKeyEx(root_key, subkey_path, view_flag, 0)
    except Exception:
        try:
            winreg.DeleteKey(root_key, subkey_path)
        except Exception:  # nosec
            pass

class UninstallWorker(QThread):
    progress_signal = Signal(int, str)
    finished_signal = Signal(bool, str)

    def __init__(self, install_dir):
        super().__init__()
        self.install_dir = install_dir

    def run(self):
        has_com = False
        try:
            import pythoncom
            pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
            has_com = True
        except ImportError:
            pass
            
        try:
            # 0. Restore system defaults before deleting files
            self.progress_signal.emit(2, "正在还原系统默认设置...")
            try:
                sys.path.insert(0, self.install_dir)
                from core_commander.core.system_tweaks import SystemTweaksService
                SystemTweaksService.restore_system_defaults()
            except Exception as e:
                print(f"Failed to restore system defaults: {e}")

            # 0.5. Kill active running CoreCommander.exe instances first to unlock files
            self.progress_signal.emit(5, "正在终止运行中的程序实例...")
            try:
                subprocess.run([resolve_system32_path("taskkill.exe"), "/f", "/im", "CoreCommander.exe"], capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)  # nosec
            except Exception:  # nosec
                pass
            time.sleep(1.0) # Grace period for OS file locks to release

            # 1. Delete shortcuts
            self.progress_signal.emit(20, "正在移除快捷方式...")
            shell = None
            desktop = None
            programs = None
            try:
                shell = win32com.client.Dispatch("WScript.Shell")
                desktop = shell.SpecialFolders("Desktop")
                programs = shell.SpecialFolders("Programs")
                
                lnk_desktop = os.path.normpath(os.path.join(desktop, f"{APP_NAME}.lnk"))
                lnk_menu = os.path.normpath(os.path.join(programs, f"{APP_NAME}.lnk"))
                
                if os.path.exists(lnk_desktop):
                    try:
                        os.chmod(lnk_desktop, stat.S_IWRITE)
                    except Exception:  # nosec
                        pass
                    try:
                        os.remove(lnk_desktop)
                    except Exception:  # nosec
                        pass
                if os.path.exists(lnk_menu):
                    try:
                        os.chmod(lnk_menu, stat.S_IWRITE)
                    except Exception:  # nosec
                        pass
                    try:
                        os.remove(lnk_menu)
                    except Exception:  # nosec
                        pass
            finally:
                # Explicitly release references before CoUninitialize
                desktop = None
                programs = None
                shell = None

            # 2. Delete registry entries
            self.progress_signal.emit(50, "正在清理系统注册表...")
            reg_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\CoreCommander"
            for view_flag in [winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY]:
                delete_reg_key_recursive(winreg.HKEY_LOCAL_MACHINE, reg_path, view_flag)

            # Cleanup autostart entries
            run_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
            for hkey_root in [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]:
                for view_flag in [winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY]:
                    try:
                        with winreg.OpenKey(hkey_root, run_path, 0, winreg.KEY_WRITE | view_flag) as key:
                            winreg.DeleteValue(key, "CoreCommander")
                    except Exception:  # nosec
                        pass

            # 3. Delete application files
            self.progress_signal.emit(80, "正在清除应用程序文件...")
            
            def remove_readonly(func, path, excinfo):
                try:
                    os.chmod(path, stat.S_IWRITE)
                    func(path)
                except Exception:  # nosec
                    pass

            for item in os.listdir(self.install_dir):
                item_path = os.path.join(self.install_dir, item)
                if item != "uninstall.exe":
                    try:
                        if os.path.isdir(item_path):
                            shutil.rmtree(item_path, onerror=remove_readonly)
                        else:
                            try:
                                os.chmod(item_path, stat.S_IWRITE)
                            except Exception:  # nosec
                                pass
                            os.remove(item_path)
                    except Exception:  # nosec
                        pass
                        
            self.progress_signal.emit(100, "卸载成功")
            self.finished_signal.emit(True, "卸载完成")
        except Exception as e:
            self.finished_signal.emit(False, f"卸载失败: {str(e)}")
        finally:
            if has_com:
                gc.collect() # Force cleanup of all COM variables
                try:
                    pythoncom.CoUninitialize()
                except Exception:  # nosec
                    pass

class InstallerWindow(QWidget):
    def __init__(self, is_uninstaller=False):
        super().__init__()
        self.is_uninstaller = is_uninstaller
        self.install_dir = DEFAULT_INSTALL_DIR
        self.zip_path = self.get_embedded_zip_path()
        
        self.setWindowTitle(f"{APP_NAME} - 卸载引导向导" if is_uninstaller else f"{APP_NAME} - 安装引导向导")
        self.setFixedSize(620, 400)
        self.setObjectName("InstallerWindow")
        
        # Borderless window flag setup
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowSystemMenuHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # Apply standard Fluent Light Theme
        setTheme(Theme.LIGHT)
        setThemeColor("#0078D4")
        
        self.setStyleSheet("""
            #InstallerWindow {
                background-color: transparent;
            }
            QLabel {
                color: #0f172a;
                font-family: "Segoe UI", "Segoe UI Variable", "Microsoft YaHei";
            }
            QCheckBox {
                color: #334155;
                font-family: "Segoe UI", "Segoe UI Variable", "Microsoft YaHei";
            }
            #LeftPanel QLabel {
                color: #ffffff;
            }
            #lbl_left_title {
                color: #ffffff;
                font-weight: bold;
                font-size: 17px;
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
            /* Card panels welcome etc. */
            #page_welcome, #page_dir, #page_progress, #page_complete, 
            #page_un_confirm, #page_un_progress, #page_un_complete {
                background-color: rgba(255, 255, 255, 0.85);
                border: 1px solid rgba(226, 232, 240, 0.8); /* Slate-200 border */
                border-radius: 10px;
            }
            LineEdit {
                color: #0f172a;
                background-color: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 13px;
                font-family: "Segoe UI", "Microsoft YaHei";
            }
            LineEdit:focus {
                border: 1.5px solid #3b82f6;
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
            #btn_next {
                background-color: #2563eb; /* Blue-600 */
                color: #ffffff;
                border: 1px solid #1d4ed8;
            }
            #btn_next:hover {
                background-color: #1d4ed8; /* Blue-700 */
            }
            #btn_next:pressed {
                background-color: #1e40af; /* Blue-800 */
            }
            #btn_next:disabled {
                background-color: #e2e8f0;
                color: #94a3b8;
                border: 1px solid #e2e8f0;
            }
            #btn_min, #btn_close {
                border-radius: 4px;
            }
        """)
        
        self.init_ui()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            # Only allow dragging on the sidebar (left panel) or top title bar area
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
        
        # 1. Fill entire window with right panel light grey color, rounded
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 12, 12)
        painter.fillPath(path, QColor("#f8fafc"))  # Slate-50 premium background
        
        # 2. Draw left panel gradient (width 180)
        painter.setClipPath(path)
        
        left_rect = QRectF(0, 0, 180, self.height())
        grad = QLinearGradient(0, 0, 0, self.height())
        # Premium Dark Deep Space Gradient
        grad.setColorAt(0, QColor("#0f172a"))  # Slate-900
        grad.setColorAt(0.5, QColor("#1e1b4b"))  # Indigo-950
        grad.setColorAt(1, QColor("#020617"))  # Slate-950
        painter.fillRect(left_rect, grad)
        
        # 3. Draw a separator line between left and right panel
        painter.setPen(QPen(QColor(255, 255, 255, 10), 1))
        painter.drawLine(180, 0, 180, self.height())
        
        # 4. Draw window border outline
        painter.setClipping(False)
        painter.setPen(QPen(QColor(15, 23, 42, 20), 1.2)) # Subtle slate-900 border
        painter.drawRoundedRect(QRectF(0.6, 0.6, self.width() - 1.2, self.height() - 1.2), 12, 12)

    def get_embedded_zip_path(self):
        # PyInstaller extracts data files to sys._MEIPASS
        if hasattr(sys, '_MEIPASS'):
            return os.path.join(sys._MEIPASS, "CoreCommander.zip")
        # Support Nuitka onefile temporary directory extraction path
        try:
            nuitka_dir = os.path.dirname(os.path.abspath(__file__))
            candidate = os.path.join(nuitka_dir, "CoreCommander.zip")
            if os.path.exists(candidate):
                return candidate
        except Exception:
            pass
        # Support Nuitka standalone compilation path resolution
        base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        candidate = os.path.join(base_dir, "CoreCommander.zip")
        if os.path.exists(candidate):
            return candidate
        return os.path.join(os.path.abspath("dist"), "CoreCommander.zip")

    def init_ui(self):
        # Split layout horizontally
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
        
        self.lbl_left_title = TitleLabel(APP_NAME, self.left_panel)
        self.lbl_left_title.setObjectName("lbl_left_title")
        self.lbl_left_title.setStyleSheet("QLabel { font-size: 16px; font-weight: bold; }")
        
        self.lbl_left_ver = BodyLabel(f"Version {VERSION}", self.left_panel)
        self.lbl_left_ver.setObjectName("lbl_left_ver")
        self.lbl_left_ver.setStyleSheet("QLabel { font-size: 12px; }")
        
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

        # Title bar layout (Title + Close/Min buttons)
        self.title_bar_layout = QHBoxLayout()
        self.title_bar_layout.setContentsMargins(0, 0, 0, 0)
        
        self.page_title_label = SubtitleLabel("", self.right_panel)
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
        self.btn_close.clicked.connect(self.close)
        
        self.title_bar_layout.addWidget(self.btn_min)
        self.title_bar_layout.addWidget(self.btn_close)
        
        self.right_layout.addLayout(self.title_bar_layout)

        # Stacked Widget for Pages
        self.stacked_widget = QStackedWidget(self.right_panel)
        self.right_layout.addWidget(self.stacked_widget, 1)

        # Create pages
        if self.is_uninstaller:
            self.init_uninstall_pages()
        else:
            self.init_install_pages()

        # Footer Buttons
        self.footer_layout = QHBoxLayout()
        self.btn_cancel = QPushButton("取消", self.right_panel)
        self.btn_cancel.setObjectName("btn_cancel")
        self.btn_back = QPushButton("上一步", self.right_panel)
        self.btn_back.setObjectName("btn_back")
        self.btn_next = QPushButton("下一步" if not self.is_uninstaller else "卸载", self.right_panel)
        self.btn_next.setObjectName("btn_next")
        
        self.footer_layout.addWidget(self.btn_cancel)
        self.footer_layout.addStretch(1)
        self.footer_layout.addWidget(self.btn_back)
        self.footer_layout.addWidget(self.btn_next)
        self.right_layout.addLayout(self.footer_layout)

        # Add Panels to Main Layout
        self.main_layout.addWidget(self.left_panel)
        self.main_layout.addWidget(self.right_panel)

        # Connections
        self.btn_cancel.clicked.connect(self.close)
        self.btn_back.clicked.connect(self.prev_page)
        self.btn_next.clicked.connect(self.next_page)

        self.update_buttons()

    def init_install_pages(self):
        # Page 1: Welcome & Intro
        self.page_welcome = QWidget()
        self.page_welcome.setObjectName("page_welcome")
        layout_welcome = QVBoxLayout(self.page_welcome)
        layout_welcome.setContentsMargins(15, 15, 15, 15)
        desc = (
            "本向导将指引您在计算机上部署 Core Commander。\n\n"
            "建议在继续安装前关闭所有其他无关应用程序。\n\n"
            "点击「下一步」以继续。"
        )
        self.lbl_welcome_desc = BodyLabel(desc)
        self.lbl_welcome_desc.setWordWrap(True)
        layout_welcome.addWidget(self.lbl_welcome_desc)
        layout_welcome.addStretch(1)
        self.stacked_widget.addWidget(self.page_welcome)

        # Page 2: Directory Choice
        self.page_dir = QWidget()
        self.page_dir.setObjectName("page_dir")
        layout_dir = QVBoxLayout(self.page_dir)
        layout_dir.setContentsMargins(15, 15, 15, 15)
        layout_dir.setSpacing(15)
        
        layout_dir.addWidget(BodyLabel("请选择安装目标文件夹："))
        
        layout_path = QHBoxLayout()
        self.txt_path = LineEdit(self.page_dir)
        self.txt_path.setText(self.install_dir)
        self.btn_browse = QPushButton("浏览...", self.page_dir)
        self.btn_browse.setObjectName("btn_browse")
        self.btn_browse.clicked.connect(self.browse_folder)
        layout_path.addWidget(self.txt_path, 1)
        layout_path.addWidget(self.btn_browse)
        layout_dir.addLayout(layout_path)
        
        total_bytes = 0
        try:
            if self.zip_path and os.path.exists(self.zip_path):
                import zipfile
                with zipfile.ZipFile(self.zip_path, 'r') as zf:
                    total_bytes = sum(info.file_size for info in zf.infolist())
        except Exception:
            pass
        if total_bytes <= 0:
            total_bytes = 1337 * 1024 * 1024  # Fallback
        size_mb = total_bytes / (1024 * 1024)
        if size_mb >= 1024:
            space_info = f"所需磁盘空间: {size_mb / 1024:.2f} GB"
        else:
            space_info = f"所需磁盘空间: {size_mb:.1f} MB"
        
        self.lbl_space = BodyLabel(space_info)
        self.lbl_space.setStyleSheet("color: rgba(0, 0, 0, 0.5);")
        layout_dir.addWidget(self.lbl_space)
        layout_dir.addStretch(1)
        self.stacked_widget.addWidget(self.page_dir)

        # Page 3: Progress
        self.page_progress = QWidget()
        self.page_progress.setObjectName("page_progress")
        layout_progress = QVBoxLayout(self.page_progress)
        layout_progress.setContentsMargins(15, 15, 15, 15)
        layout_progress.setSpacing(20)
        
        self.lbl_status = BodyLabel("准备解压并安装...")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setMaximumWidth(540)
        self.progress_bar = ProgressBar(self)
        self.progress_bar.setVal(0)
        
        layout_progress.addWidget(self.lbl_status)
        layout_progress.addWidget(self.progress_bar)
        layout_progress.addStretch(1)
        self.stacked_widget.addWidget(self.page_progress)

        # Page 4: Complete
        self.page_complete = QWidget()
        self.page_complete.setObjectName("page_complete")
        layout_complete = QVBoxLayout(self.page_complete)
        layout_complete.setContentsMargins(15, 15, 15, 15)
        layout_complete.setSpacing(15)
        
        layout_complete.addWidget(BodyLabel("Core Commander 安装已完成！已自动部署好快捷入口。"))
        
        self.chk_desktop = CheckBox("创建桌面快捷方式", self.page_complete)
        self.chk_desktop.setChecked(True)
        self.chk_start = CheckBox("创建开始菜单快捷方式", self.page_complete)
        self.chk_start.setChecked(True)
        self.chk_run = CheckBox("立即运行 Core Commander", self.page_complete)
        self.chk_run.setChecked(True)
        
        layout_complete.addWidget(self.chk_desktop)
        layout_complete.addWidget(self.chk_start)
        layout_complete.addWidget(self.chk_run)
        layout_complete.addStretch(1)
        self.stacked_widget.addWidget(self.page_complete)

    def init_uninstall_pages(self):
        # Page 1: Confirm Uninstall
        self.page_un_confirm = QWidget()
        self.page_un_confirm.setObjectName("page_un_confirm")
        layout_un_confirm = QVBoxLayout(self.page_un_confirm)
        layout_un_confirm.setContentsMargins(15, 15, 15, 15)
        desc = (
            "您确定要彻底卸载 Core Commander 及其所有配置组件吗？\n\n"
            "这将彻底移除主程序、已优化的系统电源方案快捷入口、桌面和开始菜单的快捷方式。\n\n"
            "点击「卸载」以继续。"
        )
        self.lbl_un_confirm = BodyLabel(desc)
        self.lbl_un_confirm.setWordWrap(True)
        layout_un_confirm.addWidget(self.lbl_un_confirm)
        layout_un_confirm.addStretch(1)
        self.stacked_widget.addWidget(self.page_un_confirm)

        # Page 2: Progress
        self.page_un_progress = QWidget()
        self.page_un_progress.setObjectName("page_un_progress")
        layout_un_progress = QVBoxLayout(self.page_un_progress)
        layout_un_progress.setContentsMargins(15, 15, 15, 15)
        layout_un_progress.setSpacing(20)
        
        self.lbl_un_status = BodyLabel("准备卸载项目...")
        self.lbl_un_status.setWordWrap(True)
        self.lbl_un_status.setMaximumWidth(540)
        self.un_progress_bar = ProgressBar(self)
        self.un_progress_bar.setVal(0)
        
        layout_un_progress.addWidget(self.lbl_un_status)
        layout_un_progress.addWidget(self.un_progress_bar)
        layout_un_progress.addStretch(1)
        self.stacked_widget.addWidget(self.page_un_progress)

        # Page 3: Complete
        self.page_un_complete = QWidget()
        self.page_un_complete.setObjectName("page_un_complete")
        layout_un_complete = QVBoxLayout(self.page_un_complete)
        layout_un_complete.setContentsMargins(15, 15, 15, 15)
        
        layout_un_complete.addWidget(BodyLabel("Core Commander 已成功从您的计算机上移除！"))
        layout_un_complete.addStretch(1)
        self.stacked_widget.addWidget(self.page_un_complete)

    def browse_folder(self):
        try:
            # Use native Windows shell COM BrowseForFolder
            # This avoids dependency on tkinter (not packaged by Nuitka)
            # and prevents PySide6 non-native QFileDialog QObject::eventFilter crash loop
            import win32com.client
            shell = win32com.client.Dispatch("Shell.Application")
            
            # Options: BIF_NEWDIALOGSTYLE (0x10) | BIF_RETURNONLYFSDIRS (0x01) | BIF_USENEWUI (0x40)
            # hwnd parameter makes the dialog modal to our installer window
            folder = shell.BrowseForFolder(int(self.winId()), "请选择安装路径", 0x10 | 0x01 | 0x40, "")
            if folder:
                path = os.path.normpath(folder.Self.Path)
                if os.path.basename(path).lower() != "corecommander":
                    path = os.path.join(path, "CoreCommander")
                self.install_dir = path
                self.txt_path.setText(self.install_dir)
        except Exception as e:
            InfoBar.error("错误", f"无法打开选择路径对话框: {str(e)}", duration=5000, parent=self)

    def update_buttons(self):
        idx = self.stacked_widget.currentIndex()
        if self.is_uninstaller:
            titles = ["确认卸载应用程序", "正在清理系统配置", "卸载完成"]
            self.page_title_label.setText(titles[idx] if idx < len(titles) else "")
            
            self.btn_back.setVisible(False)
            if idx == 0:
                self.btn_cancel.setEnabled(True)
                self.btn_next.setText("卸载")
                self.btn_next.setEnabled(True)
            elif idx == 1:
                self.btn_cancel.setEnabled(False)
                self.btn_next.setEnabled(False)
            elif idx == 2:
                self.btn_cancel.setEnabled(False)
                self.btn_next.setText("完成")
                self.btn_next.setEnabled(True)
        else:
            titles = ["欢迎使用安装向导", "选择安装路径", "正在进行解压和部署", "安装完成"]
            self.page_title_label.setText(titles[idx] if idx < len(titles) else "")
            
            self.btn_back.setVisible(idx > 0 and idx < 3)
            self.btn_cancel.setVisible(idx < 3)
            
            if idx == 0:
                self.btn_next.setText("下一步")
                self.btn_next.setEnabled(True)
            elif idx == 1:
                self.btn_next.setText("安装")
                self.btn_next.setEnabled(True)
            elif idx == 2:
                self.btn_next.setEnabled(False)
                self.btn_back.setEnabled(False)
            elif idx == 3:
                self.btn_back.setVisible(False)
                self.btn_next.setText("完成")
                self.btn_next.setEnabled(True)

    def prev_page(self):
        idx = self.stacked_widget.currentIndex()
        if idx > 0:
            self.stacked_widget.setCurrentIndex(idx - 1)
            self.update_buttons()

    def next_page(self):
        idx = self.stacked_widget.currentIndex()
        if self.is_uninstaller:
            if idx == 0:
                self.start_uninstallation()
            elif idx == 2:
                self.complete_uninstallation()
        else:
            # Install Flow
            if idx == 0:
                self.stacked_widget.setCurrentIndex(1)
                self.update_buttons()
            elif idx == 1:
                path = os.path.normpath(self.txt_path.text().strip())
                if os.path.basename(path).lower() != "corecommander":
                    path = os.path.join(path, "CoreCommander")
                self.install_dir = path
                self.start_installation()
            elif idx == 3:
                self.complete_installation()

    def start_installation(self):
        if not self.zip_path or not os.path.exists(self.zip_path):
            InfoBar.error("错误", "找不到安装核心包文件！请重新下载安装包。", duration=5000, parent=self)
            return

        self.stacked_widget.setCurrentIndex(2)
        self.update_buttons()

        self.worker = ExtractionWorker(self.zip_path, self.install_dir)
        self.worker.progress_signal.connect(self.on_install_progress)
        self.worker.finished_signal.connect(self.on_install_finished)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()

    def on_install_progress(self, percent, msg):
        self.progress_bar.setVal(percent)
        self.lbl_status.setText(msg)

    def on_install_finished(self, success, msg):
        if success:
            self.stacked_widget.setCurrentIndex(3)
            self.update_buttons()
        else:
            self.stacked_widget.setCurrentIndex(1)
            self.update_buttons()
            InfoBar.error("安装失败", msg, duration=5000, parent=self)

    def complete_installation(self):
        # 1. Create shortcuts
        exe_path = os.path.normpath(os.path.join(self.install_dir, "CoreCommander.exe"))
        
        has_com = False
        try:
            import pythoncom
            pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
            has_com = True
        except ImportError:
            pass

        shell = None
        try:
            if has_com:
                shell = win32com.client.Dispatch("WScript.Shell")
            
            def make_shortcut(lnk_path, target_path, work_dir):
                shortcut = None
                if shell is not None:
                    try:
                        shortcut = shell.CreateShortCut(lnk_path)
                        shortcut.TargetPath = target_path
                        shortcut.WorkingDirectory = work_dir
                        shortcut.IconLocation = target_path
                        shortcut.save()
                        return True
                    except Exception:  # nosec
                        pass
                    finally:
                        shortcut = None
                # Fallback to powershell
                try:
                    subprocess.run([
                        resolve_system32_path("powershell.exe"),
                        "-NoProfile",
                        "-NonInteractive",
                        "-Command",
                        "& { param($lnk, $target, $work) $WshShell = New-Object -ComObject WScript.Shell; $Shortcut = $WshShell.CreateShortcut($lnk); $Shortcut.TargetPath = $target; $Shortcut.WorkingDirectory = $work; $Shortcut.IconLocation = $target; $Shortcut.Save() }",
                        "-lnk", lnk_path,
                        "-target", target_path,
                        "-work", work_dir
                    ], capture_output=True, check=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)  # nosec
                    return True
                except Exception:
                    return False

            if self.chk_desktop.isChecked():
                try:
                    if shell is not None:
                        desktop = shell.SpecialFolders("Desktop")
                        lnk_desktop = os.path.normpath(os.path.join(desktop, f"{APP_NAME}.lnk"))
                        make_shortcut(lnk_desktop, exe_path, self.install_dir)
                        desktop = None
                except Exception:  # nosec
                    pass

            if self.chk_start.isChecked():
                try:
                    if shell is not None:
                        programs = shell.SpecialFolders("Programs")
                        lnk_menu = os.path.normpath(os.path.join(programs, f"{APP_NAME}.lnk"))
                        make_shortcut(lnk_menu, exe_path, self.install_dir)
                        programs = None
                except Exception:  # nosec
                    pass
        finally:
            shell = None
            if has_com:
                gc.collect()
                try:
                    pythoncom.CoUninitialize()
                except Exception:  # nosec
                    pass

        # 2. Copy uninstall.exe (self executable) to install location
        try:
            uninst_dest = os.path.join(self.install_dir, "uninstall.exe")
            if os.path.exists(uninst_dest):
                try:
                    os.chmod(uninst_dest, stat.S_IWRITE)
                except Exception:  # nosec
                    pass
            shutil.copy2(sys.executable, uninst_dest)
        except Exception:  # nosec
            pass

        # 3. Register uninstall registry entries
        try:
            reg_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\CoreCommander"
            for view_flag in [winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY]:
                try:
                    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, reg_path, 0, winreg.KEY_WRITE | view_flag) as key:
                        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
                        winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, VERSION)
                        winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, PUBLISHER)
                        winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, f'"{os.path.join(self.install_dir, "uninstall.exe")}" /uninstall')
                        winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, exe_path)
                        winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, self.install_dir)
                except Exception:  # nosec
                    pass
        except Exception:  # nosec
            pass

        # 4. Run CoreCommander if checked
        if self.chk_run.isChecked():
            try:
                subprocess.Popen([exe_path], cwd=self.install_dir)  # nosec
            except Exception:  # nosec
                pass

        self.close()

    def start_uninstallation(self):
        self.stacked_widget.setCurrentIndex(1)
        self.update_buttons()

        # Find install dir dynamically
        try:
            exe_dir = os.path.dirname(sys.executable)
            self.install_dir = exe_dir
        except Exception:  # nosec
            pass

        self.un_worker = UninstallWorker(self.install_dir)
        self.un_worker.progress_signal.connect(self.on_uninstall_progress)
        self.un_worker.finished_signal.connect(self.on_uninstall_finished)
        self.un_worker.finished.connect(self.un_worker.deleteLater)
        self.un_worker.start()

    def on_uninstall_progress(self, percent, msg):
        self.un_progress_bar.setVal(percent)
        self.lbl_un_status.setText(msg)

    def on_uninstall_finished(self, success, msg):
        if success:
            self.stacked_widget.setCurrentIndex(2)
            self.update_buttons()
        else:
            self.stacked_widget.setCurrentIndex(0)
            self.update_buttons()
            InfoBar.error("卸载失败", msg, duration=5000, parent=self)

    def complete_uninstallation(self):
        try:
            import tempfile
            exe_path = os.path.normpath(sys.executable)
            exe_name = os.path.basename(exe_path)
            install_dir = os.path.normpath(self.install_dir)
            
            # Script that polls for uninstall.exe to exit, deletes it, removes the folder, and deletes itself
            bat_content = f"""@echo off
:loop
tasklist /FI "IMAGENAME eq {exe_name}" 2>NUL | find /I /N "{exe_name}">NUL
if "%ERRORLEVEL%"=="0" (
    ping 127.0.0.1 -n 2 >nul
    goto loop
)
del /f /q "{exe_path}"
rd /s /q "{install_dir}"
del /f /q "%~f0"
"""
            temp_dir = tempfile.gettempdir()
            bat_path = os.path.join(temp_dir, "cc_cleanup.bat")
            with open(bat_path, "w", encoding="gbk") as f:
                f.write(bat_content)
                
            subprocess.Popen([bat_path], shell=True, executable=resolve_system32_path("cmd.exe"), creationflags=subprocess.CREATE_NO_WINDOW)  # nosec
        except Exception as e:
            print(f"[ERROR] Failed to spawn self-deletion script: {e}", file=sys.stderr)
        self.close()

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False

def run_silent_install():
    # 1. Get embedded zip path
    zip_path = None
    if hasattr(sys, '_MEIPASS'):
        zip_path = os.path.join(sys._MEIPASS, "CoreCommander.zip")
    else:
        try:
            nuitka_dir = os.path.dirname(os.path.abspath(__file__))
            candidate = os.path.join(nuitka_dir, "CoreCommander.zip")
            if os.path.exists(candidate):
                zip_path = candidate
        except Exception:
            pass
        if not zip_path:
            base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
            candidate = os.path.join(base_dir, "CoreCommander.zip")
            if os.path.exists(candidate):
                zip_path = candidate
            else:
                zip_path = os.path.normpath(os.path.join(os.path.abspath("dist"), "CoreCommander.zip"))
                
    if not zip_path or not os.path.exists(zip_path):
        sys.exit(1)
        
    dest_dir = DEFAULT_INSTALL_DIR
    
    # 2. Terminate existing CoreCommander.exe instances
    target_exe = os.path.normpath(os.path.join(dest_dir, "CoreCommander.exe"))
    if os.path.exists(target_exe):
        for attempt in range(10):
            try:
                subprocess.run([resolve_system32_path("taskkill.exe"), "/f", "/im", "CoreCommander.exe"], capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
            except Exception:
                pass
            try:
                with open(target_exe, 'r+b') as f:
                    pass
                break
            except OSError:
                time.sleep(0.5)
    else:
        try:
            subprocess.run([resolve_system32_path("taskkill.exe"), "/f", "/im", "CoreCommander.exe"], capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception:
            pass
            
    # 3. Create destination directory & set permissions
    if not os.path.exists(dest_dir):
        os.makedirs(dest_dir, exist_ok=True)
    try:
        username = os.environ.get("USERNAME") or os.getlogin()
        if username:
            subprocess.run([
                resolve_system32_path("icacls.exe"),
                dest_dir,
                "/inheritance:r",
                "/grant:r", "SYSTEM:(OI)(CI)F",
                "/grant:r", "*S-1-5-32-544:(OI)(CI)F",
                "/grant:r", f"{username}:(OI)(CI)F"
            ], capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        pass
        
    # 4. Unpack ZIP
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        for member in zip_ref.infolist():
            try:
                zip_ref.extract(member, dest_dir)
            except Exception:
                pass
                
    # 5. Create shortcuts
    has_com = False
    try:
        import pythoncom
        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        has_com = True
    except ImportError:
        pass

    shell = None
    try:
        if has_com:
            shell = win32com.client.Dispatch("WScript.Shell")
        
        def make_shortcut(lnk_path, target_path, work_dir):
            if shell is not None:
                try:
                    shortcut = shell.CreateShortCut(lnk_path)
                    shortcut.TargetPath = target_path
                    shortcut.WorkingDirectory = work_dir
                    shortcut.IconLocation = target_path
                    shortcut.save()
                    return True
                except Exception:
                    pass
            try:
                subprocess.run([
                    resolve_system32_path("powershell.exe"),
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "& { param($lnk, $target, $work) $WshShell = New-Object -ComObject WScript.Shell; $Shortcut = $WshShell.CreateShortcut($lnk); $Shortcut.TargetPath = $target; $Shortcut.WorkingDirectory = $work; $Shortcut.IconLocation = $target; $Shortcut.Save() }",
                    "-lnk", lnk_path,
                    "-target", target_path,
                    "-work", work_dir
                ], capture_output=True, check=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
                return True
            except Exception:
                return False

        # Create Desktop shortcut
        try:
            if shell is not None:
                desktop = shell.SpecialFolders("Desktop")
                lnk_desktop = os.path.normpath(os.path.join(desktop, f"{APP_NAME}.lnk"))
                make_shortcut(lnk_desktop, target_exe, dest_dir)
        except Exception:
            pass

        # Create Start Menu shortcut
        try:
            if shell is not None:
                programs = shell.SpecialFolders("Programs")
                lnk_menu = os.path.normpath(os.path.join(programs, f"{APP_NAME}.lnk"))
                make_shortcut(lnk_menu, target_exe, dest_dir)
        except Exception:
            pass
    finally:
        shell = None
        if has_com:
            gc.collect()
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
                
    # 6. Copy uninstall.exe (self executable) to install location
    try:
        uninst_dest = os.path.normpath(os.path.join(dest_dir, "uninstall.exe"))
        if os.path.exists(uninst_dest):
            try:
                os.chmod(uninst_dest, stat.S_IWRITE)
            except Exception:
                pass
        shutil.copy2(sys.executable, uninst_dest)
    except Exception:
        pass

    # 7. Register uninstall registry entries
    try:
        reg_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\CoreCommander"
        for view_flag in [winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY]:
            try:
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, reg_path, 0, winreg.KEY_WRITE | view_flag) as key:
                    winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
                    winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, VERSION)
                    winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, PUBLISHER)
                    winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, f'"{uninst_dest}" /uninstall')
                    winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, target_exe)
                    winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, dest_dir)
            except Exception:
                pass
    except Exception:
        pass

    # 8. Start CoreCommander
    try:
        subprocess.Popen([target_exe], cwd=dest_dir)
    except Exception:
        pass

def request_admin_elevation():
    if is_admin():
        return False
    try:
        params = " ".join(sys.argv[1:])
        ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
        if int(ret) > 32:
            return True
    except Exception:  # nosec
        pass
    return False

def main():
    # Enforce Administrator privileges
    if not is_admin():
        if request_admin_elevation():
            sys.exit(0)
        else:
            ctypes.windll.user32.MessageBoxW(
                0, 
                "安装或卸载 Core Commander 需要管理员权限。\n请以管理员身份重新运行程序，或在弹出的用户账户控制(UAC)对话框中选择「是」。", 
                "权限错误", 
                0x10 | 0x0  # MB_ICONERROR | MB_OK
            )
            sys.exit(1)

    # Detect silent mode
    if "/S" in sys.argv or "--silent" in sys.argv:
        run_silent_install()
        sys.exit(0)

    # Detect uninstaller mode
    is_uninstaller = False
    
    # 1. Check command line flags
    if "/uninstall" in sys.argv or "--uninstall" in sys.argv:
        is_uninstaller = True
        
    # 2. Check if name of current file is uninstall.exe
    exe_name = os.path.basename(sys.executable).lower()
    if "uninstall" in exe_name:
        is_uninstaller = True

    app = QApplication(sys.argv)
    window = InstallerWindow(is_uninstaller=is_uninstaller)
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
