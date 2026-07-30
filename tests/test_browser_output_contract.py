from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from maintain.config import ProjectConfig, default_config
from maintain.engine import WorkflowEngine, _provider_step
from maintain.errors import ProviderError
from maintain.exchange_package import build_exchange_package
from maintain.models import ProviderRequest, ProviderResponse
from maintain.presenter import QuietPresenter
from maintain.providers.base import Provider
from maintain.providers.browser import BrowserProvider


def _request(
        *,
        mode: str = "feature",
        allowed_files: list[str] | None = None,
        allow_deletes: bool = False,
) -> ProviderRequest:
    paths = allowed_files or ["main.c"]
    return ProviderRequest(
        schema_version=1,
        run_id="f-20260726-120000-test",
        task_id="hello-world",
        role="implement",
        instructions="Implement the requested task.",
        payload={
            "mode": mode,
            "task": {"allowed_files": paths},
            "allow_new_files": True,
            "allow_deletes": allow_deletes,
            "approved_path_aliases": {},
        },
    )


def _browser(tmp_path: Path) -> BrowserProvider:
    return BrowserProvider(
        "assistant",
        {"profile_dir": str(tmp_path / "profile")},
        tmp_path / "evidence",
    )


def test_assistant_steps_describe_the_current_user_facing_activity() -> None:
    assert _provider_step("scope", "Microsoft 365 Copilot") == (
        "PLAN", "Ask Microsoft 365 Copilot to plan the change")
    assert _provider_step("implement", "Microsoft 365 Copilot") == (
        "CHANGE", "Ask Microsoft 365 Copilot to create the change")
    assert _provider_step("review", "Microsoft 365 Copilot") == (
        "REVIEW", "Ask Microsoft 365 Copilot to review the change")


def test_browser_statuses_report_completed_actions_in_plain_language(
        tmp_path: Path) -> None:
    provider = _browser(tmp_path)
    statuses: list[tuple[str, str]] = []
    provider.set_status_callback(lambda label, message: statuses.append((label, message)))

    provider._start_journey()
    for state in (
        "page_ready",
        "model_confirmed",
        "files_ready",
        "request_submitted",
        "response_complete",
        "response_saved",
    ):
        provider._mark_state(state)

    assert statuses == [
        ("BROWSER", "The assistant chat page is ready"),
        ("MODEL", "The assistant model is ready"),
        ("ATTACH", "Attached the project files for this step"),
        ("SEND", "Sent the request to the assistant"),
        ("RESPONSE", "The assistant completed its response"),
        ("SAVE", "Saved the response and evidence"),
    ]
    assert all(len(message.split()) <= 20 for _, message in statuses)


def test_implementation_task_uses_one_inline_json_contract(tmp_path: Path) -> None:
    package = build_exchange_package(_request(), tmp_path / "package")
    task = package.paths[0].read_text(encoding="utf-8")

    assert "one complete JSON envelope" in task
    assert "downloadable Markdown file" in task
    assert '"files": [' in task
    assert '"path": "exact/repository/path"' in task
    assert '"content": "complete final file contents\\n"' in task
    assert "maintain-output.zip" not in task


def test_m365_implementation_task_uses_downloadable_zip_contract(
        tmp_path: Path) -> None:
    package = build_exchange_package(
        _request(),
        tmp_path / "package",
        implementation_transport="zip",
    )
    task = package.paths[0].read_text(encoding="utf-8")

    assert "maintain-output.zip" in task
    assert "IMPLEMENTATION.toml" in task
    assert "`files/`" in task
    assert "reply only `Maintain output ready.`" in task
    assert "Do not return JSON" in task
    assert "```json" not in task
    assert 'files = ["exact/repository/path"]' in task
    assert f'run_id = "{_request().run_id}"' in task


def test_browser_implementation_transport_defaults_by_provider(tmp_path: Path) -> None:
    inline = _browser(tmp_path / "inline")
    zipped = BrowserProvider(
        "m365_copilot_browser",
        {"profile_dir": str(tmp_path / "zip" / "profile")},
        tmp_path / "zip" / "evidence",
    )

    assert inline._implementation_transport() == "inline"
    assert zipped._implementation_transport() == "zip"
    zipped.config["implementation_transport"] = "inline"
    assert zipped._implementation_transport() == "inline"


class _VisibleNode:
    def __init__(self, visible: bool) -> None:
        self.visible = visible

    def is_visible(self) -> bool:
        return self.visible


