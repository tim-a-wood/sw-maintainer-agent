"""FR-V14: install and remove Maintain without PowerShell.

The field fault: "the install and uninstall cmd script still fail due
to an unsigned powershell script". On a managed machine the execution
policy refuses an unsigned script, and -ExecutionPolicy Bypass does
not override a policy the organisation set. Both the install and the
uninstall were unreachable, with nothing the person could do.

A batch file has no signing gate, so install.cmd finds Python and
starts install_maintain.py. These tests run everywhere, which the
PowerShell installer's never could.
"""

from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


shortcut = _load("shortcut")
installer = _load("install_maintain")


class FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


# ---------- the shortcut, written without Windows Script Host ----------

def test_a_shortcut_names_its_target_and_icon(tmp_path):
    """Every other route to a .lnk runs something a managed machine can
    refuse: Windows Script Host, or PowerShell with COM."""
    target = r"C:\Users\me\AppData\Local\Programs\Maintain\Maintain-UI.cmd"
    icon = r"C:\Users\me\AppData\Local\Programs\Maintain\maintain.ico,0"
    path = shortcut.write_shortcut(
        tmp_path / "Maintain.lnk", target, working_dir=r"C:\Users\me",
        icon=icon, description="Maintain")

    read = shortcut.read_shortcut(path)
    assert read["target"] == target
    assert icon in read["strings"]
    assert r"C:\Users\me" in read["strings"]


def test_a_shortcut_is_a_real_shell_link(tmp_path):
    """The header is what Windows reads first. A wrong size or class
    makes a file the shell will not open."""
    path = shortcut.write_shortcut(tmp_path / "x.lnk", r"C:\a\b.cmd")
    raw = path.read_bytes()

    assert int.from_bytes(raw[:4], "little") == 0x4C
    assert raw[4:20] == shortcut.LINK_CLSID
    # Unicode strings, and a target that can be resolved.
    flags = int.from_bytes(raw[20:24], "little")
    assert flags & shortcut.IS_UNICODE
    assert flags & shortcut.HAS_LINK_INFO


def test_something_that_is_not_a_shortcut_is_refused(tmp_path):
    path = tmp_path / "not.lnk"
    path.write_bytes(b"just some bytes that are not a shell link at all")
    with pytest.raises(ValueError):
        shortcut.read_shortcut(path)


# ---------- finding what the install needs ----------

def test_the_newest_release_is_chosen(tmp_path):
    listing = (
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\trefs/tags/v0.9.9\n"
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\trefs/tags/v0.9.14\n"
        "cccccccccccccccccccccccccccccccccccccccc\trefs/tags/v0.9.14^{}\n"
        "dddddddddddddddddddddddddddddddddddddddd\trefs/heads/main\n")

    def run(command, **kwargs):
        return FakeCompleted(0, listing)

    # 0.9.14 beats 0.9.9: the parts are numbers, not text.
    assert installer.newest_release_tag(run=run) == "v0.9.14"


def test_no_release_is_reported_not_guessed():
    def run(command, **kwargs):
        return FakeCompleted(1, "", "could not read from remote")

    assert installer.newest_release_tag(run=run) == ""


def test_a_python_that_runs_every_feature_comes_first():
    """Manim publishes no wheels for 3.14, so the newest Python is not
    always the right one."""
    asked: list[list[str]] = []

    def run(command, **kwargs):
        asked.append(list(command))
        return FakeCompleted(0, "3.13\n")

    found = installer.find_python(run=run, which=lambda name: f"/bin/{name}")
    assert found == ["/bin/py", "-3.13"]
    assert asked[0][:2] == ["/bin/py", "-3.13"]


def test_a_python_too_old_is_passed_over():
    answers = iter(["3.9", "3.9", "3.9", "3.12"])

    def run(command, **kwargs):
        return FakeCompleted(0, next(answers) + "\n")

    found = installer.find_python(run=run, which=lambda name: f"/bin/{name}")
    assert found == ["/bin/python3"]


def test_pip_takes_the_bare_vcs_url():
    """PEP 508's "name @ git+url" reads to pip 24 as a file path."""
    value = installer.requirement("refs/tags/v1.2.3")
    assert value == (
        "git+https://github.com/tim-a-wood/sw-maintainer-agent.git@v1.2.3")


# ---------- the install ----------

