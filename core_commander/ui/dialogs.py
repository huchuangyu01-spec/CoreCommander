# -*- coding: utf-8 -*-
import re
import psutil
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QTextEdit, QWidget, QLabel
from qfluentwidgets import (
    MessageBox, LineEdit, ListWidget, 
    TransparentToolButton, FluentIcon, BodyLabel, CaptionLabel, ComboBox,
    ProgressBar, PushButton, CheckBox, IconWidget, isDarkTheme
)
from core_commander.utils.i18n import Trans
from core_commander.utils.logger import logger


class ProcessLoaderThread(QThread):
    """
    Asynchronously loads running processes to avoid locking the UI thread.
    """
    processes_loaded = Signal(list)

    def run(self):
        procs = []
        # Querying running processes might be slow on busy machines
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                name = proc.info['name']
                pid = proc.info['pid']
                if name and pid > 0:
                    procs.append((pid, name))
            except Exception:  # nosec
                continue
        # Sort alphabetically by process name
        procs.sort(key=lambda x: x[1].lower())
        self.processes_loaded.emit(procs)


class ProcessSelectorDialog(MessageBox):
    """
    A premium Fluent dialog that allows users to search and select running processes.
    """
    def __init__(self, parent=None):
        super().__init__("选择目标进程映像", "", parent)
        self.setClosableOnMaskClicked(True)
        self.selected_pid = None
        self.selected_name = None
        self.process_data = []
        
        # Format the content layout
        self.widget.setMinimumWidth(440)
        self.textLayout.setContentsMargins(24, 10, 24, 15)
        
        # Status Label
        self.status_label = CaptionLabel("正在检索系统进程树及状态...", self)
        self.textLayout.addWidget(self.status_label)
        
        # Search panel
        search_layout = QHBoxLayout()
        self.search_edit = LineEdit()
        self.search_edit.setPlaceholderText("输入进程映像名称进行过滤...")
        self.search_edit.textChanged.connect(self.on_search_changed)
        self.search_edit.setEnabled(False)
        
        self.btn_refresh = TransparentToolButton(FluentIcon.SYNC)
        self.btn_refresh.clicked.connect(self.load_processes)
        
        search_layout.addWidget(self.search_edit)
        search_layout.addWidget(self.btn_refresh)
        self.textLayout.addLayout(search_layout)
        
        # Process List widget
        self.list_widget = ListWidget()
        self.list_widget.setFixedHeight(400)
        self.list_widget.itemDoubleClicked.connect(self.accept_selection)
        self.textLayout.addWidget(self.list_widget)
        
        # Load process list
        self.load_processes()
        
        # Translate buttons
        self.yesButton.setText("确认选择")
        self.cancelButton.setText("取消")
        
        try:
            self.yesButton.clicked.disconnect()
        except Exception:  # nosec
            pass
        self.yesButton.clicked.connect(self.accept_selection)

    def load_processes(self):
        self.status_label.setText("正在检索系统进程树及状态...")
        self.btn_refresh.setEnabled(False)
        self.search_edit.setEnabled(False)
        self.list_widget.clear()
        
        # Launch async QThread
        self.loader_thread = ProcessLoaderThread(self)
        self.loader_thread.processes_loaded.connect(self.on_processes_loaded)
        self.loader_thread.finished.connect(self.loader_thread.deleteLater)
        self.loader_thread.start()

    def on_processes_loaded(self, processes: list):
        self.process_data = processes
        self.status_label.setText(f"已成功加载 {len(processes)} 个活跃进程映像")
        self.btn_refresh.setEnabled(True)
        self.search_edit.setEnabled(True)
        self.search_edit.setFocus()
        
        # Populate List
        self.refresh_list_view("")

    def refresh_list_view(self, filter_text: str):
        self.list_widget.clear()
        filter_lower = filter_text.lower()
        
        for pid, name in self.process_data:
            if not filter_lower or filter_lower in name.lower() or filter_lower in str(pid):
                self.list_widget.addItem(f"{name}  (PID: {pid})")

    def on_search_changed(self, text: str):
        self.refresh_list_view(text)

    def accept_selection(self):
        current_item = self.list_widget.currentItem()
        if current_item:
            text = current_item.text()
            match = re.search(r'\(PID: (\d+)\)', text)
            if match:
                self.selected_pid = int(match.group(1))
                self.selected_name = text.split('(PID:')[0].strip()
                logger.info(f"User selected process: {self.selected_name} (PID: {self.selected_pid})")
                self.accept()
            else:
                logger.warning(f"Could not parse PID from selected text: {text}")
        else:
            logger.info("No process selected in list.")


