"""Versioned workflow records and provider envelopes."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunState(StrEnum):
    CREATED = "created"
    PREFLIGHT = "preflight"
    SCOPING = "scoping"
    CONTEXT_EXPANDING = "context_expanding"
    TASKS_READY = "tasks_ready"
    WORKSPACE_READY = "workspace_ready"
    IMPLEMENTING = "implementing"
    IMPLEMENTED = "implemented"
    REVIEWING = "reviewing"
    CHANGES_REQUESTED = "changes_requested"
    TESTING = "testing"
    TEST_FAILED = "test_failed"
    REPAIRING = "repairing"
    VERIFIED = "verified"
    AWAITING_ACCEPTANCE = "awaiting_acceptance"
    ACCEPTED = "accepted"
    DELIVERING = "delivering"
    DELIVERED = "delivered"
    NEEDS_HUMAN_DELIVERY = "needs_human_delivery"
    NEEDS_HUMAN = "needs_human"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ProviderCapabilities:
    structured_output: bool = True
    streaming: bool = False
    multi_turn: bool = True
    accepts_file_content: bool = True
    returns_unified_diff: bool = True
    can_edit_workspace: bool = False
    can_run_commands: bool = False
    browser_automation: bool = False
    sandbox_code_execution: bool = False
    internet_tools_in_sandbox: bool = False
    matlab_execution: bool = False
    enterprise_data_boundary: str = "configured"


@dataclass(frozen=True)
class ProviderRequest:
    schema_version: int
    run_id: str
    task_id: str
    role: str
    instructions: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class ProviderResponse:
    schema_version: int
    run_id: str
    task_id: str
    role: str
    content: dict[str, Any]
    provider: str
    conversation_id: str = ""


@dataclass
class RunRecord:
    run_id: str
    mode: str
    request: str
    repository: str
    base_commit: str
    branch: str
    worktree: str
    state: str = RunState.CREATED
    sequence: int = 0
    attempt: int = 0
    task_index: int = 0
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    config_hash: str = ""
    tree_hash: str = ""
    accepted_tree_hash: str = ""
    tasks: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RunRecord":
        return cls(**value)
