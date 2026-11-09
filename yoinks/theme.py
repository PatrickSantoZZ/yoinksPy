from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

THEME_MODES = ("auto", "light", "dark")


@dataclass(frozen=True)
class Theme:
    mode: str
    # None means "use the terminal's own foreground/background"
    primary: Optional[str]
    gray: Optional[str]
    dark: Optional[str]
    background: Optional[str]
    dim_secondary: bool
    inverse_button: bool


_THEMES = {
    # leaving colors unset is more reliable than guessing whether the
    # terminal is light or dark — ANSI defaults already follow its theme
    "auto": Theme("auto", None, None, None, None, True, True),
    "light": Theme("light", "#18181b", "#52525b", "#ffffff", "#ffffff", False, False),
    "dark": Theme("dark", "#ffffff", "#a1a1aa", "#18181b", "#18181b", False, False),
}


def theme_for(mode: str) -> Theme:
    return _THEMES[mode]


def is_theme_mode(value: object) -> bool:
    return isinstance(value, str) and value in THEME_MODES


def next_theme_mode(mode: str) -> str:
    return THEME_MODES[(THEME_MODES.index(mode) + 1) % len(THEME_MODES)]
