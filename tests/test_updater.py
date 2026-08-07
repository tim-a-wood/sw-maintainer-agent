"""FR-V12: the self-updater, which is Python so it can be tested.

The PowerShell script this replaces was never exercised below the
Windows smoke job, and the one check it did make — PowerShell's `$?`
after calling the installer — was wrong in a way no test could see. A
failed install reported success, the old version started again, and
the person got "the last update did not take" with nothing to act on.

Every test here runs on any platform. The seams are the arguments:
`run`, `popen`, `alive`, `sleep`, `clock`.
"""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest

from maintain import updater


class FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _venv(root: Path) -> Path:
    """A stand-in for the installed environment, on any platform."""
    runtime = updater.runtime_path(root)
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_text("#!/bin/sh\n", encoding="utf-8")
    return runtime


class Recorder:
    """A scripted `run`: answers version questions, records installs."""

    def __init__(self, versions: list[str], install_code: int = 0,
                 install_output: str = ""):
        self.versions = list(versions)
        self.install_code = install_code
        self.install_output = install_output
        self.commands: list[list[str]] = []

    def __call__(self, command, **kwargs):
        self.commands.append(list(command))
        if "pip" in command:
            return FakeCompleted(self.install_code, self.install_output,
                                 "" if not self.install_code
                                 else self.install_output)
        answer = self.versions.pop(0) if self.versions else ""
        return FakeCompleted(0 if answer else 1, answer + "\n")


# ---------- the pieces ----------

def test_the_wanted_version_comes_from_the_reference():
    assert updater.wanted_version("refs/tags/v1.2.3") == "1.2.3"
    assert updater.wanted_version("v0.9.14") == "0.9.14"
    # A branch is not a version, so there is nothing to compare
    # against and the check is skipped. The smoke test updates from a
    # branch, and would fail every run if this returned "main".
    assert updater.wanted_version("refs/heads/main") == ""


def test_pip_fetches_the_tag_with_no_clone_of_its_own():
    """The old script cloned the release, then ran that clone's
    installer, which resolved and downloaded the release again.

    The extra is named too (FR-V19). Qt is optional in pyproject, and
    an update that asked for the bare package left whatever the
    environment happened to have — fine until it is rebuilt, and then
    the window never opens and says nothing.
    """
    value = updater.requirement("refs/tags/v1.2.3")
    assert value == (
        "sw-maintainer-agent[ui] @ "
        "git+https://github.com/tim-a-wood/sw-maintainer-agent.git@v1.2.3")
    # pip 24 refuses that form for a local path, so a second form
    # follows for the smoke test, which updates from a checkout.
    fallback = updater.requirements("refs/tags/v1.2.3")[1]
    assert fallback.endswith("#egg=sw-maintainer-agent[ui]")


def test_waiting_for_the_app_gives_up_rather_than_hanging():
    ticks = iter([0.0, 1.0, 2.0, 3.0, 99.0, 100.0])
    slept: list[float] = []
    assert updater.wait_for_exit(
        4321, timeout=10.0, alive=lambda pid: True,
        sleep=slept.append, clock=lambda: next(ticks)) is False
    assert slept, "it must wait between looks, not spin"


def test_waiting_ends_as_soon_as_the_app_is_gone():
    looks = iter([True, True, False])
    assert updater.wait_for_exit(
        4321, timeout=10.0, alive=lambda pid: next(looks),
        sleep=lambda _s: None, clock=lambda: 0.0) is True


def test_a_missing_runtime_reports_no_version(tmp_path):
    assert updater.installed_version(tmp_path / "nothing") == ""


def test_the_version_is_what_the_runtime_says(tmp_path):
    runtime = _venv(tmp_path)
    asked = Recorder(["0.9.14"])
    assert updater.installed_version(runtime, run=asked) == "0.9.14"
    assert "import maintain; print(maintain.__version__)" in asked.commands[0]


