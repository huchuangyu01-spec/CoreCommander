# -*- coding: utf-8 -*-
"""
Core Commander - Desktop System Optimizer Entry Point
Author: B站 _可燃垃圾
"""
import os
import sys
import builtins
import subprocess
import core_commander.utils.stderr_hook

# Monkey patch subprocess.Popen to prevent cmd window popups on Windows
if sys.platform == 'win32':
    _orig_popen_init = subprocess.Popen.__init__
    def _patched_popen_init(self, *args, **kwargs):
        cflags = kwargs.get('creationflags', 0)
        cflags |= 0x08000000  # CREATE_NO_WINDOW
        kwargs['creationflags'] = cflags
        
        sinfo = kwargs.get('startupinfo', None)
        if sinfo is None:
            sinfo = subprocess.STARTUPINFO()
        sinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        sinfo.wShowWindow = subprocess.SW_HIDE
        kwargs['startupinfo'] = sinfo
        
        _orig_popen_init(self, *args, **kwargs)
    subprocess.Popen.__init__ = _patched_popen_init


# Add _internal directory to sys.path if it exists (for both frozen and source runs)
if getattr(sys, 'frozen', False):
    internal_dir = os.path.join(sys._MEIPASS, "_internal")
else:
    internal_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_internal")

if os.path.exists(internal_dir) and internal_dir not in sys.path:
    sys.path.insert(0, internal_dir)

# Apply global monkey-patch for Python 3.11+ dataclasses mutable default values
# to resolve compatibility issues with fairseq configuration objects.
try:
    import dataclasses
    _orig_get_field = dataclasses._get_field
    def patched_get_field(cls, a_name, a_type, default_kw_only=False):
        default_val = getattr(cls, a_name, dataclasses.MISSING)
        if isinstance(default_val, dataclasses.Field):
            default_val = default_val.default
        has_temp_hash = False
        original_hash = None
        val_class = None
        if default_val is not dataclasses.MISSING and default_val is not None:
            val_class = default_val.__class__
            if hasattr(val_class, '__hash__') and val_class.__hash__ is None:
                try:
                    original_hash = val_class.__hash__
                    val_class.__hash__ = lambda self: id(self)
                    has_temp_hash = True
                except (TypeError, AttributeError):
                    pass
        try:
            return _orig_get_field(cls, a_name, a_type, default_kw_only)
        finally:
            if has_temp_hash and val_class is not None:
                val_class.__hash__ = original_hash
    dataclasses._get_field = patched_get_field
except Exception:
    pass

# PyInstaller / fairseq compatibility patch for 'help' not defined
if not hasattr(builtins, 'help'):
    builtins.help = lambda *args, **kwargs: None


# Force PySide6 Qt bindings
os.environ['QT_API'] = 'pyside6'

from PySide6.QtWidgets import QApplication
from core_commander.utils.admin import is_admin, request_admin_elevation, enable_debug_privilege
from core_commander.utils.logger import logger

