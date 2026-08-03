"""Per-user project registry for convenient interactive startup."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import ConfigurationError
from .proc import hidden

SETTINGS_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class ProjectEntry:
    """A remembered project plus its current on-disk status."""

    path: Path
    name: str
    last_opened_at: str
    config_path: Path | None = None
    default_reference: str | None = None
    exists: bool = False
    valid: bool = False
    configured: bool = False

    @property
    def repository(self) -> Path:
        """Compatibility-friendly alias for callers that prefer repository."""

        return self.path


def settings_path() -> Path:
    override = os.environ.get("MAINTAIN_SETTINGS_PATH")
    return (
        Path(override).expanduser()
        if override
        else Path.home() / ".maintain" / "settings.json"
    )


def repository_root(path: Path) -> Path | None:
    candidate = path.expanduser().resolve()
    if not candidate.exists():
        return None
    if candidate.is_file():
        candidate = candidate.parent
    completed = subprocess.run(
        ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **hidden(),
    )
    if completed.returncode:
        return None
    shown = completed.stdout.strip()
    return Path(shown).resolve() if shown else None


def load_recent_projects(*, include_missing: bool = True) -> list[ProjectEntry]:
    """Load remembered projects in most-recently-opened order.

    Missing and invalid entries remain visible so an interactive project picker
    can offer repair or forget actions. Set ``include_missing`` to false to hide
    paths that are no longer present while retaining existing non-Git folders.
    """

    data = _load_settings()
    raw_projects = data.get("recent_projects")
    if data.get("schema_version") == 1 or not isinstance(raw_projects, list):
        legacy = _path_value(data.get("last_repository"))
        raw_projects = (
            [{"path": str(legacy), "last_opened_at": _timestamp()}]
            if legacy is not None
            else []
        )

    projects: list[ProjectEntry] = []
    seen: set[str] = set()
    for raw in raw_projects:
        if not isinstance(raw, dict):
            continue
        entry = _entry_from_data(raw)
        if entry is None:
            continue
        key = _path_key(entry.path)
        if key in seen:
            continue
        seen.add(key)
        if include_missing or entry.exists:
            projects.append(entry)
    return projects


def load_last_repository() -> Path | None:
    """Return the active repository, falling back to the newest valid project."""

    data = _load_settings()
    active_value = _active_value(data)
    active = _path_value(active_value)
    if active is not None:
        root = repository_root(active)
        if root is not None:
            return root
    for project in load_recent_projects():
        if project.valid:
            return project.path
    return None


def remember_repository(
    repository: Path,
    *,
    config_path: Path | None = None,
) -> None:
    """Remember and activate a repository, moving it to the front of recents."""

    root = repository_root(repository)
    if root is None:
        return

    projects = load_recent_projects()
    key = _path_key(root)
    previous = next(
        (project for project in projects if _path_key(project.path) == key),
        None,
    )
    selected_config = _resolved_config_path(root, config_path)
    if (
        selected_config is None
        and previous is not None
        and previous.config_path is not None
        and previous.config_path.is_file()
    ):
        selected_config = previous.config_path
    name = _project_name(
        root,
        selected_config,
        fallback=previous.name if previous is not None else None,
    )
    entry = _build_entry(
        root,
        name=name,
        last_opened_at=_timestamp(),
        config_path=selected_config,
        default_reference=(
            previous.default_reference if previous is not None else None
        ),
    )
    ordered = [entry] + [
        project for project in projects if _path_key(project.path) != key
    ]
    _write_settings(root, ordered)


def activate_repository(repository: Path) -> Path:
    """Open a repository, making it active and newest in the registry."""

    root = repository_root(repository)
    if root is None:
        raise ConfigurationError(f"Not a Git repository: {repository}")
    previous = next(
        (
            project
            for project in load_recent_projects()
            if _path_key(project.path) == _path_key(root)
        ),
        None,
    )
    remember_repository(
        root,
        config_path=previous.config_path if previous is not None else None,
    )
    return root


def forget_repository(repository: Path) -> bool:
    """Remove a project from the registry and choose a valid active fallback."""

    candidate = (
        repository_root(repository)
        or repository.expanduser().resolve()
    )
    key = _path_key(candidate)
    projects = load_recent_projects()
    retained = [
        project for project in projects if _path_key(project.path) != key
    ]
    if len(retained) == len(projects):
        return False
    data = _load_settings()
    active_value = _active_value(data)
    previous_active = _path_value(active_value)
    active_root = (
        repository_root(previous_active)
        if previous_active is not None
        else None
    )
    retained_keys = {_path_key(project.path) for project in retained}
    active = (
        active_root
        if active_root is not None and _path_key(active_root) in retained_keys
        else next((project.path for project in retained if project.valid), None)
    )
    _write_settings(active, retained)
    return True


def default_reference_for(repository: Path) -> str | None:
    """Return the optional Copilot reference saved for a project."""

    candidate = (
        repository_root(repository)
        or repository.expanduser().resolve()
    )
    key = _path_key(candidate)
    for project in load_recent_projects():
        if _path_key(project.path) == key:
            return project.default_reference
    return None


def set_default_reference(
    repository: Path,
    reference: str | Path | None,
) -> None:
    """Set or clear a project's default Copilot reference."""

    root = repository_root(repository)
    if root is None:
        raise ConfigurationError(f"Not a Git repository: {repository}")
    projects = load_recent_projects()
    key = _path_key(root)
    existing = next(
        (project for project in projects if _path_key(project.path) == key),
        None,
    )
    cleaned = str(reference).strip() if reference is not None else ""
    replacement = _build_entry(
        root,
        name=(
            existing.name
            if existing is not None
            else _project_name(root, None)
        ),
        last_opened_at=(
            existing.last_opened_at
            if existing is not None
            else _timestamp()
        ),
        config_path=existing.config_path if existing is not None else None,
        default_reference=cleaned or None,
    )
    if existing is None:
        projects.insert(0, replacement)
    else:
        projects = [
            replacement if _path_key(project.path) == key else project
            for project in projects
        ]

    data = _load_settings()
    active = _path_value(_active_value(data))
    if active is None or repository_root(active) is None:
        active = next((project.path for project in projects if project.valid), root)
    _write_settings(active, projects)


