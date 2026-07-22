"""State transition and acceptance invariants."""

from __future__ import annotations

from maintain.errors import PolicyError
from maintain.models import RunRecord, RunState

TRANSITIONS = {
    RunState.CREATED: {RunState.PREFLIGHT, RunState.CANCELLED},
    RunState.PREFLIGHT: {RunState.WORKSPACE_READY, RunState.NEEDS_HUMAN, RunState.FAILED},
    RunState.WORKSPACE_READY: {RunState.SCOPING, RunState.CANCELLED},
    RunState.SCOPING: {RunState.CONTEXT_EXPANDING, RunState.NEEDS_HUMAN, RunState.FAILED},
    RunState.CONTEXT_EXPANDING: {RunState.TASKS_READY, RunState.NEEDS_HUMAN, RunState.FAILED},
    RunState.TASKS_READY: {RunState.IMPLEMENTING, RunState.NEEDS_HUMAN,
                           RunState.FAILED, RunState.CANCELLED},
    RunState.IMPLEMENTING: {RunState.IMPLEMENTED, RunState.NEEDS_HUMAN, RunState.FAILED},
    RunState.IMPLEMENTED: {RunState.REVIEWING},
    RunState.REVIEWING: {RunState.TESTING, RunState.CHANGES_REQUESTED, RunState.NEEDS_HUMAN},
    RunState.CHANGES_REQUESTED: {RunState.REPAIRING, RunState.NEEDS_HUMAN},
    RunState.REPAIRING: {RunState.IMPLEMENTED, RunState.NEEDS_HUMAN, RunState.FAILED},
    RunState.TESTING: {RunState.VERIFIED, RunState.TEST_FAILED, RunState.TASKS_READY,
                       RunState.NEEDS_HUMAN},
    RunState.TEST_FAILED: {RunState.REPAIRING, RunState.NEEDS_HUMAN},
    RunState.VERIFIED: {RunState.AWAITING_ACCEPTANCE},
    RunState.AWAITING_ACCEPTANCE: {RunState.ACCEPTED, RunState.REPAIRING, RunState.CANCELLED},
    RunState.ACCEPTED: {RunState.DELIVERING},
    RunState.DELIVERING: {RunState.DELIVERED, RunState.NEEDS_HUMAN},
    RunState.DELIVERED: {RunState.NEEDS_HUMAN_DELIVERY},
    RunState.NEEDS_HUMAN: {
        RunState.PREFLIGHT, RunState.SCOPING, RunState.CONTEXT_EXPANDING,
        RunState.TASKS_READY, RunState.IMPLEMENTING, RunState.REVIEWING, RunState.CHANGES_REQUESTED,
        RunState.TESTING, RunState.TEST_FAILED, RunState.REPAIRING, RunState.DELIVERING,
        RunState.CANCELLED,
    },
    RunState.NEEDS_HUMAN_DELIVERY: {RunState.DELIVERED},
}

for _active_state in tuple(TRANSITIONS):
    if _active_state not in {RunState.DELIVERING}:
        TRANSITIONS[_active_state].add(RunState.CANCELLED)


def transition(record: RunRecord, target: RunState, *, tree_hash: str = "") -> None:
    current = RunState(record.state)
    if target not in TRANSITIONS.get(current, set()):
        raise PolicyError(f"Invalid workflow transition: {current} to {target}.")
    if target is RunState.VERIFIED:
        review = record.evidence.get("review", {})
        tests = record.evidence.get("tests", {})
        if review.get("decision") != "approve" or review.get("tree_hash") != tree_hash:
            raise PolicyError("Independent review does not approve this tree.")
        if not tests.get("passed") or tests.get("tree_hash") != tree_hash:
            raise PolicyError("Local verification does not prove this tree.")
    if target is RunState.ACCEPTED:
        if not record.tree_hash or record.tree_hash != record.evidence.get("verified_tree_hash"):
            raise PolicyError("Only the unchanged verified tree can be accepted.")
        record.accepted_tree_hash = record.tree_hash
    record.state = target
    record.sequence += 1
    record.updated_at = __import__("maintain.models", fromlist=["utc_now"]).utc_now()
