"""Project management for the desktop UI: create, open, add, and remove.

Remove only forgets the list entry; the folder and its files stay on the
computer. Creating a project makes a plain folder and does not start source
control. A run still needs Git, so the list shows each project's state.
"""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from maintain.audit import atomic_write
from maintain.config import CONFIG_NAME, ProjectConfig, default_config, find_config
from maintain.errors import ConfigurationError
from maintain.repository_memory import (forget_repository, load_recent_projects,
                                        remember_any_project)

READY = "ready"
NEEDS_SETUP = "setup"
NO_SOURCE_CONTROL = "no_git"
MISSING = "missing"

_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,63}$")


@dataclass(frozen=True)
class ProjectRow:
    path: Path
    name: str
    status: str


def classify(path: Path) -> str:
    path = Path(path)
    if not path.is_dir():
        return MISSING
    if not (path / ".git").exists():
        return NO_SOURCE_CONTROL
    if find_config(path) is None:
        return NEEDS_SETUP
    return READY


def project_rows() -> list[ProjectRow]:
    rows = []
    for entry in load_recent_projects(include_missing=True):
        rows.append(ProjectRow(path=entry.path, name=entry.name,
                               status=classify(entry.path)))
    return rows


def create_project_dir(parent: Path, name: str) -> Path:
    """Create one plain project folder. No source control starts here."""
    name = name.strip()
    if not _NAME_PATTERN.fullmatch(name):
        raise ConfigurationError(
            "A project name uses letters, numbers, spaces, dots, or dashes.")
    parent = Path(parent).expanduser().resolve()
    if not parent.is_dir():
        raise ConfigurationError(f"The parent folder does not exist: {parent}")
    target = parent / name
    if target.exists():
        raise ConfigurationError(f"The folder already exists: {target}")
    target.mkdir()
    remember_any_project(target)
    return target


def add_existing(path: Path) -> ProjectRow:
    root = remember_any_project(Path(path))
    return ProjectRow(path=root, name=root.name, status=classify(root))


def remove_project(path: Path) -> bool:
    """Remove the project from the list. The folder and its files stay."""
    return forget_repository(Path(path))


def ensure_config(repository: Path) -> Path:
    """Write the manual-ui configuration when the project has none."""
    existing = find_config(repository)
    if existing is not None:
        return existing
    rendered = json.dumps(default_config(repository, "manual-ui"), indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
            "w", suffix=".json", prefix=".maintain-validate-",
            dir=repository, delete=False, encoding="utf-8") as temporary:
        temporary.write(rendered)
        temporary_path = Path(temporary.name)
    try:
        ProjectConfig.load(temporary_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    path = repository / CONFIG_NAME
    atomic_write(path, rendered.encode())
    return path


def load_project_config(repository: Path) -> ProjectConfig:
    config_path = find_config(repository)
    if config_path is None:
        raise ConfigurationError(f"The project has no configuration: {repository}")
    return ProjectConfig.load(config_path)
