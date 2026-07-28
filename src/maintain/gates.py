"""Human decision gates in the workflow loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import PolicyError
from .models import RunRecord


@dataclass(frozen=True)
class GateDecision:
    """One human decision at a loop gate."""

    action: str  # "accept" | "repair" | "rescope"
    note: str = ""


class GateStop(PolicyError):
    """The person stopped the run at a gate. The run pauses and can resume."""

    def __init__(self, message: str = "The run is stopped. Continue it from the home screen.") -> None:
        super().__init__(message)


class WorkflowGates:
    """Default gates keep the existing automatic behavior."""

    def plan_review(self, record: RunRecord, tasks: list[dict[str, Any]]) -> GateDecision:
        """Called once per plan. accept continues; rescope re-plans with the note."""
        return GateDecision("accept")

    def review_findings(self, record: RunRecord,
                        findings: list[dict[str, Any]]) -> GateDecision:
        """Called when the review requests changes. repair or rescope."""
        return GateDecision("repair")

    def test_failure(self, record: RunRecord,
                     results: list[dict[str, Any]]) -> GateDecision:
        """Called when a local check fails. repair or rescope."""
        return GateDecision("repair")