class _ArtifactPage:
    def __init__(self, artifact_visible: bool) -> None:
        self.artifact_visible = artifact_visible

    def locator(self, _selector: str):
        return self

    def all(self) -> list[_VisibleNode]:
        return [_VisibleNode(self.artifact_visible)]


def test_implementation_envelope_waits_for_zip_when_source_is_not_inline() -> None:
    selectors = {"output_download_selector": "a[download]"}
    envelope = {
        "content": {
            "files": [],
            "changed_files": ["main.c"],
            "deleted_files": [],
        },
    }

    assert not BrowserProvider._implementation_envelope_ready(
        _ArtifactPage(False), selectors, envelope)
    assert BrowserProvider._implementation_envelope_ready(
        _ArtifactPage(True), selectors, envelope)

    envelope["content"]["deleted_files"] = ["old.c"]
    envelope["content"]["changed_files"] = ["main.c", "old.c"]
    assert not BrowserProvider._implementation_envelope_ready(
        _ArtifactPage(False), selectors, envelope)

    envelope["content"]["changed_files"] = ["old.c"]
    assert BrowserProvider._implementation_envelope_ready(
        _ArtifactPage(False), selectors, envelope)


def test_issue_contract_requires_root_cause_but_feature_contract_omits_it(
        tmp_path: Path) -> None:
    feature = build_exchange_package(
        _request(mode="feature"), tmp_path / "feature").paths[0].read_text(encoding="utf-8")
    issue = build_exchange_package(
        _request(mode="issue"), tmp_path / "issue").paths[0].read_text(encoding="utf-8")

    assert '"root_cause"' not in feature
    assert '"root_cause"' in issue
    assert '"statement": "Code-grounded root cause."' in issue


def test_inline_complete_files_round_trip_to_zip(tmp_path: Path) -> None:
    browser = _browser(tmp_path)
    content = {
        "files": [{
            "path": "main.c",
            "content": '#include <stdio.h>\n\nint main(void) {\n    puts("Hello World");\n}\n',
        }],
        "changed_files": ["main.c"],
        "deleted_files": [],
    }

    output = browser._inline_output_zip(content, _request(), "exchange")

    assert output is not None
    with zipfile.ZipFile(output) as archive:
        assert archive.namelist() == ["main.c"]
        assert archive.read("main.c").decode() == content["files"][0]["content"]


def test_downloaded_zip_manifest_is_validated_and_synthesizes_content(
        tmp_path: Path) -> None:
    output = tmp_path / "maintain-output.zip"
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "IMPLEMENTATION.toml",
            (
                "schema_version = 1\n"
                f'run_id = "{_request().run_id}"\n'
                f'task_id = "{_request().task_id}"\n'
                'role = "implement"\n'
                'files = ["main.c"]\n'
                "deleted_files = []\n"
            ),
        )
        archive.writestr("files/main.c", "int main(void) { return 0; }\n")

    assert BrowserProvider._zip_artifact_content(output, _request()) == {
        "files": [],
        "changed_files": ["main.c"],
        "deleted_files": [],
    }


def test_inline_deletion_only_response_creates_an_empty_zip(tmp_path: Path) -> None:
    browser = _browser(tmp_path)
    content = {
        "files": [],
        "changed_files": ["obsolete.c"],
        "deleted_files": ["obsolete.c"],
    }

    output = browser._inline_output_zip(
        content,
        _request(allowed_files=["obsolete.c"], allow_deletes=True),
        "exchange",
    )

    assert output is not None
    with zipfile.ZipFile(output) as archive:
        assert archive.namelist() == []


def test_inline_changed_files_must_equal_replacements_and_deletions(
        tmp_path: Path) -> None:
    browser = _browser(tmp_path)
    request = _request(
        allowed_files=["main.c", "obsolete.c"],
        allow_deletes=True,
    )
    content = {
        "files": [{"path": "main.c", "content": "int main(void) { return 0; }\n"}],
        "changed_files": ["main.c"],
        "deleted_files": ["obsolete.c"],
    }

    with pytest.raises(ProviderError, match="must equal the union"):
        browser._inline_output_zip(content, request, "exchange")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (
            {
                "files": [{"path": "main.c", "content": "x"}],
                "changed_files": ["main.c", "main.c"],
                "deleted_files": [],
            },
            "must equal the union",
        ),
        (
            {
                "files": [{"path": "main.c", "content": "x"}],
                "changed_files": ["main.c"],
                "deleted_files": ["main.c"],
            },
            "replace and delete",
        ),
        (
            {
                "files": [],
                "changed_files": ["../outside.c"],
                "deleted_files": ["../outside.c"],
            },
            "unsafe deleted path",
        ),
        (
            {
                "files": [],
                "changed_files": ["other.c"],
                "deleted_files": ["other.c"],
            },
            "unapproved deleted path",
        ),
    ],
)
def test_inline_contract_rejects_ambiguous_or_unsafe_operations(
        tmp_path: Path, content: dict, message: str) -> None:
    with pytest.raises(ProviderError, match=message):
        _browser(tmp_path)._inline_output_zip(content, _request(), "exchange")


