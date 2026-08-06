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

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

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


def _build_markdown(handoff) -> str:
    """The Markdown build reply: the same implementation as the ZIP,
    carried in the JSON envelope (FR-V4)."""
    request = handoff.request
    envelope = json.dumps({
        "schema_version": 1, "run_id": request.run_id,
        "task_id": request.task_id, "role": "implement",
        "conversation_id": "chat-build",
        "content": {"files": [
            {"path": "app.py", "content": 'VALUE = "after"\n'}],
            "deleted_files": []}})
    return "Here is the implementation.\n\n```json\n" + envelope + "\n```\n"


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

    # One minimal screen: two action cards and the packet receipt,
    # with the rare actions folded into a menu. No Continue gate.
    assert window.exchange.card.isVisibleTo(window.exchange)
    assert window.exchange.send_button.isVisibleTo(window.exchange)
    assert window.exchange.receive_button.isVisibleTo(window.exchange)
    assert not hasattr(window.exchange, "continue_button")
    actions = [item.text() for item in window.exchange.more_menu().actions()
               if item.text()]
    for expected in ("Add files…", "Export…", "Show the task text",
                     "Show the project rules", "Paste reply",
                     "Select the reply file…"):
        assert expected in actions, actions

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
    # The main route: the build reply is one pasted Markdown reply; the
    # ZIP shape stays covered by the repair journey (FR-V4).
    window.exchange.check(clipboard_text=_build_markdown(build_handoff))

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
    # FR-B7: with a remote, the saved commit goes to it.
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(Path(window.store.config.repository), "remote", "add", "origin",
         str(remote))
    window.save.accept.emit()
    wait_until(qt_app, lambda: errors or _screen(window) == "done",
               message="done screen")
    assert not errors, errors
    # The run commits on the person's own branch, in their own files.
    assert "main" in window.done.branch.text()
    assert (Path(window.store.config.repository) / "app.py"
            ).read_text() == 'VALUE = "after"\n'
    assert "1 file changed" in window.done.stat_files.text()
    assert "app.py" in window.done.file_names.text()
    # The count is applied builds, not raw timeline events (start,
    # plan, review… would triple the number).
    assert "build round" in window.done.stat_line.text()
    # No merge step and no merge command: the change is already there.
    assert not window.done._apply_holder.isVisibleTo(window.done)
    assert not window.done.merge_button.isVisibleTo(window.done)
    assert "in your files" in window.done.apply_hint.text()
    # The commit reached the remote, and the screen names it.
    assert window.done.push_line.isVisibleTo(window.done)
    assert "on the remote origin" in window.done.push_line.text()
    assert subprocess.run(["git", "-C", str(remote), "log", "-1", "--pretty=%s",
                           "main"], check=True, capture_output=True,
                          text=True).stdout.startswith("maintain:")
    assert window.done.first_note.isVisibleTo(window.done)
    window.done._copy_note()
    note = QApplication.clipboard().text()
    assert note.startswith("Saved: Change the value to after.")
    assert "app.py" in note and "Branch: main" in note
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
    assert window.foot_theme.text() == "☀"
    assert window.foot_theme.toolTip() == "Light mode"
    window.toggle_theme()
    assert window._theme == "light"
    assert window.foot_theme.text() == "☾"
    assert window.foot_theme.toolTip() == "Dark mode"
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


def test_foot_chip_names_the_project_and_switches_in_one_click(
        qt_app, tmp_path, monkeypatch):
    from maintain.repository_memory import remember_repository
    from maintain.ui import projects as project_ops
    from maintain.ui.strings import text as ui_text
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    config = _project(tmp_path)
    remember_repository(config.repository)
    second = tmp_path / "second"
    second.mkdir()
    _git(second, "init", "-b", "main")
    project_ops.ensure_config(second)
    remember_repository(second)

    window = MainWindow(config)
    window.toast = lambda *args, **kwargs: None
    # The chip names the open project, and stays as the screens change.
    assert window.foot_project.text() == "project ▾"
    window.show_issues()
    assert window.foot_project.text() == "project ▾"

    # The menu lists both projects; the open one carries the check mark.
    menu = window._project_menu()
    entries = [action for action in menu.actions() if not action.isSeparator()]
    labels = [action.text() for action in entries]
    assert labels[-1] == ui_text("projects.all")
    assert "project" in labels and "second" in labels
    current = next(a for a in entries if a.text() == "project")
    assert current.isChecked()

    # One click on the other entry switches the whole window.
    next(a for a in entries if a.text() == "second").trigger()
    assert window.store.config.repository == second.resolve()
    assert window.foot_project.text() == "second ▾"
    assert _screen(window) == "home"

    # The last entry opens the full projects screen.
    menu = window._project_menu()
    menu.actions()[-1].trigger()
    assert _screen(window) == "projects"

    # Long names elide in the chip; the tooltip keeps the full name.
    from maintain.ui.app import chip_name
    long_name = "a-very-long-project-name-indeed"
    assert chip_name(long_name).endswith("…")
    assert len(chip_name(long_name)) == 24
    window._set_project_chip(long_name)
    assert long_name in window.foot_project.toolTip()


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


def test_release_notes_show_once_after_an_update(qt_app, tmp_path, monkeypatch):
    """FR-U5: the first start after an update names what changed;
    later starts and fresh installs stay quiet."""
    from maintain import __version__
    from maintain.repository_memory import (load_ui_settings,
                                            save_ui_settings)

    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    save_ui_settings({"last_seen_version": "0.9.1", "theme": "dark"})
    config = _project(tmp_path)
    window = MainWindow(config)
    shown: list = []
    window._show_release_notes = shown.append

    window._maybe_show_release_notes()
    assert len(shown) == 1
    versions = [version for version, _ in shown[0]]
    assert versions[0] == "0.9.2" and versions[-1] == __version__
    assert load_ui_settings().get("last_seen_version") == __version__

    # The same version again: nothing shows.
    window._maybe_show_release_notes()
    assert len(shown) == 1

    # A fresh install records the version and stays quiet.
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "fresh.json"))
    window._maybe_show_release_notes()
    assert len(shown) == 1
    assert load_ui_settings().get("last_seen_version") == __version__

    # A build from before the notes existed: the current notes show.
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "old.json"))
    save_ui_settings({"theme": "dark"})
    window._maybe_show_release_notes()
    assert len(shown) == 2
    assert [version for version, _ in shown[1]] == [__version__]


