"""Hand a finished download to the desktop: put the file on the clipboard, or show its folder."""
from __future__ import annotations

import os
import subprocess
import sys
from urllib.parse import quote


def _copy_file_windows(filepath: str) -> bool:
    import ctypes
    import struct
    import time
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterClipboardFormatW.restype = wintypes.UINT

    GMEM_MOVEABLE = 0x0002
    CF_HDROP = 15

    def global_blob(data: bytes):
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            kernel32.GlobalFree(handle)
            return None
        ctypes.memmove(pointer, data, len(data))
        kernel32.GlobalUnlock(handle)
        return handle

    # DROPFILES header (offset to the list, point, fNC, fWide=1) + double-null-terminated wide paths
    paths = (os.path.abspath(filepath) + "\0\0").encode("utf-16-le")
    drop = global_blob(struct.pack("<IiiII", 20, 0, 0, 0, 1) + paths)
    effect = global_blob(struct.pack("<I", 1))  # DROPEFFECT_COPY: paste copies, not moves
    if not drop:
        return False

    for _ in range(10):  # another app may hold the clipboard for a moment
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        kernel32.GlobalFree(drop)
        if effect:
            kernel32.GlobalFree(effect)
        return False
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_HDROP, drop):
            kernel32.GlobalFree(drop)
            return False
        if effect:
            fmt = user32.RegisterClipboardFormatW("Preferred DropEffect")
            if not fmt or not user32.SetClipboardData(fmt, effect):
                kernel32.GlobalFree(effect)
        return True
    finally:
        user32.CloseClipboard()


def copy_file_to_clipboard(filepath: str) -> bool:
    """
    Put the file itself (not its path) on the clipboard, so it pastes into a
    file manager or chat app. Best effort — False when nothing supports it.
    """
    try:
        if sys.platform == "win32":
            return _copy_file_windows(filepath)
        if sys.platform == "darwin":
            escaped = filepath.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.run(["osascript", "-e", f'set the clipboard to (POSIX file "{escaped}")'],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=2, check=True)
            return True
        uri = f"file://{quote(os.path.abspath(filepath))}\n".encode()
        for command in (["wl-copy", "--type", "text/uri-list"], ["xclip", "-selection", "clipboard", "-t",
                                                                 "text/uri-list"]):
            try:
                subprocess.run(command, input=uri, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=2, check=True)
                return True
            except (OSError, subprocess.SubprocessError):
                continue  # tool missing — try the next one
    except Exception:
        pass
    return False


def reveal_in_file_manager(filepath: str) -> bool:
    """Open the download's folder (Finder highlights the file itself)."""
    folder = os.path.dirname(os.path.abspath(filepath))
    try:
        if sys.platform == "win32":
            os.startfile(folder)  # explorer's /select is unreliable without an open window
            return True
        command = ["open", "-R", filepath] if sys.platform == "darwin" else ["xdg-open", folder]
        # detached: the file manager outlives yoinks and must not draw on our screen
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return True
    except OSError:
        return False
