"""Scan, discuss, and talk packets: run-less exchanges for the project."""

from __future__ import annotations

import hashlib
import importlib.resources
import json
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence

from .audit import atomic_write
from .config import ProjectConfig
from .context import ContextFile, ContextSelector
from .engine import PROVIDER_SAFETY_HEADER
from .errors import ProviderError
from .issues import SEVERITIES, Issue, IssueCandidate
from .models import ProviderRequest
from .zip_package import PacketBuild, build_packet

SCAN_INSTRUCTIONS = (
    "Obey the project ground rules in GLOBAL.md. Scan the supplied code for "
    "defects and points to repair. Examine every supplied file, top to "
    "bottom, and report every distinct fault you find; do not stop after "
    "the first findings. payload.scan_coverage tells how many project "
    "files this scan holds; the other files come in later scans, so do "
    "not report that they are missing. Use the focus note and any files "
    "under attachments/ (for example an exported issue spreadsheet) as "
    "guides; cross-reference spreadsheet rows and carry the row reference "
    "in external_ref. Return content.issues as a list. Each issue needs "
    "title, severity (high, medium, or low), file, line, snippet, and "
    "detail. snippet must quote the offending code verbatim from the "
    "supplied file content; do not report code you were not given. Write "
    "title and detail in ASD-STE100 simplified technical English: short "
    "sentences, active voice, one idea per sentence. Write for a reader "
    "who does not know this codebase. In detail, give, in this order: "
    "what the code does now, why that is a fault, the effect the fault "
    "can cause, and the repair direction in one sentence. When you use a "
    "name from the code (a file, a function, a variable, a setting), say "
    "in a few words what it is. Keep each detail at or below 120 words. "
    "When issues share one cause or one area of functionality, give each "
    "of them the same short group label (one to three words) in group; "
    "give an unrelated issue no group. Report each distinct problem once "
    "and skip anything listed in known_issues. Do not use internet tools."
)

DISCUSS_INSTRUCTIONS = (
    "Obey the project ground rules in GLOBAL.md. Answer the question about "
    "the single issue in the payload. Ground the answer in the supplied code "
    "and any files under attachments/. Write short plain sentences for a "
    "reader who does not know this codebase; when you use a name from the "
    "code, say in a few words what it is. Return content.reply as plain "
    "text. If the evidence justifies a different severity, also return "
    "content.severity as high, medium, or low; otherwise omit it. Do not "
    "return code changes; a repair task does that. Do not use internet tools."
)

TALK_INSTRUCTIONS = (
    "Obey the project ground rules in GLOBAL.md. This package starts a "
    "working discussion about the whole project. payload.request names "
    "the subject when the person gave one. payload.open_issues lists the "
    "project's open issues with their groups. The supplied files are the "
    "project code, and payload.repository_map indexes every project "
    "file. Hold the discussion in the chat: answer, brainstorm, weigh "
    "options, and discuss whole groups of issues. Ground every claim in "
    "the supplied code and issues; when a needed file is not supplied, "
    "name it instead of guessing. Write short plain sentences for a "
    "reader who does not know this codebase; when you use a name from "
    "the code, say in a few words what it is. Do not produce the output "
    "file during the discussion. When the person ends the discussion or "
    "asks for the outcome, return one envelope whose content.outcome is "
    "issues, repair, feature, or none. For issues, add content.issues "
    "as in a scan: each issue needs title, severity (high, medium, or "
    "low), file, line, snippet quoted verbatim from the supplied code, "
    "detail in ASD-STE100 for a codebase newcomer, and an optional "
    "shared group label. For repair, add content.request as the exact "
    "fix to make in plain words, and content.issue_ids with ids from "
    "payload.open_issues when the fix targets tracked issues. For "
    "feature, add content.request as the exact change, addition, or "
    "removal to make. For none, add nothing. Do not return code "
    "changes; a repair run does that. Do not use internet tools."
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
    "three seconds or more, and move one thing at a time. Start the file "
    "with a literal BEATS list: one (text, seconds) pair for each step, in "
    "animation order. Place text only in three zones: the title band at the "
    "top, the content in the middle, and the note band at the bottom. Keep "
    "each line on screen at or below 42 characters. When text sits in a "
    "card, add a guard that scales the text to fit the card. Use "
    "attachments/PITFALLS.md and attachments/EXAMPLE-SCENE.md as guides. "
    "Use Manim Community 0.20.1. Use no external images, LaTeX, voice, "
    "plugins, or network resources. Show the explained module path in the "
    "animation and end with the main invariant. Ground every claim in "
    "CODEBASE.md; do not invent behavior. If the supplied code is "
    "insufficient, return a short list of missing files instead of a scene. "
    "Do not use internet tools."
)

