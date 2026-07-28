"""The main window: navigation, bridge wiring, and dialogs."""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QInputDialog, QLabel,
                               QMainWindow, QMessageBox, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from maintain.config import ProjectConfig
from maintain.errors import MaintainError
from maintain.gates import GateDecision
from maintain.models import RunRecord, RunState
from maintain.providers.manual_ui import PacketHandoff

from .config_store import ConfigStore
from .controller import Controller
from .screens import (BusyScreen, ChecksPage, DescribeScreen, DoneScreen,
                      FindingsScreen, GlobalPage, HistoryScreen, HomeScreen,
                      OneDrivePage, PackagePage, PlanCheckScreen,
                      ReceiveScreen, RunDetailScreen, SaveScreen,
                      SettingsScreen, SendScreen, TasksPage, TestScreen,
                      documents_count)
from .strings import text
from .widgets import StageHeader

STAGE_FOR_TASK = {"plan": 0, "build": 1, "repair": 1, "review": 2}


class MainWindow(QMainWindow):
    def __init__(self, config: ProjectConfig) -> None:
        super().__init__()
        self.setWindowTitle(f"{text('app.title')} — {config.name}")
        self.resize(640, 760)
        self.store = ConfigStore(config)
        self.controller = Controller(config)
        self.current_handoff: PacketHandoff | None = None
        self.current_record: RunRecord | None = None
        self._in_test = False

        central = QWidget()
        column = QVBoxLayout(central)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self.stage_header = StageHeader()
        self.stage_header.setVisible(False)
        column.addWidget(self.stage_header)
        self.stack = QStackedWidget()
        column.addWidget(self.stack, 1)
        column.addWidget(self._foot_bar())
        self.setCentralWidget(central)

        self.screens: dict[str, QWidget] = {}
        self._build_screens()
        self._wire_controller()
        self.show_home()

    # ----- construction -----

    def _foot_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("FootBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(14, 6, 10, 6)
        self.foot_label = QLabel("")
        self.foot_label.setObjectName("FootLabel")
        row.addWidget(self.foot_label)
        row.addStretch(1)
        self.foot_history = QPushButton(text("home.history"))
        self.foot_history.setObjectName("Ghost")
        self.foot_history.clicked.connect(self._open_live_timeline)
        self.foot_stop = QPushButton(text("stop.button"))
        self.foot_stop.setObjectName("Danger")
        self.foot_stop.clicked.connect(self._stop_run)
        row.addWidget(self.foot_history)
        row.addWidget(self.foot_stop)
        self._set_run_footer(False)
        return bar

    def _build_screens(self) -> None:
        config = self.store.config
        self.home = HomeScreen(config.name, str(config.repository))
        self.describe = DescribeScreen()
        self.send = SendScreen()
        self.receive = ReceiveScreen()
        self.plan_check = PlanCheckScreen()
        self.findings = FindingsScreen()
        self.test = TestScreen()
        self.save = SaveScreen()
        self.done = DoneScreen()
        self.history = HistoryScreen()
        self.run_detail = RunDetailScreen()
        self.busy = BusyScreen()
        self.settings = SettingsScreen()
        self.page_onedrive = OneDrivePage()
        self.page_tasks = TasksPage(self.store)
        self.page_global = GlobalPage(self.store)
        self.page_package = PackagePage()
        self.page_checks = ChecksPage()
        for name, screen in [
                ("home", self.home), ("describe", self.describe),
                ("send", self.send), ("receive", self.receive),
                ("plan", self.plan_check), ("findings", self.findings),
                ("test", self.test), ("save", self.save), ("done", self.done),
                ("history", self.history), ("run", self.run_detail),
                ("busy", self.busy), ("settings", self.settings),
                ("set-onedrive", self.page_onedrive),
                ("set-tasks", self.page_tasks), ("set-global", self.page_global),
                ("set-package", self.page_package),
                ("set-checks", self.page_checks)]:
            self.screens[name] = screen
            self.stack.addWidget(screen)

        self.home.new_change.connect(self._new_change)
        self.home.open_history.connect(self.show_history)
        self.home.open_settings.connect(lambda: self.show("settings"))
        self.home.continue_run.connect(self._continue_run)

        self.describe.start.connect(self._start_run)
        self.describe.back.connect(self.show_home)
        self.describe.import_requested.connect(self._import_run_files)

        self.send.continue_clicked.connect(lambda: self.show("receive"))
        self.send.add_attachments.connect(self._add_packet_files)
        self.send.remove_attachment.connect(self._remove_packet_file)
        self.send.import_attachments.connect(self._import_packet_files)
        self.send.export_requested.connect(self._export_packet)

        self.receive.reply_submitted.connect(self._reply_submitted)
        self.receive.kept_attachment.connect(self._keep_for_next_packet)
        self.receive.back.connect(lambda: self.show("send"))
        self.receive.import_requested.connect(self._import_reply)

        self.plan_check.accept.connect(
            lambda: self._answer_gate(GateDecision("accept")))
        self.plan_check.rescope.connect(
            lambda: self._gate_with_note("plan", "rescope"))
        self.findings.repair.connect(
            lambda: self._answer_gate(GateDecision("repair")))
        self.findings.rescope.connect(
            lambda: self._gate_with_note("rescope", "rescope"))
        self.test.repair.connect(lambda: self._answer_gate(GateDecision("repair")))
        self.test.rescope.connect(lambda: self._gate_with_note("rescope", "rescope"))

        self.save.accept.connect(self._accept_and_save)
        self.save.feedback.connect(self._feedback)
        self.save.discard.connect(self._discard)
        self.save.rerun.connect(self._rerun_checks)

        self.done.new_change.connect(lambda: self._new_change("feature"))
        self.done.open_history.connect(self.show_history)

        self.history.open_run.connect(self._open_run)
        self.history.back.connect(self.show_home)
        self.run_detail.back.connect(self.show_history)
        self.run_detail.go_back_to.connect(self._go_back_to)
        self.run_detail.undo_last.connect(
            lambda: self._go_back_to(self.run_detail.undo_target))

        self.settings.back.connect(self.show_home)
        self.settings.open_page.connect(self._open_settings_page)
        for page in (self.page_onedrive, self.page_tasks, self.page_global,
                     self.page_package, self.page_checks):
            page.back.connect(lambda: self.show("settings"))
        self.page_onedrive.saved.connect(self._settings_saved)
        self.page_tasks.saved.connect(self._settings_saved)
        self.page_tasks.add_doc.connect(self._add_document)
        self.page_tasks.remove_doc.connect(self._remove_document)
        self.page_global.saved.connect(self._settings_saved)
        self.page_package.saved.connect(self._package_saved)
        self.page_checks.saved.connect(self._checks_saved)

    def _wire_controller(self) -> None:
        bridge = self.controller.bridge
        bridge.packet_ready.connect(self._packet_ready)
        bridge.plan_ready.connect(self._plan_ready)
        bridge.findings_ready.connect(self._findings_ready)
        bridge.checks_failed.connect(self._checks_failed)
        self.controller.progress_event.connect(self._progress)
        self.controller.run_settled.connect(self._run_settled)
        self.controller.run_error.connect(self._run_failed)

    # ----- navigation -----

    def show(self, name: str) -> None:
        self.stack.setCurrentWidget(self.screens[name])

    def show_home(self) -> None:
        self._set_run_footer(False)
        self.stage_header.setVisible(False)
        summary = self.controller.resumable_run()
        self.home.set_resumable(summary)
        self.show("home")

    def show_history(self) -> None:
        self.history.show_runs(self.controller.runs())
        self.show("history")

    def _set_stage(self, index: int) -> None:
        self.stage_header.setVisible(True)
        self.stage_header.set_stage(index)

    def _set_run_footer(self, active: bool, run_id: str = "") -> None:
        self.foot_history.setVisible(active)
        self.foot_stop.setVisible(active)
        self.foot_label.setText(
            text("app.footer", run=run_id) if active and run_id
            else "Maintain")

    # ----- run start and resume -----

    def _new_change(self, mode: str) -> None:
        self.describe.reset(mode)
        self.show("describe")

    def _start_run(self, mode: str, request: str, attachments: list) -> None:
        if self.controller.start_run(mode, request, attachments):
            self.busy.show_message(text("working.plan"))
            self._set_stage(0)
            self.show("busy")

    def _continue_run(self, run_id: str) -> None:
        summary = next((item for item in self.controller.runs()
                        if item.run_id == run_id), None)
        if summary is not None and summary.state == str(RunState.AWAITING_ACCEPTANCE):
            record = self._load_record(run_id)
            if record is not None:
                self._show_save(record)
                return
        if self.controller.resume(run_id):
            self.busy.show_message(text("working.busy"))
            self._set_run_footer(True, run_id)
            self.show("busy")

    def _load_record(self, run_id: str) -> RunRecord | None:
        import json
        path = self.store.config.runtime_root / run_id / "run.json"
        try:
            record = RunRecord.from_dict(
                json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return None
        self.current_record = record
        return record

    # ----- bridge: packets -----

    def _packet_ready(self, handoff: PacketHandoff) -> None:
        self.current_handoff = handoff
        self._set_run_footer(True, handoff.request.run_id)
        self._set_stage(STAGE_FOR_TASK[handoff.task_key])
        self.send.show_handoff(handoff, self._packet_names(),
                               documents_count(self.store, handoff.task_key))
        self.receive.show_handoff(handoff)
        self.show("send")

    def _packet_names(self) -> list[str]:
        return [Path(item).name for item in
                (*self.controller.run_attachments, *self.controller.packet_extras)]

    def _add_packet_files(self, paths: list) -> None:
        self.controller.packet_extras.extend(Path(item) for item in paths)
        self._rebuild_packet()

    def _remove_packet_file(self, index: int) -> None:
        run_count = len(self.controller.run_attachments)
        if index < run_count:
            del self.controller.run_attachments[index]
        else:
            del self.controller.packet_extras[index - run_count]
        self._rebuild_packet()

    def _import_packet_files(self) -> None:
        paths = self.pick_files()
        if paths:
            self._add_packet_files(paths)

    def _rebuild_packet(self) -> None:
        if self.current_handoff is None:
            return
        try:
            self.controller.rebuild_packet(self.current_handoff)
        except MaintainError as exc:
            self.toast(str(exc))
            return
        self.send.update_packet(self.current_handoff.zip_path, self._packet_names(),
                                documents_count(self.store,
                                                self.current_handoff.task_key))
        self.toast(text("send.updated"))

    def _export_packet(self) -> None:
        if self.current_handoff is None:
            return
        destination = self.pick_save(self.current_handoff.zip_path.name)
        if destination:
            shutil.copyfile(self.current_handoff.zip_path, destination)
            self.send.mark_exported(Path(destination).name)

    def _import_reply(self) -> None:
        paths = self.pick_files()
        if paths:
            self.receive.check(path=paths[0])

    def _reply_submitted(self, reply) -> None:
        self.controller.answer_reply(reply)
        self.busy.show_message(text("working.busy"))
        self.show("busy")

    def _keep_for_next_packet(self, paths: list) -> None:
        self.controller.run_attachments.extend(Path(item) for item in paths)

    # ----- bridge: gates -----

    def _plan_ready(self, record: RunRecord, tasks: list) -> None:
        self.current_record = record
        self._set_stage(0)
        self.plan_check.show_tasks(tasks)
        self.show("plan")

    def _findings_ready(self, record: RunRecord, findings: list) -> None:
        self.current_record = record
        self._set_stage(2)
        self.findings.show_findings(findings)
        self.show("findings")

    def _checks_failed(self, record: RunRecord, results: list) -> None:
        self.current_record = record
        self._set_stage(3)
        self.test.show_failed(results)
        self.show("test")

    def _answer_gate(self, decision: GateDecision) -> None:
        self.controller.answer_decision(decision)
        self.busy.show_message(text("working.busy"))
        self.show("busy")

    def _gate_with_note(self, kind: str, action: str) -> None:
        note = self.ask_note(text(f"note.title.{kind}" if kind != "rescope"
                                  else "note.title.rescope"),
                             text(f"note.body.{kind}" if kind != "rescope"
                                  else "note.body.rescope"))
        if note is None:
            return
        self._answer_gate(GateDecision(action, note))

    # ----- progress and completion -----

    def _progress(self, phase: str, label_key: str, message: str) -> None:
        self.busy.on_progress(phase, label_key, message)
        if label_key == "CHECK":
            if not self._in_test:
                self._in_test = True
                self.test.reset()
                self._set_stage(3)
                self.show("test")
            self.test.on_progress(phase, label_key, message)

    def _run_settled(self, record: RunRecord) -> None:
        self.current_record = record
        self._in_test = False
        state = RunState(record.state)
        if state is RunState.AWAITING_ACCEPTANCE:
            self._show_save(record)
        elif state is RunState.DELIVERED:
            self.done.show_record(record)
            self._set_run_footer(False)
            self._set_stage(5)
            self.stage_header.setVisible(False)
            self.show("done")
        elif state is RunState.CANCELLED:
            self.toast(text("discard.done"))
            self.show_home()
        elif state is RunState.NEEDS_HUMAN:
            self.toast(record.error or text("paused.body"))
            self.show_home()
        else:
            self.show_home()

    def _show_save(self, record: RunRecord) -> None:
        self._set_run_footer(True, record.run_id)
        self._set_stage(4)
        changed = self.controller.changed_files(record)
        self.save.show_record(record, changed, self.controller.diff_text(record))
        self.show("save")

    def _run_failed(self, message: str) -> None:
        self._in_test = False
        self.show_error(message)
        self.show_home()

    # ----- save actions -----

    def _accept_and_save(self) -> None:
        if self.current_record is None:
            return
        if self.controller.accept_and_deliver(self.current_record.run_id):
            self.busy.show_message(text("working.busy"))
            self.show("busy")

    def _feedback(self) -> None:
        if self.current_record is None:
            return
        note = self.ask_note(text("note.title.feedback"), text("note.body.feedback"))
        if note is None:
            return
        if self.controller.feedback(self.current_record.run_id, note):
            self.busy.show_message(text("working.busy"))
            self.show("busy")

    def _discard(self) -> None:
        if self.current_record is None:
            return
        if not self.ask_confirm(text("discard.title"),
                                text("discard.body",
                                     run=self.current_record.run_id),
                                text("discard.yes"), text("discard.no")):
            return
        if self.controller.discard(self.current_record.run_id):
            self.busy.show_message(text("working.busy"))
            self.show("busy")

    def _rerun_checks(self) -> None:
        if self.current_record is None:
            return
        if self.controller.rerun_checks(self.current_record.run_id):
            self.busy.show_message(text("working.checks"))
            self.show("busy")

    # ----- history and revert -----

    def _open_live_timeline(self) -> None:
        run_id = ""
        if self.current_handoff is not None:
            run_id = self.current_handoff.request.run_id
        elif self.current_record is not None:
            run_id = self.current_record.run_id
        if run_id:
            self._open_run(run_id)

    def _open_run(self, run_id: str) -> None:
        summary = next((item for item in self.controller.runs()
                        if item.run_id == run_id), None)
        if summary is None:
            return
        timeline = self.controller.timeline(run_id)
        self.run_detail.show_timeline(summary, timeline, live=not summary.closed)
        self._open_run_id = run_id
        self.show("run")

    def _go_back_to(self, sequence: int) -> None:
        if sequence < 0 or not getattr(self, "_open_run_id", ""):
            return
        timeline = self.controller.timeline(self._open_run_id)
        target = next((item for item in timeline if item.sequence == sequence), None)
        if target is None:
            return
        index = timeline.index(target) + 1
        if not self.ask_confirm(text("run.confirm.title", n=index),
                                text("run.confirm.body", label=target.label),
                                text("run.confirm.yes"), text("run.confirm.no")):
            return
        if self.controller.busy:
            # The engine waits at a bridge question; release it first.
            self.controller.stop()
        if self.controller.revert_and_continue(self._open_run_id, sequence):
            self.toast(text("run.went_back"))
            self.busy.show_message(text("working.busy"))
            self.show("busy")

    # ----- stop -----

    def _stop_run(self) -> None:
        if not self.ask_confirm(text("stop.title"), text("stop.body"),
                                text("stop.yes"), text("stop.no")):
            return
        self.controller.stop()

    # ----- settings -----

    def _open_settings_page(self, page: str) -> None:
        if page == "onedrive":
            self.page_onedrive.load()
        elif page == "tasks":
            self.page_tasks.set_tab("project")
        elif page == "global":
            self.page_global.load()
        elif page == "package":
            self.page_package.load(self.store.config.package.style)
        elif page == "checks":
            self.page_checks.load(self.store.checks())
        self.show(f"set-{page}")

    def _settings_saved(self) -> None:
        self._after_config_change()
        self.toast(text("settings.saved"))
        self.show("settings")

    def _package_saved(self, style: str) -> None:
        try:
            self.store.set_style(style)
        except MaintainError as exc:
            self.show_error(str(exc))
            return
        self._settings_saved()

    def _checks_saved(self, rows: list) -> None:
        try:
            self.store.set_checks([(name, command) for name, command in rows])
        except MaintainError as exc:
            self.page_checks.message.setText(str(exc))
            return
        self._settings_saved()

    def _add_document(self, task) -> None:
        paths = self.pick_files()
        if not paths:
            return
        try:
            self.store.add_document(Path(paths[0]), task)
        except MaintainError as exc:
            self.show_error(str(exc))
            return
        self._after_config_change()
        self.page_tasks.refresh()

    def _remove_document(self, task, value: str) -> None:
        self.store.remove_document(value, task)
        self._after_config_change()
        self.page_tasks.refresh()

    def _after_config_change(self) -> None:
        if not self.controller.busy:
            self.controller = Controller(self.store.config)
            self._wire_controller()
        else:
            self.controller.config = self.store.config

    # ----- dialogs (overridable in tests) -----

    def ask_note(self, title: str, body: str) -> str | None:
        value, accepted = QInputDialog.getMultiLineText(self, title, body)
        value = value.strip()
        return value if accepted and value else None

    def ask_confirm(self, title: str, body: str, yes: str, no: str) -> bool:
        result = QMessageBox.question(
            self, title, body,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        return result == QMessageBox.StandardButton.Yes

    def pick_files(self) -> list[str]:
        paths, _ = QFileDialog.getOpenFileNames(self, text("describe.import"))
        return list(paths)

    def pick_save(self, name: str) -> str:
        path, _ = QFileDialog.getSaveFileName(self, text("send.export"), name)
        return path

    def show_error(self, message: str) -> None:
        QMessageBox.warning(self, text("error.title"), message)

    def toast(self, message: str) -> None:
        self.statusBar().showMessage(message, 6000)

    def _import_run_files(self) -> None:
        paths = self.pick_files()
        if paths:
            self.describe.add_files([Path(item) for item in paths])
