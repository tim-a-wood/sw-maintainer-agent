"""End-to-end journeys beyond the happy path: settings, repair rounds,
failed checks, stop and continue, discard, and the launch entry point.

Like test_ui_app, each test plays Copilot: it reads the packet handoff
and returns the reply through the same screens the person would use.
"""

from __future__ import annotations

import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")


from maintain.models import RunState  # noqa: E402
from maintain.ui.app import MainWindow  # noqa: E402

from test_ui_app import (_git, _project, _scope_reply, _screen,  # noqa: E402
                         qt_app, wait_until)

__all__ = ["qt_app"]


def _zip_reply(handoff, directory: Path, content: str) -> Path:
    """An implementation ZIP matching the handoff, for build and repair."""
    request = handoff.request
    reply = directory / f"maintain-output-{request.task_id}.zip"
    manifest = (f'schema_version = 1\nrun_id = "{request.run_id}"\n'
                f'task_id = "{request.task_id}"\nrole = "{request.role}"\n'
                'files = ["app.py"]\ndeleted_files = []\n')
    if request.payload.get("mode") == "issue":
        manifest += ('root_cause_statement = "app.py line 1 sets VALUE '
                     'to before."\nroot_cause_evidence_paths = ["app.py"]\n')
    with zipfile.ZipFile(reply, "w") as archive:
        archive.writestr("IMPLEMENTATION.toml", manifest)
        archive.writestr("files/app.py", content)
    return reply


def _review_json(handoff, findings: list | None = None) -> str:
    request = handoff.request
    return json.dumps({
        "schema_version": 1, "run_id": request.run_id,
        "task_id": request.task_id, "role": "review",
        "conversation_id": "chat-review",
        "content": {
            "decision": "changes_requested" if findings else "approve",
            "findings": findings or []}})


def _issue_scope_reply(handoff) -> str:
    """A scope reply for issue mode: the root cause is required."""
    value = json.loads(_scope_reply(handoff))
    value["content"]["root_cause"] = {
        "statement": "app.py line 1 sets VALUE to before.",
        "evidence_paths": ["app.py"]}
    return json.dumps(value)


def _drive_to_plan_gate(qt_app, window, reply) -> None:
    """Answer every scope packet (the engine may expand context) until
    the plan gate opens."""
    answered: set[int] = set()

    def ready() -> bool:
        if _screen(window) == "plan":
            return True
        handoff = window.current_handoff
        if (_screen(window) == "exchange" and handoff is not None
                and handoff.task_key == "plan" and id(handoff) not in answered):
            answered.add(id(handoff))
            window.exchange.check(clipboard_text=reply(handoff))
        return False

    wait_until(qt_app, ready, timeout=60.0, message="plan gate")


def _wired_window(tmp_path, monkeypatch) -> tuple[MainWindow, list, list]:
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    config = _project(tmp_path)
    window = MainWindow(config)
    window.ask_confirm = lambda *args, **kwargs: True
    window.ask_line = lambda *args, **kwargs: None
    errors: list[str] = []
    window.show_error = errors.append
    toasts: list[str] = []
    window.toast = toasts.append
    return window, errors, toasts


# ---------- the settings round-trip ----------

def test_settings_round_trip_through_every_page(qt_app, tmp_path, monkeypatch):
    from maintain.config import ProjectConfig
    from maintain.repository_memory import load_ui_settings
    from maintain.ui.screens import documents_count
    from maintain.ui.strings import text as ui_text

    window, errors, toasts = _wired_window(tmp_path, monkeypatch)
    window.home.open_settings.emit()
    assert _screen(window) == "settings"

    # Downloads: a missing folder is refused; Browse fills a real one.
    window._open_settings_page("downloads")
    assert _screen(window) == "set-downloads"
    window.page_downloads.downloads_edit.setText(str(tmp_path / "nowhere"))
    window.page_downloads._save()
    assert _screen(window) == "set-downloads"   # stays; nothing stored
    assert window.page_downloads.message.text() == ui_text("downloads.missing")
    (tmp_path / "dl").mkdir()
    window.pick_directory = lambda: str(tmp_path / "dl")
    window.page_downloads.browse.emit()
    assert window.page_downloads.downloads_edit.text() == str(tmp_path / "dl")
    window.page_downloads._save()
    assert _screen(window) == "settings"
    assert toasts[-1] == ui_text("settings.saved")
    values = load_ui_settings()
    assert values["downloads_path"] == str(tmp_path / "dl")

    # Global prompt: template first, then the saved file wins, then reset.
    window._open_settings_page("global")
    template = window.page_global.editor.toPlainText()
    assert "Maintain" in template or template
    window.page_global.editor.setPlainText("# My rules\nKeep it small.\n")
    window.page_global._save()
    assert window.store.global_prompt_path().is_file()
    window._open_settings_page("global")
    assert window.page_global.editor.toPlainText().startswith("# My rules")
    window.page_global._reset()
    assert window.page_global.message.text() == ui_text("global.reset.done")
    assert window.page_global.editor.toPlainText() != "# My rules\nKeep it small.\n"

    # Tasks: two project documents in one pick, a task document, and an
    # own plan prompt.
    inside = tmp_path / "project" / "docs.md"
    inside.write_text("# Docs\n", encoding="utf-8")
    second = tmp_path / "project" / "more.md"
    second.write_text("# More\n", encoding="utf-8")
    outside = tmp_path / "elsewhere.md"
    outside.write_text("# Outside\n", encoding="utf-8")
    window.pick_files = lambda: [str(inside), str(second)]
    window._open_settings_page("tasks")
    assert _screen(window) == "set-tasks"
    window.page_tasks.add_doc.emit(None)
    assert "docs.md" in window.store.config.package.documents
    assert "more.md" in window.store.config.package.documents
    window.pick_files = lambda: [str(outside)]
    window.page_tasks.set_tab("plan")
    window.page_tasks.add_doc.emit("plan")
    assert str(outside) in window.store.config.package.task("plan").documents
    assert documents_count(window.store, "plan") == 3

    overridden, builtin = window.store.task_prompt("plan")
    assert overridden is False and "tasks" in builtin.lower()
    window.page_tasks._toggle_prompt()
    overridden, _ = window.store.task_prompt("plan")
    assert overridden is True
    window.page_tasks.prompt_edit.setPlainText("Plan it my way.")
    window.page_tasks._save()
    overridden, prompt = window.store.task_prompt("plan")
    assert overridden is True and prompt == "Plan it my way."
    # Back keeps prompt edits exactly as Save does.
    window._open_settings_page("tasks")
    window.page_tasks.set_tab("plan")
    window.page_tasks.prompt_edit.setPlainText("Plan it another way.")
    window.page_tasks._back()
    overridden, prompt = window.store.task_prompt("plan")
    assert overridden is True and prompt == "Plan it another way."
    window._open_settings_page("tasks")
    window.page_tasks.set_tab("plan")
    window.page_tasks._toggle_prompt()
    overridden, _ = window.store.task_prompt("plan")
    assert overridden is False

    window.page_tasks.remove_doc.emit("plan", str(outside))
    assert not window.store.config.package.task("plan").documents
    window.page_tasks.set_tab("project")
    window.page_tasks.remove_doc.emit(None, "docs.md")
    window.page_tasks.remove_doc.emit(None, "more.md")
    assert not window.store.config.package.documents

    # Package style: zip, persisted on disk, then back to markdown.
    window._open_settings_page("package")
    window.page_package.zip_radio.setChecked(True)
    window.page_package._save()
    assert window.store.config.package.style == "zip"
    assert window.exchange.package_style == "zip"
    reloaded = ProjectConfig.load(window.store.path)
    assert reloaded.package.style == "zip"
    window._open_settings_page("package")
    window.page_package.markdown_radio.setChecked(True)
    window.page_package._save()
    assert window.store.config.package.style == "markdown"

    # Checks: an added row saves; a command-less row is refused with a message.
    window._open_settings_page("checks")
    before = len(window.store.checks())
    window.page_checks._add_row("unit", f"{sys.executable} -c pass")
    window.page_checks._save()
    assert ("unit", f"{sys.executable} -c pass") in window.store.checks()
    assert len(window.store.checks()) == before + 1
    window._open_settings_page("checks")
    window.page_checks._add_row("broken", "")
    window.page_checks._save()
    assert window.page_checks.message.text()
    assert _screen(window) == "set-checks"

    # Explain: the page says whether the command resolves, at open and
    # after save.
    from maintain.repository_memory import save_ui_settings
    values = load_ui_settings()
    values["manim_command"] = "no-such-render-command"
    save_ui_settings(values)
    window._open_settings_page("explain")
    assert window.page_explain.status.text() == ui_text("explain.set.absent")
    if os.name == "nt":
        stub = tmp_path / "manim-stub.cmd"
        stub.write_text("@echo off\r\n", encoding="utf-8")
    else:
        stub = tmp_path / "manim-stub"
        stub.write_text("#!/bin/sh\n", encoding="utf-8")
        stub.chmod(0o755)
    window.page_explain.command_edit.setText(str(stub))
    window.page_explain.saved.emit()
    assert load_ui_settings()["manim_command"] == str(stub)
    assert str(stub) in window.page_explain.status.text()

    assert not errors, errors


