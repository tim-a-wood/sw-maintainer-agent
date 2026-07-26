from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

from maintain.cli import (
    _choose_run,
    _interactive_history,
    _load,
    _run_values,
    _verification_rows,
)
from maintain.models import RunRecord
from maintain.presenter import Presenter


def _config(tmp_path: Path) -> SimpleNamespace:
    repository = tmp_path / "project"
    repository.mkdir()
    return SimpleNamespace(
        repository=repository.resolve(),
        runtime_root=tmp_path / "maintain-data" / "runs",
    )


def _write_run(config: SimpleNamespace, value: dict[str, object]) -> None:
    run_dir = config.runtime_root / str(value["run_id"])
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _record(config: SimpleNamespace, **changes: object) -> RunRecord:
    values: dict[str, object] = {
        "run_id": "f-20260725-120000-abcd",
        "mode": "feature",
        "request": "Fix history without changing the selected run",
        "repository": str(config.repository),
        "base_commit": "base",
        "branch": "maintain/example",
        "worktree": str(config.repository),
        "state": "needs_human",
        "created_at": "2026-07-25T12:00:00+00:00",
        "updated_at": "2026-07-25T12:01:00+00:00",
        "error": "Pytest is missing from the project Python environment.",
        "evidence": {},
    }
    values.update(changes)
    return RunRecord(**values)


def test_run_values_orders_by_updated_time_across_run_id_prefixes(tmp_path: Path) -> None:
    config = _config(tmp_path)
    common = {
        "state": "delivered",
        "mode": "feature",
        "request": "A saved request",
        "repository": str(config.repository),
    }
    _write_run(config, {
        **common,
        "run_id": "i-lexically-later",
        "updated_at": "2026-07-24T12:00:00+00:00",
    })
    _write_run(config, {
        **common,
        "run_id": "f-lexically-earlier",
        "updated_at": "2026-07-25T12:00:00+00:00",
    })

    assert [item["run_id"] for item in _run_values(config)] == [
        "f-lexically-earlier",
        "i-lexically-later",
    ]


def test_run_values_ignores_malformed_unknown_and_foreign_records(tmp_path: Path) -> None:
    config = _config(tmp_path)
    valid = {
        "run_id": "f-valid",
        "state": "delivered",
        "mode": "feature",
        "request": "Valid local run",
        "repository": str(config.repository),
        "updated_at": "2026-07-25T12:00:00+00:00",
    }
    _write_run(config, valid)
    _write_run(config, {
        **valid,
        "run_id": "f-foreign",
        "repository": str(tmp_path / "another-project"),
    })
    _write_run(config, {
        **valid,
        "run_id": "f-unknown-state",
        "state": "mystery",
    })
    _write_run(config, {
        "run_id": "f-missing-request",
        "state": "delivered",
        "mode": "feature",
        "repository": str(config.repository),
    })
    malformed = config.runtime_root / "f-malformed" / "run.json"
    malformed.parent.mkdir(parents=True)
    malformed.write_text("{not json", encoding="utf-8")

    assert [item["run_id"] for item in _run_values(config)] == ["f-valid"]


def test_utf8_run_loads_and_nonpositive_selection_is_rejected(tmp_path: Path) -> None:
    config = _config(tmp_path)
    newest = _record(
        config,
        run_id="f-newest",
        request="修正する 🚀",
        updated_at="2026-07-25T13:00:00+00:00",
    )
    older = _record(
        config,
        run_id="f-older",
        updated_at="2026-07-25T12:00:00+00:00",
    )
    _write_run(config, newest.to_dict())
    _write_run(config, older.to_dict())
    presenter = _ScriptedPresenter(["0", "-1", "1"])

    selected = _choose_run(config, presenter, history=True)

    assert selected == newest.run_id
    assert _load(config, newest.run_id).request == "修正する 🚀"


def test_verification_rows_aggregates_completed_and_current_without_final_duplicate(
        tmp_path: Path) -> None:
    config = _config(tmp_path)
    lint = {
        "name": "lint",
        "exit_code": 0,
        "duration_seconds": 0.25,
        "output_sha256": "lint-output",
    }
    unit = {
        "name": "unit tests",
        "exit_code": 0,
        "duration_seconds": 1.5,
        "output_sha256": "unit-output",
    }
    smoke = {
        "name": "smoke",
        "exit_code": 1,
        "duration_seconds": 2,
        "output_sha256": "smoke-output",
    }
    record = _record(
        config,
        tasks=[{"id": "task-one"}, {"id": "task-two"}],
        task_index=1,
        evidence={
            "completed_tasks": [
                {"task_id": "task-one", "tests": {"commands": [lint]}},
                {"task_id": "task-two", "tests": {"commands": [unit]}},
            ],
            "tests": {"commands": [unit, smoke]},
        },
    )

    assert _verification_rows(record) == [
        {
            "task": "task-one",
            "name": "lint",
            "result": "Passed",
            "exit_code": "0",
            "duration": "0.2s",
        },
        {
            "task": "task-two",
            "name": "unit tests",
            "result": "Passed",
            "exit_code": "0",
            "duration": "1.5s",
        },
        {
            "task": "task-two",
            "name": "smoke",
            "result": "Failed",
            "exit_code": "1",
            "duration": "2.0s",
        },
    ]


class _ScriptedPresenter(Presenter):
    def __init__(self, answers: list[str]) -> None:
        self.stream = io.StringIO()
        self._answers = iter(answers)
        super().__init__(
            stream=self.stream,
            animate=False,
            width=100,
            no_color=True,
        )

    def ask(self, label: str, default: str = "") -> str:
        return next(self._answers)


class _ReadOnlyEngine:
    def __init__(self) -> None:
        self.resume_calls: list[str] = []

    @staticmethod
    def gate_status(record: RunRecord) -> dict[str, str]:
        return {
            "configuration": "pass",
            "local_commands": "fail",
            "audit_chain": "pass",
        }

    def resume(self, run_id: str) -> RunRecord:
        self.resume_calls.append(run_id)
        raise AssertionError("Inspecting history must not resume a run.")


def test_selectable_history_shows_details_and_back_does_not_resume(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.runtime_root = tmp_path / "maintain-data [x64]" / "runs"
    record = _record(
        config,
        request="Preserve [x64] labels in saved history",
        error="Pytest [windows] is missing from the project environment.",
        evidence={
            "tests": {
                "commands": [{
                    "name": "unit tests [windows]",
                    "exit_code": 1,
                    "duration_seconds": 1.25,
                    "output_sha256": "failed-output",
                }],
            },
        },
    )
    _write_run(config, record.to_dict())
    presenter = _ScriptedPresenter(["1", "b", "b"])
    engine = _ReadOnlyEngine()

    _interactive_history(engine, config, presenter)

    output = presenter.stream.getvalue()
    normalized = output.casefold()
    assert "Select a numbered run to inspect without changing it." in output
    assert record.request in output
    assert "Feature · Needs Human" in output
    assert record.error in output
    assert "configuration" in normalized
    assert "local commands" in normalized
    assert "audit chain" in normalized
    assert "local verification" in normalized
    assert "unit tests [windows]" in output
    assert "Pytest [windows]" in output
    assert "SAVED EVIDENCE" in output
    assert "maintain-data [x64]" in output
    assert engine.resume_calls == []
