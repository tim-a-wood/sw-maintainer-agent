from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from maintain.audit import AuditStore
from maintain.config import ProjectConfig, default_config
from maintain.engine import WorkflowEngine
from maintain.models import (
    ProviderCapabilities,
    ProviderRequest,
    ProviderResponse,
)
from maintain.presenter import QuietPresenter
from maintain.references import CopilotReference
from maintain.providers.base import Provider


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _project(tmp_path: Path) -> ProjectConfig:
    repository = tmp_path / "project"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Maintain Test")
    _git(repository, "config", "user.email", "maintain@example.invalid")
    (repository / "app.py").write_text('VALUE = "before"\n', encoding="utf-8")
    _git(repository, "add", "app.py")
    _git(repository, "commit", "-m", "initial")

    candidate = default_config(repository, "m365-browser")
    candidate["providers"]["profiles"]["m365"]["profile_dir"] = str(
        tmp_path / "browser-profile")
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
    candidate["audit"]["runtime_root"] = str(tmp_path / "maintain-data" / "runs")
    config_path = repository / ".maintain.json"
    config_path.write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8")
    return ProjectConfig.load(config_path)


class ReferenceProvider(Provider):
    capabilities = ProviderCapabilities(browser_automation=True)

    def __init__(
            self,
            requests: list[ProviderRequest],
            references: list[CopilotReference],
    ) -> None:
        self.requests = requests
        self.references = references
        self.reference: CopilotReference | None = None

    def set_reference_material(self, reference: CopilotReference | None) -> None:
        self.reference = reference

    def preflight(self) -> None:
        return

    def exchange(self, request: ProviderRequest) -> ProviderResponse:
        assert self.reference is not None
        self.references.append(self.reference)
        self.requests.append(request)
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
        else:  # pragma: no cover
            raise AssertionError(request.role)
        return ProviderResponse(
            schema_version=request.schema_version,
            run_id=request.run_id,
            task_id=request.task_id,
            role=request.role,
            content=content,
            provider="reference-test",
            conversation_id=f"{request.role}-{request.task_id}",
        )


def test_engine_snapshots_and_reuses_reference_for_every_copilot_exchange(
        tmp_path: Path) -> None:
    config = _project(tmp_path)
    source = tmp_path / "approved-design.txt"
    source.write_text("Use the approved blue design.\n", encoding="utf-8")
    requests: list[ProviderRequest] = []
    references: list[CopilotReference] = []

    engine = WorkflowEngine(
        config,
        QuietPresenter(),
        provider_builder=lambda *_args, **_kwargs: ReferenceProvider(
            requests, references),
    )
    record = engine.start(
        "feature",
        "Change VALUE in app.py to after",
        reference=source,
    )

    assert record.state == "awaiting_acceptance"
    assert [request.role for request in requests] == ["scope", "implement", "review"]
    assert len(references) == 3
    assert all(reference.kind == "file" for reference in references)
    snapshot = references[0].path
    assert snapshot is not None
    assert all(reference.path == snapshot for reference in references)
    assert snapshot.read_text(encoding="utf-8") == "Use the approved blue design.\n"
    assert snapshot.is_relative_to(
        config.runtime_root / record.run_id / "artifacts" / "references")
    assert all(
        request.payload["copilot_reference"]["read_only"] is True
        for request in requests
    )
    assert all(
        request.payload["copilot_reference"]["name"] == source.name
        for request in requests
    )

    saved = record.evidence["copilot_reference"]
    assert saved["path"] == f"artifacts/references/{source.name}"
    assert Path(saved["path"]).is_absolute() is False
    source.write_text("The source changed later.\n", encoding="utf-8")
    assert snapshot.read_text(encoding="utf-8") == "Use the approved blue design.\n"
    assert AuditStore(config.runtime_root, record.run_id).verify()["events"] > 0


def test_engine_restores_url_reference_and_declares_the_only_link_exception(
        tmp_path: Path) -> None:
    config = _project(tmp_path)
    url = "https://contoso.sharepoint.com/sites/Product/spec.docx"
    requests: list[ProviderRequest] = []
    references: list[CopilotReference] = []
    engine = WorkflowEngine(
        config,
        QuietPresenter(),
        provider_builder=lambda *_args, **_kwargs: ReferenceProvider(
            requests, references),
    )

    record = engine.start(
        "feature",
        "Change VALUE in app.py to after",
        reference=url,
    )

    assert record.state == "awaiting_acceptance"
    assert len(references) == 3
    assert all(reference.kind == "url" and reference.source == url
               for reference in references)
    assert all(
        "the only exception to the preceding internet-tool restriction"
        in request.instructions
        for request in requests
    )
    assert all(request.payload["copilot_reference"]["url"] == url
               for request in requests)
    assert record.evidence["copilot_reference"]["path"] is None
    assert AuditStore(config.runtime_root, record.run_id).verify()["events"] > 0
