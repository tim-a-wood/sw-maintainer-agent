from __future__ import annotations

import io
import json
import subprocess
import sys
import tomllib
import venv
from pathlib import Path

from maintain import __version__
from maintain.audit import AuditStore
from maintain.cli import _shell_command
from maintain.config import ProjectConfig, default_config
from maintain.engine import WorkflowEngine
from maintain.models import ProviderRequest, ProviderResponse
from maintain.presenter import Presenter, QuietPresenter
from maintain.providers.base import Provider
from maintain.providers.browser import _sanitized_browser_url


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
    _git(repository, "config", "core.autocrlf", "false")
    (repository / "app.py").write_text('VALUE = "before"\n', encoding="utf-8",
                                    newline="\n")
    _git(repository, "add", "app.py")
    _git(repository, "commit", "-m", "initial")
    return repository


class ScriptedProvider(Provider):
    def __init__(self) -> None:
        self.preflight_calls = 0
        self.roles: list[str] = []

    def preflight(self) -> None:
        self.preflight_calls += 1

    def exchange(self, request: ProviderRequest) -> ProviderResponse:
        self.roles.append(request.role)
        if request.role == "scope":
            content = {
                "tasks": [{
                    "id": "change-value",
                    "objective": "Change the value",
                    "allowed_files": ["app.py"],
                    "done_when": ["VALUE is set to after."],
                    "verification": ["Read app.py."],
                    "depends_on": [],
                }],
            }
        elif request.role == "implement":
            content = {
                "patch": (
                    "diff --git a/app.py b/app.py\n"
                    "--- a/app.py\n"
                    "+++ b/app.py\n"
                    "@@ -1 +1 @@\n"
                    '-VALUE = "before"\n'
                    '+VALUE = "after"\n'
                ),
            }
        elif request.role == "review":
            content = {"decision": "approve", "findings": []}
        else:  # pragma: no cover - the engine must never invent a role
            raise AssertionError(request.role)
        return ProviderResponse(
            schema_version=request.schema_version,
            run_id=request.run_id,
            task_id=request.task_id,
            role=request.role,
            content=content,
            provider="scripted",
            conversation_id=f"{request.role}-{request.task_id}",
        )


def test_active_workflow_reaches_delivery_and_updates_source_branch(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    runtime_root = tmp_path / "maintain-data" / "runs"
    candidate = default_config(repository, "codex")
    candidate["providers"] = {
        "profiles": {"scripted": {"type": "command", "argv": ["unused"]}},
        "roles": {"scope": "scripted", "implement": "scripted", "review": "scripted"},
    }
    candidate["verification"]["commands"] = {
        "behavior": {
            "argv": [
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; "
                    "raise SystemExit(0 if "
                    "Path('app.py').read_text(encoding='utf-8') == "
                    "'VALUE = \"after\"\\n' else 1)"
                ),
            ],
            "phase": "verify",
            "timeout_seconds": 30,
        },
    }
    candidate["audit"]["runtime_root"] = str(runtime_root)
    config_path = repository / ".maintain.json"
    config_path.write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8")
    config = ProjectConfig.load(config_path)

    provider = ScriptedProvider()
    report = io.StringIO()
    engine = WorkflowEngine(
        config,
        Presenter(stream=report, animate=False, no_color=True, width=96),
        provider_builder=lambda *_args, **_kwargs: provider,
    )

    record = engine.start("feature", "Change VALUE in app.py to after")
    assert record.state == "awaiting_acceptance"
    assert provider.preflight_calls == 1
    assert provider.roles == ["scope", "implement", "review"]
    assert engine.gate_status(record)["local_commands"] == "pass"

    record = engine.accept(record.run_id)
    assert record.state == "accepted"
    record = engine.deliver(record.run_id)
    assert record.state == "delivered"
    record = engine.integrate(record.run_id, "main", confirmed=True)

    assert record.state == "delivered"
    assert (repository / "app.py").read_text(encoding="utf-8") == 'VALUE = "after"\n'
    assert _git(repository, "log", "-1", "--format=%s").startswith("maintain:")
    assert AuditStore(runtime_root, record.run_id).verify()["events"] > 0
    rendered = report.getvalue()
    assert "○  PREPARE    Prepare the project and assistant" in rendered
    assert "○  PLAN       Ask the configured assistant to plan the change" in rendered
    assert "○  CHANGE     Ask the configured assistant to create the change" in rendered
    assert "○  REVIEW     Ask the configured assistant to review the change" in rendered
    assert "✓  CHECK      The behavior check passed" in rendered
    assert "✓  TEST       All local checks passed" in rendered
    assert "✓  DELIVER    Created the verified commit" in rendered
    assert "context expanding" not in rendered.casefold()


def test_m365_setup_uses_edge_and_the_supported_entrypoint(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    candidate = default_config(repository, "m365-browser")
    profile = candidate["providers"]["profiles"]["m365"]

    assert profile["browser"] == "msedge"
    assert profile["url"].startswith("https://copilot.cloud.microsoft/")
    assert set(profile["allowed_hosts"]) == {
        "copilot.cloud.microsoft",
        "m365.cloud.microsoft",
    }


def test_windows_follow_up_commands_use_powershell_literal_quoting() -> None:
    rendered = _shell_command(
        [
            "maintain",
            "--repo",
            r"C:\repo&tools\[x64]\O'Brien %NAME%",
            "--config",
            r"C:\repo&tools\[x64]\O'Brien %NAME%\.maintain.json",
        ],
        windows=True,
    )

    assert rendered.startswith("& 'maintain' '--repo' ")
    assert r"'C:\repo&tools\[x64]\O''Brien %NAME%'" in rendered
    assert r"'C:\repo&tools\[x64]\O''Brien %NAME%\.maintain.json'" in rendered


def test_browser_diagnostics_remove_authentication_query_data() -> None:
    sanitized = _sanitized_browser_url(
        "https://m365.cloud.microsoft/chat/"
        "?login_hint=person%40example.com&client-request-id=secret#message"
    )

    assert sanitized == "https://m365.cloud.microsoft/chat/"
    assert "person" not in sanitized
    assert "client-request-id" not in sanitized


def test_runtime_and_package_versions_match() -> None:
    project = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert project["project"]["version"] == __version__


def test_missing_project_pytest_stops_before_assistant_work(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    candidate = default_config(repository, "codex")
    candidate["providers"] = {
        "profiles": {"scripted": {"type": "command", "argv": ["unused"]}},
        "roles": {"scope": "scripted", "implement": "scripted", "review": "scripted"},
    }
    candidate["verification"]["commands"] = {
        "tests": {
            "argv": ["{python}", "-m", "pytest"],
            "phase": "verify",
            "timeout_seconds": 30,
        },
    }
    candidate["audit"]["runtime_root"] = str(tmp_path / "maintain-data" / "runs")
    config_path = repository / ".maintain.json"
    config_path.write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8")
    config = ProjectConfig.load(config_path)

    isolated_python = tmp_path / "python-without-pytest"
    venv.EnvBuilder(with_pip=False).create(isolated_python)
    python = isolated_python / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )
    provider = ScriptedProvider()
    engine = WorkflowEngine(
        config,
        QuietPresenter(),
        provider_builder=lambda *_args, **_kwargs: provider,
    )
    engine.runner.python_executable = str(python)

    record = engine.start("feature", "Change VALUE in app.py to after")

    assert record.state == "needs_human"
    assert "Pytest is not installed in the project Python environment" in record.error
    assert provider.preflight_calls == 0
    assert provider.roles == []