def test_settings_edits_never_block_the_next_run(qt_app, tmp_path,
                                                 monkeypatch):
    """The walkthrough's worst find: a prompt edit wrote files into the
    repository, and every later run refused with uncommitted changes.
    Also pinned here: the home Continue card while the engine waits,
    and a package style change with a packet open."""
    window, errors, toasts = _wired_window(tmp_path, monkeypatch)
    window._open_settings_page("tasks")
    window.page_tasks.set_tab("plan")
    window.page_tasks._toggle_prompt()
    window.page_tasks.prompt_edit.setPlainText("Plan with care.")
    window.page_tasks._back()
    window._open_settings_page("global")
    window.page_global.editor.setPlainText("# Rules\nKeep it small.\n")
    window.page_global._save()

    window.home.new_change.emit("feature")
    window.describe.request_edit.setPlainText("Change the value to after.")
    window.describe._start()
    wait_until(qt_app, lambda: _screen(window) == "exchange",
               message="run despite settings edits")
    payload = window.current_handoff.request.payload
    swept = [item["path"] for item in payload["candidate_files"]]
    swept += [item["path"] for item in payload["repository_map"]]
    assert swept and not any(item.startswith(".maintain") for item in swept)

    # Continue from home returns to the waiting screen — no dead click.
    window.show_home()
    run_id = window.current_handoff.request.run_id
    window.home.continue_run.emit(run_id)
    assert _screen(window) == "exchange"

    # A style change while the packet is open re-shows and re-copies it.
    assert window.exchange.card.packet_path.suffix == ".md"
    window._open_settings_page("package")
    window.page_package.zip_radio.setChecked(True)
    window.page_package._save()
    assert window.exchange.card.packet_path.suffix == ".zip"
    from PySide6.QtWidgets import QApplication
    mime = QApplication.clipboard().mimeData()
    assert mime.hasUrls()
    assert mime.urls()[0].toLocalFile().endswith(".zip")
    window.home.continue_run.emit(run_id)
    assert _screen(window) == "exchange"
    window._stop_run()
    wait_until(qt_app, lambda: not window.controller.busy, message="stopped")
    assert not errors, errors


# ---------- the long way home: repair, failed checks, stop, discard ----------

