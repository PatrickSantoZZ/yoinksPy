"""
A tiny stand-in for ink: blocks of styled text rows, centered layout, a
full-screen differential writer, keyboard/mouse parsing and raw-mode
terminal handling for Windows and POSIX — stdlib only.
"""
from __future__ import annotations

import os
import queue
import re
import shutil
import sys
import threading
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, List, Optional, Tuple

# ── styled text ──────────────────────────────────────────────────────────

Click = Tuple[Callable[[], None], int]  # (action, extra cells left/right that still hit)


class Seg:
    __slots__ = ("text", "fg", "bg", "bold", "dim", "inverse", "click")

    def __init__(self, text: str, fg: Optional[str] = None, *, bg: Optional[str] = None, bold: bool = False,
                 dim: bool = False, inverse: bool = False, click: Optional[Click] = None) -> None:
        self.text = text
        self.fg = fg
        self.bg = bg
        self.bold = bold
        self.dim = dim
        self.inverse = inverse
        self.click = click


@lru_cache(maxsize=4096)
def char_width(ch: str) -> int:
    if unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def text_width(text: str) -> int:
    return sum(char_width(c) for c in text)


def row_width(row: List[Seg]) -> int:
    return sum(text_width(s.text) for s in row)


Row = List[Seg]


class Block:
    """A rectangle of rows; rows shorter than `width` are left-aligned."""

    def __init__(self, rows: List[Row], width: Optional[int] = None) -> None:
        self.rows = rows
        self.width = max((row_width(r) for r in rows), default=0) if width is None else width

    @property
    def height(self) -> int:
        return len(self.rows)


def line(*segs: Seg) -> Block:
    return Block([list(segs)])


def gap(lines: int = 1) -> Block:
    return Block([[] for _ in range(lines)])


def _pad(n: int, click: Optional[Click] = None) -> Row:
    return [Seg(" " * n, click=click)] if n > 0 else []


def clip_row(row: Row, width: int) -> Row:
    """Cut a row to `width` cells and pad it out to exactly that width."""
    out: Row = []
    used = 0
    for seg in row:
        if used >= width:
            break
        w = text_width(seg.text)
        if used + w <= width:
            out.append(seg)
            used += w
            continue
        kept = []
        for ch in seg.text:
            cw = char_width(ch)
            if used + cw > width:
                break
            kept.append(ch)
            used += cw
        if kept:
            out.append(Seg("".join(kept), seg.fg, bg=seg.bg, bold=seg.bold, dim=seg.dim,
                           inverse=seg.inverse, click=seg.click))
        break
    return out + _pad(width - used)


def fixed(block: Block, width: int) -> Block:
    width = max(0, width)
    return Block([clip_row(r, width) for r in block.rows], width)


def vstack(blocks: List[Block], center: bool = True) -> Block:
    width = max((b.width for b in blocks), default=0)
    rows: List[Row] = []
    for b in blocks:
        # yoga rounds a .5 offset up
        off = (width - b.width + 1) // 2 if center else 0
        for r in b.rows:
            rows.append(_pad(off) + r + _pad(width - off - row_width(r)))
    return Block(rows, width)


def hstack(blocks: List[Block]) -> Block:
    height = max((b.height for b in blocks), default=0)
    rows: List[Row] = []
    for i in range(height):
        row: Row = []
        for b in blocks:
            r = b.rows[i] if i < b.height else []
            row += r + _pad(b.width - row_width(r))
        rows.append(row)
    return Block(rows, sum(b.width for b in blocks))


def center_in(block: Block, width: int) -> Block:
    return fixed(vstack([Block([], width), block]), width)


# ── screen writer ────────────────────────────────────────────────────────

