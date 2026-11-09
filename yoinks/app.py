from __future__ import annotations

import os
import queue
import random
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from . import ytdlp as yt
from .fmt import format_bytes, format_duration, format_eta, format_speed, js_round, shorten_path, truncate, wrap_text
from .history import add_to_history, load_history
from .platforms import Platform, detect_platform, is_probably_url
from .term import Block, Key, Mouse, Screen, Seg, Terminal, center_in, clip_row, fixed, gap, hstack, line, \
    parse_input, vstack
from .theme import Theme, next_theme_mode, theme_for

OUT_DIR = str(Path.home() / "Downloads")
YOINK_BUTTON = "yoink"
DONE_LABEL = "↵ yoink another"
TAGLINE = "yoink any video. paste. yoink. done."
SITES = "youtube · x · instagram · threads · tiktok · +1800 more"
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
FRAME_SECONDS = 0.033

HINTS = {
    "input": [("↵", "yoink"), ("^c", "quit")],
    "probing": [("esc", "cancel"), ("^c", "quit")],
    "picking": [("↑↓", "choose"), ("↵", "yoink"), ("esc", "back"), ("^c", "quit")],
    "downloading": [("esc", "cancel"), ("^c", "quit")],
    "done": [("^c", "quit")],
    "error": [("↵", "try again"), ("^c", "quit")],
}


def choice_label(choice: yt.DownloadChoice) -> str:
    icon = {"audio": "♪ ", "image": "▣ "}.get(choice.kind, "▶ ")
    return f"{icon}{choice.label}"


def spinner_frame() -> str:
    return SPINNER[int(time.monotonic() / 0.08) % len(SPINNER)]


def secondary(theme: Theme, text: str, **kw) -> Seg:
    return Seg(text, theme.gray, dim=theme.dim_secondary, **kw)


# ── logo ─────────────────────────────────────────────────────────────────

ART = [
    "▓ ▓ █▀█ ▀█▀ █▀▄█ █ █ █▀▀",
    "▀█▀ █ ▓  ▓  █  ▓ ▓▀▄ ▀▀▓",
    " ▀  ▀▀▀ ▀▀▀ ▀  ▀ ▀ ▀ ▀▀▀",
]
ROWS = len(ART)
COLS = len(ART[0])
# intro: each glyph flickers in as ░, sharpens to ▒, then resolves
INTRO_MS = 900
INTRO_SPREAD_MS = 550
# shimmer: a tilted beam crosses the glyphs, thinning them one density step
SWEEP_MS = 1000
SWEEP_EVERY_MS = 7000
TILT = 2  # columns of lean per row — beam slants like /
HALF = 2.4  # beam half-width
# full-cell blocks swap to a lighter shade; half-blocks keep their glyph and dim instead
LIGHTER = {"█": "▒", "▓": "░"}
HALF_BLOCKS = {"▀", "▄"}


def _ease(t: float) -> float:
    return 1 - (1 - t) ** 3