def test_repair_failed_checks_stop_continue_and_discard(
        qt_app, tmp_path, monkeypatch):
    from maintain.ui.strings import text as ui_text

    window, errors, toasts = _wired_window(tmp_path, monkeypatch)
    flag = tmp_path / "checks-flag"
    window._open_settings_page("checks")
    window.page_checks._add_row(
        "flag", f'{sys.executable} -c "import sys, pathlib; '
                f"sys.exit(0 if pathlib.Path('{flag}').exists() else 1)\"")
    window.page_checks._save()
    assert any(name == "flag" for name, _ in window.store.checks())

    window.home.new_change.emit("feature")
    window.describe.request_edit.setPlainText("Change the value to after.")
    window.describe._start()

    # While the engine waits, a second operation is refused.
    wait_until(qt_app, lambda: _screen(window) == "exchange", message="plan packet")
    assert window.controller.busy
    assert window.controller.start_run("feature", "another", []) is False

    window.exchange.check(clipboard_text=_scope_reply(window.current_handoff))
    wait_until(qt_app, lambda: _screen(window) == "plan", message="plan gate")
    window.plan_check.accept.emit()

    wait_until(qt_app, lambda: _screen(window) == "exchange"
               and window.current_handoff.task_key == "build",
               message="build packet")
    window.grab()   # paints the packet card and both exchange regions
    window.exchange.check(
        path=_zip_reply(window.current_handoff, tmp_path, 'VALUE = "almost"\n'))

    # The review finds a blocking point; the findings gate opens.
    wait_until(qt_app, lambda: _screen(window) == "exchange"
               and window.current_handoff.task_key == "review",
               message="first review packet")
    finding = {"severity": "medium", "file": "app.py", "line": 1,
               "title": "The value is wrong",
               "evidence": "app.py line 1 sets VALUE to almost.",
               "remediation": "Set VALUE to after."}
    window.exchange.check(
        clipboard_text=_review_json(window.current_handoff, [finding]))
    wait_until(qt_app, lambda: _screen(window) == "findings",
               message="findings gate")
    window.grab()

    # Stop at the gate. The run pauses; Home offers to continue it.
    record = window.current_record
    window._stop_run()
    wait_until(qt_app, lambda: not window.controller.busy
               and _screen(window) == "home", message="paused at home")
    paused = window.controller.resumable_run()
    assert paused is not None and paused.run_id == record.run_id
    assert paused.display_state == "Waiting"

    # Continue: the audit verifies, the run resumes at the same gate.
    window.home.continue_run.emit(record.run_id)
    wait_until(qt_app, lambda: _screen(window) == "findings",
               message="findings gate after continue")
    window.findings.repair.emit()

    # The repair round fixes the file; the second review approves.
    wait_until(qt_app, lambda: _screen(window) == "exchange"
               and window.current_handoff.task_key == "repair",
               message="repair packet")
    window.exchange.check(
        path=_zip_reply(window.current_handoff, tmp_path, 'VALUE = "after"\n'))
    wait_until(qt_app, lambda: _screen(window) == "exchange"
               and window.current_handoff.task_key == "review",
               message="second review packet")
    window.exchange.check(clipboard_text=_review_json(window.current_handoff))

    # The flag file is missing, so the added check fails and the gate opens.
    wait_until(qt_app, lambda: _screen(window) == "test"
               and window.test.retry_button.isVisibleTo(window.test),
               message="failed checks", timeout=60.0)
    window.grab()

    # Fix the world, run the checks again; the run reaches the Save screen.
    flag.write_text("ready\n", encoding="utf-8")
    window.test.retry.emit()
    wait_until(qt_app, lambda: _screen(window) == "save", message="save screen",
               timeout=60.0)
    record = window.current_record
    assert RunState(record.state) is RunState.AWAITING_ACCEPTANCE
    assert window.controller.resumable_run().run_id == record.run_id
    window.grab()

    # Run the checks once more from Save (the go-back anchor path).
    window.save.rerun.emit()
    wait_until(qt_app, lambda: _screen(window) == "save"
               and not window.controller.busy,
               message="save after rerun", timeout=60.0)

    # Request one more change from Save; the repair loop runs once more.
    window.save.feedback_note.emit("Also update the wording.")
    wait_until(qt_app, lambda: _screen(window) == "exchange"
               and window.current_handoff.task_key == "repair",
               message="feedback repair packet", timeout=60.0)
    window.exchange.check(
        path=_zip_reply(window.current_handoff, tmp_path, 'VALUE = "after"\n'))
    wait_until(qt_app, lambda: _screen(window) == "exchange"
               and window.current_handoff.task_key == "review",
               message="third review packet")
    window.exchange.check(clipboard_text=_review_json(window.current_handoff))
    wait_until(qt_app, lambda: _screen(window) == "save"
               and not window.controller.busy,
               message="save after feedback", timeout=60.0)

    # The timeline shows the whole story on the run detail screen.
    window._open_live_timeline()
    assert _screen(window) == "run"
    kinds = [item.kind for item in
             window.controller.timeline(record.run_id)]
    for expected in ("plan_approved", "review_found", "feedback",
                     "checks_passed", "revert"):
        assert expected in kinds, kinds
    window.grab()
    window._continue_run(record.run_id)
    assert _screen(window) == "save"

    # Discard. The run cancels; the worktree can then be removed.
    worktree = Path(window.current_record.worktree)
    assert worktree.is_dir()
    window.save.discard.emit()
    wait_until(qt_app, lambda: not window.controller.busy
               and _screen(window) == "home", message="discarded")
    assert ui_text("discard.done") in toasts
    assert not errors, errors

    final = window.controller.runs()[0]
    assert final.run_id == record.run_id
    assert final.display_state == "Discarded"
    assert window.controller.resumable_run() is None

    assert window.controller.diff_text(window.current_record) != ""
    window.controller.engine.cleanup_workspace(record.run_id)
    assert not worktree.exists()
    # The recorded diff outlives the worktree; without any record the
    # answer is empty, never an error.
    assert "after" in window.controller.diff_text(window.current_record)
    import dataclasses
    ghost = dataclasses.replace(window.current_record,
                                run_id="f-00000000-000000-none",
                                worktree=str(tmp_path / "void"))
    assert window.controller.diff_text(ghost) == ""

    # A late action on the closed run surfaces as an error, not a crash.
    window.controller.feedback(record.run_id, "too late")
    wait_until(qt_app, lambda: errors, message="late feedback error")
    assert "acceptance" in errors[-1]
    assert window.controller.rerun_checks("f-00000000-000000-none") is False


# ---------- the launch entry point ----------

def _proxy_app(qt_app):
    class AppProxy:
        def __init__(self, argv) -> None:
            pass

        def setApplicationName(self, name: str) -> None:
            qt_app.setApplicationName(name)

        def setPalette(self, palette) -> None:
            qt_app.setPalette(palette)

        def setStyleSheet(self, sheet: str) -> None:
            qt_app.setStyleSheet(sheet)

        def setWindowIcon(self, icon) -> None:
            qt_app.setWindowIcon(icon)

        def exec(self) -> int:
            return 0

    return AppProxy


