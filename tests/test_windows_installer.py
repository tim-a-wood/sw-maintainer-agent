from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-windows.ps1"


def _installer_text() -> str:
    return INSTALLER.read_text(encoding="utf-8")


def test_windows_installer_resolves_and_checks_out_an_immutable_commit() -> None:
    script = _installer_text()

    assert "ls-remote $Repository $Reference" in script
    assert "fetch --quiet --depth 1 origin $Commit" in script
    assert "checkout --quiet --detach FETCH_HEAD" in script
    assert "if ($actual -ne $Commit)" in script
    assert "archive/refs/heads/main.zip" not in script


def test_windows_installer_verifies_the_installed_version() -> None:
    script = _installer_text()

    assert "$expectedVersion = Get-ProjectVersion -SourceRoot $sourceRoot" in script
    assert '$installedVersion -ne "Maintain $expectedVersion"' in script
    assert "Source commit: $resolvedCommit" in script


def test_windows_installer_does_not_silently_fallback_to_stale_local_source() -> None:
    script = _installer_text()

    assert "Installing from this folder" not in script
    assert "Maintain was not reported as updated" in script
    assert '--upgrade --force-reinstall $packageSource' in script
