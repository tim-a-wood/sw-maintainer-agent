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
    errors: list[str] = []
    window.show_error = errors.append
    toasts: list[str] = []
    window.toast = toasts.append
    return window, errors, toasts


# ---------- the settings round-trip ----------

def test_settings_round_trip_through_every_page(qt_app, tmp_path, monkeypatch):
    from maintain.config import ProjectConfig
    from maintain.onedrive import onedrive_settings
    from maintain.repository_memory import load_ui_settings
    from maintain.ui.screens import documents_count
    from maintain.ui.strings import text as ui_text

    window, errors, toasts = _wired_window(tmp_path, monkeypatch)
    window.home.open_settings.emit()
    assert _screen(window) == "settings"

    # OneDrive: folder, link, timeout, auto-link, and the Downloads path.
    window.pick_directory = lambda: str(tmp_path / "onedrive")
    window._open_settings_page("onedrive")
    assert _screen(window) == "set-onedrive"
    window.page_onedrive.browse.emit()
    assert window.page_onedrive.folder_edit.text() == str(tmp_path / "onedrive")
    window.page_onedrive.link_edit.setText("https://1drv.example/maintain/")
    assert "maintain-run-plan.zip" in window.page_onedrive.example.text()
    window.page_onedrive.timeout_edit.setValue(33)
    window.page_onedrive.autolink_box.setChecked(False)
    window.page_onedrive.downloads_edit.setText(str(tmp_path / "dl"))
    window.page_onedrive._save()
    assert _screen(window) == "settings"
    assert toasts[-1] == ui_text("settings.saved")
    stored = onedrive_settings()
    assert stored.folder == str(tmp_path / "onedrive")
    assert stored.link_base == "https://1drv.example/maintain/"
    assert stored.timeout_seconds == 33
    values = load_ui_settings()
    assert values["auto_link"] is False
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

    # Tasks: a project document, a task document, and an own plan prompt.
    inside = tmp_path / "project" / "docs.md"
    inside.write_text("# Docs\n", encoding="utf-8")
    outside = tmp_path / "elsewhere.md"
    outside.write_text("# Outside\n", encoding="utf-8")
    window.pick_files = lambda: [str(inside)]
    window._open_settings_page("tasks")
    assert _screen(window) == "set-tasks"
    window.page_tasks.add_doc.emit(None)
    assert "docs.md" in window.store.config.package.documents
    window.pick_files = lambda: [str(outside)]
    window.page_tasks.set_tab("plan")
    window.page_tasks.add_doc.emit("plan")
    assert str(outside) in window.store.config.package.task("plan").documents
    assert documents_count(window.store, "plan") == 2

    overridden, builtin = window.store.task_prompt("plan")
    assert overridden is False and "tasks" in builtin.lower()
    window.page_tasks._toggle_prompt()
    overridden, _ = window.store.task_prompt("plan")
    assert overridden is True
    window.page_tasks.prompt_edit.setPlainText("Plan it my way.")
    window.page_tasks._save()
    overridden, prompt = window.store.task_prompt("plan")
    assert overridden is True and prompt == "Plan it my way."
    window._open_settings_page("tasks")
    window.page_tasks.set_tab("plan")
    window.page_tasks._toggle_prompt()
    overridden, _ = window.store.task_prompt("plan")
    assert overridden is False

    window.page_tasks.remove_doc.emit("plan", str(outside))
    assert not window.store.config.package.task("plan").documents
    window.page_tasks.set_tab("project")
    window.page_tasks.remove_doc.emit(None, "docs.md")
    assert not window.store.config.package.documents

    # Package style: folder, persisted on disk, then back to zip.
    window._open_settings_page("package")
    window.page_package.folder_radio.setChecked(True)
    window.page_package._save()
    assert window.store.config.package.style == "folder"
    assert window.exchange.package_style == "folder"
    reloaded = ProjectConfig.load(window.store.path)
    assert reloaded.package.style == "folder"
    window._open_settings_page("package")
    window.page_package.zip_radio.setChecked(True)
    window.page_package._save()
    assert window.store.config.package.style == "zip"

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

    # Explain: the Manim command is a per-user setting.
    window._open_settings_page("explain")
    window.page_explain.command_edit.setText("python -m manim")
    window.page_explain.saved.emit()
    assert load_ui_settings()["manim_command"] == "python -m manim"

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
                 "set-onedrive", "set-tasks", "set-global", "set-package",
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
    wait_until(qt_app, lambda: _screen(window) == "exchange",
               message="explain packet")
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
