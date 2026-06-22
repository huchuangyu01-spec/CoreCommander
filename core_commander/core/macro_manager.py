# -*- coding: utf-8 -*-
import os
import sys
import json
import time
import random
import math
import threading
from PySide6.QtCore import QObject, Signal, QThread, QTimer
from core_commander.utils.logger import logger

# --- Windows Ctypes SendInput API Definitions ---
import ctypes
from ctypes import wintypes

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

# Mouse event flags
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_XDOWN = 0x0080
MOUSEEVENTF_XUP = 0x0100
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000

# Keyboard event flags
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong)
    ]

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

# Extended keys that need the KEYEVENTF_EXTENDEDKEY flag when using scan codes
EXTENDED_KEYS = {
    0x21, # VK_PRIOR (Page Up)
    0x22, # VK_NEXT (Page Down)
    0x23, # VK_END
    0x24, # VK_HOME
    0x25, # VK_LEFT
    0x26, # VK_UP
    0x27, # VK_RIGHT
    0x28, # VK_DOWN
    0x2D, # VK_INSERT
    0x2E, # VK_DELETE
    0x6F, # VK_DIVIDE (Numpad /)
    0xA3, # VK_RCONTROL
    0xA5  # VK_RMENU (Right Alt)
}

user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

# Configure ctypes signatures for 64-bit safety
user32.SendInput.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.GetMessageExtraInfo.argtypes = []
user32.GetMessageExtraInfo.restype = ctypes.c_ulonglong
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL

def map_vk_to_scan(vk):
    """Map Virtual Key to Scan Code."""
    return user32.MapVirtualKeyW(vk, 0)

# Load timeBeginPeriod for 1ms high resolution timing
try:
    winmm = ctypes.WinDLL('winmm', use_last_error=True)
except Exception:
    winmm = None


class MacroAction:
    """Represents a single keyboard or mouse action in the time stream."""
    def __init__(self, time_ms, event_type, key_code=0, key_name="", x=0, y=0, active_keys=None):
        self.time_ms = time_ms              # Delta milliseconds from start
        self.event_type = event_type        # key_down, key_up, mouse_move, mouse_down, mouse_up, frame
        self.key_code = key_code            # vkCode for keyboard or mouse button code (1=L, 2=R, 4=M)
        self.key_name = key_name            # Human readable key name
        self.x = x                          # Screen coordinate X
        self.y = y                          # Screen coordinate Y
        self.active_keys = active_keys if active_keys is not None else [] # For frame snapshot mode

    def to_dict(self):
        d = {
            "time_ms": self.time_ms,
            "event_type": self.event_type,
            "key_code": self.key_code,
            "key_name": self.key_name,
            "x": self.x,
            "y": self.y
        }
        if self.event_type == "frame":
            d["active_keys"] = self.active_keys
        return d

    @classmethod
    def from_dict(cls, data):
        return cls(
            time_ms=data.get("time_ms", 0),
            event_type=data.get("event_type", "key_down"),
            key_code=data.get("key_code", 0),
            key_name=data.get("key_name", ""),
            x=data.get("x", 0),
            y=data.get("y", 0),
            active_keys=data.get("active_keys", [])
        )


class MacroProfile:
    """Encapsulates a user macro profile config."""
    def __init__(self, profile_id=None, name="Unnamed Macro"):
        self.profile_id = profile_id if profile_id else f"macro_{int(time.time())}_{random.randint(1000, 9999)}"
        self.name = name
        self.category = "Default"
        self.hotkeys = []             # List of dict: {"code": int, "type": str, "name": str}
        self.hotkey_code = 0
        self.hotkey_type = "keyboard" # keyboard, mouse or none
        self.hotkey_name = ""
        self.actions = []             # List of MacroAction
        
        # New mode configurations
        self.record_mode = "event"    # event, frame
        self.replay_mode = "send_input" # send_input, direct_message
        self.play_mode = "play_once"  # play_once, hold_loop, toggle_loop
        
        # Anti-Detection Fine Settings
        self.smooth_mouse = True
        self.jitter_range_ms = 4      # Random delay jitter range (+/- ms)

    def to_dict(self):
        return {
            "profile_id": self.profile_id,
            "name": self.name,
            "category": self.category,
            "hotkeys": self.hotkeys,
            "hotkey_code": self.hotkey_code,
            "hotkey_type": self.hotkey_type,
            "hotkey_name": self.hotkey_name,
            "record_mode": self.record_mode,
            "replay_mode": self.replay_mode,
            "play_mode": getattr(self, 'play_mode', 'play_once'),
            "smooth_mouse": self.smooth_mouse,
            "jitter_range_ms": self.jitter_range_ms,
            "actions": [act.to_dict() for act in self.actions]
        }

    @classmethod
    def from_dict(cls, data):
        prof = cls(profile_id=data.get("profile_id"), name=data.get("name", "Unnamed Macro"))
        prof.category = data.get("category", "Default")
        prof.hotkeys = data.get("hotkeys", [])
        prof.hotkey_code = data.get("hotkey_code", 0)
        prof.hotkey_type = data.get("hotkey_type", "keyboard")
        prof.hotkey_name = data.get("hotkey_name", "")
        
        if not prof.hotkeys and prof.hotkey_code > 0:
            prof.hotkeys = [{
                "code": prof.hotkey_code,
                "type": prof.hotkey_type,
                "name": prof.hotkey_name
            }]
            
        prof.record_mode = data.get("record_mode", "event")
        prof.replay_mode = data.get("replay_mode", "send_input")
        prof.play_mode = data.get("play_mode", "play_once")
        prof.smooth_mouse = data.get("smooth_mouse", True)
        prof.jitter_range_ms = data.get("jitter_range_ms", 4)
        
        actions_data = data.get("actions", [])
        prof.actions = [MacroAction.from_dict(act) for act in actions_data]
        return prof


