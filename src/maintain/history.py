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


def list_runs(runtime_root: Path, repository: Path | None = None) -> list[RunSummary]:
    """Newest-first run summaries for one repository."""
    root = Path(runtime_root).expanduser()
    if not root.is_dir():
        return []
    summaries: list[RunSummary] = []
    for run_dir in root.iterdir():
        record_path = run_dir / "run.json"
        if not run_dir.is_dir() or not record_path.is_file():
            continue
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if repository is not None:
            try:
                if Path(str(record.get("repository", ""))).resolve() != Path(
                        repository).resolve():
                    continue
            except OSError:
                continue
        evidence = record.get("evidence", {})
        changed = evidence.get("changed_files", []) if isinstance(evidence, dict) else []
        summaries.append(RunSummary(
            run_id=str(record.get("run_id", run_dir.name)),
            mode=str(record.get("mode", "feature")),
            request=str(record.get("request", "")),
            state=str(record.get("state", "")),
            updated_at=str(record.get("updated_at", "")),
            created_at=str(record.get("created_at", "")),
            repository=str(record.get("repository", "")),
            changed_files=len(changed) if isinstance(changed, list) else 0,
        ))
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
