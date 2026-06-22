import os
import re

file_path = r"e:\源码\core_commander\ui\pages\settings.py"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# Replace OCR Card definition
old_ocr_card = """        # 5. OCR Overlay Info Card
        self.ocr_card = SettingCard(
            FluentIcon.DOCUMENT,
            "屏幕识图翻译",
            "屏幕 OCR 识别与翻译接口（需联网通讯）。\\n默认快捷键 (Alt+Q) 框选屏幕，松开鼠标即可识别。",
            parent=self.view
        )"""

new_ocr_card = """        # 5. OCR Overlay Info Card
        from qfluentwidgets import SimpleCardWidget, ShortcutEdit
        self.ocr_card = SimpleCardWidget(self.view)
        ocr_layout = QVBoxLayout(self.ocr_card)
        ocr_layout.setContentsMargins(20, 20, 20, 20)
        ocr_layout.setSpacing(10)
        self.lbl_ocr_title = SubtitleLabel("屏幕识图翻译", self.ocr_card)
        self.lbl_ocr_desc = CaptionLabel("屏幕 OCR 识别与翻译接口（需联网通讯）。框选屏幕，松开鼠标即可识别。", self.ocr_card)
        
        ocr_hotkey_layout = QHBoxLayout()
        self.lbl_ocr_hotkey = BodyLabel("触发快捷键:", self.ocr_card)
        self.btn_ocr_hotkey = ShortcutEdit("Alt+Q", self.ocr_card)
        ocr_hotkey_layout.addWidget(self.lbl_ocr_hotkey)
        ocr_hotkey_layout.addStretch()
        ocr_hotkey_layout.addWidget(self.btn_ocr_hotkey)
        
        ocr_layout.addWidget(self.lbl_ocr_title)
        ocr_layout.addWidget(self.lbl_ocr_desc)
        ocr_layout.addLayout(ocr_hotkey_layout)"""

content = content.replace(old_ocr_card, new_ocr_card)

# Add save settings connection
old_conn = """        self.chk_osd.checkedChanged.connect(self.save_settings_immediately)"""
new_conn = """        self.chk_osd.checkedChanged.connect(self.save_settings_immediately)
        self.btn_ocr_hotkey.shortcutChanged.connect(self.save_settings_immediately)"""
content = content.replace(old_conn, new_conn)

# Add retranslate UI for OCR
old_retrans = """        self.lbl_osd_y.setText("OSD Y 坐标 (px):" if Trans.CURRENT_LANG == "zh_CN" else "OSD Y Coord (px):")"""
new_retrans = """        self.lbl_osd_y.setText("OSD Y 坐标 (px):" if Trans.CURRENT_LANG == "zh_CN" else "OSD Y Coord (px):")
        if hasattr(self, 'lbl_ocr_title'):
            self.lbl_ocr_title.setText(Trans.get("ocr_title", "屏幕识图翻译"))
            self.lbl_ocr_desc.setText(Trans.get("ocr_desc", "屏幕 OCR 识别与翻译接口。框选屏幕，松开鼠标即可识别。"))
            self.lbl_ocr_hotkey.setText(Trans.get("ocr_hotkey_label", "触发快捷键 (点击绑定):"))"""
content = content.replace(old_retrans, new_retrans)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)
print("Settings UI updated for OCR hotkey.")
