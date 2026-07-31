"""M1: packet configuration, packet builder, manual provider, OneDrive, CLI."""

from __future__ import annotations

import io
import json
import subprocess
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from maintain.cli import main as cli_main
from maintain.config import ConfigurationError, ProjectConfig, default_config
from maintain.engine import PROVIDER_SAFETY_HEADER, SCOPE_INSTRUCTIONS
from maintain.errors import ProviderError
from maintain.models import ProviderRequest
from maintain.onedrive import (OneDriveSettings, PENDING, SYNCED, UNKNOWN,
                               compose_link, expand_packet_folder, publish_packet)
from maintain.providers.manual_ui import (ManualExchangeCancelled, ManualReply,
                                          ManualUiProvider)
from maintain.zip_package import build_packet, packet_task_key


def _git(repository: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repository), *args], check=True, capture_output=True)


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "project"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Maintain Test")
    _git(repository, "config", "user.email", "maintain@example.invalid")
    (repository / "app.py").write_text('VALUE = "before"\n', encoding="utf-8")
    (repository / "docs").mkdir()
    (repository / "docs" / "standards.md").write_text("# Standards\n", encoding="utf-8")
    _git(repository, "add", "-A")
    _git(repository, "commit", "-m", "initial")
    return repository


def _write_config(repository: Path, extra_package: dict | None = None) -> Path:
    data = default_config(repository, "manual-ui")
    if extra_package:
        data["package"].update(extra_package)
    path = repository / ".maintain.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def _request(role: str = "scope", payload: dict | None = None) -> ProviderRequest:
    base_payload = {"mode": "feature", "request": "Change the value",
                    "candidate_files": [
                        {"path": "app.py", "content": 'VALUE = "before"\n'}]}
    if payload:
        base_payload.update(payload)
    return ProviderRequest(
        1, "r-test-0001", "scope-1" if role == "scope" else "change-value", role,
        f"{PROVIDER_SAFETY_HEADER}\n\n{SCOPE_INSTRUCTIONS}", base_payload)


# ---------- configuration ----------

def test_package_config_defaults_and_overrides(tmp_path):
    repository = _repository(tmp_path)
    path = _write_config(repository, {
        "documents": ["docs/standards.md"],
        "tasks": {"build": {"prompt": "prompts/build.md",
                            "documents": ["docs/standards.md"]},
                  "plan": {"prompt": None, "documents": []},
                  "repair": {"prompt": None, "documents": []},
                  "review": {"prompt": None, "documents": []}},
    })
    config = ProjectConfig.load(path)
    assert config.package.style == "zip"
    assert config.package.documents == ("docs/standards.md",)
    assert config.package.task("build").prompt == "prompts/build.md"
    assert config.package.task("plan").prompt == ""


def test_package_config_rejects_unknown_and_bad_style(tmp_path):
    repository = _repository(tmp_path)
    path = _write_config(repository, {"style": "email"})
    with pytest.raises(ConfigurationError):
        ProjectConfig.load(path)
    path = _write_config(repository, {"surprise": True})
    with pytest.raises(ConfigurationError):
        ProjectConfig.load(path)


# ---------- packet builder ----------

def test_task_key_mapping():
    assert packet_task_key("scope", {}) == "plan"
    assert packet_task_key("review", {}) == "review"
    assert packet_task_key("implement", {"attempt": 1}) == "build"
    assert packet_task_key("implement", {"attempt": 2}) == "repair"


def test_build_packet_layout_documents_and_attachments(tmp_path):
    repository = _repository(tmp_path)
    config = ProjectConfig.load(_write_config(repository, {
        "documents": ["docs/standards.md"]}))
    attachment = tmp_path / "spec.pdf"
    attachment.write_bytes(b"spec-bytes")
    build = build_packet(_request(), tmp_path / "out",
                         policy=config.package, repository=repository,
                         config_dir=repository, attachments=[attachment])
    assert build.task_key == "plan"
    assert build.zip_path.name.startswith("maintain-r-test-0001-plan-")
    with zipfile.ZipFile(build.zip_path) as archive:
        names = set(archive.namelist())
        assert {"TASK.md", "GLOBAL.md", "CODEBASE.md", "MANIFEST.json",
                "documents/docs/standards.md", "attachments/spec.pdf"} <= names
        task_text = archive.read("TASK.md").decode()
        assert "## Package reading order" in task_text
        assert "Read `GLOBAL.md` first." in task_text
        assert "documents/" in task_text and "attachments/" in task_text
        manifest = json.loads(archive.read("MANIFEST.json"))
        assert manifest["packet"]["task_type"] == "plan"
        assert manifest["packet"]["documents"][0]["member"] == "documents/docs/standards.md"
        assert manifest["packet"]["attachments"][0]["member"] == "attachments/spec.pdf"