def test_an_install_makes_the_launchers_the_icon_and_the_shortcuts(
        tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    root = tmp_path / "Maintain"
    runtime = installer.runtime_path(root)

    def run(command, **kwargs):
        if "venv" in command:
            runtime.parent.mkdir(parents=True, exist_ok=True)
            runtime.write_text("#!/bin/sh\n", encoding="utf-8")
            return FakeCompleted(0, "")
        if "pip" in command:
            return FakeCompleted(0, "Successfully installed")
        if any("sys.version_info" in part for part in command):
            return FakeCompleted(0, "3.13\n")
        return FakeCompleted(0, "0.9.14\n")

    report = installer.install("refs/tags/v0.9.14", root=root, run=run,
                               which=lambda name: f"/bin/{name}")

    assert report.ok, report.reason
    assert report.version == "0.9.14"
    # The console launcher and the window launcher.
    assert (root / "Maintain.cmd").is_file()
    assert (root / "Maintain-UI.cmd").is_file()
    assert (root / "maintain.ico").is_file()
    # The desktop icon the person asked for, pointing at the app.
    desktop = tmp_path / "home" / "Desktop" / "Maintain.lnk"
    assert desktop.is_file()
    read = shortcut.read_shortcut(desktop)
    # FR-V17: straight at the executable, so no cmd.exe sits between
    # the shortcut and the window.
    assert read["target"].endswith(installer.ui_executable())
    assert "maintain.ico,0" in " ".join(read["strings"])
    # And stamped with the identity the app sets on itself, or Windows
    # falls back to the Python launcher's icon.
    assert shortcut.read_app_id(desktop) == installer.APP_USER_MODEL_ID
    # And a Start Menu entry beside it.
    assert (tmp_path / "appdata" / "Microsoft" / "Windows" / "Start Menu"
            / "Programs" / "Maintain" / "Maintain.lnk").is_file()


def test_the_window_shortcut_does_not_open_a_terminal(tmp_path, monkeypatch):
    """The plain "Maintain" icon is the app. A console launcher that
    pauses on exit would leave a command window behind it."""
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    root = tmp_path / "Maintain"
    installer.write_launchers(root)

    ui = (root / "Maintain-UI.cmd").read_text(encoding="utf-8")
    assert "maintain-ui.exe" in ui
    assert "start " in ui
    assert "pause" not in ui
    console = (root / "Maintain.cmd").read_text(encoding="utf-8")
    assert "maintain.exe" in console


def test_a_missing_python_says_where_to_get_one(tmp_path):
    report = installer.install("refs/tags/v0.9.14", root=tmp_path / "x",
                               run=lambda *a, **k: FakeCompleted(1, ""),
                               which=lambda name: "")
    assert not report.ok
    assert "python.org" in report.reason


def test_a_missing_git_says_where_to_get_one(tmp_path):
    def which(name):
        return "" if name == "git" else f"/bin/{name}"

    report = installer.install("refs/tags/v0.9.14", root=tmp_path / "x",
                               run=lambda *a, **k: FakeCompleted(0, "3.12\n"),
                               which=which)
    assert not report.ok
    assert "git-scm.com" in report.reason


def test_a_failed_pip_is_reported_with_its_words(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    root = tmp_path / "Maintain"
    runtime = installer.runtime_path(root)

    def run(command, **kwargs):
        if "venv" in command:
            runtime.parent.mkdir(parents=True, exist_ok=True)
            runtime.write_text("#!/bin/sh\n", encoding="utf-8")
            return FakeCompleted(0, "")
        if "pip" in command:
            return FakeCompleted(1, "", "ERROR: no matching distribution")
        if any("sys.version_info" in part for part in command):
            return FakeCompleted(0, "3.13\n")
        return FakeCompleted(0, "0.9.14\n")

    report = installer.install("refs/tags/v0.9.14", root=root, run=run,
                               which=lambda name: f"/bin/{name}")
    assert not report.ok
    assert "no matching distribution" in report.reason


# ---------- the uninstall ----------

def test_the_uninstall_takes_the_files_and_the_shortcuts(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    root = tmp_path / "Maintain"
    (root / "venv").mkdir(parents=True)
    (root / "Maintain.cmd").write_text("x", encoding="utf-8")
    desktop = tmp_path / "home" / "Desktop"
    desktop.mkdir(parents=True)
    (desktop / "Maintain.lnk").write_bytes(b"x")
    (desktop / "Maintain Console.lnk").write_bytes(b"x")

    report = installer.uninstall(root=root)

    assert report.ok, report.reason
    assert not root.exists()
    assert not (desktop / "Maintain.lnk").exists()
    assert not (desktop / "Maintain Console.lnk").exists()


# ---------- the command line ----------

def test_the_command_line_reports_a_failure_and_waits(tmp_path):
    out = io.StringIO()
    waited: list[bool] = []
    code = installer.main(
        ["--install-root", str(tmp_path / "x"), "--reference", "refs/tags/v9.9.9",
         "--repository", str(tmp_path / "nothing")],
        stream=out, wait_for_reader=lambda: waited.append(True))
    assert code == 1
    assert waited == [True]
    assert "{ MAINTAIN }" in out.getvalue()


# ---------- what the shims must not do ----------

def test_the_batch_shims_never_call_powershell():
    """The whole point: a managed machine refuses an unsigned
    PowerShell script, and Bypass does not override that."""
    for name in ("install.cmd", "uninstall.cmd"):
        body = (SCRIPTS / name).read_text(encoding="utf-8").lower()
        assert "powershell" not in body, name
        assert "pwsh" not in body, name
        assert "install_maintain.py" in body, name


def test_the_installer_needs_only_the_standard_library():
    """It runs before Maintain exists, so it can import nothing from
    the package it is installing."""
    source = (SCRIPTS / "install_maintain.py").read_text(encoding="utf-8")
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            assert "maintain" not in stripped or "install_maintain" in stripped, (
                stripped)
    assert "shell=True" not in source


def test_the_shortcut_and_the_app_claim_the_same_identity():
    """FR-V17: the field fault — "the task bar icon still appears as the
    python window icon", after the artwork was already right.

    An app that calls SetCurrentProcessExplicitAppUserModelID gets its
    own taskbar button, and Windows then looks for a shortcut carrying
    that id to take the icon and the name from. With no match it uses
    the process image instead, which for a console script is the Python
    launcher stub. The two literals have to agree, and nothing but a
    test can hold them together: the installer runs before Maintain
    exists, so it cannot import the app's constant.
    """
    app_source = (ROOT / "src" / "maintain" / "ui" / "main.py").read_text(
        encoding="utf-8")
    assert f'"{installer.APP_USER_MODEL_ID}"' in app_source, (
        "the app sets a different id from the one the installer stamps")
    assert "SetCurrentProcessExplicitAppUserModelID" in app_source


def test_the_identity_survives_a_round_trip(tmp_path):
    path = shortcut.write_shortcut(
        tmp_path / "Maintain.lnk", r"C:\a\maintain-ui.exe",
        icon=r"C:\a\maintain.ico,0", description="Maintain",
        app_id="Maintain.SimpleUI")

    assert shortcut.read_app_id(path) == "Maintain.SimpleUI"
    # The rest of the link still reads, so the new block did not
    # displace the target or the icon.
    read = shortcut.read_shortcut(path)
    assert read["target"] == r"C:\a\maintain-ui.exe"
    assert r"C:\a\maintain.ico,0" in read["strings"]


def test_a_shortcut_without_an_identity_says_so(tmp_path):
    path = shortcut.write_shortcut(tmp_path / "plain.lnk", r"C:\a\b.exe")
    assert shortcut.read_app_id(path) == ""


def test_a_python_that_cannot_make_videos_says_so_at_install(
        tmp_path, monkeypatch):
    """FR-V18: the field fault — Explain failed at the render with
    "Manim needs Python 3.11 to 3.13; this computer runs 3.14", long
    after the install that chose that Python. The install knows, so
    the install says."""
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    root = tmp_path / "Maintain"
    runtime = installer.runtime_path(root)

    def run(command, **kwargs):
        if "venv" in command:
            runtime.parent.mkdir(parents=True, exist_ok=True)
            runtime.write_text("#!/bin/sh\n", encoding="utf-8")
            return FakeCompleted(0, "")
        if "pip" in command:
            return FakeCompleted(0, "Successfully installed")
        if any("sys.version_info" in part for part in command):
            # Only 3.14 on this machine, which is the reported shape.
            return FakeCompleted(0, "3.14\n")
        return FakeCompleted(0, "0.9.18\n")

    report = installer.install("refs/tags/v0.9.18", root=root, run=run,
                               which=lambda name: f"/bin/{name}")

    assert report.ok, report.reason
    said = " ".join(report.lines)
    assert "3.14" in said
    assert "video" in said
    assert "3.13" in said
    # And it does not send them to a PowerShell script.
    assert "setup.ps1" not in said


def test_no_message_sends_the_person_to_powershell():
    """FR-V18: the install is Python now. A message that names a
    PowerShell script is a dead end on the machine that reported this,
    whose policy refuses unsigned scripts."""
    from maintain.ui.strings import STR

    for key, value in STR.items():
        assert ".ps1" not in value, (key, value)

    render_source = (ROOT / "src" / "maintain" / "render.py").read_text(
        encoding="utf-8")
    # The only mention left is the comment that explains why.
    for line in render_source.splitlines():
        if ".ps1" in line:
            assert line.strip().startswith("#"), line
