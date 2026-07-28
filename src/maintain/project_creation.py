"""Safe creation of a minimal Git project configured for Maintain."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .audit import atomic_write
from .config import CONFIG_NAME, ProjectConfig, default_config
from .errors import ConfigurationError, MaintainError


SUPPORTED_PROVIDERS = frozenset(
    {"codex", "manual-ui", "file-exchange", "chatgpt-browser", "m365-browser"}
)


@dataclass(frozen=True)
class CreatedProject:
    """Paths created for a new Maintain project."""

    repository: Path
    config_path: Path


def _validated_destination(destination: Path) -> Path:
    raw = os.fspath(destination)
    if not raw.strip():
        raise ConfigurationError("The new project path cannot be empty.")
    candidate = Path(destination).expanduser()
    if os.path.lexists(candidate):
        raise ConfigurationError(
            f"The new project path already exists: {candidate}"
        )
    parent = candidate.parent.expanduser().resolve()
    if not parent.is_dir():
        raise ConfigurationError(
            f"The parent folder for the new project does not exist: {parent}"
        )
    return parent / candidate.name


def _validated_name(destination: Path, name: str | None) -> str:
    selected = (destination.name if name is None else name).strip()
    if not selected or selected in {".", ".."}:
        raise ConfigurationError("The project name cannot be empty.")
    if len(selected) > 200:
        raise ConfigurationError("The project name cannot exceed 200 characters.")
    if any(ord(character) < 32 or ord(character) == 127 for character in selected):
        raise ConfigurationError("The project name cannot contain control characters.")
    return selected


def _run_git(
    repository: Path,
    *arguments: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    command = ["git", "-C", str(repository), *arguments]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise MaintainError(f"Git could not be started: {exc}") from exc
    if check and completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        suffix = f": {detail}" if detail else "."
        raise MaintainError(f"Git command failed ({arguments[0]}){suffix}")
    return completed


def _commit_identity_options(repository: Path) -> list[str]:
    options = ["-c", "commit.gpgSign=false"]
    name = _run_git(repository, "config", "--get", "user.name", check=False)
    email = _run_git(repository, "config", "--get", "user.email", check=False)
    if name.returncode or not name.stdout.strip():
        options.extend(("-c", "user.name=Maintain"))
    if email.returncode or not email.stdout.strip():
        options.extend(("-c", "user.email=maintain@localhost"))
    return options


def _rollback_project(repository: Path) -> OSError | None:
    try:
        shutil.rmtree(repository)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return exc
    return None


def create_project(
    destination: Path,
    provider: str = "m365-browser",
    name: str | None = None,
) -> CreatedProject:
    """Create a new Git project and a validated Maintain v2 configuration.

    The destination must not already exist and its parent folder must exist. If
    any creation step fails, only the destination created by this call is
    removed.
    """

    repository = _validated_destination(destination)
    project_name = _validated_name(repository, name)
    if provider not in SUPPORTED_PROVIDERS:
        raise ConfigurationError(f"Unknown provider for the new project: {provider}")

    created = False
    try:
        try:
            repository.mkdir()
            created = True
        except OSError as exc:
            raise MaintainError(
                f"Could not create the project folder {repository}: {exc}"
            ) from exc

        initialized = _run_git(
            repository, "init", "-b", "main", check=False)
        if initialized.returncode:
            _run_git(repository, "init")
            _run_git(repository, "symbolic-ref", "HEAD", "refs/heads/main")

        readme = f"# {project_name}\n".encode("utf-8")
        gitignore = f"{CONFIG_NAME}\n.maintain/\n".encode("utf-8")
        atomic_write(repository / "README.md", readme)
        atomic_write(repository / ".gitignore", gitignore)

        candidate = default_config(repository, provider)
        candidate["project"]["name"] = project_name
        config_path = repository / CONFIG_NAME
        rendered = json.dumps(candidate, indent=2, ensure_ascii=False) + "\n"
        atomic_write(config_path, rendered.encode("utf-8"))
        ProjectConfig.load(config_path)

        _run_git(repository, "add", "--", "README.md", ".gitignore")
        identity = _commit_identity_options(repository)
        _run_git(
            repository,
            *identity,
            "commit",
            "-m",
            "Initial project setup",
        )
        branch = _run_git(repository, "branch", "--show-current").stdout.strip()
        if branch != "main":
            raise MaintainError(
                f"The new project was initialized on {branch or 'an unknown branch'}, not main."
            )
        return CreatedProject(repository=repository, config_path=config_path)
    except BaseException as exc:
        cleanup_error = _rollback_project(repository) if created else None
        if cleanup_error is not None:
            raise MaintainError(
                f"Project creation failed and cleanup of {repository} also failed: "
                f"{cleanup_error}"
            ) from exc
        if isinstance(exc, (MaintainError, KeyboardInterrupt, SystemExit)):
            raise
        if isinstance(exc, OSError):
            raise MaintainError(f"Could not create the project: {exc}") from exc
        raise
