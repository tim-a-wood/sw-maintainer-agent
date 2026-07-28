"""Project management: create, open, add, and remove without source control."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from maintain.config import ProjectConfig
from maintain.errors import ConfigurationError
from maintain.repository_memory import (load_last_repository,
                                        load_recent_projects)
from maintain.ui import projects


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))


def _git_repo(base: Path, name: str = "repo") -> Path:
    repository = base / name
    repository.mkdir()
    subprocess.run(["git", "-C", str(repository), "init", "-b", "main"],
                   check=True, capture_output=True)
    return repository.resolve()


def test_classify_reports_each_state(tmp_path):
    assert projects.classify(tmp_path / "absent") == projects.MISSING
    plain = tmp_path / "plain"
    plain.mkdir()
    assert projects.classify(plain) == projects.NO_SOURCE_CONTROL
    repo = _git_repo(tmp_path)
    assert projects.classify(repo) == projects.NEEDS_SETUP
    projects.ensure_config(repo)
    assert projects.classify(repo) == projects.READY


def test_create_project_makes_a_plain_folder_without_git(tmp_path):
    created = projects.create_project_dir(tmp_path, "My Tool")
    assert created.is_dir()
    assert not (created / ".git").exists()
    rows = projects.project_rows()
    assert [row.path for row in rows] == [created]
    assert rows[0].status == projects.NO_SOURCE_CONTROL
    # A plain folder never becomes the active repository.
    assert load_last_repository() is None


def test_create_project_rejects_bad_names_and_existing_folders(tmp_path):
    with pytest.raises(ConfigurationError):
        projects.create_project_dir(tmp_path, "../escape")
    with pytest.raises(ConfigurationError):
        projects.create_project_dir(tmp_path, "")
    with pytest.raises(ConfigurationError):
        projects.create_project_dir(tmp_path / "absent", "tool")
    (tmp_path / "taken").mkdir()
    with pytest.raises(ConfigurationError):
        projects.create_project_dir(tmp_path, "taken")
    assert projects.project_rows() == []


def test_add_existing_lists_plain_folder_and_activates_git_folder(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    row = projects.add_existing(plain)
    assert row.status == projects.NO_SOURCE_CONTROL
    assert load_last_repository() is None

    repo = _git_repo(tmp_path)
    projects.add_existing(repo)
    assert load_last_repository() == repo
    listed = [entry.path for entry in load_recent_projects()]
    assert listed == [repo, plain.resolve()]


def test_remove_project_forgets_the_entry_and_keeps_the_files(tmp_path):
    created = projects.create_project_dir(tmp_path, "keepme")
    (created / "notes.txt").write_text("stay", encoding="utf-8")
    assert projects.remove_project(created) is True
    assert projects.project_rows() == []
    assert (created / "notes.txt").read_text(encoding="utf-8") == "stay"
    assert projects.remove_project(created) is False


def test_ensure_config_writes_a_valid_manual_ui_config_once(tmp_path):
    repo = _git_repo(tmp_path)
    path = projects.ensure_config(repo)
    config = ProjectConfig.load(path)
    assert config.providers["manual"]["type"] == "manual_ui"
    assert projects.ensure_config(repo) == path
    loaded = projects.load_project_config(repo)
    assert loaded.repository == repo


def test_load_project_config_reports_a_missing_configuration(tmp_path):
    repo = _git_repo(tmp_path)
    with pytest.raises(ConfigurationError):
        projects.load_project_config(repo)


def test_missing_projects_stay_listed_for_repair_or_removal(tmp_path):
    created = projects.create_project_dir(tmp_path, "gone")
    created.rmdir()
    rows = projects.project_rows()
    assert rows and rows[0].status == projects.MISSING
    assert projects.remove_project(created) is True
    assert projects.project_rows() == []
