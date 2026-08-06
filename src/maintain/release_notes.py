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
    "0.9.5": (
        "You can close an issue again. The reasons are in a menu that always fits.",
        "The issue screen shows your place in the list, for example Issue 3 of 24.",
        "Previous and Next move through the issues. The arrow keys do the same.",
        "After you close an issue, the next one opens. The list does not interrupt you.",
        "A return to the list keeps your position in it.",
    ),
    "0.9.6": (
        "A saved change can go into your project files with one button.",
        "Before this, the change stayed on its own branch and your files did not change.",
        "The last screen tells you if your files have the change.",
        "If the app cannot add the change, it tells you the cause in plain words.",
    ),
    "0.9.7": (
        "The home screen names a saved change that your files do not have.",
        "The button to add it is also on each saved run in the history.",
        "Before this, the button was only on the last screen of the run.",
        "The saved run also gives you the merge command, if you want a terminal.",
    ),
    "0.9.8": (
        "The tool now changes your project files directly, on your own branch.",
        "There is no separate branch to merge at the end. The change is simply there.",
        "After the tool saves, it pushes your branch to the remote.",
        "The bottom bar shows your branch. Click it to change branch or make one.",
        "The home screen shows the branch, the changed files, and the commits to push.",
        "A start still needs a clean project. A discard puts your files back.",
        "The Copilot screen is two cards now: Send to Copilot, then Receive the reply.",
        "The card whose turn it is has the blue edge. A sent card shows a green check.",
        "The other controls are in one small More menu.",
        "An update that does not install now says so, and updates cannot stay stuck.",
    ),
    "0.9.9": (
        "The install script now installs the newest release.",
        "Before this, it installed an old version, and each update installed it again.",
        "If you are on an old version, install again from the GitHub releases page.",
    ),
    "0.9.10": (
        "A reply the tool cannot use no longer asks you for the same package again and again.",
        "The run stays on its step and sends a small package that asks only for the correction.",
        "The screen tells you the attempt and the cause.",
        "You can reword the request or the plan for that step. Your words go with the package.",
        "Previous step and Next step move between steps, so you can do one again.",
        "The Copilot screen has no card to drag. The package is one line under Send.",
        "The installer now takes a Python that can run the video feature.",
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
