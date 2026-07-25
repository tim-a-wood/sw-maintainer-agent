"""Transport-neutral implementation operation contracts."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

OperationType = Literal["add", "modify", "delete"]
TransportType = Literal["markdown", "zip"]

@dataclass(frozen=True)
class FileOperation:
    operation: OperationType
    path: str
    staged_content: Path | None

@dataclass(frozen=True)
class ImplementationArtifact:
    source_path: Path
    transport: TransportType
    operations: tuple[FileOperation, ...]
    summary: str
