from __future__ import annotations

import math
import re
from typing import List, Optional


def js_round(value: float) -> int:
    """Math.round semantics (half rounds up), unlike Python's banker's rounding."""
    return math.floor(value + 0.5)


def _finite_positive(value: Optional[float]) -> bool:
    return value is not None and math.isfinite(value) and value > 0


def format_bytes(num: float) -> str:
    if not _finite_positive(num):
        return ""
    units = ["B", "KB", "MB", "GB"]
    value = float(num)
    unit = 0
    while value >= 1024 and unit < len(units) - 1:
        value /= 1024
        unit += 1
    shown = str(js_round(value)) if value >= 10 or unit == 0 else f"{value:.1f}"
    return f"{shown} {units[unit]}"


def format_duration(seconds: float) -> str:
    if not _finite_positive(seconds):
        return ""
    s = js_round(seconds)
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    mm = f"{m:02d}" if h > 0 else str(m)
    return f"{h}:{mm}:{sec:02d}" if h > 0 else f"{mm}:{sec:02d}"


def truncate(text: str, limit: int) -> str:
    return f"{text[: limit - 1]}…" if len(text) > limit else text


def shorten_path(filepath: str, homedir: str, limit: int = 60) -> str:
    pretty = f"~{filepath[len(homedir):]}" if filepath.startswith(homedir) else filepath
    if len(pretty) <= limit:
        return pretty
    match = re.search(r"\.\w{1,5}$", pretty)
    ext = match.group(0) if match else ""
    return f"{pretty[: limit - len(ext) - 1]}…{ext}"


def wrap_text(text: str, width: int, hard: bool = False) -> List[str]:
    """Word-wrap into left-flush lines. `hard` also splits words longer than a line."""
    lines: List[str] = []
    current = ""
    for word in text.split():
        while hard and len(word) > width:
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:width])
            word = word[width:]
        if not word:
            continue
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current += f" {word}"
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def format_speed(bytes_per_second: Optional[float]) -> str:
    if not _finite_positive(bytes_per_second):
        return ""
    return f"{format_bytes(bytes_per_second)}/s"


def format_eta(seconds: Optional[float]) -> str:
    if not _finite_positive(seconds):
        return ""
    return format_duration(seconds)
