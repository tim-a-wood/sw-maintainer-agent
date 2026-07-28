"""Headless end-to-end test of the desktop UI over the real engine.

The test plays Copilot: it reads each packet handoff and returns the reply
through the same screens the person would use.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import zipfile
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from maintain.config import ProjectConfig, default_config  # noqa: E402
from maintain.models import RunState  # noqa: E402
from maintain.ui.app import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    application = QApplication.instance() or QApplication([])
    yield application


def _git(repository: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repository), *args], check=True,
                   capture_output=True)


def _project(tmp_path: Path) -> ProjectConfig:
    repository = tmp_path / "project"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Maintain Test")
    _git(repository, "config", "user.email", "maintain@example.invalid")
    (repository / "app.py").write_text('VALUE = "before"\n', encoding="utf-8")
    _git(repository, "add", "app.py")
    _git(repository, "commit", "-m", "initial")
    data = default_config(repository, "manual-ui")
    data["audit"] = {"runtime_root": str(tmp_path / "runtime")}
    data["execution"]["minimum_free_disk_bytes"] = 1
    path = repository / ".maintain.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return ProjectConfig.load(path)


def wait_until(app, predicate, timeout=30.0, message="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(f"Timed out waiting for {message}")


def _screen(window: MainWindow) -> str:
    current = window.stack.currentWidget()
    for name, widget in window.screens.items():
        if widget is current:
            return name
    return "?"


def _scope_reply(handoff) -> str:
    request = handoff.request
    return json.dumps({
        "schema_version": 1, "run_id": request.run_id, "task_id": request.task_id,
        "role": "scope", "conversation_id": "chat-plan",
        "content": {"tasks": [{
            "id": "change-value", "objective": "Change the value",
            "allowed_files": ["app.py"],
            "done_when": ["VALUE is set to after."],
            "verification": ["Read app.py."], "depends_on": [],
        }]}})


def _build_zip(handoff, directory: Path) -> Path:
    request = handoff.request
    reply = directory / "maintain-output.zip"
    with zipfile.ZipFile(reply, "w") as archive:
        archive.writestr(
            "IMPLEMENTATION.toml",
            f'schema_version = 1\nrun_id = "{request.run_id}"\n'
            f'task_id = "{request.task_id}"\nrole = "implement"\n'
            'files = ["app.py"]\ndeleted_files = []\n')
        archive.writestr("files/app.py", 'VALUE = "after"\n')
    return reply


def _review_reply(handoff) -> str:
    request = handoff.request
    return json.dumps({
        "schema_version": 1, "run_id": request.run_id, "task_id": request.task_id,
        "role": "review", "conversation_id": "chat-review",
        "content": {"decision": "approve", "findings": []}})


def test_full_run_through_the_ui(qt_app, tmp_path, monkeypatch):
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    config = _project(tmp_path)
    window = MainWindow(config)
    window.ask_confirm = lambda *args, **kwargs: True
    window.ask_note = lambda *args, **kwargs: "Note."
    errors: list[str] = []
    window.show_error = errors.append
    reference = tmp_path / "notes.md"
    reference.write_text("# Notes\n", encoding="utf-8")
    window.pick_files = lambda: [str(reference)]

    assert _screen(window) == "home"

    # Describe with one run file.
    window.home.new_change.emit("feature")
    assert _screen(window) == "describe"
    window.describe.request_edit.setPlainText("Change the value to after.")
    window.describe.add_files([reference])
    window.describe._start()

    # Plan packet arrives.
    wait_until(qt_app, lambda: _screen(window) == "send", message="plan packet")
    handoff = window.current_handoff
    assert handoff.task_key == "plan"
    assert handoff.zip_path.is_file()
    with zipfile.ZipFile(handoff.zip_path) as archive:
        names = set(archive.namelist())
        assert "attachments/notes.md" in names
        assert {"TASK.md", "GLOBAL.md", "CODEBASE.md", "MANIFEST.json"} <= names

    # Add one more attachment on the Send screen; the packet is rebuilt.
    extra = tmp_path / "extra.txt"
    extra.write_text("extra", encoding="utf-8")
    window._add_packet_files([extra])
    with zipfile.ZipFile(handoff.zip_path) as archive:
        assert "attachments/extra.txt" in set(archive.namelist())

    # A wrong reply is refused and the screen stays open.
    window.send.continue_button.setEnabled(True)
    window.send.continue_clicked.emit()
    assert _screen(window) == "receive"
    window.receive.check(clipboard_text="this is not json")
    assert _screen(window) == "receive"
    assert window.receive.status.text()

    # The valid plan reply advances to the plan gate.
    window.receive.check(clipboard_text=_scope_reply(handoff))
    wait_until(qt_app, lambda: _screen(window) == "plan", message="plan gate")
    window.plan_check.accept.emit()

    # Build packet: reply with the implementation ZIP.
    wait_until(qt_app, lambda: _screen(window) == "send"
               and window.current_handoff.task_key == "build",
               message="build packet")
    build_handoff = window.current_handoff
    window.send.continue_button.setEnabled(True)
    window.send.continue_clicked.emit()
    window.receive.check(path=_build_zip(build_handoff, tmp_path))

    # Review packet: approve.
    wait_until(qt_app, lambda: _screen(window) == "send"
               and window.current_handoff.task_key == "review",
               message="review packet")
    review_handoff = window.current_handoff
    window.send.continue_button.setEnabled(True)
    window.send.continue_clicked.emit()
    window.receive.check(clipboard_text=_review_reply(review_handoff))

    # Checks pass and the Save screen appears.
    wait_until(qt_app, lambda: _screen(window) == "save", message="save screen")
    record = window.current_record
    assert RunState(record.state) is RunState.AWAITING_ACCEPTANCE
    assert "app.py" in window.save.files.text()

    # Go back to the build iteration; the review runs again with a new packet.
    window._open_live_timeline()
    assert _screen(window) == "run"
    build_anchor = next(item for item in window.controller.timeline(record.run_id)
                        if item.kind == "build_applied")
    window._go_back_to(build_anchor.sequence)
    wait_until(qt_app, lambda: errors or (_screen(window) == "send"
               and window.current_handoff.task_key == "review"),
               message="review packet after go-back")
    assert not errors, errors
    second_review = window.current_handoff
    window.send.continue_button.setEnabled(True)
    window.send.continue_clicked.emit()
    window.receive.check(clipboard_text=_review_reply(second_review))
    wait_until(qt_app, lambda: _screen(window) == "save", message="save again")
    record = window.current_record
    revert_timeline = window.controller.timeline(record.run_id)
    assert any(item.kind == "revert" for item in revert_timeline)
    assert any(item.superseded for item in revert_timeline)

    # Accept and save: the run delivers and the Done screen appears.
    window.save.accept.emit()
    wait_until(qt_app, lambda: errors or _screen(window) == "done",
               message="done screen")
    assert not errors, errors
    assert "maintain/" in window.done.branch.text()

    # History lists the run; its timeline is read-only.
    window.done.open_history.emit()
    assert _screen(window) == "history"
    runs = window.controller.runs()
    assert runs and runs[0].display_state == "Saved"
    window._open_run(runs[0].run_id)
    assert _screen(window) == "run"
    assert not window.run_detail.undo_button.isVisible()

    timeline = window.controller.timeline(runs[0].run_id)
    kinds = [item.kind for item in timeline]
    for expected in ("start", "plan_proposed", "plan_approved", "build_applied",
                     "review_approved", "checks_passed", "saved"):
        assert expected in kinds

    worktree_file = Path(record.worktree) / "app.py"
    assert worktree_file.read_text(encoding="utf-8") == 'VALUE = "after"\n'


def test_diff_line_kinds_cover_headers_changes_and_ambiguity():
    from maintain.ui.widgets import diff_line_kind
    assert diff_line_kind("diff --git a/x.py b/x.py") == "header"
    assert diff_line_kind("index 3f1c2aa..9d0b1ef 100644") == "header"
    assert diff_line_kind("--- a/src/loader.py") == "header"
    assert diff_line_kind("+++ b/src/loader.py") == "header"
    assert diff_line_kind("--- /dev/null") == "header"
    assert diff_line_kind("+++ /dev/null") == "header"
    assert diff_line_kind("@@ -39,6 +39,13 @@ def load_wind(records):") == "header"
    assert diff_line_kind("new file mode 100644") == "header"
    assert diff_line_kind("+    filtered.append(r)") == "added"
    assert diff_line_kind("-    return [r for r in records]") == "removed"
    # A removed SQL comment produces three dashes; it is not a file header.
    assert diff_line_kind("--- select comment") == "removed"
    assert diff_line_kind("+++ added plus line") == "added"
    assert diff_line_kind("     context line") == "context"
    assert diff_line_kind("") == "context"


def test_save_screen_diff_view_highlights(qt_app, tmp_path, monkeypatch):
    from PySide6.QtGui import QTextCursor
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    config = _project(tmp_path)
    window = MainWindow(config)
    diff = ("diff --git a/app.py b/app.py\n"
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -1 +1 @@\n"
            '-VALUE = "before"\n'
            '+VALUE = "after"\n')
    window.save.diff_view.setPlainText(diff)
    qt_app.processEvents()
    document = window.save.diff_view.document()

    def line_color(number: int) -> str:
        ranges = document.findBlockByNumber(number).layout().formats()
        assert ranges, f"line {number} has no highlight"
        line_format = ranges[0].format
        return line_format.foreground().color().name()

    assert line_color(4) == "#ff9b8f"   # removed
    assert line_color(5) == "#7ce0a3"   # added
    assert line_color(0) == "#8fb8e8"   # file header
    assert line_color(3) == "#8fb8e8"   # hunk header


def test_theme_defaults_dark_toggles_and_persists(qt_app, tmp_path, monkeypatch):
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    config = _project(tmp_path)
    window = MainWindow(config)
    assert window._theme == "dark"
    assert window.foot_theme.text() == "Light mode"
    window.toggle_theme()
    assert window._theme == "light"
    assert window.foot_theme.text() == "Dark mode"
    from maintain.repository_memory import load_ui_settings
    assert load_ui_settings()["theme"] == "light"
    second = MainWindow(config)
    assert second._theme == "light"
    second.toggle_theme()
    assert load_ui_settings()["theme"] == "dark"


def test_projects_screen_lists_creates_switches_and_removes(
        qt_app, tmp_path, monkeypatch):
    from maintain.repository_memory import remember_repository
    from maintain.ui.strings import text as ui_text
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()

    config = _project(tmp_path)
    remember_repository(config.repository)
    window = MainWindow(config)
    toasts: list[str] = []
    window.toast = toasts.append
    window.ask_confirm = lambda *args, **kwargs: True

    # The home screen offers Projects; the list shows the active project.
    window.home.open_projects.emit()
    assert _screen(window) == "projects"
    assert window.projects._rows.count() == 1

    # New project: a plain folder, no source control.
    parent = tmp_path / "space"
    parent.mkdir()
    window.pick_directory = lambda: str(parent)
    window.ask_text = lambda *args, **kwargs: "fresh"
    window._new_project()
    created = parent / "fresh"
    assert created.is_dir()
    assert not (created / ".git").exists()
    assert window.projects._rows.count() == 2

    # A folder without source control does not open.
    window._open_project(str(created))
    assert window.store.config.repository == config.repository
    assert toasts[-1] == ui_text("projects.no_git.open")

    # A missing folder does not open.
    window._open_project(str(tmp_path / "nowhere"))
    assert toasts[-1] == ui_text("projects.missing.open")

    # A Git folder without configuration is set up after the confirm; it opens.
    second = tmp_path / "second"
    second.mkdir()
    _git(second, "init", "-b", "main")
    window._open_project(str(second))
    assert (second / ".maintain.json").is_file()
    assert window.store.config.repository == second.resolve()
    assert _screen(window) == "home"
    assert toasts[-1] == ui_text("projects.opened", name="second")

    # The refreshed list has all three; remove keeps the files on disk.
    window.show_projects()
    assert window.projects._rows.count() == 3
    window._remove_project(str(created))
    assert window.projects._rows.count() == 2
    assert created.is_dir()


def test_stop_pauses_and_home_offers_continue(qt_app, tmp_path, monkeypatch):
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    config = _project(tmp_path)
    window = MainWindow(config)
    window.ask_confirm = lambda *args, **kwargs: True

    window.home.new_change.emit("feature")
    window.describe.request_edit.setPlainText("Change the value to after.")
    window.describe._start()
    wait_until(qt_app, lambda: _screen(window) == "send", message="plan packet")

    window._stop_run()
    wait_until(qt_app, lambda: _screen(window) == "home", message="home after stop")
    summary = window.controller.resumable_run()
    assert summary is not None
    assert summary.state == str(RunState.NEEDS_HUMAN)

    # Continue resumes the run and asks for the plan packet again.
    window._continue_run(summary.run_id)
    wait_until(qt_app, lambda: _screen(window) == "send", message="resumed packet")
    assert window.current_handoff.task_key == "plan"
    window.controller.stop()
    wait_until(qt_app, lambda: not window.controller.busy, message="pause settled")
