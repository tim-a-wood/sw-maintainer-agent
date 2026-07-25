"""Typed Copilot artifact transport results."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

SUBMISSION_NOT_OBSERVED = "SUBMISSION_NOT_OBSERVED"
SUBMISSION_CONFIRMED = "SUBMISSION_CONFIRMED"
ARTIFACT_RECEIVED = "ARTIFACT_RECEIVED"
SUBMISSION_STATES = frozenset({SUBMISSION_NOT_OBSERVED, SUBMISSION_CONFIRMED, ARTIFACT_RECEIVED})

@dataclass(frozen=True)
class ArtifactResult:
    artifact_path: Path
    artifact_type: str
    requested_filename: str
    suggested_filename: str | None
    used_transcript_fallback: bool
    submission_state: str
    browser_session_attempts: int
    navigation_attempts: int
    submission_attempts: int
    download_attempts: int
    diagnostics: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        if self.submission_state not in SUBMISSION_STATES:
            raise ValueError(f"Unsupported submission state: {self.submission_state}")
        if self.artifact_type not in {"markdown", "zip"}:
            raise ValueError(f"Unsupported artifact type: {self.artifact_type}")
        for value in (self.browser_session_attempts, self.navigation_attempts,
                      self.submission_attempts, self.download_attempts):
            if value < 0:
                raise ValueError("Attempt counters cannot be negative.")
