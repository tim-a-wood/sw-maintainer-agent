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
    toasts: list[str] = []
    window.toast = toasts.append
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
    wait_until(qt_app, lambda: _screen(window) == "exchange", message="plan packet")
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

    # One screen, two visible regions, no Continue gate.
    from PySide6.QtWidgets import QFrame
    assert window.exchange.findChild(QFrame, "SendRegion") is not None
    assert window.exchange.findChild(QFrame, "ReceiveRegion") is not None
    assert not hasattr(window.exchange, "continue_button")

    # A wrong reply is refused and the screen stays open.
    assert _screen(window) == "exchange"
    window.exchange.check(clipboard_text="this is not json")
    assert _screen(window) == "exchange"
    assert window.exchange.status.text()

    # The plan reply arrives as a downloaded Markdown file with a fenced
    # envelope; the newest-download path finds and accepts it.
    from maintain.repository_memory import load_ui_settings, save_ui_settings
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    values = load_ui_settings()
    values["downloads_path"] = str(downloads)
    save_ui_settings(values)
    window._open_newest_download()
    assert "No new file" in window.exchange.status.text()
    (downloads / "old-notes.md").write_text("stale", encoding="utf-8")
    os.utime(downloads / "old-notes.md", (1000000, 1000000))
    reply_file = downloads / "maintain-reply.md"
    reply_file.write_text(
        "Here is the reply.\n\n```json\n" + _scope_reply(handoff) + "\n```\n",
        encoding="utf-8")
    window._open_newest_download()
    wait_until(qt_app, lambda: _screen(window) == "plan", message="plan gate")
    assert any("plan is in" in item for item in toasts)
    window.plan_check.accept.emit()

    # Build packet: reply with the implementation ZIP.
    wait_until(qt_app, lambda: _screen(window) == "exchange"
               and window.current_handoff.task_key == "build",
               message="build packet")
    build_handoff = window.current_handoff
    window.exchange.check(path=_build_zip(build_handoff, tmp_path))

    # Review packet: the reply arrives while the person is on another
    # screen; the window catches it (FR-G1).
    wait_until(qt_app, lambda: _screen(window) == "exchange"
               and window.current_handoff.task_key == "review",
               message="review packet")
    review_handoff = window.current_handoff
    window.show_history()
    assert _screen(window) == "history"
    assert window.exchange.reply_open
    window._route_reply(clipboard_text=_review_reply(review_handoff))

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
    wait_until(qt_app, lambda: errors or (_screen(window) == "exchange"
               and window.current_handoff.task_key == "review"),
               message="review packet after go-back")
    assert not errors, errors
    second_review = window.current_handoff
    window.exchange.check(clipboard_text=_review_reply(second_review))
    wait_until(qt_app, lambda: _screen(window) == "save", message="save again")
    record = window.current_record
    revert_timeline = window.controller.timeline(record.run_id)
    assert any(item.kind == "revert" for item in revert_timeline)
    assert any(item.superseded for item in revert_timeline)

    # Accept and save: the run delivers and the Done screen lands the win.
    window.save.accept.emit()
    wait_until(qt_app, lambda: errors or _screen(window) == "done",
               message="done screen")
    assert not errors, errors
    assert "maintain/" in window.done.branch.text()
    assert "1 file changed" in window.done.stat_files.text()
    assert "app.py" in window.done.file_names.text()
    assert "iterations" in window.done.stat_line.text()
    window.done._copy_merge()
    assert QApplication.clipboard().text().startswith("git merge --no-ff ")
    assert window.done.first_note.isVisibleTo(window.done)
    window.done._copy_note()
    note = QApplication.clipboard().text()
    assert note.startswith("Saved: Change the value to after.")
    assert "app.py" in note and "Branch: maintain/" in note
    window.done.explain_change.emit()
    assert _screen(window) == "explain"
    assert "Explain this change" in window.explain.goal_edit.toPlainText()
    assert window.explain.files and window.explain.files[0].name == "app.py"
    window.show_home()
    assert window.home.momentum.isVisibleTo(window.home)
    assert "1 saved change" in window.home.momentum.text()
    window.home.new_change.emit("feature")
    assert window.describe._recent_holder.isVisibleTo(window.describe)

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