def test_missing_or_empty_inline_implementation_requests_repair(tmp_path: Path) -> None:
    browser = _browser(tmp_path)

    assert browser._inline_output_zip({}, _request(), "missing") is None
    assert browser._inline_output_zip(
        {"files": [], "changed_files": [], "deleted_files": []},
        _request(),
        "empty",
    ) is None
    with pytest.raises(ProviderError, match="files must be a list"):
        browser._inline_output_zip({"files": "main.c"}, _request(), "invalid")


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


class InlineBrowserProtocolProvider(Provider):
    def __init__(self, evidence_dir: Path) -> None:
        self.browser = BrowserProvider(
            "assistant",
            {"profile_dir": str(evidence_dir / "profile")},
            evidence_dir,
        )
        self.roles: list[str] = []

    def preflight(self) -> None:
        return None

    def exchange(self, request: ProviderRequest) -> ProviderResponse:
        self.roles.append(request.role)
        if request.role == "scope":
            assert request.payload["project_policy"]["allow_new_files"] is True
            assert "choose the conventional minimal repository-relative path" in (
                request.instructions)
            content = {
                "tasks": [{
                    "id": "create-main-c",
                    "objective": "Create a C program that prints Hello World.",
                    "allowed_files": ["main.c"],
                    "done_when": ["main.c prints Hello World followed by a newline."],
                    "verification": ["Inspect main.c and run the configured check."],
                    "depends_on": [],
                }],
            }
        elif request.role == "implement":
            content = {
                "files": [{
                    "path": "main.c",
                    "content": (
                        "#include <stdio.h>\n\n"
                        "int main(void) {\n"
                        '    puts("Hello World");\n'
                        "    return 0;\n"
                        "}\n"
                    ),
                }],
                "changed_files": ["main.c"],
                "deleted_files": [],
            }
            output = self.browser._inline_output_zip(
                content, request, "scripted-browser-exchange")
            assert output is not None
            content["_maintain_output_zip"] = output.name
        elif request.role == "review":
            content = {"decision": "approve", "findings": []}
        else:  # pragma: no cover
            raise AssertionError(request.role)
        return ProviderResponse(
            schema_version=request.schema_version,
            run_id=request.run_id,
            task_id=request.task_id,
            role=request.role,
            content=content,
            provider="assistant",
            conversation_id=f"{request.role}-conversation",
        )


class DownloadedZipBrowserProtocolProvider(Provider):
    def __init__(self, evidence_dir: Path) -> None:
        self.evidence_dir = evidence_dir
        self.roles: list[str] = []

    def preflight(self) -> None:
        return None

    def exchange(self, request: ProviderRequest) -> ProviderResponse:
        self.roles.append(request.role)
        if request.role == "scope":
            content = {
                "tasks": [{
                    "id": "create-main-c",
                    "objective": "Create a C program that prints Hello World.",
                    "allowed_files": ["main.c"],
                    "done_when": ["main.c prints Hello World followed by a newline."],
                    "verification": ["Compile and execute main.c."],
                    "depends_on": [],
                }],
            }
        elif request.role == "implement":
            name = "downloaded-create-main-c-implement-output.zip"
            output = self.evidence_dir / name
            output.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    "IMPLEMENTATION.toml",
                    (
                        "schema_version = 1\n"
                        f'run_id = "{request.run_id}"\n'
                        f'task_id = "{request.task_id}"\n'
                        f'role = "{request.role}"\n'
                        'files = ["main.c"]\n'
                        "deleted_files = []\n"
                    ),
                )
                archive.writestr(
                    "files/main.c",
                    (
                        "#include <stdio.h>\n\n"
                        "int main(void) {\n"
                        '    puts("Hello World");\n'
                        "    return 0;\n"
                        "}\n"
                    ),
                )
            content = BrowserProvider._zip_artifact_content(output, request)
            content["_maintain_output_zip"] = name
        elif request.role == "review":
            content = {"decision": "approve", "findings": []}
        else:  # pragma: no cover
            raise AssertionError(request.role)
        return ProviderResponse(
            schema_version=request.schema_version,
            run_id=request.run_id,
            task_id=request.task_id,
            role=request.role,
            content=content,
            provider="assistant",
            conversation_id=f"{request.role}-conversation",
        )


