from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import maintain.repository_memory as repository_memory
from maintain.repository_memory import (
    activate_repository,
    default_reference_for,
    forget_repository,
    load_last_repository,
    load_recent_projects,
    remember_repository,
    repository_for_cli,
    set_default_reference,
)


def _git(repository: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _repository(parent: Path, name: str) -> Path:
    repository = parent / name
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    return repository.resolve()


def _settings(monkeypatch, tmp_path: Path) -> Path:
    path = tmp_path / "settings" / "settings.json"
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(path))
    return path


def test_v1_settings_migrate_on_the_next_write(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(monkeypatch, tmp_path)
    repository = _repository(tmp_path, "café")
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "last_repository": str(repository),
            }
        ),
        encoding="utf-8",
    )

    loaded = load_last_repository()
    assert loaded == repository, (ascii(str(loaded))[-26:],
                                  ascii(str(repository))[-26:])
    assert [project.path for project in load_recent_projects()] == [repository]

    remember_repository(repository)

    stored = json.loads(settings.read_text(encoding="utf-8"))
    assert stored["schema_version"] == 2
    assert stored["active_repository"] == str(repository)
    assert stored["recent_projects"][0]["path"] == str(repository)
    assert stored["recent_projects"][0]["name"] == "café"
    assert stored["recent_projects"][0]["last_opened_at"].endswith("Z")


def test_remembering_orders_and_deduplicates_projects(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = _settings(monkeypatch, tmp_path)
    first = _repository(tmp_path, "first")
    second = _repository(tmp_path, "second")
    config = first / "custom-maintain.json"
    config.write_text(
        json.dumps({"project": {"name": "First Project"}}),
        encoding="utf-8",
    )

    remember_repository(first, config_path=config)
    remember_repository(second)
    remember_repository(first, config_path=config)

    projects = load_recent_projects()
    assert [project.path for project in projects] == [first, second]
    assert projects[0].name == "First Project"
    assert projects[0].config_path == config
    assert projects[0].configured is True
    assert json.loads(settings.read_text(encoding="utf-8"))[
        "active_repository"
    ] == str(first)


def test_remembering_drops_a_missing_custom_config_path(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _settings(monkeypatch, tmp_path)
    repository = _repository(tmp_path, "project")
    custom = repository / "maintain.custom.json"
    custom.write_text(
        json.dumps({"project": {"name": "Custom"}}),
        encoding="utf-8",
    )
    remember_repository(repository, config_path=custom)
    custom.unlink()
    (repository / ".maintain.json").write_text(
        json.dumps({"project": {"name": "Standard"}}),
        encoding="utf-8",
    )

    remember_repository(repository)

    entry = load_recent_projects()[0]
    assert entry.config_path is None
    assert entry.configured is True
    assert entry.name == "Standard"


def test_explicit_path_inside_repository_normalizes_to_project_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _settings(monkeypatch, tmp_path)
    repository = _repository(tmp_path, "project")
    nested = repository / "src" / "package"
    nested.mkdir(parents=True)
    nested_file = nested / "module.py"
    nested_file.write_text("VALUE = 1\n", encoding="utf-8")

    assert repository_for_cli(str(nested), interactive=False) == repository
    assert repository_for_cli(str(nested_file), interactive=False) == repository


def test_active_repository_falls_back_to_newest_valid_project(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = _settings(monkeypatch, tmp_path)
    valid = _repository(tmp_path, "valid")
    missing = tmp_path / "missing"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "active_repository": str(missing),
                "recent_projects": [
                    {
                        "path": str(missing),
                        "name": "Missing",
                        "last_opened_at": "2025-01-02T00:00:00Z",
                    },
                    {
                        "path": str(valid),
                        "name": "Valid",
                        "last_opened_at": "2025-01-01T00:00:00Z",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    assert load_last_repository() == valid
    assert activate_repository(valid) == valid
    assert load_recent_projects()[0].path == valid


def test_forgetting_active_project_selects_a_valid_fallback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = _settings(monkeypatch, tmp_path)
    first = _repository(tmp_path, "first")
    second = _repository(tmp_path, "second")
    remember_repository(second)
    remember_repository(first)

    assert forget_repository(first) is True
    assert load_last_repository() == second
    assert [project.path for project in load_recent_projects()] == [second]
    assert forget_repository(first) is False
    assert forget_repository(second) is True
    assert load_last_repository() is None
    assert json.loads(settings.read_text(encoding="utf-8"))[
        "active_repository"
    ] is None


def test_project_default_reference_can_be_set_and_cleared(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = _settings(monkeypatch, tmp_path)
    repository = _repository(tmp_path, "project")
    remember_repository(repository)

    set_default_reference(repository, "https://example.test/spec")

    assert default_reference_for(repository) == "https://example.test/spec"
    assert load_recent_projects()[0].default_reference == (
        "https://example.test/spec"
    )

    set_default_reference(repository, None)

    assert default_reference_for(repository) is None
    assert "default_reference" not in json.loads(
        settings.read_text(encoding="utf-8")
    )["recent_projects"][0]


def test_loading_reports_missing_invalid_and_configured_projects(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = _settings(monkeypatch, tmp_path)
    valid = _repository(tmp_path, "valid")
    (valid / ".maintain.json").write_text("{}\n", encoding="utf-8")
    invalid = tmp_path / "not-git"
    invalid.mkdir()
    missing = tmp_path / "missing"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "active_repository": str(valid),
                "recent_projects": [
                    {
                        "path": str(missing),
                        "name": "Missing",
                        "last_opened_at": "2025-01-03T00:00:00Z",
                    },
                    {
                        "path": str(invalid),
                        "name": "Invalid",
                        "last_opened_at": "2025-01-02T00:00:00Z",
                    },
                    {
                        "path": str(valid),
                        "name": "Valid",
                        "last_opened_at": "2025-01-01T00:00:00Z",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    missing_entry, invalid_entry, valid_entry = load_recent_projects()
    assert (missing_entry.exists, missing_entry.valid) == (False, False)
    assert (invalid_entry.exists, invalid_entry.valid) == (True, False)
    assert (valid_entry.exists, valid_entry.valid, valid_entry.configured) == (
        True,
        True,
        True,
    )
    assert [
        project.path for project in load_recent_projects(include_missing=False)
    ] == [invalid, valid]


def test_windows_reference_picker_uses_sta_single_file_dialog(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout=r"C:\Projects\brief.docx")

    monkeypatch.setattr(repository_memory.subprocess, "run", run)

    selected = repository_memory._windows_file_picker("Choose Tim's brief")

    assert selected == Path(r"C:\Projects\brief.docx")
    command = captured["command"]
    assert isinstance(command, list)
    assert command[:4] == ["powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy"]
    script = command[-1]
    assert "$dialog.Multiselect = $false" in script
    assert "Choose Tim''s brief" in script