def test_issue_crud_from_the_screens(qt_app, tmp_path, monkeypatch):
    from maintain.ui.strings import text as ui_text
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    config = _project(tmp_path)
    window = MainWindow(config)
    window.ask_confirm = lambda *args, **kwargs: True

    window.home.open_issues.emit()
    assert _screen(window) == "issues"
    assert window.issues_list.empty.isVisibleTo(window.issues_list)

    # Add: empty title refuses, a titled issue saves.
    window._new_issue()
    assert _screen(window) == "issue"
    window._save_issue()
    assert window.issue_detail.message.text() == ui_text("issue.title.empty")
    window.issue_detail.title_edit.setText("The loader accepts bad speeds")
    window.issue_detail.radio_high.setChecked(True)
    window.issue_detail.detail_edit.setPlainText("Reject speeds below zero.")
    window._save_issue()
    issue = window.controller.issues.load()[0]
    assert issue.severity == "high" and issue.source == "human"
    assert window.issue_detail.issue_id == issue.id

    # The home card counts the open issue.
    window.show_home()
    assert "1" in window.home._issues_card.sub_label.text()

    # Close with a reason, reopen, then remove for good.
    window._close_issue_with(issue.id, "wont_fix")
    closed = window.controller.issues.get(issue.id)
    assert closed.status == "closed" and closed.closed_reason == "wont_fix"
    window._reopen_issue(issue.id)
    assert window.controller.issues.get(issue.id).status == "open"
    window._remove_issue(issue.id)
    assert window.controller.issues.load() == []


def test_repair_bridge_prefills_and_links_the_run(qt_app, tmp_path, monkeypatch):
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    config = _project(tmp_path)
    window = MainWindow(config)
    issue = window.controller.issues.add(
        title="The value is wrong", detail="It must be after.",
        file="app.py", line=1, snippet='VALUE = "before"')

    window._repair_issue(issue.id)
    assert _screen(window) == "describe"
    described = window.describe.request_edit.toPlainText()
    assert "The value is wrong" in described and "app.py:1" in described
    assert window.describe.mode == "issue"

    window.describe._start()
    wait_until(qt_app, lambda: _screen(window) == "exchange", message="scan packet")
    linked = window.controller.issues.get(issue.id)
    assert window.current_handoff.request.run_id in linked.runs
    assert linked.status == "in_work"
    window.controller.stop()
    wait_until(qt_app, lambda: not window.controller.busy, message="paused")

    # FR-G4: the issue shows its linked run; one click opens the timeline.
    window._open_issue(issue.id)
    assert window.issue_detail.run_button.isVisibleTo(window.issue_detail)
    assert linked.runs[-1] in window.issue_detail.run_button.text()
    window.issue_detail.open_run.emit(linked.runs[-1])
    assert _screen(window) == "run"


def _side_envelope(window, content: dict) -> str:
    request = window._side["exchange"].request
    return json.dumps({
        "schema_version": 1, "run_id": request.run_id,
        "task_id": request.task_id, "role": request.role,
        "conversation_id": "chat-side", "content": content})


def test_scan_flow_gate_dedup_and_accept(qt_app, tmp_path, monkeypatch):
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    config = _project(tmp_path)
    window = MainWindow(config)
    window.ask_note = lambda *args, **kwargs: "look at app.py"
    toasts: list[str] = []
    window.toast = toasts.append

    window._start_scan()
    assert _screen(window) == "exchange"
    assert window._side is not None
    assert window.current_handoff.task_key == "scan"
    with zipfile.ZipFile(window.current_handoff.zip_path) as archive:
        assert {"TASK.md", "GLOBAL.md", "CODEBASE.md"} <= set(archive.namelist())

    # FR-P7: the focus field sits on the exchange screen; no dialog opened.
    assert window.exchange._focus_holder.isVisibleTo(window.exchange)
    window.exchange.focus_edit.setText("look at the loader")
    window.exchange._emit_focus()
    assert window._side["exchange"].request.payload["request"] == (
        "look at the loader")
    assert window.current_handoff.task_key == "scan"

    # A dragged reference file lands in the packet.
    sheet = tmp_path / "tracker.csv"
    sheet.write_text("ref,summary\nT-9,Old fault\n", encoding="utf-8")
    window._add_packet_files([sheet])
    with zipfile.ZipFile(window.current_handoff.zip_path) as archive:
        assert "attachments/tracker.csv" in set(archive.namelist())

    assert _screen(window) == "exchange"
    reply = _side_envelope(window, {"issues": [
        {"title": "The value is wrong", "severity": "high", "file": "app.py",
         "line": 1, "snippet": 'VALUE = "before"',
         "detail": "It must be after.", "external_ref": "T-9"},
        {"title": "Invented point", "severity": "low", "file": "app.py",
         "line": 2, "snippet": "not_in_the_file()", "detail": ""},
    ]})
    window.exchange.check(clipboard_text=reply)
    assert _screen(window) == "scan-check"
    boxes = window.scan_check._boxes
    assert len(boxes) == 2
    assert boxes[0].isChecked() and not boxes[1].isChecked()

    window._scan_accept([0])
    assert _screen(window) == "issues"
    issues = window.controller.issues.load()
    assert len(issues) == 1 and issues[0].source == "scan"
    assert issues[0].external_ref == "T-9"

    # The same finding again is dropped before the gate.
    window._start_scan()
    reply = _side_envelope(window, {"issues": [
        {"title": "The value is wrong again", "severity": "high",
         "file": "app.py", "line": 1, "snippet": 'VALUE = "before"',
         "detail": ""}]})
    window.exchange.check(clipboard_text=reply)
    assert _screen(window) == "issues"
    assert len(window.controller.issues.load()) == 1
    assert any("1" in item for item in toasts)


