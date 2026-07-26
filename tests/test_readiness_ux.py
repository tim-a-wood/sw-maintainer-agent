from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import maintain.cli as cli
from maintain.config import ProjectConfig, default_config
from maintain.engine import WorkflowEngine
from maintain.errors import PolicyError
from maintain.models import ProviderCapabilities, ProviderRequest
from maintain.presenter import QuietPresenter
from maintain.providers.base import Provider


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "project"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Maintain Test")
    _git(repository, "config", "user.email", "maintain@example.invalid")
    (repository / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repository, "add", "app.py")
    _git(repository, "commit", "-m", "initial")
    return repository


def _write_config(repository: Path, candidate: dict) -> ProjectConfig:
    path = repository / ".maintain.json"
    path.write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8")
    return ProjectConfig.load(path)


class TrackingProvider(Provider):
    capabilities = ProviderCapabilities()

    def __init__(self) -> None:
        self.preflight_calls = 0

    def preflight(self) -> None:
        self.preflight_calls += 1

    def exchange(self, request: ProviderRequest):
        raise AssertionError(f"Unexpected provider exchange for {request.role}")


def test_doctor_rejects_zero_commands_before_provider_work(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    candidate = default_config(repository, "codex")
    candidate["providers"] = {
        "profiles": {
            "tracking": {"type": "command", "argv": [sys.executable]},
        },
        "roles": {
            "scope": "tracking",
            "implement": "tracking",
            "review": "tracking",
        },
    }
    candidate["verification"]["commands"] = {}
    candidate["audit"]["runtime_root"] = str(tmp_path / "maintain-data" / "runs")
    config = _write_config(repository, candidate)
    provider = TrackingProvider()
    engine = WorkflowEngine(
        config,
        QuietPresenter(),
        provider_builder=lambda *_args, **_kwargs: provider,
    )

    with pytest.raises(
            PolicyError, match="No local verification command is configured"):
        engine.doctor()

    assert provider.preflight_calls == 0


def test_human_doctor_output_separates_preflight_categories_and_execution(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    repository = _repository(tmp_path)
    candidate = default_config(repository, "file-exchange")
    candidate["providers"]["profiles"]["exchange"]["exchange_dir"] = str(
        tmp_path / "exchange")
    candidate["audit"]["runtime_root"] = str(tmp_path / "maintain-data" / "runs")
    _write_config(repository, candidate)
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))

    result = cli.main([
        "--repo", str(repository), "--no-animation", "--no-color", "doctor",
    ])

    assert result == 0
    output = " ".join(capsys.readouterr().out.split())
    assert "PREFLIGHT PASSED" in output
    assert "Maintain can start a maintenance run." in output
    assert "No project verification commands were run." in output
    assert "REPOSITORY" in output
    assert "ASSISTANT PREFLIGHT" in output
    assert "VERIFICATION SETUP" in output
    assert "verified work" not in output.casefold()


def test_human_doctor_warns_when_coverage_is_diff_only(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    repository = _repository(tmp_path)
    candidate = default_config(repository, "file-exchange")
    candidate["providers"]["profiles"]["exchange"]["exchange_dir"] = str(
        tmp_path / "exchange")
    candidate["audit"]["runtime_root"] = str(tmp_path / "maintain-data" / "runs")
    _write_config(repository, candidate)
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))

    result = cli.main([
        "--repo", str(repository), "--no-animation", "--no-color", "doctor",
    ])

    assert result == 0
    output = " ".join(capsys.readouterr().out.split())
    assert "COVERAGE" in output
    assert "Diff formatting only; no project test command was detected" in output
    assert "Configure a project test command before relying on behavioral verification." in output


def test_provider_login_reports_session_and_unavailable_model_action(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    repository = _repository(tmp_path)
    candidate = default_config(repository, "chatgpt-browser")
    profile = candidate["providers"]["profiles"]["chatgpt"]
    profile["profile_dir"] = str(tmp_path / "browser-profile")
    profile["model"] = "Retired Model"
    profile["available_models"] = ["Retired Model"]
    candidate["audit"]["runtime_root"] = str(tmp_path / "maintain-data" / "runs")
    _write_config(repository, candidate)
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))

    class BrowserProvider:
        capabilities = ProviderCapabilities(browser_automation=True)

        def __init__(self) -> None:
            self.login_calls = 0
            self.require_selected_model: list[bool] = []

        def login(self) -> None:
            self.login_calls += 1

        def compatibility_check(self, *, require_selected_model: bool = True):
            self.require_selected_model.append(require_selected_model)
            return {
                "ready": True,
                "layout": "chatgpt-current",
                "configured_model": "Retired Model",
                "model_available": False,
                "models": ["Current Model"],
            }

    provider = BrowserProvider()

    class FakeEngine:
        def __init__(self, _config, _presenter) -> None:
            self.provider_builder = lambda *_args, **_kwargs: provider

    monkeypatch.setattr(cli, "WorkflowEngine", FakeEngine)

    result = cli.main([
        "--repo", str(repository), "--no-animation", "--no-color",
        "provider", "login", "chatgpt",
    ])

    assert result == 0
    assert provider.login_calls == 1
    assert provider.require_selected_model == [False]
    output = " ".join(capsys.readouterr().out.split())
    assert "SESSION VERIFIED" in output
    assert "Retired Model is unavailable" in output
    assert "Refresh the model list before starting work:" in output
    expected_command = cli._shell_command([
        "maintain", "--repo", str(repository),
        "--config", str(repository / ".maintain.json"),
        "provider", "model", "chatgpt", "--refresh",
    ])
    assert expected_command in output
    assert "provider is ready" not in output.casefold()
