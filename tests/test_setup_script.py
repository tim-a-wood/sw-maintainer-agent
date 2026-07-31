"""The install logic that failed on a real Windows computer once:
Python 3.14 has no Manim wheels, and the old script died instead of
installing the app without the video feature."""

from __future__ import annotations

import runpy
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

setup = types.SimpleNamespace(
    **runpy.run_path(str(ROOT / "scripts" / "setup.py")))


class FakeRunner:
    """Scripted command outcomes, recorded calls."""

    def __init__(self, outcomes=None, tools=("py", "ffmpeg", "winget")):
        self.outcomes = outcomes or {}
        self.tools = set(tools)
        self.calls: list[list[str]] = []

    def run(self, argv, capture=False):
        self.calls.append(list(argv))
        for marker, (code, stdout) in self.outcomes.items():
            if marker in " ".join(argv):
                return types.SimpleNamespace(returncode=code, stdout=stdout)
        return types.SimpleNamespace(returncode=0, stdout="")

    def which(self, name):
        return f"C:\\tools\\{name}.exe" if name in self.tools else None


PY_LIST_NEW = """\
 -V:3.14 *       C:\\Users\\tim\\AppData\\Local\\Programs\\Python\\Python314\\python.exe
 -V:3.13         C:\\Users\\tim\\AppData\\Local\\Programs\\Python\\Python313\\python.exe
 -V:3.12         C:\\Python312\\python.exe
"""

PY_LIST_OLD = """\
 -3.12-64 *      C:\\Python312\\python.exe
 -3.10-64        C:\\Python310\\python.exe
"""


def test_parse_py_list_reads_both_launcher_formats():
    new = setup.parse_py_list(PY_LIST_NEW)
    assert [item.version for item in new] == [(3, 14), (3, 13), (3, 12)]
    assert new[1].path.endswith("Python313\\python.exe")
    old = setup.parse_py_list(PY_LIST_OLD)
    assert [item.version for item in old] == [(3, 12), (3, 10)]
    assert setup.parse_py_list("no interpreters here") == []


def test_choose_interpreter_prefers_the_newest_that_supports_manim():
    listed = setup.parse_py_list(PY_LIST_NEW)
    chosen = setup.choose_interpreter((3, 14), listed)
    assert chosen is not None and chosen.version == (3, 13)
    # A current interpreter that already supports Manim stays in charge.
    assert setup.choose_interpreter((3, 12), listed) is None
    # Only unsupported versions installed: stay on the current one.
    assert setup.choose_interpreter((3, 14), setup.parse_py_list(
        " -V:3.14 * C:\\P\\python.exe")) is None
    # 3.10 is below the app's own minimum; never chosen.
    assert setup.choose_interpreter((3, 14), setup.parse_py_list(
        " -3.10-64 C:\\P\\python.exe")) is None


def test_main_installs_with_a_supported_interpreter_when_available(capsys):
    runner = FakeRunner(outcomes={
        "-0p": (0, PY_LIST_NEW),
        "show manim": (0, "Name: manim\nVersion: 0.20.1\n")})
    assert setup.main(runner, current=(3, 14)) == 0
    out = capsys.readouterr().out
    assert "the video feature works" in out
    assert "PASS: Maintain is installed" in out
    assert "PASS: Manim 0.20.1" in out
    install = next(call for call in runner.calls if "install" in call
                   and "--force" in call)
    assert install[0].endswith("Python313\\python.exe")
    assert install[-1].endswith("[ui,explain]")


def test_main_explains_when_only_python_314_exists(capsys):
    runner = FakeRunner(outcomes={
        "-0p": (0, " -V:3.14 * C:\\P314\\python.exe"),
        "show manim": (1, "")})
    assert setup.main(runner, current=(3, 14)) == 0
    out = capsys.readouterr().out
    assert "needs Python 3.11 to 3.13" in out
    assert "Install Python 3.13" in out
    assert "The video feature is off" in out


def test_main_falls_back_to_the_app_without_the_video_feature(capsys):
    runner = FakeRunner(outcomes={
        "-0p": (0, PY_LIST_OLD),
        "[ui,explain]": (1, ""),
        "show manim": (1, "")})
    assert setup.main(runner, current=(3, 12)) == 0
    out = capsys.readouterr().out
    assert "without the video feature" in out
    retries = [call for call in runner.calls if call[-1].endswith("[ui]")]
    assert retries, "the ui-only retry never ran"


def test_main_fails_plainly_when_nothing_installs(capsys):
    runner = FakeRunner(outcomes={
        "-0p": (0, PY_LIST_OLD),
        "[ui,explain]": (1, ""), "[ui]": (1, "")})
    assert setup.main(runner, current=(3, 12)) == 1
    assert "FAIL: The Maintain install failed" in capsys.readouterr().out


def test_main_rejects_an_old_python(capsys):
    assert setup.main(FakeRunner(), current=(3, 10)) == 1
    assert "too old" in capsys.readouterr().out


def test_main_warns_when_ffmpeg_and_winget_are_absent(capsys):
    runner = FakeRunner(tools=("py",), outcomes={
        "-0p": (0, PY_LIST_OLD), "show manim": (1, "")})
    assert setup.main(runner, current=(3, 12)) == 0
    assert "ffmpeg and winget are absent" in capsys.readouterr().out


def test_explain_extra_is_gated_below_python_314():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "manim==0.20.1; python_version < '3.14'" in pyproject


def test_setup_ps1_only_bootstraps_the_python_script():
    script = (ROOT / "scripts" / "setup.ps1").read_text(encoding="utf-8")
    assert "setup.py" in script
    assert "exit $LASTEXITCODE" in script
    assert "pipx install" not in script   # no decisions left in PowerShell


def test_render_message_names_the_python_314_cause():
    from maintain.render import _absent_message
    newer = _absent_message("manim", version=(3, 14))
    assert "Python 3.11 to 3.13" in newer and "3.14" in newer
    supported = _absent_message("manim", version=(3, 12))
    assert "pip install maintain[explain]" in supported


def test_windows_setup_places_a_start_menu_shortcut(capsys):
    runner = FakeRunner(outcomes={
        "-0p": (0, PY_LIST_OLD),
        "PIPX_BIN_DIR": (0, "C:\\Users\\tim\\.local\\bin\n"),
        "show manim": (0, "Version: 0.20.1\n")})
    assert setup.main(runner, current=(3, 12), platform="win32") == 0
    out = capsys.readouterr().out
    assert "PASS: Maintain is in the Start Menu" in out
    shortcut = next(call for call in runner.calls if call[0] == "powershell")
    command = shortcut[-1]
    assert "maintain-ui.exe" in command and "Maintain.lnk" in command


def test_setup_skips_the_shortcut_off_windows(capsys):
    runner = FakeRunner(outcomes={"-0p": (0, PY_LIST_OLD),
                                  "show manim": (1, "")})
    assert setup.main(runner, current=(3, 12), platform="linux") == 0
    out = capsys.readouterr().out
    assert "no shortcut is made" in out
    assert not any(call[0] == "powershell" for call in runner.calls)