def test_update_prompt_applies_or_skips(qt_app, tmp_path, monkeypatch):
    """FR-U2..U4: the ready-release card, the confirm, the detached
    helper, and the skip memory."""
    from maintain.repository_memory import load_ui_settings
    from maintain.ui import app as app_module

    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    # The probe must never reach the network from a test.
    monkeypatch.setattr(app_module, "update_available",
                        lambda *args, **kwargs: "")
    config = _project(tmp_path)
    window = MainWindow(config)
    window.show_home()

    # A found release shows one card on the home screen.
    window._offer_update("v9.9.9")
    assert window.home._update_card.isVisibleTo(window.home)
    assert "9.9.9" in window.home._update_card.title_label.text()

    # Accepting spawns the detached helper with the tag, then closes.
    spawned: list[list] = []
    monkeypatch.setattr(app_module.subprocess, "Popen",
                        lambda argv, **kwargs: spawned.append(argv))
    window.ask_confirm = lambda *args, **kwargs: True
    window.home.update_clicked.emit()
    assert len(spawned) == 1
    assert "refs/tags/v9.9.9" in spawned[0]
    assert spawned[0][spawned[0].index("-AppProcessId") + 1].isdigit()

    # The attempt is remembered. The same offer on the same version
    # means the install did not take, and the card says so instead of
    # repeating itself (the field fault: an endless update loop).
    assert load_ui_settings().get("update_attempted_tag") == "v9.9.9"
    window._offer_update("v9.9.9")
    assert "did not take" in window.home._update_card.sub_label.text()
    # A different offer is not the failed one; the plain words return.
    window._offer_update("v9.9.10")
    assert "did not take" not in window.home._update_card.sub_label.text()
    window._offer_update("v9.9.9")

    # Declining spawns nothing and keeps the card.
    window.ask_confirm = lambda *args, **kwargs: False
    window.home.update_clicked.emit()
    assert len(spawned) == 1
    assert window.home._update_card.isVisibleTo(window.home)

    # Skip hides the card, remembers the version, and stays quiet
    # when the same release is found again.
    window.home.update_skipped.emit()
    assert not window.home._update_card.isVisibleTo(window.home)
    assert load_ui_settings().get("update_skip") == "v9.9.9"
    window._offer_update("v9.9.9")
    assert not window.home._update_card.isVisibleTo(window.home)
    # A newer release than the skipped one shows again.
    window._offer_update("v9.9.10")
    assert window.home._update_card.isVisibleTo(window.home)

    # The settings switch stops and starts the look.
    window._show_settings()
    assert window.settings.updates_box.isChecked()
    window.settings.updates_box.setChecked(False)
    assert load_ui_settings().get("update_check") is False
    assert not window._update_timer.isActive()
    window.settings.updates_box.setChecked(True)
    assert load_ui_settings().get("update_check") is True
    assert window._update_timer.isActive()


def test_issue_triage_walks_the_list(qt_app, tmp_path, monkeypatch):
    """FR-I10: the field faults — the close reasons sat off the screen
    in a row too wide for the window, and every decision threw the
    person back to the list to find their place again."""
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    config = _project(tmp_path)
    window = MainWindow(config)
    for n in range(6):
        window.controller.issues.add(title=f"Fault {n}", file=f"m{n}.py")
    window.show_issues()

    order = window.issues_list.shown_ids()
    window._open_issue(order[0])
    detail = window.issue_detail
    assert _screen(window) == "issue"
    assert detail._walk_holder.isVisibleTo(detail)
    assert "1" in detail.position.text() and "6" in detail.position.text()
    assert not detail.previous_button.isEnabled()

    # Next and Previous move without a trip back to the list.
    detail.step.emit(1)
    assert detail.issue_id == order[1]
    assert detail.previous_button.isEnabled()
    detail.step.emit(-1)
    assert detail.issue_id == order[0]
    detail.step.emit(-1)      # already first: nothing moves
    assert detail.issue_id == order[0]

    # The close reasons live in a menu, so no width can hide them.
    from maintain.issues import REASONS
    menu = detail.reason_menu()
    labels = [action.text() for action in menu.actions() if action.text()]
    assert len(labels) == len(REASONS) + 1     # the reasons and the question
    choices = [action for action in menu.actions()
               if action.text() and action.isEnabled()]
    assert len(choices) == len(REASONS)

    # Choosing a reason closes that issue, and the decision advances
    # to the issue that took its place.
    closing = detail.issue_id
    choices[0].trigger()
    menu.deleteLater()
    closed = window.controller.issues.get(closing)
    assert closed.status == "closed" and closed.closed_reason == REASONS[0]
    assert _screen(window) == "issue"
    assert detail.issue_id == order[1]

    # The sweep runs to the end, then lands on the list.
    for _ in range(10):
        if _screen(window) != "issue":
            break
        window._close_issue_with(detail.issue_id, "fixed")
    assert _screen(window) == "issues"
    assert not [x for x in window.controller.issues.load()
                if x.status != "closed"]


