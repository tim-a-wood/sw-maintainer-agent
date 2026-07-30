"""Local quality checks for a scene reply: manifest, copy, and pace.

Findings never block the render. They show on the result screen and
travel in the repair packet, so Copilot corrects them in one round.
The numbers come from ASD-STE100 (20 words for one sentence) and the
BBC and Netflix caption rules (20 characters each second, three
seconds on screen).
"""

from __future__ import annotations

import ast
import re

MAX_WORDS_PER_SENTENCE = 20
MIN_SECONDS_PER_TEXT = 3.0
MAX_CHARS_PER_SECOND = 20.0
TOTAL_SECONDS_LOW = 25.0
TOTAL_SECONDS_HIGH = 50.0
MAX_FINDINGS = 16
OUTPUT_MARK = "output:"

BANNED_WORDS = (
    "utilize", "leverage", "execute", "perform", "terminate", "abort",
    "commence", "facilitate", "functionality", "invalid", "submit",
    "upload", "kindly", "please", "in order to", "prior to",
)

_PASSIVE = re.compile(
    r"\b(?:is|are|was|were|been|being)\s+[a-z]+(?:ed|en)\b", re.IGNORECASE)


def scene_beats(source: str) -> list[tuple[str, float]] | None:
    """The top-level BEATS list, or None when absent or not literal."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not (isinstance(target, ast.Name) and target.id == "BEATS"):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, SyntaxError):
            return None
        beats: list[tuple[str, float]] = []
        if not isinstance(value, (list, tuple)):
            return None
        for entry in value:
            if (not isinstance(entry, (list, tuple)) or len(entry) != 2
                    or not isinstance(entry[0], str)
                    or not isinstance(entry[1], (int, float))):
                return None
            beats.append((entry[0], float(entry[1])))
        return beats
    return None


def scene_texts(source: str) -> list[str]:
    """Every Text("...") literal, in file order, without repeats."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    texts: dict[str, None] = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "Text" and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            texts.setdefault(node.args[0].value)
    return list(texts)


def _clip(text: str) -> str:
    return text if len(text) <= 40 else text[:37] + "..."


def copy_faults(texts: list[str]) -> list[str]:
    """ASD-STE100 style faults in the on-screen texts."""
    faults: list[str] = []
    for text in texts:
        stripped = text.strip()
        if not stripped or stripped.casefold().startswith(OUTPUT_MARK):
            continue
        for sentence in re.split(r"[.!?]+", stripped):
            words = sentence.split()
            if len(words) > MAX_WORDS_PER_SENTENCE:
                faults.append(
                    f"A sentence has {len(words)} words; the limit is "
                    f"{MAX_WORDS_PER_SENTENCE}: '{_clip(stripped)}'")
        if _PASSIVE.search(stripped):
            faults.append(
                f"The text looks passive; use the active voice: "
                f"'{_clip(stripped)}'")
        lowered = f" {stripped.casefold()} "
        for banned in BANNED_WORDS:
            if re.search(rf"\b{re.escape(banned)}\b", lowered):
                faults.append(
                    f"The word '{banned}' is not simplified English; use a "
                    f"simpler word: '{_clip(stripped)}'")
    return faults


def pace_faults(beats: list[tuple[str, float]]) -> list[str]:
    """Reading-time faults in the declared beats."""
    faults: list[str] = []
    for index, (text, seconds) in enumerate(beats, start=1):
        if seconds <= 0:
            faults.append(f"Beat {index} declares no time on screen.")
            continue
        if not text.strip():
            continue
        if seconds < MIN_SECONDS_PER_TEXT:
            faults.append(
                f"Beat {index} shows text for {seconds:g} s; the minimum "
                f"is {MIN_SECONDS_PER_TEXT:g} s.")
        speed = len(text) / seconds
        if speed > MAX_CHARS_PER_SECOND:
            faults.append(
                f"Beat {index} shows {speed:.0f} characters each second; "
                f"the limit is {MAX_CHARS_PER_SECOND:g}.")
    total = sum(seconds for _, seconds in beats)
    if beats and not TOTAL_SECONDS_LOW <= total <= TOTAL_SECONDS_HIGH:
        faults.append(
            f"The beats add up to {total:g} s; the target is 30 to 45 s.")
    return faults


def quality_findings(source: str) -> list[str]:
    """All manifest, copy, and pace faults for one checked scene."""
    findings: list[str] = []
    beats = scene_beats(source)
    if beats is None:
        findings.append(
            "The scene has no literal BEATS list of (text, seconds) pairs.")
        beats = []
    findings.extend(pace_faults(beats))
    texts = scene_texts(source)
    beat_texts = [text for text, _ in beats if text.strip()]
    merged = list(dict.fromkeys(texts + beat_texts))
    findings.extend(copy_faults(merged))
    return findings[:MAX_FINDINGS]