def test_discuss_flow_notes_and_severity_confirm(qt_app, tmp_path, monkeypatch):
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    config = _project(tmp_path)
    window = MainWindow(config)
    window.ask_confirm = lambda *args, **kwargs: True
    issue = window.controller.issues.add(
        title="The bound is wrong", severity="medium",
        file="app.py", line=1, snippet='VALUE = "before"')

    # The question comes from the inline panel on the issue screen.
    window._open_issue(issue.id)
    window.issue_detail.note_panel.open("question")
    window.issue_detail.note_panel.edit.setPlainText("Is medium right?")
    window.issue_detail.note_panel._send()
    assert _screen(window) == "exchange"
    assert window.current_handoff.task_key == "discuss"
    reply = _side_envelope(window, {
        "reply": "No. The bound loses data, so high is right.",
        "severity": "high"})
    window.exchange.check(clipboard_text=reply)

    assert _screen(window) == "issue"
    final = window.controller.issues.get(issue.id)
    assert [note["author"] for note in final.notes] == ["you", "copilot"]
    assert final.severity == "high"
    assert window._side is None


SCENE_REPLY = (
    "Here is the scene.\n\n"
    "```python\n"
    "from manim import Scene, Text, FadeIn\n"
    "\n"
    "\n"
    "class DemoScene(Scene):\n"
    "    def construct(self):\n"
    '        self.play(FadeIn(Text("app.py")))\n'
    "```\n")


def _shell_stub(path, body: str) -> str:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def test_explain_flow_render_repair_and_settings(qt_app, tmp_path, monkeypatch):
    from maintain.repository_memory import load_ui_settings, save_ui_settings
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    config = _project(tmp_path)
    window = MainWindow(config)
    toasts: list[str] = []
    window.toast = toasts.append
    errors: list[str] = []
    window.show_error = errors.append

    fail_stub = _shell_stub(tmp_path / "fail-manim",
                            'echo "Boom on line 3" 1>&2\nexit 1\n')
    values = load_ui_settings()
    values["manim_command"] = fail_stub
    save_ui_settings(values)

    # A file outside the project is refused with a plain message.
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    window._start_explain([outside], "goal", "")
    assert "outside" in toasts[-1]

    # The input screen refuses an empty goal, then builds the packet.
    window.show_explain()
    assert _screen(window) == "explain"
    window.explain._start()
    assert window.explain.message.text()
    window.explain.goal_edit.setPlainText("Explain the value bound.")
    window.explain.add_files([config.repository / "app.py"])
    window.explain._start()
    assert not errors, errors
    assert _screen(window) == "exchange"
    assert window.current_handoff.task_key == "explain"
    with zipfile.ZipFile(window.current_handoff.zip_path) as archive:
        assert "one fenced code block" in archive.read("TASK.md").decode()

    # A reply without a code block is refused on the Receive screen.
    window.exchange.check(clipboard_text="no code here")
    assert _screen(window) == "exchange"

    # The valid scene reply starts the render; the failing stub reports.
    window.exchange.check(clipboard_text=SCENE_REPLY)
    assert _screen(window) == "explain-result"
    wait_until(qt_app, lambda: window.explain_result.render_chip.text() == "FAIL",
               message="failed render")
    assert "Boom on line 3" in window.explain_result.tail_view.toPlainText()
    assert "BEATS" in window.explain_result.tail_view.toPlainText()
    assert window.explain_result.repair_button.isVisibleTo(window.explain_result)

    # Repair: the new packet carries the error; a good stub then passes.
    pass_stub = _shell_stub(
        tmp_path / "pass-manim",
        'mkdir -p media/videos/scene/1080p60\n'
        'echo video > "media/videos/scene/1080p60/$3.mp4"\n')
    values = load_ui_settings()
    values["manim_command"] = pass_stub
    save_ui_settings(values)
    window._repair_explain()
    assert _screen(window) == "exchange"
    request = window._side["exchange"].request
    assert "Boom on line 3" in request.payload["render_error"]
    assert request.payload["previous_scene"].startswith("from manim")
    window.exchange.check(clipboard_text=SCENE_REPLY)
    wait_until(qt_app, lambda: window.explain_result.render_chip.text() == "PASS",
               message="passed render")
    video = window._explain["video"]
    assert video is not None and Path(video).is_file()
    assert Path(window._explain["dir"], "render", "scene.py").is_file()

    # The settings page stores the per-user Manim command.
    window._open_settings_page("explain")
    assert _screen(window) == "set-explain"
    window.page_explain.command_edit.setText("my-manim")
    window._explain_settings_saved()
    assert load_ui_settings()["manim_command"] == "my-manim"
    assert not errors, errors