def test_launch_entry_point_paths(qt_app, tmp_path, monkeypatch):
    from PySide6 import QtWidgets

    from maintain.ui.main import main as ui_main

    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(QtWidgets, "QApplication", _proxy_app(qt_app))
    warnings: list[str] = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "warning",
        staticmethod(lambda parent, title, body, *rest: warnings.append(body)))
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "question",
        staticmethod(lambda *args, **kwargs:
                     QtWidgets.QMessageBox.StandardButton.Yes))

    # No project chosen in the picker: the app leaves quietly.
    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *args, **kwargs: ""))
    assert ui_main([]) == 0

    # A folder without Git is refused with a warning.
    plain = tmp_path / "plain"
    plain.mkdir()
    assert ui_main(["--repo", str(plain)]) == 1
    assert "not a Git repository" in warnings[-1]

    # A Git folder without configuration is set up after the question.
    bare = tmp_path / "bare"
    bare.mkdir()
    _git(bare, "init", "-b", "main")
    assert ui_main(["--repo", str(bare)]) == 0
    assert (bare / ".maintain.json").is_file()

    # The remembered project opens again without the picker.
    assert ui_main([]) == 0

    # Answering No to the set-up question leaves without writing.
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    _git(fresh, "init", "-b", "main")
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "question",
        staticmethod(lambda *args, **kwargs:
                     QtWidgets.QMessageBox.StandardButton.No))
    with pytest.raises(SystemExit):
        ui_main(["--repo", str(fresh)])
    assert not (fresh / ".maintain.json").exists()

    # A broken configuration is reported, not swallowed.
    broken = tmp_path / "broken"
    broken.mkdir()
    _git(broken, "init", "-b", "main")
    (broken / ".maintain.json").write_text('{"schema_version": 999}\n',
                                           encoding="utf-8")
    assert ui_main(["--repo", str(broken)]) == 1
    assert warnings[-1]


# ---------- every screen paints ----------

def test_every_screen_paints_in_both_themes(qt_app, tmp_path, monkeypatch):
    window, errors, _ = _wired_window(tmp_path, monkeypatch)
    for name in ("home", "describe", "projects", "issues", "issue",
                 "scan-check", "explain", "history", "settings", "busy",
                 "set-downloads", "set-tasks", "set-global", "set-package",
                 "set-checks", "set-explain"):
        window.show_screen(name)
        image = window.grab()
        assert not image.isNull(), name
    window.toggle_theme()
    window.show_screen("home")
    assert not window.grab().isNull()
    window.toggle_theme()
    assert not errors, errors


# ---------- rescope from every gate ----------

def test_rescope_from_plan_findings_and_failed_checks(
        qt_app, tmp_path, monkeypatch):
    window, errors, toasts = _wired_window(tmp_path, monkeypatch)
    flag = tmp_path / "checks-flag"
    window._open_settings_page("checks")
    window.page_checks._add_row(
        "flag", f'{sys.executable} -c "import sys, pathlib; '
                f"sys.exit(0 if pathlib.Path('{flag}').exists() else 1)\"")
    window.page_checks._save()

    window.home.new_change.emit("feature")
    window.describe.request_edit.setPlainText("Change the value to after.")
    window.describe._start()

    def answer_plan(note: str | None = None) -> None:
        wait_until(qt_app, lambda: _screen(window) == "exchange"
                   and window.current_handoff.task_key == "plan",
                   message="plan packet")
        window.exchange.check(clipboard_text=_scope_reply(window.current_handoff))
        wait_until(qt_app, lambda: _screen(window) == "plan",
                   message="plan gate")
        if note is None:
            window.plan_check.accept.emit()
        else:
            window.plan_check.rescope_note.emit(note)

    def answer_build() -> None:
        wait_until(qt_app, lambda: _screen(window) == "exchange"
                   and window.current_handoff.task_key == "build",
                   message="build packet")
        window.exchange.check(path=_zip_reply(
            window.current_handoff, tmp_path, 'VALUE = "after"\n'))

    def answer_review(findings: list | None = None) -> None:
        wait_until(qt_app, lambda: _screen(window) == "exchange"
                   and window.current_handoff.task_key == "review",
                   message="review packet")
        window.exchange.check(
            clipboard_text=_review_json(window.current_handoff, findings))

    # Round one: the plan itself is sent back for changes.
    answer_plan(note="Split the plan smaller.")

    # Round two: the plan passes; the review points lead to a rescope.
    answer_plan()
    answer_build()
    answer_review([{"severity": "medium", "file": "app.py", "line": 1,
                    "title": "Wrong scope",
                    "evidence": "The change needs a wider scope.",
                    "remediation": "Plan it again."}])
    wait_until(qt_app, lambda: _screen(window) == "findings",
               message="findings gate")
    window.findings.rescope_note.emit("Scope the fix wider.")

    # Round three: the failed check leads to the last rescope.
    answer_plan()
    answer_build()
    answer_review()
    wait_until(qt_app, lambda: _screen(window) == "test"
               and window.test.retry_button.isVisibleTo(window.test),
               message="failed checks", timeout=60.0)
    window.test.rescope_note.emit("The checks need another plan.")

    # Round four passes end to end once the flag exists.
    flag.write_text("ready\n", encoding="utf-8")
    answer_plan()
    answer_build()
    answer_review()
    wait_until(qt_app, lambda: _screen(window) == "save", message="save",
               timeout=60.0)
    record = window.current_record

    timeline = window.controller.timeline(record.run_id)
    notes = [item.sub for item in timeline if item.kind == "rescope"]
    assert notes == ["Split the plan smaller.", "Scope the fix wider.",
                     "The checks need another plan."]

    window.save.accept.emit()
    wait_until(qt_app, lambda: errors or _screen(window) == "done",
               message="done", timeout=60.0)
    assert not errors, errors


# ---------- the issue mode with a reproduction check ----------

def _issue_project(tmp_path, monkeypatch, argv: list[str]):
    import json as json_module

    from maintain.config import ProjectConfig

    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    config = _project(tmp_path)
    data = json_module.loads(config.path.read_text(encoding="utf-8"))
    data.setdefault("verification", {}).setdefault("commands", {})["repro"] = {
        "argv": argv, "phase": "reproduce"}
    config.path.write_text(json_module.dumps(data, indent=2) + "\n",
                           encoding="utf-8")
    return MainWindow(ProjectConfig.load(config.path))


