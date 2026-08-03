"""What each release changed, in words for the person who uses it.

The notes ship inside the package, so the app shows them with no
network. Write every line in ASD-STE100: short sentences, active
voice, one idea per sentence. A style test enforces the rules.
"""

from __future__ import annotations

from . import __version__
from .update_check import version_tuple

NOTES: dict[str, tuple[str, ...]] = {
    "0.9.2": (
        "The app finds new versions and offers the update on the home screen.",
        "A scan now covers every project file, in parts that fit one package.",
        "Scan findings are written for a reader who is new to the code.",
        "Discuss the project: one package starts a talk in Copilot.",
        "The talk can end with new issues, a repair request, or a feature request.",
        "The issue list starts on the open work at each visit.",
    ),
    "0.9.3": (
        "The issue list, the history, and the home screen are much faster.",
        "After you accept scan findings, the app tells which files are not scanned.",
        "Many small repairs from a full walk of every screen.",
    ),
    "0.9.4": (
        "Command windows do not flash during the start or during project work.",
        "The app shows this list of changes after each update.",
        "If the app cannot start, it shows the cause and writes a log file.",
    ),
}


def notes_since(last_seen: str, current: str = "") -> list[tuple[str, tuple[str, ...]]]:
    """The versions to show, oldest first.

    With a known last-seen version, every noted version above it and
    at or below the current one shows. With no last-seen version —
    an update from a build before the notes existed — only the
    current version's notes show.
    """
    current = current or __version__
    ceiling = version_tuple(current)
    if not last_seen:
        lines = NOTES.get(current)
        return [(current, lines)] if lines else []
    floor = version_tuple(last_seen)
    if floor >= ceiling:
        return []
    chosen = [(version, lines) for version, lines in NOTES.items()
              if floor < version_tuple(version) <= ceiling]
    chosen.sort(key=lambda item: version_tuple(item[0]))
    return chosen