def test_tracker_never_hides_new_issues(qt_app, tmp_path, monkeypatch):
    """The field bug: the tracker said "Open 20" and showed nothing.

    The status tab persisted across visits, so a visit after a scan
    rendered the stale tab's empty list under counts that were right.
    Entries from outside now start on the open work; a detail round
    trip keeps the person's tab; and the store re-reads its file so
    another writer's issues appear."""
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    config = _project(tmp_path)
    window = MainWindow(config)

    # Leave the tracker on the closed tab, then add twenty issues.
    window.show_issues()
    window.issues_list.set_filter("closed")
    window.show_home()
    for n in range(20):
        window.controller.issues.add(title=f"Fault {n}")
    window.show_issues()
    assert window.issues_list._filter == "open"
    assert window.issues_list._rows.count() == 20

    # A detail round trip keeps the tab the person chose.
    first = window.controller.issues.load()[0]
    window.controller.issues.set_in_work(first.id)
    window.show_issues()
    window.issues_list.set_filter("in_work")
    window._open_issue(first.id)
    window.issue_detail.back.emit()
    assert _screen(window) == "issues"
    assert window.issues_list._filter == "in_work"
    assert window.issues_list._rows.count() == 1

    # Another writer's addition appears on the next visit: the file
    # is the truth and the tracker re-reads it.
    from maintain.issues import IssueStore
    other = IssueStore(runtime_root=window.controller.issues.runtime_root,
                       repository=window.controller.issues.repository)
    other.add(title="Added by another window")
    window.show_home()
    window.show_issues()
    assert any(issue.title == "Added by another window"
               for issue in window.controller.issues.load())
    assert window.issues_list._rows.count() == 20   # 19 open + the new one

    # Tab clicks reuse the row widgets: rebuilding the whole list on
    # every filter change was the tracker lag.
    screen = window.issues_list
    kept = screen._rows.itemAt(0).widget()
    kept_id = next(iter(screen._row_cache))
    before = screen._row_cache[kept_id][1]
    screen.set_filter("in_work")
    screen.set_filter("open")
    assert screen._row_cache[kept_id][1] is before
    assert kept.parent() is not None   # alive and re-attached, not rebuilt


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
    # FR-W3: the sweep position is on the screen; this project fits one
    # wave, so the note names every file.
    assert window.exchange.scan_note.isVisibleTo(window.exchange)
    assert "project files" in window.exchange.scan_note.text()
    # A run-less side flow carries no name chip in the foot bar.
    assert not window._run_head.isVisibleTo(window)
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
         "detail": "It must be after.", "external_ref": "T-9",
         "group": "value handling"},
        {"title": "Invented point", "severity": "low", "file": "app.py",
         "line": 2, "snippet": "not_in_the_file()", "detail": ""},
    ]})
    window.exchange.check(clipboard_text=reply)
    assert _screen(window) == "scan-check"
    boxes = window.scan_check._boxes
    assert len(boxes) == 2
    assert boxes[0].isChecked() and not boxes[1].isChecked()
    # The reply proves the wave arrived: its files are covered now, and
    # the check screen says where the sweep stands.
    from maintain.issue_packets import load_scan_coverage
    assert load_scan_coverage(window.store.config)
    assert window.scan_check.coverage.isVisibleTo(window.scan_check)

    window._scan_accept([0])
    assert _screen(window) == "issues"
    issues = window.controller.issues.load()
    assert len(issues) == 1 and issues[0].source == "scan"
    assert issues[0].external_ref == "T-9"
    assert issues[0].group == "value handling"
    # The external reference is searchable in the tracker (FR-I7).
    window.issues_list.set_filter("all")
    window.issues_list.search.setText("T-9")
    assert window.issues_list._rows.count() == 1
    window.issues_list.search.setText("")

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


def test_talk_flow_routes_each_outcome(qt_app, tmp_path, monkeypatch):
    """FR-B1..B4: one packet out, the talk happens in Copilot, and the
    closing envelope routes to the tracker, a run request, or nothing."""
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    config = _project(tmp_path)
    window = MainWindow(config)
    issue = window.controller.issues.add(
        title="The bound is wrong", group="value handling")

    # The compose screen builds one packet with the code and the issues.
    window.home.open_talk.emit()
    assert _screen(window) == "talk"
    window.talk.edit.setPlainText("The value handling group.")
    window.talk.start.emit(window.talk.edit.toPlainText().strip())
    assert _screen(window) == "exchange"
    request = window._side["exchange"].request
    assert request.role == "talk"
    assert window.current_handoff.task_key == "discuss"
    assert request.payload["open_issues"][0]["id"] == issue.id
    assert any(item["path"] == "app.py"
               for item in request.payload["candidate_files"])

    # Outcome 1: issues — through the same accept gate as a scan,
    # recorded with the discussion source.
    window.exchange.check(clipboard_text=_side_envelope(window, {
        "outcome": "issues", "issues": [
            {"title": "The value is wrong", "severity": "high",
             "file": "app.py", "line": 1, "snippet": 'VALUE = "before"',
             "detail": "The value must be after."}]}))
    assert _screen(window) == "scan-check"
    window._scan_accept([0])
    raised = [item for item in window.controller.issues.load()
              if item.source == "talk"]
    assert len(raised) == 1 and raised[0].title == "The value is wrong"

    # Outcome 2: a repair request lands in the fault description with
    # the named issue linked; nothing starts without the person.
    window._talk_start("")
    window.exchange.check(clipboard_text=_side_envelope(window, {
        "outcome": "repair", "request": "Correct the bound in app.py.",
        "issue_ids": [issue.id]}))
    assert _screen(window) == "describe"
    assert window.describe.mode == "issue"
    assert window.describe.request_edit.toPlainText() == (
        "Correct the bound in app.py.")
    assert window._pending_issue_links == [issue.id]
    assert window._side is None

    # Outcome 3: a feature request lands in the change description.
    window._talk_start("")
    window.exchange.check(clipboard_text=_side_envelope(window, {
        "outcome": "feature", "request": "Add an export command."}))
    assert _screen(window) == "describe"
    assert window.describe.mode == "feature"
    assert window.describe.request_edit.toPlainText() == (
        "Add an export command.")

    # Outcome 4: no request — home, nothing recorded.
    window._talk_start("")
    count_before = len(window.controller.issues.load())
    window.exchange.check(clipboard_text=_side_envelope(window, {
        "outcome": "none"}))
    assert _screen(window) == "home"
    assert len(window.controller.issues.load()) == count_before

    # Stop mid-exchange discards the round back to the compose screen.
    window._talk_start("")
    window._stop_run()
    assert _screen(window) == "talk"
    assert window._side is None


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


def _shell_stub(path, body: str, cmd_body: str = "") -> str:
    """A fake render command for both platforms. Windows launches
    .cmd files through subprocess, so the stub stays argv-invoked."""
    if os.name == "nt":
        target = path.with_suffix(".cmd")
        target.write_text("@echo off\r\n" + cmd_body, encoding="utf-8")
        return str(target)
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
                            'echo "Boom on line 3" 1>&2\nexit 1\n',
                            'echo Boom on line 3 1>&2\r\nexit /b 1\r\n')
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
        'echo video > "media/videos/scene/1080p60/$3.mp4"\n',
        'md media\\videos\\scene\\1080p60 2>nul\r\n'
        'echo video > "media\\videos\\scene\\1080p60\\%3.mp4"\r\n')
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


