import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from yoinks import config as config_mod
from yoinks import ytdlp as yt

from yoinks.args import parse_args
from yoinks.fmt import format_bytes, format_duration, shorten_path, truncate, wrap_text
from yoinks.platforms import detect_platform, is_probably_url
from yoinks.term import Block, Key, Mouse, Screen, Seg, parse_input
from yoinks.theme import is_theme_mode, next_theme_mode, theme_for
from yoinks.ytdlp import build_choices


class ArgsTest(unittest.TestCase):
    def test_spaced_theme_before_url(self):
        args = parse_args(["--theme", "light", "https://example.com/video"])
        self.assertEqual((args.theme_mode, args.initial_url, args.error), ("light", "https://example.com/video", None))

    def test_equals_theme_after_url(self):
        args = parse_args(["https://example.com/video", "--theme=dark"])
        self.assertEqual((args.theme_mode, args.initial_url), ("dark", "https://example.com/video"))

    def test_errors(self):
        self.assertRegex(parse_args(["--theme"]).error, "needs a value")
        self.assertRegex(parse_args(["--theme", "sepia"]).error, "unknown theme")
        self.assertRegex(parse_args(["--wat"]).error, "unknown option")
        self.assertRegex(parse_args(["one", "two"]).error, "single url")

    def test_output_and_cookies(self):
        args = parse_args(["-o", "~/Videos", "--cookies", "firefox", "https://x.com/a"])
        self.assertEqual((args.output_dir, args.cookies, args.initial_url), ("~/Videos", "firefox", "https://x.com/a"))
        self.assertEqual(parse_args(["--output=D:/v", "--cookies=none"]).cookies, "none")
        self.assertEqual(parse_args(["--cookies", "firefox:work"]).cookies, "firefox:work")
        self.assertRegex(parse_args(["-o"]).error, "needs a folder")
        self.assertRegex(parse_args(["--cookies", "netscape"]).error, "can’t read cookies")


class ThemeTest(unittest.TestCase):
    def test_modes_cycle(self):
        self.assertTrue(is_theme_mode("auto") and is_theme_mode("light") and is_theme_mode("dark"))
        self.assertFalse(is_theme_mode("sepia"))
        self.assertEqual(next_theme_mode("auto"), "light")
        self.assertEqual(next_theme_mode("light"), "dark")
        self.assertEqual(next_theme_mode("dark"), "auto")

    def test_auto_delegates_to_terminal(self):
        auto = theme_for("auto")
        self.assertIsNone(auto.primary)
        self.assertIsNone(auto.background)
        self.assertTrue(auto.dim_secondary and auto.inverse_button)
        self.assertEqual(theme_for("light").background, "#ffffff")
        self.assertEqual(theme_for("dark").primary, "#ffffff")

    def test_forced_themes_paint_background(self):
        out = []
        screen = Screen(out.append)
        screen.draw(Block([[Seg("╭─ x ─╮")]]), 20, 3, theme_for("dark").background)
        self.assertIn("48;2;24;24;27", out[-1])
        screen = Screen(out.append)
        screen.draw(Block([[Seg("╭─ x ─╮")]]), 20, 3, theme_for("auto").background)
        self.assertNotIn("48;2;", out[-1])


class FormatTest(unittest.TestCase):
    def test_bytes(self):
        self.assertEqual(format_bytes(0), "")
        self.assertEqual(format_bytes(512), "512 B")
        self.assertEqual(format_bytes(1536), "1.5 KB")
        self.assertEqual(format_bytes(52 * 1024 * 1024), "52 MB")

    def test_duration(self):
        self.assertEqual(format_duration(212), "3:32")
        self.assertEqual(format_duration(3725), "1:02:05")

    def test_text(self):
        self.assertEqual(truncate("abcdef", 4), "abc…")
        self.assertEqual(wrap_text("one two three", 7), ["one two", "three"])
        self.assertEqual(wrap_text("abcdefghij", 4, hard=True), ["abcd", "efgh", "ij"])
        self.assertEqual(shorten_path("/home/a/Downloads/x.mp4", "/home/a"), "~/Downloads/x.mp4")


class PlatformTest(unittest.TestCase):
    def test_detect(self):
        self.assertEqual(detect_platform("https://www.youtube.com/watch?v=x").key, "youtube")
        self.assertEqual(detect_platform("https://x.com/a/status/1").label, "X / Twitter")
        self.assertEqual(detect_platform("https://example.org/v").label, "example.org")
        self.assertEqual(detect_platform("nope").key, "unknown")

    def test_is_url(self):
        self.assertTrue(is_probably_url(" https://youtu.be/abc "))
        self.assertFalse(is_probably_url("ftp://x.com/a"))
        self.assertFalse(is_probably_url("https://"))
        self.assertFalse(is_probably_url("hello world"))


