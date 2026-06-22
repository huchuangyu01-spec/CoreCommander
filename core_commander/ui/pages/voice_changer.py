# -*- coding: utf-8 -*-
import os
import shutil
from PySide6.QtCore import Qt, QTimer, QThread, Signal, QSize, Property
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFileDialog, 
    QScrollArea, QFrame, QSizePolicy, QLabel, QListWidget, QListWidgetItem, QDialog
)
from qfluentwidgets import (Pivot, 
    ScrollArea, BodyLabel, ComboBox, SwitchButton, FlowLayout,
    PrimaryPushButton, PushButton, InfoBar, CardWidget, Slider, 
    CaptionLabel, TransparentToolButton, FluentIcon as FIF,
    SubtitleLabel, IconWidget, Action, ToolButton, setTheme, Theme, MessageBox,
    isDarkTheme, TitleLabel, ProgressBar
)

from core_commander.core.voice_changer.engine import (
    HAS_DEPENDENCIES, VoiceChangerEngine, get_audio_devices
)
from core_commander.core.voice_changer.downloader import MODEL_DIR
from core_commander.core.voice_changer.driver_installer import install_driver, is_driver_installed
from core_commander.utils.logger import logger
from core_commander.utils.i18n import Trans

import json

CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "voice_changer_config.json")

class VoiceChangerPageInitThread(QThread):
    finished_signal = Signal(list, list, bool) # inputs, outputs, is_installed
    
    def __init__(self, force_refresh=False, parent=None):
        super().__init__(parent)
        self.force_refresh = force_refresh
        
    def run(self):
        try:
            from core_commander.core.voice_changer.engine import get_audio_devices, HAS_DEPENDENCIES
            from core_commander.core.voice_changer.driver_installer import is_driver_installed
            
            is_installed = is_driver_installed(force_refresh=self.force_refresh)
            
            if HAS_DEPENDENCIES:
                inputs, outputs = get_audio_devices(force_refresh=self.force_refresh)
            else:
                inputs, outputs = [], []
                
            self.finished_signal.emit(inputs, outputs, is_installed)
        except Exception as e:
            logger.error(f"VoiceChangerPageInitThread exception: {e}")
            self.finished_signal.emit([], [], False)

class ModelDownloadWorker(QThread):
    progress_signal = Signal(float, str, str)  # percentage, filename, speed_text
    finished_signal = Signal(bool, str)        # success, error_msg

    def __init__(self, dest_dir):
        super().__init__()
        self.dest_dir = dest_dir
        self._is_cancelled = False
        
    def cancel(self):
        self._is_cancelled = True

    def run(self):
        import requests
        import time
        os.makedirs(self.dest_dir, exist_ok=True)
        
        files = {
            "hubert_base.pt": "https://hf-mirror.com/Daswer123/RVC_Base/resolve/main/hubert_base.pt",
            "rmvpe.pt": "https://hf-mirror.com/Daswer123/RVC_Base/resolve/main/rmvpe.pt"
        }
        
        try:
            for filename, url in files.items():
                file_path = os.path.join(self.dest_dir, filename)
                if os.path.exists(file_path):
                    if os.path.getsize(file_path) > 1024*1024:
                        continue
                
                temp_path = file_path + ".tmp"
                start_time = time.time()
                downloaded = 0
                
                try:
                    response = requests.get(url, stream=True, timeout=30)
                    response.raise_for_status()
                    total_size = int(response.headers.get('content-length', 0))
                    
                    with open(temp_path, "wb") as f:
                        for chunk in response.iter_content(chunk_size=1024*64):
                            if self._is_cancelled:
                                f.close()
                                if os.path.exists(temp_path):
                                    os.remove(temp_path)
                                self.finished_signal.emit(False, "下载已取消")
                                return
                            if chunk:
                                f.write(chunk)
                                downloaded += len(chunk)
                                if total_size > 0:
                                    percent = (downloaded / total_size) * 100
                                    elapsed = time.time() - start_time
                                    speed = downloaded / (elapsed if elapsed > 0 else 0.001)
                                    speed_mb = speed / (1024*1024)
                                    speed_text = f"{speed_mb:.2f} MB/s"
                                    self.progress_signal.emit(percent, filename, speed_text)
                    
                    if os.path.exists(temp_path):
                        if os.path.exists(file_path):
                            os.remove(file_path)
                        os.rename(temp_path, file_path)
                except Exception as inner_e:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    raise inner_e
            
            self.finished_signal.emit(True, "")
        except Exception as e:
            self.finished_signal.emit(False, str(e))

class ModelDownloadDialog(QDialog):
    def __init__(self, parent=None):
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout
        super().__init__(parent)
        self.setWindowTitle("下载基础模型权重")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint)
        self.resize(450, 180)
        self.setModal(True)
        
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(24, 24, 24, 24)
        self.layout.setSpacing(16)
        
        self.title_label = TitleLabel("正在初始化变声器基础模型", self)
        self.title_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.layout.addWidget(self.title_label)
        
        self.status_label = BodyLabel("准备开始从国内高速镜像源下载...", self)
        self.layout.addWidget(self.status_label)
        
        self.progress_bar = ProgressBar(self)
        self.progress_bar.setValue(0)
        self.layout.addWidget(self.progress_bar)
        
        self.btn_layout = QHBoxLayout()
        self.btn_layout.addStretch()
        self.cancel_btn = PushButton("取消", self)
        self.cancel_btn.clicked.connect(self.on_cancel)
        self.btn_layout.addWidget(self.cancel_btn)
        self.layout.addLayout(self.btn_layout)
        
        self.worker = None
        self.is_success = False
        
    def start_download(self, dest_dir):
        self.worker = ModelDownloadWorker(dest_dir)
        self.worker.progress_signal.connect(self.on_progress)
        self.worker.finished_signal.connect(self.on_finished)
        self.worker.start()
        
    def on_progress(self, percent, filename, speed_text):
        self.progress_bar.setValue(int(percent))
        self.status_label.setText(f"正在下载: {filename} - {percent:.1f}% ({speed_text})")
        
    def on_cancel(self):
        if self.worker:
            self.status_label.setText("正在取消下载，请稍候...")
            self.cancel_btn.setEnabled(False)
            self.worker.cancel()
            self.worker.wait()
        self.reject()
        
    def on_finished(self, success, error_msg):
        if success:
            self.is_success = True
            self.accept()
        else:
            self.is_success = False
            if "下载已取消" not in error_msg:
                from qfluentwidgets import InfoBar
                InfoBar.error("下载失败", f"基础模型权重下载失败: {error_msg}", duration=3000, parent=self.parent())
            self.reject()
            
    def closeEvent(self, event):
        self.on_cancel()
        event.accept()

