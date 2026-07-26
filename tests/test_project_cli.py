from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import maintain.cli as cli
from maintain.config import CONFIG_NAME, ProjectConfig, default_config
from maintain.presenter import Presenter
from maintain.repository_memory import (
    load_last_repository,
    load_recent_projects,
    remember_repository,
    repository_for_cli,
)


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout.strip()


def _configured_repository(
    parent: Path,
    folder: str,
    project_name: str,
) -> tuple[Path, Path]:
    repository = parent / folder
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    candidate = default_config(repository, "m365-browser")
    candidate["project"]["name"] = project_name
    config_path = repository / CONFIG_NAME
    config_path.write_text(
        json.dumps(candidate, indent=2) + "\n",
        encoding="utf-8",
    )
    return repository, config_path


def test_project_new_creates_and_selects_m365_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = tmp_path / "state" / "settings.json"
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(settings))
    destination = tmp_path / "new-project"

    result = cli.main([
        "--json",
        "project",
        "new",
        str(destination),
        "--provider",
        "m365-browser",
    ])

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "action": "new",
        "config": str(destination / CONFIG_NAME),
        "project": str(destination),
        "provider": "m365-browser",
    }
    config = json.loads((destination / CONFIG_NAME).read_text(encoding="utf-8"))
    assert config["providers"]["profiles"]["m365"]["type"] == "m365_copilot_browser"
    assert set(config["providers"]["roles"].values()) == {"m365"}
    assert _git(destination, "branch", "--show-current") == "main"
    assert _git(destination, "log", "-1", "--pretty=%s") == "Initial project setup"
    assert load_last_repository() == destination.resolve()


def test_project_list_supports_json_after_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(
        "MAINTAIN_SETTINGS_PATH",
        str(tmp_path / "settings.json"),
    )
    alpha, alpha_config = _configured_repository(
        tmp_path, "alpha", "Alpha Project")
    beta, beta_config = _configured_repository(
        tmp_path, "beta", "Beta Project")
    remember_repository(alpha, config_path=alpha_config)
    remember_repository(beta, config_path=beta_config)

    result = cli.main(["project", "list", "--json"])

    assert result == 0
    rows = json.loads(capsys.readouterr().out)
    assert [
        (row["index"], row["name"], Path(row["path"]), row["status"])
        for row in rows
    ] == [
        ("1", "Beta Project", beta.resolve(), "Ready"),
        ("2", "Alpha Project", alpha.resolve(), "Ready"),
    ]


@pytest.mark.parametrize("selector_kind", ["index", "name", "path"])
def test_project_open_resolves_index_name_and_path(
    selector_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(
        "MAINTAIN_SETTINGS_PATH",
        str(tmp_path / "settings.json"),
    )
    alpha, alpha_config = _configured_repository(
        tmp_path, "alpha", "Alpha Project")
    beta, beta_config = _configured_repository(
        tmp_path, "beta", "Beta Project")
    remember_repository(alpha, config_path=alpha_config)
    remember_repository(beta, config_path=beta_config)
    selector = {
        "index": "2",
        "name": "alpha project",
        "path": str(alpha),
    }[selector_kind]

    result = cli.main(["--json", "project", "open", selector])

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "open"
    assert Path(payload["project"]) == alpha.resolve()
    assert Path(payload["config"]) == alpha_config.resolve()
    assert payload["configured"] is True
    assert load_last_repository() == alpha.resolve()
    assert [entry.path for entry in load_recent_projects()] == [
        alpha.resolve(),
        beta.resolve(),
    ]


def test_bare_repository_resolution_uses_latest_project_without_tty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "MAINTAIN_SETTINGS_PATH",
        str(tmp_path / "settings.json"),
    )
    alpha, alpha_config = _configured_repository(
        tmp_path, "alpha", "Alpha Project")
    beta, beta_config = _configured_repository(
        tmp_path, "beta", "Beta Project")
    remember_repository(alpha, config_path=alpha_config)
    remember_repository(beta, config_path=beta_config)

    assert load_last_repository() == beta.resolve()
    assert repository_for_cli(None, interactive=False) == beta.resolve()


