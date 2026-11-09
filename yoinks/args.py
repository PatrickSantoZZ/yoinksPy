from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .config import COOKIE_BROWSERS, is_cookie_browser
from .theme import is_theme_mode


@dataclass
class CliArgs:
    help: bool = False
    version: bool = False
    initial_url: Optional[str] = None
    theme_mode: Optional[str] = None
    output_dir: Optional[str] = None
    # a browser name turns cookies on, 'none' turns them off, None leaves the config alone
    cookies: Optional[str] = None
    error: Optional[str] = None


COOKIE_VALUES = f"{', '.join(COOKIE_BROWSERS)}, or none"


def _bad_theme(value: str) -> str:
    return f"unknown theme “{value}” — use auto, light, or dark"


def _read_cookies(value: str) -> Optional[str]:
    if value.lower() in ("none", "off"):
        return "none"
    return value if is_cookie_browser(value) else None


def parse_args(args: List[str]) -> CliArgs:
    result = CliArgs()
    positional: List[str] = []
    index = 0

    def take_value(flag: str) -> Optional[str]:
        nonlocal index
        index += 1
        return args[index] if index < len(args) else None

    while index < len(args):
        arg = args[index]
        if arg in ("-h", "--help"):
            result.help = True
        elif arg in ("-v", "--version"):
            result.version = True
        elif arg == "--theme" or arg.startswith("--theme="):
            value = take_value(arg) if arg == "--theme" else arg[len("--theme="):]
            if not value:
                result.error = "--theme needs a value: auto, light, or dark"
                return result
            if not is_theme_mode(value):
                result.error = _bad_theme(value)
                return result
            result.theme_mode = value
        elif arg in ("-o", "--output") or arg.startswith("--output="):
            value = take_value(arg) if not arg.startswith("--output=") else arg[len("--output="):]
            if not value or value.startswith("-"):
                result.error = f"{arg.split('=')[0]} needs a folder, e.g. -o ~/Videos"
                return result
            result.output_dir = value
        elif arg == "--cookies" or arg.startswith("--cookies="):
            value = take_value(arg) if arg == "--cookies" else arg[len("--cookies="):]
            if not value:
                result.error = f"--cookies needs a browser: {COOKIE_VALUES}"
                return result
            parsed = _read_cookies(value)
            if not parsed:
                result.error = f"can’t read cookies from “{value}” — use {COOKIE_VALUES}"
                return result
            result.cookies = parsed
        elif arg.startswith("-"):
            result.error = f"unknown option “{arg}”"
            return result
        else:
            positional.append(arg)
        index += 1

    if len(positional) > 1:
        result.error = "expected a single url"
        return result
    result.initial_url = positional[0] if positional else None
    return result
