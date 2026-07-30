"""Find the newest Copilot reply file in the Downloads folder."""

from __future__ import annotations

from pathlib import Path

REPLY_SUFFIXES = {".zip", ".md", ".json", ".txt"}


def default_downloads() -> Path:
    return Path.home() / "Downloads"


def newest_reply(root: Path, since: float) -> Path | None:
    """The newest reply-shaped file changed at or after `since`, or None."""
    best: Path | None = None
    best_time = since
    try:
        entries = list(Path(root).iterdir())
    except OSError:
        return None
    for item in entries:
        if item.suffix.lower() not in REPLY_SUFFIXES:
            continue
        try:
            if not item.is_file():
                continue
            changed = item.stat().st_mtime
        except OSError:
            continue
        if changed >= best_time:
            best, best_time = item, changed
    return best
