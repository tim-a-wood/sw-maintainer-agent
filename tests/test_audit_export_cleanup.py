"""The audit store's integrity surface: verify failures, export, retention."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from maintain.audit import AuditStore, cleanup_runs
from maintain.errors import RecoveryError

RUN_ID = "f-20260731-120000-abcd"
OLD = "2020-01-01T00:00:00+00:00"
NEW = "2099-01-01T00:00:00+00:00"


def _store(root: Path, run_id: str = RUN_ID) -> AuditStore:
    store = AuditStore(root, run_id)
    store.append("workflow_started", {"state": "created"})
    return store


def test_run_id_and_artifact_paths_are_guarded(tmp_path):
    with pytest.raises(RecoveryError):
        AuditStore(tmp_path, "../evil")
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        store.write_artifact("/absolute.json", {"a": 1})
    with pytest.raises(ValueError):
        store.write_artifact("../escape.json", {"a": 1})
    store.write_artifact("notes/a.json", {"a": 1})
    # The same bytes may land twice; different bytes never overwrite.
    store.write_artifact("notes/a.json", {"a": 1})
    with pytest.raises(RecoveryError):
        store.write_artifact("notes/a.json", {"a": 2})
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        store.register_artifact(outside)
    assert store.verify()["events"] >= 3


def test_verify_rejects_each_kind_of_tampering(tmp_path):
    missing = AuditStore(tmp_path, "f-20260731-120000-none")
    with pytest.raises(RecoveryError, match="missing"):
        missing.verify()

    store = _store(tmp_path)
    store.write_artifact("evidence.json", {"ok": True})
    store.verify()

    # A modified payload no longer matches its recorded hash.
    lines = store.ledger.read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[0])
    event["payload"] = {"state": "forged"}
    store.ledger.write_text("\n".join([json.dumps(event)] + lines[1:]) + "\n",
                            encoding="utf-8")
    with pytest.raises(RecoveryError, match="modified"):
        store.verify()

    # A renumbered chain is invalid before hashes are even compared.
    event = json.loads(lines[0])
    event["sequence"] = 7
    store.ledger.write_text("\n".join([json.dumps(event)] + lines[1:]) + "\n",
                            encoding="utf-8")
    with pytest.raises(RecoveryError, match="chain"):
        store.verify()
    store.ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")
    store.verify()

    # A missing declared artifact and an undeclared stray both fail.
    artifact = store.artifacts / "evidence.json"
    original = artifact.read_bytes()
    artifact.unlink()
    with pytest.raises(RecoveryError, match="missing or modified"):
        store.verify()
    artifact.write_bytes(original)
    stray = store.artifacts / "stray.txt"
    stray.write_text("x", encoding="utf-8")
    with pytest.raises(RecoveryError, match="inventory"):
        store.verify()
    stray.unlink()
    store.verify()


def test_export_writes_a_sealed_package_outside_the_run(tmp_path):
    store = _store(tmp_path)
    store.write_artifact("evidence.json", {"ok": True})
    store.save_record({"run_id": RUN_ID, "state": "cancelled"})

    with pytest.raises(RecoveryError, match="outside"):
        store.export(store.run_dir / "inside.zip")

    destination = tmp_path / "exports" / "run-audit.zip"
    assert store.export(destination) == destination
    with zipfile.ZipFile(destination) as archive:
        names = set(archive.namelist())
        assert {"EXPORT-MANIFEST.json", "INDEX.json", "VERIFY.txt",
                "audit.jsonl", "run.json"} <= names
        assert "artifacts/evidence.json" in names
        index = json.loads(archive.read("INDEX.json"))
        assert index["run_id"] == RUN_ID
        assert "audit.jsonl" in index["files"]


def _run(root: Path, run_id: str, *, state: str, updated: str,
         repository: str = "/repo", worktree: str = "") -> AuditStore:
    store = _store(root, run_id)
    store.save_record({
        "run_id": run_id, "state": state, "updated_at": updated,
        "repository": repository, "worktree": worktree,
    })
    return store


def test_cleanup_removes_only_old_terminal_unaccepted_runs(tmp_path):
    root = tmp_path / "runtime"
    with pytest.raises(ValueError):
        cleanup_runs(root, 0)
    assert cleanup_runs(root, 30) == []

    _run(root, "f-20200101-000000-gone", state="cancelled", updated=OLD)
    _run(root, "f-20200101-000000-fail", state="failed", updated=OLD)
    _run(root, "f-20200101-000000-keep", state="delivered", updated=OLD)
    _run(root, "f-20990101-000000-yung", state="cancelled", updated=NEW)
    kept_tree = tmp_path / "still-here"
    kept_tree.mkdir()
    _run(root, "f-20200101-000000-tree", state="cancelled", updated=OLD,
         worktree=str(kept_tree))
    (root / "not-a-run.txt").write_text("x", encoding="utf-8")

    removed = cleanup_runs(root, 30, repository=Path("/repo"))
    assert sorted(removed) == ["f-20200101-000000-fail",
                               "f-20200101-000000-gone"]
    assert (root / "f-20200101-000000-keep").is_dir()
    assert (root / "f-20990101-000000-yung").is_dir()
    assert (root / "f-20200101-000000-tree").is_dir()

    # A different repository is out of scope for a scoped cleanup.
    _run(root, "f-20200101-000000-othr", state="cancelled", updated=OLD,
         repository="/elsewhere")
    assert cleanup_runs(root, 30, repository=Path("/repo")) == []

    # A record that cannot be evaluated stops retention loudly.
    _run(root, "f-20200101-000000-brkn", state="cancelled", updated="not-a-date")
    with pytest.raises(RecoveryError, match="retention"):
        cleanup_runs(root, 30)