def test_issue_mode_reproduces_fixes_and_verifies(qt_app, tmp_path, monkeypatch):
    window = _issue_project(tmp_path, monkeypatch, [
        sys.executable, "-c",
        "import sys, pathlib; sys.exit("
        "0 if 'after' in pathlib.Path('app.py').read_text() else 1)"])
    window.ask_confirm = lambda *args, **kwargs: True
    errors: list[str] = []
    window.show_error = errors.append
    window.toast = lambda *args, **kwargs: None

    window.home.new_change.emit("issue")
    assert _screen(window) == "describe"
    window.describe.request_edit.setPlainText("The value is wrong. Repair it.")
    window.describe._start()

    wait_until(qt_app, lambda: _screen(window) == "exchange"
               and window.current_handoff.task_key == "plan",
               message="plan packet")
    assert window.current_handoff.request.payload.get("mode") == "issue"
    _drive_to_plan_gate(qt_app, window, _issue_scope_reply)
    window.plan_check.accept.emit()

    # The reproduction runs before the fix; the build packet follows it.
    wait_until(qt_app, lambda: _screen(window) == "exchange"
               and window.current_handoff.task_key == "build",
               message="build packet", timeout=60.0)
    window.exchange.check(path=_zip_reply(
        window.current_handoff, tmp_path, 'VALUE = "after"\n'))
    wait_until(qt_app, lambda: _screen(window) == "exchange"
               and window.current_handoff.task_key == "review",
               message="review packet")
    window.exchange.check(clipboard_text=_review_json(window.current_handoff))

    wait_until(qt_app, lambda: _screen(window) == "save", message="save",
               timeout=60.0)
    record = window.current_record
    reproduction = record.evidence.get("pre_fix_reproduction", [])
    assert reproduction and reproduction[0]["exit_code"] != 0
    tested = [item["name"] for item in
              record.evidence.get("tests", {}).get("commands", [])]
    assert "repro" in tested

    window.save.accept.emit()
    wait_until(qt_app, lambda: errors or _screen(window) == "done",
               message="done", timeout=60.0)
    assert not errors, errors
    # The described fault closed with the delivery, and the done screen
    # keeps that win after the toast fades (FR-I6).
    assert "Closed 1 issue" in window.done.issues_line.text()
    assert window.done.issues_line.isVisibleTo(window.done)


def test_issue_mode_refuses_a_fault_that_does_not_reproduce(
        qt_app, tmp_path, monkeypatch):
    window = _issue_project(tmp_path, monkeypatch,
                            [sys.executable, "-c", "raise SystemExit(0)"])
    window.ask_confirm = lambda *args, **kwargs: True
    toasts: list[str] = []
    window.toast = toasts.append
    window.show_error = toasts.append

    window.home.new_change.emit("issue")
    window.describe.request_edit.setPlainText("A fault that is not real.")
    window.describe._start()
    _drive_to_plan_gate(qt_app, window, _issue_scope_reply)
    window.plan_check.accept.emit()

    # The issue does not reproduce; the run pauses and says why.
    wait_until(qt_app, lambda: not window.controller.busy
               and _screen(window) == "home", message="paused", timeout=60.0)
    assert any("did not reproduce" in item for item in toasts)
    paused = window.controller.resumable_run()
    assert paused is not None and paused.display_state == "Waiting"


# ---------- the perf pass: fast paths and the include-code choice ----------

def test_project_code_paths_walks_roots_with_the_caps(tmp_path):
    from maintain.context import project_code_paths
    repository = tmp_path / "repo"
    (repository / "src").mkdir(parents=True)
    (repository / "tests").mkdir()
    (repository / "src" / "small.py").write_text("A = 1\n", encoding="utf-8")
    (repository / "src" / "big.py").write_text("B" * 5000, encoding="utf-8")
    (repository / "src" / "binary.bin").write_bytes(b"\x00\x01")
    (repository / "tests" / "test_small.py").write_text(
        "def test(): pass\n", encoding="utf-8")
    (repository / "elsewhere.py").write_text("C = 3\n", encoding="utf-8")
    found = project_code_paths(repository, ("src", "tests"), (), 4000)
    names = sorted(path.name for path in found)
    assert names == ["small.py", "test_small.py"]


def test_include_code_ships_the_project_in_the_first_packet(
        qt_app, tmp_path, monkeypatch):
    window, errors, toasts = _wired_window(tmp_path, monkeypatch)
    repository = window.store.config.repository
    (repository / "src").mkdir()
    (repository / "src" / "helper.py").write_text("H = 1\n", encoding="utf-8")
    (repository / "tests").mkdir()
    (repository / "tests" / "test_helper.py").write_text(
        "def test(): pass\n", encoding="utf-8")
    _git(repository, "add", "-A")
    _git(repository, "commit", "-m", "add source folders")

    window.home.new_change.emit("feature")
    assert not window.describe.include_code.isChecked()   # reset each time
    window.describe.include_code.setChecked(True)
    window.describe.request_edit.setPlainText("Change the value to after.")
    window.describe._start()
    wait_until(qt_app, lambda: _screen(window) == "exchange",
               message="plan packet")
    assert any("code files" in item for item in toasts)
    with zipfile.ZipFile(window.current_handoff.zip_path) as archive:
        names = set(archive.namelist())
    assert "attachments/helper.py" in names
    assert "attachments/test_helper.py" in names
    window._stop_run()
    wait_until(qt_app, lambda: not window.controller.busy, message="stopped")
    assert not errors, errors


def test_explain_include_code_needs_no_manual_files(
        qt_app, tmp_path, monkeypatch):
    window, errors, toasts = _wired_window(tmp_path, monkeypatch)
    window.show_explain()
    window.explain.goal_edit.setPlainText("Explain the module layout.")
    # Without files and without the choice, start refuses.
    window.explain._start()
    assert window.explain.message.text()
    window.explain.include_code.setChecked(True)
    window.explain._start()
    # A refusal lands in a toast or the message line; name it instead
    # of timing out silently when a platform breaks the file walk.
    try:
        wait_until(qt_app, lambda: _screen(window) == "exchange",
                   message="explain packet")
    except AssertionError as exc:
        raise AssertionError(
            f"{exc}; toasts={toasts!r}; "
            f"message={window.explain.message.text()!r}") from None
    carried = [item["path"] for item in
               window.current_handoff.request.payload["candidate_files"]]
    assert "app.py" in carried
    window._stop_run()
    assert not errors, errors


def test_switching_to_the_open_project_never_rebuilds(qt_app, tmp_path,
                                                      monkeypatch):
    window, errors, toasts = _wired_window(tmp_path, monkeypatch)
    before = window.exchange
    window._open_project(str(window.store.config.repository))
    assert window.exchange is before          # no teardown, no rebuild
    assert _screen(window) == "home"
    assert not errors, errors


def test_home_reads_the_run_list_once(qt_app, tmp_path, monkeypatch):
    window, errors, toasts = _wired_window(tmp_path, monkeypatch)
    calls = {"count": 0}
    original = window.controller.runs

    def counted():
        calls["count"] += 1
        return original()

    window.controller.runs = counted
    window.show_home()
    assert calls["count"] == 1


