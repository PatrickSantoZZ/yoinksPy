from __future__ import annotations

import subprocess
import sys


def _read_windows() -> str:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]

    CF_UNICODETEXT = 13
    if not user32.OpenClipboard(None):
        return ""
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return ""
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


_COMMANDS = (
    [["pbpaste"]]
    if sys.platform == "darwin"
    else [["wl-paste", "--no-newline"], ["xclip", "-selection", "clipboard", "-o"], ["xsel", "--clipboard", "--output"]]
)


def read_clipboard() -> str:
    if sys.platform == "win32":
        try:
            return _read_windows()
        except Exception:
            return ""
    for command in _COMMANDS:
        try:
            result = subprocess.run(command, capture_output=True, timeout=0.5, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            continue  # tool missing — try the next one
        if result.returncode == 0:
            return result.stdout.decode("utf-8", "replace")
    return ""