def test_stop_pauses_names_the_run_and_home_offers_continue(
        qt_app, tmp_path, monkeypatch):
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    config = _project(tmp_path)
    window = MainWindow(config)
    window.ask_confirm = lambda *args, **kwargs: True
    # FR-N1: the stop prompt takes the person's own words for the run.
    asked: list = []

    def ask_line(*args, **kwargs):
        asked.append(kwargs.get("value", ""))
        return "Wire the loader"

    window.ask_line = ask_line

    window.home.new_change.emit("feature")
    window.describe.request_edit.setPlainText("Change the value to after.")
    window.describe._start()
    wait_until(qt_app, lambda: _screen(window) == "exchange", message="plan packet")
    from maintain.ui.strings import text as ui_text
    assert window.run_name.text() == ui_text("foot.name.unset")

    window._stop_run()
    wait_until(qt_app, lambda: _screen(window) == "home", message="home after stop")
    assert len(asked) == 1
    summary = window.controller.resumable_run()
    assert summary is not None
    assert summary.state == str(RunState.NEEDS_HUMAN)
    assert summary.name == "Wire the loader"
    assert summary.phase == "Plan"   # paused before any planned tasks

    # The home card says the name, the activity, and the phase.
    window.show_home()
    assert "Wire the loader" in window.home._continue.title_label.text()
    sub = window.home._continue.sub_label.text()
    assert "Change" in sub and "Plan" in sub

    # Continue resumes the run and asks for the plan packet again.
    window._continue_run(summary.run_id)
    wait_until(qt_app, lambda: _screen(window) == "exchange", message="resumed packet")
    assert window.current_handoff.task_key == "plan"

    # FR-N2: the open workflow labels its name in the foot bar; a click
    # edits it, prefilled, and the write lands when the engine settles.
    assert window.run_name.text() == "Wire the loader"
    window.ask_line = lambda *args, **kwargs: (
        asked.append(kwargs.get("value", "")) or "Rework the loader")
    window.run_name.click()
    assert asked[-1] == "Wire the loader"   # prefilled for the edit
    assert window.run_name.text() == "Rework the loader"   # shown at once

    # FR-N1: the named run stops with no further question, and the
    # queued rename lands at the settle.
    before = len(asked)
    window._stop_run()
    wait_until(qt_app, lambda: not window.controller.busy, message="second stop")
    assert len(asked) == before
    assert window.controller.resumable_run().name == "Rework the loader"
    window.show_home()
    assert "Rework the loader" in window.home._continue.title_label.text()
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


def test_exchange_screen_stays_minimal(qt_app, tmp_path, monkeypatch):
    """The two used actions stand alone; a copy by hand confirms in
    the status line. Nothing folds and nothing competes."""
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    config = _project(tmp_path)
    window = MainWindow(config)
    window.home.new_change.emit("feature")
    window.describe.request_edit.setPlainText("Change the value to after.")
    window.describe._start()
    wait_until(qt_app, lambda: _screen(window) == "exchange", message="packet")

    # The packet is in the clipboard on arrival, and the Send card
    # says so. The turn starts on Send.
    exchange = window.exchange
    assert "clipboard" in exchange.send_button.sub_label.text()
    assert exchange.send_button.property("active") == "true"
    # Pressing Send passes the turn to Receive.
    exchange.send_button.clicked.emit()
    assert "Sent" in exchange.send_button.title_label.text()
    assert exchange.receive_button.property("active") == "true"
    assert exchange.send_button.property("active") == "false"
    # The next packet resets the turn.
    exchange._set_turn(sent=False)
    assert exchange.send_button.property("active") == "true"
    # The scan controls stay away from a plain change.
    assert not exchange._focus_holder.isVisibleTo(exchange)
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


def test_reference_allclear_and_last_video(qt_app, tmp_path, monkeypatch):
    """FR-H1/H2/H4: the reference shows, all-clear speaks, videos remembered."""
    from maintain.issues import IssueCandidate
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    config = _project(tmp_path)
    window = MainWindow(config)

    window.controller.issues.capture([IssueCandidate(
        title="No bounds on speed", detail="", severity="medium",
        file="app.py", line=1, snippet='VALUE = "before"',
        external_ref="TRACKER-112", kind="scan", verified=True)],
        source="scan", run_id="scan-1")
    issue = window.controller.issues.load()[0]

    # H1/I4: the spreadsheet reference shows in the editable field.
    window._open_issue(issue.id)
    assert window.issue_detail.reference_edit.text() == "TRACKER-112"

    # I2: an in-work issue offers the way back to open.
    window.controller.issues.set_in_work(issue.id)
    window._open_issue(issue.id)
    assert window.issue_detail.reopen_button.isVisibleTo(window.issue_detail)
    assert "Return to open" in window.issue_detail.reopen_button.text()
    window._reopen_issue(issue.id)
    assert window.controller.issues.get(issue.id).status == "open"

    # I4: an edited reference is saved.
    window._open_issue(issue.id)
    window.issue_detail.reference_edit.setText("TRACKER-113")
    window._save_issue()
    assert window.controller.issues.get(issue.id).external_ref == "TRACKER-113"

    # I5: the filter tabs carry counts.
    window.show_issues()
    assert window.issues_list._tab_buttons["open"].text().endswith("1")

    # H2: closing the last issue turns the empty state into the all-clear.
    window.controller.issues.close(issue.id, "fixed")
    window.show_issues()
    window.issues_list.set_filter("open")
    assert "All clear" in window.issues_list.empty.text()
    window.show_home()
    assert "All clear" in window.home._issues_card.sub.text() \
        if hasattr(window.home._issues_card, "sub") else True

    # H4: the newest video shows on the explain screen.
    video_dir = Path(config.runtime_root).parent / "explain" / "x" / "render"
    video_dir.mkdir(parents=True)
    (video_dir / "DemoScene.mp4").write_text("video", encoding="utf-8")
    window.show_explain()
    assert window.explain.last_video.isVisibleTo(window.explain)
    assert "Last video" in window.explain.last_video.text()


