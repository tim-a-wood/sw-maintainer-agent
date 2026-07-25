"""Markdown and ZIP implementation parsers."""
from __future__ import annotations
import json
import shutil
import stat
import tempfile
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath
from .contracts import FileOperation, ImplementationArtifact
from .markdown import headings_outside_fences, normalized_text, require_sections
from .validation import has_transcript_contamination, validate_repository_path

OPS = {"Add": "add", "Modify": "modify", "Delete": "delete"}

def parse_markdown_implementation(path: Path, staging_dir: Path, *, max_bytes: int = 2_000_000,
                                  authorized: set[tuple[str, str]] | None = None,
                                  protected: tuple[str, ...] = (), excluded: tuple[str, ...] = ()) -> ImplementationArtifact:
    source = Path(path).resolve()
    text = normalized_text(source.read_bytes(), max_bytes)
    require_sections(text, "# Implementation Artifact", ("## Summary", "## File Operations"))
    if has_transcript_contamination(text):
        raise ValueError("REQ-MD-011: Probable transcript contamination.")
    headings = headings_outside_fences(text)
    summary_start = next(line for line, h in headings if h == "## Summary")
    ops_start = next(line for line, h in headings if h == "## File Operations")
    lines = text.splitlines(keepends=True)
    summary = "".join(lines[summary_start:ops_start-1]).strip()
    if not summary:
        raise ValueError("REQ-MD-005: Summary is empty.")
    operation_headings = [(line, heading) for line, heading in headings
                          if line > ops_start and heading.startswith("### ")]
    if not operation_headings:
        raise ValueError("No file operation is present.")
    staging = Path(staging_dir).resolve(); staging.mkdir(parents=True, exist_ok=True)
    result: list[FileOperation] = []; seen: set[str] = set()
    for index, (start, heading) in enumerate(operation_headings):
        try:
            label, raw_path = heading[4:].split(":", 1)
            operation = OPS[label.strip()]
        except (ValueError, KeyError) as exc:
            raise ValueError(f"Unsupported operation heading: {heading}") from exc
        relative = validate_repository_path(raw_path.strip(), protected=protected, excluded=excluded)
        if relative.casefold() in seen:
            raise ValueError(f"Duplicate operation path: {relative}")
        seen.add(relative.casefold())
        if authorized is not None and (operation, relative) not in authorized:
            raise ValueError(f"Unauthorized operation: {operation} {relative}")
        end = operation_headings[index + 1][0] - 1 if index + 1 < len(operation_headings) else len(lines)
        body = "".join(lines[start:end])
        fences = list(__import__('re').finditer(r"(?ms)^(`{3,}|~{3,})[^\n]*\n(.*?)^\1\s*$", body))
        if operation == "delete":
            if fences: raise ValueError(f"Delete operation contains content: {relative}")
            staged = None
        else:
            if len(fences) != 1 or not fences[0].group(2):
                raise ValueError(f"{operation} needs exactly one nonempty complete content block: {relative}")
            content = fences[0].group(2)
            if content.endswith("\n"): content = content[:-1]
            staged = staging / f"{len(result):04d}.content"
            staged.write_text(content, encoding="utf-8", newline="\n")
        result.append(FileOperation(operation, relative, staged))
    return ImplementationArtifact(source, "markdown", tuple(result), summary)

def parse_zip_implementation(path: Path, staging_dir: Path, *, max_file_bytes: int = 1_000_000,
                             max_total_bytes: int = 20_000_000,
                             authorized: set[tuple[str, str]] | None = None) -> ImplementationArtifact:
    source = Path(path).resolve(); staging = Path(staging_dir).resolve(); staging.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as archive:
        infos = archive.infolist(); names = [i.filename for i in infos if not i.is_dir()]
        if len(names) != len(set(names)): raise ValueError("ZIP contains duplicate members.")
        for info in infos:
            p = PurePosixPath(info.filename)
            if p.is_absolute() or "\\" in info.filename or ".." in p.parts:
                raise ValueError("ZIP contains an unsafe member.")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode): raise ValueError("ZIP contains a symbolic link.")
        if "IMPLEMENTATION.json" not in names: raise ValueError("IMPLEMENTATION.json is missing.")
        declaration = json.loads(archive.read("IMPLEMENTATION.json"))
        if declaration.get("schema_version") != 1 or not isinstance(declaration.get("operations"), list):
            raise ValueError("Invalid implementation declaration.")
        declared = {"IMPLEMENTATION.json", "NOTES.md"}; operations=[]; seen=set(); total=0
        for item in declaration["operations"]:
            op, rel = item.get("operation"), validate_repository_path(str(item.get("path", "")))
            if op not in {"add", "modify", "delete"} or rel.casefold() in seen: raise ValueError("Invalid or duplicate operation.")
            seen.add(rel.casefold())
            if authorized is not None and (op, rel) not in authorized: raise ValueError(f"Unauthorized operation: {op} {rel}")
            member = item.get("content_member")
            if op == "delete":
                if member: raise ValueError("Delete must not declare content.")
                staged=None
            else:
                expected=f"files/{rel}"
                if member != expected or member not in names: raise ValueError(f"Missing content member: {expected}")
                declared.add(member); info=archive.getinfo(member); total += info.file_size
                if info.file_size > max_file_bytes or total > max_total_bytes: raise ValueError("ZIP extraction limit exceeded.")
                staged=staging/f"{len(operations):04d}.content"; staged.parent.mkdir(parents=True, exist_ok=True)
                staged.write_bytes(archive.read(member))
            operations.append(FileOperation(op, rel, staged))
        if set(names) - declared: raise ValueError(f"Undeclared ZIP member: {sorted(set(names)-declared)[0]}")
    return ImplementationArtifact(source, "zip", tuple(operations), str(declaration.get("implementation_summary", "")).strip())
