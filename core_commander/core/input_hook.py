# -*- coding: utf-8 -*-
import ctypes
from ctypes import wintypes
from PySide6.QtCore import QObject, Signal
from core_commander.utils.logger import logger

# Low-level Hook Codes
WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14

# Window Message Codes
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_KEYUP = 0x0101
WM_SYSKEYUP = 0x0105

WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C

class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong)
    ]

class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong)
    ]

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

# VK Codes Mapping Table
VK_NAMES = {
    0x01: "Left Click",
    0x02: "Right Click",
    0x04: "Middle Click",
    0x05: "Mouse Button 4",
    0x06: "Mouse Button 5",
    0x08: "Backspace",
    0x09: "Tab",
    0x0D: "Enter",
    0x10: "Shift",
    0x11: "Ctrl",
    0x12: "Alt",
    0x13: "Pause",
    0x14: "Caps Lock",
    0x1B: "Esc",
    0x20: "Space",
    0x21: "Page Up",
    0x22: "Page Down",
    0x23: "End",
    0x24: "Home",
    0x25: "Left",
    0x26: "Up",
    0x27: "Right",
    0x28: "Down",
    0x2C: "Print Screen",
    0x2D: "Insert",
    0x2E: "Delete",
    0xA0: "Left Shift",
    0xA1: "Right Shift",
    0xA2: "Left Ctrl",
    0xA3: "Right Ctrl",
    0xA4: "Left Alt",
    0xA5: "Right Alt",
    0x5B: "Left Win",
    0x5C: "Right Win",
    0x5D: "Apps",
    0x90: "Num Lock",
    0x91: "Scroll Lock",
}
# A-Z
for vk in range(0x41, 0x5A + 1):
    VK_NAMES[vk] = chr(vk)
# 0-9
for vk in range(0x30, 0x39 + 1):
    VK_NAMES[vk] = chr(vk)
# F1 - F24
for i in range(24):
    VK_NAMES[0x70 + i] = f"F{i+1}"
# Numpad
for i in range(10):
    VK_NAMES[0x60 + i] = f"Numpad {i}"
VK_NAMES[0x6A] = "Numpad *"
VK_NAMES[0x6B] = "Numpad +"
VK_NAMES[0x6C] = "Numpad Enter"
VK_NAMES[0x6D] = "Numpad -"
VK_NAMES[0x6E] = "Numpad ."
VK_NAMES[0x6F] = "Numpad /"

HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, ctypes.c_int, ctypes.c_uint64, ctypes.c_int64)

_user32 = ctypes.WinDLL('user32', use_last_error=True)
_kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

# Cache and setup Windows hook API signatures globally
_user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint64, ctypes.c_int64]
_user32.CallNextHookEx.restype = ctypes.c_int64

_user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
_user32.SetWindowsHookExW.restype = wintypes.HHOOK

_user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
_user32.UnhookWindowsHookEx.restype = wintypes.BOOL