def test_stop_pauses_and_home_offers_continue(qt_app, tmp_path, monkeypatch):
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    config = _project(tmp_path)
    window = MainWindow(config)
    window.ask_confirm = lambda *args, **kwargs: True

    window.home.new_change.emit("feature")
    window.describe.request_edit.setPlainText("Change the value to after.")
    window.describe._start()
    wait_until(qt_app, lambda: _screen(window) == "exchange", message="plan packet")

    window._stop_run()
    wait_until(qt_app, lambda: _screen(window) == "home", message="home after stop")
    summary = window.controller.resumable_run()
    assert summary is not None
    assert summary.state == str(RunState.NEEDS_HUMAN)

    # Continue resumes the run and asks for the plan packet again.
    window._continue_run(summary.run_id)
    wait_until(qt_app, lambda: _screen(window) == "exchange", message="resumed packet")
    assert window.current_handoff.task_key == "plan"
    window.controller.stop()
    wait_until(qt_app, lambda: not window.controller.busy, message="pause settled")


def test_flaky_check_retry_reruns_and_passes(qt_app, tmp_path, monkeypatch):
    """FR-P5: Run the checks again from the failure screen, no repair round."""
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    repository = tmp_path / "project"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "T")
    _git(repository, "config", "user.email", "t@example.invalid")
    (repository / "app.py").write_text('VALUE = "before"\n', encoding="utf-8")
    marker = tmp_path / "flaky-marker"
    (repository / "flaky.py").write_text(
        "import pathlib, sys\n"
        "marker = pathlib.Path(sys.argv[1])\n"
        "if marker.exists():\n"
        "    sys.exit(0)\n"
        "marker.write_text('seen')\n"
        "sys.exit(1)\n", encoding="utf-8")
    _git(repository, "add", "-A")
    _git(repository, "commit", "-m", "initial")
    data = default_config(repository, "manual-ui")
    data["audit"] = {"runtime_root": str(tmp_path / "runtime")}
    data["execution"]["minimum_free_disk_bytes"] = 1
    data["verification"]["commands"] = {
        "flaky": {"argv": ["python3", "flaky.py", str(marker)],
                  "phase": "verify", "timeout_seconds": 60}}
    path = repository / ".maintain.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    config = ProjectConfig.load(path)

    window = MainWindow(config)
    errors: list[str] = []
    window.show_error = errors.append

    window.home.new_change.emit("feature")
    window.describe.request_edit.setPlainText("Change the value to after.")
    window.describe._start()
    wait_until(qt_app, lambda: _screen(window) == "exchange", message="plan packet")
    window.exchange.check(clipboard_text=_scope_reply(window.current_handoff))
    wait_until(qt_app, lambda: _screen(window) == "plan", message="plan gate")
    window.plan_check.accept.emit()
    wait_until(qt_app, lambda: _screen(window) == "exchange"
               and window.current_handoff.task_key == "build",
               message="build packet")
    window.exchange.check(path=_build_zip(window.current_handoff, tmp_path))
    wait_until(qt_app, lambda: _screen(window) == "exchange"
               and window.current_handoff.task_key == "review",
               message="review packet")
    window.exchange.check(clipboard_text=_review_reply(window.current_handoff))

    # The flaky check fails once; the failure screen offers the retry.
    wait_until(qt_app, lambda: _screen(window) == "test"
               and window.test.retry_button.isVisibleTo(window.test),
               message="failure screen")
    assert marker.is_file()
    window.test.retry.emit()

    # The retry runs the checks again in the same workspace; they pass.
    wait_until(qt_app, lambda: _screen(window) == "save", message="save screen",
               timeout=60.0)
    assert not errors, errors