class Logo:
    def __init__(self, animated: bool) -> None:
        self.delays = [[random.random() * INTRO_SPREAD_MS for _ in row] for row in ART]
        self.phase = "intro" if animated else "idle"
        self.animated = animated
        self.start = time.monotonic()

    def tick(self, now: float) -> None:
        if not self.animated:
            return
        elapsed = (now - self.start) * 1000
        if self.phase == "idle":
            if elapsed >= SWEEP_EVERY_MS:
                self.phase, self.start = "sweep", now
        elif elapsed >= (INTRO_MS if self.phase == "intro" else SWEEP_MS):
            self.phase, self.start = "idle", now

    def _cell(self, ch: str, row: int, col: int, t: float, theme: Theme) -> Seg:
        if ch == " " or self.phase == "idle":
            return Seg(ch, theme.primary)
        if self.phase == "intro":
            dt = t - self.delays[row][col]
            if dt < 0:
                return Seg(" ", theme.primary)
            if dt < 110:
                return secondary(theme, ch if ch in HALF_BLOCKS else "░")
            if dt < 220:
                return secondary(theme, ch if ch in HALF_BLOCKS else "▒")
            return Seg(ch, theme.primary)
        # sweep — beam position leans right as it climbs, only glyphs are touched
        p_min = -TILT * ROWS - HALF
        p_max = COLS + HALF
        p = p_min + _ease(min(1.0, t / SWEEP_MS)) * (p_max - p_min)
        d = abs(col - (ROWS - 1 - row) * TILT - p)
        if d <= HALF and 1 - d / HALF > 0.35:
            if ch in HALF_BLOCKS:
                return secondary(theme, ch)
            return Seg(LIGHTER.get(ch, ch), theme.primary)
        return Seg(ch, theme.primary)

    def block(self, theme: Theme, click: Callable[[], None]) -> Block:
        t = (time.monotonic() - self.start) * 1000
        rows = []
        for r, art_row in enumerate(ART):
            row = []
            for c, ch in enumerate(art_row):
                seg = self._cell(ch, r, c, t, theme)
                seg.click = (click, 1)
                row.append(seg)
            rows.append(row)
        return Block(rows, COLS)


# ── widgets ──────────────────────────────────────────────────────────────

def framed_input(theme: Theme, title: str, width: int, child: List[Seg], button: Optional[str] = None,
                 button_dim: bool = False, on_button: Optional[Callable[[], None]] = None) -> Block:
    """
    A single-line input frame with the title on the top border, like
    `╭─ Paste a link ────╮`. `button` renders a filled block forged onto the
    right edge; half-blocks on the outer rows keep the fill optically the
    same height as the thin frame borders.
    """
    inner = width - 2
    tail = max(0, inner - len(title) - 3)
    end_top, end_bottom = ("─", "─") if button else ("╮", "╯")
    top = clip_row([secondary(theme, "╭─ "), Seg(title, theme.primary),
                    secondary(theme, f" {'─' * tail}{end_top}")], width)
    content_width = width - 4 - (0 if button else 2)
    middle = [secondary(theme, "│ "), Seg("❯ ", theme.primary), *clip_row(child, max(0, content_width))]
    if not button:
        middle.append(secondary(theme, " │"))
    bottom = [secondary(theme, f"╰{'─' * inner}{end_bottom}")]
    if button:
        bw = len(button) + 4
        fill = theme.gray if button_dim else theme.primary
        fill_dim = button_dim and theme.dim_secondary
        click = (on_button, 1) if on_button else None
        top.append(Seg("▄" * bw, fill, bold=True, dim=fill_dim, click=click))
        # terminals disagree on whether `dim` reaches an inverted background —
        # skip inverse while dim so the label fades with the fills as a ghost button
        middle.append(Seg(
            f"  {button}  ",
            None if theme.inverse_button else theme.dark,
            bg=None if theme.inverse_button else fill,
            inverse=theme.inverse_button and not button_dim,
            dim=fill_dim,
            bold=True,
            click=click,
        ))
        bottom.append(Seg("▀" * bw, fill, bold=True, dim=fill_dim, click=click))
    return Block([top, middle, bottom])


def panel(theme: Theme, title: str, width: int, body: List[List[Seg]]) -> Block:
    """A bordered panel with the title on the top border."""
    inner = width - 2
    tail = max(0, inner - len(title) - 3)
    rows = [clip_row([secondary(theme, "╭─ "), Seg(title, theme.primary), secondary(theme, f" {'─' * tail}╮")], width)]
    for row in body:
        rows.append([secondary(theme, "│"), Seg("  "), *clip_row(row, max(0, width - 6)), Seg("  "),
                     secondary(theme, "│")])
    rows.append([secondary(theme, f"╰{'─' * inner}╯")])
    return Block(rows, width)