def repository_for_cli(explicit: str | None, *, interactive: bool) -> Path:
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        return repository_root(candidate) or candidate
    remembered = load_last_repository()
    if remembered is not None:
        return remembered
    if not interactive:
        raise ConfigurationError(
            "No repository has been selected. Use --repo PATH once or open "
            "Maintain interactively."
        )
    print("Choose the Git repository that Maintain should open.")
    while True:
        selected = _select_folder()
        if selected is None:
            raise ConfigurationError(
                "No repository was selected. Start Maintain again when you are ready."
            )
        root = repository_root(selected)
        if root is not None:
            remember_repository(root)
            return root
        print("That folder is not inside a Git repository. Choose the repository root.")


def select_folder(
    prompt: str = "Repository folder",
    *,
    allow_new: bool = False,
) -> Path | None:
    """Select a folder using the native Windows picker or a terminal prompt."""

    if sys.platform == "win32":
        return _windows_folder_picker(prompt, allow_new=allow_new)
    try:
        value = input(f"{prompt}: ").strip()
    except EOFError:
        return None
    return Path(value).expanduser() if value else None


def select_file(prompt: str = "Reference file") -> Path | None:
    """Select a file using the native Windows picker or a terminal prompt."""

    if sys.platform == "win32":
        return _windows_file_picker(prompt)
    try:
        value = input(f"{prompt}: ").strip()
    except EOFError:
        return None
    return Path(value).expanduser() if value else None


def _select_folder() -> Path | None:
    """Retain the original private helper for existing callers and tests."""

    return select_folder(
        "Select the Git repository that Maintain should open",
        allow_new=False,
    )


def _load_settings() -> dict[str, Any]:
    try:
        value = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _active_value(data: dict[str, Any]) -> object:
    return (
        data.get("last_repository")
        if data.get("schema_version") == 1
        else data.get("active_repository")
    )


def _write_settings(
    active_repository: Path | None,
    projects: list[ProjectEntry],
) -> None:
    data: dict[str, Any] = {
        key: value for key, value in _load_settings().items()
        if key not in {"schema_version", "active_repository", "recent_projects",
                       "last_repository"}
    }
    data.update({
        "schema_version": SETTINGS_SCHEMA_VERSION,
        "active_repository": (
            str(active_repository.resolve())
            if active_repository is not None
            else None
        ),
        "recent_projects": [_entry_data(project) for project in projects],
    })
    _write_settings_data(data)


def _write_settings_data(data: dict[str, Any]) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            prefix=".settings-",
            suffix=".json",
            dir=path.parent,
            delete=False,
        ) as temporary:
            temporary.write(rendered)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _entry_from_data(raw: dict[str, Any]) -> ProjectEntry | None:
    path = _path_value(raw.get("path"))
    if path is None:
        return None
    root = repository_root(path)
    if root is not None:
        path = root
    config_path = _resolved_config_path(path, raw.get("config_path"))
    name_value = raw.get("name")
    name = (
        name_value.strip()
        if isinstance(name_value, str) and name_value.strip()
        else _project_name(path, config_path)
    )
    opened_value = raw.get("last_opened_at")
    opened = (
        opened_value.strip()
        if isinstance(opened_value, str) and opened_value.strip()
        else _timestamp()
    )
    reference_value = raw.get("default_reference")
    reference = (
        reference_value.strip()
        if isinstance(reference_value, str) and reference_value.strip()
        else None
    )
    return _build_entry(
        path,
        name=name,
        last_opened_at=opened,
        config_path=config_path,
        default_reference=reference,
    )


