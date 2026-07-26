"""Validated, reproducible supporting material for browser assistants."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping
from urllib.parse import urlsplit

from .errors import ConfigurationError


DEFAULT_REFERENCE_MAX_BYTES = 10 * 1024 * 1024
MAX_REFERENCE_URL_BYTES = 4096
RESERVED_PACKAGE_NAMES = frozenset({
    "task.md",
    "codebase.md",
    "manifest.json",
})


@dataclass(frozen=True)
class CopilotReference:
    """One frozen local file or one user-provided HTTPS reference."""

    kind: Literal["file", "url"]
    source: str
    name: str
    path: Path | None
    bytes: int | None
    sha256: str | None

    def to_dict(self, *, run_dir: Path | None = None) -> dict[str, str | int | None]:
        """Return JSON-safe metadata, optionally with a run-relative snapshot path."""
        path_value: str | None = None
        if self.path is not None:
            resolved = self.path.resolve()
            if run_dir is None:
                path_value = str(resolved)
            else:
                root = run_dir.expanduser().resolve()
                if not resolved.is_relative_to(root):
                    raise ConfigurationError(
                        "The Copilot reference snapshot is outside the run directory.")
                path_value = resolved.relative_to(root).as_posix()
        return {
            "kind": self.kind,
            "source": self.source,
            "name": self.name,
            "path": path_value,
            "bytes": self.bytes,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(
            cls,
            value: Mapping[str, object],
            *,
            run_dir: Path | None = None,
    ) -> "CopilotReference":
        """Load strict saved metadata and verify its frozen local snapshot."""
        fields = {"kind", "source", "name", "path", "bytes", "sha256"}
        if set(value) != fields:
            raise ConfigurationError(
                "The saved Copilot reference metadata has unexpected or missing fields.")
        kind, source, name = value["kind"], value["source"], value["name"]
        if kind not in {"file", "url"}:
            raise ConfigurationError("The saved Copilot reference kind is invalid.")
        if not isinstance(source, str) or not source.strip():
            raise ConfigurationError("The saved Copilot reference source is invalid.")
        if not isinstance(name, str) or not name:
            raise ConfigurationError("The saved Copilot reference name is invalid.")

        if kind == "url":
            if any(value[field] is not None for field in ("path", "bytes", "sha256")):
                raise ConfigurationError(
                    "The saved Copilot URL reference must not contain file metadata.")
            reference = cls(
                kind="url",
                source=source,
                name=name,
                path=None,
                bytes=None,
                sha256=None,
            )
            verify_reference(reference)
            return reference

        saved_path = value["path"]
        saved_bytes = value["bytes"]
        saved_sha256 = value["sha256"]
        if not isinstance(saved_path, str) or not saved_path:
            raise ConfigurationError(
                "The saved Copilot file reference path is invalid.")
        if (
            isinstance(saved_bytes, bool)
            or not isinstance(saved_bytes, int)
            or saved_bytes <= 0
        ):
            raise ConfigurationError(
                "The saved Copilot file reference size is invalid.")
        if (
            not isinstance(saved_sha256, str)
            or len(saved_sha256) != 64
            or any(character not in "0123456789abcdef" for character in saved_sha256)
        ):
            raise ConfigurationError(
                "The saved Copilot file reference digest is invalid.")

        candidate = Path(saved_path)
        if run_dir is None:
            if not candidate.is_absolute():
                raise ConfigurationError(
                    "A saved relative Copilot reference path needs its run directory.")
            resolved = candidate.resolve()
        else:
            if candidate.is_absolute():
                raise ConfigurationError(
                    "A saved Copilot reference path must be relative to its run directory.")
            root = run_dir.expanduser().resolve()
            resolved = (root / candidate).resolve()
            if not resolved.is_relative_to(root):
                raise ConfigurationError(
                    "The saved Copilot reference path escapes its run directory.")

        reference = cls(
            kind="file",
            source=source,
            name=name,
            path=resolved,
            bytes=saved_bytes,
            sha256=saved_sha256,
        )
        verify_reference(reference)
        return reference


def validate_reference(
        value: str | Path, *, max_bytes: int = DEFAULT_REFERENCE_MAX_BYTES
) -> CopilotReference:
    """Validate one local file or HTTPS URL without copying the file."""
    _validate_max_bytes(max_bytes)
    raw = str(value).strip()
    if not raw:
        raise ConfigurationError("A Copilot reference cannot be empty.")

    if _looks_like_url(raw):
        if len(raw.encode("utf-8")) > MAX_REFERENCE_URL_BYTES:
            raise ConfigurationError(
                f"A Copilot reference URL cannot exceed {MAX_REFERENCE_URL_BYTES} encoded bytes.")
        parsed = urlsplit(raw)
        if parsed.scheme.casefold() != "https" or not parsed.hostname:
            raise ConfigurationError(
                "A Copilot reference URL must be a complete HTTPS URL.")
        if any(character.isspace() or ord(character) < 32 for character in raw):
            raise ConfigurationError(
                "A Copilot reference URL cannot contain whitespace or control characters.")
        name = parsed.hostname
        return CopilotReference(
            kind="url",
            source=raw,
            name=name,
            path=None,
            bytes=None,
            sha256=None,
        )

    path_value = value if isinstance(value, Path) else raw
    path = Path(path_value).expanduser().resolve()
    size, digest = _local_file_metadata(path, max_bytes=max_bytes)
    return CopilotReference(
        kind="file",
        source=str(path),
        name=path.name,
        path=path,
        bytes=size,
        sha256=digest,
    )


def prepare_reference(
        value: str | Path,
        destination: Path,
        *,
        max_bytes: int = DEFAULT_REFERENCE_MAX_BYTES,
) -> CopilotReference:
    """Validate and snapshot one reference into a run-owned directory."""
    reference = validate_reference(value, max_bytes=max_bytes)
    if reference.kind == "url":
        return reference

    assert reference.path is not None
    source = reference.path
    snapshot_dir = destination.expanduser().resolve()
    try:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigurationError(
            f"The Copilot reference snapshot directory could not be created: {destination}"
        ) from exc
    if not snapshot_dir.is_dir():
        raise ConfigurationError(
            f"The Copilot reference snapshot destination is not a directory: {destination}")

    snapshot = snapshot_dir / reference.name
    if snapshot.exists() and snapshot.resolve() != source:
        raise ConfigurationError(
            f"A Copilot reference snapshot already exists: {snapshot}")
    if snapshot.resolve() != source:
        try:
            shutil.copy2(source, snapshot)
        except OSError as exc:
            snapshot.unlink(missing_ok=True)
            raise ConfigurationError(
                f"The Copilot reference could not be snapshotted: {source}") from exc

    size, digest = _local_file_metadata(snapshot, max_bytes=max_bytes)
    if digest != reference.sha256 or size != reference.bytes:
        snapshot.unlink(missing_ok=True)
        raise ConfigurationError(
            "The Copilot reference changed while it was being snapshotted.")
    return CopilotReference(
        kind="file",
        source=reference.source,
        name=reference.name,
        path=snapshot,
        bytes=size,
        sha256=digest,
    )


def verify_reference(
        reference: CopilotReference, *, max_bytes: int = DEFAULT_REFERENCE_MAX_BYTES
) -> None:
    """Verify that a prepared reference is internally consistent and unchanged."""
    _validate_max_bytes(max_bytes)
    if reference.kind == "url":
        validated = validate_reference(reference.source, max_bytes=max_bytes)
        if (
            reference.path is not None
            or reference.bytes is not None
            or reference.sha256 is not None
            or reference.name != validated.name
        ):
            raise ConfigurationError("The Copilot URL reference metadata is invalid.")
        return
    if reference.kind != "file" or reference.path is None:
        raise ConfigurationError("The Copilot file reference metadata is invalid.")
    size, digest = _local_file_metadata(reference.path, max_bytes=max_bytes)
    if (
        reference.name != reference.path.name
        or reference.bytes != size
        or reference.sha256 != digest
    ):
        raise ConfigurationError(
            "The snapshotted Copilot reference changed after it was prepared.")


def reference_submission_line(reference: CopilotReference | None) -> str:
    """Describe a URL reference without claiming that Maintain fetched it."""
    if reference is None or reference.kind != "url":
        return ""
    verify_reference(reference)
    return (
        f"User-provided read-only reference URL: {reference.source}. "
        "Maintain did not open or verify the content at this URL. Use it only as "
        "background material if you can access it, and do not claim to have read it "
        "unless you actually did."
    )


def _local_file_metadata(path: Path, *, max_bytes: int) -> tuple[int, str]:
    if path.name.casefold() in RESERVED_PACKAGE_NAMES:
        raise ConfigurationError(
            f"The Copilot reference filename is reserved by Maintain: {path.name}")
    if not path.exists():
        raise ConfigurationError(f"The Copilot reference file does not exist: {path}")
    if not path.is_file():
        raise ConfigurationError(f"The Copilot reference is not a file: {path}")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ConfigurationError(
            f"The Copilot reference file could not be inspected: {path}") from exc
    if size == 0:
        raise ConfigurationError("The Copilot reference file is empty.")
    if size > max_bytes:
        raise ConfigurationError(
            f"The Copilot reference is too large ({size} bytes; maximum {max_bytes}).")
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ConfigurationError(
            f"The Copilot reference file could not be read: {path}") from exc
    return size, digest


def _looks_like_url(value: str) -> bool:
    parsed = urlsplit(value)
    return "://" in value or parsed.scheme.casefold() in {"http", "https"}


def _validate_max_bytes(max_bytes: int) -> None:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ConfigurationError("The Copilot reference size limit must be positive.")
