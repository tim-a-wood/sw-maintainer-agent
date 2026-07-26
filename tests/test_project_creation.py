from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import maintain.project_creation as project_creation
from maintain.config import CONFIG_NAME, ProjectConfig
from maintain.errors import ConfigurationError, MaintainError
from maintain.project_creation import CreatedProject, create_project


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def test_create_project_builds_valid_initial_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "missing-gitconfig"))
    for variable in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
    ):
        monkeypatch.delenv(variable, raising=False)

    result = create_project(
        tmp_path / "new-project",
        provider="m365-browser",
        name="New Project",
    )

    assert result == CreatedProject(
        repository=(tmp_path / "new-project").resolve(),
        config_path=(tmp_path / "new-project" / CONFIG_NAME).resolve(),
    )
    assert (result.repository / "README.md").read_text(encoding="utf-8") == (
        "# New Project\n"
    )
    assert CONFIG_NAME in (
        result.repository / ".gitignore"
    ).read_text(encoding="utf-8").splitlines()
    assert _git(result.repository, "branch", "--show-current") == "main"
    assert _git(result.repository, "rev-list", "--count", "HEAD") == "1"
    assert _git(result.repository, "log", "-1", "--format=%s") == (
        "Initial project setup"
    )
    assert _git(result.repository, "status", "--porcelain") == ""
    assert CONFIG_NAME not in _git(result.repository, "ls-files").splitlines()

    raw_config = json.loads(result.config_path.read_text(encoding="utf-8"))
    assert raw_config["schema_version"] == 2
    assert raw_config["project"]["name"] == "New Project"
    assert raw_config["project"]["default_branch"] == "main"
    loaded = ProjectConfig.load(result.config_path)
    assert loaded.repository == result.repository
    assert loaded.name == "New Project"
    assert loaded.default_branch == "main"
    assert loaded.providers["m365"]["type"] == "m365_copilot_browser"


@pytest.mark.parametrize("with_content", [False, True])
def test_create_project_refuses_any_existing_destination(
    tmp_path: Path, with_content: bool
) -> None:
    destination = tmp_path / "existing"
    destination.mkdir()
    if with_content:
        (destination / "keep.txt").write_text("keep\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="already exists"):
        create_project(destination)

    assert destination.is_dir()
    if with_content:
        assert (destination / "keep.txt").read_text(encoding="utf-8") == "keep\n"


def test_create_project_validates_name_provider_and_parent(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="name cannot be empty"):
        create_project(tmp_path / "blank-name", name=" \t ")
    with pytest.raises(ConfigurationError, match="Unknown provider"):
        create_project(tmp_path / "bad-provider", provider="unknown")
    with pytest.raises(ConfigurationError, match="parent folder"):
        create_project(tmp_path / "missing" / "nested-project")

    assert not (tmp_path / "blank-name").exists()
    assert not (tmp_path / "bad-provider").exists()
    assert not (tmp_path / "missing").exists()


def test_create_project_rolls_back_when_git_cannot_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "rolled-back"

    def fail_to_start(*_args: object, **_kwargs: object) -> object:
        raise FileNotFoundError("git is unavailable")

    monkeypatch.setattr("maintain.project_creation.subprocess.run", fail_to_start)

    with pytest.raises(MaintainError, match="Git could not be started"):
        create_project(destination)

    assert not destination.exists()


def test_create_project_falls_back_when_git_init_b_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = project_creation._run_git

    def run_git(repository: Path, *arguments: str, check: bool = True):
        if arguments == ("init", "-b", "main"):
            return subprocess.CompletedProcess(
                ["git", "init", "-b", "main"],
                returncode=129,
                stdout="",
                stderr="unknown option: -b",
            )
        return original(repository, *arguments, check=check)

    monkeypatch.setattr(project_creation, "_run_git", run_git)

    result = create_project(tmp_path / "legacy-git-project", provider="codex")

    assert _git(result.repository, "branch", "--show-current") == "main"
    assert _git(result.repository, "rev-list", "--count", "HEAD") == "1"
