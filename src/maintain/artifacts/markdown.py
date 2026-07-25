"""Strict Markdown contract parsing."""
from __future__ import annotations
import re
from dataclasses import dataclass

@dataclass(frozen=True)
class Section:
    heading: str
    body: str
    line: int

def normalized_text(data: bytes, max_bytes: int) -> str:
    if len(data) > max_bytes:
        raise ValueError("REQ-MD-012: Artifact exceeds the configured size limit.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("REQ-MD-001: Artifact is not UTF-8.") from exc
    return text.replace("\r\n", "\n").replace("\r", "\n")

def headings_outside_fences(text: str) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    fence: str | None = None
    in_html = False
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("<!--"):
            in_html = True
        if in_html:
            if "-->" in stripped:
                in_html = False
            continue
        match = re.match(r"^(`{3,}|~{3,})", stripped)
        if match:
            token = match.group(1)
            if fence is None:
                fence = token[0]
            elif token[0] == fence:
                fence = None
            continue
        if fence is None and not line.lstrip().startswith(">") and re.match(r"^#{1,6}\s+\S", line):
            result.append((number, line.strip()))
    return result

def require_sections(text: str, top_heading: str, required: tuple[str, ...]) -> None:
    headings = headings_outside_fences(text)
    top = [h for _, h in headings if h.startswith("# ") and not h.startswith("## ")]
    if top != [top_heading]:
        raise ValueError(f"REQ-MD-010: Expected exactly one {top_heading!r} heading.")
    for section in required:
        occurrences = [line for line, heading in headings if heading == section]
        if len(occurrences) != 1:
            raise ValueError(f"REQ-MD-004: Required section {section!r} must occur exactly once.")