def test_a_runtime_that_cannot_answer_reports_no_version(tmp_path):
    runtime = _venv(tmp_path)

    def broken(command, **kwargs):
        raise OSError("the environment is broken")

    assert updater.installed_version(runtime, run=broken) == ""


# ---------- the whole update ----------

def test_an_update_that_works_reports_the_new_version(tmp_path):
    _venv(tmp_path)
    started: list[list[str]] = []
    (tmp_path / "Maintain.cmd").write_text("@echo off\n", encoding="utf-8")

    result = updater.update(
        "refs/tags/v0.9.14", tmp_path, app_pid=0,
        run=Recorder(["0.9.13", "0.9.14"]),
        popen=lambda command, **kwargs: started.append(list(command)))

    assert result.ok, result.reason
    assert result.installed == "0.9.14"
    # The app comes back.
    assert started and started[0][0].endswith("Maintain.cmd")


def test_a_failed_install_is_not_called_a_success(tmp_path):
    """The field fault. pip fails, and the update must say so rather
    than start the old version and leave the person guessing."""
    _venv(tmp_path)
    started: list[list[str]] = []

    result = updater.update(
        "refs/tags/v0.9.14", tmp_path, app_pid=0,
        run=Recorder(["0.9.13"], install_code=1,
                     install_output="ERROR: Could not find a version"),
        popen=lambda command, **kwargs: started.append(list(command)))

    assert not result.ok
    assert "Could not find a version" in result.reason
    # Nothing is started again on a failure.
    assert not started


def test_an_install_that_leaves_the_old_version_is_a_failure(tmp_path):
    """pip can report success and change nothing — a cached wheel, a
    pinned dependency. The version is the only honest check, and this
    is exactly what "$?" could never catch."""
    _venv(tmp_path)

    result = updater.update(
        "refs/tags/v0.9.14", tmp_path, app_pid=0,
        run=Recorder(["0.9.13", "0.9.13"]),
        popen=lambda command, **kwargs: None)

    assert not result.ok
    assert "still 0.9.13" in result.reason
    assert "0.9.14" in result.reason


def test_an_install_that_breaks_the_runtime_is_a_failure(tmp_path):
    _venv(tmp_path)

    result = updater.update(
        "refs/tags/v0.9.14", tmp_path, app_pid=0,
        run=Recorder(["0.9.13", ""]),
        popen=lambda command, **kwargs: None)

    assert not result.ok
    assert "no Maintain runtime answers" in result.reason


def test_an_app_that_will_not_close_stops_the_update(tmp_path):
    _venv(tmp_path)
    ticks = iter([0.0, 1.0, 200.0, 201.0])

    result = updater.update(
        "refs/tags/v0.9.14", tmp_path, app_pid=99,
        run=Recorder(["0.9.13", "0.9.14"]),
        popen=lambda command, **kwargs: None,
        alive=lambda pid: True, sleep=lambda _s: None,
        clock=lambda: next(ticks))

    assert not result.ok
    assert "did not close" in result.reason


def test_a_missing_environment_says_where_it_looked(tmp_path):
    result = updater.update(
        "refs/tags/v0.9.14", tmp_path, app_pid=0,
        run=Recorder([]), popen=lambda command, **kwargs: None)

    assert not result.ok
    assert "No Maintain environment" in result.reason
    assert "GitHub releases page" in result.reason


def test_no_relaunch_leaves_the_app_closed(tmp_path):
    _venv(tmp_path)
    (tmp_path / "Maintain.cmd").write_text("@echo off\n", encoding="utf-8")
    started: list = []

    result = updater.update(
        "refs/tags/v0.9.14", tmp_path, app_pid=0, no_relaunch=True,
        run=Recorder(["0.9.13", "0.9.14"]),
        popen=lambda command, **kwargs: started.append(command))

    assert result.ok
    assert not started


# ---------- the record it leaves ----------

def test_the_log_records_a_failure_for_someone_to_read(tmp_path):
    result = updater.UpdateResult(False, "The install failed. ERROR: no wheel",
                                  wanted="0.9.14", lines=["Wanted: 0.9.14"])
    path = updater.write_log(tmp_path, result)

    written = path.read_text(encoding="utf-8")
    assert "Wanted: 0.9.14" in written
    assert "The update failed." in written
    assert "ERROR: no wheel" in written