def test_file_chips_fold_large_sets_behind_a_more_chip(qt_app):
    """FR-P10: the include-code sweep once filled the screen with 157
    chips; large sets fold behind one +N chip."""
    from maintain.ui.widgets import FileChips
    chips = FileChips()
    removed: list[int] = []
    chips.removed.connect(removed.append)

    chips.set_files([f"file{i}.py" for i in range(30)])
    assert chips._flow.count() == 13          # 12 chips + the "+18 more"
    more = chips._flow.itemAt(12).widget()
    assert "18" in more.text()
    more.click()
    assert chips._flow.count() == 31          # every chip + "Show fewer"
    chips._flow.itemAt(30).widget().click()
    assert chips._flow.count() == 13

    # The chip index still names the true file, folded or not.
    chips._flow.itemAt(0).widget().removed.emit()
    assert removed == [0]

    # A small set shows plainly; one hidden file is not worth a fold.
    chips.set_files(["a.py", "b.py"])
    assert chips._flow.count() == 2
    chips.set_files([f"f{i}" for i in range(13)])
    assert chips._flow.count() == 13


def test_explain_survives_a_restart_and_lists_its_video(
        qt_app, tmp_path, monkeypatch):
    """FR-X2/X3: a waiting explanation returns after a restart with its
    original ids; the finished one is browsable with its video."""
    from maintain.explain_store import load_explain_states
    from maintain.repository_memory import load_ui_settings, save_ui_settings
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    config = _project(tmp_path)
    window = MainWindow(config)
    window.toast = lambda *args, **kwargs: None
    errors: list[str] = []
    window.show_error = errors.append
    pass_stub = _shell_stub(
        tmp_path / "pass-manim",
        'mkdir -p media/videos/scene/1080p60\n'
        'echo video > "media/videos/scene/1080p60/$3.mp4"\n',
        'md media\\videos\\scene\\1080p60 2>nul\r\n'
        'echo video > "media\\videos\\scene\\1080p60\\%3.mp4"\r\n')
    values = load_ui_settings()
    values["manim_command"] = pass_stub
    save_ui_settings(values)

    window.show_explain()
    window.explain.goal_edit.setPlainText("Explain the value bound.")
    window.explain.add_files([config.repository / "app.py"])
    window.explain._start()
    assert _screen(window) == "exchange"
    run_id = window._explain["run_id"]
    states = load_explain_states(config)
    assert states and states[0]["run_id"] == run_id
    assert states[0]["status"] == "waiting"

    # The application dies; a fresh window offers the waiting exchange.
    window2 = MainWindow(config)
    window2.toast = lambda *args, **kwargs: None
    window2.show_error = errors.append
    window2.show_home()
    home_card = window2.home._continue_explain
    assert home_card.isVisibleTo(window2.home)
    window2._resume_explain(run_id)
    assert _screen(window2) == "exchange"
    assert window2._side["exchange"].request.run_id == run_id
    assert window2.current_handoff.zip_path.is_file()

    # The old packet's reply validates; the render finishes the state.
    window2.exchange.check(clipboard_text=SCENE_REPLY)
    wait_until(qt_app,
               lambda: window2.explain_result.render_chip.text() == "PASS",
               message="render after resume")
    mine = next(item for item in load_explain_states(config)
                if item["run_id"] == run_id)
    assert mine["status"] == "passed"
    assert mine["video"] and Path(mine["video"]).is_file()

    # The finished explanation lists on the input screen.
    window2.show_explain()
    assert window2.explain._past_holder.isVisibleTo(window2.explain)

    # A discarded exchange stops offering itself on the home screen.
    window2.explain.goal_edit.setPlainText("Another goal.")
    window2.explain.add_files([config.repository / "app.py"])
    window2.explain._start()
    assert _screen(window2) == "exchange"
    window2._stop_run()
    assert all(item["status"] != "waiting"
               for item in load_explain_states(config))
    window2.show_home()
    assert not window2.home._continue_explain.isVisibleTo(window2.home)
    assert not errors, errors


def test_run_phase_maps_states_to_loop_steps():
    from maintain.history import run_phase
    assert run_phase("scoping", []) == "Plan"
    assert run_phase("implementing", [{}]) == "Build"
    assert run_phase("repairing", [{}]) == "Build"
    assert run_phase("reviewing", [{}]) == "Review"
    assert run_phase("testing", [{}]) == "Test"
    assert run_phase("awaiting_acceptance", [{}]) == "Save"
    # A pause keeps needs_human; planned tasks split Plan from Build.
    assert run_phase("needs_human", []) == "Plan"
    assert run_phase("needs_human", [{}]) == "Build"
    assert run_phase("delivered", [{}]) == ""


def test_run_record_round_trips_the_name():
    from maintain.models import RunRecord
    record = RunRecord(run_id="r", mode="feature", request="x",
                       repository=".", base_commit="", branch="",
                       worktree="", name="Wire the loader")
    assert RunRecord.from_dict(record.to_dict()).name == "Wire the loader"
    # Records written before the field existed still load.
    old = {key: value for key, value in record.to_dict().items()
           if key != "name"}
    assert RunRecord.from_dict(old).name == ""


def test_explain_staleness_tracks_the_recorded_files(tmp_path):
    """FR-X4: the saved packet manifest is the fingerprint set; an
    edit or a deletion of a recorded file makes the video stale."""
    from maintain.explain_store import is_stale, save_explain_state
    from maintain.issue_packets import explain_request
    config = _project(tmp_path)
    request = explain_request(config, ["app.py"], "Explain the value.", "")
    state = save_explain_state(config, request.run_id, status="passed",
                               sources=["app.py"], request=request,
                               created_at="2026-07-31T00:00:00")
    assert not is_stale(config, state)
    (config.repository / "app.py").write_text('VALUE = "changed"\n',
                                              encoding="utf-8")
    assert is_stale(config, state)
    (config.repository / "app.py").unlink()
    assert is_stale(config, state)
    # A state with no recorded request stays quiet, never a false alarm.
    assert not is_stale(config, {"run_id": "x"})


