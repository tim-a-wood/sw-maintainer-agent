"""Per-user project registry.

Maintain works inside one repository at a time, but you usually have more
than one. This records which repositories you have linked so the tool can
offer them at launch, and creates new ones on request.

The registry lives outside every project, under ~/.maintain, so nothing here
is ever committed to the repositories being maintained.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = 1

README_TEMPLATE = """\
# {name}

{description}
"""

GITIGNORE_TEMPLATE = """\
.maintain/
__pycache__/
*.py[cod]
.venv/
"""


class ProjectError(RuntimeError):
    """A project could not be linked or created, with a next action."""

    def __init__(self, message: str, next_action: Optional[str] = None):
        super().__init__(message)
        self.next_action = next_action


def registry_path() -> Path:
    override = os.environ.get("MAINTAIN_REGISTRY_PATH")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".maintain" / "projects.json"


def load_registry() -> dict:
    path = registry_path()
    if not path.exists():
        return {"schema": SCHEMA_VERSION, "projects": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema": SCHEMA_VERSION, "projects": []}
    if not isinstance(data, dict) or not isinstance(data.get("projects"), list):
        return {"schema": SCHEMA_VERSION, "projects": []}
    return data


def save_registry(data: dict) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def normalise(path) -> Path:
    return Path(path).expanduser().resolve()


def is_git_repository(path: Path) -> bool:
    if not path.is_dir():
        return False
    proc = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True,
    )
    return proc.returncode == 0


def repository_root(path: Path) -> Optional[Path]:
    proc = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    return Path(proc.stdout.strip())


def list_projects() -> list:
    """Registered projects, most recently opened first."""
    projects = []
    for entry in load_registry().get("projects", []):
        if not isinstance(entry, dict) or not entry.get("path"):
            continue
        path = Path(entry["path"])
        projects.append(
            {
                "path": path,
                "name": entry.get("name") or path.name,
                "last_opened": entry.get("last_opened") or "",
                "exists": path.is_dir(),
            }
        )
    projects.sort(key=lambda item: item["last_opened"], reverse=True)
    return projects


def link_project(path, name: Optional[str] = None) -> dict:
    """Register an existing Git repository."""
    target = normalise(path)
    if not target.is_dir():
        raise ProjectError(
            f"There is no folder at {target}.",
            "Check the path, or create a new project instead.",
        )
    root = repository_root(target)
    if root is None:
        raise ProjectError(
            f"{target} is not a Git repository.",
            "Run `git init` there first, or create a new project instead.",
        )
    root = normalise(root)
    data = load_registry()
    entries = [
        entry for entry in data.get("projects", [])
        if entry.get("path") != str(root)
    ]
    entries.append(
        {
            "path": str(root),
            "name": name or root.name,
            "linked": datetime.now().isoformat(),
            "last_opened": datetime.now().isoformat(),
        }
    )
    data["schema"] = SCHEMA_VERSION
    data["projects"] = entries
    save_registry(data)
    return {"path": root, "name": name or root.name}


def forget_project(path) -> bool:
    target = str(normalise(path))
    data = load_registry()
    entries = data.get("projects", [])
    remaining = [entry for entry in entries if entry.get("path") != target]
    if len(remaining) == len(entries):
        return False
    data["projects"] = remaining
    save_registry(data)
    return True


def touch_project(path) -> None:
    """Record that a project was just opened, so it sorts first next time."""
    target = str(normalise(path))
    data = load_registry()
    for entry in data.get("projects", []):
        if entry.get("path") == target:
            entry["last_opened"] = datetime.now().isoformat()
            save_registry(data)
            return


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
    )


def commit_identity(root: Path) -> list:
    """Fall back to a local identity when Git has no global one configured."""
    options = ["-c", "commit.gpgSign=false"]
    if not git(root, "config", "--get", "user.name").stdout.strip():
        options += ["-c", "user.name=Maintain"]
    if not git(root, "config", "--get", "user.email").stdout.strip():
        options += ["-c", "user.email=maintain@localhost"]
    return options


def create_project(path, name: Optional[str] = None, description: str = "") -> dict:
    """Create a folder, make it a Git repository, and register it."""
    target = normalise(path)
    if target.exists():
        raise ProjectError(
            f"{target} already exists.",
            "Choose a folder that does not exist yet, or link the existing one.",
        )
    project_name = name or target.name
    try:
        target.mkdir(parents=True)
    except OSError as exc:
        raise ProjectError(f"Could not create {target}: {exc}")

    created = False
    try:
        if git(target, "init", "--quiet").returncode != 0:
            raise ProjectError(f"Could not initialise a Git repository at {target}.")
        created = True
        git(target, "symbolic-ref", "HEAD", "refs/heads/main")
        (target / "README.md").write_text(
            README_TEMPLATE.format(
                name=project_name,
                description=description or "A new project maintained with Maintain.",
            ),
            encoding="utf-8",
        )
        (target / ".gitignore").write_text(GITIGNORE_TEMPLATE, encoding="utf-8")
        git(target, "add", "--", "README.md", ".gitignore")
        commit = subprocess.run(
            ["git", "-C", str(target), *commit_identity(target),
             "commit", "--quiet", "-m", "Initial commit"],
            capture_output=True, text=True,
        )
        if commit.returncode != 0:
            raise ProjectError(
                "Could not create the first commit:\n"
                + (commit.stderr or commit.stdout).strip()
            )
    except ProjectError:
        if created:
            # Leave nothing half-made behind.
            import shutil

            shutil.rmtree(target, ignore_errors=True)
        raise

    link_project(target, project_name)
    return {"path": target, "name": project_name}
