"""Validation of the assistant reply ZIP (maintain-output.zip)."""

from __future__ import annotations

import json
import tomllib
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from maintain.errors import ProviderError
from maintain.models import ProviderRequest


def inline_implementation_zip(content: dict[str, Any],
                              request: ProviderRequest,
                              directory: Path) -> Path:
    """Build maintain-output.zip from a Markdown reply's JSON content.

    Copilot often cannot attach a real ZIP; the Markdown reply carries
    the same implementation as `content.files` and
    `content.deleted_files`. This synthesizes the ZIP the rest of the
    pipeline already validates and applies, so both reply shapes walk
    one code path. JSON string escapes are valid TOML basic strings,
    so json.dumps renders every manifest value.
    """
    files = content.get("files")
    if (not isinstance(files, list) or any(
            not isinstance(item, dict)
            or not isinstance(item.get("path"), str)
            or not isinstance(item.get("content"), str)
            for item in files)):
        raise ProviderError(
            "The Markdown reply needs content.files with a path and the "
            "complete content for every file.")
    deleted = content.get("deleted_files", [])
    if not isinstance(deleted, list) or any(
            not isinstance(item, str) for item in deleted):
        raise ProviderError(
            "The Markdown reply deleted_files must be a path list.")
    manifest = [
        "schema_version = 1",
        f"run_id = {json.dumps(request.run_id)}",
        f"task_id = {json.dumps(request.task_id)}",
        f"role = {json.dumps(request.role)}",
        "files = [" + ", ".join(
            json.dumps(str(item["path"])) for item in files) + "]",
        "deleted_files = [" + ", ".join(
            json.dumps(item) for item in deleted) + "]",
    ]
    root_cause = content.get("root_cause")
    if isinstance(root_cause, dict):
        statement = root_cause.get("statement")
        evidence = root_cause.get("evidence_paths")
        if isinstance(statement, str) and isinstance(evidence, list):
            manifest.append(
                f"root_cause_statement = {json.dumps(statement)}")
            manifest.append(
                "root_cause_evidence_paths = [" + ", ".join(
                    json.dumps(str(item)) for item in evidence) + "]")
    target = Path(directory) / "maintain-output.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("IMPLEMENTATION.toml", "\n".join(manifest) + "\n")
        for item in files:
            bundle.writestr(f"files/{item['path']}", item["content"])
    return target


def zip_artifact_content(archive: Path, request: ProviderRequest) -> dict[str, Any]:
    """Validate ZIP metadata and synthesize Maintain's internal response content."""
    with zipfile.ZipFile(archive) as bundle:
        infos = [item for item in bundle.infolist() if not item.is_dir()]
        names = [item.filename for item in infos]
        if len(names) != len(set(names)):
            raise ProviderError(
                "The implementation ZIP contains duplicate members.")
        if "IMPLEMENTATION.toml" not in names:
            raise ProviderError(
                "The implementation ZIP is missing IMPLEMENTATION.toml.")
        manifest_info = bundle.getinfo("IMPLEMENTATION.toml")
        if manifest_info.flag_bits & 0x1:
            raise ProviderError(
                "The implementation ZIP manifest is encrypted.")
        if manifest_info.file_size > 65_536:
            raise ProviderError(
                "The implementation ZIP manifest exceeds 64 KiB.")
        try:
            manifest = tomllib.loads(
                bundle.read(manifest_info).decode("utf-8"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise ProviderError(
                "The implementation ZIP manifest is invalid TOML.") from exc

    expected_keys = {
        "schema_version", "run_id", "task_id", "role", "files",
        "deleted_files", "root_cause_statement",
        "root_cause_evidence_paths",
    }
    unknown = set(manifest) - expected_keys
    if unknown:
        raise ProviderError(
            f"The implementation ZIP manifest has an unknown field: "
            f"{sorted(unknown)[0]}")
    if manifest.get("schema_version") != 1:
        raise ProviderError(
            "The implementation ZIP manifest has an invalid schema_version.")
    for field, expected in (
            ("run_id", request.run_id),
            ("task_id", request.task_id),
            ("role", request.role)):
        if manifest.get(field) != expected:
            raise ProviderError(
                f"The implementation ZIP manifest has the wrong {field}.")

    def paths(field: str) -> list[str]:
        values = manifest.get(field)
        if (not isinstance(values, list)
                or any(not isinstance(value, str) for value in values)
                or len(values) != len(set(values))):
            raise ProviderError(
                f"The implementation ZIP manifest {field} must be a unique path list.")
        return values

    files = paths("files")
    deleted = paths("deleted_files")
    if not files and not deleted:
        raise ProviderError(
            "The implementation ZIP manifest declares no changes.")
    overlap = set(files) & set(deleted)
    if overlap:
        raise ProviderError(
            f"The implementation ZIP cannot replace and delete the same path: "
            f"{sorted(overlap)[0]}")
    allowed = {
        str(path)
        for path in request.payload.get("task", {}).get("allowed_files", [])
    }
    for path in [*files, *deleted]:
        relative = PurePosixPath(path)
        if (relative.is_absolute() or "\\" in path or not relative.parts
                or any(part in {"", ".", ".."} for part in relative.parts)):
            raise ProviderError(
                f"The implementation ZIP manifest contains an unsafe path: {path}")
        if path not in allowed:
            raise ProviderError(
                f"The implementation ZIP manifest contains an unapproved path: {path}")
    expected_members = {
        "IMPLEMENTATION.toml",
        *(f"files/{path}" for path in files),
    }
    if set(names) != expected_members:
        missing = expected_members - set(names)
        extra = set(names) - expected_members
        defect = (
            f"missing {sorted(missing)[0]}" if missing
            else f"undeclared member {sorted(extra)[0]}")
        raise ProviderError(
            f"The implementation ZIP layout is invalid: {defect}.")

    content: dict[str, Any] = {
        "files": [],
        "changed_files": [*files, *deleted],
        "deleted_files": deleted,
    }
    if request.payload.get("mode") == "issue":
        statement = manifest.get("root_cause_statement")
        evidence = manifest.get("root_cause_evidence_paths")
        if (not isinstance(statement, str) or not statement.strip()
                or not isinstance(evidence, list) or not evidence
                or any(not isinstance(path, str) for path in evidence)
                or any(path not in allowed for path in evidence)):
            raise ProviderError(
                "The implementation ZIP manifest has an invalid issue root cause.")
        content["root_cause"] = {
            "statement": statement.strip(),
            "evidence_paths": evidence,
        }
    return content
