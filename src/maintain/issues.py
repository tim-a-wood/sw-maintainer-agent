"""Per-project issue list: the tool's memory of known faults.

One person, one machine, one writer. The store is a single JSON file
beside the runs, read and written whole. Identity comes from a content
fingerprint (kind + file + normalized snippet), so a repeated finding
updates its issue instead of duplicating it, and a dismissal persists
against future scans.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from .audit import atomic_write
from .errors import ConfigurationError

SCHEMA_VERSION = 1

OPEN = "open"
IN_WORK = "in_work"
CLOSED = "closed"
STATUSES = (OPEN, IN_WORK, CLOSED)

REASON_FIXED = "fixed"
REASON_WONT_FIX = "wont_fix"
REASON_DUPLICATE = "duplicate"
REASON_NOT_A_FAULT = "not_a_fault"
REASON_GONE = "gone"
REASONS = (REASON_FIXED, REASON_WONT_FIX, REASON_DUPLICATE,
           REASON_NOT_A_FAULT, REASON_GONE)

# Closing for one of these reasons is a dismissal: the same finding is
# dropped when it comes back. Fixed and gone reopen instead.
DISMISSAL_REASONS = (REASON_WONT_FIX, REASON_DUPLICATE, REASON_NOT_A_FAULT)

SOURCES = ("human", "review", "test", "scan", "import")
SEVERITIES = ("high", "medium", "low")

FINGERPRINT_WINDOW = 100


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def display_order(issues: list) -> list:
    """Severity first (high to low), then the newest change first."""
    rank = {"high": 0, "medium": 1, "low": 2}
    by_recency = sorted(issues, key=lambda issue: issue.updated_at,
                        reverse=True)
    return sorted(by_recency,
                  key=lambda issue: rank.get(issue.severity, 3))


def fingerprint(kind: str, file: str, snippet: str, title: str = "") -> str:
    """A stable identity: kind + file + the significant snippet characters.

    Line numbers are display data; identity lives in content. An issue
    without a snippet falls back to its normalized title.
    """
    basis = "".join(snippet.split())[:FINGERPRINT_WINDOW]
    if not basis:
        basis = "".join(title.split()).casefold()[:FINGERPRINT_WINDOW]
    digest = hashlib.sha256(f"{kind}|{file}|{basis}".encode()).hexdigest()
    return digest[:12]


@dataclass(frozen=True)
class IssueCandidate:
    """One machine-reported finding on its way into the store."""

    title: str
    detail: str = ""
    severity: str = "medium"
    file: str = ""
    line: int = 0
    snippet: str = ""
    external_ref: str = ""
    kind: str = "scan"
    verified: bool = True

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.kind, self.file, self.snippet, self.title)


@dataclass(frozen=True)
class Issue:
    id: str
    title: str
    detail: str = ""
    severity: str = "medium"
    status: str = OPEN
    closed_reason: str = ""
    source: str = "human"
    file: str = ""
    line: int = 0
    snippet: str = ""
    fingerprint: str = ""
    external_ref: str = ""
    runs: tuple[str, ...] = ()
    notes: tuple[dict, ...] = ()
    events: tuple[dict, ...] = ()
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        value = {
            "id": self.id, "title": self.title, "detail": self.detail,
            "severity": self.severity, "status": self.status,
            "closed_reason": self.closed_reason, "source": self.source,
            "file": self.file, "line": self.line, "snippet": self.snippet,
            "fingerprint": self.fingerprint, "external_ref": self.external_ref,
            "runs": list(self.runs), "notes": [dict(x) for x in self.notes],
            "events": [dict(x) for x in self.events],
            "created_at": self.created_at, "updated_at": self.updated_at,
        }
        return value

    @staticmethod
    def from_dict(value: dict) -> "Issue":
        return Issue(
            id=str(value.get("id", "")),
            title=str(value.get("title", "")),
            detail=str(value.get("detail", "")),
            severity=str(value.get("severity", "medium")),
            status=str(value.get("status", OPEN)),
            closed_reason=str(value.get("closed_reason", "")),
            source=str(value.get("source", "human")),
            file=str(value.get("file", "")),
            line=int(value.get("line", 0) or 0),
            snippet=str(value.get("snippet", "")),
            fingerprint=str(value.get("fingerprint", "")),
            external_ref=str(value.get("external_ref", "")),
            runs=tuple(str(x) for x in value.get("runs", [])),
            notes=tuple(dict(x) for x in value.get("notes", [])
                        if isinstance(x, dict)),
            events=tuple(dict(x) for x in value.get("events", [])
                         if isinstance(x, dict)),
            created_at=str(value.get("created_at", "")),
            updated_at=str(value.get("updated_at", "")),
        )


@dataclass(frozen=True)
class CaptureResult:
    added: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    reopened: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()   # fingerprints dropped by a dismissal

    @property
    def touched(self) -> tuple[str, ...]:
        return (*self.added, *self.updated, *self.reopened)


@dataclass
class IssueStore:
    """The one issue file for one repository."""

    runtime_root: Path
    repository: Path
    _issues: list[Issue] = field(default_factory=list)
    _loaded: bool = False

    @property
    def path(self) -> Path:
        key = hashlib.sha256(
            str(Path(self.repository).resolve()).encode()).hexdigest()[:16]
        return Path(self.runtime_root).parent / "issues" / f"{key}.json"

    # ----- persistence -----

    def load(self) -> list[Issue]:
        if not self._loaded:
            self._issues = []
            if self.path.is_file():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                for raw in data.get("issues", []):
                    if isinstance(raw, dict):
                        self._issues.append(Issue.from_dict(raw))
            self._loaded = True
        return list(self._issues)

    def _save(self) -> None:
        data = {
            "schema_version": SCHEMA_VERSION,
            "repository": str(Path(self.repository).resolve()),
            "issues": [issue.to_dict() for issue in self._issues],
        }
        rendered = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        atomic_write(self.path, rendered.encode())

    # ----- reads -----

    def get(self, issue_id: str) -> Issue:
        for issue in self.load():
            if issue.id == issue_id:
                return issue
        raise ConfigurationError(f"Unknown issue: {issue_id}")

    def open_count(self) -> int:
        return sum(1 for issue in self.load() if issue.status != CLOSED)

    def find_fingerprint(self, value: str) -> Issue | None:
        for issue in self.load():
            if issue.fingerprint == value:
                return issue
        return None

    # ----- writes -----

    def _put(self, updated: Issue) -> Issue:
        self.load()
        for index, issue in enumerate(self._issues):
            if issue.id == updated.id:
                self._issues[index] = updated
                break
        else:
            self._issues.insert(0, updated)
        self._save()
        return updated

    def _event(self, issue: Issue, actor: str, what: str,
               old: str = "", new: str = "") -> Issue:
        event = {"time": _now(), "actor": actor, "what": what}
        if old or new:
            event.update({"old": old, "new": new})
        return replace(issue, events=(*issue.events, event), updated_at=_now())

    def _new_id(self) -> str:
        taken = {issue.id for issue in self.load()}
        while True:
            value = secrets.token_hex(3)
            if value not in taken:
                return value

    def add(self, *, title: str, detail: str = "", severity: str = "medium",
            source: str = "human", file: str = "", line: int = 0,
            snippet: str = "", external_ref: str = "", kind: str = "",
            run_id: str = "", actor: str = "you") -> Issue:
        title = title.strip()
        if not title:
            raise ConfigurationError("An issue needs a title.")
        if severity not in SEVERITIES:
            raise ConfigurationError(f"Unknown severity: {severity}")
        if source not in SOURCES:
            raise ConfigurationError(f"Unknown issue source: {source}")
        now = _now()
        issue = Issue(
            id=self._new_id(), title=title[:200], detail=detail,
            severity=severity, source=source, file=file, line=int(line or 0),
            snippet=snippet,
            fingerprint=fingerprint(kind or source, file, snippet, title),
            external_ref=external_ref,
            runs=(run_id,) if run_id else (),
            created_at=now, updated_at=now,
        )
        issue = self._event(issue, actor, "created")
        return self._put(issue)

    def update(self, issue_id: str, *, title: str | None = None,
               detail: str | None = None, severity: str | None = None,
               external_ref: str | None = None,
               actor: str = "you") -> Issue:
        issue = self.get(issue_id)
        if external_ref is not None and external_ref != issue.external_ref:
            issue = self._event(issue, actor, "reference",
                                issue.external_ref, external_ref)
            issue = replace(issue, external_ref=external_ref.strip())
        if title is not None and title.strip() and title != issue.title:
            issue = self._event(issue, actor, "title", issue.title,
                                title.strip()[:200])
            issue = replace(issue, title=title.strip()[:200])
        if detail is not None and detail != issue.detail:
            issue = replace(self._event(issue, actor, "detail"), detail=detail)
        if severity is not None and severity != issue.severity:
            if severity not in SEVERITIES:
                raise ConfigurationError(f"Unknown severity: {severity}")
            issue = self._event(issue, actor, "severity", issue.severity,
                                severity)
            issue = replace(issue, severity=severity)
        return self._put(issue)

    def close(self, issue_id: str, reason: str, actor: str = "you") -> Issue:
        if reason not in REASONS:
            raise ConfigurationError(f"Unknown close reason: {reason}")
        issue = self.get(issue_id)
        issue = self._event(issue, actor, "closed", issue.status, reason)
        return self._put(replace(issue, status=CLOSED, closed_reason=reason))

    def reopen(self, issue_id: str, actor: str = "you") -> Issue:
        issue = self.get(issue_id)
        issue = self._event(issue, actor, "reopened", issue.closed_reason, "")
        return self._put(replace(issue, status=OPEN, closed_reason=""))

    def set_in_work(self, issue_id: str, actor: str = "you") -> Issue:
        issue = self.get(issue_id)
        if issue.status == IN_WORK:
            return issue
        issue = self._event(issue, actor, "in_work", issue.status, IN_WORK)
        return self._put(replace(issue, status=IN_WORK))

    def delete(self, issue_id: str) -> None:
        self.load()
        before = len(self._issues)
        self._issues = [issue for issue in self._issues if issue.id != issue_id]
        if len(self._issues) == before:
            raise ConfigurationError(f"Unknown issue: {issue_id}")
        self._save()

    def add_note(self, issue_id: str, author: str, text: str) -> Issue:
        issue = self.get(issue_id)
        note = {"time": _now(), "author": author, "text": text.strip()}
        issue = self._event(issue, author, "note")
        return self._put(replace(issue, notes=(*issue.notes, note)))

    def link_run(self, issue_id: str, run_id: str, actor: str = "tool") -> Issue:
        issue = self.get(issue_id)
        if run_id in issue.runs:
            return issue
        issue = self._event(issue, actor, "run", "", run_id)
        return self._put(replace(issue, runs=(*issue.runs, run_id)))

    # ----- machine capture -----

    def capture(self, candidates: list[IssueCandidate], *, source: str,
                run_id: str = "") -> CaptureResult:
        """Fold machine findings into the store, deduplicated by fingerprint."""
        added: list[str] = []
        updated: list[str] = []
        reopened: list[str] = []
        skipped: list[str] = []
        seen_batch: set[str] = set()
        for candidate in candidates:
            value = candidate.fingerprint
            if value in seen_batch:
                continue
            seen_batch.add(value)
            existing = self.find_fingerprint(value)
            if existing is None:
                issue = self.add(
                    title=candidate.title, detail=candidate.detail,
                    severity=candidate.severity, source=source,
                    file=candidate.file, line=candidate.line,
                    snippet=candidate.snippet, kind=candidate.kind,
                    external_ref=candidate.external_ref,
                    run_id=run_id, actor="tool")
                added.append(issue.id)
                continue
            if (existing.status == CLOSED
                    and existing.closed_reason in DISMISSAL_REASONS):
                skipped.append(value)
                continue
            issue = existing
            if issue.status == CLOSED:
                issue = self._event(issue, "tool", "reopened",
                                    issue.closed_reason, "")
                issue = replace(issue, status=OPEN, closed_reason="")
                reopened.append(issue.id)
            else:
                issue = self._event(issue, "tool", "seen", "", run_id)
                updated.append(issue.id)
            if run_id and run_id not in issue.runs:
                issue = replace(issue, runs=(*issue.runs, run_id))
            issue = replace(issue, line=candidate.line or issue.line)
            self._put(issue)
        return CaptureResult(added=tuple(added), updated=tuple(updated),
                             reopened=tuple(reopened), skipped=tuple(skipped))

    def known_fingerprints(self) -> set[str]:
        return {issue.fingerprint for issue in self.load() if issue.fingerprint}

    def close_for_run(self, run_id: str,
                      keep_fingerprints: set[str] = frozenset()) -> list[Issue]:
        """Close a delivered run's linked issues as fixed.

        Issues the final review still cites stay open."""
        closed: list[Issue] = []
        for issue in self.load():
            if (run_id in issue.runs and issue.status != CLOSED
                    and issue.fingerprint not in keep_fingerprints):
                closed.append(self.close(issue.id, REASON_FIXED, actor="tool"))
        return closed