class ThemeLanguageDialog(MessageBox):
    """
    A premium Fluent dialog that allows users to configure application Theme Mode, Accent Color, and Language.
    """
    def __init__(self, parent=None, settings=None):
        super().__init__("个性化与语言设置", "", parent)
        self.setClosableOnMaskClicked(True)
        self.settings = settings
        self.selected_lang = None
        self.selected_theme = None
        self.selected_color = None
        
        self.widget.setMinimumWidth(440)
        self.textLayout.setContentsMargins(24, 15, 24, 15)
        self.textLayout.setSpacing(15)
        
        # Grid layout for options
        from PySide6.QtWidgets import QGridLayout
        grid = QGridLayout()
        grid.setSpacing(15)
        
        # 1. Language
        from core_commander.utils.i18n import Trans
        self.lbl_lang = BodyLabel("界面语言 / Language:")
        self.combo_lang = ComboBox()
        for lang_code, lang_name in Trans.LANGUAGES:
            self.combo_lang.addItem(lang_name, userData=lang_code)
            
        lang_idx = 0
        for i in range(self.combo_lang.count()):
            if self.combo_lang.itemData(i) == Trans.CURRENT_LANG:
                lang_idx = i
                break
        self.combo_lang.setCurrentIndex(lang_idx)
        
        # 2. Theme Mode
        self.lbl_theme = BodyLabel("主题模式 / Theme Mode:")
        self.combo_theme = ComboBox()
        themes = [
            ("auto", "跟随系统 / Follow System"),
            ("light", "浅色模式 / Light Mode"),
            ("dark", "深色模式 / Dark Mode")
        ]
        for theme_code, theme_name in themes:
            self.combo_theme.addItem(theme_name, userData=theme_code)
            
        theme_idx = 0
        for i in range(self.combo_theme.count()):
            if self.combo_theme.itemData(i) == self.settings.theme_mode:
                theme_idx = i
                break
        self.combo_theme.setCurrentIndex(theme_idx)
        
        # 3. Accent Color
        self.lbl_color = BodyLabel("主题配色 / Accent Color:")
        self.combo_color = ComboBox()
        colors = [
            ("#0078D4", "经典蓝 / Classic Blue"),
            ("#00F2FE", "霓虹青 / Neon Cyan"),
            ("#00B294", "极客绿 / Geek Green"),
            ("#7851A9", "紫罗兰 / Violet Purple"),
            ("#555555", "曜石灰 / Obsidian Gray")
        ]
        for color_code, color_name in colors:
            self.combo_color.addItem(color_name, userData=color_code)
            
        color_idx = 0
        for i in range(self.combo_color.count()):
            if self.combo_color.itemData(i) == self.settings.accent_color:
                color_idx = i
                break
        self.combo_color.setCurrentIndex(color_idx)
        
        grid.addWidget(self.lbl_lang, 0, 0)
        grid.addWidget(self.combo_lang, 0, 1)
        grid.addWidget(self.lbl_theme, 1, 0)
        grid.addWidget(self.combo_theme, 1, 1)
        grid.addWidget(self.lbl_color, 2, 0)
        grid.addWidget(self.combo_color, 2, 1)
        
        self.textLayout.addLayout(grid)
        
        # Customize buttons
        self.yesButton.setText("应用配置")
        self.cancelButton.setText("取消")
        
        try:
            self.yesButton.clicked.disconnect()
        except Exception:  # nosec
            pass
        self.yesButton.clicked.connect(self.accept_selection)

    def accept_selection(self):
        self.selected_lang = self.combo_lang.currentData()
        self.selected_theme = self.combo_theme.currentData()
        self.selected_color = self.combo_color.currentData()
        self.accept()


