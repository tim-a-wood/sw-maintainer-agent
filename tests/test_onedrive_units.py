"""OneDrive settings coercion, the sync probe, and the publish loop."""

from __future__ import annotations


import zipfile
from pathlib import Path

import pytest

from maintain import onedrive
from maintain.errors import ConfigurationError
from maintain.onedrive import (PENDING, SYNCED, UNKNOWN, OneDriveSettings,
                               expand_packet_folder, onedrive_settings,
                               probe_sync_state, publish_packet,
                               save_onedrive_settings)


def _use_settings(tmp_path, monkeypatch, value) -> None:
    from maintain.repository_memory import load_ui_settings, save_ui_settings
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    values = load_ui_settings()
    values["onedrive"] = value
    save_ui_settings(values)


def test_settings_coerce_bad_shapes_and_clamp_the_timeout(tmp_path, monkeypatch):
    _use_settings(tmp_path, monkeypatch, "nonsense")
    assert onedrive_settings() == OneDriveSettings()
    _use_settings(tmp_path, monkeypatch, {"folder": "F", "timeout_seconds": "soon"})
    assert onedrive_settings().timeout_seconds == 120
    _use_settings(tmp_path, monkeypatch, {"timeout_seconds": 2})
    assert onedrive_settings().timeout_seconds == 10
    _use_settings(tmp_path, monkeypatch, {"timeout_seconds": 5000})
    assert onedrive_settings().timeout_seconds == 900

    save_onedrive_settings(OneDriveSettings(folder="F", link_base="L",
                                            timeout_seconds=42))
    stored = onedrive_settings()
    assert (stored.folder, stored.link_base, stored.timeout_seconds) == (
        "F", "L", 42)
    assert stored.configured is True
    assert OneDriveSettings().configured is False


class _Completed:
    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self.stdout = stdout


def test_probe_reads_the_windows_attribute_and_fails_safe(tmp_path, monkeypatch):
    target = tmp_path / "packet.zip"
    target.write_text("x", encoding="utf-8")
    assert probe_sync_state(target) == UNKNOWN   # not Windows here

    monkeypatch.setattr(onedrive.sys, "platform", "win32")
    answers = {}
    monkeypatch.setattr(
        onedrive.subprocess, "run",
        lambda *args, **kwargs: answers["value"])

    answers["value"] = _Completed(0, str(0x400000 | 0x20))
    assert probe_sync_state(target) == SYNCED
    answers["value"] = _Completed(0, "32")
    assert probe_sync_state(target) == PENDING
    answers["value"] = _Completed(1, "")
    assert probe_sync_state(target) == UNKNOWN
    answers["value"] = _Completed(0, "not-a-number")
    assert probe_sync_state(target) == UNKNOWN

    def boom(*args, **kwargs):
        raise OSError("no powershell")
    monkeypatch.setattr(onedrive.subprocess, "run", boom)
    assert probe_sync_state(target) == UNKNOWN


def _packet(tmp_path: Path) -> Path:
    packet = tmp_path / "maintain-run-plan.zip"
    with zipfile.ZipFile(packet, "w") as archive:
        archive.writestr("TASK.md", "# Task\n")
        archive.writestr("sub/notes.md", "notes\n")
    return packet


def test_publish_requires_a_folder_and_reports_an_unwritable_one(tmp_path):
    packet = _packet(tmp_path)
    with pytest.raises(ConfigurationError, match="Settings"):
        publish_packet(packet, OneDriveSettings())
    blocked = tmp_path / "blocked"
    blocked.write_text("a file, not a folder", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="not writable"):
        publish_packet(packet, OneDriveSettings(
            folder=str(blocked / "inside")))


def test_publish_waits_for_sync_and_composes_the_link(tmp_path):
    packet = _packet(tmp_path)
    states = [PENDING, PENDING, SYNCED]
    naps: list[float] = []
    ticks = iter(range(0, 100, 2))
    result = publish_packet(
        packet,
        OneDriveSettings(folder=str(tmp_path / "cloud"),
                         link_base="https://1drv.example/m/",
                         timeout_seconds=60),
        expand_folder=True,
        prober=lambda path: states.pop(0),
        sleeper=naps.append,
        clock=lambda: float(next(ticks)))
    assert result.sync_state == SYNCED
    assert naps == [2.0, 2.0]
    assert result.copied_path == tmp_path / "cloud" / packet.name
    assert result.copied_path.is_file()
    assert result.link == "https://1drv.example/m/maintain-run-plan.zip"
    expanded = tmp_path / "cloud" / packet.stem
    assert (expanded / "TASK.md").is_file()
    assert (expanded / "sub" / "notes.md").is_file()

    # A run against a clock past the timeout gives up while it stays honest.
    late = publish_packet(
        packet,
        OneDriveSettings(folder=str(tmp_path / "cloud"), timeout_seconds=10),
        prober=lambda path: PENDING,
        sleeper=lambda seconds: None,
        clock=iter([0.0, 5.0, 50.0, 51.0]).__next__)
    assert late.sync_state == PENDING
    assert late.link == ""


def test_expand_refuses_unsafe_members_and_replaces_the_old_folder(tmp_path):
    packet = _packet(tmp_path)
    target_root = tmp_path / "out"
    target_root.mkdir()
    stale = target_root / packet.stem
    stale.mkdir()
    (stale / "leftover.txt").write_text("old", encoding="utf-8")
    expanded = expand_packet_folder(packet, target_root)
    assert not (expanded / "leftover.txt").exists()
    assert (expanded / "TASK.md").is_file()

    for name in ("../escape.md", "/absolute.md", "bad\\slash.md"):
        hostile = tmp_path / "hostile.zip"
        with zipfile.ZipFile(hostile, "w") as archive:
            archive.writestr(name, "x")
        with pytest.raises(ConfigurationError, match="unsafe"):
            expand_packet_folder(hostile, target_root)
        hostile.unlink()
