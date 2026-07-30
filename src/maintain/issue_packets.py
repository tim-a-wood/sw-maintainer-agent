"""Scan and discuss packets: run-less exchanges for the issue list."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence

from .config import ProjectConfig
from .context import ContextSelector
from .engine import PROVIDER_SAFETY_HEADER
from .errors import ProviderError
from .issues import SEVERITIES, Issue, IssueCandidate
from .models import ProviderRequest
from .zip_package import PacketBuild, build_packet

SCAN_INSTRUCTIONS = (
    "Obey the project ground rules in GLOBAL.md. Scan the supplied code for "
    "defects and points to repair. Use the focus note and any files under "
    "attachments/ (for example an exported issue spreadsheet) as guides; "
    "cross-reference spreadsheet rows and carry the row reference in "
    "external_ref. Return content.issues as a list. Each issue needs title, "
    "severity (high, medium, or low), file, line, snippet, and detail. "
    "snippet must quote the offending code verbatim from the supplied file "
    "content; do not report code you were not given. Report each distinct "
    "problem once and skip anything listed in known_issues. Do not use "
    "internet tools."
)

DISCUSS_INSTRUCTIONS = (
    "Obey the project ground rules in GLOBAL.md. Answer the question about "
    "the single issue in the payload. Ground the answer in the supplied code "
    "and any files under attachments/. Return content.reply as plain text. "
    "If the evidence justifies a different severity, also return "
    "content.severity as high, medium, or low; otherwise omit it. Do not "
    "return code changes; a repair task does that. Do not use internet tools."
)

EXPLAIN_INSTRUCTIONS = (
    "Obey the project ground rules in GLOBAL.md. Explain the supplied code as "
    "one Manim animation of 30 to 45 seconds. Focus on the problem, the "
    "inputs, the transformations, the output, and the main invariant. Animate "
    "relationships and state changes; do not animate source code line by "
    "line. Write every sentence on screen in ASD-STE100 simplified technical "
    "English: short sentences, active voice, one idea per sentence, no "
    "metaphor. You can show output text from the code verbatim, marked as "
    "output. Give the viewer time to read: keep each text on screen for "
    "three seconds or more, and move one thing at a time. Use Manim "
    "Community 0.20.1. Use no external images, LaTeX, voice, plugins, or "
    "network resources. Show the explained module path in the animation and "
    "end with the main invariant. Ground every claim in CODEBASE.md; do not "
    "invent behavior. If the supplied code is insufficient, return a short "
    "list of missing files instead of a scene. Do not use internet tools."
)

_SCAN_FALLBACK_FOCUS = "defect fault error bug wrong incorrect missing unsafe"


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


@dataclass(frozen=True)
class SideExchange:
    """One run-less packet exchange in flight (scan or discuss)."""

    kind: str                      # "scan" or "discuss"
    request: ProviderRequest
    directory: Path
    issue_id: str = ""             # discuss only


def scan_request(config: ProjectConfig, focus: str,
                 known: Sequence[Issue]) -> ProviderRequest:
    selector = ContextSelector(config.repository,
                               config.source_roots + config.test_roots,
                               config.exclude_paths, config.max_file_bytes)
    disclosed = selector.select(focus.strip() or _SCAN_FALLBACK_FOCUS)
    return ProviderRequest(
        schema_version=1,
        run_id=f"scan-{_stamp()}-{secrets.token_hex(2)}",
        task_id="scan",
        role="scan",
        instructions=f"{PROVIDER_SAFETY_HEADER}\n\n{SCAN_INSTRUCTIONS}",
        payload={
            "mode": "scan",
            "request": focus.strip(),
            "repository_map": selector.repository_map(),
            "candidate_files": [
                {"path": x.path, "sha256": x.sha256, "bytes": x.bytes,
                 "content": x.content} for x in disclosed],
            "known_issues": [
                {"id": issue.id, "title": issue.title, "file": issue.file}
                for issue in known],
        })


def discuss_request(config: ProjectConfig, issue: Issue,
                    question: str) -> ProviderRequest:
    selector = ContextSelector(config.repository,
                               config.source_roots + config.test_roots,
                               config.exclude_paths, config.max_file_bytes)
    cited = selector.exact({issue.file}) if issue.file else []
    return ProviderRequest(
        schema_version=1,
        run_id=f"discuss-{_stamp()}-{secrets.token_hex(2)}",
        task_id=f"discuss-{issue.id}",
        role="discuss",
        instructions=f"{PROVIDER_SAFETY_HEADER}\n\n{DISCUSS_INSTRUCTIONS}",
        payload={
            "mode": "discuss",
            "request": question.strip(),
            "issue": issue.to_dict(),
            "repository_map": selector.repository_map(),
            "candidate_files": [
                {"path": x.path, "sha256": x.sha256, "bytes": x.bytes,
                 "content": x.content} for x in cited],
        })


def explain_request(config: ProjectConfig, files: Sequence[str], goal: str,
                    audience: str, *, previous_scene: str = "",
                    render_error: str = "") -> ProviderRequest:
    selector = ContextSelector(config.repository,
                               config.source_roots + config.test_roots,
                               config.exclude_paths, config.max_file_bytes)
    chosen = selector.exact(set(files))
    if not chosen:
        raise ProviderError("No selected file is inside the project sources.")
    payload = {
        "mode": "explain",
        "request": goal.strip(),
        "audience": audience.strip(),
        "repository_map": selector.repository_map(),
        "candidate_files": [
            {"path": x.path, "sha256": x.sha256, "bytes": x.bytes,
             "content": x.content} for x in chosen],
    }
    instructions = EXPLAIN_INSTRUCTIONS
    if previous_scene:
        payload["previous_scene"] = previous_scene
        payload["render_error"] = render_error[-4000:]
        instructions = (
            f"{EXPLAIN_INSTRUCTIONS} The previous scene failed to render; "
            "payload.render_error holds the error and payload.previous_scene "
            "holds the file. Return one corrected complete scene file."
        )
    return ProviderRequest(
        schema_version=1,
        run_id=f"explain-{_stamp()}-{secrets.token_hex(2)}",
        task_id="explain",
        role="explain",
        instructions=f"{PROVIDER_SAFETY_HEADER}\n\n{instructions}",
        payload=payload)


def explain_dir(config: ProjectConfig, run_id: str) -> Path:
    return Path(config.runtime_root).parent / "explain" / run_id


def build_side_packet(exchange: SideExchange, config: ProjectConfig,
                      attachments: Sequence[Path]) -> PacketBuild:
    return build_packet(
        exchange.request, exchange.directory,
        policy=config.package, repository=config.repository,
        config_dir=config.path.parent, attachments=attachments)


def side_packet_dir(config: ProjectConfig, run_id: str) -> Path:
    return Path(config.runtime_root).parent / "issues" / "packets" / run_id


def _verify_snippet(repository: Path, file: str, snippet: str) -> bool:
    if not file or not snippet.strip():
        return False
    candidate = Path(repository) / file
    try:
        content = candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    normalize = "".join
    return normalize(snippet.split()) in normalize(content.split())


def scan_candidates(content: dict, repository: Path) -> list[IssueCandidate]:
    """Validate a scan reply's content into candidates, snippet-verified."""
    issues = content.get("issues")
    if not isinstance(issues, list):
        raise ProviderError("The scan reply must contain content.issues as a list.")
    candidates: list[IssueCandidate] = []
    for entry in issues:
        if not isinstance(entry, dict):
            raise ProviderError("A scan issue is not structured.")
        title = str(entry.get("title", "")).strip()
        if not title:
            raise ProviderError("A scan issue has no title.")
        severity = str(entry.get("severity", "")).strip().casefold()
        if severity not in SEVERITIES:
            raise ProviderError(f"A scan issue has an invalid severity: {title[:40]}")
        file = str(entry.get("file", "")).strip()
        snippet = str(entry.get("snippet", "")).strip()
        line = entry.get("line", 0)
        candidates.append(IssueCandidate(
            title=title,
            detail=str(entry.get("detail", "")).strip(),
            severity=severity,
            file=file,
            line=int(line) if isinstance(line, int) and line > 0 else 0,
            snippet=snippet,
            external_ref=str(entry.get("external_ref", "")).strip(),
            kind="scan",
            verified=_verify_snippet(repository, file, snippet),
        ))
    return candidates


@dataclass(frozen=True)
class DiscussReply:
    reply: str
    severity: str = ""


def discuss_reply(content: dict) -> DiscussReply:
    reply = content.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        raise ProviderError("The discuss reply must contain content.reply as text.")
    severity = str(content.get("severity", "")).strip().casefold()
    if severity and severity not in SEVERITIES:
        raise ProviderError("The discuss reply has an invalid severity.")
    return DiscussReply(reply=reply.strip(), severity=severity)
