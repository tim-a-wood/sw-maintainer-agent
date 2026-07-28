"""Copy packets into OneDrive, watch synchronization, compose the link."""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import quote

from .errors import ConfigurationError
from .repository_memory import load_ui_settings, save_ui_settings

SYNCED = "synced"
PENDING = "pending"
UNKNOWN = "unknown"

# Windows marks a fully uploaded, dehydrated cloud file with this attribute.
_RECALL_ON_DATA_ACCESS = 0x400000


@dataclass(frozen=True)
class OneDriveSettings:
    folder: str = ""
    link_base: str = ""
    timeout_seconds: int = 120

    @property
    def configured(self) -> bool:
        return bool(self.folder.strip())


@dataclass(frozen=True)
class PublishResult:
    copied_path: Path
    link: str
    sync_state: str
    waited_seconds: float


def onedrive_settings() -> OneDriveSettings:
    data = load_ui_settings().get("onedrive", {})
    if not isinstance(data, dict):
        data = {}
    try:
        timeout = int(data.get("timeout_seconds", 120))
    except (TypeError, ValueError):
        timeout = 120
    return OneDriveSettings(
        folder=str(data.get("folder", "") or ""),
        link_base=str(data.get("link_base", "") or ""),
        timeout_seconds=max(10, min(timeout, 900)),
    )


def save_onedrive_settings(settings: OneDriveSettings) -> None:
    values = load_ui_settings()
    values["onedrive"] = {
        "folder": settings.folder,
        "link_base": settings.link_base,
        "timeout_seconds": settings.timeout_seconds,
    }
    save_ui_settings(values)


def compose_link(link_base: str, name: str) -> str:
    base = link_base.strip().rstrip("/")
    if not base:
        return ""
    return f"{base}/{quote(name)}"


def probe_sync_state(path: Path) -> str:
    """Best-effort OneDrive state. UNKNOWN keeps the manual fallback in charge."""
    if sys.platform != "win32":
        return UNKNOWN
    script = (
        "$item = Get-Item -LiteralPath '" + str(path).replace("'", "''") + "'; "
        "[int]$item.Attributes"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return UNKNOWN
    if completed.returncode != 0:
        return UNKNOWN
    try:
        attributes = int(completed.stdout.strip())
    except ValueError:
        return UNKNOWN
    if attributes & _RECALL_ON_DATA_ACCESS:
        return SYNCED
    return PENDING


def expand_packet_folder(zip_path: Path, destination_root: Path) -> Path:
    """The folder fallback: expand the packet next to the ZIP (package.style=folder)."""
    target = destination_root / zip_path.stem
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            member = PurePosixPath(info.filename)
            if member.is_absolute() or ".." in member.parts or "\\" in info.filename:
                raise ConfigurationError(f"The packet contains an unsafe member: {info.filename}")
            output = target / Path(*member.parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(archive.read(info))
    return target


def publish_packet(zip_path: Path, settings: OneDriveSettings, *,
                   expand_folder: bool = False,
                   prober: Callable[[Path], str] = probe_sync_state,
                   sleeper: Callable[[float], Any] = time.sleep,
                   clock: Callable[[], float] = time.monotonic) -> PublishResult:
    """Copy the packet into the OneDrive folder, wait for sync, compose the link."""
    if not settings.configured:
        raise ConfigurationError(
            "Set the OneDrive package folder in Settings before you copy a link.")
    folder = Path(settings.folder).expanduser()
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigurationError(
            f"The OneDrive package folder is not writable: {folder}") from exc
    destination = folder / zip_path.name
    shutil.copyfile(zip_path, destination)
    if expand_folder:
        expand_packet_folder(destination, folder)
    started = clock()
    state = prober(destination)
    while state == PENDING and clock() - started < settings.timeout_seconds:
        sleeper(2.0)
        state = prober(destination)
    return PublishResult(
        copied_path=destination,
        link=compose_link(settings.link_base, zip_path.name),
        sync_state=state,
        waited_seconds=clock() - started,
    )