def _rgb(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    return f"{int(h[0:2], 16)};{int(h[2:4], 16)};{int(h[4:6], 16)}"


def _sgr(style: tuple) -> str:
    fg, bg, bold, dim, inverse = style
    codes = ["0"]
    if bold:
        codes.append("1")
    if dim:
        codes.append("2")
    if inverse:
        codes.append("7")
    if fg:
        codes.append(f"38;2;{_rgb(fg)}")
    if bg:
        codes.append(f"48;2;{_rgb(bg)}")
    return f"\x1b[{';'.join(codes)}m"


_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


class Screen:
    """Paints a block centered on the full screen and remembers clickable cells."""

    def __init__(self, write: Callable[[str], None]) -> None:
        self._write = write
        self._last = ""
        self._last_size = (0, 0)
        self.spans: List[Tuple[int, int, int, Click]] = []

    def draw(self, block: Block, cols: int, rows: int, background: Optional[str]) -> None:
        # ink lays the app out one row short of the screen height
        height = max(1, rows - 1)
        top = max(0, (height - block.height + 1) // 2)
        left = max(0, (cols - block.width + 1) // 2)
        spans: List[Tuple[int, int, int, Click]] = []
        out_rows = []
        for y in range(rows):
            index = y - top
            content = block.rows[index] if 0 <= index < block.height and y < height else []
            out_rows.append(self._render_row(_pad(left) + content if content else [], cols, background, y, spans))
        frame = "\x1b[H" + "\r\n".join(out_rows)
        size = (cols, rows)
        if size != self._last_size:
            frame = "\x1b[0m\x1b[2J" + frame
            self._last_size = size
            self._last = ""
        self.spans = spans
        if frame != self._last:
            self._last = frame
            self._write(frame)

    @staticmethod
    def _render_row(segs: Row, cols: int, background: Optional[str], y: int, spans: list) -> str:
        out = []
        col = 0
        current = None
        for seg in segs:
            if col >= cols:
                break
            style = (seg.fg, seg.bg or background, seg.bold, seg.dim, seg.inverse)
            start = col
            kept = []
            for ch in _CONTROL.sub(" ", seg.text):
                w = char_width(ch)
                if col + w > cols:
                    col = cols
                    break
                kept.append(ch)
                col += w
            if kept:
                if style != current:
                    out.append(_sgr(style))
                    current = style
                out.append("".join(kept))
            if seg.click and col > start:
                spans.append((y, start, col, seg.click))
        if col < cols:
            style = (None, background, False, False, False)
            if style != current:
                out.append(_sgr(style))
            out.append(" " * (cols - col))
        out.append("\x1b[0m")
        return "".join(out)

    def click_at(self, x: int, y: int) -> Optional[Callable[[], None]]:
        """First action under a click. x/y are 1-based terminal cells."""
        col, row = x - 1, y - 1
        for span_row, x0, x1, (action, pad_x) in self.spans:
            if span_row == row and x0 - pad_x <= col <= x1 - 1 + pad_x:
                return action
        return None


# ── input parsing ────────────────────────────────────────────────────────

@dataclass
class Key:
    input: str = ""
    up: bool = False
    down: bool = False
    left: bool = False
    right: bool = False
    home: bool = False
    end: bool = False
    page_up: bool = False
    page_down: bool = False
    enter: bool = False
    escape: bool = False
    tab: bool = False
    backspace: bool = False
    delete: bool = False
    ctrl: bool = False
    shift: bool = False
    meta: bool = False
    paste: bool = False


@dataclass
class Mouse:
    button: int
    x: int
    y: int
    pressed: bool


_MOUSE = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])")
_CSI = re.compile(r"\x1b\[([0-9;?]*)([ -/]*)([@-~])")


def _char_key(ch: str) -> Key:
    if ch in ("\r", "\n"):
        return Key(enter=True)
    if ch == "\t":
        return Key(tab=True)
    if ch == "\x7f":
        return Key(backspace=True)
    if ch == "\x08":  # ctrl+backspace on Windows terminals
        return Key(backspace=True, ctrl=True)
    code = ord(ch)
    if 1 <= code <= 26:
        return Key(input=chr(code + 96), ctrl=True)
    if code < 32:
        return Key()
    return Key(input=ch)


def _csi_key(params: str, final: str) -> Key:
    parts = params.split(";") if params else []
    mod = int(parts[1]) - 1 if len(parts) > 1 and parts[1].isdigit() else 0
    key = Key(shift=bool(mod & 1), meta=bool(mod & 2), ctrl=bool(mod & 4))
    if final == "A":
        key.up = True
    elif final == "B":
        key.down = True
    elif final == "C":
        key.right = True
    elif final == "D":
        key.left = True
    elif final == "H":
        key.home = True
    elif final == "F":
        key.end = True
    elif final == "~" and parts:
        code = parts[0]
        if code in ("1", "7"):
            key.home = True
        elif code in ("4", "8"):
            key.end = True
        elif code == "3":
            key.delete = True
        elif code == "5":
            key.page_up = True
        elif code == "6":
            key.page_down = True
    return key


_SS3 = {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end"}


def parse_input(data: str) -> list:
    """Split a chunk of terminal input into Key / Mouse events."""
    events: list = []
    text: List[str] = []
    i = 0
    n = len(data)

    def flush_text() -> None:
        if text:
            events.append(Key(input="".join(text)))
            text.clear()

    while i < n:
        ch = data[i]
        if ch == "\x1b":
            flush_text()
            if data.startswith("\x1b[200~", i):
                end = data.find("\x1b[201~", i + 6)
                body = data[i + 6:] if end == -1 else data[i + 6:end]
                i = n if end == -1 else end + 6
                events.append(Key(input=body, paste=True))
                continue
            match = _MOUSE.match(data, i)
            if match:
                events.append(Mouse(int(match[1]), int(match[2]), int(match[3]), match[4] == "M"))
                i = match.end()
                continue
            match = _CSI.match(data, i)
            if match:
                events.append(_csi_key(match[1], match[3]))
                i = match.end()
                continue
            if i + 2 < n + 1 and data.startswith("\x1bO", i) and i + 2 < n:
                key = Key()
                name = _SS3.get(data[i + 2])
                if name:
                    setattr(key, name, True)
                events.append(key)
                i += 3
                continue
            if i + 1 < n and data[i + 1] != "\x1b":
                key = _char_key(data[i + 1])
                key.meta = True
                events.append(key)
                i += 2
                continue
            events.append(Key(escape=True))
            i += 1
            continue
        if ord(ch) < 32 or ch == "\x7f":
            flush_text()
            events.append(_char_key(ch))
        else:
            text.append(ch)
        i += 1
    flush_text()
    return events


# ── terminal I/O ─────────────────────────────────────────────────────────

ENTER_SEQ = "\x1b[?1049h\x1b[H\x1b[?25l\x1b[?1000h\x1b[?1006h\x1b[?2004h"
# also restores the cursor and switches mouse/paste modes off
LEAVE_SEQ = "\x1b[0m\x1b[?2004l\x1b[?1006l\x1b[?1000l\x1b[?25h\x1b[?1049l"


class Terminal:
    """Raw-mode terminal; input arrives on `events` as ('input', str)."""

    def __init__(self, events: "queue.Queue") -> None:
        self.events = events
        self._stop = threading.Event()
        self._restore: Optional[Callable[[], None]] = None
        self._thread: Optional[threading.Thread] = None

    def write(self, data: str) -> None:
        if os.name == "nt":
            sys.stdout.write(data)
            sys.stdout.flush()
        else:
            sys.stdout.buffer.write(data.encode("utf-8"))
            sys.stdout.buffer.flush()

    @staticmethod
    def size() -> Tuple[int, int]:
        size = shutil.get_terminal_size((80, 24))
        cols = size.columns if size.columns > 0 else 80
        rows = size.lines if size.lines > 1 else 24
        return cols, rows

    def start(self) -> None:
        if os.name == "nt":
            self._start_windows()
        else:
            self._start_posix()
        self.write(ENTER_SEQ)

    def stop(self) -> None:
        self._stop.set()
        try:
            self.write(LEAVE_SEQ)
        finally:
            if self._restore:
                self._restore()
                self._restore = None
        if self._thread:
            self._thread.join(timeout=0.3)

    # POSIX ----------------------------------------------------------------

    def _start_posix(self) -> None:
        import codecs
        import select
        import termios
        import tty

        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
        tty.setraw(fd)
        self._restore = lambda: termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        decoder = codecs.getincrementaldecoder("utf-8")("replace")

        def reader() -> None:
            while not self._stop.is_set():
                ready, _, _ = select.select([fd], [], [], 0.1)
                if not ready:
                    continue
                try:
                    data = os.read(fd, 4096)
                except OSError:
                    return
                if not data:
                    return
                text = decoder.decode(data)
                if text:
                    self.events.put(("input", text))

        self._thread = threading.Thread(target=reader, daemon=True)
        self._thread.start()

    # Windows --------------------------------------------------------------

    def _start_windows(self) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD

        class KEY_EVENT_RECORD(ctypes.Structure):
            _fields_ = [
                ("bKeyDown", wintypes.BOOL),
                ("wRepeatCount", wintypes.WORD),
                ("wVirtualKeyCode", wintypes.WORD),
                ("wVirtualScanCode", wintypes.WORD),
                ("uChar", wintypes.WCHAR),
                ("dwControlKeyState", wintypes.DWORD),
            ]

        class _EVENT(ctypes.Union):
            _fields_ = [("KeyEvent", KEY_EVENT_RECORD), ("_pad", ctypes.c_byte * 16)]

        class INPUT_RECORD(ctypes.Structure):
            _fields_ = [("EventType", wintypes.WORD), ("Event", _EVENT)]

        kernel32.PeekConsoleInputW.argtypes = [wintypes.HANDLE, ctypes.POINTER(INPUT_RECORD), wintypes.DWORD,
                                               ctypes.POINTER(wintypes.DWORD)]
        kernel32.ReadConsoleInputW.argtypes = kernel32.PeekConsoleInputW.argtypes
        kernel32.ReadConsoleW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                          ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]

        h_in = kernel32.GetStdHandle(-10)
        h_out = kernel32.GetStdHandle(-11)
        in_mode = wintypes.DWORD()
        out_mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(h_in, ctypes.byref(in_mode)) or \
                not kernel32.GetConsoleMode(h_out, ctypes.byref(out_mode)):
            raise OSError("yoinks needs a real console window")
        saved_in, saved_out = in_mode.value, out_mode.value

        ENABLE_PROCESSED_INPUT, ENABLE_LINE_INPUT, ENABLE_ECHO_INPUT = 0x1, 0x2, 0x4
        ENABLE_MOUSE_INPUT, ENABLE_QUICK_EDIT_MODE, ENABLE_EXTENDED_FLAGS = 0x10, 0x40, 0x80
        ENABLE_VIRTUAL_TERMINAL_INPUT = 0x200
        new_in = (saved_in | ENABLE_VIRTUAL_TERMINAL_INPUT | ENABLE_EXTENDED_FLAGS | ENABLE_MOUSE_INPUT) & ~(
            ENABLE_PROCESSED_INPUT | ENABLE_LINE_INPUT | ENABLE_ECHO_INPUT | ENABLE_QUICK_EDIT_MODE)
        new_out = saved_out | 0x1 | 0x4  # processed output + virtual terminal processing
        if not kernel32.SetConsoleMode(h_out, new_out):
            raise OSError("this console doesn't support ANSI escape sequences")
        kernel32.SetConsoleMode(h_in, new_in)

        def restore() -> None:
            kernel32.SetConsoleMode(h_in, saved_in)
            kernel32.SetConsoleMode(h_out, saved_out)

        self._restore = restore

        KEY_EVENT = 0x1
        records = (INPUT_RECORD * 64)()
        buf = ctypes.create_unicode_buffer(4096)

        def reader() -> None:
            count = wintypes.DWORD()
            while not self._stop.is_set():
                if kernel32.WaitForSingleObject(h_in, 50) != 0:
                    continue
                if not kernel32.PeekConsoleInputW(h_in, records, 64, ctypes.byref(count)) or count.value == 0:
                    continue
                has_text = any(
                    records[i].EventType == KEY_EVENT and records[i].Event.KeyEvent.bKeyDown
                    and records[i].Event.KeyEvent.uChar != "\x00"
                    for i in range(count.value)
                )
                if not has_text:
                    # focus/menu/key-up records would otherwise block ReadConsoleW forever
                    kernel32.ReadConsoleInputW(h_in, records, count.value, ctypes.byref(count))
                    continue
                if not kernel32.ReadConsoleW(h_in, buf, len(buf) - 1, ctypes.byref(count), None):
                    continue
                text = ctypes.wstring_at(buf, count.value)
                text = text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")
                if text:
                    self.events.put(("input", text))

        self._thread = threading.Thread(target=reader, daemon=True)
        self._thread.start()
