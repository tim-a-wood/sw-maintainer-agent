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


def test_windows_installer_installs_the_newest_release_by_default() -> None:
    """The field fault: the installer defaulted to a branch tip, so a
    fresh install landed on whatever that branch held — an old version
    — while the releases moved on without it."""
    script = _installer_text()

    assert '$repositoryRef = "refs/heads/main"' not in script
    assert "function Resolve-LatestReleaseTag" in script
    assert "ls-remote --tags $Repository" in script
    # The pin from the self-updater still wins over the newest tag.
    assert "MAINTAIN_PACKAGE_REF" in script
    resolve = script.index("$repositoryRef = Resolve-LatestReleaseTag")
    assert script.index("$repositoryRefOverride.Trim()") < resolve


def test_windows_installer_prefers_a_python_that_runs_every_feature() -> None:
    """The field fault: `py -3` takes the newest Python. On a computer
    with 3.14 the private runtime had no Manim, so Explain code could
    only ever report that Manim is absent."""
    script = _installer_text()

    for wanted in ('"-3.13"', '"-3.12"', '"-3.11"'):
        assert wanted in script, wanted
    # The supported versions are tried before the newest-wins fallback.
    assert script.index('"-3.13"') < script.index('@("-3")')
    # And a 3.14 runtime says what it costs, instead of staying quiet.
    assert '[Version]"3.14"' in script
    assert "cannot run the video feature" in script


def test_windows_installer_does_not_silently_fallback_to_stale_local_source() -> None:
    script = _installer_text()

    assert "Installing from this folder" not in script
    assert "Maintain was not reported as updated" in script
    assert '--upgrade --force-reinstall $packageSource' in script