class UwpDebloatThread(QThread):
    """
    Asynchronously uninstalls selected UWP packages using PowerShell and reports success/failure honestly.
    """
    progress_signal = Signal(str, int)
    # finished_signal: (success, message, succeeded_keywords)
    finished_signal = Signal(bool, str, list)

    def __init__(self, selected_apps: list, parent=None):
        super().__init__(parent)
        self.selected_apps = selected_apps

    def run(self):
        import subprocess  # nosec
        total = len(self.selected_apps)
        if total == 0:
            self.finished_signal.emit(True, "未选择任何应用进行卸载。", [])
            return

        succeeded_keywords = []
        for idx, (name, keyword) in enumerate(self.selected_apps):
            percent = int((idx / total) * 100)
            self.progress_signal.emit(f"==> 正在准备卸载: {name}", percent)
            
            # PowerShell script to remove app and verify result honestly
            cmd = f"""
            $keyword = "{keyword}"
            $success = $true
            $msg = ""
            try {{
                $pkgs = Get-AppxPackage $keyword | Where-Object {{ $_.NonRemovable -ne $true }}
                if ($pkgs) {{
                    foreach ($pkg in $pkgs) {{
                        Remove-AppxPackage -Package $pkg.PackageFullName -ErrorAction Stop
                    }}
                }}
                
                try {{
                    $pkgs_all = Get-AppxPackage -AllUsers $keyword | Where-Object {{ $_.NonRemovable -ne $true }}
                    if ($pkgs_all) {{
                        foreach ($pkg in $pkgs_all) {{
                            Remove-AppxPackage -AllUsers -Package $pkg.PackageFullName -ErrorAction SilentlyContinue
                        }}
                    }}
                }} catch {{}}

                try {{
                    $provs = Get-AppxProvisionedPackage -Online | Where-Object {{$_.PackageName -like $keyword -or $_.DisplayName -like $keyword}}
                    if ($provs) {{
                        foreach ($prov in $provs) {{
                            Remove-AppxProvisionedPackage -Online -PackageName $prov.PackageName -ErrorAction SilentlyContinue
                        }}
                    }}
                }} catch {{}}

                # Double check: is it still installed for the CURRENT user?
                $still = Get-AppxPackage $keyword | Where-Object {{ $_.NonRemovable -ne $true }}
                if ($still) {{
                    $success = $false
                    $msg = "该应用是系统组件，受到 Windows 系统保护，禁止被卸载。"
                }}
            }} catch {{
                $success = $false
                $msg = $_.Exception.Message
            }}
            
            if ($success) {{
                Write-Output "SUCCESS"
            }} else {{
                Write-Output "FAILED: $msg"
            }}
            """
            
            self.progress_signal.emit(f"正在执行 PowerShell 深度卸载指令...", percent)
            try:
                p = subprocess.Popen(  # nosec
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                out, err = p.communicate(timeout=45)
                out_str = out.decode('gbk', errors='ignore').strip()
                err_str = err.decode('gbk', errors='ignore').strip()
                
                if "SUCCESS" in out_str:
                    self.progress_signal.emit(f"[OK] {name} 卸载成功。", percent + int(100 / total))
                    succeeded_keywords.append(keyword)
                else:
                    reason = out_str.replace("FAILED:", "").strip()
                    if not reason:
                        reason = err_str if err_str else "权限不足或系统策略拦截"
                    self.progress_signal.emit(f"[失败] {name} 无法卸载。原因: {reason}", percent)
            except Exception as ex:
                self.progress_signal.emit(f"[错误] 卸载 {name} 遇到异常: {str(ex)}", percent)

        self.progress_signal.emit("==> 预装 UWP 应用深度清理工作执行结束。", 100)
        self.finished_signal.emit(True, "清理完成。", succeeded_keywords)


class UwpScanThread(QThread):
    """
    Asynchronously scans installed UWP packages.
    """
    scan_finished = Signal(list)

    def run(self):
        import subprocess  # nosec
        cmd = 'Get-AppxPackage | Where-Object { $_.NonRemovable -ne $true } | Select-Object -ExpandProperty Name'
        try:
            p = subprocess.Popen(  # nosec
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            out, _ = p.communicate(timeout=15)
            names = [line.strip().lower() for line in out.decode('gbk', errors='ignore').splitlines() if line.strip()]
        except Exception:
            names = []
        self.scan_finished.emit(names)


class UwpAppItemWidget(QWidget):
    """
    A premium custom list item widget showing UWP app name, icon, selection checkbox, and status badge.
    """
    def __init__(self, name: str, keyword: str, is_installed: bool, parent=None):
        super().__init__(parent)
        self.keyword = keyword
        self.is_installed = is_installed
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(12)
        
        # Checkbox for selection
        self.checkbox = CheckBox(self)
        self.checkbox.setEnabled(is_installed)
        layout.addWidget(self.checkbox)
        
        # Icon (thin outline) mapping
        icon_map = {
            "*3dbuilder*": FluentIcon.DEVELOPER_TOOLS,
            "*bingweather*": FluentIcon.LEAF,
            "*feedback*": FluentIcon.FEEDBACK,
            "*windowsmaps*": FluentIcon.GLOBE,
            "*mixedreality*": FluentIcon.APPLICATION,
            "*people*": FluentIcon.PEOPLE,
            "*skype*": FluentIcon.CHAT,
            "*solitairecollection*": FluentIcon.GAME,
            "*stickynotes*": FluentIcon.EDIT,
            "*yourphone*": FluentIcon.PHONE,
            "*xbox*": FluentIcon.GAME,
            "*cortana*": FluentIcon.ROBOT if hasattr(FluentIcon, 'ROBOT') else FluentIcon.PEOPLE
        }
        fluent_icon = icon_map.get(keyword, FluentIcon.APPLICATION)
        self.icon_widget = IconWidget(fluent_icon, self)
        self.icon_widget.setFixedSize(16, 16)
        layout.addWidget(self.icon_widget)
        
        # Name label
        self.name_label = BodyLabel(name, self)
        if not is_installed:
            self.name_label.setStyleSheet("color: gray;")
        layout.addWidget(self.name_label, 1)
        
        # Status badge
        self.status_label = QLabel(self)
        self.update_status(is_installed)
        layout.addWidget(self.status_label)
        
    def update_status(self, is_installed: bool):
        self.is_installed = is_installed
        self.checkbox.setEnabled(is_installed)
        if is_installed:
            txt = "已安装" if Trans.CURRENT_LANG == "zh_CN" else "Installed"
            bg = "rgba(46, 204, 113, 0.15)"
            fg = "#2ECC71" if isDarkTheme() else "#27AE60"
            self.name_label.setStyleSheet("")
        else:
            txt = "未安装" if Trans.CURRENT_LANG == "zh_CN" else "Not Installed"
            bg = "rgba(128, 128, 128, 0.15)"
            fg = "#888888"
            self.name_label.setStyleSheet("color: gray;")
            
        self.status_label.setText(f" {txt} ")
        self.status_label.setStyleSheet(f"""
            QLabel {{
                background-color: {bg};
                color: {fg};
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 11px;
                font-weight: bold;
            }}
        """)


class UwpDebloatDialog(MessageBox):
    """
    A premium Fluent dialog for listing and uninstalling pre-installed UWP bloatware.
    """
    def __init__(self, parent=None):
        super().__init__("系统预装 UWP 臃肿软件深度清理", "", parent)
        self.setClosableOnMaskClicked(True)
        self.widget.setMinimumWidth(580)
        self.textLayout.setContentsMargins(24, 10, 24, 15)
        self.textLayout.setSpacing(10)
        
        # Subtitle explanation
        self.desc_label = CaptionLabel("正在扫描系统预装组件状态，请稍候...", self)
        self.desc_label.setWordWrap(True)
        self.textLayout.addWidget(self.desc_label)
        
        # Apps list
        self.list_widget = ListWidget()
        self.list_widget.setFixedHeight(240)
        self.textLayout.addWidget(self.list_widget)
        
        self.apps_info = [
            ("3D Builder (3D 建模与打印)", "*3dbuilder*"),
            ("Microsoft Weather (系统自带天气)", "*bingweather*"),
            ("Feedback Hub (系统反馈中心)", "*feedback*"),
            ("Maps (系统自带地图)", "*windowsmaps*"),
            ("Mixed Reality Portal (混合现实门户)", "*mixedreality*"),
            ("People (系统人脉组件)", "*people*"),
            ("Skype (系统即时通讯)", "*skype*"),
            ("Solitaire Collection (微软纸牌合集)", "*solitairecollection*"),
            ("Sticky Notes (系统便签条)", "*stickynotes*"),
            ("Your Phone (手机连接助手)", "*yourphone*"),
            ("Xbox App & Live Services (Xbox 游戏平台服务)", "*xbox*"),
            ("Cortana (微软小娜语音助手)", "*cortana*"),
            ("Dev Home (开发人员主页)", "*devhome*"),
            ("Power Automate (桌面自动化处理)", "*powerautomate*"),
            ("Windows Widgets (小部件与新闻资讯)", "*webexperience*")
        ]
        
        # Action buttons
        btn_layout = QHBoxLayout()
        self.btn_select_all = PushButton("全选")
        self.btn_select_all.setEnabled(False)
        self.btn_select_all.clicked.connect(self.select_all)
        self.btn_deselect_all = PushButton("全不选")
        self.btn_deselect_all.setEnabled(False)
        self.btn_deselect_all.clicked.connect(self.deselect_all)
        btn_layout.addWidget(self.btn_select_all)
        btn_layout.addWidget(self.btn_deselect_all)
        btn_layout.addStretch()
        self.textLayout.addLayout(btn_layout)
        
        # Progress Bar
        self.progress_bar = ProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.hide()
        self.textLayout.addWidget(self.progress_bar)
        
        # Console output
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setFixedHeight(120)
        self.console.setStyleSheet("background-color: #1e1e1e; color: #00ff00; font-family: Consolas, monospace; font-size: 11px; border-radius: 4px; padding: 5px;")
        self.console.hide()
        self.textLayout.addWidget(self.console)
        
        # Setup buttons
        self.yesButton.setText("确认并卸载")
        self.yesButton.setEnabled(False)
        self.cancelButton.setText("关闭")
        
        try:
            self.yesButton.clicked.disconnect()
        except Exception:  # nosec
            pass
        self.yesButton.clicked.connect(self.start_debloat)
        
        self.items_widgets = []
        
        # Start scanning UWP packages
        self.scan_thread = UwpScanThread(self)
        self.scan_thread.scan_finished.connect(self.on_scan_finished)
        self.scan_thread.start()
        
        self.thread = None

    def on_scan_finished(self, installed_packages: list):
        self.desc_label.setText("勾选需要从系统中彻底移除的已安装应用组件。未安装的组件已自动忽略：")
        self.list_widget.clear()
        self.items_widgets.clear()
        
        import fnmatch
        has_any_installed = False
        for name, keyword in self.apps_info:
            pattern = keyword.lower()
            is_installed = False
            for pkg in installed_packages:
                if fnmatch.fnmatch(pkg, pattern):
                    is_installed = True
                    break
                    
            if is_installed:
                has_any_installed = True
                
            from PySide6.QtWidgets import QListWidgetItem
            item = QListWidgetItem(self.list_widget)
            
            # Create premium item widget
            widget = UwpAppItemWidget(name, keyword, is_installed, self.list_widget)
            item.setSizeHint(widget.sizeHint())
            
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, widget)
            self.items_widgets.append(widget)
            
        self.btn_select_all.setEnabled(has_any_installed)
        self.btn_deselect_all.setEnabled(has_any_installed)
        self.yesButton.setEnabled(has_any_installed)

    def select_all(self):
        for widget in self.items_widgets:
            if widget.is_installed:
                widget.checkbox.setChecked(True)

    def deselect_all(self):
        for widget in self.items_widgets:
            if widget.is_installed:
                widget.checkbox.setChecked(False)

    def start_debloat(self):
        selected = []
        for widget in self.items_widgets:
            if widget.is_installed and widget.checkbox.isChecked():
                selected.append((widget.name_label.text(), widget.keyword))
                
        if not selected:
            from qfluentwidgets import InfoBar
            InfoBar.warning(
                title="提示",
                content="请先在列表中勾选需要卸载的已安装组件！",
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBar.Top,
                duration=2000,
                parent=self
            )
            return
            
        self.list_widget.setEnabled(False)
        self.btn_select_all.setEnabled(False)
        self.btn_deselect_all.setEnabled(False)
        self.yesButton.setEnabled(False)
        self.cancelButton.setEnabled(False)
        
        self.progress_bar.show()
        self.progress_bar.setValue(0)
        self.console.show()
        self.console.clear()
        
        self.console.append("[INFO] 开始异步深度清理任务...")
        
        self.thread = UwpDebloatThread(selected, self)
        self.thread.progress_signal.connect(self.on_progress)
        self.thread.finished_signal.connect(self.on_finished)
        self.thread.start()

    def on_progress(self, msg: str, val: int):
        self.progress_bar.setValue(val)
        self.console.append(msg)
        self.console.ensureCursorVisible()

    def on_finished(self, success: bool, msg: str, succeeded_keywords: list):
        self.progress_bar.setValue(100)
        self.cancelButton.setEnabled(True)
        self.cancelButton.setText("完成")
        if success:
            self.console.append(f"\n[INFO] 卸载流程全部执行结束。成功卸载了 {len(succeeded_keywords)} 个组件。")
            for widget in self.items_widgets:
                if widget.keyword in succeeded_keywords:
                    widget.update_status(False)
                # Uncheck all anyway
                widget.checkbox.setChecked(False)
        else:
            self.console.append(f"\n[ERROR] 清理任务异常终止: {msg}")
        self.console.ensureCursorVisible()