def test_stale_explanation_offers_update_and_retires_on_pass(
        qt_app, tmp_path, monkeypatch):
    """FR-X4: the changed file marks the video stale; Update redoes the
    same goal over the current files, and the pass retires the old."""
    from PySide6.QtWidgets import QPushButton
    from maintain.explain_store import load_explain_states
    from maintain.repository_memory import load_ui_settings, save_ui_settings
    from maintain.ui.strings import text as ui_text
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    config = _project(tmp_path)
    window = MainWindow(config)
    window.toast = lambda *args, **kwargs: None
    errors: list[str] = []
    window.show_error = errors.append
    pass_stub = _shell_stub(
        tmp_path / "pass-manim",
        'mkdir -p media/videos/scene/1080p60\n'
        'echo video > "media/videos/scene/1080p60/$3.mp4"\n',
        'md media\\videos\\scene\\1080p60 2>nul\r\n'
        'echo video > "media\\videos\\scene\\1080p60\\%3.mp4"\r\n')
    values = load_ui_settings()
    values["manim_command"] = pass_stub
    save_ui_settings(values)

    window.show_explain()
    window.explain.goal_edit.setPlainText("Explain the value bound.")
    window.explain.add_files([config.repository / "app.py"])
    window.explain._start()
    window.exchange.check(clipboard_text=SCENE_REPLY)
    wait_until(qt_app, lambda: window.explain_result.render_chip.text() == "PASS",
               message="first render")
    old_id = window._explain["run_id"]

    # Untouched files: the list shows the video fresh, no Update button.
    window.show_explain()
    updates = [item for item in
               window.explain._past_holder.findChildren(QPushButton)
               if item.text() == ui_text("explain.update")]
    assert not updates

    # The explained file changes; the row turns stale and offers Update.
    (config.repository / "app.py").write_text('VALUE = "changed"\n',
                                              encoding="utf-8")
    window.show_explain()
    updates = [item for item in
               window.explain._past_holder.findChildren(QPushButton)
               if item.text() == ui_text("explain.update")]
    assert len(updates) == 1
    updates[0].click()
    assert _screen(window) == "exchange"
    new_id = window._explain["run_id"]
    assert new_id != old_id and window._explain["updates"] == old_id
    packet_files = window._side["exchange"].request.payload["candidate_files"]
    assert 'VALUE = "changed"' in packet_files[0]["content"]

    # The pass retires the stale one; the list keeps only the new video.
    window.exchange.check(clipboard_text=SCENE_REPLY)
    wait_until(qt_app, lambda: window.explain_result.render_chip.text() == "PASS",
               message="update render")
    states = load_explain_states(config)
    assert next(item for item in states
                if item["run_id"] == old_id)["superseded_by"] == new_id
    window.show_explain()
    cards = window.explain._past_holder.findChildren(QPushButton)
    assert not [item for item in cards
                if item.text() == ui_text("explain.update")]
    rows = [item for item in states
            if item["status"] == "passed" and not item.get("superseded_by")]
    assert [item["run_id"] for item in rows] == [new_id]
    assert not errors, errors


def test_close_asks_a_name_only_for_unnamed_work(qt_app, tmp_path,
                                                 monkeypatch):
    """FR-N1: closing mid-run names unnamed work once; named work
    closes with no question."""
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    config = _project(tmp_path)
    window = MainWindow(config)
    asked: list = []
    window.ask_line = lambda *args, **kwargs: asked.append(1) or "Night work"
    window.home.new_change.emit("feature")
    window.describe.request_edit.setPlainText("Change the value to after.")
    window.describe._start()
    wait_until(qt_app, lambda: _screen(window) == "exchange", message="packet")
    window.close()
    assert asked == [1]
    summary = window.controller.resumable_run()
    assert summary is not None and summary.name == "Night work"

    # A fresh window resumes the named run; closing asks nothing.
    window2 = MainWindow(config)
    window2.ask_line = lambda *args, **kwargs: asked.append(2) or "X"
    window2._continue_run(summary.run_id)
    wait_until(qt_app, lambda: _screen(window2) == "exchange",
               message="resumed packet")
    window2.close()
    assert asked == [1]
    assert window2.controller.resumable_run().name == "Night work"


def test_a_non_reply_drop_joins_the_packet_out_loud(qt_app, tmp_path,
                                                    monkeypatch):
    """FR-V3: a dropped file that is not the reply joins the open
    packet — rebuilt and announced, never kept silently."""
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    config = _project(tmp_path)
    window = MainWindow(config)
    window.ask_confirm = lambda *args, **kwargs: True
    window.ask_line = lambda *args, **kwargs: None
    toasts: list[str] = []
    window.toast = toasts.append
    window.home.new_change.emit("feature")
    window.describe.request_edit.setPlainText("Change the value to after.")
    window.describe._start()
    wait_until(qt_app, lambda: _screen(window) == "exchange",
               timeout=90.0, message="plan packet")
    notes = tmp_path / "meeting-notes.txt"
    notes.write_text("The value must follow the spec.\n", encoding="utf-8")
    window.exchange.check(path=notes)
    with zipfile.ZipFile(window.current_handoff.zip_path) as archive:
        assert "attachments/meeting-notes.txt" in set(archive.namelist())
    assert "not the reply" in window.exchange.status.text()
    assert "added to the package" in window.exchange.status.text()
    window._stop_run()
    wait_until(qt_app, lambda: not window.controller.busy, message="stop")


def test_named_runs_lead_history_and_the_timeline(qt_app):
    """FR-N1: the name the person gave heads the history row and the
    run detail; the request and the id stay one line below."""
    from maintain.history import RunSummary
    from maintain.ui.screens import HistoryRow, RunDetailScreen

    named = RunSummary(
        run_id="f-1", mode="feature", request="Change the value to after.",
        state="delivered", updated_at="2026-08-02T01:00:00Z",
        created_at="2026-08-02T00:00:00Z", repository="x",
        changed_files=1, name="Value correction")
    row = HistoryRow(named)
    labels = [widget.text() for widget in row.findChildren(QLabel)]
    assert any(value == "Value correction" for value in labels)
    assert any("Change the value" in value and "f-1" in value
               for value in labels)

    detail = RunDetailScreen()
    detail.show_timeline(named, [], live=False)
    assert detail.title.text() == "Value correction"
    assert "f-1" in detail.title.toolTip()
    unnamed = RunSummary(
        run_id="f-2", mode="feature", request="Another change.",
        state="delivered", updated_at="", created_at="", repository="x",
        changed_files=0)
    detail.show_timeline(unnamed, [], live=False)
    assert "f-2" in detail.title.text()


