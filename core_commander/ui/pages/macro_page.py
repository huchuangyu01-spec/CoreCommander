# -*- coding: utf-8 -*-
import os
from PySide6.QtCore import Qt, QSize, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QListWidgetItem, QHeaderView, QFileDialog, QLabel, QGridLayout, QFormLayout
)
from qfluentwidgets import (
    TitleLabel, SubtitleLabel, BodyLabel, PushButton, PrimaryPushButton,
    CardWidget, ComboBox, SwitchButton, InfoBar, CompactSpinBox,
    ListWidget, FluentIcon, Slider, TableWidget, MessageBoxBase, LineEdit
)

from core_commander.config.settings import AppSettings
from core_commander.core.macro_manager import MacroManager, MacroAction, MacroProfile
from core_commander.ui.timeline_widget import TimelineWidget, UIBlockItem
from core_commander.core.input_hook import VK_NAMES
from core_commander.utils.logger import logger
from core_commander.utils.i18n import Trans

class FluentInputDialog(MessageBoxBase):
    def __init__(self, title, placeholder_text, default_text="", parent=None):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel(title, self)
        self.lineEdit = LineEdit(self)
        self.lineEdit.setPlaceholderText(placeholder_text)
        self.lineEdit.setText(default_text)
        self.lineEdit.setClearButtonEnabled(True)

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.lineEdit)
        
        self.widget.setMinimumWidth(300)
        self.lineEdit.setFocus()