def progress_bar(theme: Theme, percent: float, width: int = 30) -> Block:
    clamped = max(0.0, min(1.0, percent))
    filled = js_round(clamped * width)
    # fixed-width percent — "5%" vs "100%" must not change the line width
    return line(
        Seg("█" * filled, theme.primary),
        secondary(theme, "░" * (width - filled)),
        Seg(" " + f"{js_round(clamped * 100)}%".rjust(4), theme.primary),
    )


def spinner_line(theme: Theme, text: str) -> Block:
    return line(Seg(spinner_frame(), theme.primary), secondary(theme, text))


# fixed-width slots — the centered line must not change width as values tick
def _part_label(p: yt.DownloadProgress) -> str:
    # explains the bar resetting between files (video, then audio)
    return f"part {p.part + 1}/{p.total_parts}  " if p.total_parts > 1 else ""


def download_meta(p: yt.DownloadProgress) -> str:
    speed = format_speed(p.speed) if p.speed else ""
    eta = f"{format_eta(p.eta)} left" if p.eta else ""
    return f"{_part_label(p)}{speed.rjust(10)}  {eta.ljust(12)}"


def indeterminate_meta(p: yt.DownloadProgress) -> str:
    speed = format_speed(p.speed) if p.speed else ""
    return f"{_part_label(p)}{format_bytes(p.downloaded_bytes).rjust(8)}  {speed.ljust(10)}"


# ── text input ───────────────────────────────────────────────────────────

def _is_word(ch: str) -> bool:
    return ch.isascii() and (ch.isalnum() or ch == "_")


def word_left(text: str, i: int) -> int:
    while i > 0 and not _is_word(text[i - 1]):
        i -= 1
    while i > 0 and _is_word(text[i - 1]):
        i -= 1
    return i


def word_right(text: str, i: int) -> int:
    while i < len(text) and not _is_word(text[i]):
        i += 1
    while i < len(text) and _is_word(text[i]):
        i += 1
    return i


def strip_control(text: str) -> str:
    return "".join(c for c in text if ord(c) >= 32 and c != "\x7f")


