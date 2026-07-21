"""Atomic artifacts and a verifiable hash-chained event ledger."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .errors import RecoveryError
from .locking import FileLock
from .models import utc_now


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def media_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


class AuditStore:
    def __init__(self, runtime_root: Path, run_id: str) -> None:
        self.run_id = run_id
        self.run_dir = runtime_root / run_id
        self.artifacts = self.run_dir / "artifacts"
        self.ledger = self.run_dir / "audit.jsonl"
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def write_artifact(self, name: str, value: object) -> dict[str, Any]:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Artifact path must be relative.")
        data = value if isinstance(value, bytes) else canonical(value)
        path = self.artifacts / relative
        atomic_write(path, data)
        artifact = {"path": f"artifacts/{relative.as_posix()}", "bytes": len(data),
                    "sha256": sha256(data), "media_type": media_type(relative)}
        self.append("artifact_written", {"artifacts": [artifact]})
        return artifact

    def register_artifact(self, path: Path) -> dict[str, Any]:
        path = path.resolve()
        try:
            relative = path.relative_to(self.run_dir)
        except ValueError as exc:
            raise ValueError("Registered artifact is outside the run directory.") from exc
        data = path.read_bytes()
        artifact = {"path": relative.as_posix(), "bytes": len(data), "sha256": sha256(data),
                    "media_type": media_type(relative)}
        self.append("artifact_registered", {"artifacts": [artifact]})
        return artifact

    def append(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        with FileLock(self.run_dir / "audit.lock", f"audit append {self.run_id}"):
            previous = "0" * 64
            sequence = 1
            if self.ledger.exists():
                lines = self.ledger.read_text(encoding="utf-8").splitlines()
                if lines:
                    last = json.loads(lines[-1])
                    previous = last["event_hash"]
                    sequence = int(last["sequence"]) + 1
            body = {"schema_version": 1, "sequence": sequence, "time": utc_now(),
                    "run_id": self.run_id, "type": event_type, "actor": "maintain",
                    "previous_hash": previous, "payload": payload}
            event = {**body, "event_hash": sha256(canonical(body))}
            with self.ledger.open("ab") as stream:
                stream.write(canonical(event) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            return event

    def save_record(self, record: object) -> None:
        value = record.to_dict() if hasattr(record, "to_dict") else record
        atomic_write(self.run_dir / "run.json", canonical(value))

    def verify(self) -> dict[str, int]:
        previous = "0" * 64
        count = 0
        declared: set[str] = set()
        if not self.ledger.exists():
            raise RecoveryError("Audit ledger is missing.")
        for expected, line in enumerate(self.ledger.read_text(encoding="utf-8").splitlines(), 1):
            event = json.loads(line)
            claimed = event.pop("event_hash")
            if event.get("sequence") != expected or event.get("previous_hash") != previous:
                raise RecoveryError(f"Audit chain is invalid at event {expected}.")
            if sha256(canonical(event)) != claimed:
                raise RecoveryError(f"Audit event {expected} was modified.")
            for artifact in event.get("payload", {}).get("artifacts", []):
                declared.add(artifact["path"])
                path = self.run_dir / artifact["path"]
                if not path.is_file() or sha256(path.read_bytes()) != artifact["sha256"]:
                    raise RecoveryError(f"Audit artifact is missing or modified: {artifact['path']}")
            previous = claimed
            count += 1
        actual = {path.relative_to(self.run_dir).as_posix()
                  for path in self.artifacts.rglob("*") if path.is_file()}
        if actual != declared:
            difference = sorted(actual.symmetric_difference(declared))
            raise RecoveryError(f"Audit artifact inventory does not match: {difference[:3]}")
        return {"events": count}

    def export(self, destination: Path) -> Path:
        self.verify()
        files = [path for path in self.run_dir.rglob("*")
                 if path.is_file() and path.name != "run.lock"]
        manifest = {path.relative_to(self.run_dir).as_posix(): sha256(path.read_bytes())
                    for path in sorted(files)}
        destination = destination.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(files):
                archive.write(path, path.relative_to(self.run_dir).as_posix())
            archive.writestr("EXPORT-MANIFEST.json", canonical(manifest))
            archive.writestr("INDEX.json", canonical({"schema_version": 1, "run_id": self.run_id,
                                                       "files": manifest}))
            archive.writestr(
                "VERIFY.txt",
                "Verify each listed SHA-256 hash. Then run maintain audit verify RUN_ID "
                "against the extracted run directory.\n",
            )
        return destination


def cleanup_runs(runtime_root: Path, older_than_days: int,
                 now: datetime | None = None) -> list[str]:
    """Remove only old, terminal, unaccepted runs after audit verification."""
    if older_than_days < 1:
        raise ValueError("Retention must be at least one day.")
    root = runtime_root.expanduser().resolve()
    if not root.is_dir():
        return []
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=older_than_days)
    removed: list[str] = []
    for run_dir in sorted(root.iterdir()):
        record_path = run_dir / "run.json"
        if not run_dir.is_dir() or not record_path.is_file():
            continue
        try:
            run_dir.resolve().relative_to(root)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            updated = datetime.fromisoformat(str(record["updated_at"]))
        except (ValueError, KeyError, json.JSONDecodeError, OSError) as exc:
            raise RecoveryError(f"Cannot evaluate retention for {run_dir.name}: {exc}") from exc
        if record.get("state") not in {"failed", "cancelled"} or updated >= cutoff:
            continue
        if record.get("accepted_tree_hash") or record.get("state") in {"accepted", "delivered"}:
            continue
        AuditStore(root, run_dir.name).verify()
        shutil.rmtree(run_dir)
        removed.append(run_dir.name)
    return removed