def _build_entry(
    path: Path,
    *,
    name: str,
    last_opened_at: str,
    config_path: Path | None,
    default_reference: str | None,
) -> ProjectEntry:
    root = repository_root(path)
    canonical = root if root is not None else path.expanduser().resolve()
    effective_config = config_path or canonical / ".maintain.json"
    return ProjectEntry(
        path=canonical,
        name=name,
        last_opened_at=last_opened_at,
        config_path=config_path,
        default_reference=default_reference,
        exists=canonical.exists(),
        valid=root is not None,
        configured=effective_config.is_file(),
    )


def _entry_data(project: ProjectEntry) -> dict[str, Any]:
    data: dict[str, Any] = {
        "path": str(project.path),
        "name": project.name,
        "last_opened_at": project.last_opened_at or _timestamp(),
    }
    if project.config_path is not None:
        data["config_path"] = str(project.config_path)
    if project.default_reference is not None:
        data["default_reference"] = project.default_reference
    return data


def _project_name(
    repository: Path,
    config_path: Path | None,
    *,
    fallback: str | None = None,
) -> str:
    candidate = config_path or repository / ".maintain.json"
    try:
        data = json.loads(candidate.read_text(encoding="utf-8"))
        project = data.get("project", {})
        name = project.get("name") if isinstance(project, dict) else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        name = None
    if isinstance(name, str) and name.strip():
        return name.strip()
    return fallback or repository.name


def _resolved_config_path(
    repository: Path,
    value: object,
) -> Path | None:
    if value is None:
        return None
    if isinstance(value, Path):
        candidate = value
    elif isinstance(value, str) and value.strip():
        candidate = Path(value)
    else:
        return None
    candidate = candidate.expanduser()
    if not candidate.is_absolute():
        candidate = repository / candidate
    return candidate.resolve()


def _path_value(value: object) -> Path | None:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        return None
    return Path(value).expanduser().resolve()


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.expanduser().resolve()))


def _timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _windows_folder_picker(
    prompt: str = "Select the Git repository that Maintain should open",
    *,
    allow_new: bool = False,
) -> Path | None:
    escaped_prompt = prompt.replace("'", "''")
    show_new = "$true" if allow_new else "$false"
    script = rf"""
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = '{escaped_prompt}'
$dialog.ShowNewFolderButton = {show_new}
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {{
    [Console]::Out.Write($dialog.SelectedPath)
}}
"""
    return _run_windows_picker(script, "folder")


def _windows_file_picker(prompt: str) -> Path | None:
    escaped_prompt = prompt.replace("'", "''")
    script = rf"""
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = '{escaped_prompt}'
$dialog.CheckFileExists = $true
$dialog.Multiselect = $false
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {{
    [Console]::Out.Write($dialog.FileName)
}}
"""
    return _run_windows_picker(script, "file")


def _run_windows_picker(script: str, kind: str) -> Path | None:
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-STA",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            **hidden(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise ConfigurationError(
            f"Windows could not open the {kind} picker."
        ) from exc
    selected = completed.stdout.strip()
    return Path(selected) if completed.returncode == 0 and selected else None


def remember_any_project(path: Path) -> Path:
    """Remember a project folder even when it has no source control yet.

    A folder with Git becomes the active repository; a plain folder joins the
    recent list without changing the active repository.
    """

    root = repository_root(path) or path.expanduser().resolve()
    projects = load_recent_projects()
    key = _path_key(root)
    previous = next(
        (project for project in projects if _path_key(project.path) == key),
        None,
    )
    config_path = _resolved_config_path(root, None)
    if (config_path is None and previous is not None
            and previous.config_path is not None
            and previous.config_path.is_file()):
        config_path = previous.config_path
    name = _project_name(
        root, config_path,
        fallback=previous.name if previous is not None else None,
    )
    entry = _build_entry(
        root, name=name, last_opened_at=_timestamp(), config_path=config_path,
        default_reference=(previous.default_reference
                           if previous is not None else None),
    )
    ordered = [entry] + [
        project for project in projects if _path_key(project.path) != key
    ]
    if (root / ".git").exists():
        active = root
    else:
        active = _path_value(_active_value(_load_settings()))
    _write_settings(active, ordered)
    return root


def load_ui_settings() -> dict[str, Any]:
    """Per-user desktop UI settings stored next to the recent projects."""

    value = _load_settings().get("ui")
    return dict(value) if isinstance(value, dict) else {}


def save_ui_settings(values: dict[str, Any]) -> None:
    data = _load_settings()
    data.setdefault("schema_version", SETTINGS_SCHEMA_VERSION)
    data["ui"] = dict(values)
    _write_settings_data(data)
