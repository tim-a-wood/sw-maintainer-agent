"""Units behind the exchange flow: downloads, fenced envelopes, notices."""

from __future__ import annotations

import os
from pathlib import Path

from maintain.downloads import newest_reply
from maintain.issues import IssueCandidate


def _touch(path: Path, stamp: float) -> Path:
    path.write_text("x", encoding="utf-8")
    os.utime(path, (stamp, stamp))
    return path


def test_newest_reply_picks_the_newest_matching_file(tmp_path):
    _touch(tmp_path / "old.md", 1000.0)
    _touch(tmp_path / "newer.json", 3000.0)
    _touch(tmp_path / "newest-but-wrong.png", 4000.0)
    _touch(tmp_path / "middle.zip", 2000.0)
    (tmp_path / "folder.md").mkdir()
    assert newest_reply(tmp_path, since=1500.0).name == "newer.json"
    assert newest_reply(tmp_path, since=3500.0) is None
    assert newest_reply(tmp_path / "absent", since=0.0) is None


def test_envelope_text_accepts_bare_and_fenced_json():
    from maintain.ui.bridge import _envelope_text
    bare = '{"a": 1}'
    assert _envelope_text(bare) == bare
    fenced = f"Here.\n\n```json\n{bare}\n```\nDone."
    assert _envelope_text(fenced).strip() == bare
    plain_fence = f"```\n{bare}\n```"
    assert _envelope_text(plain_fence).strip() == bare
    assert _envelope_text("no json here") is None
    assert _envelope_text("```json\nnot json\n```") is None


def test_notifying_store_reports_engine_captures_and_closes(tmp_path):
    from maintain.ui.controller import NotifyingIssueStore
    store = NotifyingIssueStore(runtime_root=tmp_path / "runtime",
                                repository=tmp_path)
    events: list[tuple[str, int, str]] = []
    store.notice = lambda kind, count, label: events.append(
        (kind, count, label))

    candidate = IssueCandidate(
        title="The check failed", detail="", severity="medium", file="",
        line=0, snippet="tests", external_ref="", kind="test", verified=True)
    store.capture([candidate], source="test", run_id="run-1")
    assert events == [("captured", 1, "")]

    # A human add and a scan capture stay silent; those flows toast alone.
    store.add(title="Human entry", detail="")
    store.capture([candidate], source="scan", run_id="run-2")
    assert events == [("captured", 1, "")]

    closed = store.close_for_run("run-1")
    assert len(closed) == 1
    assert events == [("captured", 1, ""),
                      ("closed", 1, "The check failed")]