class TextInput:
    """
    Single-line editor with readline-style keys: word jumps (alt/ctrl+←/→,
    alt+b/f), word delete (alt/ctrl+⌫, ^w), kill line (^u/^k), home/end
    (^a/^e), shift+arrow selection, ↑/↓ history recall, paste auto-submit
    and horizontal scrolling.
    """

    def __init__(self, cursor: int = 0) -> None:
        self.cursor_state = cursor
        self.anchor_state: Optional[int] = None
        self.history_pos: Optional[int] = None
        self.draft = ""
        self.offset = 0

    def _positions(self, value: str) -> Tuple[int, Optional[int], Optional[Tuple[int, int]]]:
        # the parent can reset value — clamp stale positions
        cursor = min(self.cursor_state, len(value))
        anchor = None if self.anchor_state is None else min(self.anchor_state, len(value))
        selection = (min(anchor, cursor), max(anchor, cursor)) if anchor is not None and anchor != cursor else None
        return cursor, anchor, selection

    def handle(self, key: Key, value: str, *, on_change: Callable[[str], None], on_submit: Callable[[str], None],
               history: List[str], submit_on_paste: Callable[[str], bool], on_tab: Callable[[], None]) -> None:
        cursor, anchor, selection = self._positions(value)

        def place(position: int, selecting: bool = False) -> None:
            if selecting:
                if anchor is None:
                    self.anchor_state = cursor
            else:
                self.anchor_state = None
            self.cursor_state = max(0, min(len(value), position))

        def edit(nxt: str, position: int) -> None:
            self.anchor_state = None
            self.cursor_state = max(0, min(len(nxt), position))
            self.history_pos = None  # editing a recalled entry turns it into a fresh draft
            on_change(nxt)

        def recall(text: str) -> None:
            self.anchor_state = None
            self.cursor_state = len(text)
            on_change(text)

        def remove(start: int, end: int) -> None:
            edit(value[:start] + value[end:], start)

        if key.enter:
            return on_submit(value)
        if key.tab:
            return on_tab()
        if key.page_up or key.page_down:
            return
        if key.escape:
            self.anchor_state = None
            return

        if key.up or key.down:
            if not history:
                return
            if key.up:
                if self.history_pos is None:
                    self.draft = value
                nxt = 0 if self.history_pos is None else min(self.history_pos + 1, len(history) - 1)
                if nxt == self.history_pos:
                    return
                self.history_pos = nxt
                recall(history[nxt])
            elif self.history_pos is not None:
                nxt = self.history_pos - 1
                self.history_pos = None if nxt < 0 else nxt
                recall(self.draft if nxt < 0 else history[nxt])
            return

        if key.home:
            return place(0)
        if key.end:
            return place(len(value))

        if key.left or key.right:
            direction = -1 if key.left else 1
            # plain arrow with a selection collapses to its edge
            if selection and not key.shift:
                return place(selection[0] if direction < 0 else selection[1])
            if key.meta or key.ctrl:
                target = word_left(value, cursor) if direction < 0 else word_right(value, cursor)
            else:
                target = cursor + direction
            return place(target, key.shift)

        by_word = key.meta or key.ctrl
        if key.backspace:
            if selection:
                return remove(*selection)
            return remove(word_left(value, cursor) if by_word else max(0, cursor - 1), cursor)
        if key.delete:
            if selection:
                return remove(*selection)
            return remove(cursor, word_right(value, cursor) if by_word else min(len(value), cursor + 1))

        if key.ctrl:
            if key.input == "a":
                return place(0)
            if key.input == "e":
                return place(len(value))
            if key.input == "u":
                return remove(0, selection[1] if selection else cursor)
            if key.input == "k":
                return remove(selection[0] if selection else cursor, len(value))
            if key.input == "w":
                end = selection[1] if selection else cursor
                return remove(word_left(value, selection[0] if selection else cursor), end)
            return
        if key.meta and not key.paste:
            if key.input == "b":
                return place(word_left(value, cursor))
            if key.input == "f":
                return place(word_right(value, cursor))
            return

        # typed or pasted text — drop control chars and newlines, replace the selection
        clean = strip_control(key.input)
        if not clean:
            return
        start, end = selection or (cursor, cursor)
        nxt = value[:start] + clean + value[end:]
        edit(nxt, start + len(clean))
        # a multi-char chunk is a paste — submit right away when it completes the field
        if len(clean) > 1 and value == "" and submit_on_paste(nxt.strip()):
            on_submit(nxt)

    def render(self, value: str, theme: Theme, placeholder: str, width: int) -> List[Seg]:
        cursor, _, selection = self._positions(value)
        # scroll the window so the cursor stays visible (it can sit one past the end)
        span = max(8, width)
        offset = min(self.offset, max(0, len(value) + 1 - span))
        if cursor < offset:
            offset = cursor
        if cursor > offset + span - 1:
            offset = cursor - span + 1
        self.offset = offset

        if not value:
            return [Seg(" ", inverse=True), secondary(theme, placeholder[: span - 1])]
        segs = []
        for column in range(min(span, len(value) - offset + 1)):
            index = offset + column
            selected = selection is not None and selection[0] <= index < selection[1]
            at_cursor = selection is None and index == cursor
            segs.append(Seg(value[index] if index < len(value) else " ", theme.primary, inverse=selected or at_cursor))
        return segs


# ── app ──────────────────────────────────────────────────────────────────

class Phase:
    def __init__(self, name: str, **fields) -> None:
        self.name = name
        self.warning: Optional[str] = None
        self.status = ""
        self.choice: Optional[yt.DownloadChoice] = None
        self.progress: Optional[yt.DownloadProgress] = None
        self.processing = False
        self.refreshing = False
        self.filepath = ""
        self.message = ""
        for k, v in fields.items():
            setattr(self, k, v)


