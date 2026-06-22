# -*- coding: utf-8 -*-
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFrame
from qfluentwidgets import (
    ScrollArea, BodyLabel, TitleLabel, SubtitleLabel, CaptionLabel,
    CardWidget, Slider, SwitchButton, PrimaryPushButton, PushButton,
    InfoBar, InfoBarPosition, IconWidget, FluentIcon as FIF, MessageBox,
    isDarkTheme
)
from core_commander.core.gpu_oc import GpuOverclockService
from core_commander.utils.i18n import Trans
from core_commander.utils.logger import logger

# Professional descriptions for the tuning sliders
SLIDER_DESCS = {
    "zh_CN": {
        "core": "调整核心工作频率的偏移量。向 GPU 的电压频率曲线（V-F Curve）应用偏置，使核心在相同电压点下运行在更高/更低的频率。",
        "mem": "调整显存（VRAM）工作频率的偏移量。提高显存频率能显著增加显存带宽，提升高分辨率和高带宽负载场景下的运行性能。",
        "power": "控制 GPU 的最大可持续运行功功耗限制（Watts）。调高功耗墙可放宽供电预算，防止 GPU 在高负载下因触发功耗限制而降频。最大上限由显卡 vBIOS 物理锁定。",
        "temp": "设置 GPU 的核心温度保护阈值。当核心温度接近此阈值时，GPU 会自动降频以降低热量。温度墙通常与功耗墙协同限制工作。",
        "voltage": "电压补偿并非直接强加绝对物理电压，而是解锁 GPU 内部 vBIOS Boost 曲线中更高电压频率步长等级（Voltage Steps）。设置为 100% 允许 GPU 在温度和功耗许可下自动申请最高 vBIOS 预设电压以最大程度优化频率上限。"
    },
    "en_US": {
        "core": "Adjusts the core frequency offset. Applies a bias to the GPU's Voltage-Frequency (V-F) curve, allowing the core to run at higher/lower frequencies at identical voltage steps.",
        "mem": "Adjusts the video memory (VRAM) frequency offset. Increasing memory clocks yields higher memory bandwidth, boosting performance under high resolutions and bandwidth-heavy tasks.",
        "power": "Controls the maximum sustainable power consumption of the GPU in Watts. Raising the power limit provides higher power budget to prevent power-limit throttling under heavy loads. The maximum limit is physically restricted by the vBIOS.",
        "temp": "Sets the thermal throttling threshold of the GPU. When the core temperature approaches this limit, the GPU will downclock to maintain safe temperatures. This target is dynamically linked with the Power Limit.",
        "voltage": "Voltage Offset does not directly force-feed physical voltage. Instead, it unlocks higher voltage-frequency steps (Voltage Steps) on the internal vBIOS Boost curve. Setting this to 100% allows the GPU to request the maximum pre-defined vBIOS voltage under safe thermal/power conditions."
    }
}

class TelemetryCard(CardWidget):
    """
    Compact, information-dense widget displaying real-time telemetry from GPU sensors.
    """
    def __init__(self, title: str, icon_fluent, parent=None):
        super().__init__(parent)
        self.setFixedHeight(76)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(12)
        
        # Left side: Icon
        self.icon_widget = IconWidget(icon_fluent, self)
        self.icon_widget.setFixedSize(26, 26)
        self.icon_widget.setStyleSheet("color: #0078D4;")
        layout.addWidget(self.icon_widget)
        
        # Right side: Text fields
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        text_layout.setContentsMargins(0, 0, 0, 0)
        
        self.lbl_title = CaptionLabel(title, self)
        self.lbl_title.setStyleSheet("font-weight: 500; color: gray;")
        
        self.lbl_value = BodyLabel("--", self)
        self.lbl_value.setStyleSheet("font-size: 16px; font-weight: bold;")
        
        self.lbl_sub = CaptionLabel("--", self)
        self.lbl_sub.setStyleSheet("color: gray; font-size: 11px;")
        
        text_layout.addWidget(self.lbl_title)
        text_layout.addWidget(self.lbl_value)
        text_layout.addWidget(self.lbl_sub)
        
        layout.addLayout(text_layout)
        layout.addStretch(1)

    def update_metrics(self, value_str: str, sub_str: str):
        self.lbl_value.setText(value_str)
        self.lbl_sub.setText(sub_str)


