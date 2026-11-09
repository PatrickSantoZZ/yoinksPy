from __future__ import annotations

import sys
from typing import List, Optional

from . import __version__
from .args import parse_args
from .clipboard import read_clipboard
from .platforms import is_probably_url

HELP = """
  yoinks — yoink any video. paste. yoink. done.

  Usage
    $ yoinks [url]

  Examples
    $ yoinks https://youtu.be/dQw4w9WgXcQ
    $ yoinks https://x.com/user/status/123456
    $ yoinks                 (prompts for a url)

  Options
    --theme <mode>  use auto, light, or dark for this run
    -h, --help      show this help
    -v, --version   show version

  Downloads are saved to ~/Downloads.
  Powered by yt-dlp — YouTube, X, Instagram, Threads, TikTok & 1800+ sites.
"""


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
        print(__version__)
        return 0

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

    app = App(initial_url=args.initial_url, clipboard_url=clipboard_url, theme_mode=args.theme_mode or "auto")
    try:
        app.run()
    except KeyboardInterrupt:
        return 130
    if app.outcome:
        print(f"✓ yoinked → {app.outcome}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