def test_save_diff_comes_from_the_recorded_artifact(qt_app, tmp_path,
                                                    monkeypatch):
    window, errors, toasts = _wired_window(tmp_path, monkeypatch)
    from maintain.models import RunRecord
    runtime = window.store.config.runtime_root
    deep = runtime / "f-20260731-140000-diff" / "artifacts" / "t1-attempt-1"
    deep.mkdir(parents=True)
    (deep / "actual.diff").write_text("diff --git a/x b/x\n+after\n",
                                     encoding="utf-8")
    record = RunRecord(
        run_id="f-20260731-140000-diff", mode="feature", request="x",
        repository=str(window.store.config.repository), base_commit="b",
        branch="maintain/x", worktree=str(tmp_path / "gone"),
        state="awaiting_acceptance")
    assert "after" in window.controller.diff_text(record)


def test_project_switch_rebinds_without_rebuilding(qt_app, tmp_path,
                                                   monkeypatch):
    from maintain.repository_memory import remember_repository
    from maintain.ui import projects as project_ops

    window, errors, toasts = _wired_window(tmp_path, monkeypatch)
    remember_repository(window.store.config.repository)
    second = tmp_path / "second"
    second.mkdir()
    _git(second, "init", "-b", "main")
    project_ops.ensure_config(second)

    kept = (window.exchange, window.home, window.page_tasks)
    window._open_project(str(second))
    assert (window.exchange, window.home, window.page_tasks) == kept
    assert window.store.config.repository == second.resolve()
    assert window.page_tasks.store is window.store
    assert window.page_global.store is window.store
    assert window.home._name.text() == "second"
    assert "second" in window.foot_project.text()
    assert _screen(window) == "home"
    # The rebound stores drive the settings pages for the new project.
    window._open_settings_page("tasks")
    window.page_tasks.set_tab("plan")
    overridden, _ = window.store.task_prompt("plan")
    assert overridden is False
    assert not errors, errors


def test_paste_routes_a_copied_file_like_a_drop(qt_app, tmp_path, monkeypatch):
    from PySide6.QtCore import QMimeData, QUrl
    from PySide6.QtWidgets import QApplication

    window, errors, toasts = _wired_window(tmp_path, monkeypatch)
    window.home.new_change.emit("feature")
    window.describe.request_edit.setPlainText("Change the value to after.")
    window.describe._start()
    wait_until(qt_app, lambda: _screen(window) == "exchange",
               message="plan packet")

    reply = tmp_path / "maintain-reply.md"
    reply.write_text("```json\n" + _scope_reply(window.current_handoff)
                     + "\n```\n", encoding="utf-8")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(reply))])
    QApplication.clipboard().setMimeData(mime)
    window.show_history()          # the reply lands from any screen
    window._paste_anywhere()
    wait_until(qt_app, lambda: _screen(window) == "plan", message="plan gate")
    window._stop_run()
    wait_until(qt_app, lambda: not window.controller.busy, message="stopped")
    assert not errors, errors


def test_exchange_wait_timer_stops_with_the_screen(qt_app, tmp_path,
                                                   monkeypatch):
    window, errors, toasts = _wired_window(tmp_path, monkeypatch)
    window.home.new_change.emit("feature")
    window.describe.request_edit.setPlainText("Change the value to after.")
    window.describe._start()
    wait_until(qt_app, lambda: _screen(window) == "exchange",
               message="plan packet")
    window.show()
    qt_app.processEvents()
    assert window.exchange._wait_timer.isActive()
    window.show_history()
    qt_app.processEvents()
    assert not window.exchange._wait_timer.isActive()
    window.show_screen("exchange")
    qt_app.processEvents()
    assert window.exchange._wait_timer.isActive()
    window._stop_run()
    wait_until(qt_app, lambda: not window.controller.busy, message="stopped")
    window.close()


def test_app_icon_paints_every_size(qt_app):
    from maintain.ui.widgets import app_icon
    icon = app_icon()
    assert not icon.isNull()
    assert len(icon.availableSizes()) >= 5


def test_exchange_screen_fits_the_default_window(qt_app, tmp_path,
                                                 monkeypatch):
    """The screenshot from the real computer showed the reply zone cut
    off; the content must fit the default window with slack to spare."""
    window, errors, toasts = _wired_window(tmp_path, monkeypatch)
    window.show()
    window.home.new_change.emit("feature")
    window.describe.request_edit.setPlainText("Change the value to after.")
    window.describe._start()
    wait_until(qt_app, lambda: _screen(window) == "exchange",
               message="plan packet")
    scroll = window.exchange.layout().itemAt(0).widget()
    content = scroll.widget().sizeHint().height()
    assert content <= 700, f"exchange content grew to {content}px"
    assert content <= scroll.viewport().height(), "the reply zone is cut off"
    window._stop_run()
    wait_until(qt_app, lambda: not window.controller.busy, message="stopped")
    window.close()
    assert not errors, errors


def test_busy_steps_animate_and_check_off(qt_app, tmp_path, monkeypatch):
    window, errors, toasts = _wired_window(tmp_path, monkeypatch)
    window.busy.show_message("The tool builds the plan package.")
    assert window.busy.steps._column.count() == 0
    window.busy.on_progress("start", "FILES", "Find the project files")
    assert window.busy.steps._active is not None
    window.busy.on_progress("complete", "FILES", "Selected the files")
    assert window.busy.steps._active is None
    window.busy.on_progress("start", "PACKET", "Build the plan package")
    window.busy.on_progress("failed", "PACKET", "The build stopped")
    marks = []
    for index in range(window.busy.steps._column.count()):
        holder = window.busy.steps._column.itemAt(index).widget()
        marks.append(holder.layout().itemAt(0).widget().text())
    assert marks == ["✓", "✗"]
    window.busy.show_message("Again")
    assert window.busy.steps._column.count() == 0


def test_explain_render_steps_reach_the_result_screen(qt_app, tmp_path,
                                                      monkeypatch):
    window, errors, toasts = _wired_window(tmp_path, monkeypatch)
    window.explain_result.show_running("/tmp/render")
    assert window.explain_result.steps._column.count() == 1   # scene check ✓
    window.explain_render_step.emit("start", "Check the geometry")
    qt_app.processEvents()
    assert window.explain_result.steps._active is not None
    window.explain_render_step.emit("complete", "")
    window.explain_render_step.emit("start", "Render the video")
    window.explain_render_step.emit("failed", "")
    qt_app.processEvents()
    assert window.explain_result.steps._active is None
    assert window.explain_result.steps._column.count() == 3


