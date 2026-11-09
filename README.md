# yoinks (Python)

yoink any video. paste. yoink. done.

Download videos from YouTube, X/Twitter, Instagram, Threads, TikTok and
1,800+ other sites — right from your terminal. Paste a url, pick a
resolution (or audio-only mp3), done. No popups, no fake download buttons,
no sketchy redirects.

A zero-dependency Python port of [pablostanley/yoinks](https://github.com/pablostanley/yoinks):
same look, same keys, same sites — no Node, no npm, nothing but the Python
standard library.

## Install

```sh
pipx install git+https://github.com/PatrickSantoZZ/YoinksPy.git
```

To update later, run the same command with `--force`.

From a local checkout:

```sh
pipx install .        # or: pip install .
```

Or run it straight from the folder without installing anything:

```sh
python -m yoinks
```

Requires Python 3.8+. Everything else (yt-dlp, ffmpeg) is used from your
PATH or fetched automatically on first run.

## Usage

```sh
$ yoinks https://youtu.be/dQw4w9WgXcQ    # straight to the format picker
$ yoinks                                 # prompts for a url
$ yoinks --theme light                   # force the light palette
$ yoinks -o ~/Videos <url>               # save somewhere else
$ yoinks --cookies firefox               # sign in as you (remembered)
$ yoinks --cookies none                  # back to signed out
```

yoinks takes over the terminal (full-screen, centered — and restores your
scrollback on exit). Pick a format with ↑/↓ (or j/k, or number keys) and
hit enter. `esc` goes back, `^c` quits. Or just use the mouse — the yoink
button, the format list and the footer hints are all clickable, and
clicking the logo takes you back home. Files are saved to `~/Downloads`,
and the file path is printed to your terminal when you're done.

If your clipboard already holds a link, press `⇥` to paste it (or `^v`).
`↑`/`↓` in the input recall previously yoinked links.

When a download is done, `o` opens its folder. Press `^d` on the start
screen to also put the finished file itself on your clipboard — paste it
straight into Explorer, Discord or a chat.

The format list offers every resolution as mp4, an **H.264** variant that
plays everywhere (QuickTime, WhatsApp, older editors, Windows without the
AV1 extension), and audio-only **mp3** with cover art and title/artist tags.
Direct image links are saved as-is.

### Signed-in downloads (Instagram & co.)

Private accounts, stories, age-gated videos and, more and more often,
ordinary Instagram posts only work when you're logged in. `--cookies
firefox` lets yt-dlp borrow your browser's login (`chrome`, `edge`, `brave`,
`opera`, `vivaldi`, `chromium`, `safari` and `whale` work too, and
`firefox:profile-name` picks a profile). It's off until you turn it on, it's
remembered, and the footer shows which browser is in use. If the cookies
can't be read, yoinks quietly carries on signed out.

On Windows, Chrome, Edge and Brave encrypt their cookies in a way yt-dlp
usually can't read anymore — use Firefox there.

### Settings

`~/.config/yoinks/config.json` holds everything that's remembered:

```json
{
  "cookies": "firefox",
  "copy_to_clipboard": false,
  "output_dir": null,
  "ytdlp_args": ["--embed-chapters", "--embed-subs"]
}
```

`ytdlp_args` is passed to every yt-dlp call — handy for anything yoinks has
no button for.

The default `auto` theme uses your terminal's own foreground and background,
so it follows light and dark terminal themes without guessing. Press `^t` or
click the theme control in the footer to cycle through `auto`, `light`, and
`dark` for the current session.

## How it works

- Powered by [yt-dlp](https://github.com/yt-dlp/yt-dlp). If yt-dlp is on
  your PATH, yoinks uses it; otherwise it downloads the standalone binary to
  `~/.yoinks/bin` on first run.
- Fragmented streams (YouTube, HLS) are fetched four fragments at a time,
  which gets around per-connection throttling. If a YouTube link answers
  403, yoinks retries with a different YouTube client (`mweb`).
- Downloaded binaries are checked against the release's published SHA-256
  checksums.
- ffmpeg (needed for merging high-res streams and mp3 extraction) is found
  on your PATH. On Windows and Linux it is otherwise fetched once into
  `~/.yoinks/bin`; on macOS install it with `brew install ffmpeg`.
- The UI is a small hand-rolled terminal renderer (`yoinks/term.py`):
  raw-mode input via `termios` / the Windows console API, truecolor ANSI
  output, SGR mouse reporting and bracketed paste.

## Development

```sh
python -m yoinks <url>
python -m unittest discover -s tests
```

## A note on fair use

yoinks is a personal-archiving tool. Downloading content may violate a
platform's terms of service — only download what you have the right to
keep, and be excellent to creators.

## License

[MIT](LICENSE) — based on yoinks by Pablo Stanley.