class ModelListItem(CardWidget):
    itemClicked = Signal(str)
    deleteClicked = Signal(str)
    
    def __init__(self, file_name, parent=None):
        super().__init__(parent)
        self.file_name = file_name
        self.is_pth = file_name.endswith(".pth")
        self.setFixedHeight(64)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 10, 20, 10)
        layout.setSpacing(16)
        
        # Icon
        icon = FIF.ROBOT if self.is_pth else FIF.SEARCH
        self.icon_widget = IconWidget(icon, self)
        self.icon_widget.setFixedSize(24, 24)
        layout.addWidget(self.icon_widget)
        
        # Name
        self.name_label = BodyLabel(self.file_name)
        self.name_label.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(self.name_label)
        
        layout.addStretch(1)
        
        # Status
        self.status_label = CaptionLabel("未激活")
        self.status_label.setStyleSheet("color: rgba(0,0,0,0.4); font-size: 13px;")
        layout.addWidget(self.status_label)
        
        # Delete Button
        self.delete_btn = TransparentToolButton(FIF.DELETE, self)
        self.delete_btn.setToolTip("删除模型")
        self.delete_btn.setFixedSize(32, 32)
        # Prevent mouse press on delete button from triggering the card click
        self.delete_btn.clicked.connect(self._on_delete_clicked)
        layout.addWidget(self.delete_btn)

    def _on_delete_clicked(self):
        self.deleteClicked.emit(self.file_name)

    def set_selected(self, selected):
        is_dark = isDarkTheme()
        if selected:
            self.setStyleSheet("CardWidget { border: 2px solid #009faa; background: rgba(0, 159, 170, 0.15); border-radius: 8px; }")
            self.status_label.setText(Trans.get("vc_status_mounted", "▶ 已加载"))
            self.status_label.setStyleSheet("color: #009faa; font-weight: bold; font-size: 13px;")
        else:
            border_color = "rgba(255,255,255,0.08)" if is_dark else "rgba(0,0,0,0.06)"
            text_color = "rgba(255,255,255,0.5)" if is_dark else "rgba(0,0,0,0.4)"
            self.setStyleSheet(f"CardWidget {{ border: 1px solid {border_color}; background: transparent; border-radius: 8px; }}")
            self.status_label.setText(Trans.get("vc_status_unmounted", "未加载"))
            self.status_label.setStyleSheet(f"color: {text_color}; font-size: 13px;")
            
    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self.itemClicked.emit(self.file_name)


