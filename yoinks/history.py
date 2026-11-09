from __future__ import annotations

import json
from pathlib import Path
from typing import List

HISTORY_FILE = Path.home() / ".config" / "yoinks" / "history.json"
LIMIT = 50


def load_history() -> List[str]:
    try:
        parsed = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [entry for entry in parsed if isinstance(entry, str)] if isinstance(parsed, list) else []


def add_to_history(url: str) -> List[str]:
    """Prepend a url (deduped, capped) and persist. Returns the new list."""
    nxt = [url, *[entry for entry in load_history() if entry != url]][:LIMIT]
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        HISTORY_FILE.write_text(json.dumps(nxt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError:
        pass  # history is a nicety — never let it break a download
    return nxt
