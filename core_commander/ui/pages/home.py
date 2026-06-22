# -*- coding: utf-8 -*-
import psutil
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QBoxLayout
from qfluentwidgets import (
    ScrollArea, TitleLabel, SubtitleLabel, BodyLabel, CaptionLabel,
    SimpleCardWidget, ElevatedCardWidget, LineEdit, PushButton, 
    PrimaryPushButton, ComboBox, CheckBox, SpinBox, InfoBar, FluentIcon,
    MessageBox, FlowLayout, StrongBodyLabel, isDarkTheme, IconWidget
)
from core_commander.core.topology import TopologyEngine
from core_commander.ui.components import CoreButton
from core_commander.ui.dialogs import ProcessSelectorDialog
from core_commander.utils.logger import logger
from core_commander.utils.i18n import Trans
from core_commander.utils.process import get_process_path_by_pid

class ProcessStatusChecker(QThread):
    result_signal = Signal(str, object, bool)

    def __init__(self, target_name: str, cached_pid: int, is_dark: bool, parent=None):
        super().__init__(parent)
        self.target_name = target_name
        self.cached_pid = cached_pid
        self.is_dark = is_dark

    def run(self):
        found_pid = None
        if self.cached_pid:
            try:
                p = psutil.Process(self.cached_pid)
                if p.name() == self.target_name:
                    found_pid = self.cached_pid
            except Exception:  # nosec
                pass

        if not found_pid:
            try:
                for p in psutil.process_iter(['pid', 'name']):
                    try:
                        p_name = p.info.get('name')
                        p_pid = p.info.get('pid')
                        if p_name == self.target_name and p_pid is not None:
                            found_pid = p_pid
                            break
                    except Exception:   # nosec
                        pass
            except Exception:  # nosec
                pass
        self.result_signal.emit(self.target_name, found_pid, self.is_dark)


