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


def test_default_downloads_points_at_the_home_folder():
    from maintain.downloads import default_downloads
    assert default_downloads().name == "Downloads"


def _handoff(reply_kind: str):
    import types

    from maintain.models import ProviderRequest
    request = ProviderRequest(
        1, "f-20260731-120000-abcd", "change-value",
        "implement" if reply_kind == "zip" else "review",
        "Do the thing.", {})
    return types.SimpleNamespace(request=request, reply_kind=reply_kind)


def test_check_reply_zip_branches(tmp_path):
    import json
    import zipfile

    from maintain.ui.bridge import check_reply

    handoff = _handoff("zip")
    refused = check_reply(handoff)
    assert not refused.valid and "maintain-output.zip" in refused.message

    # A real ZIP with the wrong content is refused with the reason.
    bad = tmp_path / "maintain-output.zip"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("README.txt", "not an implementation")
    result = check_reply(handoff, path=bad)
    assert not result.valid and result.message
    assert not result.keep_as_attachment

    # Anything that is not a ZIP at all becomes an attachment instead.
    note = tmp_path / "notes.md"
    note.write_text("# Notes\n", encoding="utf-8")
    kept = check_reply(handoff, path=note)
    assert not kept.valid and kept.keep_as_attachment

    # A corrupt file that claims to be a ZIP is refused, never kept.
    corrupt = tmp_path / "corrupt.zip"
    corrupt.write_bytes(b"PK\x03\x04 but truncated")
    broken = check_reply(handoff, path=corrupt)
    assert not broken.valid and "ZIP" in broken.message
    assert not broken.keep_as_attachment

    # A JSON reply for a different run is named as the mismatch it is.
    envelope = json.dumps({
        "schema_version": 1, "run_id": "f-00000000-000000-else",
        "task_id": "change-value", "role": "review",
        "conversation_id": "c", "content": {"decision": "approve"}})
    other = check_reply(_handoff("json"), text=envelope)
    assert not other.valid and "different task" in other.message


def test_check_reply_text_branches(tmp_path):
    from maintain.ui.bridge import check_reply

    handoff = _handoff("json")
    empty = check_reply(handoff, text="   ")
    assert not empty.valid and "copy the reply" in empty.message

    binary = tmp_path / "image.png"
    binary.write_bytes(b"\x89PNG\r\n\x1a\n\x00\xff\xfe")
    kept = check_reply(handoff, path=binary)
    assert not kept.valid and kept.keep_as_attachment

    prose = tmp_path / "reading.md"
    prose.write_text("Just prose, no envelope.", encoding="utf-8")
    also_kept = check_reply(handoff, path=prose)
    assert not also_kept.valid and also_kept.keep_as_attachment


def test_newest_reply_takes_a_file_changed_exactly_at_since(tmp_path):
    from maintain.downloads import newest_reply
    _touch(tmp_path / "exact.md", 5000.0)
    assert newest_reply(tmp_path, since=5000.0).name == "exact.md"