class ChoicesTest(unittest.TestCase):
    def test_build(self):
        info = {"formats": [
            {"format_id": "251", "acodec": "opus", "vcodec": "none", "abr": 130, "filesize": 3_000_000},
            {"format_id": "137", "vcodec": "avc1", "acodec": "none", "height": 1080, "ext": "mp4", "tbr": 4000,
             "filesize": 50_000_000},
            {"format_id": "248", "vcodec": "vp9", "acodec": "none", "height": 1080, "ext": "webm", "tbr": 5000},
            {"format_id": "18", "vcodec": "avc1", "acodec": "mp4a", "height": 360, "ext": "mp4", "tbr": 500,
             "filesize_approx": 10_000_000},
        ]}
        choices = build_choices(info)
        self.assertEqual([c.label for c in choices],
                         ["1080p · mp4 · ~51 MB", "360p · mp4 · ~9.5 MB", "audio only · mp3 · ~2.9 MB"])
        self.assertEqual(choices[-1].kind, "audio")

    def test_direct_image(self):
        choices = build_choices({"ext": "png", "direct": True})
        self.assertEqual([(c.kind, c.label, c.args) for c in choices], [("image", "image · png", [])])

    def test_image_formats(self):
        choices = build_choices({"formats": [{"ext": "jpg", "filesize": 2048}, {"ext": "jpg", "filesize": 4096}]})
        self.assertEqual([c.label for c in choices], ["image · jpg · ~4.0 KB"])

    def test_mp3_gets_cover_and_tags(self):
        audio = build_choices({})[-1]
        self.assertIn("--embed-thumbnail", audio.args)
        self.assertIn("--embed-metadata", audio.args)

    def test_compatible_h264_choice(self):
        info = {"formats": [
            {"acodec": "mp4a.40.2", "vcodec": "none", "abr": 128, "filesize": 1_000_000},
            {"acodec": "opus", "vcodec": "none", "abr": 160},
            {"vcodec": "av01.0.08M.08", "acodec": "none", "height": 1080, "ext": "mp4", "filesize": 30_000_000},
            {"vcodec": "avc1.640028", "acodec": "none", "height": 1080, "ext": "mp4", "filesize": 40_000_000},
        ]}
        compatible = [c for c in build_choices(info) if c.suffix == ".h264"]
        self.assertEqual(len(compatible), 1)
        self.assertTrue(compatible[0].label.startswith("1080p · mp4 · H.264"))
        self.assertIn("--remux-video", compatible[0].args)

    def test_no_compatible_choice_without_h264(self):
        info = {"formats": [{"vcodec": "vp9", "acodec": "none", "height": 720}, {"acodec": "opus", "vcodec": "none"}]}
        self.assertFalse([c for c in build_choices(info) if c.suffix])

    def test_no_formats(self):
        self.assertEqual([c.label for c in build_choices({})], ["best available · mp4", "audio only · mp3"])


class ConfigTest(unittest.TestCase):
    def test_roundtrip_keeps_unknown_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"mine": 1, "ytdlp_args": ["--embed-chapters"]}), encoding="utf-8")
            with mock.patch.object(config_mod, "CONFIG_FILE", path):
                cfg = config_mod.save_config(cookies="firefox")
                self.assertEqual(cfg["cookies"], "firefox")
                self.assertEqual(config_mod.load_config()["ytdlp_args"], ["--embed-chapters"])
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["mine"], 1)

    def test_bad_values_fall_back(self):
        cfg = config_mod._clean({"cookies": "netscape", "ytdlp_args": "--x", "copy_to_clipboard": "yes"})
        self.assertEqual(cfg, config_mod.DEFAULTS)


class FallbackTest(unittest.TestCase):
    def run_downloads(self, errors, is_youtube=True):
        calls = []

        def fake_download(**kwargs):
            calls.append(kwargs)
            error = errors[len(calls) - 1] if len(calls) <= len(errors) else None
            if error:
                raise RuntimeError(error)
            return "/out/file.mp4"

        refreshed = []
        with mock.patch.object(yt, "download", fake_download):
            try:
                result = yt.download_with_fallbacks(extra_args=[], is_youtube=is_youtube,
                                                    on_refresh=lambda: refreshed.append(1), info_json_path="i.json")
            except RuntimeError as error:
                result = f"error: {error}"
        return result, calls, refreshed

    def test_expired_link_refreshes_then_mweb(self):
        result, calls, refreshed = self.run_downloads(["HTTP Error 403: Forbidden", "HTTP Error 403: Forbidden"])
        self.assertEqual(result, "/out/file.mp4")
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0]["info_json_path"], "i.json")
        self.assertNotIn("info_json_path", calls[1])
        self.assertIn("youtube:player-client=mweb", calls[2]["extra_args"])
        self.assertEqual(refreshed, [1])

    def test_other_errors_are_not_retried(self):
        result, calls, _ = self.run_downloads(["Requested format is not available"])
        self.assertEqual((result, len(calls)), ("error: Requested format is not available", 1))

    def test_no_mweb_outside_youtube(self):
        _, calls, _ = self.run_downloads(["HTTP Error 403", "HTTP Error 403"], is_youtube=False)
        self.assertEqual(len(calls), 2)


class InputTest(unittest.TestCase):
    def test_keys(self):
        events = parse_input("ab\r\x1b[A\x1b[1;5D\x7f\x1b\x7f\x03\x1b")
        self.assertEqual(events[0].input, "ab")
        self.assertTrue(events[1].enter)
        self.assertTrue(events[2].up)
        self.assertTrue(events[3].left and events[3].ctrl)
        self.assertTrue(events[4].backspace and not events[4].meta)
        self.assertTrue(events[5].backspace and events[5].meta)
        self.assertTrue(events[6].ctrl and events[6].input == "c")
        self.assertTrue(events[7].escape)

    def test_mouse_and_paste(self):
        events = parse_input("\x1b[<0;34;12M\x1b[200~https://x.com/a\x1b[201~")
        self.assertEqual(events[0], Mouse(0, 34, 12, True))
        self.assertEqual(events[1], Key(input="https://x.com/a", paste=True))


if __name__ == "__main__":
    unittest.main()
