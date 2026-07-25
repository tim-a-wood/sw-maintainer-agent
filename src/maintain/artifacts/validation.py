"""Shared artifact validation findings and safe path rules."""
from __future__ import annotations
import fnmatch
import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath

@dataclass(frozen=True)
class ValidationFinding:
    rule_id: str
    severity: str
    location: str
    problem: str
    required_correction: str

class ArtifactValidationError(ValueError):
    def __init__(self, findings: list[ValidationFinding]):
        self.findings = tuple(findings)
        super().__init__("; ".join(f"{x.rule_id}: {x.problem}" for x in findings))

_DRIVE = re.compile(r"^[A-Za-z]:")

def validate_repository_path(path: str, *, protected: tuple[str, ...] = (),
                             excluded: tuple[str, ...] = ()) -> str:
    findings: list[ValidationFinding] = []
    normalized = unicodedata.normalize("NFC", path)
    candidate = PurePosixPath(normalized)
    if (not normalized or path != normalized or "\\" in path or _DRIVE.match(path) or
            candidate.is_absolute() or any(p in {"", ".", ".."} for p in candidate.parts)):
        findings.append(ValidationFinding("REQ-SEC-006", "HIGH", path, "Unsafe repository path.",
                                          "Use one normalized repository-relative POSIX path."))
    if any(fnmatch.fnmatch(normalized, p) for p in protected):
        findings.append(ValidationFinding("REQ-SEC-005", "HIGH", path, "Protected path.",
                                          "Remove the protected operation."))
    if any(fnmatch.fnmatch(normalized, p) for p in excluded):
        findings.append(ValidationFinding("REQ-SEC-005", "HIGH", path, "Excluded path.",
                                          "Remove the excluded operation."))
    if findings:
        raise ArtifactValidationError(findings)
    return normalized

TRANSCRIPT_MARKERS = ("You said:", "Copilot said:", "New chat", "Message Copilot",
                      "AI-generated content may be incorrect")
def has_transcript_contamination(text: str) -> bool:
    return sum(marker in text for marker in TRANSCRIPT_MARKERS) >= 2
