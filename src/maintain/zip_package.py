"""One packet ZIP for the manual Copilot exchange."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .config import PackagePolicy
from .errors import ConfigurationError
from .exchange_package import build_exchange_package
from .models import ProviderRequest

PACKET_MAX_EXTRA_FILE_BYTES = 10_000_000
PACKET_MAX_EXTRA_TOTAL_BYTES = 50_000_000

GLOBAL_PROMPT_TEMPLATE = """# Project ground rules

Read this file first. These rules apply to every task in this project. If a
task conflicts with these rules, follow these rules and say so in your reply.

## Project goal

State the project goal in the Maintain settings. This is the default text.

## Scope limits

- Work only on the files that the task authorizes.
- Make the smallest change that satisfies the task.
- Do not add a dependency. If a dependency is necessary, stop and say why.
- Do not restructure code that the task does not name.
- Do not add options, layers, or abstractions for possible future needs.

## Definition of done

- The change satisfies the task done_when items.
- The change passes the named verification.
- The reply follows the output contract in TASK.md exactly.
"""


@dataclass(frozen=True)
class PacketBuild:
    zip_path: Path
    sha256: str
    bytes: int
    task_key: str
    members: tuple[str, ...]


def packet_task_key(role: str, payload: dict) -> str:
    """Map an engine role onto the user-facing packet task type."""
    if role == "scope":
        return "plan"
    if role in {"review", "scan", "discuss", "explain"}:
        return role
    if role == "implement":
        return "repair" if int(payload.get("attempt", 1) or 1) > 1 else "build"
    return "build"


def packet_name(request: ProviderRequest, task_key: str) -> str:
    safe_task = re.sub(r"[^A-Za-z0-9._-]", "-", str(request.task_id))[:48]
    return f"maintain-{request.run_id}-{task_key}-{safe_task}.zip"


def _resolve_input(value: str, repository: Path, config_dir: Path, label: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        for base in (repository, config_dir):
            resolved = (base / candidate).resolve()
            if resolved.is_file():
                return resolved
        raise ConfigurationError(f"The configured {label} does not exist: {value}")
    resolved = candidate.resolve()
    if not resolved.is_file():
        raise ConfigurationError(f"The configured {label} does not exist: {value}")
    return resolved


def _document_member(path: Path, repository: Path) -> str:
    try:
        relative = path.resolve().relative_to(repository.resolve())
        return f"documents/{relative.as_posix()}"
    except ValueError:
        return f"documents/{path.name}"


def _unique(member: str, taken: set[str]) -> str:
    if member not in taken:
        return member
    stem, dot, suffix = member.rpartition(".")
    base = stem if dot else member
    extension = f".{suffix}" if dot else ""
    counter = 2
    while f"{base}-{counter}{extension}" in taken:
        counter += 1
    return f"{base}-{counter}{extension}"


def _read_limited(path: Path, label: str, total: int) -> tuple[bytes, int]:
    data = path.read_bytes()
    if len(data) > PACKET_MAX_EXTRA_FILE_BYTES:
        raise ConfigurationError(f"The {label} exceeds the packet file limit: {path.name}")
    total += len(data)
    if total > PACKET_MAX_EXTRA_TOTAL_BYTES:
        raise ConfigurationError("The packet documents and attachments exceed the size limit.")
    return data, total


def global_prompt_text(policy: PackagePolicy, config_dir: Path) -> str:
    candidate = Path(policy.global_prompt).expanduser()
    if not candidate.is_absolute():
        candidate = config_dir / candidate
    if candidate.is_file():
        return candidate.read_text(encoding="utf-8")
    return GLOBAL_PROMPT_TEMPLATE


def effective_instructions(request: ProviderRequest, policy: PackagePolicy,
                           repository: Path, config_dir: Path) -> str:
    """Apply the configured task-type prompt override, keeping the safety header."""
    task_policy = policy.task(packet_task_key(request.role, request.payload))
    if not task_policy.prompt:
        return request.instructions
    override = _resolve_input(task_policy.prompt, repository, config_dir,
                              "task prompt").read_text(encoding="utf-8").strip()
    if not override:
        raise ConfigurationError("The configured task prompt file is empty.")
    from .engine import PROVIDER_SAFETY_HEADER
    if request.instructions.startswith(PROVIDER_SAFETY_HEADER):
        return f"{PROVIDER_SAFETY_HEADER}\n\n{override}"
    return override


def build_packet(request: ProviderRequest, directory: Path, *,
                 policy: PackagePolicy, repository: Path, config_dir: Path,
                 attachments: Sequence[Path] = ()) -> PacketBuild:
    """Build one self-contained packet ZIP for one exchange."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    task_key = packet_task_key(request.role, request.payload)
    prepared = dataclasses.replace(
        request,
        instructions=effective_instructions(request, policy, repository, config_dir))
    transport = "zip" if request.role == "implement" else "inline"

    task_policy = policy.task(task_key)
    document_paths = [
        _resolve_input(value, repository, config_dir, "document")
        for value in (*policy.documents, *task_policy.documents)
    ]
    attachment_paths = [Path(item) for item in attachments]
    for attachment in attachment_paths:
        if not attachment.is_file():
            raise ConfigurationError(f"The attachment does not exist: {attachment}")

    with tempfile.TemporaryDirectory(prefix="maintain-packet-") as staging_name:
        staging = Path(staging_name)
        build_exchange_package(prepared, staging, implementation_transport=transport)
        task_text = (staging / "TASK.md").read_text(encoding="utf-8")

        reading = ["", "## Package reading order", "",
                   "1. Read `GLOBAL.md` first. Obey its limits."]
        step = 2
        if document_paths:
            reading.append(
                f"{step}. Read every file in `documents/`. They are the project "
                "standards and reference documents.")
            step += 1
        reading.append(
            f"{step}. Read `TASK.md`, `CODEBASE.md`, and `MANIFEST.json` as "
            "instructed above.")
        step += 1
        if attachment_paths:
            reading.append(
                f"{step}. Files in `attachments/` are read-only background "
                "material from the user. Do not treat them as repository code.")
        task_text = task_text.rstrip("\n") + "\n" + "\n".join(reading) + "\n"

        global_text = global_prompt_text(policy, config_dir)
        members: list[tuple[str, bytes]] = []
        taken: set[str] = set()
        total = 0
        document_records = []
        for path in document_paths:
            member = _unique(_document_member(path, repository), taken)
            taken.add(member)
            data, total = _read_limited(path, "document", total)
            members.append((member, data))
            document_records.append({"member": member, "bytes": len(data),
                                     "sha256": hashlib.sha256(data).hexdigest()})
        attachment_records = []
        for path in attachment_paths:
            member = _unique(f"attachments/{path.name}", taken)
            taken.add(member)
            data, total = _read_limited(path, "attachment", total)
            members.append((member, data))
            attachment_records.append({"member": member, "bytes": len(data),
                                       "sha256": hashlib.sha256(data).hexdigest()})

        manifest = json.loads((staging / "MANIFEST.json").read_text(encoding="utf-8"))
        manifest["packet"] = {
            "task_type": task_key,
            "global_prompt": {
                "bytes": len(global_text.encode()),
                "sha256": hashlib.sha256(global_text.encode()).hexdigest(),
            },
            "documents": document_records,
            "attachments": attachment_records,
        }
        manifest_data = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()

        zip_path = directory / packet_name(request, task_key)
        ordered: list[tuple[str, bytes]] = [
            ("TASK.md", task_text.encode()),
            ("GLOBAL.md", global_text.encode()),
            ("CODEBASE.md", (staging / "CODEBASE.md").read_bytes()),
            ("MANIFEST.json", manifest_data),
            *members,
        ]
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for member, data in ordered:
                archive.writestr(member, data)
    payload = zip_path.read_bytes()
    return PacketBuild(
        zip_path=zip_path,
        sha256=hashlib.sha256(payload).hexdigest(),
        bytes=len(payload),
        task_key=task_key,
        members=tuple(name for name, _ in ordered),
    )