def test_foot_home_and_the_theme_symbol(qt_app, tmp_path, monkeypatch):
    """The foot bar: Home returns from anywhere, the theme toggle is
    one symbol, and the run name heads the run screens instead of
    squashing into the foot."""
    from maintain.ui.strings import text as ui_text
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    config = _project(tmp_path)
    window = MainWindow(config)
    window.ask_confirm = lambda *args, **kwargs: True
    window.ask_line = lambda *args, **kwargs: None

    # Dark by default: the symbol offers the light mode, with words in
    # the tooltip.
    assert window.foot_theme.text() == ui_text("theme.symbol.light")
    assert window.foot_theme.toolTip() == ui_text("theme.to_light")
    window.toggle_theme()
    assert window.foot_theme.text() == ui_text("theme.symbol.dark")
    window.toggle_theme()

    window.home.new_change.emit("feature")
    window.describe.request_edit.setPlainText("Change the value to after.")
    window.describe._start()
    wait_until(qt_app, lambda: _screen(window) == "exchange", message="packet")
    assert window._run_head.isVisibleTo(window)
    assert window.run_name.text() == ui_text("foot.name.unset")

    # Home leaves the run; Continue returns with the head restored.
    window.foot_home.click()
    assert _screen(window) == "home"
    assert not window._run_head.isVisibleTo(window)
    window.home.continue_run.emit(window.current_handoff.request.run_id)
    assert _screen(window) == "exchange"
    assert window._run_head.isVisibleTo(window)
    assert window.foot_stop.isVisibleTo(window)
    window._stop_run()
    wait_until(qt_app, lambda: not window.controller.busy, message="stopped")


def test_build_reply_accepts_markdown_and_text_named_zip(tmp_path):
    """FR-V4: the build step takes the Markdown reply, a text file
    misnamed .zip, or the real ZIP; junk is refused with plain words."""
    from maintain.models import ProviderRequest
    from maintain.providers.manual_ui import PacketHandoff
    from maintain.ui.bridge import check_reply
    from maintain.zip_package import PacketBuild

    request = ProviderRequest(
        1, "r-1", "change-value", "implement", "Do it.",
        {"mode": "feature", "task": {"allowed_files": ["app.py"]}})
    handoff = PacketHandoff(
        request=request,
        packet=PacketBuild(zip_path=tmp_path / "packet.zip", sha256="",
                           bytes=0, task_key="build", members=()),
        reply_kind="zip")
    envelope = json.dumps({
        "schema_version": 1, "run_id": "r-1", "task_id": "change-value",
        "role": "implement", "conversation_id": "chat",
        "content": {"files": [{"path": "app.py",
                               "content": 'VALUE = "after"\n'}],
                    "deleted_files": []}})
    markdown = "The implementation.\n\n```json\n" + envelope + "\n```\n"

    pasted = check_reply(handoff, text=markdown)
    assert pasted.valid and pasted.reply.kind == "json"

    # Copilot sometimes writes text under a ZIP name; the text wins.
    misnamed = tmp_path / "maintain-output.zip"
    misnamed.write_text(markdown, encoding="utf-8")
    from_file = check_reply(handoff, path=misnamed)
    assert from_file.valid and from_file.reply.kind == "json"

    junk = tmp_path / "junk.zip"
    junk.write_bytes(b"\x00\x01 not a zip, not text \xff\xfe")
    refused = check_reply(handoff, path=junk)
    assert not refused.valid
    assert "cannot read this ZIP" in refused.message

    # The dry-run synthesis still enforces the authorized paths.
    off_scope = json.loads(envelope)
    off_scope["content"]["files"][0]["path"] = "secrets.py"
    outside = check_reply(
        handoff, text="```json\n" + json.dumps(off_scope) + "\n```")
    assert not outside.valid and "unapproved path" in outside.message

    prose = check_reply(handoff, text="hello there")
    assert not prose.valid and "Markdown reply" in prose.message