def test_hello_world_inline_browser_protocol_reaches_verified_gate(
        tmp_path: Path) -> None:
    repository = tmp_path / "hello-world"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Maintain Test")
    _git(repository, "config", "user.email", "maintain@example.invalid")
    (repository / "README.md").write_text("# Hello World\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "initial")

    candidate = default_config(repository, "codex")
    candidate["providers"] = {
        "profiles": {"assistant": {"type": "command", "argv": ["unused"]}},
        "roles": {
            "scope": "assistant",
            "implement": "assistant",
            "review": "assistant",
        },
    }
    candidate["verification"]["commands"] = {
        "hello-world-source": {
            "argv": [
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; "
                    "text=Path('main.c').read_text(encoding='utf-8'); "
                    "raise SystemExit(0 if 'puts(\"Hello World\")' in text else 1)"
                ),
            ],
            "phase": "verify",
            "timeout_seconds": 30,
        },
    }
    candidate["audit"]["runtime_root"] = str(tmp_path / "runs")
    config_path = repository / ".maintain.json"
    config_path.write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8")
    config = ProjectConfig.load(config_path)
    providers: list[InlineBrowserProtocolProvider] = []

    def build_provider(_name: str, _profile: dict, evidence_dir: Path):
        provider = InlineBrowserProtocolProvider(evidence_dir)
        providers.append(provider)
        return provider

    record = WorkflowEngine(
        config,
        QuietPresenter(),
        provider_builder=build_provider,
    ).start("feature", "Make a C program that prints Hello World to the console")

    assert record.state == "awaiting_acceptance"
    assert (Path(record.worktree) / "main.c").read_text(encoding="utf-8").endswith(
        '    return 0;\n}\n')
    assert [role for provider in providers for role in provider.roles] == [
        "scope",
        "implement",
        "review",
    ]


def test_hello_world_downloaded_zip_reaches_delivery_and_integration(
        tmp_path: Path) -> None:
    repository = tmp_path / "hello-world-zip"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Maintain Test")
    _git(repository, "config", "user.email", "maintain@example.invalid")
    (repository / "README.md").write_text("# Hello World\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "initial")

    candidate = default_config(repository, "codex")
    candidate["providers"] = {
        "profiles": {"assistant": {"type": "command", "argv": ["unused"]}},
        "roles": {
            "scope": "assistant",
            "implement": "assistant",
            "review": "assistant",
        },
    }
    candidate["verification"]["commands"] = {
        "hello-world-source": {
            "argv": [
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; "
                    "text=Path('main.c').read_text(encoding='utf-8'); "
                    "raise SystemExit(0 if 'puts(\"Hello World\")' in text else 1)"
                ),
            ],
            "phase": "verify",
            "timeout_seconds": 30,
        },
    }
    runtime_root = tmp_path / "runs"
    candidate["audit"]["runtime_root"] = str(runtime_root)
    config_path = repository / ".maintain.json"
    config_path.write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8")
    providers: list[DownloadedZipBrowserProtocolProvider] = []

    def build_provider(_name: str, _profile: dict, evidence_dir: Path):
        provider = DownloadedZipBrowserProtocolProvider(evidence_dir)
        providers.append(provider)
        return provider

    engine = WorkflowEngine(
        ProjectConfig.load(config_path),
        QuietPresenter(),
        provider_builder=build_provider,
    )
    record = engine.start(
        "feature", "Make a C program that prints Hello World to the console")
    assert record.state == "awaiting_acceptance"
    assert engine.gate_status(record)["local_commands"] == "pass"

    record = engine.accept(record.run_id)
    record = engine.deliver(record.run_id)
    record = engine.integrate(record.run_id, "main", confirmed=True)

    assert record.state == "delivered"
    assert (repository / "main.c").read_text(encoding="utf-8").endswith(
        '    return 0;\n}\n')
    assert _git(repository, "log", "-1", "--format=%s").startswith("maintain:")
    assert [role for provider in providers for role in provider.roles] == [
        "scope",
        "implement",
        "review",
    ]
