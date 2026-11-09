from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .args import parse_args
from .clipboard import read_clipboard
from .config import load_config
from .platforms import is_probably_url

HELP = """
  yoinks — yoink any video. paste. yoink. done.

  Usage
    $ yoinks [url]

  Examples
    $ yoinks https://youtu.be/dQw4w9WgXcQ
    $ yoinks https://x.com/user/status/123456
    $ yoinks                 (prompts for a url)
    $ yoinks -o ~/Videos https://youtu.be/dQw4w9WgXcQ

  Options
    -o, --output <dir>  save downloads to <dir> (default: ~/Downloads)
    --theme <mode>      use auto, light, or dark for this run
    -h, --help          show this help
    -v, --version       show version

  Downloads are saved to ~/Downloads unless you pass -o.
  Settings live in ~/.config/yoinks/config.json.
  Powered by yt-dlp — YouTube, X, Instagram, Threads, TikTok & 1800+ sites.
"""


def resolve_output_dir(value: Optional[str]) -> str:
    """Absolute download folder: the given one (~ expanded) or ~/Downloads."""
    if not value:
        return str(Path.home() / "Downloads")
    return os.path.abspath(os.path.expanduser(value))


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream.isatty():
                stream.reconfigure(errors="replace")
            else:  # pipes on Windows default to the ANSI codepage
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.error:
        print(f"yoinks: {args.error}\nTry “yoinks --help” for usage.", file=sys.stderr)
        return 1
    if args.help:
        print(HELP)
        return 0
    if args.version:
        from .ytdlp import find_ytdlp

        print(f"yoinks {__version__}")
        found = find_ytdlp()
        if found:
            print(f"yt-dlp {found.version} ({found.source}: {found.path})")
        else:
            print("yt-dlp not installed — fetched on first yoink")
        return 0

    config = load_config()

    out_dir = resolve_output_dir(args.output_dir or config.get("output_dir"))
    try:
        os.makedirs(out_dir, exist_ok=True)
        if not os.access(out_dir, os.W_OK):
            raise PermissionError("not writable")
    except OSError as error:
        print(f"yoinks: can’t save to “{out_dir}” — {error.strerror or error}", file=sys.stderr)
        return 1

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("yoinks: needs an interactive terminal", file=sys.stderr)
        return 1

    # no url given — offer the clipboard url (⇥ to paste) when it already holds one
    clipboard_url = None
    if not args.initial_url:
        clipped = read_clipboard().strip()
        # reject multi-line clipboard content
        if clipped and not any(c.isspace() for c in clipped) and is_probably_url(clipped):
            clipboard_url = clipped

    from .app import App

    app = App(initial_url=args.initial_url, clipboard_url=clipboard_url, theme_mode=args.theme_mode or "auto",
              out_dir=out_dir, config=config)
    try:
        app.run()
    except KeyboardInterrupt:
        return 130
    if app.outcome:
        print(f"✓ yoinked → {app.outcome}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