class MacroReplayThread(QThread):
    """Thread responsible for playing back actions with high timing accuracy & anti-detection."""
    finished_playback = Signal()
    progress_updated = Signal(int)

    def __init__(self, profile, parent=None):
        super().__init__(parent)
        self.profile = profile
        self.is_running = False

    def stop(self):
        self.is_running = False

    def _find_target_hwnd(self):
        """Resolves target process window HWND via Settings target process name."""
        from core_commander.config.settings import AppSettings
        try:
            settings = AppSettings()
            target_exe = settings.target_process_name
        except Exception:
            target_exe = ""
            
        import win32gui
        if not target_exe:
            return win32gui.GetForegroundWindow()
            
        target_exe = target_exe.lower().strip()
        
        # Find PID of target_exe
        import psutil
        target_pid = None
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                if proc.info['name'] and proc.info['name'].lower().strip() == target_exe:
                    target_pid = proc.info['pid']
                    break
            except Exception:
                continue
                
        if not target_pid:
            return win32gui.GetForegroundWindow()
            
        # Find HWND of target_pid
        import win32process
        hwnds = []
        def enum_windows_callback(hwnd, extra):
            if win32gui.IsWindowVisible(hwnd) and win32gui.IsWindowEnabled(hwnd):
                _, window_pid = win32process.GetWindowThreadProcessId(hwnd)
                if window_pid == target_pid:
                    rect = win32gui.GetWindowRect(hwnd)
                    w = rect[2] - rect[0]
                    h = rect[3] - rect[1]
                    if w > 100 and h > 100:
                        hwnds.append(hwnd)
            return True
            
        try:
            win32gui.EnumWindows(enum_windows_callback, None)
        except Exception:
            pass
            
        if hwnds:
            return hwnds[0]
            
        return win32gui.GetForegroundWindow()

    def _simulate_keyboard_direct_msg(self, hwnd, vk, is_down):
        """Simulate keyboard event by posting WM_KEYDOWN/WM_KEYUP directly to HWND."""
        scan = map_vk_to_scan(vk)
        lParam = 1 | (scan << 16)
        if vk in EXTENDED_KEYS:
            lParam |= (1 << 24)
            
        if is_down:
            msg = 0x0100 # WM_KEYDOWN
        else:
            msg = 0x0101 # WM_KEYUP
            lParam |= (1 << 30) | (1 << 31)
            
        ctypes.windll.user32.PostMessageW(hwnd, msg, vk, lParam)

    def _simulate_mouse_direct_msg(self, hwnd, event_type, btn_code, x, y):
        """Simulate mouse event by posting WM_MOUSEMOVE/WM_LBUTTONDOWN/etc. directly to HWND."""
        import win32gui
        try:
            client_x, client_y = win32gui.ScreenToClient(hwnd, (x, y))
        except Exception:
            client_x, client_y = x, y
            
        lParam = ((client_y & 0xFFFF) << 16) | (client_x & 0xFFFF)
        wParam = 0
        msg = 0
        
        if event_type == "mouse_move":
            msg = 0x0200 # WM_MOUSEMOVE
        elif event_type == "mouse_down":
            if btn_code == 0x01: # Left click
                msg = 0x0201 # WM_LBUTTONDOWN
                wParam = 0x0001 # MK_LBUTTON
            elif btn_code == 0x02: # Right click
                msg = 0x0204 # WM_RBUTTONDOWN
                wParam = 0x0002 # MK_RBUTTON
            elif btn_code == 0x04: # Middle click
                msg = 0x0207 # WM_MBUTTONDOWN
                wParam = 0x0010 # MK_MBUTTON
            elif btn_code == 0x05: # Mouse Button 4 (XBUTTON1)
                msg = 0x020B # WM_XBUTTONDOWN
                wParam = (1 << 16) | 0x0020 # XBUTTON1 and MK_XBUTTON1
            elif btn_code == 0x06: # Mouse Button 5 (XBUTTON2)
                msg = 0x020B # WM_XBUTTONDOWN
                wParam = (2 << 16) | 0x0040 # XBUTTON2 and MK_XBUTTON2
        elif event_type == "mouse_up":
            if btn_code == 0x01:
                msg = 0x0202 # WM_LBUTTONUP
            elif btn_code == 0x02:
                msg = 0x0205 # WM_RBUTTONUP
            elif btn_code == 0x04:
                msg = 0x0208 # WM_MBUTTONUP
            elif btn_code == 0x05:
                msg = 0x020C # WM_XBUTTONUP
                wParam = (1 << 16)
            elif btn_code == 0x06:
                msg = 0x020C # WM_XBUTTONUP
                wParam = (2 << 16)
                
        if msg > 0:
            ctypes.windll.user32.PostMessageW(hwnd, msg, wParam, lParam)

    def run(self):
        self.is_running = True
        logger.info(f"Starting playback of macro: {self.profile.name} (mode: {self.profile.replay_mode})")
        self.progress_updated.emit(0)
        
        if winmm:
            winmm.timeBeginPeriod(1)

        try:
            actions = sorted(self.profile.actions, key=lambda a: a.time_ms)
            if not actions:
                logger.warning("No actions to replay in this profile.")
                return

            hwnd = None
            if self.profile.replay_mode == "direct_message":
                hwnd = self._find_target_hwnd()
                logger.info(f"Resolved target window HWND: {hwnd} for direct posting.")

            previous_active_keys = set()
            previous_x, previous_y = self._get_current_mouse_pos()
            current_x, current_y = previous_x, previous_y
            while self.is_running:
                start_time = time.perf_counter()
                last_emit_time = 0
    
                last_target_elapsed = 0.0
    
                for i, act in enumerate(actions):
                    if not self.is_running:
                        break

                    # High-precision wait/sleep to event/frame timestamp
                    jitter = 0
                    if self.profile.replay_mode == "send_input" and self.profile.jitter_range_ms > 0:
                        jitter = random.randint(-self.profile.jitter_range_ms, self.profile.jitter_range_ms)

                    target_elapsed = (act.time_ms + jitter) / 1000.0
                    if target_elapsed < 0:
                        target_elapsed = 0.0

                    # Ensure order is preserved and guarantee at least 1ms delay between inputs to prevent engine drops
                    min_gap = 0.001
                    if target_elapsed <= last_target_elapsed + min_gap:
                        target_elapsed = last_target_elapsed + min_gap
                
                    last_target_elapsed = target_elapsed

                    while True:
                        if not self.is_running:
                            break
                        elapsed = time.perf_counter() - start_time
                    
                        elapsed_ms = int(elapsed * 1000)
                        if elapsed_ms - last_emit_time >= 50:  # Throttled to 50ms to save CPU and GUI queue size
                            self.progress_updated.emit(elapsed_ms)
                            last_emit_time = elapsed_ms
                        
                        remaining = target_elapsed - elapsed
                        if remaining <= 0:
                            break
                        elif remaining > 0.002:
                            time.sleep(remaining - 0.001)
                        else:
                            time.sleep(0)  # Yield CPU to prevent 100% spin-lock

                    if not self.is_running:
                        break

                    # Replay Event/Frame
                    if act.event_type == "frame":
                        current_active_keys = set(act.active_keys)
                    
                        # 1. Key Downs (in current, not in previous)
                        for vk in current_active_keys - previous_active_keys:
                            if vk in (0x01, 0x02, 0x04, 0x05, 0x06): # Mouse buttons
                                if hwnd:
                                    self._simulate_mouse_direct_msg(hwnd, "mouse_down", vk, act.x, act.y)
                                else:
                                    self._simulate_mouse_click(vk, True, act.x, act.y)
                            else: # Keyboard keys
                                if hwnd:
                                    self._simulate_keyboard_direct_msg(hwnd, vk, True)
                                else:
                                    self._simulate_keyboard(vk, True)

                        # 2. Key Ups (in previous, not in current)
                        for vk in previous_active_keys - current_active_keys:
                            if vk in (0x01, 0x02, 0x04, 0x05, 0x06): # Mouse buttons
                                if hwnd:
                                    self._simulate_mouse_direct_msg(hwnd, "mouse_up", vk, act.x, act.y)
                                else:
                                    self._simulate_mouse_click(vk, False, act.x, act.y)
                            else: # Keyboard keys
                                if hwnd:
                                    self._simulate_keyboard_direct_msg(hwnd, vk, False)
                                else:
                                    self._simulate_keyboard(vk, False)

                        # 3. Mouse Moves
                        if act.x != previous_x or act.y != previous_y:
                            if hwnd:
                                self._simulate_mouse_direct_msg(hwnd, "mouse_move", 0, act.x, act.y)
                            else:
                                self._simulate_mouse_move_absolute(act.x, act.y)
                            
                        previous_active_keys = current_active_keys
                        previous_x, previous_y = act.x, act.y

                    else:
                        # Traditional Event playback
                        if act.event_type in ("key_down", "key_up"):
                            is_down = (act.event_type == "key_down")
                            if hwnd:
                                self._simulate_keyboard_direct_msg(hwnd, act.key_code, is_down)
                            else:
                                self._simulate_keyboard(act.key_code, is_down)
                        elif act.event_type == "mouse_move":
                            if hwnd:
                                self._simulate_mouse_direct_msg(hwnd, "mouse_move", 0, act.x, act.y)
                            else:
                                if self.profile.smooth_mouse:
                                    dx = act.x - current_x
                                    dy = act.y - current_y
                                    dist = math.hypot(dx, dy)
                                    if dist > 15:
                                        self._simulate_bezier_mouse_move(current_x, current_y, act.x, act.y)
                                    else:
                                        self._simulate_mouse_move_absolute(act.x, act.y)
                                else:
                                    self._simulate_mouse_move_absolute(act.x, act.y)
                            current_x, current_y = act.x, act.y
                        elif act.event_type in ("mouse_down", "mouse_up"):
                            is_down = (act.event_type == "mouse_down")
                            if hwnd:
                                self._simulate_mouse_direct_msg(hwnd, act.event_type, act.key_code, act.x, act.y)
                            else:
                                self._simulate_mouse_click(act.key_code, is_down, act.x, act.y)
                                current_x, current_y = act.x, act.y
                
                # Loop condition
                if not self.is_running or self.profile.play_mode == "play_once":
                    break
                # If we are looping, add a tiny breather to avoid locking up on empty or very short macros
                if self.is_running:
                    time.sleep(0.01)

        except Exception as e:
            logger.error(f"Error during macro playback: {e}")
        finally:
            if winmm:
                winmm.timeEndPeriod(1)
            if self.is_running and actions:
                max_time = max(act.time_ms for act in actions)
                self.progress_updated.emit(max_time)
            self.is_running = False
            self.finished_playback.emit()
            logger.info("Macro playback thread finished.")

    def _get_current_mouse_pos(self):
        class POINT(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def _simulate_keyboard(self, vk, is_down):
        """Simulate keyboard event using hardware ScanCode for anti-detection."""
        scan = map_vk_to_scan(vk)
        flags = KEYEVENTF_SCANCODE
        if not is_down:
            flags |= KEYEVENTF_KEYUP
        if vk in EXTENDED_KEYS:
            flags |= KEYEVENTF_EXTENDEDKEY

        extra = user32.GetMessageExtraInfo()

        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.ki = KEYBDINPUT(
            wVk=0, 
            wScan=scan,
            dwFlags=flags,
            time=0,
            dwExtraInfo=extra
        )
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))

    def _simulate_mouse_move_absolute(self, x, y):
        """Move mouse instantly using absolute virtual desktop coordinates."""
        screen_w = user32.GetSystemMetrics(0)
        screen_h = user32.GetSystemMetrics(1)
        if screen_w <= 0 or screen_h <= 0:
            return

        nx = int((x * 65536) / screen_w)
        ny = int((y * 65536) / screen_h)

        extra = user32.GetMessageExtraInfo()

        inp = INPUT()
        inp.type = INPUT_MOUSE
        inp.mi = MOUSEINPUT(
            dx=nx,
            dy=ny,
            mouseData=0,
            dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE,
            time=0,
            dwExtraInfo=extra
        )
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))

    def _simulate_bezier_mouse_move(self, sx, sy, ex, ey):
        """Moves mouse along a smooth Bezier curve."""
        mx = (sx + ex) / 2
        my = (sy + ey) / 2
        
        dist = math.hypot(ex - sx, ey - sy)
        offset_limit = max(5, int(dist * 0.15))
        cx = mx + random.randint(-offset_limit, offset_limit)
        cy = my + random.randint(-offset_limit, offset_limit)

        steps = max(4, min(12, int(dist / 12)))
        for step in range(1, steps + 1):
            t = step / float(steps)
            bx = int((1-t)**2 * sx + 2*(1-t)*t * cx + t**2 * ex)
            by = int((1-t)**2 * sy + 2*(1-t)*t * cy + t**2 * ey)
            self._simulate_mouse_move_absolute(bx, by)
            time.sleep(0.004)

        self._simulate_mouse_move_absolute(ex, ey)

    def _simulate_mouse_click(self, btn_code, is_down, x, y):
        """Simulate mouse button press/release at absolute position."""
        self._simulate_mouse_move_absolute(x, y)
        time.sleep(0.002)

        flags = MOUSEEVENTF_ABSOLUTE
        mouseData = 0
        if btn_code == 0x01: # Left
            flags |= MOUSEEVENTF_LEFTDOWN if is_down else MOUSEEVENTF_LEFTUP
        elif btn_code == 0x02: # Right
            flags |= MOUSEEVENTF_RIGHTDOWN if is_down else MOUSEEVENTF_RIGHTUP
        elif btn_code == 0x04: # Middle
            flags |= MOUSEEVENTF_MIDDLEDOWN if is_down else MOUSEEVENTF_MIDDLEUP
        elif btn_code == 0x05: # Mouse Button 4 (XBUTTON1)
            flags |= MOUSEEVENTF_XDOWN if is_down else MOUSEEVENTF_XUP
            mouseData = 1
        elif btn_code == 0x06: # Mouse Button 5 (XBUTTON2)
            flags |= MOUSEEVENTF_XDOWN if is_down else MOUSEEVENTF_XUP
            mouseData = 2
        else:
            return

        extra = user32.GetMessageExtraInfo()

        screen_w = user32.GetSystemMetrics(0)
        screen_h = user32.GetSystemMetrics(1)
        if screen_w <= 0 or screen_h <= 0:
            return
        nx = int((x * 65536) / screen_w)
        ny = int((y * 65536) / screen_h)

        inp = INPUT()
        inp.type = INPUT_MOUSE
        inp.mi = MOUSEINPUT(
            dx=nx,
            dy=ny,
            mouseData=mouseData,
            dwFlags=flags,
            time=0,
            dwExtraInfo=extra
        )
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))




