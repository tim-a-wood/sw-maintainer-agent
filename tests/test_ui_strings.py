"""M3: the ASD-STE100 rule subset enforced on the UI string catalog (NFR-3)."""

from __future__ import annotations

import re

from maintain.ui.strings import STR, text

# One name per thing. These synonyms drift away from the controlled vocabulary.
BANNED_WORDS = {
    "upload", "submit", "artifact", "artefact", "execute", "terminate",
    "abort", "utilize", "utilise", "leverage", "perform", "invalid",
    "kindly", "please",
}

SENTENCE_WORD_LIMIT = 25


def _sentences(value: str) -> list[str]:
    cleaned = re.sub(r"\{[a-z_]+\}", "X", value)
    parts = re.split(r"(?<=[.!?])\s+", cleaned)
    return [part.strip() for part in parts if part.strip()]


def test_catalog_is_complete_and_non_empty():
    assert STR
    for key, value in STR.items():
        assert isinstance(value, str) and value.strip(), key


def test_sentences_stay_short():
    for key, value in STR.items():
        for sentence in _sentences(value):
            words = [word for word in re.split(r"\s+", sentence) if word]
            assert len(words) <= SENTENCE_WORD_LIMIT, (
                f"{key}: sentence has {len(words)} words: {sentence}")


def test_no_banned_synonyms():
    for key, value in STR.items():
        lowered = value.lower()
        for banned in BANNED_WORDS:
            assert not re.search(rf"\b{banned}\w*\b", lowered), (
                f"{key}: contains the banned word {banned!r}: {value}")


def test_controlled_names_are_consistent():
    # The reply is a "reply", never a "response"; the packet is a "package"
    # or "packet", never a "bundle".
    for key, value in STR.items():
        lowered = value.lower()
        assert "response" not in lowered, key
        assert "bundle" not in lowered, key


def test_placeholders_format_cleanly():
    samples = {"run": "0143", "stage": "Build", "count": 3, "files": "a.py",
               "activity": "Change", "phase": "Plan",
               "text": "Done", "branch": "maintain/x", "name": "a.zip",
               "link": "https://x", "task": "build", "n": 2, "label": "Plan",
               "state": "saved", "id": "a3f2c1", "severity": "high",
               "source": "review", "step": "the plan reply", "when": "today",
               "time": "2:10", "request": "Change the value.",
               "title": "The unit is ignored", "total": 120, "left": 78,
               "version": "1.2.3", "index": 3,
               "current": "main", "wanted": "maintain/f-0143"}
    for key, value in STR.items():
        names = set(re.findall(r"\{([a-z_]+)\}", value))
        arguments = {name: samples[name] for name in names}
        rendered = text(key, **arguments) if arguments else text(key)
        assert "{" not in rendered, key
