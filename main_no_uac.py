# -*- coding: utf-8 -*-
"""
Core Commander - Desktop System Optimizer Entry Point
Author: B站 _可燃垃圾
"""
import os
import sys

# Force PySide6 Qt bindings
os.environ['QT_API'] = 'pyside6'

from PySide6.QtWidgets import QApplication
from core_commander.utils.admin import is_admin, request_admin_elevation, enable_debug_privilege
from core_commander.utils.logger import logger

def main():
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
    if False:
        logger.info("Administrative privileges missing. Spawning runas dialog...")
        spawned = request_admin_elevation()
        if spawned:
            logger.info("Administrative elevation dialog spawned. Exiting current process.")
            sys.exit(0)
        else:
            logger.error("Administrative elevation dialog failed to spawn.")
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0, 
                "Core Commander 需要管理员权限才能运行。\n请重新运行并接受管理员权限请求（选择「是」），或者右键选择「以管理员身份运行」。", 
                "权限错误", 
                0x10 | 0x0  # MB_ICONERROR | MB_OK
            )
            sys.exit(1)
            
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
    
    # Late import to prevent importing Qt classes prior to QApplication initialization
    from core_commander.ui.window import MainWindow
    
    logger.info("Instantiating MainWindow interface...")
    window = MainWindow()
    window.show()
    
    logger.info("Entering main application event loop.")
    sys.exit(app.exec())

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.critical(f"Unhandled crash at application boot: {str(e)}", exc_info=True)
        sys.exit(1)