class MacroRecordThread(QThread):
    """High-precision background thread that samples keyboard/mouse frame snapshots every 10ms."""
    def __init__(self, manager, interval_ms=10, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.interval_ms = interval_ms
        self.is_running = False

    def stop(self):
        self.is_running = False

    def run(self):
        self.is_running = True
        if winmm:
            winmm.timeBeginPeriod(1)
            
        start_time = time.perf_counter()
        frame_index = 0
        
        try:
            while self.is_running:
                # Calculate next wake-up time
                frame_time = start_time + (frame_index * (self.interval_ms / 1000.0))
                now = time.perf_counter()
                sleep_time = frame_time - now
                if sleep_time > 0.002:
                    time.sleep(sleep_time - 0.001)
                while time.perf_counter() < frame_time:
                    if not self.is_running:
                        break
                    time.sleep(0)  # Yield CPU to prevent 100% spin
                    
                if not self.is_running:
                    break
                    
                # Capture snapshot
                elapsed_ms = int((time.perf_counter() - start_time) * 1000)
                self.manager._record_frame_snapshot(elapsed_ms)
                frame_index += 1
        finally:
            if winmm:
                winmm.timeEndPeriod(1)
            self.is_running = False


class MacroManager(QObject):
    """Singleton Manager controlling all macro recordings, replays, and file stores."""
    _instance = None
    state_changed = Signal(str, str) # state ("idle", "recording", "replaying"), current_profile_name
    profiles_updated = Signal()
    playback_progress = Signal(int)
    
    # Thread-safe triggers for cross-thread calls from GlobalInputHookThread
    _trigger_start_replay = Signal(str)
    _trigger_stop_replay = Signal()
    _trigger_start_record = Signal(object)
    _trigger_stop_record = Signal(object)

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(MacroManager, cls).__new__(cls)
        return cls._instance

    def __init__(self, parent=None):
        if hasattr(self, "_initialized"):
            return
        super().__init__(parent)
        self._initialized = True
        
        self.profiles = {} # profile_id -> MacroProfile
        self.current_profile_id = None
        self.current_category = "Default"
        self.input_hook = None
        
        # Ensure single instance
        if not hasattr(self, "initialized"):
            self.initialized = True
            
            # Connect internal signals to ensure thread-safety across thread boundaries
            self._trigger_start_replay.connect(self.start_replay)
            self._trigger_stop_replay.connect(self.stop_replay)
            self._trigger_start_record.connect(self.start_recording)
            self._trigger_stop_record.connect(self.stop_recording)
            
            self.lock = threading.RLock()
            self.state = "idle" # idle, recording, replaying
        self.record_start_time = 0
        self.recorded_actions = []
        
        # Active recording / replay threads
        self.record_frame_thread = None
        self.replay_thread = None
        
        # Snapshot state variables
        self.recording_active_keys = set()
        self.recording_mouse_pos = (0, 0)
        
        self.lock = threading.Lock()
        
        # Setup paths
        self.macros_dir = os.path.join(os.environ.get("APPDATA", ""), "CoreCommander", "macros")
        os.makedirs(self.macros_dir, exist_ok=True)
        
        # Load profiles
        self.load_all_profiles()

        # Pre-cache QuickChatManager to avoid dynamic instantiation overhead on every keypress
        try:
            from core_commander.core.quick_chat_manager import QuickChatManager
            self.qc_mgr = QuickChatManager()
        except Exception as e:
            logger.error(f"Failed to pre-cache QuickChatManager in MacroManager: {e}")
            self.qc_mgr = None

    def bind_to_input_hook(self, input_hook_thread):
        """Hook GlobalInputHookThread callback to manage global recording and replay triggers."""
        self.input_hook = input_hook_thread
        input_hook_thread.global_key_callback = self._global_key_callback
        input_hook_thread.global_mouse_callback = self._global_mouse_callback
        logger.info("MacroManager bound successfully to GlobalInputHookThread.")

    def get_current_profile(self):
        return self.profiles.get(self.current_profile_id)

    def get_all_categories(self):
        cats = set()
        for prof in self.profiles.values():
            if hasattr(prof, "category") and prof.category:
                cats.add(prof.category)
            else:
                cats.add("Default")
        if not cats:
            cats.add("Default")
        return sorted(list(cats))

    def load_all_profiles(self):
        """Read all ccmacro files in local macro directory."""
        self.profiles.clear()
        try:
            for file in os.listdir(self.macros_dir):
                if file.endswith(".ccmacro"):
                    filepath = os.path.join(self.macros_dir, file)
                    try:
                        with open(filepath, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            profile = MacroProfile.from_dict(data)
                            self.profiles[profile.profile_id] = profile
                    except Exception as fe:
                        logger.error(f"Failed loading macro file {file}: {fe}")
            
            if not self.profiles:
                default_prof = MacroProfile(name="默认宏配置")
                self.save_profile(default_prof)
                self.profiles[default_prof.profile_id] = default_prof

            self.current_profile_id = list(self.profiles.keys())[0]
            current_prof = self.get_current_profile()
            if current_prof:
                self.current_category = current_prof.category
            else:
                self.current_category = "Default"
            logger.info(f"Loaded {len(self.profiles)} macro profiles from storage.")
        except Exception as e:
            logger.error(f"Error loading macro directory: {e}")

    def save_profile(self, profile):
        """Save a macro profile config to local disk."""
        filepath = os.path.join(self.macros_dir, f"{profile.profile_id}.ccmacro")
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(profile.to_dict(), f, indent=4, ensure_ascii=False)
            self.profiles[profile.profile_id] = profile
            logger.info(f"Successfully saved macro profile: {profile.name}")
            self.profiles_updated.emit()
        except Exception as e:
            logger.error(f"Failed saving macro profile {profile.name}: {e}")

    def create_new_profile(self, name="New Macro Profile"):
        prof = MacroProfile(name=name)
        prof.category = self.current_category
        self.save_profile(prof)
        self.current_profile_id = prof.profile_id
        return prof

    def delete_profile(self, profile_id):
        if len(self.profiles) <= 1:
            return False
            
        filepath = os.path.join(self.macros_dir, f"{profile_id}.ccmacro")
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception as e:
                logger.error(f"Error deleting file {filepath}: {e}")

        if profile_id in self.profiles:
            del self.profiles[profile_id]

        if self.current_profile_id == profile_id:
            self.current_profile_id = list(self.profiles.keys())[0]

        logger.info(f"Deleted profile: {profile_id}")
        self.profiles_updated.emit()
        return True

    def import_profile(self, filepath):
        """Load external macro profile file into application context."""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            data["profile_id"] = f"macro_{int(time.time())}_{random.randint(1000, 9999)}"
            data["name"] = f"[导入] {data.get('name', 'Macro')}"
            
            profile = MacroProfile.from_dict(data)
            profile.category = self.current_category
            self.save_profile(profile)
            self.current_profile_id = profile.profile_id
            return profile
        except Exception as e:
            logger.error(f"Failed importing macro profile: {e}")
            raise e

    def export_profile(self, profile_id, destination_path):
        """Export specified macro profile config to selected file path."""
        profile = self.profiles.get(profile_id)
        if not profile:
            return False
        try:
            with open(destination_path, "w", encoding="utf-8") as f:
                json.dump(profile.to_dict(), f, indent=4, ensure_ascii=False)
            logger.info(f"Exported macro: {profile.name} to {destination_path}")
            return True
        except Exception as e:
            logger.error(f"Failed exporting macro profile: {e}")
            return False

    # --- Recording Control ---
    def start_recording(self, input_hook_thread):
        """Prepares GlobalInputHookThread callback to record events or frames."""
        with self.lock:
            if self.state != "idle":
                return False

            self.state = "recording"
            self.recorded_actions.clear()
            self.recording_active_keys.clear()
            
            # Initialize mouse pos to current coordinates
            class POINT(ctypes.Structure):
                _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]
            pt = POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            self.recording_mouse_pos = (pt.x, pt.y)

            self.record_start_time = time.perf_counter()
            profile = self.get_current_profile()
            record_mode = profile.record_mode if profile else "event"
            
            if record_mode == "frame":
                input_hook_thread.record_callback = self._on_frame_input_event_captured
                input_hook_thread.recording_mode = True
                
                # Start frame recording clock thread (10ms interval)
                self.record_frame_thread = MacroRecordThread(self, interval_ms=10)
                self.record_frame_thread.start()
            else:
                input_hook_thread.record_callback = self._on_input_event_captured
                input_hook_thread.recording_mode = True
            
            prof_name = profile.name if profile else "Macro"
            self.state_changed.emit("recording", prof_name)
            logger.info(f"Macro recording started (mode: {record_mode}).")
            return True

    def stop_recording(self, input_hook_thread):
        """Cleans input hooks and saves recorded stream to current profile."""
        with self.lock:
            if self.state != "recording":
                return False

            # Stop frame record thread if active
            if self.record_frame_thread:
                self.record_frame_thread.stop()
                self.record_frame_thread.wait(1000)
                self.record_frame_thread = None

            input_hook_thread.recording_mode = False
            input_hook_thread.record_callback = None
            
            self.state = "idle"
            
            profile = self.get_current_profile()
            if profile:
                # Filter out the stop hotkey F10 (0x79)
                actions_filtered = []
                record_mode = getattr(profile, "record_mode", "event")
                if record_mode == "frame":
                    for act in self.recorded_actions:
                        if act.event_type == "frame":
                            if 0x79 in act.active_keys:
                                act.active_keys = [k for k in act.active_keys if k != 0x79]
                        actions_filtered.append(act)
                    
                    # Clean up trailing Left Click (0x01) from the final frames (which clicked "Stop Recording" button)
                    for act in reversed(actions_filtered):
                        if 0x01 in act.active_keys:
                            act.active_keys = [k for k in act.active_keys if k != 0x01]
                        else:
                            break
                else:
                    actions_filtered = [act for act in self.recorded_actions if act.key_code != 0x79]
                    # Clean up trailing Left Click mouse down/up at the very end
                    while actions_filtered and actions_filtered[-1].event_type in ("mouse_up", "mouse_down") and actions_filtered[-1].key_code == 0x01:
                        actions_filtered.pop()
                
                # Shift timeline to start first action at 0ms
                if actions_filtered:
                    first_time = 0
                    if record_mode == "frame":
                        first_act = None
                        start_x, start_y = actions_filtered[0].x, actions_filtered[0].y
                        for act in actions_filtered:
                            if act.active_keys or act.x != start_x or act.y != start_y:
                                first_act = act
                                break
                        if first_act:
                            first_time = first_act.time_ms
                            actions_filtered = [act for act in actions_filtered if act.time_ms >= first_time]
                    else:
                        first_time = actions_filtered[0].time_ms
                    
                    if first_time > 0:
                        for act in actions_filtered:
                            act.time_ms -= first_time
                
                profile.actions = actions_filtered
                self.save_profile(profile)
                
            self.state_changed.emit("idle", profile.name if profile else "")
            logger.info(f"Macro recording stopped. Captured {len(self.recorded_actions)} steps/frames.")
            return True

    def _is_foreground_our_process(self):
        """Checks if the currently active foreground window belongs to our own process."""
        try:
            import win32gui
            import win32process
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd:
                return False
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            return pid == os.getpid()
        except Exception:
            return False

    def _on_input_event_captured(self, event_type, code, x, y, timestamp_ms):
        """Callback fed by GlobalInputHookThread when physical inputs occur in traditional event mode."""
        if self.state != "recording":
            return
            
        current_time = time.perf_counter()
        delta_ms = int((current_time - self.record_start_time) * 1000)
        
        from core_commander.core.input_hook import VK_NAMES
        key_name = ""
        if event_type in ("key_down", "key_up"):
            key_name = VK_NAMES.get(code, f"Key {code}")
        elif event_type in ("mouse_down", "mouse_up"):
            key_name = VK_NAMES.get(code, f"Mouse Button {code}")

        action = MacroAction(
            time_ms=delta_ms,
            event_type=event_type,
            key_code=code,
            key_name=key_name,
            x=x,
            y=y
        )
        self.recorded_actions.append(action)

    def _on_frame_input_event_captured(self, event_type, code, x, y, timestamp_ms):
        """Maintains the active keys and mouse state based on hook callbacks during frame recording."""
        if self.state != "recording":
            return
            
        with self.lock:
            if event_type == "key_down":
                self.recording_active_keys.add(code)
            elif event_type == "key_up":
                self.recording_active_keys.discard(code)
            elif event_type == "mouse_down":
                self.recording_active_keys.add(code) # Mouse buttons are treated as keys for ease of state transitions
                self.recording_mouse_pos = (x, y)
            elif event_type == "mouse_up":
                self.recording_active_keys.discard(code)
                self.recording_mouse_pos = (x, y)
            elif event_type == "mouse_move":
                self.recording_mouse_pos = (x, y)

    def _record_frame_snapshot(self, elapsed_ms):
        """Called by MacroRecordThread at 10ms ticks to save the snapshot."""
        with self.lock:
            if self.state != "recording":
                return
                
            # We allow frame snapshots to be recorded when our app is focused (keyboard key logging)
                
            mx, my = self.recording_mouse_pos
            action = MacroAction(
                time_ms=elapsed_ms,
                event_type="frame",
                x=mx,
                y=my,
                active_keys=list(self.recording_active_keys)
            )
            self.recorded_actions.append(action)

    # --- Replaying Control ---
    def start_replay(self, profile_id=None):
        """Initiates async playback thread."""
        with self.lock:
            if self.state != "idle":
                return False

            pid = profile_id if profile_id else self.current_profile_id
            profile = self.profiles.get(pid)
            if not profile or not profile.actions:
                logger.warning("No profile or empty actions for replay.")
                return False

            self.state = "replaying"
            self.state_changed.emit("replaying", profile.name)
            
            self.replay_thread = MacroReplayThread(profile)
            self.replay_thread.finished.connect(self.replay_thread.deleteLater)
            self.replay_thread.progress_updated.connect(self.playback_progress.emit)
            self.replay_thread.finished_playback.connect(self._on_replay_finished)
            self.replay_thread.start()
            return True

    def stop_replay(self):
        """Terminates active playback thread instantly."""
        with self.lock:
            if self.state != "replaying":
                return False

            if self.replay_thread:
                self.replay_thread.stop()
                self.replay_thread.wait(1000)
                self.replay_thread = None
                
            self.state = "idle"
            profile = self.get_current_profile()
            self.state_changed.emit("idle", profile.name if profile else "")
            logger.info("Macro replay aborted.")
            return True

    def _on_replay_finished(self):
        with self.lock:
            self.state = "idle"
            # Do NOT set self.replay_thread = None here immediately to avoid premature Python object
            # destruction while the underlying C++ thread is still exiting, which causes segmentation faults.
            profile = self.get_current_profile()
            self.state_changed.emit("idle", profile.name if profile else "")
            logger.info("Macro replay completed successfully.")

    def _is_target_process_active(self):
        """Checks if the target process configured in AppSettings is currently in the foreground."""
        try:
            if not hasattr(self, "settings") or self.settings is None:
                from core_commander.config.settings import AppSettings
                self.settings = AppSettings()
            target_exe = self.settings.target_process_name
            
            import win32gui
            import win32process
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd:
                return False
                
            # Cache the HWND check to prevent severe input lag (stuttering/freezing) caused by psutil
            if getattr(self, '_cached_fg_hwnd', None) == hwnd:
                return getattr(self, '_cached_fg_result', False)
                
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            
            # Prevent triggering/hijacking when our own app is focused
            if pid == os.getpid():
                return False
                
            if not target_exe:
                # If target process is empty, trigger globally (except our own app)
                return True
                
            target_exe = target_exe.lower().strip()
            import psutil
            try:
                proc = psutil.Process(pid)
                proc_name = proc.name().lower().strip()
                result = (proc_name == target_exe)
                self._cached_fg_hwnd = hwnd
                self._cached_fg_result = result
                return result
            except psutil.AccessDenied:
                # Fallback to checking Window Title if we lack permissions to read process name (e.g. anti-cheat games)
                title = win32gui.GetWindowText(hwnd).lower()
                target_base = target_exe.replace(".exe", "")
                result = (target_base in title)
                self._cached_fg_hwnd = hwnd
                self._cached_fg_result = result
                return result
            except Exception:
                self._cached_fg_hwnd = hwnd
                self._cached_fg_result = False
                return False
        except Exception:
            return True # Fallback

    # --- Global Input Hook Event Filters (Anti-Detection and Control Hotkeys) ---
    def _global_key_callback(self, vk_code, is_down):
        """
        Global keyboard hook listener callback.
        Handles recording control (F10) and replay triggering.
        Returns True to block/consume the keystroke.
        """
        if vk_code == 0x79: # F10
            if is_down:
                if self.state == "idle":
                    from core_commander.core.license import license_manager
                    if not license_manager.is_active:
                        return False
                    # Allow starting recording globally via F10
                    self._trigger_start_record.emit(self.input_hook)
                    return True
                elif self.state == "recording":
                    # Always allow stopping recording globally via F10
                    self._trigger_stop_record.emit(self.input_hook)
                    return True
            
        if self.state == "idle":
            # Prevent triggering/hijacking when our own app is focused
            try:
                import win32gui
                import win32process
                hwnd = win32gui.GetForegroundWindow()
                if hwnd:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    if pid == os.getpid():
                        return False
            except Exception:
                pass
                
            # Check Quick Chat triggers globally (except in our own app)
            try:
                if self.qc_mgr and self.qc_mgr.check_and_trigger(vk_code, is_down, is_keyboard=True):
                    return True
            except Exception as e:
                logger.error(f"Error checking quick chat in global key hook: {e}")
                
            # Match macro hotkeys first to provide detailed diagnostic logging
            for prof in self.profiles.values():
                for hk in prof.hotkeys:
                    if hk.get("type") == "keyboard" and vk_code == hk.get("code"):
                        from core_commander.core.license import license_manager
                        if not license_manager.is_active:
                            return False
                            
                        # Check category active status
                        if prof.category != self.current_category:
                            if is_down:
                                logger.info(f"Macro '{prof.name}' hotkey pressed but ignored because its category '{prof.category}' is not the currently active category ('{self.current_category}').")
                            return True # Intercept key
                            
                        # Check target process active status
                        if not self._is_target_process_active():
                            if is_down:
                                logger.info(f"Macro '{prof.name}' hotkey pressed but ignored because the target process is not in the foreground.")
                            return False # Do not intercept, let it pass to other apps
                            
                        if is_down:
                            self._trigger_start_replay.emit(prof.profile_id)
                        return True
                
        elif self.state == "replaying":
            replaying_prof = None
            if self.replay_thread:
                replaying_prof = self.replay_thread.profile
            else:
                replaying_prof = self.get_current_profile()
                
            is_hotkey = False
            if replaying_prof:
                for hk in replaying_prof.hotkeys:
                    if hk.get("type") == "keyboard" and vk_code == hk.get("code"):
                        is_hotkey = True
                        break
            if vk_code == 0x1B or is_hotkey:
                if replaying_prof and replaying_prof.play_mode == "hold_loop" and is_hotkey:
                    if not is_down: # stop on release
                        self._trigger_stop_replay.emit()
                elif replaying_prof and replaying_prof.play_mode == "toggle_loop" and is_hotkey:
                    if is_down: # toggle stop
                        self._trigger_stop_replay.emit()
                else:
                    if is_down: # default stop on press (and stop on ESC press)
                        self._trigger_stop_replay.emit()
                return True

        return False

    def _global_mouse_callback(self, code, is_down):
        """
        Global mouse click hook listener callback.
        Handles replay triggering by mouse buttons.
        """
        if self.state == "idle":
            # Prevent triggering when our own app is focused
            try:
                import win32gui
                import win32process
                hwnd = win32gui.GetForegroundWindow()
                if hwnd:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    if pid == os.getpid():
                        return False
            except Exception:
                pass

            # Check Quick Chat triggers globally
            try:
                if self.qc_mgr and self.qc_mgr.check_and_trigger(code, is_down, is_keyboard=False):
                    return True
            except Exception as e:
                logger.error(f"Error checking quick chat in global mouse hook: {e}")
                
            # Match macro hotkeys first to provide detailed diagnostic logging
            for prof in self.profiles.values():
                for hk in prof.hotkeys:
                    if hk.get("type") == "mouse" and code == hk.get("code"):
                        # Check category active status
                        if prof.category != self.current_category:
                            if is_down:
                                logger.info(f"Macro '{prof.name}' mouse hotkey pressed but ignored because its category '{prof.category}' is not the currently active category ('{self.current_category}').")
                            return True # Intercept mouse button
                            
                        # Check target process active status
                        if not self._is_target_process_active():
                            if is_down:
                                logger.info(f"Macro '{prof.name}' mouse hotkey pressed but ignored because the target process is not in the foreground.")
                            return False # Do not intercept, let it pass to other apps
                            
                        if is_down:
                            self._trigger_start_replay.emit(prof.profile_id)
                        return True
                
        elif self.state == "replaying":
            replaying_prof = None
            if self.replay_thread:
                replaying_prof = self.replay_thread.profile
            else:
                replaying_prof = self.get_current_profile()
                
            is_hotkey = False
            if replaying_prof:
                for hk in replaying_prof.hotkeys:
                    if hk.get("type") == "mouse" and code == hk.get("code"):
                        is_hotkey = True
                        break
            if is_hotkey:
                if replaying_prof and replaying_prof.play_mode == "hold_loop":
                    if not is_down: # stop on release
                        self._trigger_stop_replay.emit()
                elif replaying_prof and replaying_prof.play_mode == "toggle_loop":
                    if is_down: # toggle stop
                        self._trigger_stop_replay.emit()
                else:
                    if is_down: # default stop on press
                        self._trigger_stop_replay.emit()
                return True
                
        return False