class App:
    def __init__(self, *, initial_url: Optional[str], clipboard_url: Optional[str], theme_mode: str) -> None:
        self.events: "queue.Queue" = queue.Queue()
        self.term = Terminal(self.events)
        self.screen = Screen(self.term.write)
        self.theme_mode = theme_mode
        self.clipboard_url = clipboard_url
        self.url = initial_url or ""
        self.url_input = ""
        self.history = load_history()
        self.platform: Optional[Platform] = None
        self.info: Optional[dict] = None
        self.choices: List[yt.DownloadChoice] = []
        self.ytdlp = ""
        self.selected = 0  # choice under the cursor
        self.info_json: Optional[str] = None
        self.info_jsons: List[str] = []
        self.abort: Optional[yt.Abort] = None
        self.text_input: Optional[TextInput] = None
        self.logo = Logo(animated=True)
        self.outcome: Optional[str] = None
        self.running = True
        self.phase = Phase("probing", status="warming up…") if initial_url else Phase("input")
        self._initial_url = initial_url

    @property
    def theme(self) -> Theme:
        return theme_for(self.theme_mode)

    # lifecycle ------------------------------------------------------------

    def run(self) -> None:
        self.term.start()
        try:
            if self._initial_url:
                self.start_probe(self._initial_url)
            while self.running:
                self.draw()
                try:
                    event = self.events.get(timeout=FRAME_SECONDS)
                except queue.Empty:
                    continue
                while True:
                    self._dispatch(event)
                    if not self.running:
                        break
                    try:
                        event = self.events.get_nowait()
                    except queue.Empty:
                        break
        finally:
            if self.abort:
                self.abort.set(wait=True)
            self.term.stop()
            for path in self.info_jsons:
                try:
                    os.remove(path)
                except OSError:
                    pass

    def _dispatch(self, event) -> None:
        kind, payload = event
        if kind == "call":
            payload()
            return
        for ev in parse_input(payload):
            if isinstance(ev, Mouse):
                if ev.pressed and ev.button == 0:
                    action = self.screen.click_at(ev.x, ev.y)
                    if action:
                        action()
            else:
                self.on_key(ev)
            if not self.running:
                return

    def post(self, abort: yt.Abort, fn: Callable[[], None]) -> None:
        """Run fn on the UI thread unless the job it belongs to was cancelled meanwhile."""
        self.events.put(("call", lambda: None if abort.is_set() else fn()))

    def exit(self) -> None:
        self.running = False

    # state ----------------------------------------------------------------

    def set_phase(self, phase: Phase) -> None:
        if phase.name == "input" and self.phase.name != "input":
            self.text_input = None  # remount: cursor goes to the end of the value
        self.phase = phase

    def cycle_theme(self) -> None:
        self.theme_mode = next_theme_mode(self.theme_mode)

    def reset_to_input(self) -> None:
        self.url = ""
        self.url_input = ""
        self.platform = None
        self.info = None
        self.choices = []
        self.set_phase(Phase("input"))

    def cancel_run(self) -> None:
        if self.abort:
            self.abort.set()
        url = self.url
        self.reset_to_input()
        self.url_input = url  # keep the link around so a cancel isn't destructive

    def go_home(self) -> None:
        if self.phase.name in ("probing", "downloading"):
            self.cancel_run()
        elif self.phase.name != "input":
            self.reset_to_input()

    def start_probe(self, target_url: str) -> None:
        abort = yt.Abort()
        self.abort = abort
        self.platform = detect_platform(target_url)
        self.set_phase(Phase("probing", status="warming up…"))

        def set_status(status: str) -> None:
            if self.phase.name == "probing":
                self.phase.status = status

        def work() -> None:
            try:
                ytdlp = self.ytdlp or yt.ensure_ytdlp(lambda s: self.post(abort, lambda: set_status(s)), abort)
                self.post(abort, lambda: setattr(self, "ytdlp", ytdlp))
                if abort.is_set():
                    return
                self.post(abort, lambda: set_status("fetching video info…"))
                result = yt.probe(ytdlp, target_url, abort)
                self.info_jsons.append(result.info_json_path)

                def picked() -> None:
                    self.info_json = result.info_json_path
                    self.info = result.info
                    self.choices = yt.build_choices(result.info)
                    self.selected = 0
                    self.set_phase(Phase("picking"))

                self.post(abort, picked)
            except Exception as error:  # noqa: BLE001 — every failure becomes the error screen
                if not abort.is_set():
                    message = str(error) or error.__class__.__name__
                    self.post(abort, lambda: self.set_phase(Phase("error", message=message)))

        threading.Thread(target=work, daemon=True).start()

    def handle_url_submit(self, value: str) -> None:
        trimmed = value.strip()
        if not is_probably_url(trimmed):
            self.set_phase(Phase("input", warning="that doesn’t look like a link — paste a full url"))
            return
        self.url = trimmed
        self.start_probe(trimmed)

    def handle_pick(self, index: int) -> None:
        if not 0 <= index < len(self.choices):
            return
        choice = self.choices[index]
        abort = yt.Abort()
        self.abort = abort
        self.set_phase(Phase("downloading", choice=choice))
        url, ytdlp, info_json = self.url, self.ytdlp, self.info_json

        def update(**fields) -> Callable[[], None]:
            def apply() -> None:
                if self.phase.name == "downloading":
                    for k, v in fields.items():
                        setattr(self.phase, k, v)
            return apply

        def work() -> None:
            handlers = dict(
                on_progress=lambda p: self.post(abort, update(progress=p, processing=False)),
                on_processing=lambda: self.post(abort, update(processing=True)),
            )
            try:
                ffmpeg = yt.find_ffmpeg(lambda s: self.post(abort, update(status=s)), abort)
                self.post(abort, update(status=""))
                base = dict(ytdlp=ytdlp, url=url, choice=choice, out_dir=OUT_DIR, ffmpeg_location=ffmpeg,
                            abort=abort, **handlers)
                try:
                    # reuse the probe's metadata — starts immediately instead of re-extracting
                    filepath = yt.download(info_json_path=info_json, **base)
                except Exception:
                    if abort.is_set():
                        raise
                    # media urls in the cached info can expire — retry with a fresh extraction
                    self.post(abort, update(progress=None, refreshing=True))
                    filepath = yt.download(**base)

                def finished() -> None:
                    self.outcome = filepath
                    self.history = add_to_history(url)
                    self.set_phase(Phase("done", filepath=filepath))

                self.post(abort, finished)
            except Exception as error:  # noqa: BLE001
                if not abort.is_set():
                    message = str(error) or error.__class__.__name__
                    self.post(abort, lambda: self.set_phase(Phase("error", message=message)))

        threading.Thread(target=work, daemon=True).start()

    # input ----------------------------------------------------------------

    def on_key(self, key: Key) -> None:
        if key.ctrl and key.input == "c":
            self.exit()
            return
        if key.ctrl and key.input == "t":
            self.cycle_theme()
            return
        name = self.phase.name
        if key.escape and name in ("picking", "error", "done"):
            self.reset_to_input()
            return
        if key.escape and name in ("probing", "downloading"):
            self.cancel_run()
            return
        if key.enter and name in ("error", "done"):
            self.reset_to_input()
            return
        if name == "input":
            self._text_input().handle(
                key,
                self.url_input,
                on_change=self._set_url_input,
                on_submit=self.handle_url_submit,
                history=self.history,
                submit_on_paste=is_probably_url,
                on_tab=self._accept_clipboard,
            )
        elif name == "picking" and not key.ctrl and not key.meta:
            self._select_key(key)

    def _text_input(self) -> TextInput:
        if self.text_input is None:
            self.text_input = TextInput(len(self.url_input))
        return self.text_input

    def _set_url_input(self, value: str) -> None:
        self.url_input = value

    def _accept_clipboard(self) -> None:
        if self.clipboard_url and self.url_input == "":
            self.url_input = self.clipboard_url
            self._text_input().cursor_state = len(self.url_input)

    def _select_key(self, key: Key) -> None:
        count = len(self.choices)
        if not count:
            return
        if key.up or key.input == "k":
            self.selected = count - 1 if self.selected == 0 else self.selected - 1
        elif key.down or key.input == "j":
            self.selected = 0 if self.selected == count - 1 else self.selected + 1
        elif len(key.input) == 1 and key.input in "123456789":
            index = int(key.input) - 1
            if index < count:
                self.selected = index
                self.handle_pick(index)
        elif key.enter:
            self.handle_pick(self.selected)

    def _hint_action(self, key: str) -> Optional[Callable[[], None]]:
        name = self.phase.name
        if key == "^c":
            return self.exit
        if key == "^t":
            return self.cycle_theme
        if key == "esc":
            return self.cancel_run if name in ("probing", "downloading") else self.reset_to_input
        if key == "↵":
            if name == "input":
                return lambda: self.handle_url_submit(self.url_input)
            if name == "picking":
                return lambda: self.handle_pick(self.selected)
            if name in ("error", "done"):
                return self.reset_to_input
        return None  # ↑↓ / ↑ stay keyboard-only

    # view -----------------------------------------------------------------

    def draw(self) -> None:
        cols, rows = self.term.size()
        self.logo.tick(time.monotonic())
        self.screen.draw(self.view(cols), cols, rows, self.theme.background)

    def view(self, columns: int) -> Block:
        theme = self.theme
        box_width = max(14, min(64, columns - 6))
        content_width = max(10, min(columns - 4, 78))
        phase = self.phase

        blocks = [
            self.logo.block(theme, self.go_home),
            gap(),
            line(Seg(TAGLINE, theme.primary)),
            line(secondary(theme, SITES)),
            gap(),
        ]

        if phase.name == "input":
            child = self._text_input().render(self.url_input, theme, "https://youtube.com/watch?v=…", box_width - 6)
            parts = [framed_input(theme, "Paste a link", box_width, child, YOINK_BUTTON,
                                  on_button=lambda: self.handle_url_submit(self.url_input))]
            offered = bool(self.clipboard_url) and self.url_input == ""
            accepted = bool(self.clipboard_url) and self.url_input == self.clipboard_url
            if phase.warning:
                parts.append(line(secondary(theme, f"✗ {phase.warning}")))
            elif offered:
                parts.append(line(secondary(theme, "link in your clipboard — ⇥ to paste it")))
            elif accepted:
                parts.append(line(secondary(theme, "from your clipboard — ↵ to yoink it")))
            blocks.append(vstack(parts))

        elif phase.name == "probing":
            url = self.url if len(self.url) <= box_width - 8 else f"{self.url[: box_width - 9]}…"
            title = self.platform.label if self.platform else "Paste a link"
            blocks.append(framed_input(theme, title, box_width, [secondary(theme, url)], YOINK_BUTTON,
                                       button_dim=True))

        elif phase.name == "picking" and self.platform:
            blocks.append(self._picker(theme, content_width))

        elif phase.name == "downloading":
            blocks.append(self._downloading(theme, phase))

        elif phase.name == "done":
            label_width = len(DONE_LABEL) + 6
            click = (self.reset_to_input, 0)
            border = dict(fg=theme.gray, dim=theme.dim_secondary, click=click)
            button = Block([
                [Seg(f"╭{'─' * label_width}╮", **border)],
                [Seg("│", **border), Seg("   ", click=click), Seg(DONE_LABEL, theme.primary, bold=True, click=click),
                 Seg("   ", click=click), Seg("│", **border)],
                [Seg(f"╰{'─' * label_width}╯", **border)],
            ])
            blocks.append(vstack([
                line(Seg("✓ yoinked! ", theme.primary, bold=True), Seg("find your file in:", theme.primary)),
                line(secondary(theme, shorten_path(phase.filepath, str(Path.home()), 60))),
                gap(),
                button,
            ]))

        elif phase.name == "error":
            width = max(10, min(columns - 6, 72))
            text = Block([[Seg(l, theme.primary, bold=True)] for l in wrap_text(f"✗ {phase.message}", width, True)])
            blocks.append(center_in(text, width))

        blocks += [gap(2), self._shortcuts(theme)]
        return vstack(blocks)

    def _picker(self, theme: Theme, content_width: int) -> Block:
        left_width = max(0, content_width - 38)
        text_width = max(10, content_width - 41)
        info = self.info or {}
        title = str(info.get("title") or "")
        left_rows: List[List[Seg]] = [[]]  # paddingTop
        left_rows += [[Seg(l, theme.primary, bold=True)] for l in wrap_text(title, text_width)]
        left_rows.append([])
        meta = f"▸ {self.platform.label if self.platform else ''}"
        duration = info.get("duration")
        if isinstance(duration, (int, float)) and duration > 0:
            meta += f" · {format_duration(duration)}"
        if info.get("uploader"):
            meta += f" · {info['uploader']}"
        left_rows += [[secondary(theme, l)] for l in wrap_text(meta, text_width)]

        body = []
        for index, choice in enumerate(self.choices):
            is_selected = index == self.selected
            body.append([
                Seg("❯" if is_selected else " ", theme.primary),
                Seg(" "),
                Seg(choice_label(choice), theme.primary, bold=is_selected,
                    click=(lambda i=index: self.handle_pick(i), 1)),
            ])
        return hstack([fixed(Block(left_rows), left_width), panel(theme, "Download", 38, body)])

    def _downloading(self, theme: Theme, phase: Phase) -> Block:
        title = str((self.info or {}).get("title") or "")
        header = f"{truncate(title, 42)} · " if title else ""
        parts = [line(secondary(theme, f"{header}{phase.choice.label if phase.choice else ''}")), gap()]
        progress = phase.progress
        # every branch is exactly three rows — bar, gap, meta — so the layout never jumps
        if phase.processing:
            parts += [progress_bar(theme, 1), gap(), spinner_line(theme, " processing…")]
        elif progress and progress.total_bytes:
            parts += [progress_bar(theme, progress.downloaded_bytes / progress.total_bytes), gap(),
                      line(secondary(theme, download_meta(progress)))]
        elif progress:
            parts += [spinner_line(theme, " downloading…"), gap(), line(secondary(theme, indeterminate_meta(progress)))]
        else:
            if phase.refreshing:
                status = " link expired — grabbing a fresh one…"
            elif phase.status:
                status = f" {phase.status}"
            else:
                status = " starting download…"
            parts += [progress_bar(theme, 0), gap(), spinner_line(theme, status)]
        return vstack(parts)

    def _shortcuts(self, theme: Theme) -> Block:
        hints = [*HINTS[self.phase.name], ("^t", f"theme:{theme.mode}")]
        if self.phase.name == "input" and self.history:
            hints = [hints[0], ("↑", "history"), *hints[1:]]
        row: List[Seg] = []
        if self.phase.name == "probing":
            row += [Seg(spinner_frame(), theme.primary), secondary(theme, f" {self.phase.status}"),
                    secondary(theme, "  ·  ")]
        for index, (key, label) in enumerate(hints):
            if index:
                row.append(secondary(theme, "  ·  "))
            action = self._hint_action(key)
            click = (action, 1) if action else None
            row += [Seg(key, theme.primary, click=click), secondary(theme, f" {label}", click=click)]
        return Block([row])
