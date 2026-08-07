"""FR-V20: a run must survive another program touching its files."""

from __future__ import annotations

import pytest

from maintain import audit
from maintain.errors import RecoveryError


def test_a_moment_of_denial_does_not_stop_the_run(tmp_path, monkeypatch):
    """The field fault: PermissionError WinError 5 replacing run.json.

    A virus scanner, the search indexer, or a sync client holding the
    file for a moment is enough on Windows. The hold clears in
    milliseconds; the run died with a traceback instead of waiting.
    """
    target = tmp_path / "run.json"
    target.write_bytes(b"old")
    attempts = {"count": 0}
    real_replace = audit.os.replace

    def flaky(temporary, path):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise PermissionError(5, "Access is denied")
        real_replace(temporary, path)

    monkeypatch.setattr(audit.os, "replace", flaky)
    monkeypatch.setattr(audit.time, "sleep", lambda _s: None)

    audit.atomic_write(target, b"new")

    assert target.read_bytes() == b"new"
    assert attempts["count"] == 3


def test_a_hold_that_never_clears_says_what_to_do(tmp_path, monkeypatch):
    """It cannot wait forever. When it gives up it names the likely
    cause and the file, not a traceback."""
    target = tmp_path / "run.json"
    target.write_bytes(b"old")
    monkeypatch.setattr(
        audit.os, "replace",
        lambda *a: (_ for _ in ()).throw(PermissionError(5, "Access is denied")))
    slept: list[float] = []
    monkeypatch.setattr(audit.time, "sleep", slept.append)

    with pytest.raises(RecoveryError) as caught:
        audit.atomic_write(target, b"new")

    said = str(caught.value)
    assert "run.json" in said
    assert "virus scanner" in said or "file sync" in said
    assert "Traceback" not in said
    # It waited, and it did not spin.
    assert len(slept) == audit.REPLACE_ATTEMPTS
    assert sum(slept) > 0
    # The old content is still there; nothing was half-written.
    assert target.read_bytes() == b"old"


def test_a_fault_that_is_not_a_hold_is_not_retried(tmp_path, monkeypatch):
    """A missing folder or a bad name is not going to fix itself."""
    target = tmp_path / "run.json"
    tries = {"count": 0}

    def broken(*args):
        tries["count"] += 1
        raise OSError(22, "Invalid argument")

    monkeypatch.setattr(audit.os, "replace", broken)

    with pytest.raises(RecoveryError):
        audit.atomic_write(target, b"new")
    assert tries["count"] == 1


def test_no_leftover_temporary_file(tmp_path, monkeypatch):
    """A failed replace must not leave rubbish beside the run."""
    target = tmp_path / "run.json"
    target.write_bytes(b"old")
    monkeypatch.setattr(
        audit.os, "replace",
        lambda *a: (_ for _ in ()).throw(PermissionError(5, "denied")))
    monkeypatch.setattr(audit.time, "sleep", lambda _s: None)

    with pytest.raises(RecoveryError):
        audit.atomic_write(target, b"new")

    assert sorted(p.name for p in tmp_path.iterdir()) == ["run.json"]