class VoiceChangerPage(ScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.setObjectName("VoiceChangerPage")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setViewportMargins(0, 0, 0, 0)
        self.setStyleSheet("QScrollArea { background-color: transparent; border: none; }")
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        
        self.view = QWidget()
        self.view.setObjectName("VoiceChangerPageView")
        self.view.setStyleSheet("#VoiceChangerPageView { background-color: transparent; }")
        self.view.setMaximumWidth(1100)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        
        self.vBoxLayout = QVBoxLayout(self.view)
        self.vBoxLayout.setContentsMargins(30, 30, 30, 30)
        self.vBoxLayout.setSpacing(24)
        
        self.title_label = TitleLabel("AI 实时变声与调音台")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.vBoxLayout.addWidget(self.title_label)
        
        self.active_pth = ""
        self.active_index = ""
        self.vst_path = ""
        self.active_old_engines = []
        
        self.setup_top_control_bar()
        self.setup_tuning_section()
        self.setup_model_list()
        
        # Delay initialization to prevent UI lag when switching to this page
        QTimer.singleShot(10, self._delayed_init)

    def _delayed_init(self):
        self.refresh_devices(force_refresh=False)
        self.refresh_local_models()
        
        # Connect audio device changes to engine restart
        self.input_combo.currentIndexChanged.connect(self._on_device_changed)
        self.output_combo.currentIndexChanged.connect(self._on_device_changed)
        
        # Load user configuration
        QTimer.singleShot(100, self.load_config)

    def setup_top_control_bar(self):
        self.top_card = CardWidget()
        layout = QHBoxLayout(self.top_card)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(16)
        
        # --- Left: Audio Routing ---
        dev_layout = QHBoxLayout()
        dev_layout.setSpacing(16)
        
        in_layout = QHBoxLayout()
        in_icon = IconWidget(FIF.MICROPHONE)
        in_icon.setFixedSize(20, 20)
        self.input_combo = ComboBox()
        self.input_combo.setPlaceholderText(Trans.get("vc_input_placeholder", "选择输入设备 (麦克风)"))
        self.input_combo.setMinimumWidth(220)
        in_layout.addWidget(in_icon)
        in_layout.addWidget(self.input_combo)
        
        out_layout = QHBoxLayout()
        out_icon = IconWidget(FIF.SPEAKERS)
        out_icon.setFixedSize(20, 20)
        self.output_combo = ComboBox()
        self.output_combo.setPlaceholderText(Trans.get("vc_output_placeholder", "选择输出设备 (虚拟音频)"))
        self.output_combo.setMinimumWidth(220)
        out_layout.addWidget(out_icon)
        out_layout.addWidget(self.output_combo)
        
        self.driver_btn = PushButton(FIF.WIFI, Trans.get("vc_driver_btn_route", "配置音频路由"))
        self.driver_btn.clicked.connect(self.trigger_driver_installation)
        out_layout.addWidget(self.driver_btn)
        
        dev_layout.addLayout(in_layout)
        dev_layout.addLayout(out_layout)
        layout.addLayout(dev_layout)
        
        layout.addStretch(1)
        
        # --- Center: Master Control ---
        center_layout = QHBoxLayout()
        center_layout.setSpacing(20)
        
        self.engine_status = BodyLabel(Trans.get("vc_status_ready", "状态: 就绪"))
        self.engine_status.setStyleSheet("color: rgba(0,0,0,0.5); font-weight: bold;")
        
        self.switch_btn = PrimaryPushButton(FIF.PLAY_SOLID, Trans.get("vc_switch_btn_start", "启动引擎"))
        self.switch_btn.setFixedSize(160, 42)
        self.switch_btn.setCheckable(True)
        self.switch_btn.toggled.connect(self.toggle_voice_changer)
        
        center_layout.addWidget(self.engine_status)
        center_layout.addWidget(self.switch_btn)
        layout.addLayout(center_layout)
        
        layout.addStretch(1)
        
        self.vBoxLayout.addWidget(self.top_card)

    def setup_tuning_section(self):
        # Middle section: Two cards side by side
        tuning_layout = QHBoxLayout()
        tuning_layout.setSpacing(24)
        
        # Card 1: Basic Acoustics
        self.basic_card = CardWidget()
        basic_layout = QVBoxLayout(self.basic_card)
        basic_layout.setContentsMargins(24, 20, 24, 20)
        basic_layout.setSpacing(20)
        
        self.basic_title = SubtitleLabel(Trans.get("vc_basic_acoustics", "基础声学"))
        self.basic_title.setStyleSheet("font-size: 16px;")
        basic_layout.addWidget(self.basic_title)
        
        self.pitch_slider, _ = self._create_slider(basic_layout, Trans.get("vc_pitch_shift", "音调转换 (Pitch Shift)"), -24, 24, 0)
        self.rms_slider, _ = self._create_slider(basic_layout, Trans.get("vc_rms_mix", "包络融合 (RMS Mix)"), 0, 100, 25)
        
        m_layout = QHBoxLayout()
        self.method_combo = ComboBox()
        self.method_combo.addItems(["rmvpe", "crepe", "harvest", "pm"])
        self.method_combo.setCurrentText("rmvpe")
        self.method_combo.currentTextChanged.connect(self._update_dynamic_params)
        self.lbl_method = BodyLabel(Trans.get("vc_method", "提取算法 (Method)"))
        m_layout.addWidget(self.lbl_method)
        m_layout.addStretch(1)
        m_layout.addWidget(self.method_combo)
        basic_layout.addLayout(m_layout)
        
        h_layout = QHBoxLayout()
        self.hop_combo = ComboBox()
        self.hop_combo.addItems(["32", "64", "128", "256", "512"])
        self.hop_combo.setCurrentText("128")
        self.hop_combo.currentTextChanged.connect(self._update_dynamic_params)
        self.lbl_hop_size = BodyLabel(Trans.get("vc_hop_size", "提取步长 (Hop Size)"))
        h_layout.addWidget(self.lbl_hop_size)
        h_layout.addStretch(1)
        h_layout.addWidget(self.hop_combo)
        basic_layout.addLayout(h_layout)
        
        basic_layout.addStretch(1)
        
        # Add latency slider in basic card
        self.latency_slider, _ = self._create_slider(basic_layout, Trans.get("vc_latency", "算法延迟 (Latency / Chunk Size)"), 5, 30, 15)
        
        tuning_layout.addWidget(self.basic_card)
        
        # Card 2: Timbre Protection
        self.adv_card = CardWidget()
        adv_layout = QVBoxLayout(self.adv_card)
        adv_layout.setContentsMargins(24, 20, 24, 20)
        adv_layout.setSpacing(20)
        
        adv_header = QHBoxLayout()
        self.adv_title = SubtitleLabel(Trans.get("vc_adv_title", "音色保护"))
        self.adv_title.setStyleSheet("font-size: 16px;")
        
        self.reset_btn = TransparentToolButton(FIF.SYNC)
        self.reset_btn.setToolTip(Trans.get("preset_restore", "重置所有参数"))
        self.reset_btn.clicked.connect(self.reset_tuning_params)
        
        adv_header.addWidget(self.adv_title)
        adv_header.addStretch(1)
        adv_header.addWidget(self.reset_btn)
        adv_layout.addLayout(adv_header)
        
        self.index_slider, _ = self._create_slider(adv_layout, Trans.get("vc_index_rate", "特征检索率 (Index Rate)"), 0, 100, 75)
        self.protect_slider, _ = self._create_slider(adv_layout, Trans.get("vc_protect", "清辅音保护 (Protect)"), 0, 50, 33)
        self.f0_smooth_slider, _ = self._create_slider(adv_layout, Trans.get("vc_f0_smooth", "音高平滑度 (F0 Median)"), 0, 100, 50) # Increased default to 50 to fix bubbling (气泡声)
        adv_layout.addStretch(1)
        adv_layout.addStretch(1)
        tuning_layout.addWidget(self.adv_card)
        
        self.vBoxLayout.addLayout(tuning_layout)
        
        # Row 2: Post-FX
        fx_layout = QHBoxLayout()
        self.fx_card = CardWidget()
        fx_card_layout = QVBoxLayout(self.fx_card)
        fx_card_layout.setContentsMargins(24, 20, 24, 20)
        fx_card_layout.setSpacing(20)
        
        fx_header = QHBoxLayout()
        self.fx_title = SubtitleLabel(Trans.get("vc_fx_title", "后期效果 (Post-FX)"))
        self.fx_title.setStyleSheet("font-size: 16px;")
        fx_header.addWidget(self.fx_title)
        
        fx_header.addStretch(1)
        
        vst_layout = QHBoxLayout()
        vst_layout.setSpacing(4)
        
        self.vst_btn = PushButton(FIF.SETTING, Trans.get("vc_vst_btn_mount", "挂载高级 VST 插件"))
        self.vst_btn.clicked.connect(self.manage_vst)
        
        self.clear_vst_btn = TransparentToolButton(FIF.CLOSE)
        self.clear_vst_btn.setFixedSize(32, 32)
        self.clear_vst_btn.setToolTip(Trans.get("vc_vst_tooltip_uninstall", "卸载 VST"))
        self.clear_vst_btn.hide()
        self.clear_vst_btn.clicked.connect(self.clear_vst)
        
        vst_layout.addWidget(self.vst_btn)
        vst_layout.addWidget(self.clear_vst_btn)
        fx_header.addLayout(vst_layout)
        
        fx_card_layout.addLayout(fx_header)
        
        self.compressor_slider, _ = self._create_slider(fx_card_layout, Trans.get("vc_compressor", "声音压缩 (Compressor)"), 0, 100, 0)
        self.reverb_slider, _ = self._create_slider(fx_card_layout, Trans.get("vc_reverb", "空间混响 (Reverb)"), 0, 100, 0)
        self.deesser_slider, _ = self._create_slider(fx_card_layout, Trans.get("vc_deesser", "齿音消除 (De-esser)"), 0, 100, 0)
        
        fx_card_layout.addStretch(1)
        fx_layout.addWidget(self.fx_card)
        
        self.vBoxLayout.addLayout(fx_layout)
        
        # Interlock
        def on_method_changed(text):
            self.hop_combo.setEnabled(text in ["rmvpe", "crepe"])
        self.method_combo.currentTextChanged.connect(on_method_changed)

    def _create_slider(self, parent_layout, name, min_val, max_val, default_val):
        layout = QVBoxLayout()
        layout.setSpacing(4)
        
        header = QHBoxLayout()
        title = BodyLabel(name)
        val_label = BodyLabel(str(default_val))
        val_label.setStyleSheet("font-size: 13px;")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(val_label)
        
        slider = Slider(Qt.Orientation.Horizontal)
        slider.setMinimum(min_val)
        slider.setMaximum(max_val)
        slider.setValue(default_val)
        
        def update_label(val):
            val_label.setText(str(val))
            self._update_dynamic_params()
            
        slider.valueChanged.connect(update_label)
        
        layout.addLayout(header)
        layout.addWidget(slider)
        parent_layout.addLayout(layout)
        return slider, val_label

    def select_custom_index(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择外部 Index 文件", "", "Index Files (*.index)")
        if file_path:
            self.custom_index_path = file_path
            import os
            name = os.path.basename(file_path)
            if len(name) > 15:
                name = name[:12] + "..."
            self.custom_index_label.setText(f"已覆盖: {name}")
            self.custom_index_label.setStyleSheet("color: #009faa; font-weight: bold;")
            self.clear_index_btn.show()

    def clear_custom_index(self):
        self.custom_index_path = ""
        self.vst_path = ""
        self.current_model_tab = "pth"
        self.custom_index_label.setText(Trans.get("default", "默认") + " (与模型同名)" if Trans.CURRENT_LANG == "zh_CN" else "Default (Same name as model)")
        self.custom_index_label.setStyleSheet("color: rgba(0,0,0,0.5); font-weight: normal;")
        self.clear_index_btn.hide()

    def reset_tuning_params(self):
        self.pitch_slider.setValue(0)
        self.index_slider.setValue(75)
        self.rms_slider.setValue(25)
        self.protect_slider.setValue(33)
        self.f0_smooth_slider.setValue(50)
        self.latency_slider.setValue(15)
        self.compressor_slider.setValue(0)
        self.reverb_slider.setValue(0)
        self.deesser_slider.setValue(0)
        self.method_combo.setCurrentText("rmvpe")
        self.hop_combo.setCurrentText("128")
        
        self.save_config()

    def auto_setup_audio(self):
        best_in = ""
        best_out = ""
        
        # Detect virtual cable
        for i in range(self.output_combo.count()):
            text = self.output_combo.itemText(i)
            if "CABLE Input" in text or "VB-Audio Virtual Cable" in text:
                best_out = text
                break
                
        # Detect physical mic
        for i in range(self.input_combo.count()):
            text = self.input_combo.itemText(i)
            # Avoid picking the virtual cable as input!
            if "CABLE" not in text and "Virtual" not in text and "VoiceMeeter" not in text:
                best_in = text
                break
                
        if best_in:
            self.input_combo.setCurrentText(best_in)
        if best_out:
            self.output_combo.setCurrentText(best_out)
            
        # Pop system settings
        import subprocess
        try:
            subprocess.Popen("control mmsys.cpl,,1")
            InfoBar.success(Trans.get("msg_op_success", "操作成功"), "音频路由配置成功，软件已自动检测并映射虚拟音频设备！\n请在弹出的系统窗口中，右键点击【CABLE Output】并设置为【默认设备】和【默认通信设备】即可全局生效！" if Trans.CURRENT_LANG == "zh_CN" else "Audio routing configured successfully!\nPlease right-click 'CABLE Output' in the popup and set it as both 'Default Device' and 'Default Communication Device' to enable it globally.", parent=self, duration=8000)
        except Exception as e:
            logger.error(f"Failed to open mmsys.cpl: {e}")

    def setup_model_list(self):
        self.model_card = CardWidget()
        layout = QVBoxLayout(self.model_card)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)
        
        # --- Active Model Card ---
        self.active_card = QFrame()
        self.active_card.setObjectName("ActiveModelCard")
        # Use ID selector to prevent cascading to child labels
        self.active_card.setStyleSheet("#ActiveModelCard { background-color: rgba(0, 159, 170, 0.05); border: 1px solid rgba(0, 159, 170, 0.2); border-radius: 12px; }")
        
        active_layout = QVBoxLayout(self.active_card)
        active_layout.setContentsMargins(20, 20, 20, 20)
        active_layout.setSpacing(16)
        
        # Header
        header_layout = QHBoxLayout()
        header_icon = IconWidget(FIF.IOT)
        header_icon.setFixedSize(18, 18)
        header_layout.addWidget(header_icon)
        
        self.active_title = SubtitleLabel(Trans.get("vc_active_model", "当前运行模型"))
        self.active_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #009faa;")
        header_layout.addWidget(self.active_title)
        header_layout.addStretch(1)
        
        self.import_btn = PushButton(FIF.DOWNLOAD, Trans.get("vc_import_btn", "导入新模型"))
        self.import_btn.clicked.connect(self.import_local_model)
        header_layout.addWidget(self.import_btn)
        
        active_layout.addLayout(header_layout)
        
        # Separator line
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background-color: rgba(0, 159, 170, 0.1);")
        active_layout.addWidget(sep)
        
        # PTH row
        pth_layout = QHBoxLayout()
        pth_icon = IconWidget(FIF.ROBOT)
        pth_icon.setFixedSize(16, 16)
        pth_layout.addWidget(pth_icon)
        
        self.lbl_active_pth = BodyLabel(Trans.get("vc_pth_status", "音色模型 (PTH): {model}").format(model=Trans.get("vc_unloaded", "未加载")))
        self.lbl_active_pth.setStyleSheet("font-size: 14px;")
        pth_layout.addWidget(self.lbl_active_pth)
        pth_layout.addStretch(1)
        active_layout.addLayout(pth_layout)
        
        # Index row
        index_layout = QHBoxLayout()
        index_icon = IconWidget(FIF.SEARCH)
        index_icon.setFixedSize(16, 16)
        index_layout.addWidget(index_icon)
        
        self.lbl_active_index = BodyLabel(Trans.get("vc_index_status", "特征检索 (Index): {index}").format(index=Trans.get("vc_unloaded", "未加载")))
        self.lbl_active_index.setStyleSheet("font-size: 14px;")
        index_layout.addWidget(self.lbl_active_index)
        index_layout.addStretch(1)
        
        self.btn_clear_index = PushButton(FIF.DELETE, Trans.get("vc_btn_clear_index", "清除检索"))
        self.btn_clear_index.setStyleSheet("padding: 5px 12px 5px 36px;")
        self.btn_clear_index.setFixedSize(110, 32)
        self.btn_clear_index.clicked.connect(self.clear_active_index)
        index_layout.addWidget(self.btn_clear_index)
        active_layout.addLayout(index_layout)
        
        layout.addWidget(self.active_card)
        
        # --- Model Library ---
        self.model_list_title = SubtitleLabel(Trans.get("vc_model_list_title", "可用模型列表 (点击加载)"))
        self.model_list_title.setStyleSheet("font-size: 16px; margin-top: 10px;")
        layout.addWidget(self.model_list_title)
        
        # Scroll area for the elegant list
        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(8)
        self.list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        scroll = ScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.list_container)
        scroll.setMinimumHeight(500) # Prevents nested scroll collapse, shows ~7-8 models
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        
        layout.addWidget(scroll)
        self.vBoxLayout.addWidget(self.model_card, 1) # Give it stretch factor 1
        
        self.model_items = {}

    def refresh_local_models(self):
        # Clear existing
        while self.list_layout.count():
            child = self.list_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self.model_items.clear()
        
        # Auto-create directory and deploy preset models if missing
        os.makedirs(MODEL_DIR, exist_ok=True)
        try:
            from core_commander.core.system_tweaks import SystemTweaksService
            preset_src_dir = SystemTweaksService.get_resource_path(os.path.join("core_commander", "resources", "preset_models"))
            if os.path.exists(preset_src_dir):
                import shutil
                for filename in os.listdir(preset_src_dir):
                    src_file = os.path.join(preset_src_dir, filename)
                    dest_file = os.path.join(MODEL_DIR, filename)
                    if os.path.isfile(src_file) and not os.path.exists(dest_file):
                        shutil.copy2(src_file, dest_file)
        except Exception as e:
            logger.error(f"Failed to deploy preset models: {e}")
            
        self.add_model_btn = PushButton(FIF.ADD, Trans.get("vc_import_model_file", "导入模型文件"))
        self.add_model_btn.clicked.connect(self.import_local_model)
        self.list_layout.addWidget(self.add_model_btn)
        
        import glob
        all_files = glob.glob(os.path.join(MODEL_DIR, "**", "*.pth"), recursive=True) + glob.glob(os.path.join(MODEL_DIR, "**", "*.index"), recursive=True)
        
        for f in all_files:
            file_name = os.path.relpath(f, MODEL_DIR).replace("\\", "/")
            item = ModelListItem(file_name, self)
            item.itemClicked.connect(self._on_model_selected)
            item.deleteClicked.connect(self.delete_model)
            self.list_layout.addWidget(item)
            self.model_items[file_name] = item
            
            # Select if active
            if file_name == self.active_pth or file_name == self.active_index:
                item.set_selected(True)
                
        self.update_active_model_ui()

    def _on_model_selected(self, file_name):
        if file_name.endswith(".pth"):
            self.active_pth = file_name
        elif file_name.endswith(".index"):
            self.active_index = file_name
            
        self.update_active_model_ui()
        
        for name, item in self.model_items.items():
            if name == self.active_pth or name == self.active_index:
                item.set_selected(True)
            else:
                item.set_selected(False)
                
        if self.switch_btn.isChecked():
            if getattr(self, 'engine', None) and self.engine.is_running:
                pth_path = os.path.join(MODEL_DIR, self.active_pth) if self.active_pth else ""
                index_path = os.path.join(MODEL_DIR, self.active_index) if self.active_index else ""
                self.engine.request_hot_swap(pth_path, index_path)
            else:
                self._restart_engine()
            
    def update_active_model_ui(self):
        self.lbl_active_pth.setText(Trans.get("vc_pth_status", "音色模型 (PTH): {model}").format(model=self.active_pth if self.active_pth else Trans.get("vc_unloaded", "未加载")))
        self.lbl_active_index.setText(Trans.get("vc_index_status", "特征检索 (Index): {index}").format(index=self.active_index if self.active_index else Trans.get("vc_unloaded", "未加载")))
        self.save_config()
        
    def clear_active_index(self):
        self.active_index = ""
        self.update_active_model_ui()
        for name, item in self.model_items.items():
            if name.endswith(".index"):
                item.set_selected(False)
        if self.switch_btn.isChecked():
            if getattr(self, 'engine', None) and self.engine.is_running:
                pth_path = os.path.join(MODEL_DIR, self.active_pth) if self.active_pth else ""
                self.engine.request_hot_swap(pth_path, "")
            else:
                self._restart_engine()

    def _restart_engine(self):
        if getattr(self, "is_restarting", False):
            return
        self.is_restarting = True
        self.switch_btn.setChecked(False)
        QTimer.singleShot(600, self._do_restart_engine)

    def _do_restart_engine(self):
        self.switch_btn.setChecked(True)
        self.is_restarting = False

    def refresh_devices(self, force_refresh: bool = False):
        if not HAS_DEPENDENCIES:
            return
            
        # Check if an init thread is already running to prevent concurrent modification of UI and resource leaks
        if hasattr(self, 'init_thread') and self.init_thread is not None:
            try:
                if self.init_thread.isRunning():
                    logger.debug("VoiceChangerPageInitThread is already running, skipping duplicate refresh.")
                    return
            except Exception:  # nosec
                pass
        
        # Placeholders while loading in background
        self.input_combo.setEnabled(False)
        self.output_combo.setEnabled(False)
        self.input_combo.setPlaceholderText("正在获取音频设备列表...")
        self.output_combo.setPlaceholderText("正在获取音频设备列表...")
        self.driver_btn.setEnabled(False)
        self.driver_btn.setText("正在检测驱动状态...")
        
        # Launch async loader thread
        self.init_thread = VoiceChangerPageInitThread(force_refresh=force_refresh, parent=self)
        self.init_thread.finished_signal.connect(self._on_init_completed)
        self.init_thread.finished.connect(self.init_thread.deleteLater)
        self.init_thread.start()

    def _on_init_completed(self, inputs, outputs, is_installed):
        self.input_combo.clear()
        self.input_combo.addItems(inputs)
        
        self.output_combo.clear()
        self.output_combo.addItems(outputs)
        
        self.input_combo.setEnabled(True)
        self.output_combo.setEnabled(True)
        self.input_combo.setPlaceholderText(Trans.get("vc_input_placeholder", "选择输入设备 (麦克风)"))
        self.output_combo.setPlaceholderText(Trans.get("vc_output_placeholder", "选择输出设备 (虚拟音频)"))
        
        # Auto select virtual audio CABLE
        selected_index = -1
        for i in range(self.output_combo.count()):
            if "CABLE" in self.output_combo.itemText(i) or "VB-Audio" in self.output_combo.itemText(i):
                selected_index = i
                break
        if selected_index >= 0:
            self.output_combo.setCurrentIndex(selected_index)
            
        self.driver_btn.setEnabled(True)
        if is_installed:
            self.driver_btn.setText(Trans.get("vc_driver_btn_route", "配置音频路由"))
            self.driver_btn.setIcon(FIF.WIFI)
            self.driver_btn.setVisible(True)
        else:
            self.driver_btn.setText(Trans.get("vc_driver_btn_install", "安装声卡驱动"))
            self.driver_btn.setIcon(FIF.SETTING)
            self.driver_btn.setVisible(True)

    def check_driver_state(self):
        # Kept for backward compatibility, delegate to refresh_devices
        self.refresh_devices(force_refresh=False)

    def trigger_driver_installation(self):
        if is_driver_installed():
            self.auto_setup_audio()
            return
            
        # Check if an installer thread is already running to prevent concurrent installation processes
        if hasattr(self, 'inst_thread') and self.inst_thread is not None:
            try:
                if self.inst_thread.isRunning():
                    logger.debug("InstallerThread is already running, skipping duplicate installation trigger.")
                    return
            except Exception:  # nosec
                pass
                
        self.driver_btn.setEnabled(False)
        self.driver_btn.setText(Trans.get("vc_driver_btn_installing", "安装中..."))
        
        class InstallerThread(QThread):
            finished_signal = Signal(bool)
            def run(self):
                res = install_driver()
                self.finished_signal.emit(res)
                
        self.inst_thread = InstallerThread(self)
        self.inst_thread.finished_signal.connect(self.on_driver_installed)
        self.inst_thread.finished.connect(self.inst_thread.deleteLater)
        self.inst_thread.start()

    def on_driver_installed(self, success):
        self.driver_btn.setEnabled(True)
        if success:
            InfoBar.success("成功", "驱动已安装，正在为您自动配置路由...", parent=self)
            self.refresh_devices(force_refresh=True)
            self.auto_setup_audio()
        else:
            self.refresh_devices(force_refresh=False)
            InfoBar.error("失败", "驱动安装失败", parent=self)

    def manage_vst(self):
        import glob
        import os
        import sys
        if not self.vst_path:
            if getattr(sys, 'frozen', False):
                # PyInstaller runtime
                base_path = os.path.dirname(sys.executable)
                plugins_dir = os.path.join(base_path, "core_commander", "assets", "plugins")
            else:
                # Source code runtime
                base_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
                plugins_dir = os.path.join(base_path, "core_commander", "assets", "plugins")
                
            os.makedirs(plugins_dir, exist_ok=True)
            vst_files = glob.glob(os.path.join(plugins_dir, "*.vst3"))
            
            if not vst_files:
                InfoBar.warning("未找到插件", "请将您的 VST3 插件（如 FabFilter）放入内置目录: assets/plugins/", parent=self, duration=5000)
                return
                
            self.vst_path = vst_files[0]
            self.vst_btn.setText(Trans.get("vc_vst_btn_open", "打开 EQ 面板"))
            self.vst_btn.setIcon(FIF.PALETTE)
            self.vst_btn.setStyleSheet("""
                PushButton {
                    color: #009faa;
                    font-weight: bold;
                    padding: 5px 12px 5px 36px;
                }
            """)
            self.clear_vst_btn.show()
            
            # Hot-load if engine is running
            if getattr(self, 'engine', None) and self.engine.is_running:
                success = self.engine.load_vst_plugin(self.vst_path)
                if success:
                    InfoBar.success("加载成功", f"成功挂载内置插件: {os.path.basename(self.vst_path)}", parent=self)
                    self.engine.show_vst_editor()
                else:
                    InfoBar.error("错误", "内置插件加载失败", parent=self)
                    self.vst_path = ""
                    self.vst_btn.setText(Trans.get("vc_vst_btn_mount", "挂载高级 VST 插件"))
                    self.vst_btn.setIcon(FIF.SETTING)
                    self.vst_btn.setStyleSheet("")
            else:
                InfoBar.success("已预备", f"已选中 {os.path.basename(self.vst_path)}。启动引擎后将自动挂载！", parent=self)
        else:
            if getattr(self, 'engine', None) and self.engine.is_running:
                self.engine.show_vst_editor()
            else:
                InfoBar.warning(Trans.get("msg_op_failed", "提示"), "请先启动引擎，才能打开高级调音面板" if Trans.CURRENT_LANG == "zh_CN" else "Please start the engine first to open the advanced tuner panel.", parent=self)

    def clear_vst(self):
        self.vst_path = ""
        self.vst_btn.setText(Trans.get("vc_vst_btn_mount", "挂载高级 VST 插件"))
        self.vst_btn.setIcon(FIF.SETTING)
        self.vst_btn.setStyleSheet("")
        self.clear_vst_btn.hide()
        
        if getattr(self, 'engine', None) and self.engine.is_running:
            self.engine.load_vst_plugin("")
            InfoBar.success("已卸载", "高级 EQ 效果已关闭并卸载", parent=self)

    def import_local_model(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择模型文件", "", "RVC Models (*.pth *.index)")
        if not file_path:
            return
            
        file_name = os.path.basename(file_path)
        os.makedirs(MODEL_DIR, exist_ok=True)
        
        try:
            shutil.copy2(file_path, os.path.join(MODEL_DIR, file_name))
            InfoBar.success("成功", f"文件 {file_name} 导入成功！", parent=self)
            self.refresh_local_models()
        except Exception as e:
            InfoBar.error("失败", f"导入失败: {e}", parent=self)

    def delete_model(self, file_name):
        w = MessageBox(
            '删除文件',
            f'确定要永久删除文件 "{file_name}" 吗？此操作不可恢复。',
            self
        )
        if w.exec():
            try:
                target_path = os.path.join(MODEL_DIR, file_name)
                if os.path.exists(target_path):
                    os.remove(target_path)
                
                # If deleted model is currently running, stop the engine and clear state
                if self.active_pth == file_name:
                    self.active_pth = ""
                    self.toggle_voice_changer(False)
                    self.switch_btn.setChecked(False)
                if self.active_index == file_name:
                    self.active_index = ""
                    
                self.update_active_model_ui()
                InfoBar.success("已删除", f"文件 {file_name} 已彻底删除", parent=self)
                self.refresh_local_models()
            except Exception as e:
                InfoBar.error("删除失败", f"无法删除文件: {e}", parent=self)

    def _update_dynamic_params(self, *_):
        if getattr(self, 'engine', None) and self.engine.is_running:
            params = {
                "pitch": self.pitch_slider.value(),
                "index_rate": self.index_slider.value() / 100.0,
                "rms_mix": self.rms_slider.value() / 100.0,
                "protect": self.protect_slider.value() / 100.0,
                "method": self.method_combo.currentText(),
                "f0_smooth": self.f0_smooth_slider.value() / 100.0,
                "hop_size": int(self.hop_combo.currentText()),
                "compressor": getattr(self, 'compressor_slider', None).value() / 100.0 if hasattr(self, 'compressor_slider') else 0.0,
                "reverb": getattr(self, 'reverb_slider', None).value() / 100.0 if hasattr(self, 'reverb_slider') else 0.0,
                "deesser": getattr(self, 'deesser_slider', None).value() / 100.0 if hasattr(self, 'deesser_slider') else 0.0,
            }
            if hasattr(self.engine, 'update_params'):
                self.engine.update_params(params)
            else:
                self.engine.tune_params.update(params)
            
        self.save_config()

    def _on_device_changed(self, index=None):
        self.save_config()
        if getattr(self, 'engine', None) and self.engine.is_running:
            self.toggle_voice_changer(False)
            self.switch_btn.setChecked(False)
            InfoBar.warning("设备已更改", "检测到音频设备改变，引擎已自动停止，请重新启动。", parent=self)

    def _on_engine_finished(self, engine_instance):
        if not engine_instance:
            return
        try:
            try:
                import shiboken6 as shiboken
            except ImportError:
                import shiboken
            if shiboken.isValid(engine_instance):
                if not engine_instance.isRunning():
                    from core_commander.core.voice_changer.engine import VoiceChangerEngine
                    if hasattr(VoiceChangerEngine, '_active_instances'):
                        VoiceChangerEngine._active_instances.discard(engine_instance)
                    if engine_instance in self.active_old_engines:
                        self.active_old_engines.remove(engine_instance)
                    engine_instance.deleteLater()
            else:
                from core_commander.core.voice_changer.engine import VoiceChangerEngine
                if hasattr(VoiceChangerEngine, '_active_instances'):
                    VoiceChangerEngine._active_instances.discard(engine_instance)
                if engine_instance in self.active_old_engines:
                    self.active_old_engines.remove(engine_instance)
        except Exception as e:
            logger.error(f"Error cleaning up engine instance: {e}")

    def _on_engine_status_changed(self, status):
        self.engine_status.setText(f"状态: {status}")

    def _on_current_engine_finished(self):
        sender_engine = self.sender()
        if sender_engine:
            self._on_engine_finished(sender_engine)

    def toggle_voice_changer(self, checked):
        # Clean up completed threads from active_old_engines list safely checking Shiboken C++ state
        valid_engines = []
        for eng in self.active_old_engines:
            try:
                try:
                    import shiboken6 as shiboken
                except ImportError:
                    import shiboken
                if shiboken.isValid(eng) and eng.isRunning():
                    valid_engines.append(eng)
            except Exception:
                try:
                    if hasattr(eng, 'isRunning') and eng.isRunning():
                        valid_engines.append(eng)
                except RuntimeError:
                    # Ignore libshiboken already deleted C++ object error
                    continue
        self.active_old_engines = valid_engines
        
        import time
        if checked:
            # Cooldown check: ensure at least 800ms between stop and start to let OS audio drivers release device
            elapsed = time.time() - getattr(self, 'last_stop_time', 0.0)
            if elapsed < 0.8:
                wait_ms = int((0.8 - elapsed) * 1000)
                QTimer.singleShot(max(10, wait_ms), lambda: self.toggle_voice_changer(True) if self.switch_btn.isChecked() else None)
                return

            # If there is still a running old engine thread, wait for it to finish and release PortAudio devices
            if self.active_old_engines:
                QTimer.singleShot(100, lambda: self.toggle_voice_changer(True) if self.switch_btn.isChecked() else None)
                return
            is_loading = getattr(self, '_is_loading_config', False)
            from core_commander.ui.activation_dialog import require_license
            if not require_license(self, "AI变声器引擎", silent=is_loading):
                self.switch_btn.blockSignals(True)
                self.switch_btn.setChecked(False)
                self.switch_btn.blockSignals(False)
                return
            if is_loading:
                return
        if not checked:
            self.last_stop_time = time.time()
            if hasattr(self, 'engine') and self.engine is not None:
                self.engine.stop()
                # 移除 self.engine.wait()，防止 CUDA 垃圾回收或音频流关闭时卡死主界面
                # 让旧线程在后台自行销毁，并将其加入跟踪列表
                old_engine = self.engine
                self.active_old_engines.append(old_engine)
                self.engine = None
            self.engine_status.setText(Trans.get("vc_status_inactive", "状态: 未激活"))
            self.switch_btn.setText(Trans.get("vc_switch_btn_start", "启动引擎"))
            self.switch_btn.setIcon(FIF.PLAY_SOLID)
            return

        pth_path = os.path.join(MODEL_DIR, self.active_pth) if self.active_pth else ""
        index_path = os.path.join(MODEL_DIR, self.active_index) if self.active_index else ""
        
        if not pth_path or not os.path.exists(pth_path):
            InfoBar.info("原声模式", "未选择模型，已启用原声直通模式 (支持 EQ/VST 效果)" if Trans.CURRENT_LANG == "zh_CN" else "Direct audio mode enabled (Supports EQ/VST)", parent=self)

        input_dev = self.input_combo.currentText()
        output_dev = self.output_combo.currentText()
        
        if not input_dev or not output_dev:
            InfoBar.warning(Trans.get("msg_op_failed", "错误"), "请选择音频输入输出设备" if Trans.CURRENT_LANG == "zh_CN" else "Please select both audio input and output devices.", parent=self)
            self.switch_btn.setChecked(False)
            return

        self.engine_status.setText(Trans.get("vc_status_initializing", "状态: 初始化中..."))
        self.switch_btn.setText(Trans.get("vc_switch_btn_stop", "停止引擎"))
        self.switch_btn.setIcon(FIF.PAUSE_BOLD)
        
        tune_params = {
            "pitch": self.pitch_slider.value(),
            "index_rate": self.index_slider.value() / 100.0,
            "rms_mix": self.rms_slider.value() / 100.0,
            "protect": self.protect_slider.value() / 100.0,
            "method": self.method_combo.currentText(),
            "f0_smooth": self.f0_smooth_slider.value() / 100.0,
            "hop_size": int(self.hop_combo.currentText()),
            "compressor": getattr(self, 'compressor_slider', None).value() / 100.0 if hasattr(self, 'compressor_slider') else 0.0,
            "reverb": getattr(self, 'reverb_slider', None).value() / 100.0 if hasattr(self, 'reverb_slider') else 0.0,
            "deesser": getattr(self, 'deesser_slider', None).value() / 100.0 if hasattr(self, 'deesser_slider') else 0.0,
            "latency_ms": self.latency_slider.value() * 10,
            "vst_path": self.vst_path
        }
        # Check if RVC base models are required and exist
        if pth_path:
            import importlib.util
            spec = importlib.util.find_spec("rvc_python")
            if spec and spec.submodule_search_locations:
                rvc_dir = spec.submodule_search_locations[0]
                base_model_dir = os.path.normpath(os.path.join(rvc_dir, "base_model"))
                hubert_path = os.path.join(base_model_dir, "hubert_base.pt")
                rmvpe_path = os.path.join(base_model_dir, "rmvpe.pt")
                
                if not os.path.exists(hubert_path) or not os.path.exists(rmvpe_path):
                    dialog = ModelDownloadDialog(self)
                    dialog.start_download(base_model_dir)
                    dialog.exec()
                    if not dialog.is_success:
                        self.switch_btn.blockSignals(True)
                        self.switch_btn.setChecked(False)
                        self.switch_btn.blockSignals(False)
                        self.engine_status.setText(Trans.get("vc_status_inactive", "状态: 未激活"))
                        self.switch_btn.setText(Trans.get("vc_switch_btn_start", "启动引擎"))
                        self.switch_btn.setIcon(FIF.PLAY_SOLID)
                        return

        try:
            self.engine = VoiceChangerEngine(
                pth_path=pth_path,
                index_path=index_path if os.path.exists(index_path) else "",
                input_device=self.input_combo.currentText(),
                output_device=self.output_combo.currentText(),
                tune_params=tune_params
            )
            self.engine.status_signal.connect(self._on_engine_status_changed)
            self.engine.audio_error.connect(self._on_engine_error)
            self.engine.finished.connect(self._on_current_engine_finished)
            self.engine.start()
            self.switch_btn.setText(Trans.get("vc_switch_btn_stop", "停止引擎"))
            self.switch_btn.setIcon(FIF.PAUSE_BOLD)
        except Exception as e:
            self._on_engine_error(str(e))
            self.switch_btn.setChecked(False)
            self.switch_btn.setText(Trans.get("vc_switch_btn_start", "启动引擎"))
            self.switch_btn.setIcon(FIF.PLAY_SOLID)

    def save_config(self):
        try:
            os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
            config = {
                "input_device": self.input_combo.currentText(),
                "output_device": self.output_combo.currentText(),
                "pitch": self.pitch_slider.value(),
                "index_rate": self.index_slider.value(),
                "rms_mix": self.rms_slider.value(),
                "protect": self.protect_slider.value(),
                "f0_smooth": self.f0_smooth_slider.value(),
                "latency_ms": self.latency_slider.value(),
                "method": self.method_combo.currentText(),
                "hop_size": self.hop_combo.currentText(),
                "compressor": getattr(self, 'compressor_slider', None).value() if hasattr(self, 'compressor_slider') else 0,
                "reverb": getattr(self, 'reverb_slider', None).value() if hasattr(self, 'reverb_slider') else 0,
                "deesser": getattr(self, 'deesser_slider', None).value() if hasattr(self, 'deesser_slider') else 0,
                "vst_path": self.vst_path,
                "active_pth": self.active_pth,
                "active_index": self.active_index
            }
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logger.error(f"保存配置失败: {e}")

    def load_config(self):
        self._is_loading_config = True
        if not os.path.exists(CONFIG_FILE):
            self._is_loading_config = False
            return
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            # Restore inputs/outputs without triggering events
            self.input_combo.blockSignals(True)
            self.output_combo.blockSignals(True)
            if config.get("input_device") and self.input_combo.findText(config["input_device"]) >= 0:
                self.input_combo.setCurrentText(config["input_device"])
            if config.get("output_device") and self.output_combo.findText(config["output_device"]) >= 0:
                self.output_combo.setCurrentText(config["output_device"])
            self.input_combo.blockSignals(False)
            self.output_combo.blockSignals(False)
            
            # Restore sliders
            self.pitch_slider.setValue(config.get("pitch", 0))
            self.index_slider.setValue(config.get("index_rate", 75))
            self.rms_slider.setValue(config.get("rms_mix", 25))
            self.protect_slider.setValue(config.get("protect", 33))
            self.f0_smooth_slider.setValue(config.get("f0_smooth", 30))
            self.latency_slider.setValue(config.get("latency_ms", 15))
            
            # Restore combos & switches
            if config.get("method"): self.method_combo.setCurrentText(config["method"])
            if config.get("hop_size"): self.hop_combo.setCurrentText(config["hop_size"])
            if hasattr(self, 'compressor_slider'):
                self.compressor_slider.setValue(config.get("compressor", 0))
            if hasattr(self, 'reverb_slider'):
                self.reverb_slider.setValue(config.get("reverb", 0))
            if hasattr(self, 'deesser_slider'):
                # Handle old boolean deesser config migrating to slider 0-100
                old_deesser = config.get("deesser", 0)
                if isinstance(old_deesser, bool):
                    old_deesser = 0 # Default to 0 instead of 100 if it was just a switch before to avoid shock
                self.deesser_slider.setValue(old_deesser)
            
            # Restore VST
            if config.get("vst_path") and os.path.exists(config["vst_path"]):
                self.vst_path = config["vst_path"]
                self.vst_btn.setText(os.path.basename(self.vst_path))
                self.vst_btn.setStyleSheet("""
                PushButton {
                    color: #009faa;
                    border: 1px solid #009faa;
                    background-color: rgba(0, 159, 170, 0.1);
                    padding: 5px 12px 5px 36px;
                    font-weight: bold;
                }
            """)
                self.clear_vst_btn.show()
                
            # Restore active models
            if config.get("active_pth"):
                self._on_model_selected(config["active_pth"])
            if config.get("active_index"):
                self._on_model_selected(config["active_index"])
                
        except Exception as e:
            logger.error(f"加载配置失败: {e}")
        finally:
            self._is_loading_config = False

    def _on_engine_error(self, err):
        InfoBar.error("异常", err, parent=self)
        self.switch_btn.setChecked(False)
        self.engine_status.setText(Trans.get("vc_status_stopped", "状态: 已停止"))

    def retranslate_ui(self):
        # Update placeholders
        if self.input_combo.currentIndex() == -1:
            self.input_combo.setPlaceholderText(Trans.get("vc_input_placeholder", "选择输入设备 (麦克风)"))
        if self.output_combo.currentIndex() == -1:
            self.output_combo.setPlaceholderText(Trans.get("vc_output_placeholder", "选择输出设备 (虚拟音频)"))
        
        # Driver button text
        if is_driver_installed():
            self.driver_btn.setText(Trans.get("vc_driver_btn_route", "配置音频路由"))
        else:
            self.driver_btn.setText(Trans.get("vc_driver_btn_install", "安装声卡驱动"))
            
        # Switch button text
        if self.switch_btn.isChecked():
            self.switch_btn.setText(Trans.get("vc_switch_btn_stop", "停止引擎"))
            self.switch_btn.setIcon(FIF.PAUSE_SOLID)
            self.engine_status.setText(Trans.get("vc_status_ready", "状态: 就绪"))
        else:
            self.switch_btn.setText(Trans.get("vc_switch_btn_start", "启动引擎"))
            self.switch_btn.setIcon(FIF.PLAY_SOLID)
            self.engine_status.setText(Trans.get("vc_status_inactive", "状态: 未激活"))
            
        # Title/Headers
        if hasattr(self, 'basic_title'):
            self.basic_title.setText(Trans.get("vc_basic_acoustics", "基础声学"))
        if hasattr(self, 'adv_title'):
            self.adv_title.setText(Trans.get("vc_adv_title", "音色保护"))
        if hasattr(self, 'fx_title'):
            self.fx_title.setText(Trans.get("vc_fx_title", "后期效果 (Post-FX)"))
        if hasattr(self, 'active_title'):
            self.active_title.setText(Trans.get("vc_active_model", "当前运行模型"))
        if hasattr(self, 'model_list_title'):
            self.model_list_title.setText(Trans.get("vc_model_list_title", "可用模型列表 (点击加载)"))
        if hasattr(self, 'add_model_btn'):
            self.add_model_btn.setText(Trans.get("vc_import_model_file", "导入模型文件"))
            
        # Labels
        if hasattr(self, 'lbl_method'):
            self.lbl_method.setText(Trans.get("vc_method", "提取算法 (Method)"))
        if hasattr(self, 'lbl_hop_size'):
            self.lbl_hop_size.setText(Trans.get("vc_hop_size", "提取步长 (Hop Size)"))
            
        # Refresh current active labels
        self.lbl_active_pth.setText(Trans.get("vc_pth_status", "音色模型 (PTH): {model}").format(model=self.active_pth if self.active_pth else Trans.get("vc_unloaded", "未加载")))
        self.lbl_active_index.setText(Trans.get("vc_index_status", "特征检索 (Index): {index}").format(index=self.active_index if self.active_index else Trans.get("vc_unloaded", "未加载")))
        self.btn_clear_index.setText(Trans.get("vc_btn_clear_index", "清除检索"))
        self.import_btn.setText(Trans.get("vc_import_btn", "导入新模型"))
        
        # VST button
        if self.vst_path:
            self.vst_btn.setText(Trans.get("vc_vst_btn_open", "打开 EQ 面板"))
        else:
            self.vst_btn.setText(Trans.get("vc_vst_btn_mount", "挂载高级 VST 插件"))
