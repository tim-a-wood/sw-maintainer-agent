"""Human-readable browser exchange packages with focused repository context."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import ProviderRequest


@dataclass(frozen=True)
class ExchangePackage:
    paths: tuple[Path, ...]
    sha256: str
    bytes: int


def build_exchange_package(request: ProviderRequest, directory: Path) -> ExchangePackage:
    """Build no more than three files for a browser-based assistant."""
    directory.mkdir(parents=True, exist_ok=True)
    source_files = _source_files(request.payload)
    task_name = "TASK.md"
    code_name = "CODEBASE.md"
    manifest_name = "MANIFEST.json"

    codebase = _codebase_markdown(source_files, request.payload.get("diff"))
    task = _task_markdown(request, code_name, manifest_name)
    task_path, code_path, manifest_path = (
        directory / task_name, directory / code_name, directory / manifest_name
    )
    task_path.write_text(task, encoding="utf-8")
    code_path.write_text(codebase, encoding="utf-8")

    manifest = {
        "package_version": 1,
        "schema_version": request.schema_version,
        "run_id": request.run_id,
        "task_id": request.task_id,
        "role": request.role,
        "attachments": [
            _file_record(task_path, "task"),
            _file_record(code_path, "focused_codebase"),
        ],
        "context_files": [
            {"path": path, "bytes": len(content.encode()),
             "sha256": hashlib.sha256(content.encode()).hexdigest()}
            for path, content in source_files
        ],
        "payload": _manifest_payload(request.payload),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    paths = (task_path, code_path, manifest_path)
    digest = hashlib.sha256()
    total = 0
    for path in paths:
        data = path.read_bytes()
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(data)
        total += len(data)
    return ExchangePackage(paths, digest.hexdigest(), total)


def _task_markdown(request: ProviderRequest, code_name: str, manifest_name: str) -> str:
    output = (
        "Create and attach `maintain-output.zip`. Put each complete changed file at its exact "
        "repository-relative path in the ZIP. Do not add an outer directory or unrelated files. "
        "For an approved deletion, omit that file from the ZIP and list it in `deleted_files`. "
        "Also return the JSON envelope below in the chat. In `content`, include `changed_files` "
        "for every replaced, added, or deleted path; include `deleted_files` (an empty list when "
        "none); and, for an issue, include `root_cause`. Do not put a patch in the JSON."
        if request.role == "implement" else
        "Return only the JSON envelope below in the chat. Do not create or attach output files."
    )
    role_contract = {
        "scope": (
            "Return `content.tasks` in dependency order. Each task must contain `id`, `objective`, "
            "`allowed_files`, `done_when`, `verification`, and `depends_on`. If essential code is "
            "missing, return `content.context_queries` instead of tasks or guesses."
        ),
        "implement": (
            "Return `content.changed_files` and `content.deleted_files` as exact repository paths. "
            "Issue work must also return `content.root_cause.statement` and code-grounded "
            "`content.root_cause.evidence_paths`."
        ),
        "review": (
            "Return `content.decision` as `approve` or `changes_requested`. Return "
            "`content.findings` as a list. Each finding must contain `severity`, `file`, `line`, "
            "`evidence`, and `remediation`. Use an empty list when there are no findings."
        ),
    }.get(request.role, "Return a concise factual result in `content`.")
    examples: dict[str, dict[str, Any]] = {
        "scope": {
            "tasks": [{
                "id": "short-task-id",
                "objective": "One exact outcome",
                "allowed_files": ["path/from/the/file/map"],
                "done_when": ["Observable completion condition"],
                "verification": ["How the result will be checked"],
                "depends_on": [],
            }],
            "context_queries": [],
        },
        "implement": {
            "changed_files": ["exact/repository/path"],
            "deleted_files": [],
            "root_cause": {
                "statement": "Required for issue work; omit for feature work.",
                "evidence_paths": ["exact/repository/path"],
            },
        },
        "review": {
            "decision": "approve",
            "findings": [],
        },
    }
    envelope = {
        "schema_version": request.schema_version,
        "run_id": request.run_id,
        "task_id": request.task_id,
        "role": request.role,
        "provider": "assistant",
        "conversation_id": "assigned-by-maintain",
        "content": examples.get(request.role, {"summary": "Concise factual result"}),
    }
    return (
        "# Maintenance task\n\n"
        f"- Run: `{request.run_id}`\n"
        f"- Task: `{request.task_id}`\n"
        f"- Role: `{request.role}`\n\n"
        "## Required action\n\n"
        f"{request.instructions.strip()}\n\n"
        "## Attached context\n\n"
        f"Read `{code_name}` for the complete focused code context and its file index. "
        f"Read `{manifest_name}` for exact identifiers, hashes, task data, and evidence. "
        "Use only these attachments. Do not use internet tools.\n\n"
        "## Required output\n\n"
        f"{output}\n\n{role_contract}\n\n"
        "```json\n"
        f"{json.dumps(envelope, ensure_ascii=False, indent=2)}\n"
        "```\n"
    )


def _codebase_markdown(source_files: list[tuple[str, str]], diff: object) -> str:
    lines = [
        "# Focused codebase",
        "",
        "This document contains all repository code supplied for this task.",
        "",
        "## File map",
        "",
    ]
    if source_files:
        for index, (path, content) in enumerate(source_files, 1):
            digest = hashlib.sha256(content.encode()).hexdigest()[:12]
            lines.append(f"{index}. `{path}` — {len(content.encode())} bytes — SHA-256 `{digest}`")
    else:
        lines.append("No complete repository files were required for this exchange.")
    lines.extend(["", "## File contents", ""])
    for path, content in source_files:
        language = _language(path)
        fence = "`" * max(4, _longest_backtick_run(content) + 1)
        lines.extend([
            f"### `{path}`",
            "",
            f"{fence}{language}",
            content.rstrip("\n"),
            fence,
            "",
        ])
    if isinstance(diff, str) and diff.strip():
        fence = "`" * max(4, _longest_backtick_run(diff) + 1)
        lines.extend([
            "## Current change",
            "",
            "This is the exact repository diff for review or repair.",
            "",
            f"{fence}diff",
            diff.rstrip("\n"),
            fence,
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def _source_files(payload: dict[str, Any]) -> list[tuple[str, str]]:
    found: dict[str, str] = {}
    candidates = payload.get("candidate_files")
    if isinstance(candidates, list):
        for item in candidates:
            if isinstance(item, dict) and isinstance(item.get("path"), str) and isinstance(
                    item.get("content"), str):
                found[item["path"]] = item["content"]
    files = payload.get("files")
    if isinstance(files, dict):
        for path, content in files.items():
            if isinstance(path, str) and isinstance(content, str):
                found[path] = content
    return list(found.items())


def _manifest_payload(payload: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(payload)
    candidates = value.get("candidate_files")
    if isinstance(candidates, list):
        for item in candidates:
            if isinstance(item, dict) and isinstance(item.get("content"), str):
                content = item.pop("content")
                item["content_location"] = "CODEBASE.md"
                item.setdefault("bytes", len(content.encode()))
                item.setdefault("sha256", hashlib.sha256(content.encode()).hexdigest())
    files = value.get("files")
    if isinstance(files, dict):
        value["files"] = {
            path: {"content_location": "CODEBASE.md", "bytes": len(content.encode()),
                   "sha256": hashlib.sha256(content.encode()).hexdigest()}
            for path, content in files.items() if isinstance(path, str) and isinstance(content, str)
        }
    diff = value.get("diff")
    if isinstance(diff, str):
        value["diff"] = {"content_location": "CODEBASE.md#current-change",
                         "bytes": len(diff.encode()),
                         "sha256": hashlib.sha256(diff.encode()).hexdigest()}
    return value


def _file_record(path: Path, purpose: str) -> dict[str, object]:
    data = path.read_bytes()
    return {"name": path.name, "purpose": purpose, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def _longest_backtick_run(value: str) -> int:
    import re
    return max((len(item) for item in re.findall(r"`+", value)), default=0)


def _language(path: str) -> str:
    return {
        ".py": "python", ".js": "javascript", ".jsx": "jsx", ".ts": "typescript",
        ".tsx": "tsx", ".json": "json", ".md": "markdown", ".html": "html",
        ".css": "css", ".yml": "yaml", ".yaml": "yaml", ".toml": "toml",
        ".m": "matlab", ".c": "c", ".cpp": "cpp", ".h": "c",
    }.get(Path(path).suffix.casefold(), "text")
