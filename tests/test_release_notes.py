"""FR-U5: the release notes catalog and its selection rules."""

from __future__ import annotations

import re

from maintain import __version__
from maintain.release_notes import NOTES, notes_since

BANNED_WORDS = {
    "upload", "submit", "artifact", "artefact", "execute", "terminate",
    "abort", "utilize", "utilise", "leverage", "perform", "invalid",
    "kindly", "please",
}

SENTENCE_WORD_LIMIT = 25


def test_every_release_ships_its_notes():
    """The release ritual: a version bump without notes must fail."""
    assert __version__ in NOTES
    for version, lines in NOTES.items():
        assert re.fullmatch(r"\d+(\.\d+)*", version), version
        assert lines, version


def test_notes_follow_the_simplified_english_rules():
    for version, lines in NOTES.items():
        for line in lines:
            assert line.strip() == line and line.endswith("."), (version, line)
            for sentence in re.split(r"(?<=[.!?])\s+", line):
                words = [word for word in re.split(r"\s+", sentence) if word]
                assert len(words) <= SENTENCE_WORD_LIMIT, (version, sentence)
            lowered = line.lower()
            for banned in BANNED_WORDS:
                assert not re.search(rf"\b{banned}\w*\b", lowered), (
                    version, banned, line)
            assert "response" not in lowered and "bundle" not in lowered, line


def test_notes_since_selects_the_span():
    assert [v for v, _ in notes_since("0.9.1", "0.9.3")] == ["0.9.2", "0.9.3"]
    assert [v for v, _ in notes_since("0.9.2", "0.9.3")] == ["0.9.3"]
    assert notes_since("0.9.3", "0.9.3") == []
    assert notes_since("9.9.9", "0.9.3") == []
    # An update from a build before the notes existed shows only the
    # current version's notes.
    assert [v for v, _ in notes_since("", "0.9.3")] == ["0.9.3"]
    # No notes for the current version: nothing shows.
    assert notes_since("", "9.9.9") == []
