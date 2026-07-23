"""Per-user repository selection for convenient interactive startup."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .errors import ConfigurationError


def settings_path() -> Path:
    override = os.environ.get("MAINTAIN_SETTINGS_PATH")
    return (Path(override).expanduser() if override
            else Path.home() / ".maintain" / "settings.json")


def repository_root(path: Path) -> Path | None:
    candidate = path.expanduser().resolve()
    if not candidate.exists():
        return None
    completed = subprocess.run(
        ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=False,
    )
    if completed.returncode:
        return None
    shown = completed.stdout.strip()
    return Path(shown).resolve() if shown else None


def load_last_repository() -> Path | None:
    path = settings_path()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        candidate = Path(str(value.get("last_repository", "")))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    return repository_root(candidate)


def remember_repository(repository: Path) -> None:
    root = repository_root(repository)
    if root is None:
        return
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(
        {"schema_version": 1, "last_repository": str(root)}, indent=2,
    ) + "\n"
    with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", prefix=".settings-", suffix=".json",
            dir=path.parent, delete=False) as temporary:
        temporary.write(rendered)
        temporary.flush()
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)


def repository_for_cli(explicit: str | None, *, interactive: bool) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    remembered = load_last_repository()
    if remembered is not None:
        return remembered
    if not interactive:
        raise ConfigurationError(
            "No repository has been selected. Use --repo PATH once or open Maintain interactively.")
    print("Choose the Git repository that Maintain should open.")
    while True:
        selected = _select_folder()
        if selected is None:
            raise ConfigurationError(
                "No repository was selected. Start Maintain again when you are ready.")
        root = repository_root(selected)
        if root is not None:
            remember_repository(root)
            return root
        print("That folder is not inside a Git repository. Choose the repository root.")


def _select_folder() -> Path | None:
    if sys.platform == "win32":
        return _windows_folder_picker()
    try:
        value = input("Repository folder: ").strip()
    except EOFError:
        return None
    return Path(value).expanduser() if value else None


def _windows_folder_picker() -> Path | None:
    script = r"""
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Select the Git repository that Maintain should open'
$dialog.ShowNewFolderButton = $false
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::Out.Write($dialog.SelectedPath)
}
"""
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass",
             "-Command", script],
            capture_output=True, text=True, check=False,
        )
    except OSError as exc:
        raise ConfigurationError(
            "Windows could not open the repository folder picker.") from exc
    selected = completed.stdout.strip()
    return Path(selected) if completed.returncode == 0 and selected else None
