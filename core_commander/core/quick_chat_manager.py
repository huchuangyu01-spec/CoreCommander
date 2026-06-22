# -*- coding: utf-8 -*-
import os
import json
import time
import threading
import ctypes
from ctypes import wintypes
from PySide6.QtCore import QObject, Signal
from core_commander.utils.logger import logger

# --- Windows Ctypes SendInput API Definitions ---
INPUT_KEYBOARD = 1

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong)
    ]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD)
    ]

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong)
    ]

class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT)
    ]

class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("u", INPUT_UNION)
    ]

user32 = ctypes.WinDLL('user32', use_last_error=True)
user32.SendInput.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.GetMessageExtraInfo.argtypes = []
user32.GetMessageExtraInfo.restype = ctypes.c_ulonglong

class QuickChatManager(QObject):
    """
    Manager service for configuring and executing in-game Quick Chat / Quick Speech phrases.
    Also supports Spam Mode (轰炸模式) with looping and custom delay interval.
    Uses KEYEVENTF_UNICODE to bypass IME input issues for instant typing.
    """
    rules_updated = Signal()
    spam_state_changed = Signal(bool) # Emitted when spam mode starts (True) or stops (False)
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        super().__init__()
        self._initialized = True
        self.config_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "data", "quick_chat_config.json"
        )
        self._spam_active = False
        self.rules = []
        self.load_rules()

    def load_rules(self):
        self.spam_hotkey_code = 0x75 # F6
        self.spam_hotkey_type = "keyboard"
        self.spam_hotkey_name = "F6"
        self.spam_interval_ms = 1000
        self.spam_loop = False
        self.rules = []
        
        if not os.path.exists(self.config_file):
            self.rules = [
                {
                    "id": "sample_1",
                    "text": "中路 Miss，注意防守！",
                    "hotkey_code": 0x74, # F5
                    "hotkey_type": "keyboard",
                    "hotkey_name": "F5",
                    "enabled": True
                }
            ]
            self.save_rules()
            return
            
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    self.rules = data.get("rules", [])
                    self.spam_hotkey_code = data.get("spam_hotkey_code", 0x75)
                    self.spam_hotkey_type = data.get("spam_hotkey_type", "keyboard")
                    self.spam_hotkey_name = data.get("spam_hotkey_name", "F6")
                    self.spam_interval_ms = data.get("spam_interval_ms", 1000)
                    self.spam_loop = data.get("spam_loop", False)
                else:
                    self.rules = data
        except Exception as e:
            logger.error(f"Error loading quick chat rules: {e}")

    def save_rules(self):
        try:
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
            data = {
                "rules": self.rules,
                "spam_hotkey_code": self.spam_hotkey_code,
                "spam_hotkey_type": self.spam_hotkey_type,
                "spam_hotkey_name": self.spam_hotkey_name,
                "spam_interval_ms": self.spam_interval_ms,
                "spam_loop": self.spam_loop
            }
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            self.rules_updated.emit()
        except Exception as e:
            logger.error(f"Error saving quick chat rules: {e}")

    def is_spam_active(self) -> bool:
        return self._spam_active

    def check_and_trigger(self, vk_code, is_down, is_keyboard=True) -> bool:
        """
        Invoked from global hooks to check if the hotkey matches any active quick chat rule or spam toggle.
        Returns True if handled/triggered to consume the keystroke event.
        """
        from core_commander.core.license import license_manager
        if not license_manager.is_active:
            return False

        if not is_down:
            return False
            
        expected_type = "keyboard" if is_keyboard else "mouse"
        
        # 1. First check if the hotkey matches the Spam Mode toggle
        if self.spam_hotkey_type == expected_type and self.spam_hotkey_code == vk_code:
            self.toggle_spam_mode()
            return True
            
        # 2. If Spam Mode is currently running, ignore individual triggers
        if self._spam_active:
            return False
            
        # 3. Check individual quick chat rules
        for rule in self.rules:
            if not rule.get("enabled", True):
                continue
                
            if rule.get("hotkey_type") == expected_type and rule.get("hotkey_code") == vk_code:
                text = rule.get("text", "")
                if text:
                    self.execute_speech(text)
                return True
        return False

    def execute_speech(self, text):
        """Simulate Enter key -> Typist -> Enter key sequence in background thread."""
        def run():
            self._type_and_send(text)
        threading.Thread(target=run, daemon=True).start()

    def toggle_spam_mode(self):
        if self._spam_active:
            self.stop_spam_mode()
        else:
            self.start_spam_mode()

    def start_spam_mode(self):
        if self._spam_active:
            return
        self._spam_active = True
        self.spam_state_changed.emit(True)
        logger.info("Spam Mode started")
        
        def run():
            while self._spam_active:
                enabled_rules = [r for r in self.rules if r.get("enabled", True)]
                if not enabled_rules:
                    break
                    
                for rule in enabled_rules:
                    if not self._spam_active:
                        break
                    text = rule.get("text", "")
                    if text:
                        self._type_and_send(text)
                        
                    # Sleep in increments of 50ms so we can abort instantly
                    interval_sec = self.spam_interval_ms / 1000.0
                    steps = int(interval_sec / 0.05)
                    for _ in range(max(1, steps)):
                        if not self._spam_active:
                            break
                        time.sleep(0.05)
                        
                if not self.spam_loop:
                    break
                    
            self._spam_active = False
            self.spam_state_changed.emit(False)
            logger.info("Spam Mode stopped")
            
        threading.Thread(target=run, daemon=True).start()

    def stop_spam_mode(self):
        self._spam_active = False
        logger.info("Spam Mode stop requested")

    def _type_and_send(self, text):
        # Prevent keyboard loopback/tampering when our own window is in the foreground
        try:
            import win32gui
            import win32process
            hwnd = win32gui.GetForegroundWindow()
            if hwnd:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid == os.getpid():
                    logger.warning("Quick chat key simulation blocked because our own app is in the foreground.")
                    return
        except Exception as e:
            logger.error(f"Error checking foreground process in typing simulation: {e}")

        # 1. Send Enter to open chatbox
        self._simulate_key(0x0D, is_down=True)
        time.sleep(0.01)
        self._simulate_key(0x0D, is_down=False)
        time.sleep(0.05) # Wait for chat window animation
        
        # 2. Type each unicode character
        for char in text:
            self._simulate_unicode_char(char)
            time.sleep(0.002) # 2ms delay
            
        time.sleep(0.05) # Wait for buffers to flush
        
        # 3. Send Enter to send chat message
        self._simulate_key(0x0D, is_down=True)
        time.sleep(0.01)
        self._simulate_key(0x0D, is_down=False)

    def _simulate_key(self, vk, is_down):
        scan = user32.MapVirtualKeyW(vk, 0)
        flags = 0x0008  # KEYEVENTF_SCANCODE
        if not is_down:
            flags |= 0x0002  # KEYEVENTF_KEYUP
            
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.ki.wVk = 0
        inp.ki.wScan = scan
        inp.ki.dwFlags = flags
        inp.ki.time = 0
        inp.ki.dwExtraInfo = user32.GetMessageExtraInfo()
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))

    def _simulate_unicode_char(self, char):
        extra = user32.GetMessageExtraInfo()
        
        # Key down
        inp_down = INPUT()
        inp_down.type = INPUT_KEYBOARD
        inp_down.ki.wVk = 0
        inp_down.ki.wScan = ord(char)
        inp_down.ki.dwFlags = 0x0004  # KEYEVENTF_UNICODE
        inp_down.ki.time = 0
        inp_down.ki.dwExtraInfo = extra
        user32.SendInput(1, ctypes.byref(inp_down), ctypes.sizeof(inp_down))
        
        # Key up
        inp_up = INPUT()
        inp_up.type = INPUT_KEYBOARD
        inp_up.ki.wVk = 0
        inp_up.ki.wScan = ord(char)
        inp_up.ki.dwFlags = 0x0004 | 0x0002  # KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
        inp_up.ki.time = 0
        inp_up.ki.dwExtraInfo = extra
        user32.SendInput(1, ctypes.byref(inp_up), ctypes.sizeof(inp_up))