def test_the_log_appends_so_earlier_tries_survive(tmp_path):
    updater.write_log(tmp_path, updater.UpdateResult(False, "first"))
    updater.write_log(tmp_path, updater.UpdateResult(False, "second"))

    written = updater.log_path(tmp_path).read_text(encoding="utf-8")
    assert "first" in written and "second" in written


def test_a_log_that_cannot_be_written_does_not_stop_the_update(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("not a folder", encoding="utf-8")
    # No raise: the update mattered, the log is the record of it.
    updater.write_log(blocked / "root", updater.UpdateResult(True))


# ---------- the command line the app starts ----------

def test_the_command_line_reports_success(tmp_path, monkeypatch):
    _venv(tmp_path)
    (tmp_path / "Maintain.cmd").write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setattr(updater.subprocess, "run",
                        Recorder(["0.9.13", "0.9.14"]))
    out = io.StringIO()

    code = updater.main([
        "--reference", "refs/tags/v0.9.14",
        "--install-root", str(tmp_path),
        "--no-relaunch"], stream=out)

    assert code == 0
    assert "The update is complete." in out.getvalue()
    assert "Installed: 0.9.14" in updater.log_path(tmp_path).read_text(
        encoding="utf-8")


def test_the_command_line_names_the_log_when_it_fails(tmp_path):
    out = io.StringIO()
    waited: list[bool] = []

    code = updater.main([
        "--reference", "refs/tags/v0.9.14",
        "--install-root", str(tmp_path / "nowhere"),
        "--no-relaunch"], stream=out,
        wait_for_reader=lambda: waited.append(True))

    assert code == 1
    printed = out.getvalue()
    assert "The update failed." in printed
    assert "Update log:" in printed
    assert "No Maintain environment" in printed
    # The window waits, or the person never reads it.
    assert waited == [True]


def test_the_reference_is_required():
    with pytest.raises(SystemExit):
        updater.main(["--install-root", "."], stream=io.StringIO())


# ---------- what it must never do ----------

def test_the_updater_needs_nothing_from_maintain():
    """It is copied to a temporary folder and run there, so an import
    from the package it is replacing would either fail or hold the
    files it must change."""
    source = Path(updater.__file__).read_text(encoding="utf-8")
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            assert "maintain" not in stripped, stripped
    assert "shell=True" not in source


def test_the_powershell_updater_is_gone():
    root = Path(updater.__file__).resolve().parents[2]
    assert not (root / "maintain" / "data" / "windows"
                / "update-maintain.ps1").exists()


def test_the_install_root_falls_back_when_the_app_runs_from_a_checkout(
        monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert updater.install_root() == tmp_path / "Programs" / "Maintain"


def test_the_install_root_is_found_from_the_running_interpreter(
        monkeypatch, tmp_path):
    root = tmp_path / "Programs" / "Maintain"
    (root / "venv" / "Scripts").mkdir(parents=True)
    monkeypatch.setattr(
        updater.sys, "executable", str(root / "venv" / "Scripts" / "python.exe"))
    assert updater.install_root() == root


def test_the_install_uses_no_shell_and_asks_no_questions(tmp_path):
    runtime = _venv(tmp_path)
    asked = Recorder(["0.9.13"])
    updater.install(runtime, "refs/tags/v0.9.14", run=asked)

    command = asked.commands[0]
    assert command[:5] == [str(runtime), "-m", "pip", "install", "--upgrade"]
    assert "--no-input" in command


def test_a_pip_that_cannot_start_is_reported_not_raised(tmp_path):
    runtime = _venv(tmp_path)

    def broken(command, **kwargs):
        raise subprocess.SubprocessError("pip is missing")

    ok, output = updater.install(runtime, "refs/tags/v0.9.14", run=broken)
    assert not ok
    assert "pip is missing" in output