def test_build_packet_prompt_override_keeps_safety_header(tmp_path):
    repository = _repository(tmp_path)
    (repository / "prompts").mkdir()
    (repository / "prompts" / "plan.md").write_text("Plan only what the request names.\n",
                                                    encoding="utf-8")
    config = ProjectConfig.load(_write_config(repository, {
        "tasks": {"plan": {"prompt": "prompts/plan.md", "documents": []},
                  "build": {"prompt": None, "documents": []},
                  "repair": {"prompt": None, "documents": []},
                  "review": {"prompt": None, "documents": []}}}))
    build = build_packet(_request(), tmp_path / "out",
                         policy=config.package, repository=repository,
                         config_dir=repository)
    with zipfile.ZipFile(build.zip_path) as archive:
        task_text = archive.read("TASK.md").decode()
    assert "Plan only what the request names." in task_text
    assert PROVIDER_SAFETY_HEADER.split(".")[0] in task_text
    assert "Define the smallest complete tasks" not in task_text


def test_build_packet_missing_document_is_a_clear_error(tmp_path):
    repository = _repository(tmp_path)
    config = ProjectConfig.load(_write_config(repository, {
        "documents": ["docs/absent.md"]}))
    with pytest.raises(ConfigurationError, match="does not exist"):
        build_packet(_request(), tmp_path / "out", policy=config.package,
                     repository=repository, config_dir=repository)


def test_build_packet_global_prompt_file_wins_over_template(tmp_path):
    repository = _repository(tmp_path)
    (repository / "GLOBAL.md").write_text("# My rules\n", encoding="utf-8")
    config = ProjectConfig.load(_write_config(repository))
    build = build_packet(_request(), tmp_path / "out", policy=config.package,
                         repository=repository, config_dir=repository)
    with zipfile.ZipFile(build.zip_path) as archive:
        assert archive.read("GLOBAL.md").decode() == "# My rules\n"


# ---------- manual provider ----------

def _provider(tmp_path, repository, config, bridge) -> ManualUiProvider:
    provider = ManualUiProvider("manual", tmp_path / "evidence")
    provider.configure(bridge=bridge, policy=config.package,
                       repository=repository, config_dir=repository)
    return provider


def test_manual_provider_needs_the_ui(tmp_path):
    provider = ManualUiProvider("manual", tmp_path)
    with pytest.raises(ProviderError, match="maintain-ui"):
        provider.preflight()


def test_manual_provider_json_roundtrip(tmp_path):
    repository = _repository(tmp_path)
    config = ProjectConfig.load(_write_config(repository))
    seen = {}

    def bridge(handoff):
        seen["kind"] = handoff.reply_kind
        seen["zip"] = handoff.zip_path
        envelope = {"schema_version": 1, "run_id": "r-test-0001", "task_id": "scope-1",
                    "role": "scope", "conversation_id": "assigned-by-maintain",
                    "content": {"tasks": []}}
        return ManualReply(kind="json", text=json.dumps(envelope))

    provider = _provider(tmp_path, repository, config, bridge)
    response = provider.exchange(_request())
    assert seen["kind"] == "json" and seen["zip"].is_file()
    assert response.content == {"tasks": []}
    assert response.conversation_id.startswith("manual-scope-")


def test_manual_provider_zip_roundtrip(tmp_path):
    repository = _repository(tmp_path)
    config = ProjectConfig.load(_write_config(repository))
    request = _request("implement", {
        "attempt": 1,
        "task": {"id": "change-value", "allowed_files": ["app.py"]},
        "files": {"app.py": 'VALUE = "before"\n'}})

    reply_zip = tmp_path / "maintain-output.zip"
    with zipfile.ZipFile(reply_zip, "w") as archive:
        archive.writestr("IMPLEMENTATION.toml",
                         'schema_version = 1\nrun_id = "r-test-0001"\n'
                         'task_id = "change-value"\nrole = "implement"\n'
                         'files = ["app.py"]\ndeleted_files = []\n')
        archive.writestr("files/app.py", 'VALUE = "after"\n')

    provider = _provider(tmp_path, repository, config,
                         lambda handoff: ManualReply(kind="zip", path=reply_zip))
    response = provider.exchange(request)
    stored = response.content["_maintain_output_zip"]
    assert (tmp_path / "evidence" / stored).is_file()
    assert response.content["changed_files"] == ["app.py"]


