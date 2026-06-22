import sys
import ctypes

# Only mock and patch when running on non-Windows platforms (Linux/macOS)
if sys.platform != "win32":
    from unittest.mock import MagicMock

    # 1. Patch ctypes
    if not hasattr(ctypes, "windll"):
        class WinDLLMock:
            def __getattr__(self, name):
                return MagicMock()
        ctypes.windll = WinDLLMock()

    if not hasattr(ctypes, "wintypes"):
        # Create a mock wintypes module/object
        wintypes_mock = MagicMock()
        
        # Populate basic windll-compatible types to prevent common import or function signature errors
        wintypes_mock.DWORD = ctypes.c_ulong
        wintypes_mock.WORD = ctypes.c_ushort
        wintypes_mock.BYTE = ctypes.c_ubyte
        wintypes_mock.HWND = ctypes.c_void_p
        wintypes_mock.HANDLE = ctypes.c_void_p
        wintypes_mock.BOOL = ctypes.c_int
        wintypes_mock.LPCWSTR = ctypes.c_wchar_p
        wintypes_mock.LPWSTR = ctypes.c_wchar_p
        wintypes_mock.LPCSTR = ctypes.c_char_p
        wintypes_mock.LPSTR = ctypes.c_char_p
        
        ctypes.wintypes = wintypes_mock
        sys.modules["ctypes.wintypes"] = wintypes_mock

    # 2. Mock imports of platform-specific libraries
    mock_modules = [
        "winreg",
        "win32service",
        "win32serviceutil",
        "win32api",
        "win32con",
        "pythoncom",
        "wmi",
        "sounddevice",
    ]

    for mod_name in mock_modules:
        if mod_name not in sys.modules:
            mock_mod = MagicMock()
            sys.modules[mod_name] = mock_mod