def test_fault_flow_ties_into_the_issue_tracker(qt_app, tmp_path, monkeypatch):
    """FR-I6: the fault screen offers the open tracked issues; picking
    one repairs it linked, and a described fault lands in the tracker —
    described again, it reuses the same issue. Picking also offers the
    relatives (FR-I9): same scan group, or same file when ungrouped."""
    from maintain.issues import IssueCandidate
    from maintain.ui.strings import text as ui_text
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    config = _project(tmp_path)
    window = MainWindow(config)
    offers: list[str] = []
    accept_related = {"value": False}

    def confirm(title, body="", *args, **kwargs):
        if title == ui_text("issues.related.title"):
            offers.append(body)
            return accept_related["value"]
        return True

    window.ask_confirm = confirm
    window.ask_line = lambda *args, **kwargs: None
    window.toast = lambda *args, **kwargs: None
    errors: list[str] = []
    window.show_error = errors.append

    window.controller.issues.capture([
        IssueCandidate(title="The value is wrong", severity="high",
                       file="app.py", line=1, snippet='VALUE = "before"'),
        IssueCandidate(title="A slow loop", severity="low", file="app.py",
                       line=1, snippet="loop"),
        *[IssueCandidate(title=f"Small fault {index}", severity="low",
                         file=f"src/mod{index}.py", line=index,
                         snippet=f"x{index}") for index in range(4)],
    ], source="scan")

    # The fault screen lists the top tracked issues; past four, one
    # line leads to the whole tracker (FR-I7). Long titles wrap.
    window.home.new_change.emit("issue")
    describe = window.describe

    def card(index):
        row = describe._issues_column.itemAt(index).widget()
        return row.layout().itemAt(1).widget()

    assert describe._issues_holder.isVisibleTo(describe)
    assert describe._issues_column.count() == 4
    assert card(0).title_label.wordWrap()
    assert describe._issues_more.isVisibleTo(describe)
    assert "+2" in describe._issues_more.text()
    describe._issues_more.click()
    assert _screen(window) == "issues"
    window.home.new_change.emit("feature")
    assert not describe._issues_holder.isVisibleTo(describe)
    assert not describe._issues_more.isVisibleTo(describe)

    # Picking one prefills the fault; the started run links to it. The
    # ungrouped pick offers its same-file neighbor; declined, the run
    # repairs the picked issue alone (FR-I9 fallback).
    window.home.new_change.emit("issue")
    card(0).clicked.emit()
    assert len(offers) == 1
    assert "app.py" in offers[0] and "A slow loop" in offers[0]
    assert "The value is wrong" in describe.request_edit.toPlainText()
    assert "A slow loop" not in describe.request_edit.toPlainText()
    describe._start()
    wait_until(qt_app, lambda: _screen(window) == "exchange",
               timeout=90.0, message="picked issue packet")
    picked_run = window.current_handoff.request.run_id
    picked = next(item for item in window.controller.issues.load()
                  if item.title == "The value is wrong")
    assert picked.status == "in_work"
    assert picked_run in picked.runs
    assert sum(1 for item in window.controller.issues.load()
               if picked_run in item.runs) == 1
    window._stop_run()
    wait_until(qt_app, lambda: not window.controller.busy, message="stop one")

    # FR-I8: checking several repairs them together in one linked run.
    window.home.new_change.emit("issue")
    describe._issue_checks[0][0].setChecked(True)
    assert not describe.repair_together.isVisibleTo(describe)
    describe._issue_checks[1][0].setChecked(True)
    assert describe.repair_together.isVisibleTo(describe)
    assert "2" in describe.repair_together.text()
    describe.repair_together.click()
    body = describe.request_edit.toPlainText()
    assert "Repair these 2 related faults together." in body
    assert "1. The value is wrong" in body
    describe._start()
    wait_until(qt_app, lambda: _screen(window) == "exchange",
               timeout=90.0, message="together packet")
    together_run = window.current_handoff.request.run_id
    linked = [item for item in window.controller.issues.load()
              if together_run in item.runs]
    assert len(linked) == 2
    assert all(item.status == "in_work" for item in linked)
    window._stop_run()
    wait_until(qt_app, lambda: not window.controller.busy,
               message="stop together")

    # A described new fault lands in the tracker, linked and in work.
    # The held issues from the together run mark their cards, and the
    # composed repair text stays out of the recent-request chips.
    from maintain.repository_memory import load_ui_settings
    recents = [str(item) for item in
               load_ui_settings().get("recent_requests", [])]
    assert not any("Repair these" in item for item in recents)
    window.home.new_change.emit("issue")
    assert any("In work" in card(index).sub_label.text()
               for index in range(describe._issues_column.count()))
    describe.request_edit.setPlainText("The loader misses the bound check.")
    describe._start()
    wait_until(qt_app, lambda: _screen(window) == "exchange",
               timeout=90.0, message="described packet")
    described = [item for item in window.controller.issues.load()
                 if item.source == "described"]
    assert len(described) == 1
    assert described[0].status == "in_work"
    assert described[0].title.startswith("The loader misses")
    assert window.current_handoff.request.run_id in described[0].runs
    # The issue list renders the described source without a crash — the
    # screenshot run caught a missing catalog key here once. With many
    # issues, the filter line narrows the list as you type (FR-I7).
    window.show_issues()
    window.issues_list.set_filter("all")
    assert _screen(window) == "issues"
    assert window.issues_list._rows.count() == 7
    window.issues_list.search.setText("loader")
    assert window.issues_list._rows.count() == 1
    window.issues_list.search.setText("small fault")
    assert window.issues_list._rows.count() == 4
    window.issues_list.search.setText("")
    assert window.issues_list._rows.count() == 7
    window._stop_run()
    wait_until(qt_app, lambda: not window.controller.busy, message="stop two")

    # The same words again reuse the tracked issue — no duplicate.
    window.home.new_change.emit("issue")
    describe.request_edit.setPlainText("The loader misses the bound check.")
    describe._start()
    wait_until(qt_app, lambda: _screen(window) == "exchange",
               timeout=90.0, message="described again")
    described = [item for item in window.controller.issues.load()
                 if item.source == "described"]
    assert len(described) == 1
    assert len(described[0].runs) == 2
    window._stop_run()
    wait_until(qt_app, lambda: not window.controller.busy, message="stop three")

    # FR-I9 proper: the scanner grouped two issues across files. The
    # pick offers the group mate; accepted, one run repairs both, and
    # the group label is searchable in the tracker.
    grouped = window.controller.issues.capture([
        IssueCandidate(title="The parser drops the last row",
                       severity="medium", file="src/parse.py", line=3,
                       snippet="rows[:-1]", group="parser bounds"),
        IssueCandidate(title="The writer skips the header",
                       severity="medium", file="src/write.py", line=8,
                       snippet="skip_header", group="parser bounds"),
    ], source="scan")
    offers.clear()
    accept_related["value"] = True
    describe.pick_issue.emit(grouped.added[0])
    assert len(offers) == 1
    assert "parser bounds" in offers[0]
    assert "The writer skips the header" in offers[0]
    body = describe.request_edit.toPlainText()
    assert "Repair these 2 related faults together." in body
    assert "The parser drops the last row" in body
    assert "The writer skips the header" in body
    describe._start()
    wait_until(qt_app, lambda: _screen(window) == "exchange",
               timeout=90.0, message="grouped packet")
    grouped_run = window.current_handoff.request.run_id
    mates = [item for item in window.controller.issues.load()
             if grouped_run in item.runs]
    assert len(mates) == 2
    assert all(item.status == "in_work" for item in mates)
    window.show_issues()
    window.issues_list.set_filter("all")
    window.issues_list.search.setText("parser bounds")
    assert window.issues_list._rows.count() == 2
    window._stop_run()
    wait_until(qt_app, lambda: not window.controller.busy,
               message="stop grouped")
    assert not errors, errors