def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


@dataclass(frozen=True)
class SideExchange:
    """One run-less packet exchange in flight (scan, discuss, or talk)."""

    kind: str                      # "scan", "discuss", or "talk"
    request: ProviderRequest
    directory: Path
    issue_id: str = ""             # discuss only
    disclosed: tuple[tuple[str, str], ...] = ()   # scan: (path, sha) sent
    restart: bool = False          # scan: this wave starts a new cycle


@dataclass(frozen=True)
class ScanWave:
    """One scan packet plus its place in the whole-project sweep."""

    request: ProviderRequest
    disclosed: tuple[tuple[str, str], ...]
    total: int                     # files in the project inventory
    remaining_after: int           # files still uncovered after this wave
    restart: bool                  # a finished cycle starts over


_WAVE_BYTES = 350_000


def _selector(config: ProjectConfig) -> ContextSelector:
    return ContextSelector(config.repository,
                           config.source_roots + config.test_roots,
                           config.exclude_paths, config.max_file_bytes)


def _ranked_inventory(selector: ContextSelector,
                      focus: str) -> list[ContextFile]:
    """The whole inventory; focus-matching files first when a focus exists."""
    inventory = selector.all_files()
    if not focus.strip():
        return inventory
    ranked = selector.select(focus, limit_files=len(inventory) + 9,
                             limit_bytes=1 << 40)
    ahead = {item.path for item in ranked}
    return ranked + [item for item in inventory if item.path not in ahead]


def _fill_wave(ordered: list[ContextFile],
               budget: int) -> list[ContextFile]:
    chosen: list[ContextFile] = []
    total = 0
    for item in ordered:
        if chosen and total + item.bytes > budget:
            continue
        chosen.append(item)
        total += item.bytes
    return chosen


def scan_coverage_path(config: ProjectConfig) -> Path:
    key = hashlib.sha256(
        str(Path(config.repository).resolve()).encode()).hexdigest()[:16]
    return Path(config.runtime_root).parent / "issues" / f"{key}.scan.json"


def load_scan_coverage(config: ProjectConfig) -> dict[str, str]:
    path = scan_coverage_path(config)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    covered = data.get("covered")
    return ({str(key): str(value) for key, value in covered.items()}
            if isinstance(covered, dict) else {})


def save_scan_coverage(config: ProjectConfig,
                       covered: dict[str, str]) -> None:
    rendered = json.dumps({"covered": covered}, ensure_ascii=False,
                          indent=2) + "\n"
    atomic_write(scan_coverage_path(config), rendered.encode())


def scan_request(config: ProjectConfig, focus: str, known: Sequence[Issue],
                 covered: dict[str, str] | None = None) -> ScanWave:
    """One wave of the whole-project sweep.

    Every inventory file gets disclosed across successive scans: each
    wave takes the uncovered files (focus-matching ones first) up to
    the packet budget. A changed file returns to the uncovered set on
    its own, and a finished cycle starts the sweep over."""
    selector = _selector(config)
    covered = dict(covered or {})
    ordered = _ranked_inventory(selector, focus)
    remaining = [item for item in ordered
                 if covered.get(item.path) != item.sha256]
    restart = bool(ordered) and not remaining
    if restart:
        remaining = ordered
    disclosed = _fill_wave(remaining, _WAVE_BYTES)
    request = ProviderRequest(
        schema_version=1,
        run_id=f"scan-{_stamp()}-{secrets.token_hex(2)}",
        task_id="scan",
        role="scan",
        instructions=f"{PROVIDER_SAFETY_HEADER}\n\n{SCAN_INSTRUCTIONS}",
        payload={
            "mode": "scan",
            "request": focus.strip(),
            "repository_map": selector.repository_map(),
            "scan_coverage": {"files_in_scan": len(disclosed),
                              "files_in_project": len(ordered)},
            "candidate_files": [
                {"path": x.path, "sha256": x.sha256, "bytes": x.bytes,
                 "content": x.content} for x in disclosed],
            "known_issues": [
                {"id": issue.id, "title": issue.title, "file": issue.file}
                for issue in known],
        })
    return ScanWave(
        request=request,
        disclosed=tuple((item.path, item.sha256) for item in disclosed),
        total=len(ordered),
        remaining_after=len(remaining) - len(disclosed),
        restart=restart)


