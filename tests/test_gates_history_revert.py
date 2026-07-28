"""M2: workflow gates, rescope, iteration history, and revert."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from maintain.config import ProjectConfig, default_config
from maintain.engine import WorkflowEngine
from maintain.errors import PolicyError
from maintain.gates import GateDecision, GateStop, WorkflowGates
from maintain.history import list_runs, run_timeline
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
    _git(repository, "config", "core.autocrlf", "false")
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


PATCH = (
    "diff --git a/app.py b/app.py\n"
    "--- a/app.py\n"
    "+++ b/app.py\n"
    "@@ -1 +1 @@\n"
    '-VALUE = "before"\n'
    '+VALUE = "after"\n'
)


class ScriptedProvider(Provider):
    """Scope twice with different plans; review rejects once, then approves."""

    def __init__(self, *, reject_first_review: bool = False) -> None:
        self.scope_calls = 0
        self.review_calls = 0
        self.reject_first_review = reject_first_review
        self.scope_payloads: list[dict] = []

    def preflight(self) -> None:
        return None

    def exchange(self, request: ProviderRequest) -> ProviderResponse:
        if request.role == "scope":
            self.scope_calls += 1
            self.scope_payloads.append(request.payload)
            content = {"tasks": [{
                "id": f"change-value-{self.scope_calls}",
                "objective": "Change the value",
                "allowed_files": ["app.py"],
                "done_when": ["VALUE is set to after."],
                "verification": ["Read app.py."],
                "depends_on": [],
            }]}
        elif request.role == "implement":
            content = {"patch": PATCH}
        elif request.role == "review":
            self.review_calls += 1
            if self.reject_first_review and self.review_calls == 1:
                content = {"decision": "changes_requested", "findings": [{
                    "severity": "low", "file": "app.py", "line": 1,
                    "evidence": "The value has no unit comment.",
                    "remediation": "State the unit in a comment.",
                }]}
            else:
                content = {"decision": "approve", "findings": []}
        else:  # pragma: no cover
            raise AssertionError(request.role)
        return ProviderResponse(
            schema_version=request.schema_version, run_id=request.run_id,
            task_id=request.task_id, role=request.role, content=content,
            provider="scripted",
            conversation_id=f"{request.role}-{request.task_id}-{self.scope_calls}"
                            f"-{self.review_calls}")


class ScriptedGates(WorkflowGates):
    def __init__(self, plan: list[GateDecision] | None = None,
                 review: list[GateDecision] | None = None) -> None:
        self.plan = list(plan or [])
        self.review = list(review or [])
        self.plans_seen: list[list] = []

    def plan_review(self, record, tasks) -> GateDecision:
        self.plans_seen.append(tasks)
        return self.plan.pop(0) if self.plan else GateDecision("accept")

    def review_findings(self, record, findings) -> GateDecision:
        return self.review.pop(0) if self.review else GateDecision("repair")


def _engine(config, provider, gates=None) -> WorkflowEngine:
    return WorkflowEngine(config, QuietPresenter(),
                         provider_builder=lambda name, cfg, evidence: provider,
                         gates=gates)


def test_default_gates_keep_the_automatic_flow(tmp_path):
    repository = _repository(tmp_path)
    config = _config(tmp_path, repository)
    provider = ScriptedProvider()
    record = _engine(config, provider).start("feature", "Change the value")
    assert RunState(record.state) is RunState.AWAITING_ACCEPTANCE
    assert record.evidence["plan_approved"] is True


def test_plan_gate_rescope_replans_with_the_note(tmp_path):
    repository = _repository(tmp_path)
    config = _config(tmp_path, repository)
    provider = ScriptedProvider()
    gates = ScriptedGates(plan=[GateDecision("rescope", "Make it smaller."),
                                GateDecision("accept")])
    record = _engine(config, provider, gates).start("feature", "Change the value")
    assert RunState(record.state) is RunState.AWAITING_ACCEPTANCE
    assert provider.scope_calls == 2
    assert provider.scope_payloads[1]["human_notes"] == ["Make it smaller."]
    assert record.tasks[0]["id"] == "change-value-2"
    labels = [item.label for item in run_timeline(config.runtime_root, record.run_id)]
    assert "Plan requested again" in labels
    assert any(label.startswith("Plan changed") for label in labels)


def test_review_gate_rescope_resets_the_worktree(tmp_path):
    repository = _repository(tmp_path)
    config = _config(tmp_path, repository)
    provider = ScriptedProvider(reject_first_review=True)
    gates = ScriptedGates(review=[GateDecision("rescope", "Plan it differently.")])
    record = _engine(config, provider, gates).start("feature", "Change the value")
    assert RunState(record.state) is RunState.AWAITING_ACCEPTANCE
    assert provider.scope_calls == 2
    worktree = Path(record.worktree)
    assert (worktree / "app.py").read_text(encoding="utf-8") == 'VALUE = "after"\n'


def test_gate_stop_pauses_and_resume_continues(tmp_path):
    repository = _repository(tmp_path)
    config = _config(tmp_path, repository)
    provider = ScriptedProvider()

    class StopOnce(WorkflowGates):
        def __init__(self) -> None:
            self.stopped = False

        def plan_review(self, record, tasks) -> GateDecision:
            if not self.stopped:
                self.stopped = True
                raise GateStop()
            return GateDecision("accept")

    gates = StopOnce()
    engine = _engine(config, provider, gates)
    record = engine.start("feature", "Change the value")
    assert RunState(record.state) is RunState.NEEDS_HUMAN
    resumed = engine.resume(record.run_id)
    assert RunState(resumed.state) is RunState.AWAITING_ACCEPTANCE


def test_timeline_records_the_full_loop(tmp_path):
    repository = _repository(tmp_path)
    config = _config(tmp_path, repository)
    provider = ScriptedProvider(reject_first_review=True)
    engine = _engine(config, provider)
    record = engine.start("feature", "Change the value")
    engine.accept(record.run_id)
    engine.deliver(record.run_id)
    timeline = run_timeline(config.runtime_root, record.run_id)
    kinds = [item.kind for item in timeline]
    assert kinds[0] == "start"
    for expected in ("plan_proposed", "plan_approved", "build_applied",
                     "review_found", "repair_applied", "review_approved",
                     "checks_passed", "saved"):
        assert expected in kinds
    saved = [item for item in timeline if item.kind == "saved"]
    assert saved and not saved[0].can_go_back

    runs = list_runs(config.runtime_root, repository)
    assert runs[0].run_id == record.run_id
    assert runs[0].display_state == "Saved"
    assert runs[0].closed


def test_revert_returns_to_the_plan_and_the_run_completes_again(tmp_path):
    repository = _repository(tmp_path)
    config = _config(tmp_path, repository)
    provider = ScriptedProvider()
    engine = _engine(config, provider)
    record = engine.start("feature", "Change the value")
    assert RunState(record.state) is RunState.AWAITING_ACCEPTANCE
    worktree = Path(record.worktree)
    assert (worktree / "app.py").read_text(encoding="utf-8") == 'VALUE = "after"\n'

    timeline = run_timeline(config.runtime_root, record.run_id)
    plan_event = next(item for item in timeline if item.kind == "plan_proposed")
    reverted = engine.revert_to(record.run_id, plan_event.sequence)
    assert RunState(reverted.state) is RunState.TASKS_READY
    assert (worktree / "app.py").read_text(encoding="utf-8") == 'VALUE = "before"\n'
    assert "plan_approved" not in reverted.evidence

    after = run_timeline(config.runtime_root, record.run_id)
    revert_events = [item for item in after if item.kind == "revert"]
    assert revert_events and revert_events[0].label == "Went back"
    superseded = [item for item in after if item.superseded]
    assert any(item.kind == "build_applied" for item in superseded)

    finished = engine.run(reverted)
    assert RunState(finished.state) is RunState.AWAITING_ACCEPTANCE
    assert (worktree / "app.py").read_text(encoding="utf-8") == 'VALUE = "after"\n'


def test_revert_to_build_repeats_review_and_verification(tmp_path):
    repository = _repository(tmp_path)
    config = _config(tmp_path, repository)
    provider = ScriptedProvider()
    engine = _engine(config, provider)
    record = engine.start("feature", "Change the value")
    timeline = run_timeline(config.runtime_root, record.run_id)
    build_event = next(item for item in timeline if item.kind == "build_applied")
    reverted = engine.revert_to(record.run_id, build_event.sequence)
    assert RunState(reverted.state) is RunState.REVIEWING
    assert (Path(record.worktree) / "app.py").read_text(
        encoding="utf-8") == 'VALUE = "after"\n'
    finished = engine.run(reverted)
    assert RunState(finished.state) is RunState.AWAITING_ACCEPTANCE


def test_revert_refuses_closed_runs_and_non_anchors(tmp_path):
    repository = _repository(tmp_path)
    config = _config(tmp_path, repository)
    provider = ScriptedProvider()
    engine = _engine(config, provider)
    record = engine.start("feature", "Change the value")
    timeline = run_timeline(config.runtime_root, record.run_id)
    start_event = next(item for item in timeline if item.kind == "start")
    with pytest.raises(PolicyError, match="not a go-back point"):
        engine.revert_to(record.run_id, start_event.sequence)
    engine.accept(record.run_id)
    engine.deliver(record.run_id)
    plan_event = next(item for item in timeline if item.kind == "plan_proposed")
    with pytest.raises(PolicyError, match="closed"):
        engine.revert_to(record.run_id, plan_event.sequence)
