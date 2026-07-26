from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import maintain.cli as cli
from maintain.models import RunRecord


class _RecordingConsole:
    def __init__(self, events: list[dict[str, object]]) -> None:
        self.events = events

    def print(self, *values: object, **_kwargs: object) -> None:
        self.events.append({
            "kind": "print",
            "text": " ".join(str(value) for value in values),
        })


class _RecordingPresenter:
    def __init__(self, answers: list[str | None]) -> None:
        self.answers = list(answers)
        self.events: list[dict[str, object]] = []
        self.console = _RecordingConsole(self.events)

    def outcome(
        self,
        label: str,
        title: str,
        message: str = "",
        facts=(),
        actions=(),
        tone: str = "accent",
    ) -> None:
        self.events.append({
            "kind": "outcome",
            "label": label,
            "title": title,
            "message": message,
            "facts": list(facts),
            "actions": list(actions),
            "tone": tone,
        })

    def ask(self, label: str, default: str = "") -> str:
        self.events.append({"kind": "ask", "label": label, "default": default})
        if not self.answers:
            raise AssertionError(f"Unexpected prompt: {label}")
        answer = self.answers.pop(0)
        return default if answer is None else answer

    def error(self, message: str, hint: str = "") -> None:
        self.events.append({
            "kind": "error",
            "message": message,
            "hint": hint,
        })


class _RecordingEngine:
    def __init__(self, resumed: RunRecord | None = None) -> None:
        self.resumed = resumed
        self.resume_calls: list[str] = []

    def resume(self, run_id: str) -> RunRecord:
        self.resume_calls.append(run_id)
        if self.resumed is None:
            raise AssertionError("This recovery choice must not resume the run")
        return self.resumed


def _paused_record(*, repair_limit: bool = False) -> RunRecord:
    evidence = {"paused_from": "testing"}
    if repair_limit:
        evidence["pause_reason"] = "repair_limit"
    return RunRecord(
        run_id="f-20260725-120000-abcd",
        mode="feature",
        request="Make recovery understandable",
        repository="/project",
        base_commit="base",
        branch="maintain/recovery",
        worktree="/worktree",
        state="needs_human",
        evidence=evidence,
        error="Pytest is not installed in the project Python environment.",
    )


@pytest.mark.parametrize("answer", [None, "N", "B"])
def test_interactive_recovery_default_no_and_back_do_not_resume(answer: str | None) -> None:
    record = _paused_record()
    presenter = _RecordingPresenter([answer])
    engine = _RecordingEngine()

    returned = cli._interactive_resume(engine, record, presenter)

    assert returned is record
    assert engine.resume_calls == []
    prompt = next(event for event in presenter.events if event["kind"] == "ask")
    assert prompt["label"] == "Have you completed the required action?"
    assert prompt["default"] == "N"


def test_interactive_recovery_yes_resumes_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _paused_record()
    resumed = _paused_record()
    resumed.state = "cancelled"
    presenter = _RecordingPresenter(["Y"])
    engine = _RecordingEngine(resumed)
    monkeypatch.setattr(cli, "_pause", lambda _presenter: None)

    returned = cli._interactive_resume(engine, record, presenter)

    assert returned is resumed
    assert engine.resume_calls == [record.run_id]


def test_interactive_recovery_invalid_answer_reprompts_without_resuming() -> None:
    record = _paused_record()
    presenter = _RecordingPresenter(["later", "N"])
    engine = _RecordingEngine()

    returned = cli._interactive_resume(engine, record, presenter)

    assert returned is record
    assert engine.resume_calls == []
    prompts = [event for event in presenter.events if event["kind"] == "ask"]
    assert len(prompts) == 2
    assert any(
        event["kind"] == "print"
        and "Choose Y to try again or N to keep the run saved." in str(event["text"])
        for event in presenter.events
    )


def test_repair_limit_explains_and_confirms_another_cycle() -> None:
    record = _paused_record(repair_limit=True)
    presenter = _RecordingPresenter(["N"])
    engine = _RecordingEngine()

    cli._interactive_resume(engine, record, presenter)

    outcome = next(event for event in presenter.events if event["kind"] == "outcome")
    assert any(
        "Continuing allows one more automated repair cycle." in action
        for action in outcome["actions"]
    )
    prompt = next(event for event in presenter.events if event["kind"] == "ask")
    assert prompt["label"] == "Start another automated repair cycle now?"
    assert prompt["default"] == "N"
    assert engine.resume_calls == []


def test_saved_error_and_guidance_appear_before_confirmation_prompt() -> None:
    record = _paused_record()
    presenter = _RecordingPresenter(["N"])
    engine = _RecordingEngine()

    cli._interactive_resume(engine, record, presenter)

    outcome_index = next(
        index for index, event in enumerate(presenter.events)
        if event["kind"] == "outcome"
    )
    prompt_index = next(
        index for index, event in enumerate(presenter.events)
        if event["kind"] == "ask"
    )
    outcome = presenter.events[outcome_index]
    assert outcome_index < prompt_index
    assert outcome["message"] == record.error
    assert any(
        "Run the affected verification command" in action
        for action in outcome["actions"]
    )


def test_direct_resume_command_is_explicit_and_never_prompts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    record = _paused_record()
    record.repository = str(tmp_path)
    calls: list[str] = []

    class _DirectResumeEngine:
        def __init__(self, _config: object, _presenter: object) -> None:
            pass

        def resume(self, run_id: str) -> RunRecord:
            calls.append(run_id)
            return record

    config = SimpleNamespace(repository=tmp_path, path=tmp_path / ".maintain.json")
    monkeypatch.setattr(cli, "_config", lambda _args: config)
    monkeypatch.setattr(cli, "WorkflowEngine", _DirectResumeEngine)

    def unexpected_input(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("The explicit resume command must not prompt")

    monkeypatch.setattr("builtins.input", unexpected_input)

    exit_code = cli.main([
        "--repo", str(tmp_path),
        "--json",
        "resume", record.run_id,
    ])

    assert exit_code == 0
    assert calls == [record.run_id]
    assert json.loads(capsys.readouterr().out)["run_id"] == record.run_id
