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


def test_every_replace_in_the_tool_waits_for_the_hold_to_clear():
    """The instruction from the field, after the same fault appeared at
    a second point in the workflow: fix it where it lives, not only
    where it was reported.

    A bare os.replace anywhere in the package is this fault waiting for
    a different file. The only one allowed is the retry itself.
    """
    import ast
    from pathlib import Path

    allowed = {("audit.py", "replace_when_free")}
    found: set[tuple[str, str]] = set()
    total = 0

    def is_replace(node) -> bool:
        return (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "replace"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "os")

    for path in sorted(Path(audit.__file__).parent.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        total += sum(1 for node in ast.walk(tree) if is_replace(node))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for inner in ast.walk(node):
                    if is_replace(inner):
                        found.add((path.name, node.name))

    assert found == allowed, sorted(found)
    # A replace outside any function would not appear above.
    assert total == 1, f"{total} calls to os.replace in the package"
