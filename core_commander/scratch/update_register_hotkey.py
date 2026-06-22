import os
import re

file_path = r"e:\源码\core_commander\ui\window.py"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

old_func_pattern = re.compile(r"    def register_global_hotkey\(self\):.*?    def unregister_global_hotkey\(self\):", re.DOTALL)

new_func = """    def register_global_hotkey(self):
        \"\"\"
        Registers the global hotkey (e.g. Ctrl+Shift+O) via keyboard module.
        \"\"\"
        hotkey_str = self.settings.fps_overlay_hotkey
        if not hotkey_str or hotkey_str == "无":
            self.unregister_global_hotkey()
            return
            
        if (hasattr(self, 'current_registered_hotkey') and 
            self.current_registered_hotkey == hotkey_str and 
            hasattr(self, 'global_hotkey_hook') and 
            self.global_hotkey_hook is not None):
            return
            
        self.unregister_global_hotkey()
        
        import keyboard
        try:
            hotkey_str_parsed = hotkey_str.replace("Meta", "Windows").replace("Control", "Ctrl").lower()
            self.global_hotkey_hook = keyboard.add_hotkey(hotkey_str_parsed, self.toggle_osd_visibility, suppress=False)
            self.hotkey_registered = True
            self.current_registered_hotkey = hotkey_str
            from loguru import logger
            logger.info(f"Registered global hotkey '{hotkey_str}' via keyboard module.")
        except Exception as e:
            from loguru import logger
            logger.error(f"Failed to register OSD hotkey {hotkey_str}: {e}")
            self.hotkey_registered = False

    def unregister_global_hotkey(self):"""

content = old_func_pattern.sub(new_func, content)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)
print("Updated register_global_hotkey successfully.")
