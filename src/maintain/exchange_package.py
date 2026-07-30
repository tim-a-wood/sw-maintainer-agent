"""Human-readable browser exchange packages with focused repository context."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import ProviderRequest
from .references import CopilotReference, validate_reference, verify_reference


@dataclass(frozen=True)
class ExchangePackage:
    paths: tuple[Path, ...]
    sha256: str
    bytes: int


def build_exchange_package(
        request: ProviderRequest,
        directory: Path,
        reference_path: Path | None = None,
        *,
        reference: CopilotReference | None = None,
        implementation_transport: str = "inline",
) -> ExchangePackage:
    """Build three package files and, optionally, one frozen user reference."""
    if implementation_transport not in {"inline", "zip"}:
        raise ValueError("implementation_transport must be inline or zip.")
    if reference_path is not None and reference is not None:
        raise ValueError("Provide reference_path or reference, not both.")
    if reference is not None:
        verify_reference(reference)
        prepared_reference = reference
    elif reference_path is not None:
        prepared_reference = validate_reference(reference_path)
    else:
        prepared_reference = None
    directory.mkdir(parents=True, exist_ok=True)
    source_files = _source_files(request.payload)
    task_name = "TASK.md"
    code_name = "CODEBASE.md"
    manifest_name = "MANIFEST.json"

    codebase = _codebase_markdown(source_files, request.payload.get("diff"))
    task = _task_markdown(
        request,
        code_name,
        manifest_name,
        prepared_reference,
        implementation_transport,
    )
    task_path, code_path, manifest_path = (
        directory / task_name, directory / code_name, directory / manifest_name
    )
    task_path.write_text(task, encoding="utf-8")
    code_path.write_text(codebase, encoding="utf-8")

    attachments = [
        _file_record(task_path, "task"),
        _file_record(code_path, "focused_codebase"),
    ]
    if prepared_reference is not None and prepared_reference.kind == "file":
        assert prepared_reference.path is not None
        attachments.append(_file_record(prepared_reference.path, "user_reference"))
    manifest = {
        "package_version": 1,
        "schema_version": request.schema_version,
        "run_id": request.run_id,
        "task_id": request.task_id,
        "role": request.role,
        "attachments": attachments,
        "context_files": [
            {"path": path, "bytes": len(content.encode()),
             "sha256": hashlib.sha256(content.encode()).hexdigest()}
            for path, content in source_files
        ],
        "payload": _manifest_payload(request.payload),
    }
    if prepared_reference is not None:
        manifest["user_reference"] = _manifest_reference(prepared_reference)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    package_paths = [task_path, code_path, manifest_path]
    if prepared_reference is not None and prepared_reference.kind == "file":
        assert prepared_reference.path is not None
        package_paths.append(prepared_reference.path)
    paths = tuple(package_paths)
    digest = hashlib.sha256()
    total = 0
    for path in paths:
        data = path.read_bytes()
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(data)
        total += len(data)
    if prepared_reference is not None:
        verify_reference(prepared_reference)
    return ExchangePackage(paths, digest.hexdigest(), total)


def _task_markdown(
        request: ProviderRequest,
        code_name: str,
        manifest_name: str,
        reference: CopilotReference | None = None,
        implementation_transport: str = "inline",
) -> str:
    inline_output = (
        "Write one complete JSON envelope into one downloadable Markdown file and give the "
        "file for download; the person clicks download in the chat. Put the envelope inside "
        "one ```json code block and add nothing else to the file. Do not return a patch. In "
        "`content.files`, include one object with `path` and `content` for every added or "
        "modified file. `content` must be the complete final UTF-8 file, not a diff, excerpt, "
        "placeholder, or fenced block. Use an empty `files` list only for a deletion-only "
        "implementation. Put approved deletions in `content.deleted_files`. Set "
        "`content.changed_files` to exactly the union of the `files` paths and "
        "`deleted_files`, with no duplicates. Use only paths authorized by the supplied task."
    )
    zip_output = (
        "Create and attach one downloadable ZIP file named `maintain-output.zip`. At the ZIP "
        "root, include `IMPLEMENTATION.toml` plus every complete added or modified file under "
        "`files/` at its exact authorized repository-relative path. Do not add a wrapper "
        "directory, notes, patches, excerpts, placeholders, or undeclared files. The TOML manifest "
        "must use the exact run, task, and role values shown below, list added or modified paths in "
        "`files`, and list approved deletions in `deleted_files`. For issue work it must also "
        "contain `root_cause_statement` and `root_cause_evidence_paths`; feature work must omit "
        "those fields. Finish creating the downloadable ZIP, then reply only `Maintain output "
        "ready.` Do not return JSON, source code, a patch, or manifest content in the chat."
    )
    scene_output = (
        "Write one complete Python scene file into one downloadable Markdown file and give "
        "the file for download; the person clicks download in the chat. Put the scene inside "
        "exactly one fenced code block marked `python`, and no other code blocks. Short "
        "prose around the block is permitted and is ignored."
    )
    output = (
        zip_output if request.role == "implement" and implementation_transport == "zip"
        else inline_output if request.role == "implement"
        else scene_output if request.role == "explain" else
        "Write one complete JSON envelope into one downloadable Markdown file and give the "
        "file for download; the person clicks download in the chat. Put the envelope inside "
        "one ```json code block and add nothing else to the file."
    )
    role_contract = {
        "scope": (
            "Return `content.tasks` in dependency order. Each task must contain `id`, `objective`, "
            "`allowed_files`, `done_when`, `verification`, and `depends_on`. If essential code is "
            "missing, return `content.context_queries` instead of tasks or guesses. Existing-file "
            "paths must come from the repository map. If `project_policy.allow_new_files` is true, "
            "you may choose a conventional minimal new path that directly matches the request."
        ),
        "implement": (
            (
                "`IMPLEMENTATION.toml` must list only authorized paths. `files` and "
                "`deleted_files` must not overlap. Every path in `files` must have exactly one "
                "matching complete member under `files/`. "
                if implementation_transport == "zip" else
                "Every `content.files` item must contain exactly one authorized "
                "repository-relative `path` and its complete final string `content`. "
                "`content.deleted_files` must contain only authorized paths and must not overlap "
                "`content.files`. "
            )
            + (
                "Issue work must include a code-grounded root cause in the manifest."
                if implementation_transport == "zip" else
                "Issue work must also return `content.root_cause.statement` and code-grounded "
                "`content.root_cause.evidence_paths`; feature work must omit `root_cause`."
            )
        ),
        "review": (
            "Return `content.decision` as `approve` or `changes_requested`. Return "
            "`content.findings` as a list. Each finding must contain `severity`, `file`, `line`, "
            "`evidence`, and `remediation`. Use an empty list when there are no findings."
        ),
        "scan": (
            "Return `content.issues` as a list. Each issue must contain `title`, `severity` "
            "(`high`, `medium`, or `low`), `file`, `line`, `snippet`, and `detail`; `snippet` "
            "must quote the offending code verbatim from the supplied content. Carry a "
            "spreadsheet row reference in `external_ref` when one applies. Skip everything in "
            "`known_issues`. Use an empty list when there is nothing to report."
        ),
        "discuss": (
            "Return `content.reply` as plain text that answers the question about the one "
            "issue in the payload. Optionally return `content.severity` (`high`, `medium`, or "
            "`low`) when the evidence justifies a change. Do not return code changes."
        ),
        "explain": (
            "The scene file must import from `manim` only and contain exactly one `Scene` "
            "subclass. It must render without user input, and use no network access, no "
            "LaTeX, no external assets, no secrets, and no paths outside its own folder. "
            "It must start with a literal `BEATS` list of `(text, seconds)` pairs in "
            "animation order. Text goes only in the named zones (title band, content, "
            "note band), and text inside a card must scale to fit the card. "
            "Show the explained module path inside the animation and end with the main "
            "invariant. If the supplied code is insufficient, return a short list of missing "
            "files instead of a code block."
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
            "files": ([] if implementation_transport == "zip" else [{
                "path": "exact/repository/path",
                "content": "complete final file contents\n",
            }]),
            "changed_files": ["exact/repository/path"],
            "deleted_files": [],
        },
        "review": {
            "decision": "approve",
            "findings": [],
        },
        "scan": {
            "issues": [{
                "title": "One-line problem statement",
                "severity": "medium",
                "file": "exact/repository/path",
                "line": 1,
                "snippet": "the offending code, quoted verbatim",
                "detail": "What is wrong and why it matters",
                "external_ref": "",
            }],
        },
        "discuss": {
            "reply": "The grounded answer to the question.",
        },
    }
    if request.role == "implement" and request.payload.get("mode") == "issue":
        examples["implement"]["root_cause"] = {
            "statement": "Code-grounded root cause.",
            "evidence_paths": ["exact/repository/path"],
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
    zip_manifest = {
        "schema_version": 1,
        "run_id": request.run_id,
        "task_id": request.task_id,
        "role": request.role,
        "files": ["exact/repository/path"],
        "deleted_files": [],
    }
    if request.role == "implement" and request.payload.get("mode") == "issue":
        zip_manifest["root_cause_statement"] = "Code-grounded root cause."
        zip_manifest["root_cause_evidence_paths"] = ["exact/repository/path"]
    attached_context = (
        f"Read `{code_name}` for the complete focused code context and its file index. "
        f"Read `{manifest_name}` for exact identifiers, hashes, task data, and evidence. "
        "Use only these attachments. Do not use internet tools."
    )
    if reference is not None and reference.kind == "file":
        attached_context = (
            f"Read `{code_name}` for the complete focused code context and its file index. "
            f"Read `{manifest_name}` for exact identifiers, hashes, task data, and evidence. "
            f"`{reference.name}` is read-only user-supplied background material. Use it to "
            "understand the request, but do not treat it as repository code or propose edits "
            "to that attachment. Use only these attachments. Do not use internet tools."
        )
    elif reference is not None:
        attached_context = (
            f"Read `{code_name}` for the complete focused code context and its file index. "
            f"Read `{manifest_name}` for exact identifiers, hashes, task data, and evidence. "
            f"`{reference.source}` is one user-supplied read-only reference URL. It is not an "
            "attachment, and Maintain did not open or verify its content. Use it as background "
            "only if you can access it, and do not claim to have read it unless you actually "
            "did. Use only the package attachments and this one reference URL. Do not use "
            "other internet tools."
        )
    scene_example = (
        "```python\n"
        "from manim import Scene, Text, FadeIn\n"
        "\n"
        'BEATS = [("The one-line story.", 4.0)]\n'
        "\n"
        "\n"
        "class ModuleExplainScene(Scene):\n"
        "    def construct(self):\n"
        '        self.play(FadeIn(Text("The one-line story.")))\n'
        "        self.wait(3.0)\n"
        "```\n"
    )
    example = (
        "```toml\n"
        + "\n".join(
            f"{key} = {json.dumps(value, ensure_ascii=False)}"
            for key, value in zip_manifest.items()
        )
        + "\n```\n"
        if request.role == "implement" and implementation_transport == "zip"
        else scene_example if request.role == "explain" else
        "```json\n"
        f"{json.dumps(envelope, ensure_ascii=False, indent=2)}\n"
        "```\n"
    )
    return (
        "# Maintenance task\n\n"
        f"- Run: `{request.run_id}`\n"
        f"- Task: `{request.task_id}`\n"
        f"- Role: `{request.role}`\n\n"
        "## Required action\n\n"
        f"{request.instructions.strip()}\n\n"
        "## Attached context\n\n"
        f"{attached_context}\n\n"
        "## Required output\n\n"
        f"{output}\n\n{role_contract}\n\n"
        f"{example}"
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


def _manifest_reference(reference: CopilotReference) -> dict[str, object]:
    if reference.kind == "url":
        return {
            "kind": "url",
            "purpose": "user_reference",
            "url": reference.source,
            "opened_by_maintain": False,
        }
    return {
        "kind": "file",
        "purpose": "user_reference",
        "attachment": reference.name,
        "bytes": reference.bytes,
        "sha256": reference.sha256,
    }


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