def discuss_request(config: ProjectConfig, issue: Issue,
                    question: str) -> ProviderRequest:
    selector = _selector(config)
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


def talk_request(config: ProjectConfig, topic: str,
                 issues: Sequence[Issue]) -> ProviderRequest:
    """One handover packet for a discussion held in Copilot itself.

    Disclosure fills the packet budget from the whole inventory, the
    files that match the topic first, so the conversation carries as
    much of the codebase as one packet holds."""
    selector = _selector(config)
    disclosed = _fill_wave(_ranked_inventory(selector, topic), _WAVE_BYTES)
    return ProviderRequest(
        schema_version=1,
        run_id=f"talk-{_stamp()}-{secrets.token_hex(2)}",
        task_id="talk",
        role="talk",
        instructions=f"{PROVIDER_SAFETY_HEADER}\n\n{TALK_INSTRUCTIONS}",
        payload={
            "mode": "talk",
            "request": topic.strip(),
            "open_issues": [
                {"id": issue.id, "title": issue.title,
                 "severity": issue.severity, "status": issue.status,
                 "file": issue.file, "group": issue.group}
                for issue in issues if issue.status != "closed"],
            "repository_map": selector.repository_map(),
            "candidate_files": [
                {"path": x.path, "sha256": x.sha256, "bytes": x.bytes,
                 "content": x.content} for x in disclosed],
        })


TALK_OUTCOMES = ("issues", "repair", "feature", "none")


@dataclass(frozen=True)
class TalkOutcome:
    """What the external discussion ended with."""

    outcome: str                            # one of TALK_OUTCOMES
    request: str = ""                       # repair and feature
    issue_ids: tuple[str, ...] = ()         # repair, optional
    issues: tuple[IssueCandidate, ...] = ()   # issues outcome


def talk_outcome(content: dict, repository: Path) -> TalkOutcome:
    """Validate the discussion's closing envelope."""
    outcome = str(content.get("outcome", "")).strip().casefold()
    if outcome not in TALK_OUTCOMES:
        raise ProviderError(
            "The reply must contain content.outcome as issues, repair, "
            "feature, or none.")
    if outcome == "issues":
        candidates = scan_candidates(content, repository)
        if not candidates:
            raise ProviderError(
                "The issues outcome needs content.issues with at least "
                "one issue.")
        return TalkOutcome(outcome="issues", issues=tuple(candidates))
    if outcome in {"repair", "feature"}:
        request = str(content.get("request", "")).strip()
        if not request:
            raise ProviderError(
                "The request outcome needs content.request as text.")
        raw_ids = content.get("issue_ids", [])
        issue_ids = (tuple(str(x).strip() for x in raw_ids if str(x).strip())
                     if isinstance(raw_ids, list) and outcome == "repair"
                     else ())
        return TalkOutcome(outcome=outcome, request=request,
                           issue_ids=issue_ids)
    return TalkOutcome(outcome="none")


def explain_request(config: ProjectConfig, files: Sequence[str], goal: str,
                    audience: str, *, previous_scene: str = "",
                    render_error: str = "",
                    findings: Sequence[str] = ()) -> ProviderRequest:
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
    if findings:
        payload["lint_findings"] = list(findings)
        instructions = (
            f"{instructions} payload.lint_findings lists copy, pace, and "
            "layout faults from the local checks; correct each one."
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


def explain_attachments() -> list[Path]:
    """The pitfalls guide and the gold example scene, shipped with the app."""
    try:
        root = Path(str(importlib.resources.files("maintain") / "data"
                        / "explain"))
    except (ModuleNotFoundError, TypeError):
        return []
    if not root.is_dir():
        return []
    return sorted(root.glob("*.md"))


def build_side_packet(exchange: SideExchange, config: ProjectConfig,
                      attachments: Sequence[Path]) -> PacketBuild:
    extra = explain_attachments() if exchange.kind == "explain" else []
    return build_packet(
        exchange.request, exchange.directory,
        policy=config.package, repository=config.repository,
        config_dir=config.path.parent, attachments=[*attachments, *extra])


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
            group=str(entry.get("group", "")).strip()[:40],
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
        raise ProviderError("The reply must contain content.reply as text.")
    severity = str(content.get("severity", "")).strip().casefold()
    if severity and severity not in SEVERITIES:
        raise ProviderError("The discuss reply has an invalid severity.")
    return DiscussReply(reply=reply.strip(), severity=severity)
