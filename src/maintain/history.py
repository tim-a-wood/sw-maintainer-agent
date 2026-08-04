"""Read-only run history and iteration timelines from the audit store."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    mode: str
    request: str
    state: str
    updated_at: str
    created_at: str
    repository: str
    changed_files: int
    name: str = ""
    phase: str = ""    # Plan, Build, Review, Test, or Save
    # FR-D11: a saved commit that the person's files do not have yet.
    awaiting_files: bool = False
    delivered_commit: str = ""

    @property
    def display_state(self) -> str:
        return {
            "delivered": "Saved",
            "cancelled": "Discarded",
            "failed": "Failed",
            "needs_human": "Waiting",
            "needs_human_delivery": "Waiting",
            "awaiting_acceptance": "Waiting",
        }.get(self.state, "In work")

    @property
    def closed(self) -> bool:
        return self.state in {"delivered", "cancelled", "failed"}


@dataclass(frozen=True)
class IterationEvent:
    sequence: int
    time: str
    label: str
    sub: str
    kind: str
    tree_hash: str
    resume_state: str
    superseded: bool

    @property
    def can_go_back(self) -> bool:
        return bool(self.resume_state)


_PHASES = {
    "created": "Plan", "preflight": "Plan", "scoping": "Plan",
    "context_expanding": "Plan", "tasks_ready": "Plan",
    "workspace_ready": "Build", "implementing": "Build",
    "implemented": "Build", "repairing": "Build",
    "reviewing": "Review", "changes_requested": "Review",
    "testing": "Test", "test_failed": "Test",
    "verified": "Save", "awaiting_acceptance": "Save",
    "accepted": "Save", "delivering": "Save",
    "needs_human_delivery": "Save",
}


def run_phase(state: str, tasks: list) -> str:
    """The loop step a run stands in, for the home card.

    A paused run keeps the state needs_human; the presence of planned
    tasks then separates a plan-stage pause from a build-stage one.
    """
    if state == "needs_human":
        return "Build" if tasks else "Plan"
    return _PHASES.get(state, "")


# Parsed summaries, keyed by runtime root then run id, each pinned to
# its record file's stamp. A home visit re-reads only changed records;
# with hundreds of runs the difference is a screenful of file reads
# and path resolutions on every navigation.
_SUMMARY_CACHE: dict[str, dict[str, tuple[int, int, str, RunSummary]]] = {}


def list_runs(runtime_root: Path, repository: Path | None = None) -> list[RunSummary]:
    """Newest-first run summaries for one repository."""
    root = Path(runtime_root).expanduser()
    if not root.is_dir():
        return []
    cache = _SUMMARY_CACHE.setdefault(str(root), {})
    target = str(Path(repository).resolve()) if repository is not None else None
    summaries: list[RunSummary] = []
    seen: set[str] = set()
    for run_dir in root.iterdir():
        record_path = run_dir / "run.json"
        try:
            stamp = record_path.stat()
        except OSError:
            continue
        if not run_dir.is_dir():
            continue
        seen.add(run_dir.name)
        cached = cache.get(run_dir.name)
        if (cached is not None and cached[0] == stamp.st_mtime_ns
                and cached[1] == stamp.st_size):
            resolved_repository, summary = cached[2], cached[3]
        else:
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            try:
                resolved_repository = str(
                    Path(str(record.get("repository", ""))).resolve())
            except OSError:
                continue
            evidence = record.get("evidence", {})
            changed = (evidence.get("changed_files", [])
                       if isinstance(evidence, dict) else [])
            delivery = (evidence.get("delivery", {})
                        if isinstance(evidence, dict) else {})
            if not isinstance(delivery, dict):
                delivery = {}
            awaiting = bool(
                delivery.get("commit")
                and not delivery.get("integrated_commit")
                and str(record.get("state", "")) in {
                    "delivered", "needs_human_delivery"})
            summary = RunSummary(
                run_id=str(record.get("run_id", run_dir.name)),
                mode=str(record.get("mode", "feature")),
                request=str(record.get("request", "")),
                state=str(record.get("state", "")),
                updated_at=str(record.get("updated_at", "")),
                created_at=str(record.get("created_at", "")),
                repository=str(record.get("repository", "")),
                changed_files=len(changed) if isinstance(changed, list) else 0,
                name=str(record.get("name", "")),
                phase=run_phase(str(record.get("state", "")),
                                record.get("tasks") or []),
                awaiting_files=awaiting,
                delivered_commit=str(delivery.get("commit", "")),
            )
            cache[run_dir.name] = (stamp.st_mtime_ns, stamp.st_size,
                                   resolved_repository, summary)
        if target is not None and resolved_repository != target:
            continue
        summaries.append(summary)
    for stale in set(cache) - seen:
        del cache[stale]
    summaries.sort(key=lambda item: item.updated_at, reverse=True)
    return summaries


def run_timeline(runtime_root: Path, run_id: str) -> list[IterationEvent]:
    """The run's iteration events in order, with superseded marks after reverts."""
    ledger = Path(runtime_root).expanduser() / run_id / "audit.jsonl"
    if not ledger.is_file():
        return []
    raw: list[dict] = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "iteration":
            raw.append(event)
    superseded: set[int] = set()
    for event in raw:
        payload = event.get("payload", {})
        if payload.get("kind") != "revert":
            continue
        target = int(payload.get("target_sequence", 0) or 0)
        for other in raw:
            if target < int(other.get("sequence", 0)) < int(event.get("sequence", 0)):
                superseded.add(int(other.get("sequence", 0)))
    timeline: list[IterationEvent] = []
    for event in raw:
        payload = event.get("payload", {})
        sequence = int(event.get("sequence", 0))
        timeline.append(IterationEvent(
            sequence=sequence,
            time=str(event.get("time", "")),
            label=str(payload.get("label", "")),
            sub=str(payload.get("sub", "")),
            kind=str(payload.get("kind", "")),
            tree_hash=str(payload.get("tree_hash", "")),
            resume_state=str(payload.get("resume_state", "")),
            superseded=sequence in superseded,
        ))
    return timeline
