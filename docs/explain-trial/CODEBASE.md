# CODEBASE

## Index

1. `src/maintain/issues.py` — the fingerprint and the capture path.
2. `tests/test_issues.py` — the tests that ground the behavior.

## 1. `src/maintain/issues.py` (excerpts)

### Constants and dismissal rule

```python

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


```

### The fingerprint

```python
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
```

### The capture path

```python
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
```

### Close on delivery

```python
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
```

## 2. `tests/test_issues.py` (grounding tests)

```python
def test_capture_adds_then_updates_never_duplicates(tmp_path):
    store = _store(tmp_path)
    first = store.capture([_candidate()], source="review", run_id="f-1")
    assert len(first.added) == 1 and not first.updated
    again = store.capture([_candidate(line=3)], source="review", run_id="f-2")
    assert not again.added and len(again.updated) == 1
    issues = store.load()
    assert len(issues) == 1
    assert issues[0].runs == ("f-1", "f-2")
    assert issues[0].line == 3
```

```python
def test_capture_respects_dismissals_and_reopens_fixed(tmp_path):
    store = _store(tmp_path)
    store.capture([_candidate()], source="review", run_id="f-1")
    issue = store.load()[0]

    store.close(issue.id, REASON_NOT_A_FAULT)
    dismissed = store.capture([_candidate()], source="review", run_id="f-2")
    assert dismissed.skipped and not dismissed.added and not dismissed.reopened
    assert store.get(issue.id).status == CLOSED

    store.reopen(issue.id)
    store.close(issue.id, REASON_FIXED)
    back = store.capture([_candidate()], source="review", run_id="f-3")
    assert back.reopened == (issue.id,)
    assert store.get(issue.id).status == OPEN
```

```python
def test_close_for_run_keeps_cited_fingerprints(tmp_path):
    store = _store(tmp_path)
    store.capture([_candidate(),
                   _candidate(snippet="other = 1", title="Second point")],
                  source="review", run_id="f-1")
    keep = {_candidate().fingerprint}
    closed = store.close_for_run("f-1", keep_fingerprints=keep)
    assert len(closed) == 1
    statuses = {issue.title: issue.status for issue in store.load()}
    assert statuses["The bound is wrong"] == OPEN
    assert statuses["Second point"] == CLOSED


# ----- engine hooks -----

PATCH = (
    "diff --git a/app.py b/app.py\n"
    "--- a/app.py\n"
    "+++ b/app.py\n"
    "@@ -1 +1 @@\n"
    '-VALUE = "before"\n'
    '+VALUE = "after"\n'
)


class ReviewOnceProvider(Provider):
    """Reject the first review with one finding, then approve."""

    def __init__(self, approve_findings: list | None = None) -> None:
        self.review_calls = 0
        self.approve_findings = approve_findings or []

    def preflight(self) -> None:
        return None

    def exchange(self, request: ProviderRequest) -> ProviderResponse:
        if request.role == "scope":
            content = {"tasks": [{
                "id": "change-value", "objective": "Change the value",
                "allowed_files": ["app.py"],
                "done_when": ["VALUE is set to after."],
                "verification": ["Read app.py."], "depends_on": [],
            }]}
        elif request.role == "implement":
            content = {"patch": PATCH}
        elif request.role == "review":
            self.review_calls += 1
            if self.review_calls == 1 and not self.approve_findings:
                content = {"decision": "changes_requested", "findings": [{
                    "severity": "medium", "file": "app.py", "line": 1,
                    "evidence": "The value has no unit comment.",
                    "remediation": "State the unit in a comment.",
                }]}
            else:
                content = {"decision": "approve",
                           "findings": list(self.approve_findings)}
        else:  # pragma: no cover
            raise AssertionError(request.role)
        return ProviderResponse(
            schema_version=request.schema_version, run_id=request.run_id,
            task_id=request.task_id, role=request.role, content=content,
            provider="scripted",
            conversation_id=f"{request.role}-{self.review_calls}")
```

## Notes

- Sources of findings: review, test, scan. Human entries do not pass
  through capture.
- A dismissal is a close with reason wont_fix, duplicate, or
  not_a_fault. A close with reason fixed or gone is not a dismissal.