def test_saved_custom_config_is_reused_on_the_next_bare_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "MAINTAIN_SETTINGS_PATH",
        str(tmp_path / "settings.json"),
    )
    repository, default_path = _configured_repository(
        tmp_path, "custom", "Custom Project")
    custom_path = repository / "maintain.custom.json"
    default_path.replace(custom_path)
    remember_repository(repository, config_path=custom_path)

    loaded = cli._config(SimpleNamespace(
        repo=str(repository),
        config=None,
    ))

    assert loaded.path == custom_path.resolve()
    assert loaded.name == "Custom Project"

    custom_path.replace(default_path)
    loaded_after_move = cli._config(SimpleNamespace(
        repo=str(repository),
        config=None,
    ))

    assert loaded_after_move.path == default_path.resolve()
    assert load_recent_projects()[0].config_path == default_path.resolve()


def test_project_forget_removes_active_project_and_selects_latest_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(
        "MAINTAIN_SETTINGS_PATH",
        str(tmp_path / "settings.json"),
    )
    alpha, alpha_config = _configured_repository(
        tmp_path, "alpha", "Alpha Project")
    beta, beta_config = _configured_repository(
        tmp_path, "beta", "Beta Project")
    remember_repository(alpha, config_path=alpha_config)
    remember_repository(beta, config_path=beta_config)

    result = cli.main(["--json", "project", "forget", "Beta Project"])

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "action": "forget",
        "project": str(beta.resolve()),
    }
    assert [entry.path for entry in load_recent_projects()] == [alpha.resolve()]
    assert load_last_repository() == alpha.resolve()


@pytest.mark.parametrize("command", ["feature", "issue"])
def test_feature_and_issue_parse_copilot_reference_options(
    command: str,
    tmp_path: Path,
) -> None:
    reference = tmp_path / "reference.docx"

    args = cli.parser().parse_args([
        command,
        "Use",
        "the",
        "reference",
        "--reference",
        str(reference),
        "--save-reference",
    ])

    assert args.command == command
    assert args.request == ["Use", "the", "reference"]
    assert args.reference == str(reference)
    assert args.no_reference is False
    assert args.save_reference is True


def test_reference_and_no_reference_are_mutually_exclusive() -> None:
    args = cli.parser().parse_args([
        "feature",
        "Run without the saved brief",
        "--no-reference",
    ])

    assert args.no_reference is True
    assert args.reference is None
    with pytest.raises(SystemExit):
        cli.parser().parse_args([
            "feature",
            "Conflicting reference choices",
            "--reference",
            "https://example.com/spec",
            "--no-reference",
        ])


def test_home_shows_project_identity_and_project_controls() -> None:
    stream = io.StringIO()
    presenter = Presenter(
        stream=stream,
        animate=False,
        width=100,
        no_color=True,
    )

    presenter.home(
        "Example",
        "Codex",
        repository="/projects/example",
        branch="main",
        assistant_settings=False,
    )

    output = stream.getvalue()
    assert "/projects/example" in output
    assert "main" in output
    assert "Switch project" in output
    assert "Create a new project" in output
    assert "Assistant settings" not in output


def test_interactive_copilot_reference_can_use_the_file_picker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "MAINTAIN_SETTINGS_PATH",
        str(tmp_path / "settings.json"),
    )
    repository, config_path = _configured_repository(
        tmp_path, "reference-project", "Reference Project")
    config = ProjectConfig.load(config_path)
    reference = tmp_path / "brief.docx"
    reference.write_bytes(b"project brief")
    monkeypatch.setattr(cli, "select_file", lambda *_args, **_kwargs: reference)
    presenter = Presenter(
        stream=io.StringIO(),
        animate=False,
        width=100,
        no_color=True,
    )
    answers = iter(["b", "n"])
    presenter.ask = lambda *_args, **_kwargs: next(answers)  # type: ignore[method-assign]

    selected = cli._interactive_copilot_reference(config, presenter)

    assert selected == str(reference.resolve())
