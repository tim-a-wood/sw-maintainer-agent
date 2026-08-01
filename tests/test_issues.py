"""M4: the issue store, machine capture, and the scan/discuss packet layer."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from maintain.config import ProjectConfig, default_config
from maintain.engine import WorkflowEngine
from maintain.errors import ConfigurationError, ProviderError
from maintain.issue_packets import (build_side_packet, discuss_reply,
                                    discuss_request, scan_candidates,
                                    scan_request, side_packet_dir, SideExchange)
from maintain.issues import (CLOSED, IN_WORK, OPEN, REASON_FIXED,
                             REASON_NOT_A_FAULT, REASON_WONT_FIX, Issue,
                             IssueCandidate, IssueStore, fingerprint,
                             related_open_issues)
from maintain.models import ProviderRequest, ProviderResponse, RunState
from maintain.presenter import QuietPresenter
from maintain.providers.base import Provider


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(["git", "-C", str(repository), *args],
                               check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "project"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Maintain Test")
    _git(repository, "config", "user.email", "maintain@example.invalid")
    (repository / "app.py").write_text('VALUE = "before"\n', encoding="utf-8")
    _git(repository, "add", "app.py")
    _git(repository, "commit", "-m", "initial")
    return repository


def _config(tmp_path: Path, repository: Path) -> ProjectConfig:
    data = default_config(repository, "codex")
    data["audit"] = {"runtime_root": str(tmp_path / "runtime")}
    path = repository / ".maintain.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return ProjectConfig.load(path)


def _store(tmp_path: Path) -> IssueStore:
    return IssueStore(runtime_root=tmp_path / "runtime" / "runs",
                      repository=tmp_path / "project")


# ----- store basics -----

def test_add_get_update_close_reopen_delete_roundtrip(tmp_path):
    store = _store(tmp_path)
    issue = store.add(title="The loader accepts negative speeds",
                      severity="high", detail="Speeds below zero pass.")
    assert issue.status == OPEN and issue.source == "human"
    assert len(issue.id) == 6

    fresh = IssueStore(runtime_root=store.runtime_root,
                       repository=store.repository)
    loaded = fresh.get(issue.id)
    assert loaded.title == issue.title and loaded.severity == "high"

    fresh.update(issue.id, title="The loader accepts wrong speeds",
                 severity="medium")
    fresh.close(issue.id, REASON_WONT_FIX)
    closed = fresh.get(issue.id)
    assert closed.status == CLOSED and closed.closed_reason == REASON_WONT_FIX
    assert [event["what"] for event in closed.events] == [
        "created", "title", "severity", "closed"]

    fresh.reopen(issue.id)
    assert fresh.get(issue.id).status == OPEN
    fresh.set_in_work(issue.id)
    assert fresh.get(issue.id).status == IN_WORK

    fresh.delete(issue.id)
    with pytest.raises(ConfigurationError):
        fresh.get(issue.id)
    assert fresh.load() == []


def test_add_rejects_empty_title_and_bad_values(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ConfigurationError):
        store.add(title="   ")
    with pytest.raises(ConfigurationError):
        store.add(title="x", severity="urgent")
    with pytest.raises(ConfigurationError):
        store.close("nope", REASON_FIXED)
    store.add(title="x")
    with pytest.raises(ConfigurationError):
        store.close(store.load()[0].id, "because")


def test_notes_and_run_links_are_attributed(tmp_path):
    store = _store(tmp_path)
    issue = store.add(title="A point to discuss")
    store.add_note(issue.id, "you", "Is this a real fault?")
    store.add_note(issue.id, "copilot", "Yes. The bound is wrong.")
    store.link_run(issue.id, "f-1")
    store.link_run(issue.id, "f-1")
    loaded = store.get(issue.id)
    assert [note["author"] for note in loaded.notes] == ["you", "copilot"]
    assert loaded.runs == ("f-1",)


def test_fingerprint_ignores_whitespace_and_falls_back_to_title():
    a = fingerprint("review", "app.py", "return [r for r in records]")
    b = fingerprint("review", "app.py", "return [r  for r\n  in records]")
    c = fingerprint("review", "app.py", "return [x for x in records]")
    assert a == b != c
    assert fingerprint("test", "", "", "The tests check failed") == \
        fingerprint("test", "", "", "the  tests CHECK failed")


# ----- capture semantics -----

def _candidate(**overrides) -> IssueCandidate:
    values = {"title": "The bound is wrong", "severity": "medium",
              "file": "app.py", "line": 1,
              "snippet": 'VALUE = "before"', "kind": "review"}
    values.update(overrides)
    return IssueCandidate(**values)


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


def test_capture_deduplicates_within_one_batch(tmp_path):
    store = _store(tmp_path)
    result = store.capture([_candidate(), _candidate(line=9)],
                           source="scan")
    assert len(result.added) == 1 and len(store.load()) == 1


def test_capture_carries_and_refreshes_the_group(tmp_path):
    store = _store(tmp_path)
    store.capture([_candidate(group="value handling")], source="scan")
    assert store.load()[0].group == "value handling"
    # A later scan can name the group; silence keeps the known one.
    store.capture([_candidate(group="")], source="scan")
    assert store.load()[0].group == "value handling"
    store.capture([_candidate(group="  bounds  ")], source="scan")
    assert store.load()[0].group == "bounds"
    long = store.add(title="Wide", group="g" * 60)
    assert len(long.group) == 40


def test_related_open_issues_group_first_then_same_file(tmp_path):
    store = _store(tmp_path)
    store.capture([
        _candidate(title="Parser drops the last row", file="src/parse.py",
                   snippet="rows[:-1]", group="parser bounds"),
        _candidate(title="Writer skips the header", file="src/write.py",
                   snippet="skip_header", group="parser bounds"),
        _candidate(title="A slow loop", file="src/parse.py",
                   snippet="for x in xs"),
        _candidate(title="Another slow loop", file="src/parse.py",
                   snippet="while True"),
    ], source="scan")
    by_title = {issue.title: issue for issue in store.load()}

    # A grouped pick relates by its label across files — never by file.
    mates = related_open_issues(store.load(),
                                by_title["Parser drops the last row"])
    assert [x.title for x in mates] == ["Writer skips the header"]

    # An ungrouped pick falls back to its file, skipping grouped issues.
    mates = related_open_issues(store.load(), by_title["A slow loop"])
    assert [x.title for x in mates] == ["Another slow loop"]

    # A closed relative never comes back into the offer.
    store.close(by_title["Writer skips the header"].id, REASON_FIXED)
    assert related_open_issues(
        store.load(), store.get(by_title["Parser drops the last row"].id)
    ) == []

    # The offer stays a decision, not a wall: at most four relatives.
    store.capture([_candidate(title=f"Wide fault {index}",
                              file=f"src/w{index}.py",
                              snippet=f"w{index}", group="wide net")
                   for index in range(6)], source="scan")
    picked = next(issue for issue in store.load()
                  if issue.title == "Wide fault 0")
    assert len(related_open_issues(store.load(), picked)) == 4


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


def test_engine_captures_review_findings_and_closes_on_delivery(tmp_path):
    repository = _repository(tmp_path)
    config = _config(tmp_path, repository)
    store = IssueStore(runtime_root=config.runtime_root, repository=repository)
    provider = ReviewOnceProvider()
    engine = WorkflowEngine(config, QuietPresenter(),
                            provider_builder=lambda n, c, e: provider,
                            issues=store)
    record = engine.start("feature", "Change the value")
    assert RunState(record.state) is RunState.AWAITING_ACCEPTANCE

    captured = store.load()
    assert len(captured) == 1
    issue = captured[0]
    assert issue.source == "review" and issue.severity == "medium"
    assert issue.file == "app.py" and record.run_id in issue.runs
    assert issue.status == OPEN

    engine.accept(record.run_id)
    engine.deliver(record.run_id)
    final = store.get(issue.id)
    assert final.status == CLOSED and final.closed_reason == REASON_FIXED


def test_engine_keeps_issues_cited_by_the_final_review(tmp_path):
    repository = _repository(tmp_path)
    config = _config(tmp_path, repository)
    store = IssueStore(runtime_root=config.runtime_root, repository=repository)
    provider = ReviewOnceProvider(approve_findings=[{
        "severity": "low", "file": "app.py", "line": 1,
        "evidence": "A style nit that does not block.",
        "remediation": "Improve the name later.",
    }])
    engine = WorkflowEngine(config, QuietPresenter(),
                            provider_builder=lambda n, c, e: provider,
                            issues=store)
    record = engine.start("feature", "Change the value")
    engine.accept(record.run_id)
    engine.deliver(record.run_id)
    issues = store.load()
    assert len(issues) == 1
    assert issues[0].status == OPEN


def test_engine_captures_failed_checks(tmp_path):
    repository = _repository(tmp_path)
    data = default_config(repository, "codex")
    data["audit"] = {"runtime_root": str(tmp_path / "runtime")}
    data["verification"]["commands"] = {
        "always-fails": {"argv": ["python3", "-c", "raise SystemExit(1)"],
                         "phase": "verify", "timeout_seconds": 60}}
    path = repository / ".maintain.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    config = ProjectConfig.load(path)
    store = IssueStore(runtime_root=config.runtime_root, repository=repository)
    provider = ReviewOnceProvider(approve_findings=[])
    provider.review_calls = 1  # approve immediately
    engine = WorkflowEngine(config, QuietPresenter(),
                            provider_builder=lambda n, c, e: provider,
                            issues=store)
    record = engine.start("feature", "Change the value")
    assert RunState(record.state) in {RunState.NEEDS_HUMAN, RunState.TEST_FAILED}
    issues = store.load()
    assert any(issue.source == "test"
               and issue.title == "The always-fails check failed"
               for issue in issues)


# ----- scan and discuss packets -----

def test_scan_request_and_packet_carry_codebase_and_known_issues(tmp_path):
    repository = _repository(tmp_path)
    config = _config(tmp_path, repository)
    known = [Issue(id="aa11bb", title="Known point", file="app.py")]
    request = scan_request(config, "the value bound", known)
    assert request.role == "scan" and request.run_id.startswith("scan-")
    assert request.payload["known_issues"][0]["id"] == "aa11bb"
    paths = [item["path"] for item in request.payload["repository_map"]]
    assert "app.py" in paths

    exchange = SideExchange(kind="scan", request=request,
                            directory=side_packet_dir(config, request.run_id))
    attachment = tmp_path / "tracker.csv"
    attachment.write_text("ref,summary\nT-1,Old fault\n", encoding="utf-8")
    packet = build_side_packet(exchange, config, [attachment])
    assert packet.task_key == "scan"
    assert "attachments/tracker.csv" in packet.members


def test_scan_candidates_validate_and_verify_snippets(tmp_path):
    repository = _repository(tmp_path)
    good = {"title": "The value is wrong", "severity": "high",
            "file": "app.py", "line": 1, "snippet": 'VALUE = "before"',
            "detail": "The value must be after.", "external_ref": "T-1",
            "group": "value handling"}
    fabricated = {"title": "Invented point", "severity": "low",
                  "file": "app.py", "line": 3,
                  "snippet": "does_not_exist()", "detail": ""}
    candidates = scan_candidates({"issues": [good, fabricated]}, repository)
    assert candidates[0].verified is True
    assert candidates[0].external_ref == "T-1"
    assert candidates[0].group == "value handling"
    assert candidates[1].verified is False
    assert candidates[1].group == ""

    with pytest.raises(ProviderError):
        scan_candidates({"issues": "no"}, repository)
    with pytest.raises(ProviderError):
        scan_candidates({"issues": [{"title": "", "severity": "low"}]},
                        repository)
    with pytest.raises(ProviderError):
        scan_candidates({"issues": [{"title": "x", "severity": "urgent"}]},
                        repository)


def test_side_packets_carry_the_ground_rules_and_configured_prompts(tmp_path):
    import zipfile
    from maintain.engine import PROVIDER_SAFETY_HEADER
    from maintain.ui.config_store import ConfigStore

    repository = _repository(tmp_path)
    config = _config(tmp_path, repository)
    (repository / "GLOBAL.md").write_text(
        "# Project ground rules\n\nStay inside the wind tools scope.\n",
        encoding="utf-8")

    def taskmd(request) -> tuple[str, str]:
        exchange = SideExchange(kind=request.role, request=request,
                                directory=side_packet_dir(config,
                                                          request.run_id))
        packet = build_side_packet(exchange, config, [])
        with zipfile.ZipFile(packet.zip_path) as archive:
            return (archive.read("TASK.md").decode(),
                    archive.read("GLOBAL.md").decode())

    store = _store(tmp_path)
    issue = store.add(title="The bound is wrong", file="app.py", line=1)
    for request in (scan_request(config, "", []),
                    discuss_request(config, issue, "Severity?")):
        # The safety header and the configured ground rules ride along.
        assert request.instructions.startswith(PROVIDER_SAFETY_HEADER)
        task_text, global_text = taskmd(request)
        assert "Stay inside the wind tools scope." in global_text
        assert "Read `GLOBAL.md` first. Obey its limits." in task_text
        assert PROVIDER_SAFETY_HEADER in task_text

    # A configured scan prompt replaces the built-in text in the packet,
    # and the safety header survives the override.
    ConfigStore(config).set_task_prompt(
        "scan", "Scan only the loader module for unit faults.")
    config = ProjectConfig.load(config.path)
    task_text, _ = taskmd(scan_request(config, "", []))
    assert "Scan only the loader module for unit faults." in task_text
    assert PROVIDER_SAFETY_HEADER in task_text
    assert "cross-reference spreadsheet rows" not in task_text


def test_discuss_request_and_reply_validation(tmp_path):
    repository = _repository(tmp_path)
    config = _config(tmp_path, repository)
    store = _store(tmp_path)
    issue = store.add(title="The bound is wrong", file="app.py", line=1,
                      snippet='VALUE = "before"')
    request = discuss_request(config, issue, "Is high the right severity?")
    assert request.role == "discuss"
    assert request.payload["issue"]["id"] == issue.id
    assert request.payload["candidate_files"][0]["path"] == "app.py"

    parsed = discuss_reply({"reply": "Yes. The bound loses data.",
                            "severity": "high"})
    assert parsed.reply.startswith("Yes.") and parsed.severity == "high"
    assert discuss_reply({"reply": "Fine."}).severity == ""
    with pytest.raises(ProviderError):
        discuss_reply({"reply": "  "})
    with pytest.raises(ProviderError):
        discuss_reply({"reply": "ok", "severity": "urgent"})
