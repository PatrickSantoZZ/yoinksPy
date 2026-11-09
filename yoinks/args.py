from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .theme import is_theme_mode


@dataclass
class CliArgs:
    help: bool = False
    version: bool = False
    initial_url: Optional[str] = None
    theme_mode: Optional[str] = None
    output_dir: Optional[str] = None
    error: Optional[str] = None


def _bad_theme(value: str) -> str:
    return f"unknown theme “{value}” — use auto, light, or dark"


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
