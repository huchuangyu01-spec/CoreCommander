import os

file_path = r"e:\源码\core_commander\ui\window.py"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# Update load_settings
old_load = """        if hasattr(self, 'general_page'):
            if win and hasattr(win, 'settings') and win.settings.rate_limiter_hotkey != "无":
                self.btn_rl_bind.setText(f"按键: {win.settings.rate_limiter_hotkey}")"""

# We just need to find where self.general_page is loaded and add btn_ocr_hotkey update
# Actually, let's just append to load_settings in window.py
old_load2 = """            self.general_page.rl_unit_combo.setCurrentText(self.settings.rate_limiter_unit)
            self.general_page.update_rate_limiter_controls_state()"""
new_load2 = """            self.general_page.rl_unit_combo.setCurrentText(self.settings.rate_limiter_unit)
            self.general_page.update_rate_limiter_controls_state()
            if hasattr(self.general_page, 'btn_ocr_hotkey'):
                self.general_page.btn_ocr_hotkey.setShortcut(self.settings.ocr_hotkey)"""
content = content.replace(old_load2, new_load2)

# Update save_settings to save btn_ocr_hotkey
old_save = """            new_enable = self.network_page.rl_switch.isChecked()"""
new_save = """            if hasattr(self.general_page, 'btn_ocr_hotkey'):
                new_ocr_hotkey = self.general_page.btn_ocr_hotkey.shortcut().toString()
                if new_ocr_hotkey and self.settings.ocr_hotkey != new_ocr_hotkey:
                    self.settings.ocr_hotkey = new_ocr_hotkey
                    self.register_ocr_hotkey(force=True)
            
            new_enable = self.network_page.rl_switch.isChecked()"""
content = content.replace(old_save, new_save)

# Update register_ocr_hotkey
old_reg = """    def register_ocr_hotkey(self):
        if hasattr(self, 'ocr_hotkey_registered') and self.ocr_hotkey_registered:
            return
            
        # Register Alt+Q for OCR overlay (MOD_ALT = 0x0001, 'Q' = 0x51)
        # We use a dedicated hotkey listener thread for it to avoid conflicts
        try:
            self.ocr_hotkey_thread = HotkeyListenerThread(0x0001 | 0x4000, 0x51, hotkey_id=998, parent=self)
            self.ocr_hotkey_thread.triggered.connect(self.toggle_ocr_overlay)
            self.ocr_hotkey_thread.start()
            self.ocr_hotkey_registered = True
            logger.info("Registered Alt+Q hotkey for OCR Overlay (ID 998).")
        except Exception as e:
            logger.error(f"Failed to register OCR hotkey: {e}")"""

new_reg = """    def register_ocr_hotkey(self, force=False):
        if hasattr(self, 'ocr_hotkey_registered') and self.ocr_hotkey_registered and not force:
            return
            
        if force and hasattr(self, 'ocr_hotkey_thread') and self.ocr_hotkey_thread:
            self.ocr_hotkey_thread.stop()
            self.ocr_hotkey_thread.wait()
            self.ocr_hotkey_thread = None
            
        import keyboard
        # Register using keyboard module which takes string representation directly (e.g. "Alt+Q", "Ctrl+Shift+F")
        try:
            if hasattr(self, 'ocr_hotkey_hook'):
                keyboard.remove_hotkey(self.ocr_hotkey_hook)
                
            hotkey_str = self.settings.ocr_hotkey.replace("Meta", "Windows").replace("Control", "Ctrl").lower()
            if hotkey_str:
                self.ocr_hotkey_hook = keyboard.add_hotkey(hotkey_str, self.toggle_ocr_overlay, suppress=False)
                self.ocr_hotkey_registered = True
                logger.info(f"Registered {self.settings.ocr_hotkey} hotkey for OCR Overlay.")
        except Exception as e:
            logger.error(f"Failed to register OCR hotkey {self.settings.ocr_hotkey}: {e}")"""
content = content.replace(old_reg, new_reg)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)
print("window.py updated.")
