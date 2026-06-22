# -*- coding: utf-8 -*-
import os
import sys
import psutil
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QEvent, QTranslator, QLibraryInfo, QLocale, QAbstractNativeEventFilter, QCoreApplication, QThread, Signal
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect
from qfluentwidgets import FluentWindow, FluentIcon, NavigationItemPosition, InfoBar, MessageBox, setThemeColor, setTheme, Theme, isDarkTheme, FluentTranslator

from core_commander.config.settings import AppSettings
from core_commander.config.exceptions import CoreCommanderException
from core_commander.core.topology import TopologyEngine
from core_commander.core.isolation import ProcessIsolationService
from core_commander.core.power import PowerService
from core_commander.core.worker import MemoryCleanerWorker, OptimizationWorker, SystemStateScannerWorker
from core_commander.core.system_tweaks import SystemTweaksService, SystemTweakThread
from core_commander.core.fps_collector import FpsCollectorService
from core_commander.core.gpu_oc import GpuOverclockService
from core_commander.ui.overlay import GameOverlay
from core_commander.core.input_hook import GlobalInputHookThread
from core_commander.core.tweaks.throttler import NetworkThrottlerService

import ctypes
from ctypes import wintypes

# Global Windows API Ctypes definitions for thread operations
try:
    kernel32 = ctypes.WinDLL('kernel32.dll')
    kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenThread.restype = ctypes.c_void_p
    kernel32.SetThreadIdealProcessor.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    kernel32.SetThreadIdealProcessor.restype = wintypes.DWORD
    kernel32.SetThreadAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    kernel32.SetThreadAffinityMask.restype = ctypes.c_size_t
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = wintypes.BOOL
    HAS_THREAD_API = True
except Exception:
    HAS_THREAD_API = False

from core_commander.ui.pages.home import HomePage
from core_commander.ui.pages.logs import LogOverlay
from core_commander.ui.crosshair_overlay import CrosshairOverlay
from core_commander.ui.macro_overlay import MacroOverlay
from core_commander.core.macro_manager import MacroManager

def make_general_page(parent):
    from core_commander.ui.pages.settings import SettingsGeneralPage
    return SettingsGeneralPage(parent)

def make_optimization_page(parent):
    from core_commander.ui.pages.settings import SettingsOptimizationPage
    return SettingsOptimizationPage(parent)

def make_tools_page(parent):
    from core_commander.ui.pages.settings import SettingsToolsPage
    return SettingsToolsPage(parent)

def make_startup_page(parent):
    from core_commander.ui.pages.startup import StartupPage
    return StartupPage(parent)

def make_about_page(parent):
    from core_commander.ui.pages.about import AboutPage
    return AboutPage(parent)

def make_crosshair_page(parent):
    from core_commander.ui.pages.crosshair_page import CrosshairPage
    return CrosshairPage(parent)

def make_macro_page(parent):
    from core_commander.ui.pages.macro_page import MacroPage
    return MacroPage(parent)

def make_gpu_oc_page(parent):
    from core_commander.ui.pages.gpu_oc_page import GpuOverclockPage
    return GpuOverclockPage(parent)

def make_quick_chat_page(parent):
    from core_commander.ui.pages.quick_chat_page import QuickChatPage
    return QuickChatPage(parent)

def make_voice_changer_page(parent):
    from core_commander.ui.pages.voice_changer import VoiceChangerPage
    return VoiceChangerPage(parent)

from core_commander.utils.logger import logger
from core_commander.utils.admin import is_admin, enable_debug_privilege
from PySide6.QtWidgets import QWidget, QVBoxLayout

class LazyPageContainer(QWidget):
    def __init__(self, factory_func, object_name=None, parent=None):
        super().__init__(parent)
        if object_name:
            self.setObjectName(object_name)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.page = None
        self.factory_func = factory_func
        self.parent_window = parent

    def load_page(self):
        if self.page is None:
            logger.info(f"Loading page container: {self.objectName()}")
            self.page = self.factory_func(self.parent_window)
            self.layout.addWidget(self.page)
            self.retranslate_ui()
            if hasattr(self.parent_window, 'load_page_settings'):
                self.parent_window.load_page_settings(self.objectName(), self.page)

    def showEvent(self, event):
        super().showEvent(event)
        self.load_page()

    def retranslate_ui(self):
        if self.page and hasattr(self.page, 'retranslate_ui'):
            self.page.retranslate_ui()

    def __getattr__(self, name):
        if self.page is not None:
            return getattr(self.page, name)
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}' (page not loaded)")
from core_commander.utils.i18n import Trans

class HotkeyListenerThread(QThread):
    triggered = Signal()
    
    def __init__(self, mods: int, key_code: int, parent=None):
        super().__init__(parent)
        self.mods = mods
        self.key_code = key_code
        self.running = False
        self.thread_id = 0
        
    def run(self):
        self.running = True
        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        
        # Register thread-level hotkey (HWND = 0)
        ret = ctypes.windll.user32.RegisterHotKey(0, 999, self.mods, self.key_code)
        if not ret:
            logger.error(f"HotkeyListenerThread: RegisterHotKey failed. Error: {ctypes.windll.kernel32.GetLastError()}")
            return
            
        logger.info(f"HotkeyListenerThread: Global hotkey registered successfully on thread ID {self.thread_id}.")
        
        class MSG(ctypes.Structure):
            _fields_ = [
                ("hwnd", ctypes.c_void_p),
                ("message", ctypes.c_uint),
                ("wParam", ctypes.c_void_p),
                ("lParam", ctypes.c_void_p),
                ("time", ctypes.c_ulong),
                ("pt_x", ctypes.c_long),
                ("pt_y", ctypes.c_long)
            ]
            
        msg = MSG()
        while self.running:
            r = ctypes.windll.user32.GetMessageW(ctypes.byref(msg), 0, 0, 0)
            if r <= 0 or not self.running:
                break
            if msg.message == 0x0312:  # WM_HOTKEY
                if msg.wParam == 999:
                    self.triggered.emit()
            ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
            ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))
            
        ctypes.windll.user32.UnregisterHotKey(0, 999)
        logger.info("HotkeyListenerThread: Global hotkey unregistered and loop exited.")

    def stop(self):
        self.running = False
        if self.thread_id > 0:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)