class MacroPage(QWidget):
    """
    GUI page for configuring, recording, editing, and sharing macro actions.
    Uses the advanced multi-track timeline ruler editor and a side properties panel.
    """
    def __init__(self, main_win):
        super().__init__()
        self.setObjectName("MacroPage")
        self.main_win = main_win
        self.settings = main_win.settings
        self.macro_manager = MacroManager()
        
        # State
        self.is_binding_hotkey = False
        self.is_binding_block_key = False
        
        # Layouts
        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(30, 20, 30, 20)
        self.main_layout.setSpacing(20)
        
        # 1. Left Panel: Profile Management
        self.left_panel = QWidget(self)
        self.left_layout = QVBoxLayout(self.left_panel)
        self.left_layout.setContentsMargins(0, 0, 0, 0)
        self.left_layout.setSpacing(15)
        self.left_panel.setFixedWidth(310)
        self.init_left_panel()
        
        # 2. Right Panel: Timeline & Properties
        self.right_panel = QWidget(self)
        self.right_layout = QVBoxLayout(self.right_panel)
        self.right_layout.setContentsMargins(0, 0, 0, 0)
        self.right_layout.setSpacing(15)
        self.init_right_panel()
        
        self.main_layout.addWidget(self.left_panel)
        self.main_layout.addWidget(self.right_panel)
        
        # Manager Event Connects
        self.macro_manager.profiles_updated.connect(self.populate_profiles_list)
        self.macro_manager.state_changed.connect(self.on_manager_state_changed)
        self.macro_manager.playback_progress.connect(self.on_playback_progress_updated)
        
        # Initialize
        self.populate_category_combo()
        self.populate_profiles_list()
        self.load_selected_profile_details()
        self.setup_timeline_sync()

    @property
    def input_hook(self):
        return self.main_win.input_hook_thread

    def init_left_panel(self):
        title = TitleLabel(Trans.get("nav_macro"), self.left_panel)
        self.left_layout.addWidget(title)
        
        # Profile list card
        self.card_profiles = CardWidget(self.left_panel)
        self.card_profiles_layout = QVBoxLayout(self.card_profiles)
        self.card_profiles_layout.setContentsMargins(15, 15, 15, 15)
        self.card_profiles_layout.setSpacing(10)
        
        lbl_p_title = SubtitleLabel("宏配置文件", self.card_profiles)
        self.card_profiles_layout.addWidget(lbl_p_title)
        
        # Category Selector Layout
        cat_title = BodyLabel("配置分组类别 (Category)", self.card_profiles)
        cat_title.setStyleSheet("font-weight: bold; color: #1a1a1a;")
        self.card_profiles_layout.addWidget(cat_title)
        
        self.cmb_category = ComboBox(self.card_profiles)
        self.cmb_category.currentIndexChanged.connect(self.on_category_changed)
        self.card_profiles_layout.addWidget(self.cmb_category)
        
        cat_btn_row = QHBoxLayout()
        self.btn_new_cat = PushButton("新建类别", self.card_profiles, FluentIcon.ADD)
        self.btn_new_cat.clicked.connect(self.on_new_category)
        self.btn_del_cat = PushButton("删除类别", self.card_profiles, FluentIcon.DELETE)
        self.btn_del_cat.clicked.connect(self.on_delete_category)
        
        cat_btn_row.addWidget(self.btn_new_cat, 1)
        cat_btn_row.addWidget(self.btn_del_cat, 1)
        self.card_profiles_layout.addLayout(cat_btn_row)
        
        self.list_profiles = ListWidget(self.card_profiles)
        self.list_profiles.setFixedHeight(160)
        self.list_profiles.currentItemChanged.connect(self.on_profile_selected_changed)
        self.list_profiles.itemDoubleClicked.connect(self.on_profile_double_clicked)
        self.card_profiles_layout.addWidget(self.list_profiles)
        
        # Buttons row 1
        btn_layout_1 = QHBoxLayout()
        self.btn_new = PushButton("新建", self.card_profiles, FluentIcon.ADD)
        self.btn_new.clicked.connect(self.on_new_profile)
        self.btn_clone = PushButton("克隆", self.card_profiles, FluentIcon.COPY)
        self.btn_clone.clicked.connect(self.on_clone_profile)
        self.btn_delete = PushButton("删除", self.card_profiles, FluentIcon.DELETE)
        self.btn_delete.clicked.connect(self.on_delete_profile)
        btn_layout_1.addWidget(self.btn_new)
        btn_layout_1.addWidget(self.btn_clone)
        btn_layout_1.addWidget(self.btn_delete)
        self.card_profiles_layout.addLayout(btn_layout_1)
        
        # Buttons row for hotkey
        btn_layout_key = QHBoxLayout()
        self.btn_bind = PushButton("一键改键", self.card_profiles, FluentIcon.EDIT)
        self.btn_bind.clicked.connect(self.on_start_hotkey_binding)
        self.btn_unbind = PushButton("清除热键", self.card_profiles, FluentIcon.DELETE)
        self.btn_unbind.clicked.connect(self.on_clear_hotkey)
        btn_layout_key.addWidget(self.btn_bind)
        btn_layout_key.addWidget(self.btn_unbind)
        self.card_profiles_layout.addLayout(btn_layout_key)
        
        # Playback Mode Row
        mode_layout = QHBoxLayout()
        lbl_mode = BodyLabel("触发模式:", self.card_profiles)
        self.cmb_play_mode = ComboBox(self.card_profiles)
        self.cmb_play_mode.addItems(["执行一次", "按住时循环", "按下循环/停止"])
        self.cmb_play_mode.currentIndexChanged.connect(self.on_play_mode_changed)
        mode_layout.addWidget(lbl_mode)
        mode_layout.addWidget(self.cmb_play_mode)
        self.card_profiles_layout.addLayout(mode_layout)
        
        # Buttons row 2 (Import/Export)
        btn_layout_2 = QHBoxLayout()
        self.btn_import = PushButton("导入配置", self.card_profiles, FluentIcon.DOWNLOAD)
        self.btn_import.setToolTip("导入 .ccmacro 配置文件")
        self.btn_import.clicked.connect(self.on_import_profile)
        self.btn_export = PushButton("导出配置", self.card_profiles, FluentIcon.SHARE)
        self.btn_export.setToolTip("导出 .ccmacro 配置文件")
        self.btn_export.clicked.connect(self.on_export_profile)
        btn_layout_2.addWidget(self.btn_import)
        btn_layout_2.addWidget(self.btn_export)
        self.card_profiles_layout.addLayout(btn_layout_2)
        
        self.left_layout.addWidget(self.card_profiles)
        
        # HUD control card
        self.card_hud = CardWidget(self.left_panel)
        self.card_hud_layout = QVBoxLayout(self.card_hud)
        self.card_hud_layout.setContentsMargins(15, 15, 15, 15)
        self.card_hud_layout.setSpacing(12)
        
        lbl_hud_title = SubtitleLabel("HUD 悬浮层设置", self.card_hud)
        self.card_hud_layout.addWidget(lbl_hud_title)
        
        row_hud_enable = QHBoxLayout()
        lbl_hud_enable = BodyLabel("启用 HUD 状态层", self.card_hud)
        self.switch_hud = SwitchButton(self.card_hud)
        self.switch_hud.setChecked(self.settings.get_bool("enable_macro_hud", True))
        self.switch_hud.checkedChanged.connect(self.on_hud_enable_changed)
        row_hud_enable.addWidget(lbl_hud_enable)
        row_hud_enable.addStretch()
        row_hud_enable.addWidget(self.switch_hud)
        self.card_hud_layout.addLayout(row_hud_enable)
        
        row_hud_lock = QHBoxLayout()
        lbl_hud_lock = BodyLabel("锁定 HUD 坐标位置", self.card_hud)
        self.switch_hud_lock = SwitchButton(self.card_hud)
        self.switch_hud_lock.setChecked(self.settings.get_bool("macro_hud_locked", False))
        self.switch_hud_lock.checkedChanged.connect(self.on_hud_lock_changed)
        row_hud_lock.addWidget(lbl_hud_lock)
        row_hud_lock.addStretch()
        row_hud_lock.addWidget(self.switch_hud_lock)
        self.card_hud_layout.addLayout(row_hud_lock)
        
        self.left_layout.addWidget(self.card_hud)
        self.left_layout.addStretch()

    def init_right_panel(self):
        # 1. Controller Row Card
        self.card_ctrl = CardWidget(self.right_panel)
        self.card_ctrl.setObjectName("CardCtrl")
        self.card_ctrl_layout = QHBoxLayout(self.card_ctrl)
        self.card_ctrl_layout.setContentsMargins(20, 20, 20, 20)
        self.card_ctrl_layout.setSpacing(15)
        
        self.btn_record = PrimaryPushButton("开始录制 (F10)", self.card_ctrl, FluentIcon.VIDEO)
        self.btn_record.clicked.connect(self.toggle_recording)
        
        self.btn_replay = PushButton("回放测试", self.card_ctrl, FluentIcon.PLAY)
        self.btn_replay.clicked.connect(self.toggle_playback)
        
        self.lbl_recording_tip = BodyLabel(
            "说明：在需要开始/结束录制时在任意窗口按下 <b>F10</b> 即可，控制键不会被录入到宏动作中。",
            self.card_ctrl
        )
        self.lbl_recording_tip.setWordWrap(True)
        self.lbl_recording_tip.setStyleSheet("color: #666666; font-size: 13px;")
        
        self.card_ctrl_layout.addWidget(self.btn_record, 0)
        self.card_ctrl_layout.addWidget(self.btn_replay, 0)
        self.card_ctrl_layout.addWidget(self.lbl_recording_tip, 1)
        
        # Light white theme stylesheet for card_ctrl
        self.card_ctrl.setStyleSheet("""
            #CardCtrl {
                background-color: #ffffff;
                border: 1px solid #e0e0e0;
                border-radius: 8px;
            }
        """)
        
        self.right_layout.addWidget(self.card_ctrl)
        
        # 2. Timeline Editor Card
        self.card_timeline = CardWidget(self.right_panel)
        self.card_timeline_layout = QVBoxLayout(self.card_timeline)
        self.card_timeline_layout.setContentsMargins(20, 15, 20, 15)
        self.card_timeline_layout.setSpacing(10)
        
        self.lbl_t_title = SubtitleLabel("时间轴画布 (Timeline Tracks)", self.card_timeline)
        self.card_timeline_layout.addWidget(self.lbl_t_title)
        
        # Horizontal Timeline Canvas Widget
        self.timeline = TimelineWidget(self.card_timeline)
        self.timeline.selectionChanged.connect(self.on_timeline_selection_changed)
        self.timeline.multiSelectionChanged.connect(self.on_timeline_multi_selection_changed)
        self.timeline.blocksChanged.connect(self.on_timeline_blocks_modified)
        self.timeline.blockDoubleClicked.connect(self.on_prop_bind_key)
        self.card_timeline_layout.addWidget(self.timeline)
        
        # Canvas Toolbar row
        canvas_tool_layout = QHBoxLayout()
        self.btn_arrange = PushButton("一键排列整理", self.card_timeline, FluentIcon.TILES)
        self.btn_arrange.clicked.connect(self.on_arrange_blocks)
        
        self.btn_clear_blocks = PushButton("清除全部", self.card_timeline, FluentIcon.DELETE)
        self.btn_clear_blocks.clicked.connect(self.on_clear_all_blocks)
        
        # Zoom scale slider
        lbl_zoom = BodyLabel("时间轴缩放:", self.card_timeline)
        self.slider_zoom = Slider(Qt.Orientation.Horizontal, self.card_timeline)
        self.slider_zoom.setRange(1, 40)
        self.slider_zoom.setValue(10) # 10ms/px
        self.slider_zoom.setFixedWidth(100)
        self.slider_zoom.valueChanged.connect(self.on_zoom_changed)
        
        canvas_tool_layout.addWidget(self.btn_arrange)
        canvas_tool_layout.addWidget(self.btn_clear_blocks)
        canvas_tool_layout.addStretch()
        canvas_tool_layout.addWidget(lbl_zoom)
        canvas_tool_layout.addWidget(self.slider_zoom)
        self.card_timeline_layout.addLayout(canvas_tool_layout)
        
        self.right_layout.addWidget(self.card_timeline)
        
        # 3. Properties Panel Card
        self.card_props = CardWidget(self.right_panel)
        self.card_props.setObjectName("CardProps")
        self.card_props.setMinimumHeight(200)
        self.card_props_layout = QVBoxLayout(self.card_props)
        self.card_props_layout.setContentsMargins(24, 20, 24, 20)
        self.card_props_layout.setSpacing(12)
        
        self.lbl_props_title = SubtitleLabel("动作属性编辑器 (Properties Editor)", self.card_props)
        self.lbl_props_title.setStyleSheet("color: #000000; font-weight: bold; font-size: 16px;")
        self.card_props_layout.addWidget(self.lbl_props_title)
        
        # Classic white background styling for properties card
        self.card_props.setStyleSheet("""
            #CardProps {
                background-color: #ffffff;
                border: 1px solid #e0e0e0;
                border-radius: 8px;
            }
        """)
        
        # Placeholder when no block selected (Classic gray text)
        self.lbl_props_placeholder = BodyLabel(
            "没有选中的动作。请在上方时间轴中点击选中一个色块进行属性编辑。",
            self.card_props
        )
        self.lbl_props_placeholder.setStyleSheet("color: #666666;")
        self.lbl_props_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.card_props_layout.addWidget(self.lbl_props_placeholder)
        
        # Real Editor Area (hidden by default)
        self.widget_props_editor = QWidget(self.card_props)
        self.props_editor_layout = QHBoxLayout(self.widget_props_editor)
        self.props_editor_layout.setContentsMargins(0, 0, 0, 0)
        self.props_editor_layout.setSpacing(20)
        
        self.init_properties_controls()
        self.card_props_layout.addWidget(self.widget_props_editor)
        self.widget_props_editor.hide()
        
        self.right_layout.addWidget(self.card_props)
        self.right_layout.addStretch()

    def init_properties_controls(self):
        """Build controls inside properties widget."""
        # Left side: numerical sliders (start time, duration, action name)
        left_side = QWidget(self.widget_props_editor)
        left_layout = QVBoxLayout(left_side)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)
        
        form_widget = QWidget(left_side)
        form_layout = QFormLayout(form_widget)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(12)
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form_layout.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        
        self.lbl_start = BodyLabel("开始时间:", form_widget)
        self.lbl_start.setStyleSheet("color: #333333;")
        self.spin_start = CompactSpinBox(form_widget)
        self.spin_start.setRange(0, 100000)
        self.spin_start.setSuffix(" ms")
        self.spin_start.setMinimumWidth(120)
        self.spin_start.valueChanged.connect(self.on_prop_start_changed)
        
        self.lbl_duration = BodyLabel("按住时长:", form_widget)
        self.lbl_duration.setStyleSheet("color: #333333;")
        self.spin_duration = CompactSpinBox(form_widget)
        self.spin_duration.setRange(10, 100000)
        self.spin_duration.setSuffix(" ms")
        self.spin_duration.setMinimumWidth(120)
        self.spin_duration.valueChanged.connect(self.on_prop_duration_changed)
        
        # Dynamically shown based on type
        self.lbl_prop_action = BodyLabel("动作配置:", form_widget)
        self.lbl_prop_action.setStyleSheet("color: #333333;")
        self.btn_prop_action = PushButton("点击绑定按键", form_widget, FluentIcon.EDIT)
        self.btn_prop_action.setMinimumWidth(120)
        self.btn_prop_action.clicked.connect(self.on_prop_bind_key)
        
        # Coordinates input for mouse click/move
        self.lbl_coord_x = BodyLabel("坐标 X:", form_widget)
        self.lbl_coord_x.setStyleSheet("color: #333333;")
        self.spin_coord_x = CompactSpinBox(form_widget)
        self.spin_coord_x.setRange(0, 7680)
        self.spin_coord_x.setMinimumWidth(120)
        self.spin_coord_x.valueChanged.connect(self.on_prop_x_changed)
        
        self.lbl_coord_y = BodyLabel("坐标 Y:", form_widget)
        self.lbl_coord_y.setStyleSheet("color: #333333;")
        self.spin_coord_y = CompactSpinBox(form_widget)
        self.spin_coord_y.setRange(0, 4320)
        self.spin_coord_y.setMinimumWidth(120)
        self.spin_coord_y.valueChanged.connect(self.on_prop_y_changed)
        
        form_layout.addRow(self.lbl_start, self.spin_start)
        form_layout.addRow(self.lbl_duration, self.spin_duration)
        form_layout.addRow(self.lbl_prop_action, self.btn_prop_action)
        form_layout.addRow(self.lbl_coord_x, self.spin_coord_x)
        form_layout.addRow(self.lbl_coord_y, self.spin_coord_y)
        
        left_layout.addWidget(form_widget)
        left_layout.addStretch()
        
        # Styles for left side controls (Classic Flat style)
        spinbox_style = """
            CompactSpinBox {
                background-color: #ffffff;
                border: 1px solid #cccccc;
                border-radius: 4px;
                color: #000000;
                padding: 4px 8px;
            }
        """
        self.spin_start.setStyleSheet(spinbox_style)
        self.spin_duration.setStyleSheet(spinbox_style)
        self.spin_coord_x.setStyleSheet(spinbox_style)
        self.spin_coord_y.setStyleSheet(spinbox_style)
        
        button_style = """
            PushButton {
                background-color: #ffffff;
                border: 1px solid #cccccc;
                border-radius: 4px;
                color: #333333;
                padding: 4px 10px 4px 36px;
                font-weight: bold;
            }
            PushButton:hover {
                background-color: #f5f5f5;
                border: 1px solid #bbbbbb;
                color: #000000;
            }
            PushButton:pressed {
                background-color: #e5e5e5;
                border: 1px solid #aaaaaa;
            }
        """
        self.btn_prop_action.setStyleSheet(button_style)
        
        self.props_editor_layout.addWidget(left_side, 1)
        
        # Right side: coordinate list editor (specifically for mouse moves paths)
        self.right_side_path = QWidget(self.widget_props_editor)
        right_layout = QVBoxLayout(self.right_side_path)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)
        
        lbl_path_title = BodyLabel("鼠标轨迹折线点 (相对时间, X, Y):", self.right_side_path)
        lbl_path_title.setStyleSheet("font-weight: bold; color: #333333;")
        right_layout.addWidget(lbl_path_title)
        
        self.table_path = TableWidget(self.right_side_path)
        self.table_path.setColumnCount(3)
        self.table_path.setHorizontalHeaderLabels(["时间偏移 (ms)", "坐标 X", "坐标 Y"])
        self.table_path.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_path.setFixedHeight(110)
        self.table_path.itemChanged.connect(self.on_path_item_changed)
        right_layout.addWidget(self.table_path)
        
        # Styles for QTableWidget / TableWidget (Classic Flat style)
        self.table_path.setStyleSheet("""
            TableWidget {
                background-color: #ffffff;
                border: 1px solid #cccccc;
                border-radius: 4px;
                color: #1a1a1a;
                gridline-color: #e2e8f0;
            }
            QTableWidget::item {
                color: #1a1a1a;
            }
            QHeaderView::section {
                background-color: #f8fafc;
                color: #475569;
                border: none;
                border-bottom: 1px solid #e2e8f0;
                font-weight: bold;
                padding: 5px;
            }
        """)
        
        path_buttons = QHBoxLayout()
        self.btn_add_point = PushButton("添加点", self.right_side_path, FluentIcon.ADD)
        self.btn_add_point.clicked.connect(self.on_add_path_point)
        self.btn_del_point = PushButton("删除点", self.right_side_path, FluentIcon.DELETE)
        self.btn_del_point.clicked.connect(self.on_delete_path_point)
        path_buttons.addWidget(self.btn_add_point)
        path_buttons.addWidget(self.btn_del_point)
        right_layout.addLayout(path_buttons)
        
        self.btn_add_point.setStyleSheet(button_style)
        self.btn_del_point.setStyleSheet(button_style)
        right_layout.addStretch()
        
        self.props_editor_layout.addWidget(self.right_side_path, 2)

    # --- Profile List Loader ---
    def populate_category_combo(self):
        self.cmb_category.blockSignals(True)
        self.cmb_category.clear()
        categories = self.macro_manager.get_all_categories()
        self.cmb_category.addItems(categories)
        
        idx = self.cmb_category.findText(self.macro_manager.current_category)
        if idx >= 0:
            self.cmb_category.setCurrentIndex(idx)
        else:
            self.cmb_category.setCurrentIndex(0)
            self.macro_manager.current_category = self.cmb_category.currentText()
        self.cmb_category.blockSignals(False)

    def on_category_changed(self, idx):
        if idx < 0:
            return
        cat_name = self.cmb_category.currentText()
        self.macro_manager.current_category = cat_name
        self.populate_profiles_list()
        self.load_selected_profile_details()

    def on_new_category(self):
        dialog = FluentInputDialog("新建配置分类", "请输入新的分类名称:", parent=self.main_win)
        if dialog.exec():
            text = dialog.lineEdit.text().strip()
            if text:
                cat_name = text
                self.macro_manager.current_category = cat_name
                self.macro_manager.create_new_profile("默认配置")
                self.populate_category_combo()
                self.populate_profiles_list()
                self.load_selected_profile_details()
                InfoBar.success("新建分类成功", f"成功创建并切换至新分类: {cat_name}", parent=self.main_win)

    def on_delete_category(self):
        cat_name = self.cmb_category.currentText()
        if cat_name == "Default":
            InfoBar.warning("删除失败", "默认分类 (Default) 无法被删除。", parent=self.main_win)
            return
            
        from qfluentwidgets import MessageBox
        dialog = MessageBox("确认删除", f"您确定要删除当前分类 '{cat_name}' 及其包含的所有宏配置预设吗？\n删除后不可恢复！", self)
        if dialog.exec():
            pids_to_delete = [pid for pid, prof in self.macro_manager.profiles.items() if prof.category == cat_name]
            
            # If deleting all profiles, create a default one first to bypass the len() <= 1 safety check
            if len(pids_to_delete) >= len(self.macro_manager.profiles):
                self.macro_manager.current_category = "Default"
                self.macro_manager.create_new_profile("新配置预设")
                
            for pid in pids_to_delete:
                self.macro_manager.delete_profile(pid)
                
            self.macro_manager.current_category = "Default"
            self.populate_category_combo()
            self.populate_profiles_list()
            self.load_selected_profile_details()
            InfoBar.success("删除成功", f"已成功删除分类 '{cat_name}' 及其下所有预设。", parent=self.main_win)

    def populate_profiles_list(self):
        self.list_profiles.blockSignals(True)
        self.list_profiles.clear()
        selected_row = 0
        
        filtered_profiles = [
            (pid, prof) for pid, prof in self.macro_manager.profiles.items()
            if getattr(prof, "category", "Default") == self.macro_manager.current_category
        ]
        
        if not filtered_profiles:
            prof = self.macro_manager.create_new_profile("新配置预设")
            filtered_profiles = [(prof.profile_id, prof)]
            
        for i, (pid, prof) in enumerate(filtered_profiles):
            hotkey_name = getattr(prof, "hotkey_name", "")
            if not hotkey_name and hasattr(prof, "hotkeys") and prof.hotkeys:
                hotkey_name = prof.hotkeys[0].get("name", "")
            
            display_text = f"{prof.name} ({hotkey_name})" if hotkey_name else f"{prof.name} (未绑定热键)"
            item = QListWidgetItem(display_text)
            item.setData(Qt.ItemDataRole.UserRole, pid)
            self.list_profiles.addItem(item)
            if pid == self.macro_manager.current_profile_id:
                selected_row = i
                
        self.list_profiles.setCurrentRow(selected_row)
        if filtered_profiles:
            self.macro_manager.current_profile_id = filtered_profiles[selected_row][0]
            
        self.list_profiles.blockSignals(False)

    def on_profile_selected_changed(self, current, previous):
        if not current:
            return
        pid = current.data(Qt.ItemDataRole.UserRole)
        self.macro_manager.current_profile_id = pid
        self.load_selected_profile_details()

    def on_profile_double_clicked(self, item):
        if not item:
            return
        pid = item.data(Qt.ItemDataRole.UserRole)
        profile = self.macro_manager.profiles.get(pid)
        if not profile:
            return
            
        dialog = FluentInputDialog("重命名配置", "请输入新的配置名称:", default_text=profile.name, parent=self.main_win)
        if dialog.exec():
            text = dialog.lineEdit.text().strip()
            if text:
                profile.name = text
                self.macro_manager.save_profile(profile)
                self.populate_profiles_list()
                InfoBar.success("重命名成功", f"配置已重命名为: {profile.name}", parent=self.main_win)

    def load_selected_profile_details(self):
        profile = self.macro_manager.get_current_profile()
        if not profile:
            self.cmb_play_mode.setEnabled(False)
            return
            
        self.cmb_play_mode.setEnabled(True)
        self.cmb_play_mode.blockSignals(True)
        if getattr(profile, "play_mode", "play_once") == "hold_loop":
            self.cmb_play_mode.setCurrentIndex(1)
        elif getattr(profile, "play_mode", "play_once") == "toggle_loop":
            self.cmb_play_mode.setCurrentIndex(2)
        else:
            self.cmb_play_mode.setCurrentIndex(0)
        self.cmb_play_mode.blockSignals(False)
            
        # Populate Canvas with profile Actions
        self.timeline.set_actions(profile.actions)

    def on_play_mode_changed(self, index):
        profile = self.macro_manager.get_current_profile()
        if not profile:
            return
            
        if index == 1:
            profile.play_mode = "hold_loop"
        elif index == 2:
            profile.play_mode = "toggle_loop"
        else:
            profile.play_mode = "play_once"
            
        self.macro_manager.save_profile(profile)

    # --- Ruler Zoom control ---
    def on_zoom_changed(self, val):
        # Slider val 1..40 maps directly to scale (ms per pixel)
        self.timeline.set_scale(val)

    # --- Profile Manager Operations ---
    def on_new_profile(self):
        prof = self.macro_manager.create_new_profile("新宏配置")
        self.populate_profiles_list()
        self.load_selected_profile_details()
        InfoBar.success("新建成功", f"成功创建宏配置: {prof.name}", parent=self.main_win)

    def on_clone_profile(self):
        profile = self.macro_manager.get_current_profile()
        if not profile:
            return
        cloned = MacroProfile(name=f"{profile.name} (副本)")
        cloned.category = getattr(profile, "category", "Default")
        cloned.hotkeys = list(getattr(profile, "hotkeys", []))
        cloned.hotkey_code = profile.hotkey_code
        cloned.hotkey_type = profile.hotkey_type
        cloned.hotkey_name = profile.hotkey_name
        cloned.record_mode = getattr(profile, "record_mode", "event")
        cloned.replay_mode = getattr(profile, "replay_mode", "send_input")
        cloned.play_mode = getattr(profile, "play_mode", "play_once")
        cloned.smooth_mouse = profile.smooth_mouse
        cloned.jitter_range_ms = profile.jitter_range_ms
        cloned.actions = [MacroAction(act.time_ms, act.event_type, act.key_code, act.key_name, act.x, act.y, getattr(act, "active_keys", [])) for act in profile.actions]
        
        self.macro_manager.save_profile(cloned)
        self.macro_manager.current_profile_id = cloned.profile_id
        self.populate_profiles_list()
        self.load_selected_profile_details()
        InfoBar.success("克隆成功", f"成功克隆配置: {cloned.name}", parent=self.main_win)

    def on_delete_profile(self):
        profile = self.macro_manager.get_current_profile()
        if not profile:
            return
        if len(self.macro_manager.profiles) <= 1:
            InfoBar.warning("无法删除", "系统必须保留至少一个宏配置。", parent=self.main_win)
            return
            
        name = profile.name
        if self.macro_manager.delete_profile(profile.profile_id):
            self.populate_profiles_list()
            self.load_selected_profile_details()
            InfoBar.success("删除成功", f"配置 '{name}' 已删除。", parent=self.main_win)

    def on_import_profile(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "导入宏配置文件", "", "CoreCommander Macro (*.ccmacro)")
        if filepath:
            try:
                prof = self.macro_manager.import_profile(filepath)
                self.populate_profiles_list()
                self.load_selected_profile_details()
                InfoBar.success("导入成功", f"宏配置 '{prof.name}' 导入成功。", parent=self.main_win)
            except Exception as e:
                InfoBar.error("导入失败", f"文件格式损坏或不兼容。错误: {e}", parent=self.main_win)

    def on_export_profile(self):
        profile = self.macro_manager.get_current_profile()
        if not profile:
            return
        filepath, _ = QFileDialog.getSaveFileName(self, "导出宏配置文件", f"{profile.name}.ccmacro", "CoreCommander Macro (*.ccmacro)")
        if filepath:
            if self.macro_manager.export_profile(profile.profile_id, filepath):
                InfoBar.success("导出成功", f"成功导出配置到: {filepath}", parent=self.main_win)
            else:
                InfoBar.error("导出失败", "写入文件时发生系统错误。", parent=self.main_win)

    # --- Hotkey Settings ---
    def on_hotkey_type_changed(self, idx):
        # Deprecated: multi-hotkeys manages type per-key automatically.
        pass

    def on_start_hotkey_binding(self):
        if not self.input_hook:
            InfoBar.error("绑定失败", "全局输入驱动钩子尚未就绪，请稍后重试。", parent=self.main_win)
            return
        self.is_binding_hotkey = True
        self.btn_bind.setText("请按下按键...")
        self.btn_bind.setEnabled(False)
        self.input_hook.key_bind_captured.connect(self.on_hotkey_bound_captured)
        self.input_hook.set_binding_mode(True)
        
    def on_hotkey_bound_captured(self, name, code, key_type):
        try:
            self.input_hook.key_bind_captured.disconnect(self.on_hotkey_bound_captured)
        except Exception:
            pass
        self.is_binding_hotkey = False
        self.btn_bind.setEnabled(True)
        self.btn_bind.setText("一键改键")
        
        profile = self.macro_manager.get_current_profile()
        if profile:
            profile.hotkey_code = code
            profile.hotkey_type = key_type
            profile.hotkey_name = name
            profile.hotkeys = [{"code": code, "type": key_type, "name": name}]
            
            self.macro_manager.save_profile(profile)
            self.populate_profiles_list()
            self.load_selected_profile_details()
            
            if hasattr(self.main_win, "macro_overlay") and self.main_win.macro_overlay:
                self.main_win.macro_overlay.refresh_ui()
            InfoBar.success("热键绑定成功", f"成功绑定触发热键: {name}", parent=self.main_win)

    def on_clear_hotkey(self):
        profile = self.macro_manager.get_current_profile()
        if profile:
            profile.hotkey_code = 0
            profile.hotkey_type = "none"
            profile.hotkey_name = ""
            profile.hotkeys = []
                
            self.macro_manager.save_profile(profile)
            self.populate_profiles_list()
            self.load_selected_profile_details()
            if hasattr(self.main_win, "macro_overlay") and self.main_win.macro_overlay:
                self.main_win.macro_overlay.refresh_ui()
            InfoBar.success("热键清除成功", f"已成功清除触发热键", parent=self.main_win)

    # --- OSD Configurations ---
    def on_hud_enable_changed(self, checked):
        self.settings.set_value("enable_macro_hud", checked)
        if checked:
            if hasattr(self.main_win, "macro_overlay") and self.main_win.macro_overlay:
                self.main_win.macro_overlay.show()
        else:
            if hasattr(self.main_win, "macro_overlay") and self.main_win.macro_overlay:
                self.main_win.macro_overlay.hide()

    def on_hud_lock_changed(self, checked):
        self.settings.set_value("macro_hud_locked", checked)
        if hasattr(self.main_win, "macro_overlay") and self.main_win.macro_overlay:
            self.main_win.macro_overlay.set_locked(checked)



    # --- Canvas Blocks Modified callback ---
    def on_timeline_blocks_modified(self):
        """Re-parse blocks list, map coordinates, and save to profile."""
        profile = self.macro_manager.get_current_profile()
        if profile:
            profile.actions = self.timeline.get_actions(record_mode=profile.record_mode)
            self.macro_manager.save_profile(profile)
            
            # Sync OSD OSD HUD if active
            if hasattr(self.main_win, "macro_overlay") and self.main_win.macro_overlay:
                self.main_win.macro_overlay.refresh_ui()
                
            # If selected block start/dur changed, sync inputs
            if self.timeline.selected_block:
                self.load_block_properties(self.timeline.selected_block)

    # --- Properties Selection change handler ---
    def on_timeline_selection_changed(self, block):
        if len(self.timeline.selected_blocks) > 1:
            return
        if not block:
            self.widget_props_editor.hide()
            self.lbl_props_placeholder.show()
        else:
            self.lbl_props_placeholder.hide()
            self.load_block_properties(block)
            self.widget_props_editor.show()

    def on_timeline_multi_selection_changed(self, blocks):
        if len(blocks) <= 1:
            return
        self.lbl_props_placeholder.hide()
        self.load_batch_properties(blocks)
        self.widget_props_editor.show()

    def load_block_properties(self, block):
        self.spin_start.blockSignals(True)
        self.spin_duration.blockSignals(True)
        self.spin_coord_x.blockSignals(True)
        self.spin_coord_y.blockSignals(True)
        
        self.lbl_start.setText("开始时间:")
        self.spin_start.setRange(0, 100000)
        self.spin_start.setValue(block.start_time)
        self.spin_start.setSuffix(" ms")
        
        self.lbl_duration.setText("按住时长:")
        self.spin_duration.setRange(10, 100000)
        self.spin_duration.setValue(block.duration)
        self.spin_duration.setSuffix(" ms")
        
        # Display based on type
        if block.type_str == "keyboard":
            self.lbl_prop_action.show()
            self.btn_prop_action.show()
            self.btn_prop_action.setText(f"键盘键: {block.key_name}")
            
            self.lbl_coord_x.hide()
            self.spin_coord_x.hide()
            self.lbl_coord_y.hide()
            self.spin_coord_y.hide()
            self.right_side_path.hide()
        elif block.type_str == "mouse_click":
            self.lbl_prop_action.show()
            self.btn_prop_action.show()
            # 1=L, 2=R, 4=M
            btn_name = {0x01: "Left Click", 0x02: "Right Click", 0x04: "Middle Click"}.get(block.key_code, "Left Click")
            self.btn_prop_action.setText(f"点击键: {btn_name}")
            
            self.lbl_coord_x.show()
            self.spin_coord_x.show()
            self.spin_coord_x.setValue(block.x)
            
            self.lbl_coord_y.show()
            self.spin_coord_y.show()
            self.spin_coord_y.setValue(block.y)
            self.right_side_path.hide()
        elif block.type_str == "mouse_move":
            self.lbl_prop_action.hide()
            self.btn_prop_action.hide()
            
            self.lbl_coord_x.hide()
            self.spin_coord_x.hide()
            self.lbl_coord_y.hide()
            self.spin_coord_y.hide()
            
            # Show path table list
            self.right_side_path.show()
            self.populate_path_table(block)
            
        self.spin_start.blockSignals(False)
        self.spin_duration.blockSignals(False)
        self.spin_coord_x.blockSignals(False)
        self.spin_coord_y.blockSignals(False)

    def populate_path_table(self, block):
        self.table_path.blockSignals(True)
        self.table_path.clearContents()
        self.table_path.setRowCount(len(block.path_points))
        
        for row, (rel_t, cx, cy) in enumerate(block.path_points):
            # 1. Rel Time
            item_t = QTableWidgetItem(str(rel_t))
            item_t.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_t.setForeground(QColor("#1a1a1a"))
            self.table_path.setItem(row, 0, item_t)
            
            # 2. X
            item_x = QTableWidgetItem(str(cx))
            item_x.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_x.setForeground(QColor("#1a1a1a"))
            self.table_path.setItem(row, 1, item_x)
            
            # 3. Y
            item_y = QTableWidgetItem(str(cy))
            item_y.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_y.setForeground(QColor("#1a1a1a"))
            self.table_path.setItem(row, 2, item_y)
            
        self.table_path.blockSignals(False)

    def load_batch_properties(self, blocks):
        self.spin_start.blockSignals(True)
        self.spin_duration.blockSignals(True)
        self.spin_coord_x.blockSignals(True)
        self.spin_coord_y.blockSignals(True)
        
        self.lbl_start.setText("时间偏移:")
        self.spin_start.setRange(-100000, 100000)
        self.spin_start.setValue(0)
        self.spin_start.setSuffix(" ms")
        
        self.lbl_duration.setText("统一时长:")
        self.spin_duration.setRange(10, 100000)
        self.spin_duration.setValue(blocks[0].duration)
        self.spin_duration.setSuffix(" ms")
        
        self.lbl_prop_action.hide()
        self.btn_prop_action.hide()
        self.right_side_path.hide()
        
        has_mouse = any(b.type_str in ("mouse_click", "mouse_move") for b in blocks)
        if has_mouse:
            self.lbl_coord_x.show()
            self.spin_coord_x.show()
            first_mouse = next(b for b in blocks if b.type_str in ("mouse_click", "mouse_move"))
            self.spin_coord_x.setValue(first_mouse.x)
            
            self.lbl_coord_y.show()
            self.spin_coord_y.show()
            self.spin_coord_y.setValue(first_mouse.y)
        else:
            self.lbl_coord_x.hide()
            self.spin_coord_x.hide()
            self.lbl_coord_y.hide()
            self.spin_coord_y.hide()
            
        self.spin_start.blockSignals(False)
        self.spin_duration.blockSignals(False)
        self.spin_coord_x.blockSignals(False)
        self.spin_coord_y.blockSignals(False)

    # --- Properties Editing Event slots ---
    def on_prop_start_changed(self, val):
        self.timeline.push_undo_state()
        blocks = self.timeline.selected_blocks
        if len(blocks) > 1:
            if val == 0:
                return
            for block in blocks:
                left_lim, right_lim = self.timeline._get_track_drag_limits(block, block.track_index)
                new_start = block.start_time + val
                new_start = max(left_lim, min(right_lim - block.duration, new_start))
                new_start = round(new_start / 10.0) * 10
                block.start_time = new_start
                
            self.spin_start.blockSignals(True)
            self.spin_start.setValue(0)
            self.spin_start.blockSignals(False)
            
            self.on_timeline_blocks_modified()
        elif len(blocks) == 1:
            block = blocks[0]
            left_lim, right_lim = self.timeline._get_track_drag_limits(block, block.track_index)
            new_start = max(left_lim, min(right_lim - block.duration, val))
            new_start = round(new_start / 10.0) * 10
            if new_start < left_lim:
                new_start = left_lim
            if new_start + block.duration > right_lim:
                new_start = right_lim - block.duration
            block.start_time = new_start
            
            self.spin_start.blockSignals(True)
            self.spin_start.setValue(block.start_time)
            self.spin_start.blockSignals(False)
            
            self.on_timeline_blocks_modified()

    def on_prop_duration_changed(self, val):
        self.timeline.push_undo_state()
        blocks = self.timeline.selected_blocks
        if len(blocks) > 1:
            for block in blocks:
                left_lim, right_lim = self.timeline._get_track_drag_limits(block, block.track_index, exclude_selected=True)
                new_dur = max(10, min(right_lim - block.start_time, val))
                new_dur = round(new_dur / 10.0) * 10
                block.duration = new_dur
            self.on_timeline_blocks_modified()
        elif len(blocks) == 1:
            block = blocks[0]
            left_lim, right_lim = self.timeline._get_track_drag_limits(block, block.track_index)
            new_dur = max(10, min(right_lim - block.start_time, val))
            new_dur = round(new_dur / 10.0) * 10
            if block.start_time + new_dur > right_lim:
                new_dur = right_lim - block.start_time
            block.duration = max(10, new_dur)
            
            self.spin_duration.blockSignals(True)
            self.spin_duration.setValue(block.duration)
            self.spin_duration.blockSignals(False)
            
            self.on_timeline_blocks_modified()

    def on_prop_x_changed(self, val):
        self.timeline.push_undo_state()
        blocks = self.timeline.selected_blocks
        if len(blocks) > 1:
            for block in blocks:
                if block.type_str in ("mouse_click", "mouse_move"):
                    block.x = val
            self.on_timeline_blocks_modified()
        elif len(blocks) == 1:
            block = blocks[0]
            block.x = val
            self.on_timeline_blocks_modified()

    def on_prop_y_changed(self, val):
        self.timeline.push_undo_state()
        blocks = self.timeline.selected_blocks
        if len(blocks) > 1:
            for block in blocks:
                if block.type_str in ("mouse_click", "mouse_move"):
                    block.y = val
            self.on_timeline_blocks_modified()
        elif len(blocks) == 1:
            block = blocks[0]
            block.y = val
            self.on_timeline_blocks_modified()

    def on_prop_bind_key(self, _=None):
        block = self.timeline.selected_block
        if not block or not self.input_hook:
            return
            
        self.is_binding_block_key = True
        self.btn_prop_action.setText("按下按键...")
        self.btn_prop_action.setEnabled(False)
        
        self.input_hook.key_bind_captured.connect(self.on_block_key_bound_captured)
        self.input_hook.set_binding_mode(True)

    def on_block_key_bound_captured(self, name, code, key_type):
        try:
            self.input_hook.key_bind_captured.disconnect(self.on_block_key_bound_captured)
        except Exception:
            pass
        self.is_binding_block_key = False
        self.btn_prop_action.setEnabled(True)
        
        block = self.timeline.selected_block
        if block:
            self.timeline.push_undo_state()
            block.key_code = code
            block.key_name = name
            
            if block.type_str == "keyboard":
                self.btn_prop_action.setText(f"键盘键: {name}")
            elif block.type_str == "mouse_click":
                self.btn_prop_action.setText(f"点击键: {name}")
                
            self.on_timeline_blocks_modified()

    # --- Mouse Path Editor slots ---
    def on_path_item_changed(self, item):
        block = self.timeline.selected_block
        if not block or block.type_str != "mouse_move":
            return
        self.timeline.push_undo_state()
            
        row = item.row()
        col = item.column()
        text = item.text().strip()
        
        if row >= len(block.path_points):
            return
            
        rel_t, cx, cy = block.path_points[row]
        
        try:
            if col == 0:
                rel_t = max(0, int(text))
            elif col == 1:
                cx = max(0, int(text))
            elif col == 2:
                cy = max(0, int(text))
        except ValueError:
            pass
            
        block.path_points[row] = (rel_t, cx, cy)
        # Re-sort points inside path by relative offset
        block.path_points.sort(key=lambda p: p[0])
        # Auto-update duration to last node time
        if block.path_points:
            block.duration = max(10, block.path_points[-1][0])
            
        self.on_timeline_blocks_modified()
        self.populate_path_table(block)

    def on_add_path_point(self):
        block = self.timeline.selected_block
        if not block or block.type_str != "mouse_move":
            return
        self.timeline.push_undo_state()
        next_t = 100
        if block.path_points:
            next_t = block.path_points[-1][0] + 100
        block.path_points.append((next_t, 500, 500))
        block.duration = max(10, block.path_points[-1][0])
        
        self.on_timeline_blocks_modified()
        self.populate_path_table(block)

    def on_delete_path_point(self):
        block = self.timeline.selected_block
        if not block or block.type_str != "mouse_move":
            return
        self.timeline.push_undo_state()
        row = self.table_path.currentRow()
        if row < 0 or row >= len(block.path_points):
            return
        block.path_points.pop(row)
        if block.path_points:
            block.duration = max(10, block.path_points[-1][0])
        self.on_timeline_blocks_modified()
        self.populate_path_table(block)

    def on_clear_all_blocks(self):
        self.timeline.push_undo_state()
        profile = self.macro_manager.get_current_profile()
        if profile:
            profile.actions.clear()
            self.macro_manager.save_profile(profile)
            self.timeline.set_actions([])

    # --- Recording/Replay Controller triggers ---
    def toggle_recording(self):
        if not self.input_hook:
            return
        if self.macro_manager.state == "idle":
            from core_commander.ui.activation_dialog import require_license
            if not require_license(self, "按键与鼠标录制"):
                return
            if self.macro_manager.start_recording(self.input_hook):
                self.btn_record.setText("停止录制 (F10)")
                self.btn_replay.setEnabled(False)
        elif self.macro_manager.state == "recording":
            if self.macro_manager.stop_recording(self.input_hook):
                self.btn_record.setText("开始录制 (F10)")
                self.btn_replay.setEnabled(True)
                self.load_selected_profile_details()

    def toggle_playback(self):
        if self.macro_manager.state == "idle":
            from core_commander.ui.activation_dialog import require_license
            if not require_license(self, "按键与鼠标录制"):
                return
            if self.macro_manager.start_replay():
                self.btn_replay.setText("中止回放")
                self.btn_record.setEnabled(False)
        elif self.macro_manager.state == "replaying":
            self.macro_manager.stop_replay()
            self.btn_record.setEnabled(True)
            self.btn_replay.setText("回放测试")

    def on_manager_state_changed(self, state, name):
        if state == "idle":
            self.btn_record.setText("开始录制 (F10)")
            self.btn_record.setEnabled(True)
            self.btn_replay.setText("回放测试")
            self.btn_replay.setEnabled(True)
            self.load_selected_profile_details()
        elif state == "recording":
            self.btn_record.setText("停止录制 (F10)")
            self.btn_record.setEnabled(True)
            self.btn_replay.setEnabled(False)
        elif state == "replaying":
            self.btn_record.setEnabled(False)
            self.btn_replay.setText("中止回放")
            self.btn_replay.setEnabled(True)

    def on_arrange_blocks(self):
        selected = self.timeline.selected_blocks
        if not selected and self.timeline.selected_block:
            selected = [self.timeline.selected_block]

        profile = self.macro_manager.get_current_profile()
        if not profile:
            return

        if selected:
            # Group selected blocks by track_index
            groups = {}
            for block in selected:
                groups.setdefault(block.track_index, []).append(block)
            
            # Arrange per track: snap selected blocks sequentially within their own track
            for track_idx, blocks_in_track in groups.items():
                sorted_blocks = sorted(blocks_in_track, key=lambda b: b.start_time)
                curr_time = sorted_blocks[0].start_time
                for block in sorted_blocks:
                    block.start_time = curr_time
                    curr_time += block.duration
        else:
            # Global tidy: eliminate global dead time (silence periods across all tracks)
            all_blocks = self.timeline.blocks
            if not all_blocks:
                return
                
            # 1. Gather all active intervals
            intervals = []
            for block in all_blocks:
                intervals.append((block.start_time, block.start_time + block.duration))
                
            # 2. Merge overlapping or touching intervals
            intervals.sort(key=lambda x: x[0])
            merged = []
            for start, end in intervals:
                if not merged or merged[-1][1] < start:
                    merged.append([start, end])
                else:
                    merged[-1][1] = max(merged[-1][1], end)
                    
            # 3. Compress gaps between disjoint active intervals (cap maximum gap to 50ms)
            min_gap = 50  # ms
            new_last_end = 0
            last_end = 0
            shifts = {} # (orig_start, orig_end) -> shift
            
            for start, end in merged:
                gap = start - last_end
                new_gap = min(gap, min_gap) if gap >= 0 else gap
                new_start = new_last_end + new_gap
                shift = start - new_start
                shifts[(start, end)] = shift
                
                new_last_end = new_start + (end - start)
                last_end = end
                
            # 4. Apply shift to each block using center point check
            for block in all_blocks:
                center = block.start_time + block.duration / 2
                for (orig_start, orig_end), shift in shifts.items():
                    if orig_start <= center <= orig_end:
                        block.start_time -= shift
                        break
                        
        # Save changes to profile, and reload actions to recalculate clean track indices
        self.on_timeline_blocks_modified()
        actions = self.timeline.get_actions(record_mode=profile.record_mode)
        self.timeline.set_actions(actions)
        self.on_timeline_blocks_modified()
        self.timeline.update()

    def on_playback_progress_updated(self, elapsed_ms):
        """Callback to update red playhead on timeline canvas during playback."""
        self.timeline.set_playhead(elapsed_ms)

    def setup_timeline_sync(self):
        overlay = getattr(self.main_win, "macro_overlay", None)
        if not overlay or not overlay.timeline:
            return
            
        t_main = self.timeline
        t_over = overlay.timeline
        
        # Sync scale (zoom)
        def sync_scale(source, target):
            def handler(val):
                if target.scale != val:
                    target.blockSignals(True)
                    target.set_scale(val)
                    target.blockSignals(False)
            return handler
            
        t_main.scaleChanged.connect(sync_scale(t_main, t_over))
        t_over.scaleChanged.connect(sync_scale(t_over, t_main))
        
        # Sync scroll offset
        def sync_scroll(source, target):
            def handler(val):
                if target.scroll_offset_x != val:
                    target.blockSignals(True)
                    target.set_scroll_offset(val)
                    target.blockSignals(False)
            return handler
            
        t_main.scrollOffsetChanged.connect(sync_scroll(t_main, t_over))
        t_over.scrollOffsetChanged.connect(sync_scroll(t_over, t_main))
        
        # Sync playhead
        def sync_playhead(source, target):
            def handler(val):
                if target.playhead_ms != val:
                    target.blockSignals(True)
                    target.set_playhead(val)
                    target.blockSignals(False)
            return handler
            
        t_main.playheadChanged.connect(sync_playhead(t_main, t_over))
        t_over.playheadChanged.connect(sync_playhead(t_over, t_main))

        # Sync selection
        def sync_selection(source, target):
            def handler(block):
                if not block:
                    target.selected_block = None
                    target.selected_blocks = []
                else:
                    found = next((b for b in target.blocks if b.block_id == block.block_id), None)
                    target.selected_block = found
                    target.selected_blocks = [found] if found else []
                target.update()
            return handler
            
        t_main.selectionChanged.connect(sync_selection(t_main, t_over))
        t_over.selectionChanged.connect(sync_selection(t_over, t_main))