def test_enter_and_escape_drive_the_screen_keys(qt_app, tmp_path, monkeypatch):
    """FR-P9: Enter fires the primary action; Esc goes back."""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    config = _project(tmp_path)
    window = MainWindow(config)

    fired: list[str] = []
    window.plan_check.set_keys(lambda: fired.append("accept"),
                               lambda: fired.append("back"))
    enter = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return,
                      Qt.KeyboardModifier.NoModifier)
    window.plan_check.keyPressEvent(enter)
    escape = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                       Qt.KeyboardModifier.NoModifier)
    window.plan_check.keyPressEvent(escape)
    assert fired == ["accept", "back"]

    # Esc from the issues list goes home (wired by the application).
    window.show_issues()
    window.issues_list.keyPressEvent(escape)
    assert _screen(window) == "home"


def test_toasts_notes_hints_and_round_context(qt_app, tmp_path, monkeypatch):
    """E1-E6: chips, inline notes, checks hint, round label, audience."""
    from maintain.repository_memory import load_ui_settings, save_ui_settings
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    config = _project(tmp_path)
    window = MainWindow(config)

    # E1: toasts are chips now, at most two.
    window.toast("The plan is in.")
    window.toast("Added 1 to the issue list.")
    window.toast("The review is in.")
    assert window._toasts.count() == 2

    # E2: the note panel keeps text on cancel and clears after send.
    panel = window.plan_check.note_panel
    panel.open("What must change?")
    panel.edit.setPlainText("Split the task.")
    panel.hide()
    assert panel.edit.toPlainText() == "Split the task."
    sent: list[str] = []
    window.plan_check.rescope_note.connect(sent.append)
    panel.open("What must change?")
    panel._send()
    assert sent == ["Split the task."]
    assert panel.edit.toPlainText() == ""

    # E3: the default project has only diff-check, so the hint shows.
    window._new_change("feature")
    assert window.describe.checks_hint.isVisibleTo(window.describe)

    # E4: the findings eyebrow names the round from round two on.
    window.findings.show_findings([{"severity": "low", "file": "a", "line": 1,
                                    "evidence": "e", "remediation": "r"}], 2)
    assert "ROUND 2" in window.findings.eyebrow.text()
    window.findings.show_findings([{"severity": "low", "file": "a", "line": 1,
                                    "evidence": "e", "remediation": "r"}], 1)
    assert "ROUND" not in window.findings.eyebrow.text()

    # E5: the audience is remembered.
    values = load_ui_settings()
    values["explain_audience"] = "the safety board"
    save_ui_settings(values)
    window.show_explain()
    assert window.explain.audience_edit.text() == "the safety board"


def test_send_region_folds_after_the_packet_leaves(qt_app, tmp_path, monkeypatch):
    """FR-F2: the send region collapses once the packet is out."""
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    config = _project(tmp_path)
    window = MainWindow(config)
    window.home.new_change.emit("feature")
    window.describe.request_edit.setPlainText("Change the value to after.")
    window.describe._start()
    wait_until(qt_app, lambda: _screen(window) == "exchange", message="packet")

    assert window.exchange._send_full.isVisibleTo(window.exchange)
    window.exchange._copy_file()
    assert not window.exchange._send_full.isVisibleTo(window.exchange)
    assert window.exchange._send_summary.isVisibleTo(window.exchange)
    assert "Sent" in window.exchange.sent_label.text()
    window.exchange._unfold()
    assert window.exchange._send_full.isVisibleTo(window.exchange)
    window.controller.stop()
    wait_until(qt_app, lambda: not window.controller.busy, message="paused")


def test_issue_display_order_puts_high_first():
    from types import SimpleNamespace
    from maintain.issues import display_order
    rows = [
        SimpleNamespace(severity="low", updated_at="2026-07-30T10:00:00"),
        SimpleNamespace(severity="high", updated_at="2026-07-29T10:00:00"),
        SimpleNamespace(severity="medium", updated_at="2026-07-30T09:00:00"),
        SimpleNamespace(severity="high", updated_at="2026-07-30T08:00:00"),
    ]
    ordered = display_order(rows)
    assert [item.severity for item in ordered] == [
        "high", "high", "medium", "low"]
    assert ordered[0].updated_at > ordered[1].updated_at