class MainWindow(FluentWindow):
    """
    Main application shell implementing modern Fluent layout navigation.
    Coordinating background watchdog threads, optimization triggers, and settings persistence.
    """
    def __init__(self):
        super().__init__()
        self.is_initializing = True
        
        # Verify administrative privilege at boot safeguard
        if not is_admin():
            logger.error("MainWindow initialization failed: Process is not running with administrative privileges.")
            MessageBox("特权检查失败", "请以管理员特权启动本程序，以便获得底层系统拓扑分析与处理器关联调配权限。", self).exec()
            sys.exit(1)
            
        enable_debug_privilege()
        
        self.setWindowTitle("Core Commander - Fluent Design")
        self.resize(1090, 800)
        
        # Core system data initialization
        self.target_pid = None
        self.target_name = ""
        self.optimized_game_path = ""
        self.current_pid = None
        self.current_mask = []
        self.is_optimized = False
        self.is_auto_optimizing = False
        self.is_loading_preset = False
        self.is_loading_settings = True
        self.rate_limiter_state = "inactive"
        self.rate_limiter_enabled_cached = False
        self.rate_limiter_mode_cached = "hold"
        self.rate_limiter_type_cached = "qos"
        self.rate_limiter_value_cached = 100.0
        self.rate_limiter_unit_cached = "KB/s"
        self.input_hook_thread = None
        self.qt_translator = None
        self.fluent_translator = None
        self.changed_immediate_keys = set()
        
        self.IMMEDIATE_KEYS = {
            "enable_hard_working_set",
            "enable_net_bindings_tweak",
            "enable_net_imod_tweak",

            "disable_windows_visual_effects",
            "disable_windows_transparency",
            "enable_child_optimization",
            "enable_dwm_tweak",
            "enable_dwm_super_wet_tweak",
            "keyboard_repeat_delay_level",
            "win32_prio_sep",
            "enable_isolation",
            "enable_watchdog",
            "enable_core_parking",
            "enable_epp_max",
            "enable_timer_resolution_tweak",
            "enable_naraka_priority",
            "enable_custom_power_plan",
            "enable_gpu_optimization",
            "enable_nvidia_nip",
            "disable_useless_services",
            "disable_wsearch_tweak",
            "disable_gamedvr",
            "disable_smartscreen",
            "disable_firewall",
            "enable_game_gpu_preference_tweak",
            "enable_tcp_bbr_tweak",
            "enable_eee_tweak",
            "enable_web_search_tweak",
            "enable_telemetry_tasks_tweak",
            "enable_prefetcher_tweak",
            "enable_extreme_debloat_tweak",
            "enable_client_priority_demote",
            "enable_download_maps_tweak",
            "enable_map_updates_tweak"
        }
        
        # Load topology
        try:
            self.topology = TopologyEngine.get_topology()
        except Exception as e:
            logger.critical(f"Fatal topology load error: {str(e)}")
            self.topology = []
            
        # Configuration settings instance
        self.settings = AppSettings()
        self.auto_watchdog_thread = None
        
        # Hardware Info Initialization
        self.cpu_name = TopologyEngine.get_cpu_info()
        self.cpu_vendor = TopologyEngine.get_cpu_vendor()
        self.gpu_vendor = SystemTweaksService.get_gpu_vendor()
        self.ram_gb = round(psutil.virtual_memory().total / (1024**3))
        
        # QTimers for scheduling background activities
        self.watchdog = QTimer(self)
        self.watchdog.timeout.connect(self.run_watchdog)
        self.watchdog_counter = 0

        # Define Windows Event Hook callback for foreground window changes (EVENT_SYSTEM_FOREGROUND = 0x0003)
        self.win_event_hook = None
        if os.name == 'nt':
            try:
                WINEVENTPROC = ctypes.WINFUNCTYPE(
                    None,
                    wintypes.HANDLE,
                    wintypes.DWORD,
                    wintypes.HWND,
                    wintypes.LONG,
                    wintypes.LONG,
                    wintypes.DWORD,
                    wintypes.DWORD
                )
                def callback(hWinEventHook, event, hwnd, idObject, idChild, dwEventThread, dwmsEventTime):
                    try:
                        ProcessIsolationService.restore_foreground_process()
                    except Exception as e:
                        logger.debug(f"Error in WinEventHook callback: {e}")
                self.win_event_proc = WINEVENTPROC(callback)
            except Exception as e:
                logger.error(f"Failed to define WinEventHook callback: {e}")
                self.win_event_proc = None
        else:
            self.win_event_proc = None
        
        self.mem_timer = QTimer(self)
        self.mem_timer.timeout.connect(self.perform_auto_clean)
        
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.update_status)
        self.status_timer.start(2000) # Check process running state every 2 seconds
        
        self.rate_limiter_watchdog_timer = QTimer(self)
        self.rate_limiter_watchdog_timer.timeout.connect(self.check_rate_limiter_physical_key_state)
        
        # Instantiate child sub-interfaces
        self.is_loading_settings = True
        # Instantiate child sub-interfaces using LazyPageContainer
        self.is_loading_settings = True
        self.home_page = HomePage(self)
        
        self.general_page = LazyPageContainer(make_general_page, "generalPage", self)
        self.optimization_page = LazyPageContainer(make_optimization_page, "optimizationPage", self)
        
        # Backward compatibility aliasing layer
        self.cpu_page = self.optimization_page
        self.peripheral_page = self.optimization_page
        self.gpu_page = self.optimization_page
        self.memory_page = self.optimization_page
        self.privacy_page = self.optimization_page
        self.network_page = self.optimization_page
        
        self.tools_page = LazyPageContainer(make_tools_page, "toolsPage", self)
        self.startup_page = LazyPageContainer(make_startup_page, "startupPage", self)
        self.about_page = LazyPageContainer(make_about_page, "aboutPage", self)
        self.voice_changer_page = LazyPageContainer(make_voice_changer_page, "voiceChangerPage", self)
        self.crosshair_page = LazyPageContainer(make_crosshair_page, "crosshairPage", self)
        self.macro_page = LazyPageContainer(make_macro_page, "macroPage", self)
        self.gpu_oc_page = LazyPageContainer(make_gpu_oc_page, "gpuOcPage", self)
        self.quick_chat_page = LazyPageContainer(make_quick_chat_page, "quickChatPage", self)
        
        # Instantiate overlays
        self.log_overlay = LogOverlay(self)
        self.crosshair_overlay = CrosshairOverlay(self.settings, self)
        if self.settings.enable_crosshair:
            self.crosshair_overlay.refresh()
            
        self.macro_overlay = MacroOverlay(self.settings, self)
        if self.settings.get_bool("enable_macro_hud", True):
            self.macro_overlay.show()
        
        # OSD Overlay & FPS Collector variables
        self.overlay = None
        self.fps_collector = None
        self.hotkey_registered = False
        
        # Configure navigation and store item references for dynamic text updates
        self.nav_items = {
            'home': self.addSubInterface(self.home_page, FluentIcon.HOME, "主控制台"),
            'general': self.addSubInterface(self.general_page, FluentIcon.SETTING, "基础设置"),
            'optimization': self.addSubInterface(self.optimization_page, FluentIcon.BROOM, "深度系统优化"),
            'gpu_oc': self.addSubInterface(self.gpu_oc_page, FluentIcon.SPEED_HIGH, Trans.get("nav_gpu_oc")),
            'tools': self.addSubInterface(self.tools_page, FluentIcon.DEVELOPER_TOOLS, "性能维护与系统高级工具"),
            'voice_changer': self.addSubInterface(self.voice_changer_page, FluentIcon.MICROPHONE, "AI 实时变声"),
            'crosshair': self.addSubInterface(self.crosshair_page, FluentIcon.GAME, "屏幕准星"),
            'macro': self.addSubInterface(self.macro_page, FluentIcon.SCROLL, Trans.get("nav_macro")),
            'quick_chat': self.addSubInterface(self.quick_chat_page, FluentIcon.CHAT, "快捷发言"),
            'startup': self.addSubInterface(self.startup_page, FluentIcon.SEND, "引导自启动项管理"),
            'about': self.addSubInterface(self.about_page, FluentIcon.INFO, "系统配置与关于", NavigationItemPosition.BOTTOM)
        }
        
        # Load configuration file
        self.load_settings()
        
        # Apply GPU overclock settings if enabled on startup
        if self.settings.gpu_apply_on_startup:
            try:
                logger.info("Applying saved GPU overclock settings on startup...")
                GpuOverclockService.apply_overclock(
                    core_offset=self.settings.gpu_core_offset,
                    mem_offset=self.settings.gpu_mem_offset,
                    power_limit_pct=self.settings.gpu_power_limit,
                    temp_limit=self.settings.gpu_temp_limit,
                    voltage_pct=self.settings.gpu_voltage
                )
            except Exception as e:
                logger.error(f"Failed to apply GPU overclock settings on startup: {e}")
        
        # Activate high precision system timer resolution if enabled
        if self.settings.enable_timer_resolution_tweak:
            SystemTweaksService.set_timer_resolution_active(True)
        
        # Configure styling dynamically based on settings
        theme_mode = self.settings.theme_mode
        if theme_mode == "light":
            setTheme(Theme.LIGHT)
        elif theme_mode == "dark":
            setTheme(Theme.DARK)
        else:
            setTheme(Theme.AUTO)
        setThemeColor(self.settings.accent_color)
        
        # Add custom palette button in bottom-left corner
        from qfluentwidgets import NavigationPushButton
        self.palette_btn = NavigationPushButton(FluentIcon.PALETTE, "主题与语言" if Trans.CURRENT_LANG == "zh_CN" else "Theme & Language", False, self)
        self.navigationInterface.addWidget(
            routeKey="palette",
            widget=self.palette_btn,
            onClick=self.show_palette_dialog,
            position=NavigationItemPosition.BOTTOM
        )
        
        self.license_btn = NavigationPushButton(FluentIcon.CERTIFICATE, "未激活", False, self)
        # 即使禁用点击，也让它保持正常颜色显示，可以修改样式
        self.license_btn.setStyleSheet("NavigationPushButton { color: gray; }")
        self.navigationInterface.addWidget(
            routeKey="license_info",
            widget=self.license_btn,
            onClick=lambda: None,
            position=NavigationItemPosition.BOTTOM
        )
        self.update_license_display()
        
        # Post-display initialization (only run once on startup)
        self.update_custom_stylesheet()
        self.retranslate_ui()
        self.stackedWidget.currentChanged.connect(self.animate_page_transition)
        
        from qfluentwidgets import qconfig
        qconfig.themeChanged.connect(self.on_theme_changed)
        
        # Asynchronously scan system states 100ms after startup to keep GUI launch instant
        QTimer.singleShot(100, lambda: self.detect_and_sync_system_states(force_sync=False))
        
        # Install application event filter for click-outside sidebar collapse
        QApplication.instance().installEventFilter(self)
        
        # Register global hotkeys and threads asynchronously to ensure instant and non-blocking GUI startup
        self.is_initializing = False
        QTimer.singleShot(150, self.async_startup_services)
        
        logger.info("MainWindow initialized successfully.")
        
    def update_license_display(self):
        from core_commander.core.license import license_manager
        from datetime import datetime
        if not license_manager.is_active:
            self.license_btn.setText("未激活")
            self.license_btn.setIcon(FluentIcon.INFO)
            self.license_btn.setStyleSheet("NavigationPushButton { color: gray; }")
        else:
            if license_manager.expiry_timestamp == 0:
                self.license_btn.setText("授权: 永久有效")
            else:
                expire_date = datetime.fromtimestamp(license_manager.expiry_timestamp).strftime('%Y-%m-%d')
                self.license_btn.setText(f"到期: {expire_date}")
            self.license_btn.setIcon(FluentIcon.CERTIFICATE)
            self.license_btn.setStyleSheet("NavigationPushButton { color: #009688; font-weight: bold; }")

    def async_startup_services(self):
        """Starts background threads/watchdogs/hotkeys asynchronously after GUI startup to keep GUI launch instant and non-blocking."""
        logger.info("Starting background services (watchdog, hotkeys, input hook) asynchronously.")
        self.update_auto_watchdog()
        self.register_global_hotkey()
        self.start_input_hook_thread()
        self.register_ocr_hotkey()
        self.update_fps_collector_lifecycle()
        
        # Staggered background pre-loading of lazy sub-pages to ensure zero-stutter on first page switch
        QTimer.singleShot(200, lambda: self.general_page.load_page())
        QTimer.singleShot(450, lambda: self.optimization_page.load_page())
        QTimer.singleShot(700, lambda: self.tools_page.load_page())
        QTimer.singleShot(950, lambda: self.voice_changer_page.load_page())
        QTimer.singleShot(1200, lambda: self.gpu_oc_page.load_page())
        QTimer.singleShot(1450, lambda: self.crosshair_page.load_page())
        QTimer.singleShot(1700, lambda: self.macro_page.load_page())
        QTimer.singleShot(1950, lambda: self.quick_chat_page.load_page())
        QTimer.singleShot(2200, lambda: self.startup_page.load_page())
        QTimer.singleShot(2450, lambda: self.about_page.load_page())

    def on_theme_changed(self):
        logger.info("Visual theme change detected. Synchronizing text colors.")
        self.home_page.update_proc_display()
        self.log_overlay.update_style()
        self.update_custom_stylesheet()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress:
            # Block global hotkey presses (e.g. Space, Enter) from triggering focused controls when the window has focus
            if event.key() == Qt.Key.Key_Space or event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                rl_hotkey = getattr(self.settings, 'rate_limiter_hotkey', '').strip().lower()
                osd_hotkey = getattr(self.settings, 'fps_overlay_hotkey', '').strip().lower()
                
                key_str = ""
                if event.key() == Qt.Key.Key_Space:
                    key_str = "space"
                elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    key_str = "enter"
                
                if key_str and (key_str == rl_hotkey or key_str == osd_hotkey):
                    # Consume keypress event to prevent triggering focused GUI elements
                    return True

        if event.type() == QEvent.Type.MouseButtonPress:
            if hasattr(self, 'navigationInterface') and hasattr(self.navigationInterface, 'panel'):
                if self.isActiveWindow() and not self.navigationInterface.panel.isCollapsed():
                    pos = event.globalPosition().toPoint() if hasattr(event, 'globalPosition') else event.globalPos()
                    pos_in_nav = self.navigationInterface.mapFromGlobal(pos)
                    if not self.navigationInterface.rect().contains(pos_in_nav):
                        self.navigationInterface.panel.collapse()
        return super().eventFilter(obj, event)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange:
            if self.isActiveWindow():
                from core_commander.core.guard import check_window_focus_hook
                check_window_focus_hook()
        elif event.type() == QEvent.Type.WindowStateChange:
            self.update_telemetry_state()
        super().changeEvent(event)

    def animate_page_transition(self, index):
        # Bypass opacity transition effect because applying QGraphicsOpacityEffect 
        # on complex card pages with hundreds of styled widgets causes heavy CPU/GPU rendering lag.
        self.update_telemetry_state()

    def update_telemetry_state(self):
        """
        Dynamically suspends or resumes the background hardware telemetry worker
        based on window state, visibility, and selected page to eliminate redundant CPU/GPU queries.
        """
        try:
            if not hasattr(self, 'fps_collector') or not self.fps_collector:
                return
            
            # Telemetry is active if overlay is shown OR main window is visible, NOT minimized, AND user is on home_page or gpu_oc_page
            overlay_active = hasattr(self, 'overlay') and self.overlay and self.overlay.isVisible()
            ui_visible = self.isVisible() and not self.isMinimized()
            
            active_page = self.stackedWidget.currentWidget()
            page_active = active_page in [self.home_page, getattr(self, 'gpu_oc_page', None)]
            
            should_run = overlay_active or (ui_visible and page_active)
            
            if hasattr(self.fps_collector, 'hw_worker') and self.fps_collector.hw_worker:
                self.fps_collector.hw_worker.paused = not should_run
        except Exception as e:
            logger.debug(f"Failed to update telemetry state: {e}")

    def update_custom_stylesheet(self):
        logger.info("Inside update_custom_stylesheet - start")
        is_dark = isDarkTheme()
        if is_dark:
            qss = """
                /* Deep dark futuristic backing */
                MainWindow, FluentWindow, LogOverlay {
                    background-color: #0c0c12;
                }
                
                /* Sidebar navigation styling */
                NavigationInterface, NavigationPanel {
                    background-color: #08080c;
                    border-right: 1px solid rgba(255, 255, 255, 0.05);
                }
                
                /* Subpage view containers */
                #HomePageView, #StartupPageView, #AboutPageView, #SettingsPageView, ScrollArea, QScrollArea {
                    background-color: #0c0c12;
                    border: none;
                }

                /* Card Widgets Glassmorphism */
                SimpleCardWidget, ElevatedCardWidget, ExpandSettingCard {
                    background-color: rgba(22, 22, 29, 0.6);
                    border: 1px solid rgba(255, 255, 255, 0.06);
                    border-radius: 12px;
                }
                SimpleCardWidget:hover, ElevatedCardWidget:hover, ExpandSettingCard:hover {
                    background-color: rgba(30, 30, 42, 0.7);
                    border: 1px solid rgba(255, 255, 255, 0.1);
                }
            """
        else:
            qss = """
                /* Clean light backing */
                MainWindow, FluentWindow, LogOverlay {
                    background-color: #f3f3f9;
                }
                
                /* Sidebar navigation styling */
                NavigationInterface, NavigationPanel {
                    background-color: #f9f9fc;
                    border-right: 1px solid rgba(0, 0, 0, 0.05);
                }
                
                /* Subpage view containers */
                #HomePageView, #StartupPageView, #AboutPageView, #SettingsPageView, ScrollArea, QScrollArea {
                    background-color: #f3f3f9;
                    border: none;
                }

                /* Card Widgets Glassmorphism in Light Mode */
                SimpleCardWidget, ElevatedCardWidget, ExpandSettingCard {
                    background-color: rgba(255, 255, 255, 0.85);
                    border: 1px solid rgba(0, 0, 0, 0.05);
                    border-radius: 12px;
                }
                SimpleCardWidget:hover, ElevatedCardWidget:hover, ExpandSettingCard:hover {
                    background-color: rgba(255, 255, 255, 0.95);
                    border: 1px solid rgba(0, 0, 0, 0.1);
                }
            """
        logger.info("Inside update_custom_stylesheet - setting stylesheet")
        self.setStyleSheet(qss)
        logger.info("Inside update_custom_stylesheet - end")

    def show_palette_dialog(self):
        from core_commander.ui.dialogs import ThemeLanguageDialog
        from core_commander.utils.i18n import Trans
        
        dlg = ThemeLanguageDialog(self, self.settings)
        if Trans.CURRENT_LANG == "zh_CN":
            dlg.setWindowTitle("主题与语言设置")
            dlg.lbl_lang.setText("界面语言 / Language:")
            dlg.lbl_theme.setText("主题模式 / Theme Mode:")
            dlg.lbl_color.setText("主题配色 / Accent Color:")
            dlg.yesButton.setText("应用并保存")
            dlg.cancelButton.setText("取消")
        else:
            dlg.setWindowTitle("Theme & Language Settings")
            dlg.lbl_lang.setText("Interface Language:")
            dlg.lbl_theme.setText("Theme Mode:")
            dlg.lbl_color.setText("Accent Color:")
            dlg.yesButton.setText("Apply")
            dlg.cancelButton.setText("Cancel")
            
        if dlg.exec():
            lang_changed = Trans.CURRENT_LANG != dlg.selected_lang
            theme_changed = self.settings.theme_mode != dlg.selected_theme
            color_changed = self.settings.accent_color != dlg.selected_color
            
            if lang_changed:
                self.change_language(dlg.selected_lang)
            if theme_changed:
                self.change_theme_mode(dlg.selected_theme)
            if color_changed:
                self.change_accent_color(dlg.selected_color)

    def load_translators(self, lang_code: str):
        app = QApplication.instance()
        if not app:
            return
            
        # Uninstall old translators if they exist
        if self.qt_translator:
            app.removeTranslator(self.qt_translator)
            self.qt_translator = None
            
        if self.fluent_translator:
            app.removeTranslator(self.fluent_translator)
            self.fluent_translator = None
            
        # Install new Qt standard translator
        qt_path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
        translator = QTranslator()
        # Find e.g. qt_zh_CN.qm
        if translator.load(f"qt_{lang_code}", qt_path):
            app.installTranslator(translator)
            self.qt_translator = translator
            logger.info(f"Loaded Qt standard translator for {lang_code}")
        else:
            # Try loading prefix e.g. qt_zh if qt_zh_CN not found or fallback
            short_lang = lang_code.split('_')[0]
            if translator.load(f"qt_{short_lang}", qt_path):
                app.installTranslator(translator)
                self.qt_translator = translator
                logger.info(f"Loaded Qt standard translator for fallback {short_lang}")
                
        # Install new QFluentWidgets translator
        locale = QLocale(lang_code)
        fluent_trans = FluentTranslator(locale)
        app.installTranslator(fluent_trans)
        self.fluent_translator = fluent_trans
        logger.info(f"Loaded QFluentWidgets translator for {lang_code}")

    def change_language(self, lang_code: str):
        logger.info(f"Switching language to: {lang_code}")
        Trans.CURRENT_LANG = lang_code
        self.settings.language = lang_code
        self.save_settings()
        self.load_translators(lang_code)
        self.retranslate_ui()
        InfoBar.success(Trans.get("msg_op_success"), f"Language switched to: {[name for code, name in Trans.LANGUAGES if code == lang_code][0]}", parent=self)

    def change_theme_mode(self, theme_code: str):
        logger.info(f"Switching theme mode to: {theme_code}")
        self.settings.theme_mode = theme_code
        self.save_settings()
        
        if theme_code == "light":
            setTheme(Theme.LIGHT)
        elif theme_code == "dark":
            setTheme(Theme.DARK)
        else:
            setTheme(Theme.AUTO)
            
        self.update_custom_stylesheet()
        InfoBar.success(Trans.get("msg_op_success"), "Theme updated", parent=self)

    def change_accent_color(self, color_code: str):
        logger.info(f"Switching accent color to: {color_code}")
        self.settings.accent_color = color_code
        self.save_settings()
        setThemeColor(color_code)
        self.update_custom_stylesheet()
        InfoBar.success(Trans.get("msg_op_success"), "Accent color updated", parent=self)

    def retranslate_ui(self):
        self.setWindowTitle(Trans.get("app_title"))
        
        # Retranslate sidebar items
        self.nav_items['home'].setText(Trans.get("nav_home"))
        self.nav_items['general'].setText(Trans.get("nav_general"))
        self.nav_items['optimization'].setText(Trans.get("nav_optimization"))
        self.nav_items['gpu_oc'].setText(Trans.get("nav_gpu_oc"))
        self.nav_items['tools'].setText(Trans.get("nav_tools"))
        self.nav_items['voice_changer'].setText(Trans.get("nav_voice_changer"))
        self.nav_items['crosshair'].setText("屏幕准星" if Trans.CURRENT_LANG == "zh_CN" else "Crosshair")
        self.nav_items['quick_chat'].setText("快捷发言" if Trans.CURRENT_LANG == "zh_CN" else "Quick Chat")
        self.nav_items['startup'].setText(Trans.get("nav_startup"))
        self.nav_items['about'].setText(Trans.get("nav_about"))
        
        if hasattr(self, 'palette_btn'):
            self.palette_btn.setText("主题与语言" if Trans.CURRENT_LANG == "zh_CN" else "Theme & Language")
            
        # Retranslate all child pages
        self.home_page.retranslate_ui()
        self.general_page.retranslate_ui()
        self.optimization_page.retranslate_ui()
        self.gpu_oc_page.retranslate_ui()
        self.tools_page.retranslate_ui()
        self.voice_changer_page.retranslate_ui()
        self.crosshair_page.retranslate_ui()
        self.quick_chat_page.retranslate_ui()
        self.startup_page.retranslate_ui()
        self.about_page.retranslate_ui()
        
        self.update_pending_status()
        self.update_cpu_power_cards_relation()

    def update_cpu_power_cards_relation(self):
        """
        根据专属高性能电源计划的开启状态，级联勾选并置灰锁定核心停车和 EPP 卡片。
        对于 AMD 3D V-Cache 双 CCD 处理器，强制保持启用核心停车（即不禁用）以优化调度。
        """
        if getattr(self, 'optimization_page', None) is None or self.optimization_page.page is None:
            return
        power_plan_checked = self.gpu_page.chk_intel_plan.isChecked() or self.gpu_page.chk_amd_plan.isChecked()
        
        # 多语言支持的提示后缀
        tip = " (已由专属电源计划内置启用并接管)"
        if Trans.CURRENT_LANG == "en_US":
            tip = " (Managed & enabled by custom power plan)"
        elif Trans.CURRENT_LANG == "ja_JP":
            tip = " (専用電源プランによって管理・有効化されています)"
        elif Trans.CURRENT_LANG == "ko_KR":
            tip = " (전용 전원 계획에 의해 관리 및 활성화됨)"
        elif Trans.CURRENT_LANG == "ru_RU":
            tip = " (Управляется и включено выделенной схемой питания)"
        elif Trans.CURRENT_LANG == "de_DE":
            tip = " (Verwaltet und aktiviert durch dedizierten Energiesparplan)"
        elif Trans.CURRENT_LANG == "fr_FR":
            tip = " (Géré et activé par le mode d'alimentation dédié)"
        elif Trans.CURRENT_LANG == "es_ES":
            tip = " (Gestionado y habilitado por el plan de energía dedicado)"

        parking_desc_base = Trans.get("chk_parking_desc")
        epp_desc_base = Trans.get("chk_epp_desc")

        # Check AMD Dual-CCD V-Cache CPU
        from core_commander.core.topology import TopologyEngine
        is_vcache_dual_ccd = TopologyEngine.is_amd_dual_ccd_vcache()

        if is_vcache_dual_ccd:
            vcache_tip = " (已根据 3D V-Cache 架构强制保持启用核心停车，以实现最佳调度)"
            if Trans.CURRENT_LANG == "en_US":
                vcache_tip = " (Core Parking kept enabled for AMD 3D V-Cache dual-CCD optimization)"
            elif Trans.CURRENT_LANG == "ja_JP":
                vcache_tip = " (AMD 3D V-Cache の最適化のためにコアパーキングは有効のままです)"
            elif Trans.CURRENT_LANG == "ko_KR":
                vcache_tip = " (AMD 3D V-Cache 최적화를 위해 코어 파킹이 활성화된 상태로 유지됨)"
            elif Trans.CURRENT_LANG == "ru_RU":
                vcache_tip = " (Парковка ядер оставлена включенной для оптимизации AMD 3D V-Cache)"
            elif Trans.CURRENT_LANG == "de_DE":
                vcache_tip = " (Core Parking bleibt aktiviert für AMD 3D V-Cache-Optimierung)"
            elif Trans.CURRENT_LANG == "fr_FR":
                vcache_tip = " (Le parking des cœurs reste activé pour l'optimisation AMD 3D V-Cache)"
            elif Trans.CURRENT_LANG == "es_ES":
                vcache_tip = " (El estacionamiento de núcleos permanece habilitado para la optimización AMD 3D V-Cache)"

            # 强制不勾选（不禁用核心停车）并置灰
            self.cpu_page.chk_parking.blockSignals(True)
            self.cpu_page.chk_parking.setChecked(False)
            self.cpu_page.chk_parking.blockSignals(False)
            self.cpu_page.chk_parking.setEnabled(False)
            self.cpu_page.chk_parking.setContent(parking_desc_base + vcache_tip)
            self.cpu_page.chk_parking.applied_state = False
            self.cpu_page.chk_parking.update_status(False, is_pending=False)

            # EPP 卡片仍然受专属电源计划控制
            if power_plan_checked:
                self.cpu_page.chk_epp.blockSignals(True)
                self.cpu_page.chk_epp.setChecked(True)
                self.cpu_page.chk_epp.blockSignals(False)
                self.cpu_page.chk_epp.setEnabled(False)
                self.cpu_page.chk_epp.setContent(epp_desc_base + tip)
                
                plan_applied = (self.gpu_page.chk_intel_plan.applied_state is True) or (self.gpu_page.chk_amd_plan.applied_state is True)
                if plan_applied:
                    self.cpu_page.chk_epp.applied_state = True
                    self.cpu_page.chk_epp.update_status(True, is_pending=False)
                else:
                    self.cpu_page.chk_epp.applied_state = False
                    self.cpu_page.chk_epp.update_status(True, is_pending=True)
            else:
                self.cpu_page.chk_epp.setEnabled(True)
                self.cpu_page.chk_epp.setContent(epp_desc_base)
                self.cpu_page.chk_epp.update_status(
                    self.cpu_page.chk_epp.isChecked(),
                    is_pending=(self.cpu_page.chk_epp.applied_state is not None and self.cpu_page.chk_epp.isChecked() != self.cpu_page.chk_epp.applied_state)
                )
        else:
            if power_plan_checked:
                # 1. 强制勾选并置灰
                self.cpu_page.chk_parking.blockSignals(True)
                self.cpu_page.chk_parking.setChecked(True)
                self.cpu_page.chk_parking.blockSignals(False)
                self.cpu_page.chk_parking.setEnabled(False)
                self.cpu_page.chk_parking.setContent(parking_desc_base + tip)

                self.cpu_page.chk_epp.blockSignals(True)
                self.cpu_page.chk_epp.setChecked(True)
                self.cpu_page.chk_epp.blockSignals(False)
                self.cpu_page.chk_epp.setEnabled(False)
                self.cpu_page.chk_epp.setContent(epp_desc_base + tip)

                # 2. 同步已部署生效状态以避免黄色待生效警告
                plan_applied = (self.gpu_page.chk_intel_plan.applied_state is True) or (self.gpu_page.chk_amd_plan.applied_state is True)
                if plan_applied:
                    self.cpu_page.chk_parking.applied_state = True
                    self.cpu_page.chk_epp.applied_state = True
                    self.cpu_page.chk_parking.update_status(True, is_pending=False)
                    self.cpu_page.chk_epp.update_status(True, is_pending=False)
                else:
                    self.cpu_page.chk_parking.applied_state = False
                    self.cpu_page.chk_epp.applied_state = False
                    self.cpu_page.chk_parking.update_status(True, is_pending=True)
                    self.cpu_page.chk_epp.update_status(True, is_pending=True)
            else:
                # 1. 恢复正常可修改状态与文字
                self.cpu_page.chk_parking.setEnabled(True)
                self.cpu_page.chk_parking.setContent(parking_desc_base)
                
                self.cpu_page.chk_epp.setEnabled(True)
                self.cpu_page.chk_epp.setContent(epp_desc_base)

                # 2. 刷新待生效状态
                self.cpu_page.chk_parking.update_status(
                    self.cpu_page.chk_parking.isChecked(),
                    is_pending=(self.cpu_page.chk_parking.applied_state is not None and self.cpu_page.chk_parking.isChecked() != self.cpu_page.chk_parking.applied_state)
                )
                self.cpu_page.chk_epp.update_status(
                    self.cpu_page.chk_epp.isChecked(),
                    is_pending=(self.cpu_page.chk_epp.applied_state is not None and self.cpu_page.chk_epp.isChecked() != self.cpu_page.chk_epp.applied_state)
                )

    def update_status(self):
        """
        Periodically checks process status to update the UI indicators.
        """
        if self.stackedWidget.currentWidget() == self.home_page:
            self.home_page.update_proc_display()

    def perform_memory_clean(self, silent: bool = False, callback=None):
        """
        Launches the asynchronous memory cleaner worker thread.
        """
        if hasattr(self, 'mem_worker') and self.mem_worker is not None:
            try:
                if self.mem_worker.isRunning():
                    logger.warning("Memory cleaner worker is already running. Skipping new trigger.")
                    return
            except RuntimeError:
                pass

        target_pid = self.target_pid
        logger.info(f"Triggering asynchronous memory clean. Target protected PID: {target_pid}")
        
        self.mem_worker = MemoryCleanerWorker(
            protect_pid=target_pid, 
            custom_whitelist=list(self.settings.custom_whitelist), 
            parent=self
        )
        self.mem_worker.finished_signal.connect(
            lambda success, msg: self.on_mem_clean_finished(success, msg, silent, callback)
        )
        self.mem_worker.finished.connect(self.mem_worker.deleteLater)
        self.mem_worker.start()

    def on_mem_clean_finished(self, success: bool, mode_msg: str, silent: bool, callback=None):
        if callback:
            callback()
            
        if not silent:
            if success:
                logger.info(f"Memory clean success: {mode_msg}")
                InfoBar.success("整理完成", f"物理内存工作集已成功整理: {mode_msg}", parent=self)
            else:
                logger.error(f"Memory clean failed: {mode_msg}")
                InfoBar.error("整理失败", f"物理内存工作集整理失败: {mode_msg}", parent=self)

    def perform_auto_clean(self):
        """
        Periodically scheduled automatic memory cleaner target.
        """
        logger.info("Triggering scheduled auto memory clean...")
        self.perform_memory_clean(silent=True)

    def get_settings_dict(self) -> dict:
        kb_list = [100, 50, 30, 20, 16, 12, 10, 8, 6]
        kb_idx = self.peripheral_page.keyboard_queue_card.comboBox.currentIndex()
        kb_size = kb_list[kb_idx] if 0 <= kb_idx < len(kb_list) else 100
        
        m_list = [100, 50, 30, 20, 16, 12, 10, 8]
        m_idx = self.peripheral_page.mouse_queue_card.comboBox.currentIndex()
        m_size = m_list[m_idx] if 0 <= m_idx < len(m_list) else 100
        
        power_plan_checked = self.gpu_page.chk_intel_plan.isChecked() or self.gpu_page.chk_amd_plan.isChecked()

        PRIO_SEP_VALUES = [2, 20, 21, 22, 24, 25, 26, 36, 37, 38, 40, 41, 42]
        prio_idx = self.cpu_page.win32_prio_card.comboBox.currentIndex()
        prio_val = PRIO_SEP_VALUES[prio_idx] if 0 <= prio_idx < len(PRIO_SEP_VALUES) else 26

        return {
            "enable_global_fse_tweak": self.gpu_page.chk_global_fse.isChecked(),
            "enable_game_fse_tweak": self.gpu_page.chk_game_fse.isChecked(),
            "target_process_path": self.settings.target_process_path,
            "enable_widgets_tweak": self.optimization_page.chk_widgets.isChecked(),
            "enable_sticky_keys_tweak": self.peripheral_page.chk_sticky_keys.isChecked(),
            "enable_startup_delay_tweak": self.optimization_page.chk_startup_delay.isChecked(),
            "enable_menu_delay_tweak": self.optimization_page.chk_menu_delay.isChecked(),
            "enable_settings_sync_tweak": self.privacy_page.chk_settings_sync.isChecked(),
            "enable_dynamic_lighting_tweak": self.peripheral_page.chk_dynamic_lighting.isChecked(),
            "enable_gpu_msi_tweak": self.gpu_page.chk_gpu_msi.isChecked(),
            "enable_xbox_save_tweak": self.privacy_page.chk_xbox_save.isChecked(),
            "enable_store_auto_update_tweak": self.privacy_page.chk_store_auto_update.isChecked(),
            "enable_vulnerable_driver_blocklist_tweak": self.cpu_page.chk_vulnerable_driver_blocklist.isChecked(),
            "enable_prevent_device_encryption_tweak": self.optimization_page.chk_prevent_device_encryption.isChecked(),
            "enable_spotlight_tweak": self.optimization_page.chk_spotlight.isChecked(),
            "enable_hard_working_set": self.memory_page.chk_hard_working_set.isChecked(),
            "enable_net_imod_tweak": self.network_page.chk_net_imod.isChecked(),
            "enable_net_bindings_tweak": self.network_page.chk_net_bindings.isChecked(),
            "enable_wifi_tweak": self.network_page.chk_wifi_tweak.isChecked(),
            "enable_game_gpu_preference_tweak": self.gpu_page.chk_game_gpu_preference.isChecked(),
            "enable_irq_affinity_tweak": self.gpu_page.chk_irq_affinity.isChecked(),
            "enable_power_throttling_tweak": self.cpu_page.chk_power_throttling.isChecked(),
            "enable_tcp_bbr_tweak": self.network_page.chk_tcp_bbr.isChecked(),
            "enable_eee_tweak": self.network_page.chk_eee.isChecked(),
            "enable_web_search_tweak": self.privacy_page.chk_web_search.isChecked(),

            "enable_telemetry_tasks_tweak": self.privacy_page.chk_telemetry_tasks.isChecked(),
            "rate_limiter_pulse_duration": (max(0.01, min(float(self.general_page.rl_pulse_input.text()), 60.0)) * 1000.0) if hasattr(self.general_page, 'rl_pulse_input') and self.general_page.rl_pulse_input.text() else 3000.0,
            "rate_limiter_pulse_delay": (max(0.0, min(float(self.general_page.rl_pulse_delay_input.text()), 10.0)) * 1000.0) if hasattr(self.general_page, "rl_pulse_delay_input") and self.general_page.rl_pulse_delay_input.text() else 0.0,

            "enable_extreme_debloat_tweak": self.privacy_page.chk_extreme_debloat.isChecked(),
            "enable_prefetcher_tweak": self.memory_page.chk_prefetcher.isChecked(),

            "target_process_name": self.target_name,
            "win32_prio_sep": prio_val,
            "disable_hpet": self.cpu_page.chk_hpet.isChecked(),
            "keyboard_queue_size": kb_size,
            "mouse_queue_size": m_size,
            "enable_dwm_tweak": self.cpu_page.chk_dwm.isChecked(),
            "disable_useless_services": self.privacy_page.chk_services.isChecked(),
            "disable_wsearch_tweak": self.privacy_page.chk_wsearch.isChecked(),
            "enable_custom_power_plan": power_plan_checked,
            "enable_ram_optimization": self.memory_page.chk_ram_opt.isChecked(),
            "enable_nvme_optimization": self.memory_page.chk_nvme_opt.isChecked(),
            "enable_gpu_optimization": self.gpu_page.chk_gpu_opt.isChecked(),
            "disable_spectre_meltdown": self.privacy_page.chk_spectre.isChecked(),
            "disable_gpu_preemption": self.gpu_page.chk_preemption.isChecked(),
            "disable_gamedvr": self.privacy_page.chk_gamedvr.isChecked(),
            "enable_ultimate_network_tweak": self.network_page.chk_ult_net.isChecked(),
            "enable_usb_low_latency_tweak": self.peripheral_page.chk_usb_lat.isChecked(),
            "enable_dpc_latency_tweak": self.cpu_page.chk_dpc.isChecked(),
            "enable_dwm_super_wet_tweak": self.gpu_page.chk_dwm_wet.isChecked(),
            "keyboard_repeat_delay_level": self.peripheral_page.keyboard_repeat_rate_card.comboBox.currentIndex(),
            "enable_timer_resolution_tweak": self.cpu_page.chk_timer_res.isChecked(),
            "enable_usb_imod_tweak": self.peripheral_page.chk_imod.isChecked(),
            "disable_pcipower": self.gpu_page.chk_pcipower.isChecked(),
            "enable_directx_tweaks": self.gpu_page.chk_directx.isChecked(),
            "enable_device_power_tweak": self.privacy_page.chk_dev_power.isChecked(),
            "enable_dns_tweak": self.network_page.chk_dns.isChecked(),
            "enable_consult_interests_tweak": self.optimization_page.chk_consult_interests.isChecked(),
            "enable_tips_suggestions_tweak": self.optimization_page.chk_tips_suggestions.isChecked(),
            "enable_desktop_heap_tweak": self.privacy_page.chk_desktop_heap.isChecked(),
            "enable_uac_tweak": self.privacy_page.chk_uac.isChecked(),
            "enable_download_maps_tweak": self.privacy_page.chk_download_maps.isChecked(),
            "enable_bg_apps_tweak": self.privacy_page.chk_bg_apps.isChecked(),
            "enable_map_updates_tweak": self.privacy_page.chk_map_updates.isChecked(),
            "enable_autoshare_tweak": self.privacy_page.chk_autoshare.isChecked(),
            "enable_autorun_tweak": self.privacy_page.chk_autorun.isChecked(),
            "enable_mouse_latency_tweak": self.peripheral_page.chk_mouse_lat.isChecked(),
            "enable_config_alloc_tweak": self.memory_page.chk_config_alloc.isChecked(),
            "enable_gpu_firmware_tweak": self.gpu_page.chk_gpu_firmware.isChecked(),
            "disable_memory_compression": self.memory_page.chk_memory_comp.isChecked(),
            "enable_naraka_priority": self.cpu_page.chk_naraka_priority.isChecked(),
            "enable_gpu_pstate_tweak": self.gpu_page.chk_gpu_pstate.isChecked(),
            "enable_network_tweak": self.network_page.chk_network.isChecked(),
            "disable_windows_visual_effects": self.optimization_page.chk_visual_effects.isChecked(),
            "disable_windows_transparency": self.optimization_page.chk_transparency.isChecked(),
            "disable_copilot": self.privacy_page.chk_copilot.isChecked(),
            "disable_security_notifications": self.privacy_page.chk_security_notifications.isChecked(),
            "disable_defender": self.privacy_page.chk_defender.isChecked(),
            "disable_smartscreen": self.privacy_page.chk_smartscreen.isChecked(),
            "disable_firewall": self.privacy_page.chk_firewall.isChecked(),
            "enable_driver_priority_tweak": self.cpu_page.chk_driver_prio.isChecked(),
            "disable_hyperv_virtualization": self.privacy_page.chk_hyperv.isChecked(),
            "disable_hags": self.gpu_page.chk_hags.isChecked(),
            "enable_gpu_irq_tweak": self.gpu_page.chk_gpu_irq.isChecked(),
            "enable_nvidia_nip": self.gpu_page.chk_gpu_nip.isChecked(),
            "enable_network_msi_tweak": self.network_page.chk_network_msi.isChecked(),
            "enable_storage_msi_tweak": self.memory_page.chk_storage_msi.isChecked(),
            "enable_dwm_presentation_tweak": self.gpu_page.chk_dwm_presentation.isChecked(),
            "enable_client_priority_demote": self.cpu_page.chk_client_priority_demote.isChecked(),
            "enable_core_parking": self.cpu_page.chk_parking.isChecked(),
            "enable_epp_max": self.cpu_page.chk_epp.isChecked(),
        }

    def set_settings_apply_buttons_enabled(self, enabled: bool):
        for page in [self.general_page, self.cpu_page, self.peripheral_page, 
                     self.gpu_page, self.memory_page, self.privacy_page, 
                     self.network_page, self.tools_page]:
            if hasattr(page, 'apply_btn'):
                page.apply_btn.setEnabled(enabled)

    def apply_system_tweaks(self, callback=None):
        """
        Applies only system registry/service tweaks asynchronously.
        """
        do_backup = True

        self.save_settings()
        
        self.log_overlay.start_loading(
            title="正在部署系统内核与进程调度策略...",
            subtitle="系统正在后台写入所选的注册表键值与核心服务配置，请稍候。"
        )
        
        settings_dict = self.get_settings_dict()
        pending_keys = self.get_pending_keys()
        
        # Determine if reboot is required based on pending keys before applying them
        IMMEDIATE_KEYS = self.IMMEDIATE_KEYS
        reboot_required = any(key not in IMMEDIATE_KEYS for key in pending_keys)
        logger.info(f"Applying tweaks: pending_keys={pending_keys}, reboot_required={reboot_required}")
        
        self.navigationInterface.setEnabled(False)
        self.set_settings_apply_buttons_enabled(False)
        self.home_page.apply_btn.setEnabled(False)
        
        self.tweak_thread = SystemTweakThread(settings_dict, self.cpu_vendor, self.gpu_vendor, do_backup=do_backup, pending_keys=pending_keys, parent=self)
        self.tweak_thread.log_signal.connect(self.log_overlay.append_log)
        
        def on_tweak_finished(success, msg):
            self.navigationInterface.setEnabled(True)
            self.home_page.apply_btn.setEnabled(True)
            self.update_pending_status()
            
            if success:
                # 物理工作集强制锁死
                if self.memory_page.chk_hard_working_set.isChecked() and self.current_pid:
                    try:
                        SystemTweaksService.lock_process_memory(self.current_pid, 2048, 8192)
                    except Exception as mem_err:
                        logger.error(f"Failed to lock working set for target process: {str(mem_err)}")

                self.detect_and_sync_system_states()
                # Clear preset highlights
                for page in [self.general_page, self.cpu_page, self.peripheral_page, 
                             self.gpu_page, self.memory_page, self.privacy_page, 
                             self.network_page, self.tools_page]:
                    if hasattr(page, 'presetPanel'):
                        page.presetPanel.highlight_preset("")
                self.log_overlay.finish_loading(True, "系统配置与调度参数已成功写入生效。", reboot_required=reboot_required)
                InfoBar.success("部署策略成功", "系统配置与性能调优选项已成功部署并生效", parent=self)
                if callback:
                    callback(True, msg)
            else:
                self.log_overlay.finish_loading(False, msg)
                MessageBox("部署策略失败", msg, self).exec()
                if callback:
                    callback(False, msg)
                    
        self.tweak_thread.finished_signal.connect(on_tweak_finished)
        self.tweak_thread.finished.connect(self.tweak_thread.deleteLater)
        self.tweak_thread.start()

    def apply_optimization(self, home_page: HomePage):
        """
        Collects core and thread selections, whitelists, and sets system optimizations.
        """
        from core_commander.core.guard import check_apply_optimization_hook, get_decrypted_tweak_payload
        if not check_apply_optimization_hook():
            return
            
        payload = get_decrypted_tweak_payload()
        if payload.get("priority_separation", 26) == 0:
            MessageBox("运行环境异常", "系统检测到当前的授权信息与底层硬件指纹（HWID）不匹配！\n核心优化参数解密失败，继续强行应用可能会导致系统出现严重卡顿或不稳定，系统已自动拦截本次操作。", self).exec()
            return
            
        if getattr(self, 'is_optimized', False):
            self.cancel_optimization(home_page)
            return

        pid = self.target_pid
        
        if not pid or not psutil.pid_exists(pid):
            logger.warning(f"Attempted to optimize invalid process: {self.target_name}")
            InfoBar.error("进程未运行", f"进程 {self.target_name} 当前未在后台运行，请先启动该进程", parent=self)
            return

        # Fetch priority threads
        pri_list = []
        p1 = home_page.combo_primary1.currentData()
        p2 = home_page.combo_primary2.currentData()
        if p1 != -1: 
            pri_list.append(p1)
        if p2 != -1: 
            pri_list.append(p2)
        pri_list = list(set(pri_list))

        # Build affinity mask from logical thread selections
        aff = []
        for btn in home_page.all_core_buttons:
            if btn.isChecked():
                aff.extend(btn.threads)
        
        # Enforce priority thread inclusions
        if pri_list:
            for tid in pri_list:
                if tid not in aff:
                    aff.append(tid)
            aff.sort()

        if not aff:
            logger.warning("Optimization denied: No physical cores selected.")
            InfoBar.warning("参数配置错误", "请至少分配一个可用的处理器核心作为处理器关联掩码。", parent=self)
            return

        # Keep current cache
        self.current_pid = pid
        self.current_mask = aff
        
        # Save settings first
        self.save_settings()
        
        # Pre-enable Windows Firewall and allow local rules merging if rate limiter is configured
        if self.settings.enable_rate_limiter:
            logger.info("Applying optimization: pre-enabling Windows Firewall and local rules merging for rate limiter.")
            try:
                import subprocess
                subprocess.Popen(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command", "Set-NetFirewallProfile -Profile Domain,Private,Public -Enabled True -AllowLocalFirewallRules True"],
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
            except Exception as e:
                logger.error(f"Failed to pre-enable firewall: {e}")
        
        # Disable apply button to prevent double-clicks
        home_page.apply_btn.setEnabled(False)
        home_page.apply_btn.setText(Trans.get("strategy_applying"))
        
        # Apply QoS policy for game exe (instantaneous system call)
        try:
            proc_name = psutil.Process(pid).exe()
            self.optimized_game_path = proc_name
            SystemTweaksService.apply_qos_policy(proc_name)
            logger.info(f"Game QoS policy applied: {os.path.basename(proc_name)}")
            
            # Pre-create firewall rules for rate limiter in the background
            try:
                from core_commander.core.tweaks.throttler import NetworkThrottlerService
                NetworkThrottlerService.setup_qos_nla_bypass()
                NetworkThrottlerService.pre_create_rules(pid, os.path.basename(proc_name))
            except Exception as e:
                logger.warning(f"Failed to initiate rate limiter rules pre-creation: {e}")
        except Exception as qos_err:
            logger.warning(f"Game QoS policy application failed: {str(qos_err)}")
        
        enable_iso = self.optimization_page.chk_iso.isChecked() if self.optimization_page.page is not None else self.settings.enable_isolation
        custom_wl = list(self.settings.custom_whitelist)
        enable_parking = self.cpu_page.chk_parking.isChecked() if self.optimization_page.page is not None else self.settings.enable_core_parking
        enable_epp = self.cpu_page.chk_epp.isChecked() if self.optimization_page.page is not None else self.settings.enable_epp_max
        enable_network = self.network_page.chk_network.isChecked() if self.optimization_page.page is not None else self.settings.enable_network_tweak
        enable_child = self.cpu_page.chk_child.isChecked() if self.optimization_page.page is not None else self.settings.enable_child_optimization
        enable_wifi = self.network_page.chk_wifi_tweak.isChecked() if self.optimization_page.page is not None else self.settings.enable_wifi_tweak
        
        self.worker = OptimizationWorker(
            pid, pri_list, aff, enable_iso, self.topology, custom_wl,
            enable_parking, enable_epp, enable_network, enable_child,
            enable_wifi_tweak=enable_wifi, parent=self
        )
        self.worker.finished_signal.connect(self.on_optimization_finished)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()

    def _revert_system_settings_and_timers(self):
        """
        Helper to revert CPU hardware settings, network optimizations, QoS policies,
        power schemes, background affinities, and resume the memory clean timer.
        """
        # Stop WinEventHook if running
        self._stop_win_event_hook()

        # Restore auto memory clean timer if enabled
        if self.settings.enable_auto_mem_clean:
            interval_min = self.settings.mem_clean_interval
            self.mem_timer.start(interval_min * 60 * 1000)
            logger.info("Auto memory clean timer resumed.")
            
        # Revert hardware tweaks, power plan, and QoS/AppCompat policies
        try:
            logger.info("Reverting CPU hardware settings, network optimizations, QoS policy, and power scheme.")
            PowerService.tune_cpu_hardware_parameters(self.settings.enable_core_parking, self.settings.enable_epp_max)
            PowerService.optimize_system_network_latency(self.settings.enable_network_tweak)
            PowerService.restore_original_power_scheme()
            
            # Restore WLAN auto config if wifi tweak was enabled
            try:
                import subprocess
                subprocess.run(["netsh", "wlan", "set", "autoconfig", "enabled=yes", "interface=*"], capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
                logger.info("Restored WLAN autoconfig state to enabled (all interfaces).")
            except Exception as wifi_err:
                logger.error(f"Failed to restore WLAN autoconfig state: {wifi_err}")
            if self.optimized_game_path:
                SystemTweaksService.remove_qos_policy(self.optimized_game_path)
                self.optimized_game_path = ""
            
            # Restore firewall back to disabled state if config specifies to disable it
            if self.settings.disable_firewall:
                logger.info("Reverting firewall back to disabled state according to system tweaks config.")
                try:
                    SystemTweaksService.apply_firewall(True)
                except Exception as fw_err:
                    logger.error(f"Failed to revert firewall to disabled state: {fw_err}")
            # Restore background process affinities
            try:
                logger.info("Restoring background process affinities back to normal.")
                custom_wl = list(self.settings.custom_whitelist)
                ProcessIsolationService.restore_all_processes(custom_whitelist=custom_wl)
            except Exception as ex:
                logger.error(f"Failed to restore background process affinities: {str(ex)}")
        except Exception as e:
            logger.error(f"Failed to revert system tweaks and power scheme: {str(e)}")

    def _start_win_event_hook(self):
        if os.name == 'nt' and self.win_event_proc and not self.win_event_hook:
            try:
                self.win_event_hook = ctypes.windll.user32.SetWinEventHook(
                    0x0003, # EVENT_SYSTEM_FOREGROUND
                    0x0003, # EVENT_SYSTEM_FOREGROUND
                    None,
                    self.win_event_proc,
                    0,
                    0,
                    0 # WINEVENT_OUTOFCONTEXT
                )
                logger.info("Successfully registered EVENT_SYSTEM_FOREGROUND WinEventHook.")
            except Exception as e:
                logger.error(f"Failed to register WinEventHook: {e}")

    def _stop_win_event_hook(self):
        if os.name == 'nt' and self.win_event_hook:
            try:
                ctypes.windll.user32.UnhookWinEvent(self.win_event_hook)
                logger.info("Successfully unregistered WinEventHook.")
            except Exception as e:
                logger.error(f"Failed to unregister WinEventHook: {e}")
            finally:
                self.win_event_hook = None

    def cancel_optimization(self, home_page: HomePage):
        """
        Cancels active process optimizations and restores process and its children to defaults.
        """
        self.watchdog.stop()
        self._stop_win_event_hook()
        self.is_optimized = False
        
        # 释放物理工作集锁死
        if self.current_pid:
            try:
                SystemTweaksService.lock_process_memory(self.current_pid, -1, -1)
            except Exception as mem_err:
                logger.warning(f"Failed to release process memory lock: {str(mem_err)}")

        self._revert_system_settings_and_timers()
            
        pid = self.target_pid
        if not pid or not psutil.pid_exists(pid):
            home_page.apply_btn.setText(Trans.get("strategy_apply_btn"))
            InfoBar.success("调度已取消", "进程已不存在，调度监控与系统参数已还原", parent=self)
            return
            
        try:
            p = psutil.Process(pid)
            try:
                p.nice(psutil.NORMAL_PRIORITY_CLASS)
            except Exception as e:
                logger.warning(f"Failed to reset priority for target process: {str(e)}")
                
            all_threads = []
            for c in self.topology:
                all_threads.extend(c.get('threads', []))
                
            if all_threads:
                try:
                    p.cpu_affinity(all_threads)
                except Exception as e:
                    logger.warning(f"Failed to reset CPU affinity for target process: {str(e)}")
                    
            if self.cpu_page.chk_child.isChecked():
                for child in p.children(recursive=True):
                    try:
                        child.nice(psutil.NORMAL_PRIORITY_CLASS)
                        child.cpu_affinity(all_threads)
                    except Exception:  # nosec
                        continue
                        
            InfoBar.success("性能调度已取消", "已成功恢复目标进程及其子进程的 CPU 亲和性与优先级。", parent=self)
        except Exception as e:
            logger.error(f"Error cancelling optimization: {str(e)}")
            InfoBar.error("取消调度失败", f"恢复进程状态时发生错误: {str(e)}", parent=self)
            
        home_page.apply_btn.setText(Trans.get("strategy_apply_btn"))

    def on_optimization_finished(self, success: bool, msg: str):
        self.navigationInterface.setEnabled(True)
        self.set_settings_apply_buttons_enabled(True)
        self.home_page.apply_btn.setEnabled(True)
        
        if success:
            self.is_optimized = True
            self.detect_and_sync_system_states()
            logger.info(f"Optimization successfully applied: {msg}")
            
            # Update home page apply button text to cancel button
            if getattr(self, 'is_auto_optimizing', False):
                self.home_page.apply_btn.setText("自动优化中...")
                self.home_page.apply_btn.setEnabled(False)
            else:
                self.home_page.apply_btn.setText(Trans.get("strategy_cancel_btn"))
                self.home_page.apply_btn.setEnabled(True)
            
            # Show success toast
            InfoBar.success("性能调度部署成功", msg, parent=self)
            
            # Start persistent watchdog if configured
            enable_watchdog = self.optimization_page.chk_dog.isChecked() if self.optimization_page.page is not None else self.settings.enable_watchdog
            if enable_watchdog:
                logger.info("Starting background configuration watchdog.")
                self.watchdog.start(4000)
                self._start_win_event_hook()
            else:
                self.watchdog.stop()
                self._stop_win_event_hook()
            
            # Suspend auto memory clean timer during optimization to avoid game stutters
            if self.mem_timer.isActive():
                self.mem_timer.stop()
                logger.info("Auto memory clean timer temporarily suspended during optimization to protect game frame pacing.")
        else:
            self.is_optimized = False
            logger.error(f"Optimization failed: {msg}")
            self.home_page.apply_btn.setText(Trans.get("strategy_apply_btn"))
            InfoBar.error("性能调度部署失败", msg, parent=self)

    def run_watchdog(self):
        """
        Periodically checks priority and affinity attributes of target process and background threads.
        """
        if not self.current_pid: 
            return
            
        try:
            # Check target process running status
            if not psutil.pid_exists(self.current_pid):
                logger.info("Watchdog detected target process has exited. Stopping watchdog.")
                self.is_optimized = False
                self.home_page.apply_btn.setText(Trans.get("strategy_apply_btn"))
                self.watchdog.stop()
                self._revert_system_settings_and_timers()
                return

            p = psutil.Process(self.current_pid)
            cur = p.cpu_affinity()
            
            # Re-enforce target core affinity and priority if modified by system scheduling
            try:
                if set(cur) != set(self.current_mask):
                    logger.info(f"Watchdog resetting affinity for PID {self.current_pid} from {cur} back to {self.current_mask}.")
                    p.cpu_affinity(self.current_mask)
                if p.nice() != psutil.HIGH_PRIORITY_CLASS:
                    logger.info("Watchdog resetting priority to High.")
                    p.nice(psutil.HIGH_PRIORITY_CLASS)
            except Exception as e: 
                logger.debug(f"Watchdog sub-task execution warning: {str(e)}")
            
            # Fast check: if user alt-tabbed to an isolated process, restore its affinity immediately to prevent UI pause/lag
            try:
                ProcessIsolationService.restore_foreground_process()
            except Exception as e:
                logger.debug(f"Failed to restore foreground process affinity in watchdog: {e}")

            # Increment watchdog counter for background tasks scheduling (period: 8 cycles / ~32 seconds)
            self.watchdog_counter = (self.watchdog_counter + 1) % 8

            # Re-enforce Thread Ideal Processor and Affinity for preferred cores (async to prevent GUI micro-stutters)
            try:
                p1 = self.home_page.combo_primary1.currentData()
                p2 = self.home_page.combo_primary2.currentData()
                valid_ids = []
                for t in [p1, p2]:
                    if t is not None and t >= 0 and t not in valid_ids:
                        valid_ids.append(t)
                if valid_ids:
                    import threading
                    def async_ideal_processor():
                        try:
                            THREAD_SET_INFORMATION = 0x0020
                            THREAD_QUERY_INFORMATION = 0x0040
                            
                            if psutil.pid_exists(self.current_pid):
                                proc_obj = psutil.Process(self.current_pid)
                                threads = proc_obj.threads()
                                if threads:
                                    threads.sort(key=lambda t: t.user_time + t.system_time, reverse=True)
                                    for idx, target_core in enumerate(valid_ids):
                                        if idx < len(threads):
                                            thread_info = threads[idx]
                                            h_thread = kernel32.OpenThread(THREAD_SET_INFORMATION | THREAD_QUERY_INFORMATION, False, thread_info.id)
                                            if h_thread:
                                                try:
                                                    mask = 1 << target_core
                                                    kernel32.SetThreadAffinityMask(h_thread, mask)
                                                    kernel32.SetThreadIdealProcessor(h_thread, target_core)
                                                except Exception:  # nosec
                                                    pass
                                                finally:
                                                    kernel32.CloseHandle(h_thread)
                        except Exception as ex:
                            logger.debug(f"Async watchdog Thread Affinity/Ideal Processor adjustment failed: {str(ex)}")
                    threading.Thread(target=async_ideal_processor, daemon=True).start()
            except Exception as e:
                logger.debug(f"Failed to initiate watchdog Thread Ideal Processor routing: {str(e)}")
            
            enable_iso = self.optimization_page.chk_iso.isChecked() if self.optimization_page.page is not None else self.settings.enable_isolation
            if enable_iso and self.watchdog_counter == 4:
                import threading
                custom_wl = list(self.settings.custom_whitelist)
                def async_isolate():
                    try:
                        pool = ProcessIsolationService.calculate_isolation_pool(self.topology)
                        ProcessIsolationService.isolate_background_processes(
                            self.current_pid, pool, custom_wl
                        )
                    except Exception as ex:
                        logger.debug(f"Async watchdog background process isolation error: {str(ex)}")
                threading.Thread(target=async_isolate, daemon=True).start()
        except psutil.NoSuchProcess:
            logger.info("Watchdog detected target process has exited during check. Stopping watchdog.")
            self.is_optimized = False
            self.home_page.apply_btn.setText(Trans.get("strategy_apply_btn"))
            self.watchdog.stop()
            self._revert_system_settings_and_timers()
        except Exception as e: 
            logger.error(f"Watchdog exception encountered: {str(e)}")
            self.is_optimized = False
            self.home_page.apply_btn.setText(Trans.get("strategy_apply_btn"))
            self.watchdog.stop()
            self._revert_system_settings_and_timers()

    def reapply_preferred_cores(self):
        """
        Immediately reapplies the preferred core thread affinity and ideal processor settings
        if optimization is currently active.
        """
        if not self.is_optimized or not self.current_pid:
            return
            
        try:
            p1 = self.home_page.combo_primary1.currentData()
            p2 = self.home_page.combo_primary2.currentData()
            valid_ids = []
            for t in [p1, p2]:
                if t is not None and t >= 0 and t not in valid_ids:
                    valid_ids.append(t)
            if valid_ids:
                import threading
                def async_ideal_processor():
                    try:
                        THREAD_SET_INFORMATION = 0x0020
                        THREAD_QUERY_INFORMATION = 0x0040
                        
                        if psutil.pid_exists(self.current_pid):
                            proc_obj = psutil.Process(self.current_pid)
                            threads = proc_obj.threads()
                            if threads:
                                threads.sort(key=lambda t: t.user_time + t.system_time, reverse=True)
                                for idx, target_core in enumerate(valid_ids):
                                    if idx < len(threads):
                                        thread_info = threads[idx]
                                        h_thread = kernel32.OpenThread(THREAD_SET_INFORMATION | THREAD_QUERY_INFORMATION, False, thread_info.id)
                                        if h_thread:
                                            try:
                                                mask = 1 << target_core
                                                kernel32.SetThreadAffinityMask(h_thread, mask)
                                                kernel32.SetThreadIdealProcessor(h_thread, target_core)
                                            except Exception:  # nosec
                                                pass
                                            finally:
                                                kernel32.CloseHandle(h_thread)
                    except Exception as ex:
                        logger.debug(f"Immediate preferred cores adjustment failed: {str(ex)}")
                threading.Thread(target=async_ideal_processor, daemon=True).start()
        except Exception as e:
            logger.debug(f"Failed to initiate immediate preferred cores routing: {str(e)}")

    def load_settings(self):
        """
        Loads user configurations at startup.
        """
        self.is_loading_settings = True
        try:
            Trans.CURRENT_LANG = self.settings.language
            self.load_translators(Trans.CURRENT_LANG)
            
            last_proc = self.settings.target_process_name
            if last_proc:
                self.target_name = last_proc
                self.home_page.update_proc_display()
                if not self.settings.target_process_path:
                    def resolve_path_async():
                        try:
                            from core_commander.utils import find_game_path
                            path = find_game_path(last_proc)
                            if path:
                                logger.info(f"Proactively resolved target process path asynchronously: {path}")
                                self.settings.target_process_path = path
                        except Exception as ex:
                            logger.warning(f"Failed to resolve target process path asynchronously: {str(ex)}")
                    
                    import threading
                    threading.Thread(target=resolve_path_async, daemon=True).start()

            self.home_page.combo_primary1.setCurrentIndex(self.settings.p1_idx)
            self.home_page.combo_primary2.setCurrentIndex(self.settings.p2_idx)
            
            self.home_page.chk_mem_auto.setChecked(self.settings.enable_auto_mem_clean)
            self.home_page.spin_mem_interval.setValue(self.settings.mem_clean_interval)

            # Load CPU affinity mask from settings
            if self.settings.has_saved_affinity:
                saved_threads = self.settings.affinity_mask_threads
                for section in self.home_page.sections:
                    section['checkbox'].blockSignals(True)
                for btn in self.home_page.all_core_buttons:
                    btn.blockSignals(True)
                    
                for btn in self.home_page.all_core_buttons:
                    is_checked = any(t in saved_threads for t in btn.threads)
                    btn.setChecked(is_checked)
                    
                for btn in self.home_page.all_core_buttons:
                    btn.blockSignals(False)
                    
                self.home_page.update_chk_all_states()
                for section in self.home_page.sections:
                    section['checkbox'].blockSignals(False)
            else:
                self.home_page.update_chk_all_states()
                
            self.home_page.update_primary()
            
            # Load settings for pages if they are already created (i.e. not None)
            if self.general_page.page is not None:
                self.load_page_settings("generalPage", self.general_page.page)
            if self.optimization_page.page is not None:
                self.load_page_settings("optimizationPage", self.optimization_page.page)
            if self.tools_page.page is not None:
                self.load_page_settings("toolsPage", self.tools_page.page)
                
        except Exception as e:
            logger.error(f"Error loading configurations: {str(e)}")
        finally:
            self.is_loading_settings = False
            self.update_auto_watchdog()
            self.register_global_hotkey()
            self.update_fps_collector_lifecycle()

    def load_page_settings(self, page_name, page):
        self.is_loading_settings = True
        try:
            if page_name in ("generalPage", "general"):
                page.rl_switch.setChecked(self.settings.enable_rate_limiter)
                page.rl_download_input.setText(str(self.settings.rate_limiter_download_value))
                mode_val = self.settings.rate_limiter_mode
                if mode_val == "toggle":
                    page.rl_mode_combo.setCurrentIndex(0)
                elif mode_val == "pulse":
                    page.rl_mode_combo.setCurrentIndex(2)
                else:
                    page.rl_mode_combo.setCurrentIndex(1)
                if hasattr(page, 'rl_pulse_input'):
                    page.rl_pulse_input.setText(str(self.settings.rate_limiter_pulse_duration / 1000.0))
                type_idx = 0 if self.settings.rate_limiter_type == "qos" else 1
                page.rl_type_combo.setCurrentIndex(type_idx)
                unit_map = {"KB/s": 0, "Mbps": 1, "ms": 2}
                unit_idx = unit_map.get(self.settings.rate_limiter_unit, 0)
                page.rl_unit_combo.setCurrentIndex(unit_idx)
                page.update_rate_limiter_controls_state()
                if hasattr(page, 'btn_ocr_hotkey'):
                    page.btn_ocr_hotkey.setText(self.settings.ocr_hotkey)
                page.chk_show_all_cpu.setChecked(self.settings.show_all_cpu_options)
                page.chk_osd.setChecked(self.settings.enable_fps_overlay)
                page.switch_osd_lock.setChecked(self.settings.fps_overlay_lock)
                page.slider_osd_font.setValue(self.settings.fps_overlay_font_size)
                page.lbl_osd_font_val.setText(f"{self.settings.fps_overlay_font_size} px")
                page.chk_osd_cpu_gpu.setChecked(self.settings.fps_overlay_show_cpu_gpu)
                page.chk_osd_ram.setChecked(self.settings.fps_overlay_show_ram)
                page.chk_osd_frametime.setChecked(self.settings.fps_overlay_show_frametime)
                page.btn_osd_hotkey.setText(self.settings.fps_overlay_hotkey)
                page.spin_osd_x.setValue(self.settings.fps_overlay_pos_x)
                page.spin_osd_y.setValue(self.settings.fps_overlay_pos_y)
                
                prio_val = self.settings.win32_prio_sep
                PRIO_SEP_VALUES = [2, 20, 21, 22, 24, 25, 26, 36, 37, 38, 40, 41, 42]
                try:
                    prio_idx = PRIO_SEP_VALUES.index(prio_val)
                except ValueError:
                    prio_idx = 6 # 26
                page.win32_prio_card.setCurrentIndex(prio_idx)
                
                kb_list = [100, 50, 30, 20, 16, 12, 10, 8, 6]
                try:
                    kb_idx = kb_list.index(self.settings.keyboard_queue_size)
                except ValueError:
                    kb_idx = 0
                page.keyboard_queue_card.setCurrentIndex(kb_idx)
                
                m_list = [100, 50, 30, 20, 16, 12, 10, 8]
                try:
                    m_idx = m_list.index(self.settings.mouse_queue_size)
                except ValueError:
                    m_idx = 0
                page.mouse_queue_card.setCurrentIndex(m_idx)
                
                page.keyboard_repeat_rate_card.setCurrentIndex(self.settings.keyboard_repeat_delay_level)
                
            elif page_name in ("optimizationPage", "optimization"):
                page.chk_widgets.setChecked(self.settings.enable_widgets_tweak)
                page.chk_startup_delay.setChecked(self.settings.enable_startup_delay_tweak)
                page.chk_menu_delay.setChecked(self.settings.enable_menu_delay_tweak)
                page.chk_prevent_device_encryption.setChecked(self.settings.enable_prevent_device_encryption_tweak)
                page.chk_spotlight.setChecked(self.settings.enable_spotlight_tweak)
                page.chk_iso.setChecked(self.settings.enable_isolation)
                page.chk_dog.setChecked(self.settings.enable_watchdog)
                page.chk_visual_effects.setChecked(self.settings.disable_windows_visual_effects)
                page.chk_transparency.setChecked(self.settings.disable_windows_transparency)
                page.chk_consult_interests.setChecked(self.settings.enable_consult_interests_tweak)
                page.chk_tips_suggestions.setChecked(self.settings.enable_tips_suggestions_tweak)
                
                page.chk_global_fse.setChecked(self.settings.enable_global_fse_tweak)
                page.chk_game_fse.setChecked(self.settings.enable_game_fse_tweak)
                page.chk_sticky_keys.setChecked(self.settings.enable_sticky_keys_tweak)
                page.chk_settings_sync.setChecked(self.settings.enable_settings_sync_tweak)
                page.chk_dynamic_lighting.setChecked(self.settings.enable_dynamic_lighting_tweak)
                page.chk_gpu_msi.setChecked(self.settings.enable_gpu_msi_tweak)
                page.chk_network_msi.setChecked(self.settings.enable_network_msi_tweak)
                page.chk_storage_msi.setChecked(self.settings.enable_storage_msi_tweak)
                page.chk_dwm_presentation.setChecked(self.settings.enable_dwm_presentation_tweak)
                page.chk_client_priority_demote.setChecked(self.settings.enable_client_priority_demote)
                page.chk_xbox_save.setChecked(self.settings.enable_xbox_save_tweak)
                page.chk_store_auto_update.setChecked(self.settings.enable_store_auto_update_tweak)
                page.chk_vulnerable_driver_blocklist.setChecked(self.settings.enable_vulnerable_driver_blocklist_tweak)
                page.chk_hard_working_set.setChecked(self.settings.enable_hard_working_set)
                page.chk_net_imod.setChecked(self.settings.enable_net_imod_tweak)
                page.chk_net_bindings.setChecked(self.settings.enable_net_bindings_tweak)
                page.chk_wifi_tweak.setChecked(self.settings.enable_wifi_tweak)
                page.chk_game_gpu_preference.setChecked(self.settings.enable_game_gpu_preference_tweak)
                page.chk_irq_affinity.setChecked(self.settings.enable_irq_affinity_tweak)
                page.chk_power_throttling.setChecked(self.settings.enable_power_throttling_tweak)
                page.chk_tcp_bbr.setChecked(self.settings.enable_tcp_bbr_tweak)
                page.chk_eee.setChecked(self.settings.enable_eee_tweak)
                page.chk_web_search.setChecked(self.settings.enable_web_search_tweak)
                page.chk_telemetry_tasks.setChecked(self.settings.enable_telemetry_tasks_tweak)
                page.chk_extreme_debloat.setChecked(self.settings.enable_extreme_debloat_tweak)
                page.chk_prefetcher.setChecked(self.settings.enable_prefetcher_tweak)
                page.chk_parking.setChecked(self.settings.enable_core_parking)
                page.chk_epp.setChecked(self.settings.enable_epp_max)
                page.chk_hpet.setChecked(self.settings.disable_hpet)
                
                page.chk_dwm.setChecked(self.settings.enable_dwm_tweak)
                page.chk_dpc.setChecked(self.settings.enable_dpc_latency_tweak)
                page.chk_timer_res.setChecked(self.settings.enable_timer_resolution_tweak)
                page.chk_naraka_priority.setChecked(self.settings.enable_naraka_priority)
                page.chk_child.setChecked(self.settings.enable_child_optimization)
                page.chk_driver_prio.setChecked(self.settings.enable_driver_priority_tweak)
                
                page.chk_usb_lat.setChecked(self.settings.enable_usb_low_latency_tweak)
                page.chk_imod.setChecked(self.settings.enable_usb_imod_tweak)
                page.chk_mouse_lat.setChecked(self.settings.enable_mouse_latency_tweak)
                
                page.chk_preemption.setChecked(self.settings.disable_gpu_preemption)
                page.chk_dwm_wet.setChecked(self.settings.enable_dwm_super_wet_tweak)
                page.chk_directx.setChecked(self.settings.enable_directx_tweaks)
                page.chk_gpu_firmware.setChecked(self.settings.enable_gpu_firmware_tweak)
                page.chk_gpu_pstate.setChecked(self.settings.enable_gpu_pstate_tweak)
                
                page.chk_intel_plan.setChecked(self.settings.enable_custom_power_plan)
                page.chk_amd_plan.setChecked(self.settings.enable_custom_power_plan)
                
                # Connect custom power plan check state changed to trigger CPU power cards relation update
                page.chk_intel_plan.checkedChanged.connect(self.update_cpu_power_cards_relation)
                page.chk_amd_plan.checkedChanged.connect(self.update_cpu_power_cards_relation)
                self.update_cpu_power_cards_relation()
                
                page.chk_gpu_opt.setChecked(self.settings.enable_gpu_optimization)
                page.chk_pcipower.setChecked(self.settings.disable_pcipower)
                page.chk_gpu_irq.setChecked(self.settings.enable_gpu_irq_tweak)
                page.chk_hags.setChecked(self.settings.disable_hags)
                page.chk_gpu_nip.setChecked(self.settings.enable_nvidia_nip)
                
                page.chk_ram_opt.setChecked(self.settings.enable_ram_optimization)
                page.chk_nvme_opt.setChecked(self.settings.enable_nvme_optimization)
                page.chk_memory_comp.setChecked(self.settings.disable_memory_compression)
                page.chk_config_alloc.setChecked(self.settings.enable_config_alloc_tweak)
                
                page.chk_services.setChecked(self.settings.disable_useless_services)
                page.chk_wsearch.setChecked(self.settings.disable_wsearch_tweak)
                page.chk_spectre.setChecked(self.settings.disable_spectre_meltdown)
                page.chk_copilot.setChecked(self.settings.disable_copilot)
                page.chk_gamedvr.setChecked(self.settings.disable_gamedvr)
                page.chk_dev_power.setChecked(self.settings.enable_device_power_tweak)
                page.chk_uac.setChecked(self.settings.enable_uac_tweak)
                page.chk_desktop_heap.setChecked(self.settings.enable_desktop_heap_tweak)
                page.chk_download_maps.setChecked(self.settings.enable_download_maps_tweak)
                page.chk_bg_apps.setChecked(self.settings.enable_bg_apps_tweak)
                page.chk_map_updates.setChecked(self.settings.enable_map_updates_tweak)
                page.chk_autoshare.setChecked(self.settings.enable_autoshare_tweak)
                page.chk_autorun.setChecked(self.settings.enable_autorun_tweak)
                page.chk_hyperv.setChecked(self.settings.disable_hyperv_virtualization)
                
                page.chk_security_notifications.setChecked(self.settings.disable_security_notifications)
                page.chk_defender.setChecked(self.settings.disable_defender)
                page.chk_smartscreen.setChecked(self.settings.disable_smartscreen)
                page.chk_firewall.setChecked(self.settings.disable_firewall)
                
                page.chk_network.setChecked(self.settings.enable_network_tweak)
                page.chk_ult_net.setChecked(self.settings.enable_ultimate_network_tweak)
                page.chk_dns.setChecked(self.settings.enable_dns_tweak)
                
            elif page_name in ("toolsPage", "tools"):
                page.load_whitelist_ui(self.settings.custom_whitelist)
                
        except Exception as e:
            logger.error(f"Failed to load page settings for {page_name}: {e}")
        finally:
            self.is_loading_settings = False

    def update_auto_watchdog(self):
        if getattr(self, 'is_initializing', False) or getattr(self, 'is_closing', False):
            return

        new_enable = self.settings.enable_watchdog
        new_target = self.settings.target_process_name
        new_path = self.settings.target_process_path
        new_settings = {
            "enable_gpu_clock_lock": self.settings.enable_gpu_pstate_tweak,
            "enable_nvidia_drs": self.settings.enable_nvidia_nip,
            "enable_irq_separation": self.settings.enable_irq_affinity_tweak,
            "enable_client_priority_demote": self.settings.enable_client_priority_demote
        }

        # Check if current running watchdog is already up-to-date
        if hasattr(self, 'auto_watchdog_thread') and self.auto_watchdog_thread:
            curr = self.auto_watchdog_thread
            import os
            curr_path = os.path.normpath(curr.target_path).lower().strip() if curr.target_path else ""
            new_path_norm = os.path.normpath(new_path).lower().strip() if new_path else ""
            
            if (curr.running and 
                curr.target_exe == new_target.lower().strip() and 
                curr_path == new_path_norm and 
                curr.settings_dict == new_settings and 
                new_enable):
                return  # Identical, keep running

        # Stop the existing watchdog thread
        if hasattr(self, 'auto_watchdog_thread') and self.auto_watchdog_thread:
            try:
                self.auto_watchdog_thread.stop()
                self.auto_watchdog_thread.wait(300)
            except Exception:
                pass
            self.auto_watchdog_thread = None

        if new_enable and new_target:
            from core_commander.core.watchdog import GameWatchdogService
            self.auto_watchdog_thread = GameWatchdogService(
                new_target,
                new_path,
                new_settings,
                self
            )
            self.auto_watchdog_thread.game_detected_signal.connect(self.on_auto_game_detected)
            self.auto_watchdog_thread.game_exited_signal.connect(self.on_auto_game_exited)
            self.auto_watchdog_thread.start()
            logger.info(f"Auto-switching watchdog thread started for target game: {new_target}")

    def on_auto_game_detected(self, pid: int, name: str):
        logger.info(f"Watchdog detected target game: {name} (PID: {pid}). Triggering process optimizations.")
        self.target_pid = pid
        self.is_optimized = False
        self.is_auto_optimizing = True
        
        # Trigger the full process optimization pipeline (priority, affinity, preferred cores, QoS, etc.)
        self.apply_optimization(self.home_page)
        
        # Override apply button to show auto-optimization state immediately
        self.home_page.apply_btn.setText("自动优化中...")
    def on_auto_game_exited(self, name: str):
        logger.info(f"Watchdog detected target game exited: {name}. Reverting process optimizations.")
        # Trigger the full cancellation and revert pipeline (restores background process affinities, power scheme, QoS, etc.)
        self.cancel_optimization(self.home_page)
        self.is_auto_optimizing = False
        self.current_pid = None
        self.target_pid = None
        
        # Clean up persistent firewall rules
        try:
            from core_commander.core.tweaks.throttler import NetworkThrottlerService
            NetworkThrottlerService.force_delete_rules()
        except Exception as e:
            logger.error(f"Error deleting QoS firewall rules on game exit: {e}")

        self.home_page.update_proc_display()
        self.update_fps_collector_lifecycle()

    def detect_and_sync_system_states(self, force_sync: bool = False):
        """
        Launches the asynchronous system state scanner thread.
        """
        if getattr(self, 'is_scanning_states', False):
            if not force_sync:
                return
        logger.info(f"Starting asynchronous system state background scan (force_sync={force_sync})...")
        self.is_forcing_sync = force_sync
        self.is_scanning_states = True
        try:
            self.state_scanner = SystemStateScannerWorker(
                gpu_vendor=self.gpu_vendor, 
                target_exe=self.target_name, 
                target_path=self.settings.target_process_path, 
                parent=self
            )
            self.state_scanner.finished_signal.connect(self.on_system_states_scanned)
            self.state_scanner.finished.connect(self.state_scanner.deleteLater)
            self.state_scanner.start()
        except Exception as e:
            logger.error(f"Failed to start system state scanner: {str(e)}")
            self.is_scanning_states = False

    def on_system_states_scanned(self, states: dict):
        self.is_scanning_states = False
        if not states:
            logger.warning("Empty states dictionary returned from background scanner.")
            return
            
        is_first_run = not self.settings.get_bool("first_run_done", False)
        force_sync = getattr(self, 'is_forcing_sync', False) or is_first_run
        self.is_forcing_sync = False
        has_changes = False

        # Helper to set checkbox state with baseline sync
        def set_chk(chk_card, actual_state):
            nonlocal has_changes
            if chk_card:
                is_msi = False
                if self.optimization_page.page is not None:
                    try:
                        is_msi = chk_card in (self.optimization_page.chk_gpu_msi, self.optimization_page.chk_network_msi, self.optimization_page.chk_storage_msi)
                    except AttributeError:
                        pass

                if is_msi:
                    ui_checked_state = (actual_state == 2)
                else:
                    ui_checked_state = bool(actual_state)

                if chk_card.applied_state is None and not force_sync:
                    # Startup baseline scan: preserve UI checkbox state loaded from settings.json
                    chk_card.applied_state = actual_state
                    pending_val = (chk_card.isChecked() != ui_checked_state)
                    if pending_val and not getattr(chk_card, 'is_immediate', False):
                        pending_val = "reboot_pending"
                    chk_card.update_status(chk_card.isChecked(), is_pending=pending_val)
                else:
                    if is_msi:
                        expected = 2 if chk_card.isChecked() else 0
                        was_pending = (chk_card.applied_state is not None and chk_card.applied_state != expected)
                    else:
                        was_pending = (chk_card.applied_state is not None and chk_card.isChecked() != chk_card.applied_state)
                    if force_sync:
                        if chk_card.isChecked() != ui_checked_state:
                            has_changes = True
                        chk_card.blockSignals(True)
                        chk_card.setChecked(ui_checked_state)
                        chk_card.blockSignals(False)
                        chk_card.applied_state = actual_state
                        chk_card.update_status(ui_checked_state, is_pending=False)
                    else:
                        chk_card.applied_state = actual_state
                        pending_val = (chk_card.isChecked() != ui_checked_state)
                        if pending_val and not getattr(chk_card, 'is_immediate', False):
                            pending_val = "reboot_pending"
                        chk_card.update_status(chk_card.isChecked(), is_pending=pending_val)
        def set_combo(combo_card, actual_idx):
            nonlocal has_changes
            if combo_card:
                if combo_card.applied_state is None and not force_sync:
                    # Startup baseline scan: preserve UI combobox index loaded from settings.json
                    combo_card.applied_state = actual_idx
                else:
                    if force_sync:
                        if combo_card.comboBox.currentIndex() != actual_idx:
                            has_changes = True
                        combo_card.comboBox.blockSignals(True)
                        combo_card.comboBox.setCurrentIndex(actual_idx)
                        combo_card.comboBox.blockSignals(False)
                    combo_card.applied_state = actual_idx

        try:
            # Update General Page (physically on general_page)
            if self.general_page.page is not None:
                # 1. Win32 priority separation
                prio_val = states.get('win32_prio')
                PRIO_SEP_VALUES = [2, 20, 21, 22, 24, 25, 26, 36, 37, 38, 40, 41, 42]
                if prio_val in PRIO_SEP_VALUES:
                    set_combo(self.general_page.win32_prio_card, PRIO_SEP_VALUES.index(prio_val))

                # 2. Keyboard queue size
                kb_val = states.get('kb_val')
                kb_list = [100, 50, 30, 20, 16, 12, 10, 8, 6]
                if kb_val in kb_list:
                    set_combo(self.general_page.keyboard_queue_card, kb_list.index(kb_val))

                # 3. Mouse queue size
                m_val = states.get('m_val')
                m_list = [100, 50, 30, 20, 16, 12, 10, 8]
                if m_val in m_list:
                    set_combo(self.general_page.mouse_queue_card, m_list.index(m_val))

                # 4. Keyboard Repeat Rate
                kb_rep = states.get('keyboard_repeat', False)
                if kb_rep:
                    current_idx = self.general_page.keyboard_repeat_rate_card.comboBox.currentIndex()
                    if current_idx == 0:
                        stored_level = self.settings.keyboard_repeat_delay_level
                        set_combo(self.general_page.keyboard_repeat_rate_card, stored_level if stored_level > 0 else 2)
                    else:
                        set_combo(self.general_page.keyboard_repeat_rate_card, current_idx)
                else:
                    set_combo(self.general_page.keyboard_repeat_rate_card, 0)

            # Update Optimization Page (physically on optimization_page)
            if self.optimization_page.page is not None:
                # 5. Core Parking
                set_chk(self.cpu_page.chk_parking, states.get('core_parking', False))

                # 6. EPP
                set_chk(self.cpu_page.chk_epp, states.get('epp', False))

                # 7. HPET
                set_chk(self.cpu_page.chk_hpet, states.get('hpet', False))

                # 8. Network tweaks
                set_chk(self.network_page.chk_network, states.get('network_throttling', False))

                # 9. Services & Telemetry
                set_chk(self.privacy_page.chk_services, states.get('services_disabled', False))
                set_chk(self.privacy_page.chk_wsearch, states.get('wsearch_disabled', False))

                # 10. RAM Split Host Threshold
                set_chk(self.memory_page.chk_ram_opt, states.get('ram_opt', False))

                # 11. NVMe Last Access Timestamp & short name
                set_chk(self.memory_page.chk_nvme_opt, states.get('nvme_opt', False))

                # 12. Spectre Meltdown Mitigation
                set_chk(self.privacy_page.chk_spectre, states.get('spectre', False))

                # 13. GPU Preemption
                set_chk(self.gpu_page.chk_preemption, states.get('preemption', False))

                # 14. GameDVR
                set_chk(self.privacy_page.chk_gamedvr, states.get('gamedvr', False))

                # 15. Ultimate network
                set_chk(self.network_page.chk_ult_net, states.get('ult_net', False))

                # 16. DWM Frame Latency
                set_chk(self.cpu_page.chk_dwm, states.get('dwm_tweak', False))

                # 17. DPC latency
                set_chk(self.cpu_page.chk_dpc, states.get('dpc', False))

                # 18. DWM SuperWet
                set_chk(self.gpu_page.chk_dwm_wet, states.get('dwm_wet', False))

                # 19. GlobalTimerResolutionRequests
                set_chk(self.cpu_page.chk_timer_res, states.get('timer_res', False))

                # 20. PCI Power Express ASPM
                set_chk(self.gpu_page.chk_pcipower, states.get('pcipower', False))

                # 21. DirectX Flip Discard
                set_chk(self.gpu_page.chk_directx, states.get('directx', False))

                # 22. DNS ServiceProvider Priorities
                set_chk(self.network_page.chk_dns, states.get('dns', False))

                # 23. Feeds and softlanding tips
                set_chk(self.optimization_page.chk_consult_interests, states.get('feeds', False))
                set_chk(self.optimization_page.chk_tips_suggestions, states.get('tips', False))

                # 24. Desktop Heap
                set_chk(self.privacy_page.chk_desktop_heap, states.get('desktop_heap', False))

                # 25. UAC EnableLUA
                set_chk(self.privacy_page.chk_uac, states.get('uac', False))

                # 26. MapsBroker download Maps
                set_chk(self.privacy_page.chk_download_maps, states.get('download_maps', False))

                # 27. Background Access App Execution
                set_chk(self.privacy_page.chk_bg_apps, states.get('bg_apps', False))
                set_chk(self.privacy_page.chk_map_updates, states.get('map_updates', False))

                # 28. AutoShare
                set_chk(self.privacy_page.chk_autoshare, states.get('autoshare', False))

                # 29. AutoRun Explorer policies
                set_chk(self.privacy_page.chk_autorun, states.get('autorun', False))

                # 30. Mouse flat speed & curves
                set_chk(self.peripheral_page.chk_mouse_lat, states.get('mouse_lat', False))

                # 31. ConfigFileAllocSize
                set_chk(self.memory_page.chk_config_alloc, states.get('config_alloc', False))

                # 32. GPU Firmware & PState
                set_chk(self.gpu_page.chk_gpu_firmware, states.get('gpu_firmware', False))
                set_chk(self.gpu_page.chk_gpu_pstate, states.get('gpu_pstate', False))

                # 33. Game priority
                set_chk(self.cpu_page.chk_naraka_priority, states.get('naraka', False))

                # 34. Memory compression status
                set_chk(self.memory_page.chk_memory_comp, states.get('memory_comp', False))

                # 35. Visual effects
                set_chk(self.optimization_page.chk_visual_effects, states.get('visual_effects', False))

                # 36. Transparency
                set_chk(self.optimization_page.chk_transparency, states.get('transparency', False))

                # 37. Copilot
                set_chk(self.privacy_page.chk_copilot, states.get('copilot', False))

                # 38. Security Notifications
                set_chk(self.privacy_page.chk_security_notifications, states.get('sec_notif', False))

                # 39. Defender
                set_chk(self.privacy_page.chk_defender, states.get('defender', False))

                # 40. SmartScreen
                set_chk(self.privacy_page.chk_smartscreen, states.get('smartscreen', False))

                # 41. Firewall
                set_chk(self.privacy_page.chk_firewall, states.get('firewall', False))

                # 42. Driver Priority
                set_chk(self.cpu_page.chk_driver_prio, states.get('driver_prio', False))

                # 43. Hyper-V
                set_chk(self.privacy_page.chk_hyperv, states.get('hyperv', False))
                
                # 44. GPU Optimization
                set_chk(self.gpu_page.chk_gpu_opt, states.get('gpu_opt', False))

                # 45. GPU IRQ Priority
                set_chk(self.gpu_page.chk_gpu_irq, states.get('gpu_irq', False))

                # 46. GPU HAGS
                set_chk(self.gpu_page.chk_hags, states.get('hags', False))

                # 47. Custom Power Plan
                power_plan_active = states.get('custom_power_plan', False)
                if self.cpu_vendor == "Intel":
                    set_chk(self.gpu_page.chk_intel_plan, power_plan_active)
                    self.gpu_page.chk_amd_plan.applied_state = False
                    self.gpu_page.chk_amd_plan.update_status(False, is_pending=False)
                elif self.cpu_vendor == "AMD":
                    set_chk(self.gpu_page.chk_amd_plan, power_plan_active)
                    self.gpu_page.chk_intel_plan.applied_state = False
                    self.gpu_page.chk_intel_plan.update_status(False, is_pending=False)
                else:
                    set_chk(self.gpu_page.chk_intel_plan, power_plan_active)
                    set_chk(self.gpu_page.chk_amd_plan, power_plan_active)

                set_chk(self.gpu_page.chk_global_fse, states.get('global_fse', False))
                set_chk(self.gpu_page.chk_game_fse, states.get('game_fse', False))
                set_chk(self.optimization_page.chk_widgets, states.get('widgets', False))
                set_chk(self.peripheral_page.chk_sticky_keys, states.get('sticky_keys', False))
                set_chk(self.optimization_page.chk_startup_delay, states.get('startup_delay', False))
                set_chk(self.optimization_page.chk_menu_delay, states.get('menu_delay', False))
                set_chk(self.privacy_page.chk_settings_sync, states.get('settings_sync', False))
                set_chk(self.peripheral_page.chk_dynamic_lighting, states.get('dynamic_lighting', False))
                set_chk(self.gpu_page.chk_gpu_msi, states.get('gpu_msi', 0))
                set_chk(self.privacy_page.chk_xbox_save, states.get('xbox_save', False))
                set_chk(self.privacy_page.chk_store_auto_update, states.get('store_auto_update', False))
                set_chk(self.cpu_page.chk_vulnerable_driver_blocklist, states.get('vulnerable_driver_blocklist', False))
                set_chk(self.optimization_page.chk_prevent_device_encryption, states.get('prevent_device_encryption', False))
                set_chk(self.optimization_page.chk_spotlight, states.get('spotlight', False))
                set_chk(self.memory_page.chk_hard_working_set, self.settings.enable_hard_working_set)
                set_chk(self.cpu_page.chk_child, self.settings.enable_child_optimization)
                set_chk(self.network_page.chk_net_imod, states.get('net_imod', False))
                set_chk(self.network_page.chk_net_bindings, states.get('net_bindings', False))
                set_chk(self.network_page.chk_wifi_tweak, self.settings.enable_wifi_tweak)
                set_chk(self.gpu_page.chk_game_gpu_preference, states.get('game_gpu_preference', False))
                set_chk(self.gpu_page.chk_irq_affinity, states.get('irq_affinity', False))
                set_chk(self.cpu_page.chk_power_throttling, states.get('power_throttling', False))
                set_chk(self.network_page.chk_tcp_bbr, states.get('tcp_bbr', False))
                set_chk(self.network_page.chk_eee, states.get('eee_tweak', False))
                set_chk(self.privacy_page.chk_web_search, states.get('web_search', False))
                set_chk(self.privacy_page.chk_telemetry_tasks, states.get('telemetry_tasks', False))
                set_chk(self.privacy_page.chk_extreme_debloat, states.get('extreme_debloat', False))
                set_chk(self.memory_page.chk_prefetcher, states.get('prefetcher', False))
                set_chk(self.network_page.chk_network_msi, states.get('network_msi', 0))
                set_chk(self.memory_page.chk_storage_msi, states.get('storage_msi', 0))
                set_chk(self.gpu_page.chk_dwm_presentation, states.get('dwm_presentation', False))
                set_chk(self.cpu_page.chk_client_priority_demote, self.settings.enable_client_priority_demote)
                set_chk(self.gpu_page.chk_gpu_nip, self.settings.enable_nvidia_nip)
                set_chk(self.peripheral_page.chk_usb_lat, states.get('usb_lat', False))
                set_chk(self.peripheral_page.chk_imod, states.get('usb_imod', False))
                set_chk(self.privacy_page.chk_dev_power, states.get('dev_power', False))

                # Override security state indicators with actual warnings if applied but not active
                defender_active = not states.get('defender', False)
                if defender_active and self.privacy_page.chk_defender.isChecked():
                    if states.get('tamper_protection', False):
                        self.privacy_page.chk_defender.statusLabel.setText("未生效 (请先手动关闭“篡改防护”)")
                        self.privacy_page.chk_defender.statusLabel.setStyleSheet("color: #FF8C00; font-weight: bold; font-size: 13px;")
                    else:
                        self.privacy_page.chk_defender.statusLabel.setText("未生效 (重启计算机后生效)")
                        self.privacy_page.chk_defender.statusLabel.setStyleSheet("color: #E2B13C; font-weight: bold; font-size: 13px;")
                        
                firewall_active = not states.get('firewall', False)
                if firewall_active and self.privacy_page.chk_firewall.isChecked():
                    self.privacy_page.chk_firewall.statusLabel.setText("未生效 (被系统安全策略拦截)")
                    self.privacy_page.chk_firewall.statusLabel.setStyleSheet("color: #FF8C00; font-weight: bold; font-size: 13px;")

            if is_first_run:
                self.settings.set_value("first_run_done", True)
                has_changes = True

            # Save settings immediately to synchronize the settings.json if actual changes occurred
            if has_changes:
                logger.info("System state scanner detected configuration changes. Saving settings.")
                self.save_settings()
            else:
                logger.info("System state scanner finished. No configuration changes detected, skipping registry write.")
            self.update_cpu_power_cards_relation()
            self.update_pending_status()
            
            # Startup baseline scan completion check: automatically re-apply pending immediate tweaks
            if not getattr(self, 'first_scan_done', False):
                self.first_scan_done = True
                pending_keys = self.get_pending_keys()
                immediate_pending = [key for key in pending_keys if key in self.IMMEDIATE_KEYS]
                if immediate_pending:
                    logger.info(f"Startup scan detected pending immediate tweaks: {immediate_pending}. Re-applying silently.")
                    self.apply_immediate_tweaks_silently()
                    
            logger.info("Asynchronous system state background scan completed and UI updated.")
        except Exception as e:
            logger.error(f"Error updating UI in on_system_states_scanned: {str(e)}")

    def save_settings(self):
        """
        Persists configurations.
        """
        if getattr(self, 'is_loading_settings', False) or getattr(self, 'is_loading_preset', False) or getattr(self, 'is_scanning_states', False):
            return
        try:
            self.settings.target_process_name = self.target_name
            self.settings.p1_idx = self.home_page.combo_primary1.currentIndex()
            self.settings.p2_idx = self.home_page.combo_primary2.currentIndex()
            
            # Optimization Page
            if self.optimization_page.page is not None:
                self.settings.enable_widgets_tweak = self.optimization_page.chk_widgets.isChecked()
                self.settings.enable_startup_delay_tweak = self.optimization_page.chk_startup_delay.isChecked()
                self.settings.enable_menu_delay_tweak = self.optimization_page.chk_menu_delay.isChecked()
                self.settings.enable_prevent_device_encryption_tweak = self.optimization_page.chk_prevent_device_encryption.isChecked()
                self.settings.enable_spotlight_tweak = self.optimization_page.chk_spotlight.isChecked()
                self.settings.enable_isolation = self.optimization_page.chk_iso.isChecked()
                self.settings.enable_watchdog = self.optimization_page.chk_dog.isChecked()
                self.settings.disable_windows_visual_effects = self.optimization_page.chk_visual_effects.isChecked()
                self.settings.disable_windows_transparency = self.optimization_page.chk_transparency.isChecked()
                self.settings.enable_consult_interests_tweak = self.optimization_page.chk_consult_interests.isChecked()
                self.settings.enable_tips_suggestions_tweak = self.optimization_page.chk_tips_suggestions.isChecked()

                self.settings.enable_global_fse_tweak = self.gpu_page.chk_global_fse.isChecked()
                self.settings.enable_game_fse_tweak = self.gpu_page.chk_game_fse.isChecked()
                self.settings.enable_sticky_keys_tweak = self.peripheral_page.chk_sticky_keys.isChecked()
                self.settings.enable_settings_sync_tweak = self.privacy_page.chk_settings_sync.isChecked()
                self.settings.enable_dynamic_lighting_tweak = self.peripheral_page.chk_dynamic_lighting.isChecked()
                self.settings.enable_gpu_msi_tweak = self.gpu_page.chk_gpu_msi.isChecked()
                self.settings.enable_network_msi_tweak = self.network_page.chk_network_msi.isChecked()
                self.settings.enable_storage_msi_tweak = self.memory_page.chk_storage_msi.isChecked()
                self.settings.enable_dwm_presentation_tweak = self.gpu_page.chk_dwm_presentation.isChecked()
                self.settings.enable_client_priority_demote = self.cpu_page.chk_client_priority_demote.isChecked()
                self.settings.enable_xbox_save_tweak = self.privacy_page.chk_xbox_save.isChecked()
                self.settings.enable_store_auto_update_tweak = self.privacy_page.chk_store_auto_update.isChecked()
                self.settings.enable_vulnerable_driver_blocklist_tweak = self.cpu_page.chk_vulnerable_driver_blocklist.isChecked()
                self.settings.enable_hard_working_set = self.memory_page.chk_hard_working_set.isChecked()
                self.settings.enable_net_imod_tweak = self.network_page.chk_net_imod.isChecked()
                self.settings.enable_net_bindings_tweak = self.network_page.chk_net_bindings.isChecked()
                self.settings.enable_wifi_tweak = self.network_page.chk_wifi_tweak.isChecked()
                self.settings.enable_game_gpu_preference_tweak = self.gpu_page.chk_game_gpu_preference.isChecked()
                self.settings.enable_irq_affinity_tweak = self.gpu_page.chk_irq_affinity.isChecked()
                self.settings.enable_power_throttling_tweak = self.cpu_page.chk_power_throttling.isChecked()
                self.settings.enable_tcp_bbr_tweak = self.network_page.chk_tcp_bbr.isChecked()
                self.settings.enable_eee_tweak = self.network_page.chk_eee.isChecked()
                self.settings.enable_web_search_tweak = self.privacy_page.chk_web_search.isChecked()
                self.settings.enable_telemetry_tasks_tweak = self.privacy_page.chk_telemetry_tasks.isChecked()
                self.settings.enable_extreme_debloat_tweak = self.privacy_page.chk_extreme_debloat.isChecked()
                self.settings.enable_prefetcher_tweak = self.memory_page.chk_prefetcher.isChecked()

                self.settings.enable_core_parking = self.cpu_page.chk_parking.isChecked()
                self.settings.enable_epp_max = self.cpu_page.chk_epp.isChecked()
                self.settings.disable_hpet = self.cpu_page.chk_hpet.isChecked()
                
                PRIO_SEP_VALUES = [2, 20, 21, 22, 24, 25, 26, 36, 37, 38, 40, 41, 42]
                prio_idx = self.cpu_page.win32_prio_card.comboBox.currentIndex()
                prio_val = PRIO_SEP_VALUES[prio_idx] if 0 <= prio_idx < len(PRIO_SEP_VALUES) else 26
                self.settings.win32_prio_sep = prio_val
                
                self.settings.enable_dwm_tweak = self.cpu_page.chk_dwm.isChecked()
                self.settings.enable_dpc_latency_tweak = self.cpu_page.chk_dpc.isChecked()
                self.settings.enable_timer_resolution_tweak = self.cpu_page.chk_timer_res.isChecked()
                self.settings.enable_naraka_priority = self.cpu_page.chk_naraka_priority.isChecked()
                self.settings.enable_child_optimization = self.cpu_page.chk_child.isChecked()
                self.settings.enable_driver_priority_tweak = self.cpu_page.chk_driver_prio.isChecked()
                
                # Peripheral Page
                kb_list = [100, 50, 30, 20, 16, 12, 10, 8, 6]
                kb_idx = self.peripheral_page.keyboard_queue_card.comboBox.currentIndex()
                self.settings.keyboard_queue_size = kb_list[kb_idx] if 0 <= kb_idx < len(kb_list) else 100
                
                m_list = [100, 50, 30, 20, 16, 12, 10, 8]
                m_idx = self.peripheral_page.mouse_queue_card.comboBox.currentIndex()
                self.settings.mouse_queue_size = m_list[m_idx] if 0 <= m_idx < len(m_list) else 100
                
                self.settings.keyboard_repeat_delay_level = self.peripheral_page.keyboard_repeat_rate_card.comboBox.currentIndex()
                self.settings.enable_usb_low_latency_tweak = self.peripheral_page.chk_usb_lat.isChecked()
                self.settings.enable_usb_imod_tweak = self.peripheral_page.chk_imod.isChecked()
                self.settings.enable_mouse_latency_tweak = self.peripheral_page.chk_mouse_lat.isChecked()
                
                # GPU Page
                self.settings.disable_gpu_preemption = self.gpu_page.chk_preemption.isChecked()
                self.settings.enable_dwm_super_wet_tweak = self.gpu_page.chk_dwm_wet.isChecked()
                self.settings.enable_directx_tweaks = self.gpu_page.chk_directx.isChecked()
                self.settings.enable_gpu_firmware_tweak = self.gpu_page.chk_gpu_firmware.isChecked()
                self.settings.enable_gpu_pstate_tweak = self.gpu_page.chk_gpu_pstate.isChecked()
                power_plan_checked = self.gpu_page.chk_intel_plan.isChecked() or self.gpu_page.chk_amd_plan.isChecked()
                self.settings.enable_custom_power_plan = power_plan_checked
                self.settings.enable_gpu_optimization = self.gpu_page.chk_gpu_opt.isChecked()
                self.settings.disable_pcipower = self.gpu_page.chk_pcipower.isChecked()
                self.settings.enable_gpu_irq_tweak = self.gpu_page.chk_gpu_irq.isChecked()
                self.settings.disable_hags = self.gpu_page.chk_hags.isChecked()
                self.settings.enable_nvidia_nip = self.gpu_page.chk_gpu_nip.isChecked()
                
                # Memory Page
                self.settings.enable_ram_optimization = self.memory_page.chk_ram_opt.isChecked()
                self.settings.enable_nvme_optimization = self.memory_page.chk_nvme_opt.isChecked()
                self.settings.disable_memory_compression = self.memory_page.chk_memory_comp.isChecked()
                self.settings.enable_config_alloc_tweak = self.memory_page.chk_config_alloc.isChecked()
                
                # Privacy Page
                self.settings.disable_useless_services = self.privacy_page.chk_services.isChecked()
                self.settings.disable_wsearch_tweak = self.privacy_page.chk_wsearch.isChecked()
                self.settings.disable_spectre_meltdown = self.privacy_page.chk_spectre.isChecked()
                self.settings.disable_copilot = self.privacy_page.chk_copilot.isChecked()
                self.settings.disable_gamedvr = self.privacy_page.chk_gamedvr.isChecked()
                self.settings.enable_device_power_tweak = self.privacy_page.chk_dev_power.isChecked()
                self.settings.enable_uac_tweak = self.privacy_page.chk_uac.isChecked()
                self.settings.enable_desktop_heap_tweak = self.privacy_page.chk_desktop_heap.isChecked()
                self.settings.enable_download_maps_tweak = self.privacy_page.chk_download_maps.isChecked()
                self.settings.enable_bg_apps_tweak = self.privacy_page.chk_bg_apps.isChecked()
                self.settings.enable_map_updates_tweak = self.privacy_page.chk_map_updates.isChecked()
                self.settings.enable_autoshare_tweak = self.privacy_page.chk_autoshare.isChecked()
                self.settings.enable_autorun_tweak = self.privacy_page.chk_autorun.isChecked()
                self.settings.disable_hyperv_virtualization = self.privacy_page.chk_hyperv.isChecked()
                
                # Security section
                self.settings.disable_security_notifications = self.privacy_page.chk_security_notifications.isChecked()
                self.settings.disable_defender = self.privacy_page.chk_defender.isChecked()
                self.settings.disable_smartscreen = self.privacy_page.chk_smartscreen.isChecked()
                self.settings.disable_firewall = self.privacy_page.chk_firewall.isChecked()
                
                # Network Page
                self.settings.enable_network_tweak = self.network_page.chk_network.isChecked()
                self.settings.enable_ultimate_network_tweak = self.network_page.chk_ult_net.isChecked()
                self.settings.enable_dns_tweak = self.network_page.chk_dns.isChecked()
            
            self.settings.enable_auto_mem_clean = self.home_page.chk_mem_auto.isChecked()
            self.settings.mem_clean_interval = self.home_page.spin_mem_interval.value()
            
            # Save core affinity mask
            checked_threads = []
            for btn in self.home_page.all_core_buttons:
                if btn.isChecked():
                    checked_threads.extend(btn.threads)
            self.settings.affinity_mask_threads = checked_threads

            # General Page
            if self.general_page.page is not None:
                self.settings.show_all_cpu_options = self.general_page.chk_show_all_cpu.isChecked()
                
                # Save Rate Limiter Settings
                old_enable = self.settings.enable_rate_limiter
                if hasattr(self.general_page, 'btn_ocr_hotkey'):
                    new_ocr_hotkey = self.general_page.btn_ocr_hotkey.text()
                    if new_ocr_hotkey and self.settings.ocr_hotkey != new_ocr_hotkey:
                        self.settings.ocr_hotkey = new_ocr_hotkey
                        self.register_ocr_hotkey(force=True)
                
                new_enable = self.general_page.rl_switch.isChecked()
                self.settings.enable_rate_limiter = new_enable
                
                try:
                    # self.settings.rate_limiter_upload_value = float(self.general_page.rl_upload_input.text())
                    pass
                except ValueError:
                    self.settings.rate_limiter_upload_value = 100.0

                try:
                    self.settings.rate_limiter_download_value = float(self.general_page.rl_download_input.text())
                except ValueError:
                    self.settings.rate_limiter_download_value = 100.0
                
                self.settings.rate_limiter_unit = self.general_page.rl_unit_combo.currentText()
                
                mode_data = self.general_page.rl_mode_combo.currentData()
                self.settings.rate_limiter_mode = mode_data or "toggle"
                
                type_data = self.general_page.rl_type_combo.currentData()
                self.settings.rate_limiter_type = type_data or "firewall"
                if hasattr(self.general_page, 'rl_pulse_input') and self.general_page.rl_pulse_input.text():
                    try:
                        val = float(self.general_page.rl_pulse_input.text())
                        val = max(0.01, min(val, 60.0))
                        self.settings.rate_limiter_pulse_duration = val * 1000.0
                    except ValueError:
                        pass
                if hasattr(self.general_page, "rl_pulse_delay_input") and self.general_page.rl_pulse_delay_input.text():
                    try:
                        val = float(self.general_page.rl_pulse_delay_input.text())
                        self.settings.rate_limiter_pulse_delay = val * 1000.0
                    except ValueError:
                        pass
                
                if new_enable:
                    if not old_enable or self.rate_limiter_state == "inactive":
                        self.rate_limiter_state = "waiting"
                    if self.input_hook_thread:
                        self.input_hook_thread.update_hotkey(
                            self.settings.rate_limiter_hotkey_code, 
                            self.settings.rate_limiter_hotkey_type
                        )
                else:
                    self.rate_limiter_state = "inactive"
                    NetworkThrottlerService.remove_rate_limit()
                
                self.update_rate_limiter_ui()
                self.update_rate_limiter_cache_vars()
                
                # Save OSD Settings
                self.settings.enable_fps_overlay = self.general_page.chk_osd.isChecked()
                self.settings.fps_overlay_lock = self.general_page.switch_osd_lock.isChecked()
                self.settings.fps_overlay_font_size = self.general_page.slider_osd_font.value()
                self.settings.fps_overlay_show_cpu_gpu = self.general_page.chk_osd_cpu_gpu.isChecked()
                self.settings.fps_overlay_show_ram = self.general_page.chk_osd_ram.isChecked()
                self.settings.fps_overlay_show_frametime = self.general_page.chk_osd_frametime.isChecked()
                self.settings.fps_overlay_hotkey = self.general_page.btn_osd_hotkey.text()
                self.settings.fps_overlay_pos_x = self.general_page.spin_osd_x.value()
                self.settings.fps_overlay_pos_y = self.general_page.spin_osd_y.value()

            # Apply and sync OSD settings in real-time (skip during close cleanup)
            if not getattr(self, 'is_closing', False):
                if hasattr(self, 'overlay') and self.overlay:
                    self.overlay.apply_theme_settings()
                    self.overlay.set_locked(self.settings.fps_overlay_lock)
                    self.overlay.move(self.settings.fps_overlay_pos_x, self.settings.fps_overlay_pos_y)
                
                self.register_global_hotkey()
                self.update_fps_collector_lifecycle()

            logger.info("Application settings saved to persistent storage.")
            self.update_pending_status()
            self.update_auto_watchdog()
            # Immediately reapply preferred cores if optimization is running
            self.reapply_preferred_cores()
        except Exception as e:
            logger.error(f"Failed to save application configurations: {str(e)}")

    def trigger_debounced_save_settings(self):
        """
        Triggers a debounced settings save and immediate tweaks application.
        Aggregates multiple rapid UI events (clicks) into a single call.
        """
        if getattr(self, 'is_loading_settings', False) or getattr(self, 'is_loading_preset', False) or getattr(self, 'is_closing', False):
            return
            
        if not hasattr(self, 'save_settings_timer'):
            from PySide6.QtCore import QTimer
            self.save_settings_timer = QTimer(self)
            self.save_settings_timer.setSingleShot(True)
            self.save_settings_timer.timeout.connect(self.debounced_save_and_apply)
            
        self.save_settings_timer.start(500)  # 500ms debounce delay

    def debounced_save_and_apply(self):
        if getattr(self, 'is_closing', False):
            return
        logger.info("Executing debounced save settings and immediate tweaks...")
        self.save_settings()
        self.apply_immediate_tweaks_silently()

    def register_changed_immediate_key(self, attr_name):
        if not hasattr(self, 'changed_immediate_keys'):
            self.changed_immediate_keys = set()
        
        mapping = {
            "chk_iso": "enable_isolation",
            "chk_dog": "enable_watchdog",
            "chk_parking": "enable_core_parking",
            "chk_epp": "enable_epp_max",
            "chk_dwm": "enable_dwm_tweak",
            "chk_timer_res": "enable_timer_resolution_tweak",
            "chk_naraka_priority": "enable_naraka_priority",
            "chk_child": "enable_child_optimization",
            "chk_client_priority_demote": "enable_client_priority_demote",
            "chk_dwm_wet": "enable_dwm_super_wet_tweak",
            "chk_intel_plan": "enable_custom_power_plan",
            "chk_amd_plan": "enable_custom_power_plan",
            "chk_gpu_opt": "enable_gpu_optimization",
            "chk_gpu_nip": "enable_nvidia_nip",
            "chk_hard_working_set": "enable_hard_working_set",
            "chk_prefetcher": "enable_prefetcher_tweak",
            "chk_net_imod": "enable_net_imod_tweak",
            "chk_net_bindings": "enable_net_bindings_tweak",
            "chk_tcp_bbr": "enable_tcp_bbr_tweak",
            "chk_eee": "enable_eee_tweak",
            "chk_services": "disable_useless_services",
            "chk_wsearch": "disable_wsearch_tweak",
            "chk_gamedvr": "disable_gamedvr",
            "chk_dev_power": "enable_device_power_tweak",
            "chk_download_maps": "enable_download_maps_tweak",
            "chk_bg_apps": "enable_bg_apps_tweak",
            "chk_map_updates": "enable_map_updates_tweak",
            "chk_autorun": "enable_autorun_tweak",
            "chk_widgets": "enable_widgets_tweak",
            "chk_sticky_keys": "enable_sticky_keys_tweak",
            "chk_startup_delay": "enable_startup_delay_tweak",
            "chk_menu_delay": "enable_menu_delay_tweak",
            "chk_settings_sync": "enable_settings_sync_tweak",
            "chk_dynamic_lighting": "enable_dynamic_lighting_tweak",
            "chk_gpu_msi": "enable_gpu_msi_tweak",
            "chk_xbox_save": "enable_xbox_save_tweak",
            "chk_store_auto_update": "enable_store_auto_update_tweak",
            "chk_vulnerable_driver_blocklist": "enable_vulnerable_driver_blocklist_tweak",
            "chk_prevent_device_encryption": "enable_prevent_device_encryption_tweak",
            "chk_spotlight": "enable_spotlight_tweak",
            "chk_wifi_tweak": "enable_wifi_tweak",
            "chk_game_gpu_preference": "enable_game_gpu_preference_tweak",
            "chk_irq_affinity": "enable_irq_affinity_tweak",
            "chk_power_throttling": "enable_power_throttling_tweak",
            "chk_web_search": "enable_web_search_tweak",
            "chk_telemetry_tasks": "enable_telemetry_tasks_tweak",
            "chk_extreme_debloat": "enable_extreme_debloat_tweak",
            "chk_security_notifications": "disable_security_notifications",
            "chk_defender": "disable_defender",
            "chk_smartscreen": "disable_smartscreen",
            "chk_firewall": "disable_firewall",
            "chk_visual_effects": "disable_windows_visual_effects",
            "chk_transparency": "disable_windows_transparency",
            "chk_consult_interests": "enable_consult_interests_tweak",
            "chk_tips_suggestions": "enable_tips_suggestions_tweak"
        }
        key = mapping.get(attr_name)
        if key:
            self.changed_immediate_keys.add(key)
            logger.info(f"Registered manually changed immediate key: {key} (from {attr_name})")

    def apply_immediate_tweaks_silently(self):
        """
        Applies immediate tweaks silently in a background thread when settings are changed.
        """
        if getattr(self, 'is_loading_settings', False) or getattr(self, 'is_loading_preset', False):
            return

        settings_dict = self.get_settings_dict()
        
        # Only apply immediate keys that were actually changed in this UI session
        changed_keys = getattr(self, 'changed_immediate_keys', set())
        if not changed_keys:
            return
            
        immediate_pending = [key for key in changed_keys if key in self.IMMEDIATE_KEYS]
        self.changed_immediate_keys.clear()  # Clear for next time

        if not immediate_pending:
            return
        logger.info(f"Applying immediate tweaks silently: {immediate_pending}")

        # Check if already running to prevent concurrent thread conflict (safeguard against shiboken deleted object)
        is_running = False
        try:
            if hasattr(self, 'silent_tweak_thread') and self.silent_tweak_thread:
                is_running = self.silent_tweak_thread.isRunning()
        except RuntimeError:
            self.silent_tweak_thread = None

        if is_running:
            if not hasattr(self, 'silent_retry_timer'):
                from PySide6.QtCore import QTimer
                self.silent_retry_timer = QTimer(self)
                self.silent_retry_timer.setSingleShot(True)
                self.silent_retry_timer.timeout.connect(self.apply_immediate_tweaks_silently)
            self.silent_retry_timer.start(500)
            return

        self.silent_tweak_thread = SystemTweakThread(
            settings_dict=settings_dict,
            cpu_vendor=self.cpu_vendor,
            gpu_vendor=self.gpu_vendor,
            do_backup=True,
            pending_keys=immediate_pending,
            parent=self,
            use_active_backup=False
        )

        def on_silent_finished(success, msg):
            logger.info(f"Silent immediate tweak deployment finished: success={success}, msg={msg}")
            if success:
                self.detect_and_sync_system_states()

        self.silent_tweak_thread.log_signal.connect(self.log_overlay.append_log)
        self.silent_tweak_thread.finished_signal.connect(on_silent_finished)
        self.silent_tweak_thread.finished.connect(self.silent_tweak_thread.deleteLater)
        self.silent_tweak_thread.start()

    def update_pending_status(self):
        if not hasattr(self, 'nav_items'):
            return
            
        page_items = [
            (self.general_page, self.nav_items['general'], Trans.get("nav_general")),
            (self.optimization_page, self.nav_items['optimization'], Trans.get("nav_optimization")),
            (self.tools_page, self.nav_items['tools'], Trans.get("nav_tools")),
        ]
        
        total_pending = 0
        for page, item, base_name in page_items:
            if hasattr(page, 'page') and page.page is None:
                count = 0
            else:
                count = page.get_pending_changes_count()
            total_pending += count
            if count > 0:
                item.setText(f"{base_name} ({count})")
            else:
                item.setText(base_name)
                
        # Update floating apply buttons
        for page in [self.general_page, self.optimization_page, self.tools_page]:
            if hasattr(page, 'page') and page.page is None:
                continue
            if hasattr(page, 'apply_btn'):
                if total_pending > 0:
                    page.apply_btn.setText(f"确认生效 ({total_pending})")
                    page.apply_btn.setEnabled(True)
                else:
                    page.apply_btn.setText("确认生效")
                    page.apply_btn.setEnabled(False)
                    
        # Update home page apply button
        if hasattr(self, 'home_page') and hasattr(self.home_page, 'apply_btn'):
            if getattr(self, 'is_optimized', False):
                self.home_page.apply_btn.setText(Trans.get("strategy_cancel_btn"))
            else:
                if total_pending > 0:
                    self.home_page.apply_btn.setText(f"{Trans.get('strategy_apply_btn')} ({total_pending})")
                else:
                    self.home_page.apply_btn.setText(Trans.get("strategy_apply_btn"))

    def get_pending_keys(self) -> list:
        pending_keys = []
        
        # General Page
        if getattr(self, 'general_page', None) is not None and self.general_page.page is not None:
            prio_idx = self.general_page.win32_prio_card.comboBox.currentIndex()
            if self.general_page.win32_prio_card.applied_state is not None and prio_idx != self.general_page.win32_prio_card.applied_state:
                pending_keys.append("win32_prio_sep")
                
            kb_idx = self.general_page.keyboard_queue_card.comboBox.currentIndex()
            if self.general_page.keyboard_queue_card.applied_state is not None and kb_idx != self.general_page.keyboard_queue_card.applied_state:
                pending_keys.append("keyboard_queue_size")
                
            m_idx = self.general_page.mouse_queue_card.comboBox.currentIndex()
            if self.general_page.mouse_queue_card.applied_state is not None and m_idx != self.general_page.mouse_queue_card.applied_state:
                pending_keys.append("mouse_queue_size")
                
            kb_rep_idx = self.general_page.keyboard_repeat_rate_card.comboBox.currentIndex()
            if self.general_page.keyboard_repeat_rate_card.applied_state is not None and kb_rep_idx != self.general_page.keyboard_repeat_rate_card.applied_state:
                pending_keys.append("keyboard_repeat_delay_level")
            
        # CPU Page, etc. (Optimization Page)
        if getattr(self, 'optimization_page', None) is not None and self.optimization_page.page is not None:
            # CPU Tab
            if self.cpu_page.chk_parking.isChecked() != self.cpu_page.chk_parking.applied_state:
                pending_keys.append("enable_core_parking")
            if self.cpu_page.chk_epp.isChecked() != self.cpu_page.chk_epp.applied_state:
                pending_keys.append("enable_epp_max")
            if self.cpu_page.chk_hpet.isChecked() != self.cpu_page.chk_hpet.applied_state:
                pending_keys.append("disable_hpet")
            if self.cpu_page.chk_dwm.isChecked() != self.cpu_page.chk_dwm.applied_state:
                pending_keys.append("enable_dwm_tweak")
            if self.cpu_page.chk_dpc.isChecked() != self.cpu_page.chk_dpc.applied_state:
                pending_keys.append("enable_dpc_latency_tweak")
            if self.cpu_page.chk_timer_res.isChecked() != self.cpu_page.chk_timer_res.applied_state:
                pending_keys.append("enable_timer_resolution_tweak")
            if self.cpu_page.chk_naraka_priority.isChecked() != self.cpu_page.chk_naraka_priority.applied_state:
                pending_keys.append("enable_naraka_priority")
            if self.cpu_page.chk_child.isChecked() != self.cpu_page.chk_child.applied_state:
                pending_keys.append("enable_child_optimization")
            if self.cpu_page.chk_driver_prio.isChecked() != self.cpu_page.chk_driver_prio.applied_state:
                pending_keys.append("enable_driver_priority_tweak")
            if self.cpu_page.chk_vulnerable_driver_blocklist.applied_state is not None and self.cpu_page.chk_vulnerable_driver_blocklist.isChecked() != self.cpu_page.chk_vulnerable_driver_blocklist.applied_state:
                pending_keys.append("enable_vulnerable_driver_blocklist_tweak")
            if self.cpu_page.chk_power_throttling.applied_state is not None and self.cpu_page.chk_power_throttling.isChecked() != self.cpu_page.chk_power_throttling.applied_state:
                pending_keys.append("enable_power_throttling_tweak")
                
            # Peripheral Tab
            if self.peripheral_page.chk_usb_lat.isChecked() != self.peripheral_page.chk_usb_lat.applied_state:
                pending_keys.append("enable_usb_low_latency_tweak")
            if self.peripheral_page.chk_imod.isChecked() != self.peripheral_page.chk_imod.applied_state:
                pending_keys.append("enable_usb_imod_tweak")
            if self.peripheral_page.chk_mouse_lat.isChecked() != self.peripheral_page.chk_mouse_lat.applied_state:
                pending_keys.append("enable_mouse_latency_tweak")
            if self.peripheral_page.chk_sticky_keys.applied_state is not None and self.peripheral_page.chk_sticky_keys.isChecked() != self.peripheral_page.chk_sticky_keys.applied_state:
                pending_keys.append("enable_sticky_keys_tweak")
            if self.peripheral_page.chk_dynamic_lighting.applied_state is not None and self.peripheral_page.chk_dynamic_lighting.isChecked() != self.peripheral_page.chk_dynamic_lighting.applied_state:
                pending_keys.append("enable_dynamic_lighting_tweak")
                
            # GPU Tab
            if self.gpu_page.chk_preemption.isChecked() != self.gpu_page.chk_preemption.applied_state:
                pending_keys.append("disable_gpu_preemption")
            if self.gpu_page.chk_dwm_wet.isChecked() != self.gpu_page.chk_dwm_wet.applied_state:
                pending_keys.append("enable_dwm_super_wet_tweak")
            if self.gpu_page.chk_directx.isChecked() != self.gpu_page.chk_directx.applied_state:
                pending_keys.append("enable_directx_tweaks")
            if self.gpu_page.chk_gpu_firmware.isChecked() != self.gpu_page.chk_gpu_firmware.applied_state:
                pending_keys.append("enable_gpu_firmware_tweak")
            if self.gpu_page.chk_gpu_pstate.isChecked() != self.gpu_page.chk_gpu_pstate.applied_state:
                pending_keys.append("enable_gpu_pstate_tweak")
            if self.gpu_page.chk_intel_plan.isChecked() != self.gpu_page.chk_intel_plan.applied_state:
                pending_keys.append("enable_custom_power_plan")
            if self.gpu_page.chk_amd_plan.isChecked() != self.gpu_page.chk_amd_plan.applied_state:
                pending_keys.append("enable_custom_power_plan")
            if self.gpu_page.chk_gpu_opt.isChecked() != self.gpu_page.chk_gpu_opt.applied_state:
                pending_keys.append("enable_gpu_optimization")
            if self.gpu_page.chk_pcipower.isChecked() != self.gpu_page.chk_pcipower.applied_state:
                pending_keys.append("disable_pcipower")
            if self.gpu_page.chk_gpu_irq.isChecked() != self.gpu_page.chk_gpu_irq.applied_state:
                pending_keys.append("enable_gpu_irq_tweak")
            if self.gpu_page.chk_hags.isChecked() != self.gpu_page.chk_hags.applied_state:
                pending_keys.append("disable_hags")
            if self.gpu_page.chk_gpu_nip.isChecked() != self.gpu_page.chk_gpu_nip.applied_state:
                pending_keys.append("enable_nvidia_nip")
            if self.gpu_page.chk_gpu_msi.applied_state is not None:
                expected_msi = 2 if self.gpu_page.chk_gpu_msi.isChecked() else 0
                if self.gpu_page.chk_gpu_msi.applied_state != expected_msi:
                    pending_keys.append("enable_gpu_msi_tweak")
            if self.gpu_page.chk_global_fse.applied_state is not None and self.gpu_page.chk_global_fse.isChecked() != self.gpu_page.chk_global_fse.applied_state:
                pending_keys.append("enable_global_fse_tweak")
            if self.gpu_page.chk_game_fse.applied_state is not None and self.gpu_page.chk_game_fse.isChecked() != self.gpu_page.chk_game_fse.applied_state:
                pending_keys.append("enable_game_fse_tweak")
            if self.gpu_page.chk_dwm_presentation.applied_state is not None and self.gpu_page.chk_dwm_presentation.isChecked() != self.gpu_page.chk_dwm_presentation.applied_state:
                pending_keys.append("enable_dwm_presentation_tweak")
            if self.gpu_page.chk_game_gpu_preference.applied_state is not None and self.gpu_page.chk_game_gpu_preference.isChecked() != self.gpu_page.chk_game_gpu_preference.applied_state:
                pending_keys.append("enable_game_gpu_preference_tweak")
            if self.gpu_page.chk_irq_affinity.applied_state is not None and self.gpu_page.chk_irq_affinity.isChecked() != self.gpu_page.chk_irq_affinity.applied_state:
                pending_keys.append("enable_irq_affinity_tweak")
                
            # Memory Tab
            if self.memory_page.chk_ram_opt.isChecked() != self.memory_page.chk_ram_opt.applied_state:
                pending_keys.append("enable_ram_optimization")
            if self.memory_page.chk_nvme_opt.isChecked() != self.memory_page.chk_nvme_opt.applied_state:
                pending_keys.append("enable_nvme_optimization")
            if self.memory_page.chk_memory_comp.isChecked() != self.memory_page.chk_memory_comp.applied_state:
                pending_keys.append("disable_memory_compression")
            if self.memory_page.chk_config_alloc.isChecked() != self.memory_page.chk_config_alloc.applied_state:
                pending_keys.append("enable_config_alloc_tweak")
            if self.memory_page.chk_hard_working_set.applied_state is not None and self.memory_page.chk_hard_working_set.isChecked() != self.memory_page.chk_hard_working_set.applied_state:
                pending_keys.append("enable_hard_working_set")
            if self.memory_page.chk_storage_msi.applied_state is not None:
                expected_msi = 2 if self.memory_page.chk_storage_msi.isChecked() else 0
                if self.memory_page.chk_storage_msi.applied_state != expected_msi:
                    pending_keys.append("enable_storage_msi_tweak")
            if self.memory_page.chk_prefetcher.applied_state is not None and self.memory_page.chk_prefetcher.isChecked() != self.memory_page.chk_prefetcher.applied_state:
                pending_keys.append("enable_prefetcher_tweak")
                
            # Privacy Tab
            if self.privacy_page.chk_services.isChecked() != self.privacy_page.chk_services.applied_state:
                pending_keys.append("disable_useless_services")
            if self.privacy_page.chk_wsearch.isChecked() != self.privacy_page.chk_wsearch.applied_state:
                pending_keys.append("disable_wsearch_tweak")
            if self.privacy_page.chk_spectre.isChecked() != self.privacy_page.chk_spectre.applied_state:
                pending_keys.append("disable_spectre_meltdown")
            if self.privacy_page.chk_copilot.isChecked() != self.privacy_page.chk_copilot.applied_state:
                pending_keys.append("disable_copilot")
            if self.privacy_page.chk_gamedvr.isChecked() != self.privacy_page.chk_gamedvr.applied_state:
                pending_keys.append("disable_gamedvr")
            if self.privacy_page.chk_dev_power.isChecked() != self.privacy_page.chk_dev_power.applied_state:
                pending_keys.append("enable_device_power_tweak")
            if self.privacy_page.chk_uac.isChecked() != self.privacy_page.chk_uac.applied_state:
                pending_keys.append("enable_uac_tweak")
            if self.privacy_page.chk_desktop_heap.isChecked() != self.privacy_page.chk_desktop_heap.applied_state:
                pending_keys.append("enable_desktop_heap_tweak")
            if self.privacy_page.chk_download_maps.isChecked() != self.privacy_page.chk_download_maps.applied_state:
                pending_keys.append("enable_download_maps_tweak")
            if self.privacy_page.chk_bg_apps.isChecked() != self.privacy_page.chk_bg_apps.applied_state:
                pending_keys.append("enable_bg_apps_tweak")
            if self.privacy_page.chk_map_updates.isChecked() != self.privacy_page.chk_map_updates.applied_state:
                pending_keys.append("enable_map_updates_tweak")
            if self.privacy_page.chk_autoshare.isChecked() != self.privacy_page.chk_autoshare.applied_state:
                pending_keys.append("enable_autoshare_tweak")
            if self.privacy_page.chk_autorun.isChecked() != self.privacy_page.chk_autorun.applied_state:
                pending_keys.append("enable_autorun_tweak")
            if self.privacy_page.chk_hyperv.isChecked() != self.privacy_page.chk_hyperv.applied_state:
                pending_keys.append("disable_hyperv_virtualization")
            if self.privacy_page.chk_security_notifications.isChecked() != self.privacy_page.chk_security_notifications.applied_state:
                pending_keys.append("disable_security_notifications")
            if self.privacy_page.chk_defender.isChecked() != self.privacy_page.chk_defender.applied_state:
                pending_keys.append("disable_defender")
            if self.privacy_page.chk_smartscreen.isChecked() != self.privacy_page.chk_smartscreen.applied_state:
                pending_keys.append("disable_smartscreen")
            if self.privacy_page.chk_firewall.isChecked() != self.privacy_page.chk_firewall.applied_state:
                pending_keys.append("disable_firewall")
            if self.privacy_page.chk_settings_sync.applied_state is not None and self.privacy_page.chk_settings_sync.isChecked() != self.privacy_page.chk_settings_sync.applied_state:
                pending_keys.append("enable_settings_sync_tweak")
            if self.privacy_page.chk_xbox_save.applied_state is not None and self.privacy_page.chk_xbox_save.isChecked() != self.privacy_page.chk_xbox_save.applied_state:
                pending_keys.append("enable_xbox_save_tweak")
            if self.privacy_page.chk_store_auto_update.applied_state is not None and self.privacy_page.chk_store_auto_update.isChecked() != self.privacy_page.chk_store_auto_update.applied_state:
                pending_keys.append("enable_store_auto_update_tweak")
            if self.privacy_page.chk_web_search.applied_state is not None and self.privacy_page.chk_web_search.isChecked() != self.privacy_page.chk_web_search.applied_state:
                pending_keys.append("enable_web_search_tweak")
            if self.privacy_page.chk_telemetry_tasks.applied_state is not None and self.privacy_page.chk_telemetry_tasks.isChecked() != self.privacy_page.chk_telemetry_tasks.applied_state:
                pending_keys.append("enable_telemetry_tasks_tweak")
            if self.privacy_page.chk_extreme_debloat.applied_state is not None and self.privacy_page.chk_extreme_debloat.isChecked() != self.privacy_page.chk_extreme_debloat.applied_state:
                pending_keys.append("enable_extreme_debloat_tweak")
                
            # Network Tab
            if self.network_page.chk_network.isChecked() != self.network_page.chk_network.applied_state:
                pending_keys.append("enable_network_tweak")
            if self.network_page.chk_ult_net.isChecked() != self.network_page.chk_ult_net.applied_state:
                pending_keys.append("enable_ultimate_network_tweak")
            if self.network_page.chk_dns.isChecked() != self.network_page.chk_dns.applied_state:
                pending_keys.append("enable_dns_tweak")
            if self.network_page.chk_net_imod.applied_state is not None and self.network_page.chk_net_imod.isChecked() != self.network_page.chk_net_imod.applied_state:
                pending_keys.append("enable_net_imod_tweak")
            if self.network_page.chk_net_bindings.applied_state is not None and self.network_page.chk_net_bindings.isChecked() != self.network_page.chk_net_bindings.applied_state:
                pending_keys.append("enable_net_bindings_tweak")
            if self.network_page.chk_wifi_tweak.applied_state is not None and self.network_page.chk_wifi_tweak.isChecked() != self.network_page.chk_wifi_tweak.applied_state:
                pending_keys.append("enable_wifi_tweak")
            if self.network_page.chk_tcp_bbr.applied_state is not None and self.network_page.chk_tcp_bbr.isChecked() != self.network_page.chk_tcp_bbr.applied_state:
                pending_keys.append("enable_tcp_bbr_tweak")
            if self.network_page.chk_eee.applied_state is not None and self.network_page.chk_eee.isChecked() != self.network_page.chk_eee.applied_state:
                pending_keys.append("enable_eee_tweak")
            if self.network_page.chk_network_msi.applied_state is not None:
                expected_msi = 2 if self.network_page.chk_network_msi.isChecked() else 0
                if self.network_page.chk_network_msi.applied_state != expected_msi:
                    pending_keys.append("enable_network_msi_tweak")
                    
            # UX Tab (in Optimization Page)
            if self.optimization_page.chk_visual_effects.isChecked() != self.optimization_page.chk_visual_effects.applied_state:
                pending_keys.append("disable_windows_visual_effects")
            if self.optimization_page.chk_transparency.isChecked() != self.optimization_page.chk_transparency.applied_state:
                pending_keys.append("disable_windows_transparency")
            if self.optimization_page.chk_consult_interests.isChecked() != self.optimization_page.chk_consult_interests.applied_state:
                pending_keys.append("enable_consult_interests_tweak")
            if self.optimization_page.chk_tips_suggestions.isChecked() != self.optimization_page.chk_tips_suggestions.applied_state:
                pending_keys.append("enable_tips_suggestions_tweak")
            if self.optimization_page.chk_widgets.applied_state is not None and self.optimization_page.chk_widgets.isChecked() != self.optimization_page.chk_widgets.applied_state:
                pending_keys.append("enable_widgets_tweak")
            if self.optimization_page.chk_startup_delay.applied_state is not None and self.optimization_page.chk_startup_delay.isChecked() != self.optimization_page.chk_startup_delay.applied_state:
                pending_keys.append("enable_startup_delay_tweak")
            if self.optimization_page.chk_menu_delay.applied_state is not None and self.optimization_page.chk_menu_delay.isChecked() != self.optimization_page.chk_menu_delay.applied_state:
                pending_keys.append("enable_menu_delay_tweak")
            if self.optimization_page.chk_prevent_device_encryption.applied_state is not None and self.optimization_page.chk_prevent_device_encryption.isChecked() != self.optimization_page.chk_prevent_device_encryption.applied_state:
                pending_keys.append("enable_prevent_device_encryption_tweak")
            if self.optimization_page.chk_spotlight.applied_state is not None and self.optimization_page.chk_spotlight.isChecked() != self.optimization_page.chk_spotlight.applied_state:
                pending_keys.append("enable_spotlight_tweak")
            
        return pending_keys

    def clear_preset_highlights(self):
        for page in [self.general_page, self.cpu_page, self.peripheral_page,
                     self.gpu_page, self.memory_page, self.privacy_page,
                     self.network_page, self.tools_page]:
            if hasattr(page, 'presetPanel'):
                page.presetPanel.highlight_preset("")

    def load_preset(self, preset_type: str):
        """
        Loads a pre-configured set of performance tweaks depending on the preset type:
        'default', 'optimal', or 'maximum'.
        Directly sets UI element states (which will trigger pending statuses and show correct counts on side panel).
        """
        logger.info(f"Loading optimization preset: {preset_type}")
        self.is_loading_preset = True

        # 1. Helper to set switch card value (all switches physically reside on optimization_page)
        def set_sw(card_name, val):
            if self.optimization_page.page is not None:
                try:
                    card = getattr(self.optimization_page, card_name, None)
                    if card and hasattr(card, 'switchButton'):
                        card.switchButton.setChecked(val)
                except AttributeError:
                    pass

        # 2. Helper to set combo card index (all combos physically reside on general_page)
        def set_cb(card_name, idx):
            if self.general_page.page is not None:
                try:
                    card = getattr(self.general_page, card_name, None)
                    if card and hasattr(card, 'comboBox'):
                        card.comboBox.setCurrentIndex(idx)
                except AttributeError:
                    pass

        try:
            PRIO_SEP_VALUES = [2, 20, 21, 22, 24, 25, 26, 36, 37, 38, 40, 41, 42]
            kb_list = [100, 50, 30, 20, 16, 12, 10, 8, 6]
            m_list = [100, 50, 30, 20, 16, 12, 10, 8]

            if preset_type == "default":
                # 新增 15 项默认关闭
                set_sw("chk_widgets", False)
                set_sw("chk_sticky_keys", False)
                set_sw("chk_startup_delay", False)
                set_sw("chk_menu_delay", False)
                set_sw("chk_settings_sync", False)
                set_sw("chk_dynamic_lighting", False)
                set_sw("chk_gpu_msi", False)
                set_sw("chk_xbox_save", False)
                set_sw("chk_store_auto_update", False)
                set_sw("chk_vulnerable_driver_blocklist", False)
                set_sw("chk_prevent_device_encryption", False)
                set_sw("chk_spotlight", False)
                set_sw("chk_hard_working_set", False)
                set_sw("chk_net_imod", False)
                set_sw("chk_net_bindings", False)
                set_sw("chk_power_throttling", False)
                set_sw("chk_tcp_bbr", False)
                set_sw("chk_eee", False)
                set_sw("chk_web_search", False)
                set_sw("chk_telemetry_tasks", False)
                set_sw("chk_extreme_debloat", False)
                set_sw("chk_prefetcher", False)
                set_sw("chk_network_msi", False)
                set_sw("chk_storage_msi", False)
                set_sw("chk_dwm_presentation", False)
                set_sw("chk_client_priority_demote", False)

                # General Page (actually physically optimization_page now)
                set_sw("chk_iso", True)
                set_sw("chk_dog", True)
                set_sw("chk_visual_effects", False)
                set_sw("chk_transparency", False)
                set_sw("chk_consult_interests", False)
                set_sw("chk_tips_suggestions", False)

                # CPU Page
                set_sw("chk_parking", False)
                set_sw("chk_epp", False)
                set_sw("chk_hpet", False)
                set_cb("win32_prio_card", PRIO_SEP_VALUES.index(21))
                set_sw("chk_dwm", False)
                set_sw("chk_dpc", False)
                set_sw("chk_timer_res", False)
                set_sw("chk_naraka_priority", False)
                set_sw("chk_child", True)
                set_sw("chk_driver_prio", False)

                # Peripheral Page
                set_cb("keyboard_queue_card", kb_list.index(100))
                set_cb("mouse_queue_card", m_list.index(100))
                set_cb("keyboard_repeat_rate_card", 0)
                set_sw("chk_usb_lat", False)
                set_sw("chk_imod", False)
                set_sw("chk_mouse_lat", False)

                # GPU Page
                set_sw("chk_preemption", False)
                set_sw("chk_dwm_wet", False)
                set_sw("chk_directx", False)
                set_sw("chk_gpu_firmware", False)
                set_sw("chk_gpu_pstate", False)
                set_sw("chk_intel_plan", False)
                set_sw("chk_amd_plan", False)
                set_sw("chk_gpu_opt", False)
                set_sw("chk_pcipower", False)
                set_sw("chk_gpu_irq", False)
                set_sw("chk_hags", False)

                # Memory Page
                set_sw("chk_ram_opt", False)
                set_sw("chk_nvme_opt", False)
                set_sw("chk_memory_comp", False)
                set_sw("chk_config_alloc", False)

                # Privacy Page
                set_sw("chk_services", False)
                set_sw("chk_wsearch", False)
                set_sw("chk_spectre", False)
                set_sw("chk_copilot", False)
                set_sw("chk_gamedvr", False)
                set_sw("chk_dev_power", False)
                set_sw("chk_uac", False)
                set_sw("chk_desktop_heap", False)
                set_sw("chk_download_maps", False)
                set_sw("chk_bg_apps", False)
                set_sw("chk_map_updates", False)
                set_sw("chk_autoshare", False)
                set_sw("chk_autorun", False)
                set_sw("chk_hyperv", False)
                set_sw("chk_security_notifications", False)
                set_sw("chk_defender", False)
                set_sw("chk_smartscreen", False)
                set_sw("chk_firewall", False)

                # Network Page
                set_sw("chk_network", False)
                set_sw("chk_ult_net", False)
                set_sw("chk_dns", False)

            elif preset_type == "optimal":
                # 新增 15 项推荐配置
                set_sw("chk_widgets", True)
                set_sw("chk_sticky_keys", True)
                set_sw("chk_startup_delay", True)
                set_sw("chk_menu_delay", True)
                set_sw("chk_settings_sync", True)
                set_sw("chk_dynamic_lighting", False)
                set_sw("chk_gpu_msi", True)
                set_sw("chk_xbox_save", True)
                set_sw("chk_store_auto_update", True)
                set_sw("chk_vulnerable_driver_blocklist", False)
                set_sw("chk_prevent_device_encryption", True)
                set_sw("chk_spotlight", True)
                set_sw("chk_hard_working_set", True)
                set_sw("chk_net_imod", True)
                set_sw("chk_net_bindings", False)
                set_sw("chk_power_throttling", True)
                set_sw("chk_tcp_bbr", True)
                set_sw("chk_eee", True)
                set_sw("chk_web_search", True)
                set_sw("chk_telemetry_tasks", True)
                set_sw("chk_extreme_debloat", True)
                set_sw("chk_prefetcher", True)
                set_sw("chk_network_msi", True)
                set_sw("chk_storage_msi", True)
                set_sw("chk_dwm_presentation", True)
                set_sw("chk_client_priority_demote", True)

                # General Page
                set_sw("chk_iso", True)
                set_sw("chk_dog", True)
                set_sw("chk_visual_effects", False)
                set_sw("chk_transparency", True)
                set_sw("chk_consult_interests", True)
                set_sw("chk_tips_suggestions", True)

                # CPU Page
                set_sw("chk_parking", True)
                set_sw("chk_epp", True)
                set_sw("chk_hpet", True)
                set_cb("win32_prio_card", PRIO_SEP_VALUES.index(26))
                set_sw("chk_dwm", True)
                set_sw("chk_dpc", True)
                set_sw("chk_timer_res", True)
                set_sw("chk_naraka_priority", True)
                set_sw("chk_child", True)
                set_sw("chk_driver_prio", True)

                # Peripheral Page
                set_cb("keyboard_queue_card", kb_list.index(16))
                set_cb("mouse_queue_card", m_list.index(16))
                set_cb("keyboard_repeat_rate_card", 2)
                set_sw("chk_usb_lat", True)
                set_sw("chk_imod", False)
                set_sw("chk_mouse_lat", True)

                # GPU Page
                set_sw("chk_preemption", False)
                set_sw("chk_dwm_wet", True)
                set_sw("chk_directx", True)
                set_sw("chk_gpu_firmware", True)
                set_sw("chk_gpu_pstate", True)
                set_sw("chk_intel_plan", True)
                set_sw("chk_amd_plan", True)
                set_sw("chk_gpu_opt", True)
                set_sw("chk_pcipower", True)
                set_sw("chk_gpu_irq", True)
                set_sw("chk_hags", False)
                set_sw("chk_global_fse", False)

                # Memory Page
                set_sw("chk_ram_opt", True)
                set_sw("chk_nvme_opt", True)
                set_sw("chk_memory_comp", False)
                set_sw("chk_config_alloc", True)

                # Privacy Page
                set_sw("chk_services", True)
                set_sw("chk_wsearch", True)
                set_sw("chk_spectre", False)
                set_sw("chk_copilot", True)
                set_sw("chk_gamedvr", True)
                set_sw("chk_dev_power", True)
                set_sw("chk_uac", False)
                set_sw("chk_desktop_heap", True)
                set_sw("chk_download_maps", True)
                set_sw("chk_bg_apps", True)
                set_sw("chk_map_updates", True)
                set_sw("chk_autoshare", True)
                set_sw("chk_autorun", True)
                set_sw("chk_hyperv", False)
                set_sw("chk_security_notifications", True)
                set_sw("chk_defender", False)
                set_sw("chk_smartscreen", False)
                set_sw("chk_firewall", False)

                # Network Page
                set_sw("chk_network", True)
                set_sw("chk_ult_net", True)
                set_sw("chk_dns", True)

            elif preset_type == "maximum":
                # 新增 15 项极限拉满
                set_sw("chk_widgets", True)
                set_sw("chk_sticky_keys", True)
                set_sw("chk_startup_delay", True)
                set_sw("chk_menu_delay", True)
                set_sw("chk_settings_sync", True)
                set_sw("chk_dynamic_lighting", True)
                set_sw("chk_gpu_msi", True)
                set_sw("chk_xbox_save", True)
                set_sw("chk_store_auto_update", True)
                set_sw("chk_vulnerable_driver_blocklist", True)
                set_sw("chk_prevent_device_encryption", True)
                set_sw("chk_spotlight", True)
                set_sw("chk_hard_working_set", True)
                set_sw("chk_net_imod", True)
                set_sw("chk_net_bindings", True)
                set_sw("chk_power_throttling", True)
                set_sw("chk_tcp_bbr", True)
                set_sw("chk_eee", True)
                set_sw("chk_web_search", True)
                set_sw("chk_telemetry_tasks", True)
                set_sw("chk_extreme_debloat", True)
                set_sw("chk_prefetcher", True)
                set_sw("chk_network_msi", True)
                set_sw("chk_storage_msi", True)
                set_sw("chk_dwm_presentation", True)
                set_sw("chk_client_priority_demote", True)

                # General Page
                set_sw("chk_iso", True)
                set_sw("chk_dog", True)
                set_sw("chk_visual_effects", True)
                set_sw("chk_transparency", True)
                set_sw("chk_consult_interests", True)
                set_sw("chk_tips_suggestions", True)

                # CPU Page
                set_sw("chk_parking", True)
                set_sw("chk_epp", True)
                set_sw("chk_hpet", True)
                set_cb("win32_prio_card", PRIO_SEP_VALUES.index(26))
                set_sw("chk_dwm", True)
                set_sw("chk_dpc", True)
                set_sw("chk_timer_res", True)
                set_sw("chk_naraka_priority", True)
                set_sw("chk_child", True)
                set_sw("chk_driver_prio", True)

                # Peripheral Page
                set_cb("keyboard_queue_card", kb_list.index(16))
                set_cb("mouse_queue_card", m_list.index(16))
                set_cb("keyboard_repeat_rate_card", 4)
                set_sw("chk_usb_lat", True)
                set_sw("chk_imod", True)
                set_sw("chk_mouse_lat", True)

                # GPU Page
                set_sw("chk_preemption", True)
                set_sw("chk_dwm_wet", True)
                set_sw("chk_directx", True)
                set_sw("chk_gpu_firmware", True)
                set_sw("chk_gpu_pstate", True)
                set_sw("chk_intel_plan", True)
                set_sw("chk_amd_plan", True)
                set_sw("chk_gpu_opt", True)
                set_sw("chk_pcipower", True)
                set_sw("chk_gpu_irq", True)
                set_sw("chk_hags", True)
                set_sw("chk_global_fse", True)

                # Memory Page
                set_sw("chk_ram_opt", True)
                set_sw("chk_nvme_opt", True)
                set_sw("chk_memory_comp", True)
                set_sw("chk_config_alloc", True)

                # Privacy Page
                set_sw("chk_services", True)
                set_sw("chk_wsearch", True)
                set_sw("chk_spectre", True)
                set_sw("chk_copilot", True)
                set_sw("chk_gamedvr", True)
                set_sw("chk_dev_power", True)
                set_sw("chk_uac", True)
                set_sw("chk_desktop_heap", True)
                set_sw("chk_download_maps", True)
                set_sw("chk_bg_apps", True)
                set_sw("chk_map_updates", True)
                set_sw("chk_autoshare", True)
                set_sw("chk_autorun", True)
                set_sw("chk_hyperv", True)
                set_sw("chk_security_notifications", True)
                set_sw("chk_defender", True)
                set_sw("chk_smartscreen", True)
                set_sw("chk_firewall", True)

                # Network Page
                set_sw("chk_network", True)
                set_sw("chk_ult_net", True)
                set_sw("chk_dns", True)
        finally:
            self.is_loading_preset = False

        # Highlight preset buttons
        for page in [self.general_page, self.optimization_page, self.tools_page]:
            if page.page is not None and hasattr(page, 'presetPanel'):
                page.presetPanel.highlight_preset(preset_type)

        # Triggers side bar pending count updates
        self.update_cpu_power_cards_relation()
        self.update_pending_status()

        # Show feedback
        msg = {
            "default": "系统默认初始状态配置已装载",
            "optimal": "推荐性能调优方案已装载",
            "maximum": "极限性能调度方案已装载"
        }[preset_type]
        
        active_widget = self.stackedWidget.currentWidget()
        InfoBar.success("配置方案装载成功", f"{msg}。请返回仪表盘主页，并点击[应用系统与进程关联配置]以使更改在系统内核中生效。", parent=active_widget or self)

    def update_fps_collector_lifecycle(self):
        """
        Controls the initialization, update and destruction of the FpsCollectorService thread
        and GameOverlay topmost view based on user settings and target process states.
        """
        if getattr(self, 'is_initializing', False):
            return
        target_name = None
        target_pid = None
        
        # 1. Prefer watchdog target game PID
        if getattr(self, 'current_pid', None) is not None:
            target_pid = self.current_pid
            target_name = self.settings.target_process_name
        # 2. Manual select target game PID
        elif getattr(self, 'target_pid', None) is not None:
            target_pid = self.target_pid
            target_name = self.settings.target_process_name
        # 3. Setting target process name
        elif self.settings.target_process_name:
            target_name = self.settings.target_process_name

        if self.settings.enable_fps_overlay:
            # A. Create overlay if it doesn't exist
            if not self.overlay:
                self.overlay = GameOverlay(self.settings, None)
                self.overlay.show()
                logger.info("OSD Overlay created and shown.")
            
            # B. Start or update collector
            if not self.fps_collector:
                self.fps_collector = FpsCollectorService(target_proc_name=target_name, target_pid=target_pid)
                self.fps_collector.stats_updated.connect(lambda stats: self.overlay.update_stats(stats) if self.overlay else None)
                self.fps_collector.status_msg.connect(lambda msg: self.overlay.lbl_game_title.setText(msg) if (self.overlay and hasattr(self.overlay, 'lbl_game_title') and self.overlay.lbl_game_title) else None)
                self.fps_collector.start()
                logger.info(f"OSD FpsCollectorService started (target: {target_name}, PID: {target_pid}).")
            else:
                # Update target dynamically if it changed
                if self.fps_collector.target_proc_name != target_name or self.fps_collector.target_pid != target_pid:
                    self.fps_collector.target_proc_name = target_name
                    self.fps_collector.target_pid = target_pid
                    logger.info(f"OSD FpsCollectorService target updated dynamically to Name: {target_name}, PID: {target_pid}.")
        else:
            # C. Tear down
            if self.fps_collector:
                logger.info("Stopping OSD FpsCollectorService...")
                self.fps_collector.stop()
                self.fps_collector.wait()
                self.fps_collector = None
            if self.overlay:
                logger.info("Closing OSD Overlay...")
                self.overlay.close()
                self.overlay = None

    def register_global_hotkey(self):
        """
        Registers the global hotkey (e.g. Ctrl+Shift+O) via RegisterHotKey to a background thread.
        """
        if getattr(self, 'is_initializing', False):
            return
        hotkey_str = self.settings.fps_overlay_hotkey
        if not hotkey_str:
            self.unregister_global_hotkey()
            return
            
        if (hasattr(self, 'current_registered_hotkey') and 
            self.current_registered_hotkey == hotkey_str and 
            hasattr(self, 'hotkey_thread') and 
            self.hotkey_thread is not None):
            return
            
        self.unregister_global_hotkey()
        
        mods = 0
        key_code = 0
        
        vk_map = {
            "space": 0x20,      # VK_SPACE
            "enter": 0x0D,      # VK_RETURN
            "tab": 0x09,        # VK_TAB
            "backspace": 0x08,  # VK_BACK
            "delete": 0x2E,     # VK_DELETE
            "insert": 0x2D,     # VK_INSERT
            "home": 0x24,       # VK_HOME
            "end": 0x23,        # VK_END
            "pageup": 0x21,     # VK_PRIOR
            "pagedown": 0x22,   # VK_NEXT
            "left": 0x25,       # VK_LEFT
            "right": 0x27,      # VK_RIGHT
            "up": 0x26,         # VK_UP
            "down": 0x28,       # VK_DOWN
            "-": 0xBD,          # VK_OEM_MINUS
            "=": 0xBB,          # VK_OEM_PLUS
            "[": 0xDB,          # VK_OEM_4
            "]": 0xDD,          # VK_OEM_6
            ";": 0xBA,          # VK_OEM_1
            "'": 0xDE,          # VK_OEM_7
            ",": 0xBC,          # VK_OEM_COMMA
            ".": 0xBE,          # VK_OEM_PERIOD
            "/": 0xBF,          # VK_OEM_2
            "\\": 0xDC,         # VK_OEM_5
            "~": 0xC0,          # VK_OEM_3
        }
        
        parts = hotkey_str.split('+')
        for part in parts:
            part = part.strip().lower()
            if part == "ctrl":
                mods |= 0x0002  # MOD_CONTROL
            elif part == "shift":
                mods |= 0x0004  # MOD_SHIFT
            elif part == "alt":
                mods |= 0x0001  # MOD_ALT
            elif part == "win":
                mods |= 0x0008  # MOD_WIN
            else:
                if part in vk_map:
                    key_code = vk_map[part]
                elif len(part) == 1:
                    key_code = ord(part.upper())
                elif part.startswith('f') and part[1:].isdigit():
                    f_num = int(part[1:])
                    key_code = 0x6F + f_num
                    
        if key_code > 0:
            # Register hotkey with thread-level message queue
            # MOD_NOREPEAT = 0x4000
            self.hotkey_thread = HotkeyListenerThread(mods | 0x4000, key_code, self)
            self.hotkey_thread.triggered.connect(self.toggle_osd_visibility)
            self.hotkey_thread.start()
            self.hotkey_registered = True
            self.current_registered_hotkey = hotkey_str
            logger.info(f"Registered global hotkey '{hotkey_str}' via background listener thread.")

    def unregister_global_hotkey(self):
        """
        Unregisters the global OSD hotkey thread.
        """
        if getattr(self, 'hotkey_registered', False) or (hasattr(self, 'hotkey_thread') and self.hotkey_thread):
            if hasattr(self, 'hotkey_thread') and self.hotkey_thread:
                try:
                    self.hotkey_thread.stop()
                    self.hotkey_thread.wait(300)
                except Exception as e:
                    logger.error(f"Error stopping hotkey listener thread: {e}")
                self.hotkey_thread = None
            self.hotkey_registered = False
            self.current_registered_hotkey = None
            logger.info("Unregistered global hotkey listener thread.")

    def toggle_osd_visibility(self):
        """
        Toggles OSD Overlay display state via global hotkey.
        """
        new_state = not self.settings.enable_fps_overlay
        self.settings.enable_fps_overlay = new_state
        self.general_page.chk_osd.setChecked(new_state)
        self.save_settings()
        
        # Feedback Notification
        if new_state:
            InfoBar.success("OSD 已开启", "游戏性能悬浮监视器已唤出", parent=self)
        else:
            InfoBar.info("OSD 已隐藏", "游戏性能悬浮监视器已关闭", parent=self)

    def start_input_hook_thread(self):
        if getattr(self, 'is_initializing', False):
            return
        try:
            self.input_hook_thread = GlobalInputHookThread(self)
            self.input_hook_thread.update_hotkey(self.settings.rate_limiter_hotkey_code, self.settings.rate_limiter_hotkey_type)
            self.input_hook_thread.direct_press_cb = self.on_rate_limiter_pressed_direct
            self.input_hook_thread.direct_release_cb = self.on_rate_limiter_released_direct
            self.input_hook_thread.hotkey_pressed.connect(self.on_rate_limiter_pressed)
            self.input_hook_thread.hotkey_released.connect(self.on_rate_limiter_released)
            # Bind MacroManager to hook thread for global recordings & playback triggers
            MacroManager().bind_to_input_hook(self.input_hook_thread)
            self.input_hook_thread.start()
            logger.info("Global keyboard/mouse input hook thread started.")
        except Exception as e:
            logger.error(f"Failed to start GlobalInputHookThread: {str(e)}")

    def update_rate_limiter_cache_vars(self):
        self.rate_limiter_enabled_cached = self.settings.enable_rate_limiter
        self.rate_limiter_mode_cached = self.settings.rate_limiter_mode
        self.rate_limiter_type_cached = self.settings.rate_limiter_type
        self.rate_limiter_value_cached = self.settings.rate_limiter_value
        self.rate_limiter_unit_cached = self.settings.rate_limiter_unit
        self.rate_limiter_pulse_duration_cached = self.settings.rate_limiter_pulse_duration
        self.rate_limiter_pulse_delay_cached = self.settings.rate_limiter_pulse_delay
        self.rate_limiter_direction_cached = self.settings.rate_limiter_direction
        
        # Sync to NetworkThrottlerService globally
        NetworkThrottlerService._current_limit_type = self.settings.rate_limiter_type
        NetworkThrottlerService._current_rate_value = self.settings.rate_limiter_value
        NetworkThrottlerService._current_unit = self.settings.rate_limiter_unit

    def on_rate_limiter_pressed_direct(self):
        if not getattr(self, 'rate_limiter_enabled_cached', False):
            return
            
        import time
        now = time.time()
        last_press = getattr(self, '_last_rl_direct_press', 0)
        if now - last_press < 0.15:
            return
        self._last_rl_direct_press = now
        
        mode = getattr(self, 'rate_limiter_mode_cached', 'hold')
        limit_type = getattr(self, 'rate_limiter_type_cached', 'qos')
        rate_val = getattr(self, 'rate_limiter_value_cached', 100.0)
        unit = getattr(self, 'rate_limiter_unit_cached', 'KB/s')
        pulse_duration = getattr(self, 'rate_limiter_pulse_duration_cached', 3000.0)
        pulse_delay = getattr(self, 'rate_limiter_pulse_delay_cached', 0.0)
        
        # Atomically flip throttling flag in memory in 0.00ms
        if mode == "toggle":
            if NetworkThrottlerService._is_throttling:
                NetworkThrottlerService._is_throttling = False
                wd_alive = (NetworkThrottlerService._wd_thread is not None and NetworkThrottlerService._wd_thread.is_alive())
                if not wd_alive:
                    self.apply_rate_limiter(False)
            else:
                NetworkThrottlerService._current_limit_type = limit_type
                NetworkThrottlerService._current_rate_value = rate_val
                NetworkThrottlerService._current_unit = unit
                NetworkThrottlerService._is_throttling = True
                wd_alive = (NetworkThrottlerService._wd_thread is not None and NetworkThrottlerService._wd_thread.is_alive())
                if not wd_alive:
                    self.apply_rate_limiter(True)
        elif mode == "hold":
            NetworkThrottlerService._current_limit_type = limit_type
            NetworkThrottlerService._current_rate_value = rate_val
            NetworkThrottlerService._current_unit = unit
            NetworkThrottlerService._is_throttling = True
            wd_alive = (NetworkThrottlerService._wd_thread is not None and NetworkThrottlerService._wd_thread.is_alive())
            if not wd_alive:
                self.apply_rate_limiter(True)
        elif mode == "pulse":
            if NetworkThrottlerService._is_throttling:
                NetworkThrottlerService._is_throttling = False
                if NetworkThrottlerService._wd_thread and NetworkThrottlerService._wd_thread.is_alive():
                    try:
                        NetworkThrottlerService._wd_thread.sender_thread.clear(send_remaining=True)
                    except Exception:
                        pass
                if hasattr(self, '_pulse_delay_timer') and self._pulse_delay_timer:
                    try:
                        self._pulse_delay_timer.cancel()
                    except Exception:
                        pass
                if hasattr(self, '_pulse_timer') and self._pulse_timer:
                    try:
                        self._pulse_timer.cancel()
                    except Exception:
                        pass
                NetworkThrottlerService.remove_rate_limit()
            else:
                NetworkThrottlerService._current_limit_type = limit_type
                NetworkThrottlerService._current_rate_value = rate_val
                NetworkThrottlerService._current_unit = unit
                NetworkThrottlerService._is_throttling = True
                
                wd_alive = (NetworkThrottlerService._wd_thread is not None and NetworkThrottlerService._wd_thread.is_alive())
                if not wd_alive:
                    direction = getattr(self, 'rate_limiter_direction_cached', 'both')
                    NetworkThrottlerService.apply_rate_limit(self.target_pid, self.target_name, 0.0, rate_val, unit, limit_type, direction)
                
                if hasattr(self, '_pulse_delay_timer') and self._pulse_delay_timer:
                    try:
                        self._pulse_delay_timer.cancel()
                    except Exception:
                        pass
                
                if hasattr(self, '_pulse_timer') and self._pulse_timer:
                    try:
                        self._pulse_timer.cancel()
                    except Exception:
                        pass
                
                duration_sec = max(0.01, pulse_duration / 1000.0)
                delay_sec = max(0.0, pulse_delay / 1000.0)
                
                def _pulse_release():
                    NetworkThrottlerService._is_throttling = False
                    if NetworkThrottlerService._wd_thread and NetworkThrottlerService._wd_thread.is_alive():
                        try:
                            NetworkThrottlerService._wd_thread.sender_thread.clear(send_remaining=True)
                        except Exception:
                            pass
                    NetworkThrottlerService.remove_rate_limit()
                    
                def _pulse_start():
                    NetworkThrottlerService._is_throttling = True
                    self._pulse_timer = threading.Timer(duration_sec, _pulse_release)
                    self._pulse_timer.daemon = True
                    self._pulse_timer.start()

                import threading
                if delay_sec > 0.001:
                    NetworkThrottlerService._is_throttling = False
                    self._pulse_delay_timer = threading.Timer(delay_sec, _pulse_start)
                    self._pulse_delay_timer.daemon = True
                    self._pulse_delay_timer.start()
                else:
                    _pulse_start()

    def on_rate_limiter_released_direct(self):
        if not getattr(self, 'rate_limiter_enabled_cached', False):
            return
        mode = getattr(self, 'rate_limiter_mode_cached', 'hold')
        if mode == "hold":
            NetworkThrottlerService._is_throttling = False
            # Instantly clear queue on release to prevent late packets
            wd_alive = (NetworkThrottlerService._wd_thread is not None and NetworkThrottlerService._wd_thread.is_alive())
            if wd_alive:
                try:
                    NetworkThrottlerService._wd_thread.sender_thread.clear(send_remaining=True)
                except Exception:
                    pass
            else:
                self.apply_rate_limiter(False)

    def on_rate_limiter_pressed(self):
        if not self.settings.enable_rate_limiter:
            return
        
        mode = self.settings.rate_limiter_mode
        logger.info(f"Rate limiter hotkey pressed. Mode: {mode}, Current State: {self.rate_limiter_state}")
        
        if mode == "toggle":
            if self.rate_limiter_state == "active":
                self.apply_rate_limiter(False)
            else:
                self.apply_rate_limiter(True)
        elif mode == "hold":
            self.apply_rate_limiter(True)
        elif mode == "pulse":
            if self.rate_limiter_state == "active":
                logger.info("Pulse Mode: Key pressed while active. Canceling pulse.")
                self.apply_rate_limiter(False)
            else:
                self.apply_rate_limiter(True)

    def on_rate_limiter_released(self):
        if not self.settings.enable_rate_limiter:
            return
        
        mode = self.settings.rate_limiter_mode
        logger.info(f"Rate limiter hotkey released. Mode: {mode}, Current State: {self.rate_limiter_state}")
        
        if mode == "hold":
            self.apply_rate_limiter(False)

    def check_rate_limiter_physical_key_state(self):
        """
        Watchdog timer to check if the hotkey is physically released,
        bypassing any lost keyup hook events.
        """
        if self.rate_limiter_state == "active" and self.settings.rate_limiter_mode == "hold":
            vk = self.settings.rate_limiter_hotkey_code
            if vk > 0:
                is_down = ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000
                if not is_down:
                    logger.info("Watchdog: Hotkey physically released. Forcing release of rate limiter.")
                    self.apply_rate_limiter(False)
                    if hasattr(self, 'input_hook_thread') and self.input_hook_thread:
                        self.input_hook_thread.is_currently_pressed = False

    def toggle_ocr_overlay(self):
        from core_commander.ui.ocr_overlay import OCROverlayWindow
        if not hasattr(self, 'ocr_overlay_window') or self.ocr_overlay_window is None:
            self.ocr_overlay_window = OCROverlayWindow()
        
        if self.ocr_overlay_window.isVisible():
            self.ocr_overlay_window.hide()
        else:
            self.ocr_overlay_window.showFullScreen()

    def register_ocr_hotkey(self, force=False):
        if getattr(self, 'is_initializing', False):
            return
        if hasattr(self, 'ocr_hotkey_registered') and self.ocr_hotkey_registered and not force:
            return
            
        if force and hasattr(self, 'ocr_hotkey_thread') and self.ocr_hotkey_thread:
            try:
                self.ocr_hotkey_thread.stop()
                self.ocr_hotkey_thread.wait(300)
            except Exception:
                pass
            self.ocr_hotkey_thread = None

        hotkey_str = self.settings.ocr_hotkey
        if not hotkey_str or hotkey_str == "无":
            return
            
        mods = 0
        key_code = 0
        vk_map = {
            "space": 0x20, "enter": 0x0D, "tab": 0x09, "backspace": 0x08, "delete": 0x2E,
            "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
            "left": 0x25, "right": 0x27, "up": 0x26, "down": 0x28, "-": 0xBD, "=": 0xBB,
            "[": 0xDB, "]": 0xDD, ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF,
            "\\": 0xDC, "~": 0xC0
        }
        parts = hotkey_str.split('+')
        for part in parts:
            part = part.strip().lower()
            if part == "ctrl" or part == "control":
                mods |= 0x0002
            elif part == "shift":
                mods |= 0x0004
            elif part == "alt" or part == "meta":
                mods |= 0x0001
            elif part == "win":
                mods |= 0x0008
            else:
                if part in vk_map:
                    key_code = vk_map[part]
                elif len(part) == 1:
                    key_code = ord(part.upper())
                elif part.startswith('f') and part[1:].isdigit():
                    key_code = 0x6F + int(part[1:])
                    
        if key_code > 0:
            try:
                # 998 is an arbitrary id for OCR to distinguish from OSD (which uses default 100 or 101)
                self.ocr_hotkey_thread = HotkeyListenerThread(mods | 0x4000, key_code, self)
                self.ocr_hotkey_thread.triggered.connect(self.toggle_ocr_overlay)
                self.ocr_hotkey_thread.start()
                self.ocr_hotkey_registered = True
                logger.info(f"Registered OCR hotkey '{hotkey_str}' via background listener thread.")
            except Exception as e:
                logger.error(f"Failed to register OCR hotkey {hotkey_str}: {e}")

    def unregister_ocr_hotkey(self):
        """
        Unregisters the global OCR hotkey thread.
        """
        if getattr(self, 'ocr_hotkey_registered', False) or (hasattr(self, 'ocr_hotkey_thread') and self.ocr_hotkey_thread):
            if hasattr(self, 'ocr_hotkey_thread') and self.ocr_hotkey_thread:
                try:
                    self.ocr_hotkey_thread.stop()
                    self.ocr_hotkey_thread.wait(300)
                except Exception as e:
                    logger.error(f"Error stopping OCR hotkey thread: {e}")
                self.ocr_hotkey_thread = None
            self.ocr_hotkey_registered = False
            logger.info("Unregistered global OCR hotkey thread.")

    def apply_rate_limiter(self, active: bool):
        if active:
            logger.info(f"Applying rate limit on target: PID={self.target_pid}, Name={self.target_name} at Down:{self.settings.rate_limiter_download_value} {self.settings.rate_limiter_unit}")
            
            upload_val = getattr(self.settings, 'rate_limiter_upload_value', 0.0)
            
            success = NetworkThrottlerService.apply_rate_limit(
                self.target_pid, 
                self.target_name, 
                upload_val, 
                self.settings.rate_limiter_download_value,
                self.settings.rate_limiter_unit,
                self.settings.rate_limiter_type,
                self.settings.rate_limiter_direction
            )
            if success:
                self.rate_limiter_state = "active"
                logger.info("QoS network policies applied successfully.")
                
                if self.settings.rate_limiter_mode == "pulse":
                    duration_ms = max(10, self.settings.rate_limiter_pulse_duration)
                    delay_ms = max(0, self.settings.rate_limiter_pulse_delay)
                    if hasattr(self, '_pulse_thread_timer') and self._pulse_thread_timer:
                        self._pulse_thread_timer.cancel()
                    import threading
                    self._pulse_thread_timer = threading.Timer((duration_ms + delay_ms) / 1000.0, self.on_ui_pulse_timeout)
                    self._pulse_thread_timer.start()
                elif self.settings.rate_limiter_mode == "hold":
                    self.rate_limiter_watchdog_timer.start(50)  # Check key state every 50ms
            else:
                self.rate_limiter_state = "waiting"
                logger.warning("Failed to apply QoS network policies.")
        else:
            logger.info("Removing rate limit...")
            if self.rate_limiter_watchdog_timer.isActive():
                self.rate_limiter_watchdog_timer.stop()
            success = NetworkThrottlerService.remove_rate_limit()
            if success:
                logger.info("QoS network policies removed successfully.")
            self.rate_limiter_state = "waiting" if self.settings.enable_rate_limiter else "inactive"
            
        self.update_rate_limiter_ui()

    def on_ui_pulse_timeout(self):
        logger.info("UI Pulse Timeout: Restoring UI state.")
        self.apply_rate_limiter(False)
        if hasattr(self, 'input_hook_thread') and self.input_hook_thread:
            vk = self.settings.rate_limiter_hotkey_code
            if vk > 0:
                is_down = ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000
                if not is_down:
                    self.input_hook_thread.is_currently_pressed = False

    def update_rate_limiter_ui(self):
        if hasattr(self, 'home_page') and self.home_page:
            self.home_page.update_rate_limiter_status()

    def showEvent(self, event):
        super().showEvent(event)
        # Register hotkey when the window is shown
        if not getattr(self, 'hotkey_registered', False):
            self.register_global_hotkey()
        if not getattr(self, 'input_hook_thread', None):
            self.start_input_hook_thread()
        self.update_telemetry_state()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.update_telemetry_state()

    def nativeEvent(self, eventType, message):
        """
        Overrides QWidget.nativeEvent to filter Win32 events.
        """
        return super().nativeEvent(eventType, message)

    def closeEvent(self, event):
        """
        Saves settings and shuts down active loops before window closing.
        """
        self.is_closing = True
        logger.info("MainWindow close event received. Cleaning up.")
        
        # Stop macro playback and OSD HUD
        try:
            MacroManager().stop_replay()
            if hasattr(self, "macro_overlay") and self.macro_overlay:
                if hasattr(self.macro_overlay, "cleanup_widget"):
                    try:
                        self.macro_overlay.cleanup_widget()
                    except Exception:
                        pass
                self.macro_overlay.hide()
                self.macro_overlay.close()
            # Explicitly cleanup macro timeline and pages
            for page_attr in ['macro_page', 'gpu_oc_page']:
                if hasattr(self, page_attr) and getattr(self, page_attr):
                    page = getattr(self, page_attr)
                    if hasattr(page, 'timeline') and page.timeline and hasattr(page.timeline, 'cleanup_widget'):
                        try:
                            page.timeline.cleanup_widget()
                        except Exception:
                            pass
                    if hasattr(page, 'cleanup_widget'):
                        try:
                            page.cleanup_widget()
                        except Exception:
                            pass
        except Exception as e:
            logger.error(f"Error cleaning up macro structures: {e}")
            
        # Stop global input hook thread and clear rate limit QoS policy
        if getattr(self, 'input_hook_thread', None):
            try:
                self.input_hook_thread.stop()
                self.input_hook_thread.wait(300)
            except Exception as e:
                logger.error(f"Error stopping input hook thread: {e}")
            self.input_hook_thread = None
        
        try:
            from core_commander.core.tweaks.throttler import NetworkThrottlerService
            NetworkThrottlerService.force_delete_rules()
            # Wait for background queue to finish rule cleanup
            NetworkThrottlerService.join_worker()
        except Exception as e:
            logger.error(f"Error removing QoS rate limit on shutdown: {e}")
        
        # Stop all timers immediately
        self.watchdog.stop()
        self._stop_win_event_hook()
        self.mem_timer.stop()
        self.status_timer.stop()
        if hasattr(self, 'rate_limiter_watchdog_timer'):
            self.rate_limiter_watchdog_timer.stop()
        if hasattr(self, 'save_settings_timer'):
            self.save_settings_timer.stop()
        if hasattr(self, 'silent_retry_timer'):
            self.silent_retry_timer.stop()
        if hasattr(self, '_pulse_thread_timer') and self._pulse_thread_timer:
            try:
                self._pulse_thread_timer.cancel()
            except Exception:
                pass
            self._pulse_thread_timer = None

        # Stop and wait for OCR hotkey thread
        if getattr(self, 'ocr_hotkey_thread', None):
            try:
                self.ocr_hotkey_thread.stop()
                self.ocr_hotkey_thread.wait(300)
            except Exception as e:
                logger.error(f"Error stopping OCR hotkey thread: {e}")
            self.ocr_hotkey_thread = None

        # Stop and wait for any active VoiceChangerEngine instances
        try:
            from core_commander.core.voice_changer.engine import VoiceChangerEngine
            if hasattr(VoiceChangerEngine, '_active_instances'):
                active_engines = list(VoiceChangerEngine._active_instances)
                for engine in active_engines:
                    try:
                        engine.stop()
                        engine.wait(500)
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"Error stopping voice changer engines on close: {e}")

        # Automatically close RTSS when CoreCommander exits
        try:
            import psutil
            for proc in psutil.process_iter(['name']):
                try:
                    if proc.info['name'] and proc.info['name'].lower() in ['rtss.exe', 'rtsshooksloader64.exe']:
                        proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            logger.info("RTSS shut down successfully on application close.")
        except Exception as e:
            logger.error(f"Failed to shut down RTSS: {e}")



        # Unregister global hotkey
        self.unregister_global_hotkey()

        # Stop and wait for all active background threads
        def safe_cleanup_thread(thread_attr, stop_method=None):
            if hasattr(self, thread_attr) and getattr(self, thread_attr):
                t = getattr(self, thread_attr)
                try:
                    if t.isRunning():
                        try:
                            t.disconnect()
                        except Exception:
                            pass
                        if stop_method and hasattr(t, stop_method):
                            getattr(t, stop_method)()
                        t.wait(3000)
                except Exception:
                    pass
                setattr(self, thread_attr, None)

        safe_cleanup_thread('auto_watchdog_thread', 'stop')
        safe_cleanup_thread('fps_collector', 'stop')
        safe_cleanup_thread('state_scanner', 'stop')
        safe_cleanup_thread('mem_worker', 'stop')
        safe_cleanup_thread('tweak_thread', 'stop')
        safe_cleanup_thread('silent_tweak_thread', 'stop')
        safe_cleanup_thread('worker', 'stop')

        if hasattr(self, 'overlay') and self.overlay:
            try:
                self.overlay.close()
            except Exception:
                pass
            self.overlay = None

        # Persist configurations
        self.save_settings()
        
        # Restore GPU overclock / tuning parameters to system default on exit
        try:
            logger.info("Restoring GPU parameters to defaults on exit...")
            GpuOverclockService.restore_defaults()
        except Exception as e:
            logger.error(f"Error restoring GPU parameters on shutdown: {str(e)}")
        
        # Revert CPU hardware settings, network settings, QoS policy, and power plan during shutdown
        # ONLY if active game optimization was running.
        if getattr(self, 'is_optimized', False):
            try:
                self._revert_system_settings_and_timers()
            except Exception as e:
                logger.error(f"Error during shutdown system tweaks restoration: {str(e)}")
            
        event.accept()
