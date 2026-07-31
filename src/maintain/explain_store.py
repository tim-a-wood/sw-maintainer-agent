"""Explain runs on disk, so they survive the application.

Each explain run keeps one state.json next to its packets and its
render output. The waiting run comes back on the home screen after a
restart; the finished ones make the browsable list of explanations
and their videos.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from maintain.config import ProjectConfig
from maintain.issue_packets import explain_dir
from maintain.models import ProviderRequest

WAITING = "waiting"
PASSED = "passed"
FAILED = "failed"
DISCARDED = "discarded"


def explain_root(config: ProjectConfig) -> Path:
    return Path(config.runtime_root).expanduser().parent / "explain"


def save_explain_state(config: ProjectConfig, run_id: str,
                       **changes) -> dict:
    """Merge the changes into the run's state.json and return it."""
    directory = explain_dir(config, run_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "state.json"
    state: dict = {}
    if path.is_file():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
    state.update(changes)
    state["run_id"] = run_id
    request = state.get("request")
    if isinstance(request, ProviderRequest):
        state["request"] = asdict(request)
    path.write_text(json.dumps(state, indent=2, default=str),
                    encoding="utf-8")
    return state


def load_explain_states(config: ProjectConfig) -> list[dict]:
    """Every recorded explain run, newest first, broken files skipped."""
    root = explain_root(config)
    if not root.is_dir():
        return []
    states: list[dict] = []
    for directory in root.iterdir():
        path = directory / "state.json"
        if not path.is_file():
            continue
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(state, dict) and state.get("run_id"):
            states.append(state)
    states.sort(key=lambda item: str(item.get("created_at", "")),
                reverse=True)
    return states


def resumable_explain(config: ProjectConfig) -> dict | None:
    """The newest waiting explain whose packet still exists, if any."""
    for state in load_explain_states(config):
        if state.get("status") != WAITING:
            continue
        packet = str(state.get("packet", ""))
        if packet and Path(packet).is_file():
            return state
    return None


def restore_request(state: dict) -> ProviderRequest | None:
    """The original request back from disk — same run and task ids, so
    a reply Copilot wrote for the old packet still validates."""
    data = state.get("request")
    if not isinstance(data, dict):
        return None
    try:
        return ProviderRequest(**data)
    except TypeError:
        return None
