"""The read-only history surface and the transition guards behind it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from maintain.errors import PolicyError
from maintain.history import list_runs, run_timeline
from maintain.models import RunRecord, RunState
from maintain.policy import transition


def _record_dir(root: Path, run_id: str, record: dict) -> Path:
    run_dir = root / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps(record), encoding="utf-8")
    return run_dir


def test_list_runs_skips_what_it_cannot_read(tmp_path):
    root = tmp_path / "runtime"
    assert list_runs(root) == []
    root.mkdir()
    (root / "stray.txt").write_text("x", encoding="utf-8")
    (root / "empty-dir").mkdir()
    broken = root / "broken"
    broken.mkdir()
    (broken / "run.json").write_text("{not json", encoding="utf-8")
    _record_dir(root, "mine", {
        "run_id": "mine", "state": "delivered", "repository": str(tmp_path),
        "updated_at": "2026-07-30T10:00:00+00:00",
        "evidence": {"changed_files": ["a.py", "b.py"]}})
    _record_dir(root, "other", {
        "run_id": "other", "state": "delivered",
        "repository": str(tmp_path / "elsewhere"),
        "updated_at": "2026-07-31T10:00:00+00:00"})

    everything = list_runs(root)
    assert [item.run_id for item in everything] == ["other", "mine"]
    scoped = list_runs(root, repository=tmp_path)
    assert [item.run_id for item in scoped] == ["mine"]
    assert scoped[0].changed_files == 2
    assert scoped[0].display_state == "Saved"
    assert scoped[0].closed is True


def test_list_runs_reuses_unchanged_records(tmp_path):
    """The summary cache: only a changed record file gets re-parsed.

    Hundreds of runs re-read on every home visit was measurable lag;
    the stamp check makes a revisit cost one stat per run."""
    import os

    root = tmp_path / "runtime"
    root.mkdir()
    for n in range(3):
        _record_dir(root, f"r-{n}", {
            "run_id": f"r-{n}", "state": "delivered",
            "repository": str(tmp_path),
            "updated_at": f"2026-07-3{n}T10:00:00+00:00"})

    first = {item.run_id: item for item in list_runs(root)}
    second = {item.run_id: item for item in list_runs(root)}
    for run_id in first:
        assert second[run_id] is first[run_id]

    # A rewritten record returns fresh; the others stay cached.
    changed = root / "r-1" / "run.json"
    record = json.loads(changed.read_text(encoding="utf-8"))
    record["name"] = "Renamed"
    changed.write_text(json.dumps(record), encoding="utf-8")
    os.utime(changed, ns=(1, 1))   # force a different stamp either way
    third = {item.run_id: item for item in list_runs(root)}
    assert third["r-1"] is not first["r-1"]
    assert third["r-1"].name == "Renamed"
    assert third["r-0"] is first["r-0"] and third["r-2"] is first["r-2"]

    # A removed run leaves the list and the cache.
    (root / "r-2" / "run.json").unlink()
    (root / "r-2").rmdir()
    assert sorted(item.run_id for item in list_runs(root)) == ["r-0", "r-1"]


def test_run_timeline_survives_missing_and_bad_ledgers(tmp_path):
    assert run_timeline(tmp_path, "absent") == []
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    events = [
        "not json at all",
        json.dumps({"type": "other", "sequence": 1}),
        json.dumps({"type": "iteration", "sequence": 2,
                    "payload": {"label": "Build applied", "kind": "build_applied",
                                "resume_state": "implemented"}}),
    ]
    (run_dir / "audit.jsonl").write_text("\n".join(events) + "\n",
                                        encoding="utf-8")
    timeline = run_timeline(tmp_path, "run-1")
    assert len(timeline) == 1
    assert timeline[0].label == "Build applied"
    assert timeline[0].can_go_back is True


def _record(state: str, evidence: dict | None = None,
            tree_hash: str = "") -> RunRecord:
    return RunRecord(
        run_id="f-20260731-120000-abcd", mode="feature", request="Change it",
        repository="/project", base_commit="base", branch="maintain/x",
        worktree="/worktree", state=state, evidence=evidence or {},
        tree_hash=tree_hash)


def test_transition_guards_refuse_unproven_promotions():
    with pytest.raises(PolicyError, match="Invalid workflow transition"):
        transition(_record("created"), RunState.DELIVERED)

    unreviewed = _record("testing")
    with pytest.raises(PolicyError, match="review"):
        transition(unreviewed, RunState.VERIFIED, tree_hash="t1")

    untested = _record("testing", evidence={
        "review": {"decision": "approve", "tree_hash": "t1"}})
    with pytest.raises(PolicyError, match="verification"):
        transition(untested, RunState.VERIFIED, tree_hash="t1")

    proven = _record("testing", evidence={
        "review": {"decision": "approve", "tree_hash": "t1"},
        "tests": {"passed": True, "tree_hash": "t1"}})
    transition(proven, RunState.VERIFIED, tree_hash="t1")
    assert RunState(proven.state) is RunState.VERIFIED

    drifted = _record("awaiting_acceptance", tree_hash="t2",
                      evidence={"verified_tree_hash": "t1"})
    with pytest.raises(PolicyError, match="unchanged verified tree"):
        transition(drifted, RunState.ACCEPTED)


def test_cancel_is_reachable_everywhere_but_delivering():
    from maintain.policy import TRANSITIONS
    assert RunState.CANCELLED not in TRANSITIONS[RunState.DELIVERING]
    for state in (RunState.SCOPING, RunState.TESTING, RunState.REVIEWING,
                  RunState.IMPLEMENTING, RunState.AWAITING_ACCEPTANCE):
        assert RunState.CANCELLED in TRANSITIONS[state], state


def test_verified_needs_both_proofs_on_the_same_tree():
    stale_review = _record("testing", evidence={
        "review": {"decision": "approve", "tree_hash": "old"},
        "tests": {"passed": True, "tree_hash": "t1"}})
    with pytest.raises(PolicyError, match="review"):
        transition(stale_review, RunState.VERIFIED, tree_hash="t1")

    stale_tests = _record("testing", evidence={
        "review": {"decision": "approve", "tree_hash": "t1"},
        "tests": {"passed": True, "tree_hash": "old"}})
    with pytest.raises(PolicyError, match="verification"):
        transition(stale_tests, RunState.VERIFIED, tree_hash="t1")

    unproven = _record("awaiting_acceptance", tree_hash="",
                       evidence={"verified_tree_hash": ""})
    with pytest.raises(PolicyError, match="verified tree"):
        transition(unproven, RunState.ACCEPTED)


def test_superseded_marks_exclude_the_revert_target_itself(tmp_path):
    run_dir = tmp_path / "run-2"
    run_dir.mkdir()
    events = [
        json.dumps({"type": "iteration", "sequence": 2,
                    "payload": {"label": "Plan approved", "kind": "plan_approved"}}),
        json.dumps({"type": "iteration", "sequence": 4,
                    "payload": {"label": "Build applied", "kind": "build_applied"}}),
        json.dumps({"type": "iteration", "sequence": 6,
                    "payload": {"label": "Went back", "kind": "revert",
                                "target_sequence": 2}}),
    ]
    (run_dir / "audit.jsonl").write_text("\n".join(events) + "\n",
                                        encoding="utf-8")
    timeline = run_timeline(tmp_path, "run-2")
    marks = {item.sequence: item.superseded for item in timeline}
    assert marks == {2: False, 4: True, 6: False}