def test_markdown_packet_carries_everything_readable(tmp_path):
    import zipfile as zip_module

    from maintain.zip_package import markdown_packet, packet_sidecars

    packet = tmp_path / "maintain-run-plan.zip"
    with zip_module.ZipFile(packet, "w") as archive:
        archive.writestr("TASK.md", "# Task\n\n```json\n{\"a\": 1}\n```\n")
        archive.writestr("GLOBAL.md", "# Rules\n")
        archive.writestr("MANIFEST.json", '{"run": "r-1"}')
        archive.writestr("documents/standards.md", "# Standards\n")
        archive.writestr("attachments/spec.pdf", b"%PDF-1.4\x00binary")
    rendered = markdown_packet(packet)
    content = rendered.read_text(encoding="utf-8")
    assert rendered.suffix == ".md"
    assert "## FILE: TASK.md" in content
    assert "## FILE: GLOBAL.md" in content
    assert '````json\n{"run": "r-1"}\n````' in content
    # The inner fence of TASK.md survives intact.
    assert '```json\n{"a": 1}\n```' in content
    # The binary attachment is declared, never embedded.
    assert "attachments/spec.pdf" in content
    assert "Attach this file with the packet" in content
    assert "%PDF" not in content.replace("attachments/spec.pdf", "")
    assert packet_sidecars(packet) == ["attachments/spec.pdf"]


def test_markdown_style_shows_the_md_as_the_package(qt_app, tmp_path,
                                                    monkeypatch):
    window, errors, toasts = _wired_window(tmp_path, monkeypatch)
    assert window.store.config.package.style == "markdown"   # the default
    window.home.new_change.emit("feature")
    window.describe.request_edit.setPlainText("Change the value to after.")
    window.describe._start()
    wait_until(qt_app, lambda: _screen(window) == "exchange",
               message="plan packet")
    shown = window.exchange.card.packet_path
    assert str(shown).endswith(".md")
    body = Path(shown).read_text(encoding="utf-8")
    assert "## FILE: TASK.md" in body and "## FILE: CODEBASE.md" in body
    # The reply contract is unchanged: the run still validates and moves.
    window.exchange.check(clipboard_text=_scope_reply(window.current_handoff))
    wait_until(qt_app, lambda: _screen(window) == "plan", message="plan gate")
    window._stop_run()
    wait_until(qt_app, lambda: not window.controller.busy, message="stopped")
    assert not errors, errors


def test_package_page_offers_and_saves_all_three_styles(qt_app, tmp_path,
                                                        monkeypatch):
    window, errors, toasts = _wired_window(tmp_path, monkeypatch)
    window._open_settings_page("package")
    assert window.page_package.markdown_radio.isChecked()
    window.page_package.zip_radio.setChecked(True)
    window.page_package._save()
    assert window.store.config.package.style == "zip"
    window._open_settings_page("package")
    window.page_package.markdown_radio.setChecked(True)
    window.page_package._save()
    assert window.store.config.package.style == "markdown"


def test_packet_lands_in_the_clipboard_on_arrival(qt_app, tmp_path,
                                                  monkeypatch):
    """The main route: the one Markdown file is in the clipboard the
    moment the packet appears — one paste into Copilot, done."""
    from maintain.ui.strings import text as ui_text

    window, errors, toasts = _wired_window(tmp_path, monkeypatch)
    window.home.new_change.emit("feature")
    window.describe.request_edit.setPlainText("Change the value to after.")
    window.describe._start()
    wait_until(qt_app, lambda: _screen(window) == "exchange",
               message="plan packet")
    from PySide6.QtWidgets import QApplication
    mime = QApplication.clipboard().mimeData()
    assert mime.hasUrls(), "the packet file is not in the clipboard"
    copied = Path(mime.urls()[0].toLocalFile())
    assert copied.suffix == ".md" and copied.is_file()
    assert window.exchange.send_status.text() == ui_text("send.file.copied")
    # The region stays open — attachments are still at hand; only a
    # copy by hand folds it to the one-line summary (FR-F2).
    exchange = window.exchange
    assert exchange._send_full.isVisibleTo(exchange)
    window._stop_run()
    wait_until(qt_app, lambda: not window.controller.busy, message="stopped")
    assert not errors, errors


def test_manim_resolves_from_the_app_environment(tmp_path, monkeypatch):
    import maintain.render as render_module

    # A configured custom command passes through untouched.
    assert render_module.resolve_manim_command("py -m manim") == "py -m manim"
    # The default resolves to the interpreter's sibling script when
    # PATH cannot see manim (pipx exposes only the app's own commands).
    fake_env = tmp_path / "venv"
    fake_env.mkdir()
    sibling = fake_env / ("manim.exe" if sys.platform == "win32"
                         else "manim")
    sibling.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(render_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(render_module.sys, "executable",
                        str(fake_env / "python"))
    assert render_module.resolve_manim_command("manim") == str(sibling)
    assert render_module.manim_available(str(sibling))
    # Nothing anywhere: the default stays and is reported absent.
    sibling.unlink()
    assert render_module.resolve_manim_command("manim") == "manim"
    assert not render_module.manim_available("manim")


def test_explain_offers_the_manim_install_and_resumes(qt_app, tmp_path,
                                                      monkeypatch):
    import maintain.ui.app as app_module

    window, errors, toasts = _wired_window(tmp_path, monkeypatch)
    monkeypatch.setattr("maintain.render.manim_available", lambda c: False)
    monkeypatch.setattr("maintain.render.resolve_manim_command", lambda c: c)
    monkeypatch.setattr(app_module.sys, "version_info", (3, 12, 0),
                        raising=False) if hasattr(app_module, "sys") else None
    installed = {"count": 0}

    def fake_install() -> bool:
        installed["count"] += 1
        monkeypatch.setattr("maintain.render.manim_available",
                            lambda c: True)
        return True

    monkeypatch.setattr(app_module, "_pip_install_manim", fake_install)
    asked = {"count": 0}

    def confirm(*args, **kwargs):
        asked["count"] += 1
        return True

    window.ask_confirm = confirm
    window.show_explain()
    window.explain.goal_edit.setPlainText("Explain the value flow.")
    window.explain.include_code.setChecked(True)
    window.explain._start()
    wait_until(qt_app, lambda: installed["count"] == 1, message="install ran")
    wait_until(qt_app, lambda: _screen(window) == "exchange",
               message="explain packet after install")
    assert asked["count"] == 1
    assert any("video feature is ready" in item for item in toasts)
    window._stop_run()
    assert not errors, errors


def _tiny_pdf(content: str) -> bytes:
    """A minimal, valid one-page PDF with real text, byte-exact xref."""
    stream = f"BT /F1 12 Tf 50 700 Td ({content}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
        + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    head = b"%PDF-1.4\n"
    body = b""
    offsets = []
    for number, obj in enumerate(objects, 1):
        offsets.append(len(head) + len(body))
        body += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_at = len(head) + len(body)
    xref = b"xref\n0 6\n0000000000 65535 f \n" + b"".join(
        f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    trailer = (b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"
               + str(xref_at).encode() + b"\n%%EOF\n")
    return head + body + xref + trailer


def _tiny_docx(paragraphs: list[str]) -> bytes:
    import io
    buffer = io.BytesIO()
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
                   for text in paragraphs)
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml",
                         f'<?xml version="1.0"?><w:document><w:body>{body}'
                         "</w:body></w:document>")
    return buffer.getvalue()