class HomePage(ScrollArea):
    """
    Main page for CPU logical core selection, target process, memory clean configuration,
    and triggering system optimizations.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.setObjectName("HomePage")
        # Scroll area styling
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setViewportMargins(0, 0, 0, 0)
        self.setStyleSheet("QScrollArea { background-color: transparent; border: none; }")
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter) # Center the page inside scroll viewport
        
        # Scroll layout wrapper
        self.view = QWidget()
        self.view.setObjectName("HomePageView")
        self.view.setStyleSheet("#HomePageView { background-color: transparent; }")
        self.view.setMaximumWidth(1100) # Comfortable maximum width prevents stretching on wide screens
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        
        self.vBoxLayout = QVBoxLayout(self.view)
        self.vBoxLayout.setContentsMargins(30, 30, 30, 30)
        self.vBoxLayout.setSpacing(20)
        
        # Title (Aligned Left for Dashboard Header)
        self.title_label = TitleLabel("Core Commander")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        # self.vBoxLayout.addWidget(self.title_label)
        
        # Create 2-Column Split Layout
        self.main_columns_widget = QWidget()
        self.main_columns_widget.setObjectName("MainColumnsWidget")
        self.main_columns_widget.setStyleSheet("#MainColumnsWidget { background-color: transparent; }")
        self.main_columns_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self.main_columns_widget)
        self.main_columns_layout.setContentsMargins(0, 0, 0, 0)
        self.main_columns_layout.setSpacing(20)
        
        # Left Panel (stretch 3) - Core Selection & Preferred Cores
        self.left_panel = QWidget()
        self.left_panel.setObjectName("LeftPanelWidget")
        self.left_panel.setStyleSheet("#LeftPanelWidget { background-color: transparent; }")
        self.left_layout = QVBoxLayout(self.left_panel)
        self.left_layout.setContentsMargins(0, 0, 0, 0)
        self.left_layout.setSpacing(20)
        
        self.core_card = self.create_core_selection_card()
        self.primary_card = self.create_primary_core_card()
        
        self.left_layout.addWidget(self.core_card)
        self.left_layout.addWidget(self.primary_card)
        self.left_layout.addStretch(1)
        
        # Right Panel (stretch 2) - Info, Process, Memory & Strategy
        self.right_panel = QWidget()
        self.right_panel.setObjectName("RightPanelWidget")
        self.right_panel.setStyleSheet("#RightPanelWidget { background-color: transparent; }")
        self.right_layout = QVBoxLayout(self.right_panel)
        self.right_layout.setContentsMargins(0, 0, 0, 0)
        self.right_layout.setSpacing(20)
        
        self.cpu_card = self.create_cpu_info_card()
        self.proc_card = self.create_process_card()
        self.memory_card = self.create_memory_card()
        self.submit_card = self.create_submit_card()
        
        self.right_layout.addWidget(self.cpu_card)
        self.right_layout.addWidget(self.proc_card)
        self.right_layout.addWidget(self.memory_card)
        self.right_layout.addWidget(self.submit_card)
        self.right_layout.addStretch(1)
        
        self.main_columns_layout.addWidget(self.left_panel, 3)
        self.main_columns_layout.addWidget(self.right_panel, 2)
        
        self.vBoxLayout.addWidget(self.main_columns_widget)
        
        # State Data
        self.all_core_buttons = []
        self.sections = []
        
        # Initialize
        self.load_topology()
        self.retranslate_ui()
        
        # Initialize CPU percent counters
        psutil.cpu_percent(interval=None, percpu=True)
        
        # CPU usage update timer (low frequency 1500ms to be ultra lightweight)
        self.cpu_timer = QTimer(self)
        self.cpu_timer.timeout.connect(self.update_cpu_usages)
        self.cpu_timer.start(1500)
        
        # Wave animation tick timer (30ms for smooth 33fps wave transitions)
        self.animation_timer = QTimer(self)
        self.animation_timer.timeout.connect(self.animate_core_buttons)
        self.animation_timer.start(30)

    def create_cpu_info_card(self):
        card = SimpleCardWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)
        
        self.cpu_info_title = SubtitleLabel("系统硬件资源拓扑概览")
        self.cpu_info_title.setWordWrap(True)
        self.cpu_info_val = BodyLabel("")
        self.cpu_info_val.setWordWrap(True)
        self.cpu_info_threads = CaptionLabel("")
        
        layout.addWidget(self.cpu_info_title)
        layout.addWidget(self.cpu_info_val)
        layout.addWidget(self.cpu_info_threads)
        return card

    def create_process_card(self):
        card = SimpleCardWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        self.proc_title = SubtitleLabel("关联目标进程")
        self.proc_title.setWordWrap(True)
        layout.addWidget(self.proc_title)
        
        h_layout = QHBoxLayout()
        self.proc_display = LineEdit()
        self.proc_display.setReadOnly(True)
        
        self.btn_select_proc = PushButton("", self)
        self.btn_select_proc.setFocusPolicy(Qt.NoFocus)
        self.btn_select_proc.setIcon(FluentIcon.SEARCH)
        self.btn_select_proc.clicked.connect(self.select_process)
        
        h_layout.addWidget(self.proc_display, 1)
        h_layout.addWidget(self.btn_select_proc)
        layout.addLayout(h_layout)

        # Rate limiter status indicator
        rl_status_layout = QHBoxLayout()
        rl_status_layout.setSpacing(6)
        self.rl_status_icon = IconWidget(FluentIcon.GLOBE, card)
        self.rl_status_icon.setFixedSize(16, 16)
        self.rl_status_label = CaptionLabel("网卡限速: 未启用", card)
        self.rl_status_label.setStyleSheet("color: gray;")
        
        rl_status_layout.addWidget(self.rl_status_icon)
        rl_status_layout.addWidget(self.rl_status_label)
        rl_status_layout.addStretch()
        layout.addLayout(rl_status_layout)

        return card

    def create_primary_core_card(self):
        card = SimpleCardWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        self.primary_title = SubtitleLabel("主线程处理器关联首选")
        self.primary_title.setWordWrap(True)
        layout.addWidget(self.primary_title)
        
        grid = QGridLayout()
        grid.setSpacing(10)
        
        self.lbl1 = BodyLabel("首选逻辑处理器 1:")
        self.combo_primary1 = ComboBox()
        
        self.lbl2 = BodyLabel("首选逻辑处理器 2:")
        self.combo_primary2 = ComboBox()
        
        grid.addWidget(self.lbl1, 0, 0)
        grid.addWidget(self.combo_primary1, 0, 1)
        grid.addWidget(self.lbl2, 1, 0)
        grid.addWidget(self.combo_primary2, 1, 1)
        
        layout.addLayout(grid)
        
        # Connect signals to update primary visuals and save settings dynamically
        self.combo_primary1.currentIndexChanged.connect(self.update_primary)
        self.combo_primary1.currentIndexChanged.connect(lambda idx: self.parent_window.save_settings() if self.parent_window else None)
        self.combo_primary2.currentIndexChanged.connect(self.update_primary)
        self.combo_primary2.currentIndexChanged.connect(lambda idx: self.parent_window.save_settings() if self.parent_window else None)
        
        return card

    def create_core_selection_card(self):
        card = SimpleCardWidget()
        self.core_layout = QVBoxLayout(card)
        self.core_layout.setContentsMargins(20, 20, 20, 20)
        self.core_layout.setSpacing(15)
        
        self.core_selection_title = SubtitleLabel("处理器关联亲和性掩码 (Processor Affinity Mask)")
        self.core_selection_title.setWordWrap(True)
        self.core_layout.addWidget(self.core_selection_title)
        return card

    def create_memory_card(self):
        card = ElevatedCardWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        self.memory_title = SubtitleLabel("系统物理内存管理 (Working Set & Standby List)")
        self.memory_title.setWordWrap(True)
        layout.addWidget(self.memory_title)
        
        # CheckBox
        self.chk_mem_auto = CheckBox("启用周期性物理内存整理")
        self.chk_mem_auto.stateChanged.connect(self.toggle_mem_timer)
        layout.addWidget(self.chk_mem_auto)
        
        # Row 2: Cleaning interval controls
        row2_layout = QHBoxLayout()
        self.lbl_interval = BodyLabel("周期整理间隔:")
        self.spin_mem_interval = SpinBox()
        self.spin_mem_interval.setRange(1, 1440)
        self.spin_mem_interval.setValue(30)
        self.spin_mem_interval.valueChanged.connect(self.toggle_mem_timer)
        
        row2_layout.addWidget(self.lbl_interval)
        row2_layout.addWidget(self.spin_mem_interval)
        row2_layout.addStretch()
        layout.addLayout(row2_layout)
        
        # Button: Manual clean button below
        self.btn_mem_now = PushButton("即时内存整理", self)
        self.btn_mem_now.setFocusPolicy(Qt.NoFocus)
        self.btn_mem_now.setIcon(FluentIcon.PLAY)
        self.btn_mem_now.clicked.connect(self.perform_manual_clean)
        layout.addWidget(self.btn_mem_now)
        
        return card

    def create_submit_card(self):
        card = ElevatedCardWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        
        self.submit_title = SubtitleLabel("策略调度中心")
        self.submit_title.setWordWrap(True)
        self.submit_desc = CaptionLabel("应用处理器关联亲和性、CPU优先级及所选的系统底层调优参数，点击应用部署以在系统内核中生效。")
        self.submit_desc.setWordWrap(True)
        
        self.apply_btn = PrimaryPushButton("应用系统与进程关联配置", self)
        self.apply_btn.setFocusPolicy(Qt.NoFocus)
        self.apply_btn.setFixedHeight(50)
        self.apply_btn.clicked.connect(self.apply_optimization)
        
        layout.addWidget(self.submit_title)
        layout.addWidget(self.submit_desc)
        layout.addWidget(self.apply_btn)
        return card

    def retranslate_ui(self):
        # Home title
        self.title_label.setText(Trans.get("home_header"))
        
        # CPU Info Card
        self.cpu_info_title.setText(Trans.get("cpu_topo_title"))
        cpu_name = TopologyEngine.get_cpu_info()
        self.cpu_info_val.setText(f"{Trans.get('cpu_info_label')}{cpu_name}")
        
        topology = self.parent_window.topology if self.parent_window else []
        p_count = len([c for c in topology if c['type'] == 'P-Core'])
        e_count = len([c for c in topology if c['type'] == 'E-Core'])
        total_threads = sum(len(c.get('threads', [])) for c in topology)
        if e_count > 0:
            self.cpu_info_threads.setText(Trans.get("cpu_threads_label").format(threads=total_threads, p_cores=p_count, e_cores=e_count))
        else:
            self.cpu_info_threads.setText(Trans.get("cpu_threads_label_p").format(threads=total_threads, p_cores=p_count))
            
        # Process Card
        self.proc_title.setText(Trans.get("attach_proc_title"))
        if not self.parent_window or not self.parent_window.target_name:
            self.proc_display.setPlaceholderText(Trans.get("attach_proc_placeholder"))
        self.btn_select_proc.setText(Trans.get("attach_proc_btn"))
        
        # Primary preferred cores
        self.primary_title.setText(Trans.get("pref_core_title"))
        self.lbl1.setText(Trans.get("pref_core_1"))
        self.lbl2.setText(Trans.get("pref_core_2"))
        
        # Update ComboBox index 0 text dynamically without resetting the whole list if initialized
        if self.combo_primary1.count() > 0:
            self.combo_primary1.setItemText(0, Trans.get("pref_core_none"))
        if self.combo_primary2.count() > 0:
            self.combo_primary2.setItemText(0, Trans.get("pref_core_none"))
            
        # Core selection card
        self.core_selection_title.setText(Trans.get("affinity_mask_title"))
        
        # Memory card
        self.memory_title.setText(Trans.get("mem_mgmt_title"))
        self.chk_mem_auto.setText(Trans.get("mem_auto_clean"))
        self.btn_mem_now.setText(Trans.get("mem_clean_btn"))
        self.lbl_interval.setText(Trans.get("mem_interval_label"))
        self.spin_mem_interval.setSuffix(Trans.get("mem_interval_suffix"))
        
        # Strategy card
        self.submit_title.setText(Trans.get("strategy_center_title"))
        self.submit_desc.setText(Trans.get("strategy_center_desc"))
        
        # Update apply button text
        if self.parent_window and getattr(self.parent_window, 'is_optimized', False):
            self.apply_btn.setText(Trans.get("strategy_cancel_btn"))
        else:
            pending_count = 0
            if self.parent_window and hasattr(self.parent_window, 'get_pending_keys'):
                pending_count = len(self.parent_window.get_pending_keys())
                
            if pending_count > 0:
                self.apply_btn.setText(f"{Trans.get('strategy_apply_btn')} ({pending_count})")
            else:
                self.apply_btn.setText(Trans.get("strategy_apply_btn"))

        if hasattr(self, 'p_core_section_label'):
            self.p_core_section_label.setText("性能核 (P-Cores)" if Trans.CURRENT_LANG == "zh_CN" else "P-Cores")
        if hasattr(self, 'e_core_section_label'):
            self.e_core_section_label.setText("能效核 (E-Cores)" if Trans.CURRENT_LANG == "zh_CN" else "E-Cores")
        if hasattr(self, 'chk_all_p'):
            self.chk_all_p.setText("全选" if Trans.CURRENT_LANG == "zh_CN" else "Select All")
        if hasattr(self, 'chk_all_e'):
            self.chk_all_e.setText("全选" if Trans.CURRENT_LANG == "zh_CN" else "Select All")

    def load_topology(self):
        topology = self.parent_window.topology if self.parent_window else []
        cpu_vendor = TopologyEngine.get_cpu_vendor()
        
        self.combo_primary1.blockSignals(True)
        self.combo_primary2.blockSignals(True)
        self.combo_primary1.clear()
        self.combo_primary2.clear()
        self.combo_primary1.addItem(Trans.get("pref_core_none"), userData=-1)
        self.combo_primary2.addItem(Trans.get("pref_core_none"), userData=-1)
        
        p_cores = [c for c in topology if c['type'] == 'P-Core']
        e_cores = [c for c in topology if c['type'] == 'E-Core']
        
        if p_cores:
            self.add_core_section("性能核 (P-Cores)", p_cores, False, cpu_vendor)
        
        if e_cores:
            self.add_core_section("能效核 (E-Cores)", e_cores, True, cpu_vendor)

        self.combo_primary1.blockSignals(False)
        self.combo_primary2.blockSignals(False)
        self.update_chk_all_states()

    def add_core_section(self, title: str, cores: list, is_e_core: bool, cpu_vendor: str):
        section_widget = QWidget()
        section_layout = QVBoxLayout(section_widget)
        section_layout.setContentsMargins(0, 10, 0, 10)
        
        # Section header with Select All box
        header_layout = QHBoxLayout()
        lbl = StrongBodyLabel(title)
        header_layout.addWidget(lbl)
        
        # Check AMD 3D V-Cache Dual-CCD CPU
        is_vcache_dual_ccd = (cpu_vendor == "AMD" and len(cores) > 8 and "3D" in TopologyEngine.get_cpu_info().upper())

        chk_all = CheckBox("全选")
        if is_vcache_dual_ccd:
            chk_all.setChecked(False)
        else:
            chk_all.setChecked(not is_e_core)
        header_layout.addWidget(chk_all)
        header_layout.addStretch()
        section_layout.addLayout(header_layout)
        
        if is_e_core:
            self.e_core_section_label = lbl
            self.chk_all_e = chk_all
        else:
            self.p_core_section_label = lbl
            self.chk_all_p = chk_all
        
        # Flow layout for buttons (automatic line wrapping)
        grid = FlowLayout()
        grid.setSpacing(10)
        
        buttons = []
        for i, c in enumerate(cores):
            btn = CoreButton(c)
            if is_vcache_dual_ccd:
                # Pre-check only CCD0 (first half of physical cores)
                btn.blockSignals(True)
                btn.setChecked(i < len(cores) // 2)
                btn.blockSignals(False)
            buttons.append(btn)
            self.all_core_buttons.append(btn)
            grid.addWidget(btn)
            btn.clicked.connect(self.on_core_config_changed)
            
            # Populate primary thread drop-down boxes
            phy_id = c['core_id']
            main_thread_id = sorted(c['threads'])[0]
            vendor_txt = "[Intel P-Core]" if cpu_vendor == "INTEL" else "[AMD Physical]"
            if c['type'] == 'P-Core':
                txt = f"{vendor_txt} 物理核 #{phy_id} (线程 {main_thread_id})"
                self.combo_primary1.addItem(txt, userData=main_thread_id)
                self.combo_primary2.addItem(txt, userData=main_thread_id)
        
        section_layout.addLayout(grid)
        
        # Checkbox binding
        def toggle_all():
            state = chk_all.isChecked()
            for b in buttons:
                b.blockSignals(True)
                b.setChecked(state)
                b.blockSignals(False)
            self.on_core_config_changed()
        
        chk_all.stateChanged.connect(toggle_all)
        
        self.core_layout.addWidget(section_widget)
        self.sections.append({'buttons': buttons, 'checkbox': chk_all})

    def update_chk_all_states(self):
        for section in self.sections:
            buttons = section['buttons']
            chk_all = section['checkbox']
            chk_all.blockSignals(True)
            chk_all.setChecked(all(b.isChecked() for b in buttons))
            chk_all.blockSignals(False)

    def on_core_config_changed(self):
        self.update_chk_all_states()
        if self.parent_window:
            self.parent_window.save_settings()

    def update_primary(self, index=None):
        id1 = self.combo_primary1.currentData()
        id2 = self.combo_primary2.currentData()
        
        for b in self.all_core_buttons:
            is_target = False
            if id1 != -1 and id1 in b.threads: 
                is_target = True
            if id2 != -1 and id2 in b.threads: 
                is_target = True
                
            if is_target:
                b.blockSignals(True)
                b.setChecked(True)
                b.blockSignals(False)
            b.set_primary_visual(is_target)
            
        self.update_chk_all_states()

    def select_process(self):
        dlg = ProcessSelectorDialog(self)
        if dlg.exec():
            self.parent_window.target_pid = dlg.selected_pid
            self.parent_window.target_name = dlg.selected_name
            self.update_proc_display()
            if self.parent_window:
                try:
                    path = get_process_path_by_pid(dlg.selected_pid)
                    if path:
                        self.parent_window.settings.target_process_path = path
                    else:
                        logger.warning(f"Could not resolve path for selected PID {dlg.selected_pid}")
                except Exception as ex:
                    logger.warning(f"Failed to get target process path: {str(ex)}")
                self.parent_window.save_settings()

    def update_rate_limiter_status(self):
        win = self.parent_window
        if not win or not hasattr(win, 'settings') or not hasattr(self, 'rl_status_label'):
            return
            
        if not win.settings.enable_rate_limiter:
            self.rl_status_label.setText(Trans.get("rate_limiter_status_inactive"))
            self.rl_status_label.setStyleSheet("color: gray; font-weight: normal;")
            self.rl_status_icon.setStyleSheet("color: gray;")
        else:
            state = getattr(win, 'rate_limiter_state', 'waiting') # 'waiting' or 'active'
            if state == 'active':
                val = win.settings.rate_limiter_value
                unit = win.settings.rate_limiter_unit
                self.rl_status_label.setText(f"{Trans.get('rate_limiter_status_active')} ({val} {unit})")
                color = "#4ADE80" if isDarkTheme() else "#16A34A"
                self.rl_status_label.setStyleSheet(f"color: {color}; font-weight: bold;")
                self.rl_status_icon.setStyleSheet(f"color: {color};")
            else: # waiting
                key = win.settings.rate_limiter_hotkey
                self.rl_status_label.setText(f"{Trans.get('rate_limiter_status_waiting')} ({Trans.get('rate_limiter_btn_bind')[:-2]}: {key})")
                color = "#60A5FA" if isDarkTheme() else "#2563EB"
                self.rl_status_label.setStyleSheet(f"color: {color}; font-weight: bold;")
                self.rl_status_icon.setStyleSheet(f"color: {color};")

    def update_proc_display(self):
        self.update_rate_limiter_status()
        target_name = self.parent_window.target_name
        if not target_name:
            self.proc_display.setText("")
            self.proc_display.setStyleSheet("")
            return

        if hasattr(self, '_status_checker') and self._status_checker.isRunning():
            return

        cached_pid = self.parent_window.target_pid
        is_dark = isDarkTheme()

        self._status_checker = ProcessStatusChecker(target_name, cached_pid, is_dark, self)
        self._status_checker.result_signal.connect(self.on_process_status_checked)
        self._status_checker.start()

    def on_process_status_checked(self, target_name, found_pid, is_dark):
        if not self.parent_window:
            return
        if target_name != self.parent_window.target_name:
            return

        if found_pid:
            self.parent_window.target_pid = found_pid
            self.proc_display.setText(f"{target_name} (PID: {found_pid})")
            color = "#4ADE80" if is_dark else "#16A34A"
            self.proc_display.setStyleSheet(f"color: {color}; font-weight: bold;")
            try:
                path = get_process_path_by_pid(found_pid)
                if path:
                    self.parent_window.settings.target_process_path = path
            except Exception as e:
                logger.warning(f"Failed to refresh process path for PID {found_pid}: {str(e)}")
        else:
            self.parent_window.target_pid = None
            self.proc_display.setText(f"{target_name} (进程当前未运行)")
            color = "#F87171" if is_dark else "#DC2626"
            self.proc_display.setStyleSheet(f"color: {color}; font-weight: bold;")

        if hasattr(self.parent_window, 'update_fps_collector_lifecycle'):
            self.parent_window.update_fps_collector_lifecycle()

    def toggle_mem_timer(self):
        if self.parent_window:
            self.parent_window.save_settings()
        if self.chk_mem_auto.isChecked():
            interval_min = self.spin_mem_interval.value()
            interval_ms = interval_min * 60 * 1000
            self.parent_window.mem_timer.start(interval_ms)
            InfoBar.success("已开启", f"自动物理内存整理已启用，清理间隔: {interval_min} 分钟", parent=self)
        else:
            self.parent_window.mem_timer.stop()
            InfoBar.info("已关闭", "自动物理内存整理已关闭", parent=self)

    def perform_manual_clean(self):
        self.btn_mem_now.setEnabled(False)
        self.btn_mem_now.setText("内存整理中...")
        self.parent_window.perform_memory_clean(silent=False, callback=self.on_mem_clean_done)

    def on_mem_clean_done(self):
        self.btn_mem_now.setEnabled(True)
        self.btn_mem_now.setText("即时内存整理")

    def apply_optimization(self):
        self.parent_window.apply_optimization(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        content_width = self.width() - 60
        if content_width < 750:
            self.main_columns_layout.setDirection(QBoxLayout.Direction.TopToBottom)
            self.main_columns_layout.setStretch(0, 0)
            self.main_columns_layout.setStretch(1, 0)
        else:
            self.main_columns_layout.setDirection(QBoxLayout.Direction.LeftToRight)
            self.main_columns_layout.setStretch(0, 3)
            self.main_columns_layout.setStretch(1, 2)
            
    def update_cpu_usages(self):
        # Safeguard: If window is minimized, skip CPU query completely to save resources
        if self.window() and self.window().isMinimized():
            return
            
        try:
            cpu_percents = psutil.cpu_percent(interval=None, percpu=True)
            if not cpu_percents:
                return
            for btn in self.all_core_buttons:
                if not hasattr(btn, 'threads'):
                    continue
                # Find all percentages for the threads associated with this core button
                valid_percents = [cpu_percents[t] for t in btn.threads if t < len(cpu_percents)]
                if valid_percents:
                    btn.usage = sum(valid_percents) / len(valid_percents)
                else:
                    btn.usage = 0.0
        except Exception as e:
            logger.warning(f"Failed to query CPU percents: {str(e)}")
            
    def animate_core_buttons(self):
        # Safeguard: If window is minimized, skip animation completely to achieve 0% active rendering overhead
        if self.window() and self.window().isMinimized():
            return
            
        for btn in self.all_core_buttons:
            if hasattr(btn, 'tick_wave'):
                btn.tick_wave()
                
    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, 'cpu_timer') and not self.cpu_timer.isActive():
            self.cpu_timer.start(1500)
        if hasattr(self, 'animation_timer') and not self.animation_timer.isActive():
            self.animation_timer.start(30)
            
    def hideEvent(self, event):
        super().hideEvent(event)
        if hasattr(self, 'cpu_timer') and self.cpu_timer.isActive():
            self.cpu_timer.stop()
        if hasattr(self, 'animation_timer') and self.animation_timer.isActive():
            self.animation_timer.stop()