class GpuOverclockPage(QWidget):
    """
    UI Page for GPU Overclocking and performance tuning.
    Reorganized with a dense telemetry dashboard and a sticky action bottom bar.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.setObjectName("GpuOverclockPage")
        
        # Main vertical layout: ScrollArea at top/middle, Sticky Bar at bottom
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)
        
        # 1. Scroll Area for settings
        self.scroll_area = ScrollArea(self)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setViewportMargins(0, 0, 0, 0)
        self.scroll_area.setStyleSheet("QScrollArea { background-color: transparent; border: none; }")
        self.scroll_area.setWidgetResizable(True)
        
        self.view = QWidget()
        self.view.setObjectName("GpuOverclockPageView")
        self.view.setStyleSheet("#GpuOverclockPageView { background-color: transparent; }")
        self.scroll_area.setWidget(self.view)
        
        self.vBoxLayout = QVBoxLayout(self.view)
        self.vBoxLayout.setContentsMargins(36, 36, 36, 24)
        self.vBoxLayout.setSpacing(24)
        
        self.main_layout.addWidget(self.scroll_area)
        
        self.settings = self.parent_window.settings if parent else None
        self.gpu_info = GpuOverclockService.get_gpu_oc_info()
        
        self.init_ui()
        
        # Monitor timer
        self.monitor_timer = QTimer(self)
        self.monitor_timer.timeout.connect(self.refresh_monitor_stats)

    def init_ui(self):
        # 1. Header Section
        self.title_label = TitleLabel(Trans.get("gpu_oc_title", "GPU 硬件超频与性能微调"), self)
        self.desc_label = BodyLabel(
            Trans.get("gpu_oc_desc", "通过图形接口底层 API 调整显卡核心与显存频率偏移量、功耗限制及电压偏置，优化图形处理器计算性能。"),
            self
        )
        self.desc_label.setStyleSheet("color: rgba(0,0,0,0.6); font-size: 14px;")
        
        self.vBoxLayout.addWidget(self.title_label)
        self.vBoxLayout.addWidget(self.desc_label)
        
        if not self.gpu_info.get("supported", False):
            # Not Supported Card
            self.unsupported_card = CardWidget(self)
            card_layout = QHBoxLayout(self.unsupported_card)
            card_layout.setContentsMargins(24, 24, 24, 24)
            card_layout.setSpacing(16)
            
            icon = IconWidget(FIF.INFO, self)
            icon.setFixedSize(32, 32)
            card_layout.addWidget(icon)
            
            msg = BodyLabel(
                Trans.get("gpu_oc_not_supported", "当前系统未检测到支持的 NVIDIA 显卡或 NVAPI/NVML 驱动程序接口加载失败。"),
                self
            )
            msg.setStyleSheet("font-size: 15px; font-weight: bold; color: rgba(0,0,0,0.6);")
            card_layout.addWidget(msg)
            card_layout.addStretch(1)
            
            self.vBoxLayout.addWidget(self.unsupported_card)
            return

        # 2. Safety Disclaimer Card
        self.warning_card = CardWidget(self)
        self.warning_card.setStyleSheet(
            "CardWidget { border: 1px solid rgba(255, 165, 0, 0.3); background-color: rgba(255, 165, 0, 0.05); border-radius: 8px; }"
        )
        warning_layout = QVBoxLayout(self.warning_card)
        warning_layout.setContentsMargins(20, 16, 20, 16)
        warning_layout.setSpacing(6)
        
        warn_header = QHBoxLayout()
        warn_icon = IconWidget(FIF.INFO, self)
        warn_icon.setFixedSize(18, 18)
        warn_icon.setStyleSheet("color: #FFA500;")
        warn_header.addWidget(warn_icon)
        
        warn_title = SubtitleLabel(Trans.get("gpu_oc_warning_title", "安全警示与超频免责声明"), self)
        warn_title.setStyleSheet("font-size: 14px; font-weight: bold; color: #D97706;")
        warn_header.addWidget(warn_title)
        warn_header.addStretch(1)
        warning_layout.addLayout(warn_header)
        
        warn_desc = BodyLabel(
            Trans.get("gpu_oc_warning_desc", "警告：超频可能会导致系统不稳、蓝屏、黑屏、甚至硬件寿命缩短。现代 GPU 具有严格的 vBIOS 和硬件级别保护，电压与功耗限制通常被物理锁定，软件无法突破硬件最高电压。开启此功能前请确保已了解相关风险。"),
            self
        )
        warn_desc.setWordWrap(True)
        warn_desc.setStyleSheet("color: #78350F; font-size: 12px; line-height: 1.4;")
        warning_layout.addWidget(warn_desc)
        
        self.vBoxLayout.addWidget(self.warning_card)
        
        # 3. Dense Telemetry Monitor Dashboard Card
        self.monitor_card = CardWidget(self)
        monitor_layout = QVBoxLayout(self.monitor_card)
        monitor_layout.setContentsMargins(24, 20, 24, 20)
        monitor_layout.setSpacing(12)
        
        gpu_name = self.gpu_info.get("gpu_name", "NVIDIA GPU")
        self.monitor_title = SubtitleLabel(f"{Trans.get('gpu_oc_monitor_title', 'GPU 实时监控与状态面板')} - {gpu_name}", self)
        monitor_layout.addWidget(self.monitor_title)
        
        sep = QFrame(self)
        sep.setFixedHeight(1)
        sep.setStyleSheet("background-color: rgba(0,0,0,0.06);")
        monitor_layout.addWidget(sep)
        
        # 4x2 Telemetry Grid
        self.monitor_grid = QGridLayout()
        self.monitor_grid.setSpacing(12)
        self.monitor_grid.setContentsMargins(0, 4, 0, 4)
        
        # Create Telemetry Cards
        self.card_core = TelemetryCard("GPU 核心频率 (Core Clock)", FIF.DEVELOPER_TOOLS, self)
        self.card_mem = TelemetryCard("GPU 显存频率 (Memory Clock)", FIF.TILES, self)
        self.card_temp = TelemetryCard("GPU 核心温度 (Temperature)", FIF.INFO, self)
        self.card_power = TelemetryCard("GPU 实时功耗 (Power Draw)", FIF.LEAF, self)
        
        self.card_gpu_util = TelemetryCard("GPU 核心负载 (GPU Load)", FIF.SPEED_HIGH, self)
        self.card_vram = TelemetryCard("GPU 显存占用 (VRAM Usage)", FIF.FOLDER, self)
        self.card_fan = TelemetryCard("散热风扇转速 (Fan Speed)", FIF.SYNC, self)
        self.card_pcie = TelemetryCard("PCIe 物理总线 (Bus Interface)", FIF.LINK, self)
        
        # Grid layout placement
        self.monitor_grid.addWidget(self.card_core, 0, 0)
        self.monitor_grid.addWidget(self.card_mem, 0, 1)
        self.monitor_grid.addWidget(self.card_temp, 1, 0)
        self.monitor_grid.addWidget(self.card_power, 1, 1)
        
        self.monitor_grid.addWidget(self.card_gpu_util, 2, 0)
        self.monitor_grid.addWidget(self.card_vram, 2, 1)
        self.monitor_grid.addWidget(self.card_fan, 3, 0)
        self.monitor_grid.addWidget(self.card_pcie, 3, 1)
        
        monitor_layout.addLayout(self.monitor_grid)
        self.vBoxLayout.addWidget(self.monitor_card)
        
        # 4. Tuning Control Sliders Panel
        self.tuning_card = CardWidget(self)
        tuning_layout = QVBoxLayout(self.tuning_card)
        tuning_layout.setContentsMargins(24, 24, 24, 24)
        tuning_layout.setSpacing(24)
        
        lang = Trans.CURRENT_LANG if Trans.CURRENT_LANG in SLIDER_DESCS else "en_US"
        
        # Core Clock Offset Slider
        self.slider_core, self.lbl_val_core = self._create_slider_row(
            tuning_layout,
            Trans.get("gpu_oc_core_offset", "GPU 核心频率偏移 (Core Clock Offset)"),
            -200, 250,
            self.settings.gpu_core_offset if self.settings else 0,
            suffix=" MHz",
            desc=SLIDER_DESCS[lang]["core"]
        )
        
        # Memory Clock Offset Slider
        self.slider_mem, self.lbl_val_mem = self._create_slider_row(
            tuning_layout,
            Trans.get("gpu_oc_mem_offset", "GPU 显存频率偏移 (Memory Clock Offset)"),
            -500, 1000,
            self.settings.gpu_mem_offset if self.settings else 0,
            suffix=" MHz",
            desc=SLIDER_DESCS[lang]["mem"]
        )
        
        # Power Limit Slider
        power_default = self.gpu_info.get("power_default_w", 0)
        power_min = self.gpu_info.get("power_min_w", 0)
        power_max = self.gpu_info.get("power_max_w", 0)
        if power_default > 0:
            self.min_power_pct = int((power_min / power_default) * 100)
            self.max_power_pct = int((power_max / power_default) * 100)
        else:
            self.min_power_pct = 70
            self.max_power_pct = 120
            
        initial_power_pct = self.settings.gpu_power_limit if self.settings else 100.0
        self.slider_power, self.lbl_val_power = self._create_slider_row(
            tuning_layout,
            Trans.get("gpu_oc_power_limit", "功耗墙限制 (Power Limit)"),
            self.min_power_pct, self.max_power_pct,
            int(initial_power_pct),
            suffix=" %",
            desc=SLIDER_DESCS[lang]["power"]
        )
        
        # Temperature Limit Slider
        temp_min = self.gpu_info.get("temp_min", 65)
        temp_max = self.gpu_info.get("temp_max", 90)
        initial_temp = self.settings.gpu_temp_limit if self.settings else 83
        self.slider_temp, self.lbl_val_temp = self._create_slider_row(
            tuning_layout,
            Trans.get("gpu_oc_temp_limit", "温度墙限制 (Temp Limit)"),
            temp_min, temp_max,
            initial_temp,
            suffix=" °C",
            desc=SLIDER_DESCS[lang]["temp"]
        )
        
        # Voltage Offset Slider
        initial_voltage = self.settings.gpu_voltage if self.settings else 0
        self.slider_voltage, self.lbl_val_voltage = self._create_slider_row(
            tuning_layout,
            Trans.get("gpu_oc_voltage", "电压补偿百分比 (Voltage Offset)"),
            0, 100,
            initial_voltage,
            suffix=" %",
            desc=SLIDER_DESCS[lang]["voltage"]
        )
        
        self.vBoxLayout.addWidget(self.tuning_card)
        
        # 5. Startup Auto-Apply Configurations Card
        self.config_card = CardWidget(self)
        config_layout = QHBoxLayout(self.config_card)
        config_layout.setContentsMargins(24, 20, 24, 20)
        config_layout.setSpacing(16)
        
        startup_text_layout = QVBoxLayout()
        startup_text_layout.setSpacing(4)
        self.lbl_startup_title = BodyLabel(Trans.get("gpu_oc_startup_apply", "开机自动应用此超频配置 (不推荐)"), self)
        self.lbl_startup_title.setStyleSheet("font-weight: bold;")
        self.lbl_startup_desc = CaptionLabel(
            "提示：此配置将在 Core Commander 后台服务启动时自动加载。如系统遇超频崩盘，开机可能再次触发蓝屏。建议手动应用测试稳定性后再考虑启用。" if Trans.CURRENT_LANG == "zh_CN"
            else "Tip: Profile applies on Core Commander start. Unstable profiles may cause boot crashes. Test stability thoroughly first.",
            self
        )
        startup_text_layout.addWidget(self.lbl_startup_title)
        startup_text_layout.addWidget(self.lbl_startup_desc)
        config_layout.addLayout(startup_text_layout)
        config_layout.addStretch(1)
        
        self.switch_startup = SwitchButton(self)
        self.switch_startup.setChecked(self.settings.gpu_apply_on_startup if self.settings else False)
        self.switch_startup.checkedChanged.connect(self.on_startup_switch_changed)
        config_layout.addWidget(self.switch_startup)
        
        self.vBoxLayout.addWidget(self.config_card)
        
        # 6. Sticky Action Control Bottom Bar (Never clipped, stays on top of viewport)
        self.bottom_bar = QFrame(self)
        self.bottom_bar.setObjectName("GpuOverclockBottomBar")
        
        # Set colors matching current light/dark theme
        is_dark = isDarkTheme()
        bg = "rgba(32, 32, 32, 0.96)" if is_dark else "rgba(248, 248, 248, 0.96)"
        border = "rgba(255, 255, 255, 0.08)" if is_dark else "rgba(0, 0, 0, 0.08)"
        self.bottom_bar.setStyleSheet(
            f"QFrame#GpuOverclockBottomBar {{ "
            f"  background-color: {bg}; "
            f"  border-top: 1px solid {border}; "
            f"}}"
        )
        
        self.bottom_layout = QHBoxLayout(self.bottom_bar)
        self.bottom_layout.setContentsMargins(36, 16, 36, 16)
        self.bottom_layout.setSpacing(16)
        
        self.btn_apply = PrimaryPushButton(FIF.COMPLETED, Trans.get("gpu_oc_apply_btn", "应用超频配置 (Apply)"), self)
        self.btn_apply.setFixedHeight(40)
        self.btn_apply.setFixedWidth(220)
        self.btn_apply.clicked.connect(self.apply_tuning_profile)
        
        self.btn_restore = PushButton(FIF.HISTORY, Trans.get("gpu_oc_restore_btn", "恢复默认参数 (Restore Defaults)"), self)
        self.btn_restore.setFixedHeight(40)
        self.btn_restore.setFixedWidth(220)
        self.btn_restore.clicked.connect(self.restore_tuning_defaults)
        
        self.bottom_layout.addWidget(self.btn_apply)
        self.bottom_layout.addWidget(self.btn_restore)
        self.bottom_layout.addStretch(1)
        
        self.main_layout.addWidget(self.bottom_bar)
        
        # Update telemetry immediately on boot
        self.refresh_monitor_stats()

    def _create_slider_row(self, parent_layout, name, min_val, max_val, default_val, suffix="", desc=""):
        row_widget = QWidget(self)
        layout = QVBoxLayout(row_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        
        header = QHBoxLayout()
        title = BodyLabel(name, self)
        title.setStyleSheet("font-weight: bold;")
        
        formatted_val = f"+{default_val}" if default_val > 0 and suffix == " MHz" else str(default_val)
        val_label = BodyLabel(f"{formatted_val}{suffix}", self)
        val_label.setStyleSheet("color: rgba(128,128,128,0.8); font-weight: bold;")
        
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(val_label)
        
        slider = Slider(Qt.Orientation.Horizontal, self)
        slider.setMinimum(min_val)
        slider.setMaximum(max_val)
        slider.setValue(default_val)
        
        def update_label(val):
            fmt = f"+{val}" if val > 0 and suffix == " MHz" else str(val)
            val_label.setText(f"{fmt}{suffix}")
            
        slider.valueChanged.connect(update_label)
        
        desc_label = CaptionLabel(desc, self)
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet("color: gray; font-size: 11px; line-height: 1.3;")
        
        layout.addLayout(header)
        layout.addWidget(slider)
        layout.addWidget(desc_label)
        
        parent_layout.addWidget(row_widget)
        return slider, val_label

    def refresh_monitor_stats(self):
        """
        Queries actual values from GPU to display in monitor slots.
        """
        if not self.gpu_info.get("supported", False):
            return
            
        try:
            info = GpuOverclockService.get_gpu_oc_info()
            
            # TDR Detection
            actual_core_offset = info.get("core_offset", 0)
            actual_mem_offset = info.get("mem_offset", 0)
            if GpuOverclockService.overclock_applied and actual_core_offset == 0 and actual_mem_offset == 0:
                logger.warning("GPU Page: TDR detected via monitoring! Reverting UI and settings to default.")
                self.restore_tuning_defaults(show_msg=False)
                InfoBar.warning(
                    title="显卡驱动已重置" if Trans.CURRENT_LANG == "zh_CN" else "GPU Driver Reset",
                    content="检测到显卡驱动因不稳定被系统重置 (TDR)，超频参数已安全还原至默认以防系统崩溃。" if Trans.CURRENT_LANG == "zh_CN"
                    else "GPU driver reset detected (TDR). Overclocking parameters have been safely reverted to default to prevent crash loops.",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=8000,
                    parent=self
                )
                return
            
            # Update title header with name
            gpu_name = info.get("gpu_name", "NVIDIA GPU")
            self.monitor_title.setText(
                f"{Trans.get('gpu_oc_monitor_title', 'GPU 实时监控与状态面板')} - {gpu_name}"
            )
            
            # 1. Core Clock
            live_core = info.get("live_core_clock", 0)
            core_offset = info.get("core_offset", 0)
            base_core = max(0, live_core - core_offset)
            offset_sign = "+" if core_offset >= 0 else ""
            self.card_core.update_metrics(
                f"{live_core} MHz" if live_core > 0 else "N/A",
                f"Base: {base_core} MHz | Offset: {offset_sign}{core_offset} MHz"
            )
            
            # 2. Memory Clock
            live_mem = info.get("live_mem_clock", 0)
            mem_offset = info.get("mem_offset", 0)
            base_mem = max(0, live_mem - mem_offset)
            offset_sign = "+" if mem_offset >= 0 else ""
            self.card_mem.update_metrics(
                f"{live_mem} MHz" if live_mem > 0 else "N/A",
                f"Base: {base_mem} MHz | Offset: {offset_sign}{mem_offset} MHz"
            )
            
            # 3. Temp
            live_temp = info.get("live_temp", 0)
            live_vram = info.get("live_vram_temp", 0)
            temp_limit = info.get("temp_limit", 83)
            vram_str = f" | VRAM: {live_vram} °C" if live_vram > 0 else ""
            self.card_temp.update_metrics(
                f"{live_temp} °C" if live_temp > 0 else "N/A",
                f"Limit Target: {temp_limit} °C{vram_str}"
            )
            
            # 4. Power Draw
            live_power = info.get("live_power_w", 0.0)
            pl_w = info.get("power_limit_w", 0.0)
            p_default = info.get("power_default_w", 0.0)
            p_pct_str = ""
            if p_default > 0:
                p_pct = (pl_w / p_default) * 100
                p_pct_str = f" ({p_pct:.0f}%)"
            self.card_power.update_metrics(
                f"{live_power:.2f} W" if live_power > 0 else "N/A",
                f"Limit: {pl_w:.1f} W{p_pct_str}"
            )
            
            # 5. GPU Util
            gpu_util = info.get("live_gpu_util", -1)
            gpu_util_str = f"{gpu_util} %" if gpu_util >= 0 else "N/A"
            self.card_gpu_util.update_metrics(
                gpu_util_str,
                "Active Rendering & Compute" if Trans.CURRENT_LANG == "en_US" else "核心渲染与通用计算"
            )
            
            # 6. VRAM Usage
            vram_used = info.get("vram_used_mb", 0.0)
            vram_total = info.get("vram_total_mb", 0.0)
            vram_pct = (vram_used / vram_total * 100) if vram_total > 0 else 0
            self.card_vram.update_metrics(
                f"{vram_used:.1f} MB / {vram_total:.1f} MB" if vram_total > 0 else "N/A",
                f"VRAM Capacity Used: {vram_pct:.1f}%" if Trans.CURRENT_LANG == "en_US" else f"显存占用百分比: {vram_pct:.1f}%"
            )
            
            # 7. Fan Speed
            fan = info.get("live_fan_speed", -1)
            fan_str = f"{fan} %" if fan >= 0 else "N/A"
            self.card_fan.update_metrics(
                fan_str,
                "Active Cooling Fan Speed" if Trans.CURRENT_LANG == "en_US" else "主动散热风扇转速"
            )
            
            # 8. PCIe Info
            pcie_w = info.get("pcie_width", 0)
            pcie_g = info.get("pcie_gen", 0)
            pcie_str = f"PCIe Gen {pcie_g} x{pcie_w}" if pcie_w > 0 and pcie_g > 0 else "N/A"
            self.card_pcie.update_metrics(
                pcie_str,
                "Bus Interface Speed & Width" if Trans.CURRENT_LANG == "en_US" else "系统总线接口带宽与协议"
            )
            
        except Exception as e:
            logger.debug(f"Failed to query monitor stats: {e}")

    def on_startup_switch_changed(self, checked: bool):
        if not self.settings:
            return
            
        if checked:
            title = "安全性风险警告" if Trans.CURRENT_LANG == "zh_CN" else "Safety Warning"
            content = (
                "警告：您正在开启“开机自动应用超频”选项。\n\n"
                "如果超频参数（如核心或显存频率）设置过高，可能导致显卡无法正常渲染进而导致系统蓝屏或崩溃。"
                "由于软件开机自动应用此设定，系统可能会陷入“开机即崩溃”的无限恶性循环，此时需要进入安全模式手动重置配置。\n\n"
                "您是否确认开启此选项？"
                if Trans.CURRENT_LANG == "zh_CN" else
                "WARNING: You are enabling \"Apply Overclock on Startup\".\n\n"
                "If the offsets are set too high, the GPU may crash on boot, causing an infinite BSOD loop. "
                "You would need to boot into Safe Mode to clean the app settings.\n\n"
                "Do you really want to enable this option?"
            )
            
            dialog = MessageBox(title, content, self.parent_window)
            dialog.yesButton.setText("确认开启 (Confirm)" if Trans.CURRENT_LANG == "zh_CN" else "Yes, I understand")
            dialog.cancelButton.setText("取消 (Cancel)" if Trans.CURRENT_LANG == "zh_CN" else "Cancel")
            
            if dialog.exec():
                self.settings.gpu_apply_on_startup = True
            else:
                self.switch_startup.setChecked(False)
                self.settings.gpu_apply_on_startup = False
        else:
            self.settings.gpu_apply_on_startup = False

    def apply_tuning_profile(self):
        """
        Reads values from the sliders, applies them to the GPU via GpuOverclockService,
        and saves them to configuration settings.
        """
        core = self.slider_core.value()
        mem = self.slider_mem.value()
        power = float(self.slider_power.value())
        temp = self.slider_temp.value()
        voltage = self.slider_voltage.value()
        
        success = GpuOverclockService.apply_overclock(
            core_offset=core,
            mem_offset=mem,
            power_limit_pct=power,
            temp_limit=temp,
            voltage_pct=voltage
        )
        
        if success:
            if self.settings:
                self.settings.gpu_core_offset = core
                self.settings.gpu_mem_offset = mem
                self.settings.gpu_power_limit = power
                self.settings.gpu_temp_limit = temp
                self.settings.gpu_voltage = voltage
                
            InfoBar.success(
                title=Trans.get("msg_op_success", "操作成功"),
                content=Trans.get("gpu_oc_success", "超频配置应用成功"),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self
            )
            
            self.refresh_monitor_stats()
        else:
            InfoBar.error(
                title=Trans.get("msg_op_failed", "操作失败"),
                content="应用超频参数失败，请确保以管理员权限启动且显卡驱动正常运行。" if Trans.CURRENT_LANG == "zh_CN"
                else "Failed to apply overclock offsets. Ensure you are running as admin.",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3500,
                parent=self
            )

    def restore_tuning_defaults(self, show_msg: bool = True):
        """
        Restores default tuning parameters on the sliders, calls restore_defaults on the service,
        and clears user overrides in settings.
        """
        self.slider_core.setValue(0)
        self.slider_mem.setValue(0)
        self.slider_power.setValue(100)
        self.slider_temp.setValue(83)
        self.slider_voltage.setValue(0)
        
        if hasattr(self, 'switch_startup'):
            self.switch_startup.setChecked(False)
            
        GpuOverclockService.restore_defaults()
        
        if self.settings:
            self.settings.gpu_core_offset = 0
            self.settings.gpu_mem_offset = 0
            self.settings.gpu_power_limit = 100.0
            self.settings.gpu_temp_limit = 83
            self.settings.gpu_voltage = 0
            self.settings.gpu_apply_on_startup = False
            
        if show_msg:
            InfoBar.success(
                title=Trans.get("msg_op_success", "操作成功"),
                content=Trans.get("gpu_oc_restore_success", "显卡已恢复系统默认状态"),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self
            )
        
        self.refresh_monitor_stats()

    def retranslate_ui(self):
        """
        Handles dynamic locale updates when language changes.
        """
        self.title_label.setText(Trans.get("gpu_oc_title", "GPU 硬件超频与性能微调"))
        self.desc_label.setText(Trans.get("gpu_oc_desc", "通过图形接口底层 API 调整显卡核心与显存频率偏移量、功耗限制及电压偏置，优化图形处理器计算性能。"))
        
        if not self.gpu_info.get("supported", False):
            if hasattr(self, 'unsupported_card'):
                for child in self.unsupported_card.findChildren(BodyLabel):
                    child.setText(Trans.get("gpu_oc_not_supported", "当前系统未检测到支持的 NVIDIA 显卡或 NVAPI/NVML 驱动程序接口加载失败。"))
            return
            
        self.lbl_startup_title.setText(Trans.get("gpu_oc_startup_apply", "开机自动应用此超频配置 (不推荐)"))
        self.btn_apply.setText(Trans.get("gpu_oc_apply_btn", "应用超频配置 (Apply)"))
        self.btn_restore.setText(Trans.get("gpu_oc_restore_btn", "恢复默认参数 (Restore Defaults)"))
        
        self.refresh_monitor_stats()

    def showEvent(self, event):
        super().showEvent(event)
        if self.gpu_info.get("supported", False) and hasattr(self, 'monitor_timer') and not self.monitor_timer.isActive():
            self.monitor_timer.start(2000)
            self.refresh_monitor_stats()

    def hideEvent(self, event):
        super().hideEvent(event)
        if hasattr(self, 'monitor_timer') and self.monitor_timer.isActive():
            self.monitor_timer.stop()

    def cleanup_widget(self):
        if hasattr(self, 'monitor_timer') and self.monitor_timer:
            try:
                self.monitor_timer.stop()
                self.monitor_timer.timeout.disconnect()
            except Exception:
                pass