_user32.PostThreadMessageW.argtypes = [wintypes.DWORD, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
_user32.PostThreadMessageW.restype = wintypes.BOOL

_kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE

import threading

class GlobalInputHookThread(QObject):
    key_bind_captured = Signal(str, int, str)  # key_name, key_code, key_type
    hotkey_pressed = Signal()
    hotkey_released = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.h_kb_hook = None
        self.h_ms_hook = None
        self.hook_thread_id = None
        self.hook_thread = None
        self.running = False
        
        # Private states for properties
        self._binding_mode = False
        self._recording_mode = False
        self.need_mouse = False
        
        # Bound hotkey target state
        self.bound_code = 0
        self.bound_type = "keyboard" # keyboard or mouse
        
        # Key tracking state
        self.is_currently_pressed = False
        
        self.direct_press_cb = None
        self.direct_release_cb = None
        
        # Store callback references strictly to prevent Python GC crashing Windows hook callback
        self.kb_hook_proc = HOOKPROC(self._keyboard_hook_handler)
        self.ms_hook_proc = HOOKPROC(self._mouse_hook_handler)

    @property
    def recording_mode(self) -> bool:
        return self._recording_mode
        
    @recording_mode.setter
    def recording_mode(self, enabled: bool):
        if self._recording_mode != enabled:
            self._recording_mode = enabled
            self._update_hooks_active_state()

    @property
    def binding_mode(self) -> bool:
        return self._binding_mode
        
    @binding_mode.setter
    def binding_mode(self, enabled: bool):
        if self._binding_mode != enabled:
            self._binding_mode = enabled
            self._update_hooks_active_state()

    def _update_hooks_active_state(self):
        """
        Dynamically installs or uninstalls WH_MOUSE_LL hook 
        based on active recording, binding, or bound hotkey type requirements.
        """
        currently_need_mouse = (self._recording_mode or 
                                self._binding_mode or 
                                (self.bound_type == "mouse"))
        
        if currently_need_mouse != self.need_mouse:
            logger.info(f"Dynamically switching mouse hook requirement: {self.need_mouse} -> {currently_need_mouse}")
            if self.running:
                is_on_hook_thread = (threading.get_native_id() == self.hook_thread_id)
                if is_on_hook_thread:
                    self.need_mouse = currently_need_mouse
                    threading.Thread(target=self._restart_hook_thread_async, daemon=True).start()
                else:
                    self.stop()
                    self.start(need_mouse=currently_need_mouse)
            else:
                self.need_mouse = currently_need_mouse

    def _restart_hook_thread_async(self):
        self.stop()
        self.start(need_mouse=self.need_mouse)

    def set_binding_mode(self, enabled: bool):
        """Enables or disables key/mouse binding recording mode."""
        self.binding_mode = enabled
        logger.info(f"GlobalInputHookThread binding mode set to: {enabled}")

    def update_hotkey(self, code: int, key_type: str):
        """Updates the active hotkey filter configuration."""
        self.bound_code = code
        self.bound_type = key_type
        self.is_currently_pressed = False
        logger.info(f"GlobalInputHookThread hotkey updated to code: {code}, type: {key_type}")
        self._update_hooks_active_state()

    def start(self, need_mouse: bool = None):
        if self.running:
            return
        if need_mouse is None:
            need_mouse = (self._recording_mode or 
                          self._binding_mode or 
                          (self.bound_type == "mouse"))
        self.need_mouse = need_mouse
        self.running = True
        self._started_event = threading.Event()
        self.hook_thread = threading.Thread(target=self._run_hook_loop, daemon=True)
        self.hook_thread.start()
        self._started_event.wait(timeout=2.0)
        logger.info(f"GlobalInputHookThread background thread started (need_mouse={need_mouse}).")

    def _safe_execute_cb(self, cb):
        try:
            cb()
        except Exception as e:
            logger.debug(f"Error in input hook asynchronous callback: {e}")

    def _run_hook_loop(self):
        self.hook_thread_id = threading.get_native_id()
        msg = MSG()
        
        # Force message queue creation for this thread
        _user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, 0)
        
        h_mod = _kernel32.GetModuleHandleW(None)
        
        self.h_kb_hook = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, self.kb_hook_proc, h_mod, 0)
        if not self.h_kb_hook:
            logger.error(f"Failed to install keyboard hook. Error: {ctypes.WinError(ctypes.get_last_error())}")
            
        if self.need_mouse:
            self.h_ms_hook = _user32.SetWindowsHookExW(WH_MOUSE_LL, self.ms_hook_proc, h_mod, 0)
            if not self.h_ms_hook:
                logger.error(f"Failed to install mouse hook. Error: {ctypes.WinError(ctypes.get_last_error())}")
        else:
            self.h_ms_hook = None
            
        self._started_event.set()
        
        try:
            while self.running:
                r = _user32.GetMessageW(ctypes.byref(msg), 0, 0, 0)
                if r <= 0:
                    break
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if self.h_kb_hook:
                _user32.UnhookWindowsHookEx(self.h_kb_hook)
                self.h_kb_hook = None
            if self.h_ms_hook:
                _user32.UnhookWindowsHookEx(self.h_ms_hook)
                self.h_ms_hook = None
            logger.info("GlobalInputHookThread message loop exited.")

    def stop(self):
        self.running = False
        if self.hook_thread_id:
            # Post WM_QUIT to break the GetMessageW loop gracefully
            _user32.PostThreadMessageW(self.hook_thread_id, 0x0012, 0, 0)
        if self.hook_thread and self.hook_thread.is_alive():
            self.hook_thread.join(timeout=1.0)
        logger.info("GlobalInputHookThread stopped.")

    def wait(self, timeout=None):
        """Mock method for compatibility."""
        pass

    def _keyboard_hook_handler(self, nCode, wParam, lParam):
        try:
            if nCode >= 0:
                kbd = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                
                vk_code = kbd.vkCode
                is_down = wParam in (0x0100, 0x0104) # WM_KEYDOWN or WM_SYSKEYDOWN
                is_up = wParam in (WM_KEYUP, WM_SYSKEYUP)
                
                # Global generic key listener callback (can block key by returning True)
                if getattr(self, "global_key_callback", None) and (is_down or is_up):
                    if self.global_key_callback(vk_code, is_down):
                        return 1
                
                # Record mode handler
                if self.recording_mode and self.record_callback and (is_down or is_up):
                    event_type = "key_down" if is_down else "key_up"
                    self.record_callback(event_type, vk_code, 0, 0, kbd.time)
                
                if self.binding_mode and is_down:
                    key_name = VK_NAMES.get(vk_code, f"Key {vk_code}")
                    self.key_bind_captured.emit(key_name, vk_code, "keyboard")
                    self.binding_mode = False
                    return 1
                        
                elif not self.binding_mode:
                    if self.bound_type == "keyboard" and vk_code == self.bound_code:
                        if is_down and not self.is_currently_pressed:
                            self.is_currently_pressed = True
                            if self.direct_press_cb:
                                # Run in separate thread to prevent OS-level hook blocking/deadlocks
                                threading.Thread(target=self._safe_execute_cb, args=(self.direct_press_cb,), daemon=True).start()
                            self.hotkey_pressed.emit()
                        elif is_up and self.is_currently_pressed:
                            self.is_currently_pressed = False
                            if self.direct_release_cb:
                                # Run in separate thread to prevent OS-level hook blocking/deadlocks
                                threading.Thread(target=self._safe_execute_cb, args=(self.direct_release_cb,), daemon=True).start()
                            self.hotkey_released.emit()
                        # Allow the key event to pass through to the game by NOT returning 1 here
        except Exception as ex:
            logger.error(f"Error in keyboard hook callback: {str(ex)}")
            
        # VERY IMPORTANT: explicitly cast arguments to prevent 64-bit pointer truncation in CallNextHookEx
        return _user32.CallNextHookEx(None, ctypes.c_int(nCode), ctypes.c_uint64(wParam), ctypes.c_int64(lParam))

    def _mouse_hook_handler(self, nCode, wParam, lParam):
        if wParam == 0x0200: # WM_MOUSEMOVE
            if not getattr(self, "_recording_mode", False):
                return _user32.CallNextHookEx(None, ctypes.c_int(nCode), ctypes.c_uint64(wParam), ctypes.c_int64(lParam))
            if self.recording_mode and self.record_callback:
                try:
                    ms = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    # Skip injected events (simulated by our replay thread or SendInput)
                    if ms.flags & 0x01: # LLMHF_INJECTED
                        return _user32.CallNextHookEx(None, ctypes.c_int(nCode), ctypes.c_uint64(wParam), ctypes.c_int64(lParam))
                    self.record_callback("mouse_move", 0, ms.pt.x, ms.pt.y, ms.time)
                except Exception as ex:
                    logger.debug(f"Error recording mouse move: {ex}")
            return _user32.CallNextHookEx(None, ctypes.c_int(nCode), ctypes.c_uint64(wParam), ctypes.c_int64(lParam))
            
        try:
            if nCode >= 0:
                ms = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                # Skip injected events (simulated by our replay thread or SendInput)
                if ms.flags & 0x01: # LLMHF_INJECTED
                    return _user32.CallNextHookEx(None, ctypes.c_int(nCode), ctypes.c_uint64(wParam), ctypes.c_int64(lParam))
                is_down = False
                is_up = False
                code = 0
                
                if wParam == WM_LBUTTONDOWN:
                    is_down = True
                    code = 0x01
                elif wParam == WM_LBUTTONUP:
                    is_up = True
                    code = 0x01
                elif wParam == WM_RBUTTONDOWN:
                    is_down = True
                    code = 0x02
                elif wParam == WM_RBUTTONUP:
                    is_up = True
                    code = 0x02
                elif wParam == WM_MBUTTONDOWN:
                    is_down = True
                    code = 0x04
                elif wParam == WM_MBUTTONUP:
                    is_up = True
                    code = 0x04
                elif wParam == WM_XBUTTONDOWN:
                    ms = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    xbutton = (ms.mouseData >> 16) & 0xFFFF
                    is_down = True
                    code = 0x05 if xbutton == 1 else 0x06
                elif wParam == WM_XBUTTONUP:
                    ms = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    xbutton = (ms.mouseData >> 16) & 0xFFFF
                    is_up = True
                    code = 0x05 if xbutton == 1 else 0x06
                    
                if code > 0:
                    # Global generic mouse listener callback (can block mouse click by returning True)
                    if getattr(self, "global_mouse_callback", None):
                        if self.global_mouse_callback(code, is_down):
                            return 1
                            
                    if self.recording_mode and self.record_callback:
                        try:
                            ms = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                            event_type = "mouse_down" if is_down else "mouse_up"
                            self.record_callback(event_type, code, ms.pt.x, ms.pt.y, ms.time)
                        except Exception as ex:
                            logger.debug(f"Error recording mouse click: {ex}")
                            
                    if self.binding_mode and is_down:
                        key_name = VK_NAMES.get(code, f"Mouse Button {code}")
                        self.key_bind_captured.emit(key_name, code, "mouse")
                        self.binding_mode = False
                        return 1
                            
                    elif not self.binding_mode:
                        if self.bound_type == "mouse" and code == self.bound_code:
                            if is_down and not self.is_currently_pressed:
                                self.is_currently_pressed = True
                                if self.direct_press_cb:
                                    # Run in separate thread to prevent OS-level hook blocking/deadlocks
                                    threading.Thread(target=self._safe_execute_cb, args=(self.direct_press_cb,), daemon=True).start()
                                self.hotkey_pressed.emit()
                            elif is_up and self.is_currently_pressed:
                                self.is_currently_pressed = False
                                if self.direct_release_cb:
                                    # Run in separate thread to prevent OS-level hook blocking/deadlocks
                                    threading.Thread(target=self._safe_execute_cb, args=(self.direct_release_cb,), daemon=True).start()
                                self.hotkey_released.emit()
                            # Allow the mouse event to pass through to the game
        except Exception as ex:
            logger.error(f"Error in mouse hook callback: {str(ex)}")
            
        # VERY IMPORTANT: explicitly cast arguments to prevent 64-bit pointer truncation in CallNextHookEx
        return _user32.CallNextHookEx(None, ctypes.c_int(nCode), ctypes.c_uint64(wParam), ctypes.c_int64(lParam))
