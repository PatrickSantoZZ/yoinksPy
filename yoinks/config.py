"""
Persistent settings in ~/.config/yoinks/config.json:

    {
      "output_dir": null,            // default download folder (null = ~/Downloads)
      "ytdlp_args": []               // extra yt-dlp arguments, e.g. ["--embed-chapters"]
    }
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

CONFIG_FILE = Path.home() / ".config" / "yoinks" / "config.json"

DEFAULTS: Dict[str, Any] = {
    "output_dir": None,
    "ytdlp_args": [],
}


def _clean(raw: Any) -> Dict[str, Any]:
    config = dict(DEFAULTS)
    if not isinstance(raw, dict):
        return config
    if isinstance(raw.get("output_dir"), str) and raw["output_dir"].strip():
        config["output_dir"] = raw["output_dir"]
    args = raw.get("ytdlp_args")
    if isinstance(args, list) and all(isinstance(a, str) for a in args):
        config["ytdlp_args"] = list(args)
    return config


def load_config() -> Dict[str, Any]:
    try:
        return _clean(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return dict(DEFAULTS)


def save_config(**changes: Any) -> Dict[str, Any]:
    """Merge changes into the stored config. Keys the user added by hand are kept."""
    try:
        stored = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if not isinstance(stored, dict):
            stored = {}
    except (OSError, ValueError):
        stored = {}
    stored = {**DEFAULTS, **stored, **changes}
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(stored, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError:
        pass  # a read-only home shouldn't stop anyone from downloading a video
    return _clean(stored)


def ytdlp_args(config: Dict[str, Any]) -> List[str]:
    return list(config.get("ytdlp_args") or [])