def main():
    if "--daemon" in sys.argv:
        from core_commander.core.guard import run_guard_daemon
        sys.exit(run_guard_daemon())

    # Initialize security guard and HWID/Debugger checks before anything else
    from core_commander.core.guard import initialize_guard
    initialize_guard()

    # Enforce single instance check using a named Windows Mutex
    mutex_name = "Global\\CoreCommanderUniqueMutexInstance"
    import ctypes
    _instance_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
    last_error = ctypes.windll.kernel32.GetLastError()
    if last_error == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.user32.MessageBoxW(
            0, 
            "Core Commander 已经在运行中。\n请检查系统右下角托盘或任务管理器，请勿重复启动本程序。", 
            "提示", 
            0x40 | 0x0  # MB_ICONINFORMATION | MB_OK
        )
        sys.exit(0)

    logger.info("Initializing Core Commander Application boot sequence...")

    
    # Administrative privilege check and elevation request
    #if not is_admin():
    #    logger.info("Administrative privileges missing. Spawning runas dialog...")
    #    spawned = request_admin_elevation()
    #    if spawned:
    #        logger.info("Administrative elevation dialog spawned. Exiting current process.")
    #        sys.exit(0)
    #    else:
    #        logger.error("Administrative elevation dialog failed to spawn.")
    #        import ctypes
    #        ctypes.windll.user32.MessageBoxW(
    #            0, 
    #            "Core Commander 需要管理员权限才能运行。\n请重新运行并接受管理员权限请求（选择「是」），或者右键选择「以管理员身份运行」。", 
    #            "权限错误", 
    #            0x10 | 0x0  # MB_ICONERROR | MB_OK
    #        )
    #        sys.exit(1)
            
    # Enable system privileges for diagnostic tools and memory management
    enable_debug_privilege()
    
    # Run PySide6 Application
    app = QApplication(sys.argv)
    app.setApplicationName("Core Commander")
    app.setOrganizationName("CoreCommander")
    
    # Apply stylesheet cache patch to speed up widget creation and prevent thousands of redundant disk reads
    try:
        import qfluentwidgets.common.style_sheet as qss_mod
        _qss_cache = {}
        _orig_getStyleSheetFromFile = qss_mod.getStyleSheetFromFile

        def cached_getStyleSheetFromFile(file):
            if isinstance(file, str):
                if file in _qss_cache:
                    return _qss_cache[file]
                content = _orig_getStyleSheetFromFile(file)
                _qss_cache[file] = content
                return content
            
            try:
                name = file.fileName()
                if name and name in _qss_cache:
                    return _qss_cache[name]
            except Exception:
                name = None
            
            content = _orig_getStyleSheetFromFile(file)
            if name:
                _qss_cache[name] = content
            return content

        qss_mod.getStyleSheetFromFile = cached_getStyleSheetFromFile
        logger.info("Successfully applied QFluentWidgets stylesheet load cache monkey-patch.")
    except Exception as patch_err:
        logger.warning(f"Failed to apply stylesheet load cache monkey-patch: {patch_err}")
    
    # Check dependencies before loading MainWindow without importing them to save RAM
    dependencies_ready = False
    import importlib.util
    if importlib.util.find_spec("torch") is not None and importlib.util.find_spec("rvc_python") is not None:
        dependencies_ready = True

    if not dependencies_ready:
        from core_commander.ui.setup.deployment_dialog import DeploymentDialog
        from PySide6.QtWidgets import QDialog
        logger.info("Core dependencies missing. Launching Deployment Dialog.")
        dialog = DeploymentDialog()
        if dialog.exec() == QDialog.DialogCode.Accepted:
            logger.info("Deployment successful.")
        else:
            logger.info("Deployment cancelled or failed. Exiting.")
            sys.exit(0)

    # Show splash screen to improve perceived startup speed
    from PySide6.QtWidgets import QLabel
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont
    
    splash = QLabel("⚡ Core Commander\n\n正在初始化核心组件，请稍候...")
    splash.setWindowFlags(Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
    splash.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    splash.setAlignment(Qt.AlignmentFlag.AlignCenter)
    splash.setStyleSheet("""
        QLabel {
            background-color: rgba(30, 30, 30, 230);
            color: #ffffff;
            border: 1px solid #555555;
            border-radius: 12px;
            padding: 20px;
        }
    """)
    font = QFont("Microsoft YaHei", 12, QFont.Weight.Bold)
    splash.setFont(font)
    splash.resize(360, 160)
    
    screen_geometry = app.primaryScreen().availableGeometry()
    splash.move((screen_geometry.width() - splash.width()) // 2, (screen_geometry.height() - splash.height()) // 2)
    splash.show()
    app.processEvents()

    # Late import to prevent importing Qt classes prior to QApplication initialization
    from core_commander.ui.window import MainWindow
    
    logger.info("Instantiating MainWindow interface...")
    window = MainWindow()
    
    splash.close()
    window.show()
    exit_code = app.exec()
    sys.exit(exit_code)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.critical(f"Unhandled crash at application boot: {str(e)}", exc_info=True)
        sys.exit(1)