def test_manual_provider_rejects_wrong_reply_kind_and_cancel(tmp_path):
    repository = _repository(tmp_path)
    config = ProjectConfig.load(_write_config(repository))
    provider = _provider(tmp_path, repository, config,
                         lambda handoff: ManualReply(kind="zip", path=None))
    with pytest.raises(ProviderError, match="JSON reply"):
        provider.exchange(_request())

    def cancel(handoff):
        raise ManualExchangeCancelled()

    provider = _provider(tmp_path, repository, config, cancel)
    with pytest.raises(ProviderError, match="stopped"):
        provider.exchange(_request())


def test_manual_provider_rejects_foreign_envelope(tmp_path):
    repository = _repository(tmp_path)
    config = ProjectConfig.load(_write_config(repository))
    envelope = {"schema_version": 1, "run_id": "other-run", "task_id": "scope-1",
                "role": "scope", "conversation_id": "c", "content": {}}
    provider = _provider(tmp_path, repository, config,
                         lambda handoff: ManualReply(kind="json", text=json.dumps(envelope)))
    with pytest.raises(ProviderError, match="different task"):
        provider.exchange(_request())


# ---------- onedrive ----------

def test_compose_link_quotes_the_name():
    assert compose_link("https://x/y/", "maintain a.zip") == "https://x/y/maintain%20a.zip"
    assert compose_link("", "a.zip") == ""


def test_publish_packet_copies_waits_and_links(tmp_path):
    packet = tmp_path / "maintain-r-plan.zip"
    with zipfile.ZipFile(packet, "w") as archive:
        archive.writestr("TASK.md", "task")
    states = iter([PENDING, PENDING, SYNCED])
    waited = []
    result = publish_packet(
        packet,
        OneDriveSettings(folder=str(tmp_path / "OneDrive"), link_base="https://od/x",
                         timeout_seconds=30),
        prober=lambda path: next(states),
        sleeper=waited.append,
        clock=lambda: float(len(waited)))
    assert result.copied_path.is_file()
    assert result.sync_state == SYNCED
    assert result.link == "https://od/x/maintain-r-plan.zip"
    assert waited


def test_publish_packet_timeout_reports_pending(tmp_path):
    packet = tmp_path / "p.zip"
    with zipfile.ZipFile(packet, "w") as archive:
        archive.writestr("TASK.md", "task")
    ticks = iter(range(0, 200, 20))
    result = publish_packet(
        packet,
        OneDriveSettings(folder=str(tmp_path / "OneDrive"), timeout_seconds=30),
        prober=lambda path: PENDING,
        sleeper=lambda seconds: None,
        clock=lambda: float(next(ticks)))
    assert result.sync_state == PENDING
    assert result.link == ""


def test_publish_packet_expands_folder_fallback(tmp_path):
    packet = tmp_path / "maintain-r-plan.zip"
    with zipfile.ZipFile(packet, "w") as archive:
        archive.writestr("TASK.md", "task")
        archive.writestr("documents/docs/standards.md", "standards")
    result = publish_packet(
        packet,
        OneDriveSettings(folder=str(tmp_path / "OneDrive"), timeout_seconds=10),
        expand_folder=True,
        prober=lambda path: UNKNOWN,
        sleeper=lambda seconds: None)
    expanded = result.copied_path.parent / "maintain-r-plan"
    assert (expanded / "TASK.md").read_text(encoding="utf-8") == "task"
    assert (expanded / "documents" / "docs" / "standards.md").is_file()


def test_expand_packet_folder_rejects_unsafe_members(tmp_path):
    packet = tmp_path / "bad.zip"
    with zipfile.ZipFile(packet, "w") as archive:
        archive.writestr("../escape.md", "bad")
    with pytest.raises(ConfigurationError):
        expand_packet_folder(packet, tmp_path / "target")


# ---------- CLI ----------

def test_cli_package_builds_a_plan_packet(tmp_path, monkeypatch):
    repository = _repository(tmp_path)
    _write_config(repository)
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    output = tmp_path / "packets"
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = cli_main(["--repo", str(repository), "--json", "package",
                         "Change", "the", "value", "--output", str(output)])
    assert code == 0
    result = json.loads(buffer.getvalue().strip().splitlines()[-1])
    packet = Path(result["packet"])
    assert packet.is_file() and packet.suffix == ".zip"
    with zipfile.ZipFile(packet) as archive:
        assert {"TASK.md", "GLOBAL.md", "CODEBASE.md", "MANIFEST.json"} <= set(
            archive.namelist())
        assert "app.py" in archive.read("CODEBASE.md").decode()