def test_extract_text_reads_pdf_docx_and_refuses_images():
    from maintain.extract import extract_text

    pdf = extract_text("spec.pdf", _tiny_pdf("Wind limits apply above FL100"))
    assert pdf.ok and "Wind limits apply above FL100" in pdf.text

    docx = extract_text("notes.docx", _tiny_docx(
        ["Reference notes", "Use metres per second"]))
    assert docx.ok
    assert "Reference notes\nUse metres per second" in docx.text

    image = extract_text("diagram.png", b"\x89PNG\r\n\x1a\n\x00")
    assert not image.ok and "not a text-carrying" in image.note

    broken = extract_text("broken.pdf", b"%PDF-1.4 not really")
    assert not broken.ok


def test_markdown_packet_embeds_extracted_reference_text(tmp_path):
    from maintain.zip_package import markdown_packet, packet_sidecars

    packet = tmp_path / "maintain-run-plan.zip"
    with zipfile.ZipFile(packet, "w") as archive:
        archive.writestr("TASK.md", "# Task\n")
        archive.writestr("attachments/limits.pdf",
                         _tiny_pdf("Never exceed 250 knots below FL100"))
        archive.writestr("attachments/notes.docx",
                         _tiny_docx(["The reference values are final."]))
        archive.writestr("attachments/photo.png", b"\x89PNG\r\n\x1a\n\x00")
    content = markdown_packet(packet).read_text(encoding="utf-8")
    assert "## FILE: attachments/limits.pdf (extracted text)" in content
    assert "Never exceed 250 knots below FL100" in content
    assert "## FILE: attachments/notes.docx (extracted text)" in content
    assert "The reference values are final." in content
    # Only the image stays a separate attachment, with its reason.
    assert "attachments/photo.png" in content
    assert "not a text-carrying" in content
    assert packet_sidecars(packet) == ["attachments/photo.png"]


def test_explain_result_embeds_and_releases_the_video(qt_app, tmp_path,
                                                      monkeypatch):
    """The finished scene plays inside the result view; a new render or
    a step away lets go of the file, so nothing stays locked."""
    from PySide6.QtCore import Signal
    from PySide6.QtWidgets import QWidget

    import maintain.ui.screens as screens_module

    class FakePanel(QWidget):
        failed = Signal()

        def __init__(self):
            super().__init__()
            self.calls = []

        def load(self, path):
            self.calls.append(("load", str(path)))

        def stop(self):
            self.calls.append(("stop",))

    made = []

    def fake_factory():
        made.append(FakePanel())
        return made[-1]

    monkeypatch.setattr(screens_module, "make_video_panel", fake_factory)
    screen = screens_module.ExplainResultScreen()
    screen.show()
    qt_app.processEvents()
    screen.show_running(str(tmp_path))
    assert not screen.video_area.isVisibleTo(screen)

    video = tmp_path / "scene.mp4"
    video.write_bytes(b"x")
    screen.show_passed(None, video)
    assert made, "the player was never made"
    panel = made[0]
    assert ("load", str(video)) in panel.calls
    assert screen.video_area.isVisibleTo(screen)
    assert not screen.video_hint.isVisibleTo(screen)
    assert not screen.sheet_view.isVisibleTo(screen)   # no still repeat

    # A new render stops the playback before anything else runs.
    screen.show_running(str(tmp_path))
    assert ("stop",) in panel.calls
    assert not screen.video_area.isVisibleTo(screen)

    # Leaving the view releases the file.
    panel.calls.clear()
    screen.show_passed(None, video)
    screen.hide()
    qt_app.processEvents()
    assert ("stop",) in panel.calls

    # A decode failure falls back to the plain buttons, quietly.
    panel.calls.clear()
    screen.show()
    screen.show_passed(None, video)
    panel.failed.emit()
    qt_app.processEvents()
    assert ("stop",) in panel.calls
    assert not screen.video_area.isVisibleTo(screen)
    assert len(made) == 1   # one player, reused for every render


def test_explain_result_hints_at_setup_without_the_player(qt_app, tmp_path,
                                                          monkeypatch):
    import maintain.ui.screens as screens_module

    monkeypatch.setattr(screens_module, "make_video_panel", lambda: None)
    screen = screens_module.ExplainResultScreen()
    video = tmp_path / "scene.mp4"
    video.write_bytes(b"x")
    screen.show_passed(None, video)
    assert screen.video_hint.isVisibleTo(screen)
    assert not screen.video_area.isVisibleTo(screen)
    # Without a video there is nothing to enable; no hint either.
    screen.show_running(str(tmp_path))
    screen.show_passed(None, None)
    assert not screen.video_hint.isVisibleTo(screen)


def test_manim_install_also_brings_the_video_player(monkeypatch):
    """The one in-app install enables the render and the in-window
    player together, pinned to the Qt that already runs."""
    import subprocess
    import types as types_module

    import PySide6

    from maintain.ui.app import _pip_install_manim

    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = list(argv)
        return types_module.SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _pip_install_manim() is True
    joined = " ".join(seen["argv"])
    assert "manim==0.20.1" in joined
    assert f"PySide6-Addons=={PySide6.__version__}" in joined
