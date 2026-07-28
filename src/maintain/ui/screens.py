"""All screens. One screen, one decision. Text comes from the STE catalog."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton,
                               QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from maintain.history import IterationEvent, RunSummary
from maintain.models import RunRecord
from maintain.onedrive import (PENDING, SYNCED, OneDriveSettings,
                               onedrive_settings, publish_packet,
                               save_onedrive_settings)
from maintain.providers.manual_ui import PacketHandoff
from maintain.zip_package import global_prompt_text

from .config_store import BUILTIN_PROMPTS, ConfigStore
from .strings import text
from .widgets import DropZone, FileChips, PacketCard, button, label

TASK_TITLES = {"plan": "send.plan.title", "build": "send.build.title",
               "repair": "send.repair.title", "review": "send.review.title"}
TASK_STEPS = {"plan": "Step 1 of 5 — Plan", "build": "Step 2 of 5 — Build",
              "repair": "Step 2 of 5 — Build", "review": "Step 3 of 5 — Review"}


class Screen(QWidget):
    """A scrollable page with a single column of content."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Screen")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner.setObjectName("Screen")
        self.column = QVBoxLayout(inner)
        self.column.setContentsMargins(24, 20, 24, 22)
        self.column.setSpacing(12)
        self.column.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll)

    def add(self, widget: QWidget) -> QWidget:
        self.column.insertWidget(self.column.count() - 1, widget)
        return widget

    def add_row(self, *widgets: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        for widget in widgets:
            row.addWidget(widget)
        row.addStretch(1)
        holder = QWidget()
        holder.setLayout(row)
        self.add(holder)
        return row


class HomeScreen(Screen):
    new_change = Signal(str)     # mode
    open_history = Signal()
    open_settings = Signal()
    continue_run = Signal(str)   # run_id

    def __init__(self, project_name: str, project_path: str) -> None:
        super().__init__()
        self.add(label(project_name, "Title"))
        self.add(label(project_path, "Hint"))
        self._continue = button("", "Choice", None)
        self._continue.setVisible(False)
        self._continue.clicked.connect(self._emit_continue)
        self._continue_run_id = ""
        self.add(self._continue)
        self.add(button(f"{text('home.change')}\n{text('home.change.sub')}", "Choice",
                        lambda: self.new_change.emit("feature")))
        self.add(button(f"{text('home.fault')}\n{text('home.fault.sub')}", "Choice",
                        lambda: self.new_change.emit("issue")))
        self.add(button(f"{text('home.history')}\n{text('home.history.sub')}", "Choice",
                        self.open_history.emit))
        self.add(button(f"{text('home.settings')}\n{text('home.settings.sub')}", "Choice",
                        self.open_settings.emit))

    def set_resumable(self, summary: RunSummary | None, stage_name: str = "") -> None:
        if summary is None:
            self._continue.setVisible(False)
            return
        self._continue_run_id = summary.run_id
        self._continue.setText(
            f"{text('home.continue', run=summary.run_id)}\n"
            f"{text('home.continue.sub', stage=stage_name or summary.display_state)}")
        self._continue.setVisible(True)

    def _emit_continue(self) -> None:
        if self._continue_run_id:
            self.continue_run.emit(self._continue_run_id)


class DescribeScreen(Screen):
    start = Signal(str, str, list)   # mode, request, attachments
    back = Signal()
    import_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.mode = "feature"
        self._title = label(text("describe.title"), "Title")
        self.add(self._title)
        self.request_edit = QPlainTextEdit()
        self.request_edit.setPlaceholderText(text("describe.placeholder"))
        self.request_edit.setFixedHeight(96)
        self.add(self.request_edit)
        zone = DropZone(text("describe.drop.main"), text("describe.drop.sub"))
        zone.files_dropped.connect(self.add_files)
        self.add(zone)
        self.chips = FileChips()
        self.chips.removed.connect(self._remove)
        self.add(self.chips)
        self.add_row(button(text("describe.import"), "Secondary",
                            self.import_requested.emit))
        self.message = label("", "Bad")
        self.add(self.message)
        self.add_row(
            button(text("describe.start"), "Primary", self._start),
            button(text("receive.back"), "Ghost", self.back.emit))
        self.attachments: list[Path] = []

    def reset(self, mode: str) -> None:
        self.mode = mode
        self._title.setText(text("describe.fault.title" if mode == "issue"
                                 else "describe.title"))
        self.request_edit.setPlainText("")
        self.attachments = []
        self.chips.set_files([])
        self.message.setText("")

    def add_files(self, paths: list[Path]) -> None:
        self.attachments.extend(Path(item) for item in paths)
        self.chips.set_files([item.name for item in self.attachments])

    def _remove(self, index: int) -> None:
        del self.attachments[index]
        self.chips.set_files([item.name for item in self.attachments])

    def _start(self) -> None:
        request = self.request_edit.toPlainText().strip()
        if not request:
            self.message.setText(text("describe.empty"))
            return
        self.message.setText("")
        self.start.emit(self.mode, request, list(self.attachments))


class SendScreen(Screen):
    continue_clicked = Signal()
    add_attachments = Signal(list)       # list[Path] added to this packet
    remove_attachment = Signal(int)
    import_attachments = Signal()
    export_requested = Signal()
    link_state = Signal(str, str)        # state, message (internal, thread-safe)

    def __init__(self) -> None:
        super().__init__()
        self.handoff: PacketHandoff | None = None
        self._out_action = False
        self.eyebrow = label("", "Eyebrow")
        self.add(self.eyebrow)
        self.title = label("", "Title")
        self.add(self.title)
        self.add(label(text("send.lead"), "Lead"))
        self.card = PacketCard()
        self.card.drag_started.connect(self._mark_out)
        self.add(self.card)
        self.contents = label("", "Hint")
        self.add(self.contents)
        self.add(label(text("send.attachments"), "Hint"))
        self.chips = FileChips()
        self.chips.removed.connect(self.remove_attachment.emit)
        self.add(self.chips)
        zone = DropZone(text("send.attach.drop"))
        zone.files_dropped.connect(self.add_attachments.emit)
        self.add(zone)
        self.add_row(button(text("send.attach.add"), "Secondary",
                            self.import_attachments.emit))
        self.link_button = button(text("send.copy_link"), "Primary", self._copy_link)
        self.add(self.link_button)
        self.add(label(text("send.copy_link.sub"), "Hint"))
        self.add_row(
            button(text("send.copy_file"), "Secondary", self._copy_file),
            button(text("send.export"), "Secondary", self.export_requested.emit))
        self.status = label("", "Lead")
        self.add(self.status)
        self.continue_button = button(text("send.continue"), "Primary",
                                      self.continue_clicked.emit)
        self.continue_button.setEnabled(False)
        self.caption = label(text("send.continue.before"), "Hint")
        self.add(self.continue_button)
        self.add(self.caption)
        self.link_state.connect(self._on_link_state)

    def show_handoff(self, handoff: PacketHandoff, attachment_names: list[str],
                     document_count: int) -> None:
        self.handoff = handoff
        self._out_action = False
        again = ""
        request = handoff.request
        if handoff.task_key == "plan" and "round-" in request.task_id:
            again = " · again"
        if handoff.task_key == "review" and not request.task_id.endswith("-1"):
            again = ""
        self.eyebrow.setText(TASK_STEPS[handoff.task_key].upper() + again.upper())
        self.title.setText(text(TASK_TITLES[handoff.task_key]))
        self.update_packet(handoff.zip_path, attachment_names, document_count)
        self.status.setText("")
        self.continue_button.setEnabled(False)
        self.caption.setText(text("send.continue.before"))

    def update_packet(self, zip_path: Path, attachment_names: list[str],
                      document_count: int) -> None:
        size = zip_path.stat().st_size if zip_path.is_file() else 0
        self.card.set_packet(zip_path, size)
        documents = (f"documents/ — {document_count} · " if document_count else "")
        self.contents.setText(
            f"{text('send.contents')}  TASK.md · GLOBAL.md · CODEBASE.md · "
            f"MANIFEST.json · {documents}attachments/ — {len(attachment_names)}")
        self.chips.set_files(attachment_names)

    def _mark_out(self) -> None:
        self._out_action = True
        self.continue_button.setEnabled(True)
        self.caption.setText(text("send.continue.after"))

    def _copy_file(self) -> None:
        if self.handoff is None:
            return
        from PySide6.QtCore import QMimeData, QUrl
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(self.card.packet_path))])
        QGuiApplication.clipboard().setMimeData(mime)
        self.status.setText(text("send.file.copied"))
        self._mark_out()

    def _copy_link(self) -> None:
        if self.handoff is None:
            return
        settings = onedrive_settings()
        packet = self.card.packet_path
        self.link_button.setEnabled(False)
        self.status.setText(text("send.link.copying"))
        expand = False
        style = getattr(self.parent(), "package_style", "zip")
        expand = style == "folder"

        def work() -> None:
            try:
                result = publish_packet(Path(packet), settings, expand_folder=expand)
            except Exception as exc:  # noqa: BLE001 - shown to the person
                self.link_state.emit("error", str(exc))
                return
            self.link_state.emit(result.sync_state, result.link)

        threading.Thread(target=work, daemon=True, name="maintain-onedrive").start()

    def _on_link_state(self, state: str, value: str) -> None:
        self.link_button.setEnabled(True)
        if state == "error":
            self.status.setText(value)
            return
        if value:
            QGuiApplication.clipboard().setText(value)
        if state == SYNCED:
            self.status.setText(f"{text('send.link.done')} {text('send.link.paste')}")
        elif state == PENDING:
            self.status.setText(text("send.link.manual"))
        else:
            self.status.setText(f"{text('send.link.paste')} {text('send.link.manual')}")
        self._mark_out()

    def mark_exported(self, name: str) -> None:
        self.status.setText(text("send.exported", name=name))
        self._mark_out()


class ReceiveScreen(Screen):
    reply_submitted = Signal(object)   # ManualReply
    kept_attachment = Signal(list)     # list[Path]
    back = Signal()
    import_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.handoff: PacketHandoff | None = None
        self.eyebrow = label("", "Eyebrow")
        self.add(self.eyebrow)
        self.add(label(text("receive.title"), "Title"))
        self.lead = label("", "Lead")
        self.add(self.lead)
        zone = DropZone(text("receive.drop"), "")
        zone.files_dropped.connect(self._dropped)
        self.add(zone)
        self.paste_button = button(text("receive.paste"), "Primary", self._paste)
        self.import_button = button(text("receive.import"), "Secondary",
                                    self.import_requested.emit)
        self.add_row(self.paste_button, self.import_button)
        self.status = label("", "Bad")
        self.add(self.status)
        self.add_row(button(text("receive.back"), "Ghost", self.back.emit))

    def show_handoff(self, handoff: PacketHandoff) -> None:
        self.handoff = handoff
        self.eyebrow.setText(TASK_STEPS[handoff.task_key].upper())
        zip_reply = handoff.reply_kind == "zip"
        self.lead.setText(text("receive.lead.zip" if zip_reply else "receive.lead.json"))
        self.paste_button.setVisible(not zip_reply)
        self.import_button.setVisible(True)
        self.status.setText("")

    def _paste(self) -> None:
        if self.handoff is None:
            return
        self.check(clipboard_text=QGuiApplication.clipboard().text())

    def _dropped(self, paths: list[Path]) -> None:
        if paths:
            self.check(path=paths[0])

    def check(self, *, clipboard_text: str = "", path: Path | None = None) -> None:
        from .bridge import check_reply
        if self.handoff is None:
            return
        result = check_reply(self.handoff, text=clipboard_text, path=path)
        if result.valid:
            self.status.setObjectName("Ok")
            self.status.setText(text("receive.checking"))
            self.reply_submitted.emit(result.reply)
            return
        if result.keep_as_attachment and path is not None:
            self.status.setObjectName("Lead")
            self.status.setText(text("receive.kept"))
            self.kept_attachment.emit([path])
        else:
            self.status.setObjectName("Bad")
            self.status.setText(result.message or text("receive.clipboard.empty"))
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)


class PlanCheckScreen(Screen):
    accept = Signal()
    rescope = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.add(label("STEP 1 OF 5 — PLAN", "Eyebrow"))
        self.title = label("", "Title")
        self.add(self.title)
        self._cards = QVBoxLayout()
        holder = QWidget()
        holder.setLayout(self._cards)
        self.add(holder)
        self.add_row(
            button(text("plan.accept"), "Primary", self.accept.emit),
            button(text("plan.rescope"), "Secondary", self.rescope.emit))

    def show_tasks(self, tasks: list[dict]) -> None:
        self.title.setText(text("plan.title.one") if len(tasks) == 1
                           else text("plan.title.many", count=len(tasks)))
        while self._cards.count():
            item = self._cards.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for index, task in enumerate(tasks, start=1):
            card = QFrame()
            card.setObjectName("Card")
            box = QVBoxLayout(card)
            box.setContentsMargins(14, 12, 14, 12)
            box.addWidget(label(f"{index}. {task.get('objective', '')}"))
            files = ", ".join(map(str, task.get("allowed_files", [])))
            box.addWidget(label(text("plan.files", files=files), "Hint"))
            done = "; ".join(map(str, task.get("done_when", [])))
            box.addWidget(label(text("plan.done_when", text=done), "Hint"))
            self._cards.addWidget(card)


class FindingsScreen(Screen):
    repair = Signal()
    rescope = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.add(label("STEP 3 OF 5 — REVIEW", "Eyebrow"))
        self.title = label("", "Title")
        self.add(self.title)
        self._cards = QVBoxLayout()
        holder = QWidget()
        holder.setLayout(self._cards)
        self.add(holder)
        self.add(label(text("findings.repair.sub"), "Hint"))
        self.add_row(
            button(text("findings.repair.button"), "Primary", self.repair.emit),
            button(text("findings.rescope"), "Ghost", self.rescope.emit))

    def show_findings(self, findings: list[dict]) -> None:
        self.title.setText(text("findings.title.one") if len(findings) == 1
                           else text("findings.title.many", count=len(findings)))
        while self._cards.count():
            item = self._cards.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for finding in findings:
            card = QFrame()
            card.setObjectName("Finding")
            box = QVBoxLayout(card)
            box.setContentsMargins(14, 12, 14, 12)
            location = f"{finding.get('severity', '')} · " \
                       f"{finding.get('file', '')}:{finding.get('line', '')}"
            box.addWidget(label(location, "Mono"))
            box.addWidget(label(f"{text('findings.evidence')} "
                                f"{finding.get('evidence', '')}"))
            box.addWidget(label(f"{text('findings.repair')} "
                                f"{finding.get('remediation', '')}"))
            self._cards.addWidget(card)


class TestScreen(Screen):
    repair = Signal()
    rescope = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.add(label("STEP 4 OF 5 — TEST", "Eyebrow"))
        self.add(label(text("test.title"), "Title"))
        self._rows = QVBoxLayout()
        holder = QWidget()
        holder.setLayout(self._rows)
        self.add(holder)
        self.outcome = label("", "Ok")
        self.add(self.outcome)
        self.repair_button = button(text("test.repair"), "Primary", self.repair.emit)
        self.rescope_button = button(text("test.rescope"), "Ghost", self.rescope.emit)
        self.add_row(self.repair_button, self.rescope_button)
        self._set_failed_controls(False)
        self._checks: dict[str, QLabel] = {}

    def reset(self) -> None:
        while self._rows.count():
            item = self._rows.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._checks = {}
        self.outcome.setText("")
        self._set_failed_controls(False)

    def _set_failed_controls(self, visible: bool) -> None:
        self.repair_button.setVisible(visible)
        self.rescope_button.setVisible(visible)

    def on_progress(self, phase: str, label_key: str, message: str) -> None:
        if label_key != "CHECK":
            return
        name = message.split(" check", 1)[0].removeprefix("Run the ").strip()
        if phase == "start":
            self._add_check(name, "…", "StateWait")
        elif phase == "complete":
            name = message.removeprefix("The ").removesuffix(" check passed").strip()
            self._add_check(name, "PASS", "StatePass")
        elif phase == "failed":
            name = message.removeprefix("The ").removesuffix(" check failed").strip()
            self._add_check(name, "FAIL", "StateFail")

    def _add_check(self, name: str, state: str, style: str) -> None:
        if name not in self._checks:
            row = QHBoxLayout()
            row_holder = QFrame()
            row_holder.setObjectName("Card")
            row.setContentsMargins(13, 9, 13, 9)
            row_holder.setLayout(row)
            row.addWidget(label(name))
            badge = QLabel(state)
            badge.setObjectName(style)
            row.addStretch(1)
            row.addWidget(badge)
            self._rows.addWidget(row_holder)
            self._checks[name] = badge
        badge = self._checks[name]
        badge.setText(state)
        badge.setObjectName(style)
        badge.style().unpolish(badge)
        badge.style().polish(badge)

    def show_failed(self, results: list[dict]) -> None:
        for result in results:
            name = str(result.get("name", "check"))
            passed = int(result.get("exit_code", 1) or 0) == 0
            self._add_check(name, "PASS" if passed else "FAIL",
                            "StatePass" if passed else "StateFail")
        self.outcome.setObjectName("Bad")
        self.outcome.setText(text("test.failed"))
        self._set_failed_controls(True)


class SaveScreen(Screen):
    accept = Signal()
    feedback = Signal()
    discard = Signal()
    rerun = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.record: RunRecord | None = None
        self.add(label("STEP 5 OF 5 — SAVE", "Eyebrow"))
        self.title = label("", "Title")
        self.add(self.title)
        self.files = label("", "Mono")
        self.add(self.files)
        self.diff_toggle = button(text("save.diff"), "Ghost", self._toggle_diff)
        self.add_row(self.diff_toggle)
        self.diff_view = QPlainTextEdit()
        self.diff_view.setObjectName("Code")
        self.diff_view.setReadOnly(True)
        self.diff_view.setVisible(False)
        self.diff_view.setMinimumHeight(180)
        self.add(self.diff_view)
        self.add(label(text("save.accept.sub"), "Hint"))
        self.add_row(
            button(text("save.accept"), "Primary", self.accept.emit),
            button(text("save.feedback"), "Secondary", self.feedback.emit),
            button(text("test.run_again"), "Ghost", self.rerun.emit),
            button(text("save.discard"), "Danger", self.discard.emit))

    def show_record(self, record: RunRecord, changed: list[str], diff: str) -> None:
        self.record = record
        self.title.setText(text("save.title", count=len(changed)))
        self.files.setText("\n".join(changed))
        self.diff_view.setPlainText(diff)
        self.diff_view.setVisible(False)
        self.diff_toggle.setText(text("save.diff"))

    def _toggle_diff(self) -> None:
        self.diff_view.setVisible(not self.diff_view.isVisible())


class DoneScreen(Screen):
    new_change = Signal()
    open_history = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.add(label(text("done.title"), "Title"))
        self.branch = label("", "Mono")
        self.add(self.branch)
        self.add(label(text("done.audit"), "Lead"))
        self.add_row(
            button(text("done.new"), "Primary", self.new_change.emit),
            button(text("done.history"), "Ghost", self.open_history.emit))

    def show_record(self, record: RunRecord) -> None:
        self.branch.setText(text("done.branch", branch=record.branch))


class HistoryScreen(Screen):
    open_run = Signal(str)
    back = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.add(label(text("history.title"), "Title"))
        self._rows = QVBoxLayout()
        holder = QWidget()
        holder.setLayout(self._rows)
        self.add(holder)
        self.empty = label(text("history.empty"), "Lead")
        self.add(self.empty)
        self.add_row(button(text("history.back"), "Ghost", self.back.emit))

    def show_runs(self, runs: list[RunSummary]) -> None:
        while self._rows.count():
            item = self._rows.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.empty.setVisible(not runs)
        for summary in runs:
            request = " ".join(summary.request.split())[:70]
            row = button(f"Run {summary.run_id} — {request}\n"
                         f"{summary.display_state} · {summary.updated_at[:10]}",
                         "Choice",
                         None)
            row.clicked.connect(
                lambda _=False, run_id=summary.run_id: self.open_run.emit(run_id))
            self._rows.addWidget(row)


class RunDetailScreen(Screen):
    go_back_to = Signal(int)    # sequence
    undo_last = Signal()
    back = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.title = label("", "Title")
        self.add(self.title)
        self.subtitle = label("", "Lead")
        self.add(self.subtitle)
        self.undo_button = button(text("run.undo"), "Secondary", self.undo_last.emit)
        self.add_row(self.undo_button)
        self._rows = QVBoxLayout()
        holder = QWidget()
        holder.setLayout(self._rows)
        self.add(holder)
        self.add_row(button(text("history.back"), "Ghost", self.back.emit))
        self.undo_target = -1

    def show_timeline(self, summary: RunSummary, timeline: list[IterationEvent],
                      live: bool) -> None:
        self.title.setText(text("run.title", run=summary.run_id))
        self.subtitle.setText(
            " ".join(summary.request.split())[:90] if live
            else text("run.readonly", state=summary.display_state.lower()))
        anchors = [item for item in timeline if item.can_go_back]
        self.undo_target = anchors[-2].sequence if live and len(anchors) >= 2 else -1
        self.undo_button.setVisible(live)
        self.undo_button.setEnabled(self.undo_target >= 0)
        while self._rows.count():
            item = self._rows.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        last_index = len(timeline) - 1
        for index, event in enumerate(timeline):
            card = QFrame()
            card.setObjectName("Card")
            grid = QGridLayout(card)
            grid.setContentsMargins(13, 9, 13, 9)
            title = label(f"{index + 1}.  {event.label}")
            grid.addWidget(title, 0, 0)
            if event.superseded:
                tag = QLabel(text("run.superseded"))
                tag.setObjectName("StateWait")
                grid.addWidget(tag, 0, 1)
            if event.sub:
                grid.addWidget(label(event.sub, "Hint"), 1, 0)
            time_label = label(event.time[11:16] or event.time[:10], "Hint")
            grid.addWidget(time_label, 0, 3)
            if live and event.can_go_back and index < last_index:
                go = button(text("run.goback"), "Ghost", None)
                go.clicked.connect(
                    lambda _=False, seq=event.sequence: self.go_back_to.emit(seq))
                grid.addWidget(go, 0, 2)
            grid.setColumnStretch(0, 1)
            self._rows.addWidget(card)


class BusyScreen(Screen):
    def __init__(self) -> None:
        super().__init__()
        self.title = label(text("working.busy"), "Title")
        self.add(self.title)
        self.status = label("", "Lead")
        self.add(self.status)

    def show_message(self, message: str) -> None:
        self.title.setText(message or text("working.busy"))
        self.status.setText("")

    def on_progress(self, phase: str, label_key: str, message: str) -> None:
        if phase in {"start", "complete"}:
            self.status.setText(message)


class SettingsScreen(Screen):
    open_page = Signal(str)
    back = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.add(label(text("settings.title"), "Title"))
        for key in ("onedrive", "tasks", "global", "package", "checks"):
            self.add(button(f"{text('settings.' + key)}\n"
                            f"{text('settings.' + key + '.sub')}", "Choice",
                            lambda page=key: self.open_page.emit(page)))
        self.add_row(button(text("settings.back"), "Ghost", self.back.emit))


class OneDrivePage(Screen):
    saved = Signal()
    back = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.add(label(text("settings.onedrive"), "Title"))
        self.add(label(text("onedrive.folder"), "Hint"))
        self.folder_edit = QLineEdit()
        self.add(self.folder_edit)
        self.add(label(text("onedrive.folder.hint"), "Hint"))
        self.add(label(text("onedrive.link"), "Hint"))
        self.link_edit = QLineEdit()
        self.link_edit.textChanged.connect(self._preview)
        self.add(self.link_edit)
        self.add(label(text("onedrive.link.hint"), "Hint"))
        self.example = label("", "Hint")
        self.add(self.example)
        self.add(label(text("onedrive.timeout"), "Hint"))
        self.timeout_edit = QSpinBox()
        self.timeout_edit.setRange(10, 900)
        self.add(self.timeout_edit)
        self.add(label(text("onedrive.timeout.hint"), "Hint"))
        self.add_row(
            button(text("settings.save"), "Primary", self._save),
            button(text("settings.back"), "Ghost", self.back.emit))

    def load(self) -> None:
        settings = onedrive_settings()
        self.folder_edit.setText(settings.folder)
        self.link_edit.setText(settings.link_base)
        self.timeout_edit.setValue(settings.timeout_seconds)

    def _preview(self, value: str) -> None:
        base = value.strip().rstrip("/")
        self.example.setText(
            text("onedrive.example", link=f"{base}/maintain-run-plan.zip") if base else "")

    def _save(self) -> None:
        save_onedrive_settings(OneDriveSettings(
            folder=self.folder_edit.text().strip(),
            link_base=self.link_edit.text().strip(),
            timeout_seconds=int(self.timeout_edit.value())))
        self.saved.emit()


class TasksPage(Screen):
    saved = Signal()
    back = Signal()
    add_doc = Signal(object)      # None for project, task key for task level
    remove_doc = Signal(object, str)

    def __init__(self, store: ConfigStore) -> None:
        super().__init__()
        self.store = store
        self.tab = "project"
        self.add(label(text("settings.tasks"), "Title"))
        tabs = QHBoxLayout()
        holder = QWidget()
        holder.setLayout(tabs)
        self._tab_buttons: dict[str, QPushButton] = {}
        for key in ("project", "plan", "build", "repair", "review"):
            name = text("tasks.project") if key == "project" else key.capitalize()
            control = button(name, "Secondary", lambda k=key: self.set_tab(k))
            tabs.addWidget(control)
            self._tab_buttons[key] = control
        tabs.addStretch(1)
        self.add(holder)
        self.section_title = label("", "Hint")
        self.add(self.section_title)
        self.prompt_state = label("", "Lead")
        self.add(self.prompt_state)
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setFixedHeight(140)
        self.add(self.prompt_edit)
        self.prompt_toggle = button("", "Secondary", self._toggle_prompt)
        self.add_row(self.prompt_toggle)
        self.docs_title = label("", "Hint")
        self.add(self.docs_title)
        self.docs = FileChips()
        self.docs.removed.connect(self._remove_doc)
        self.add(self.docs)
        self.add_row(button(text("tasks.docs.add"), "Secondary",
                            lambda: self.add_doc.emit(
                                None if self.tab == "project" else self.tab)))
        self.add_row(
            button(text("settings.save"), "Primary", self._save),
            button(text("settings.back"), "Ghost", self.back.emit))
        self._doc_values: list[str] = []
        self._override = False

    def set_tab(self, key: str) -> None:
        self._flush_prompt()
        self.tab = key
        self.refresh()

    def refresh(self) -> None:
        package = self.store.config.package
        project = self.tab == "project"
        self.prompt_state.setVisible(not project)
        self.prompt_edit.setVisible(not project)
        self.prompt_toggle.setVisible(not project)
        if project:
            self.section_title.setText(text("tasks.docs.project"))
            self.docs_title.setText(text("tasks.docs.project.hint"))
            self._doc_values = list(package.documents)
        else:
            self._override, prompt = self.store.task_prompt(self.tab)
            self.section_title.setText(text("tasks.prompt", task=self.tab))
            self.prompt_state.setText(text(
                "tasks.prompt.own" if self._override else "tasks.prompt.builtin"))
            self.prompt_edit.setPlainText(prompt)
            self.prompt_edit.setReadOnly(not self._override)
            self.prompt_toggle.setText(text(
                "tasks.prompt.reset" if self._override else "tasks.prompt.change"))
            self.docs_title.setText(text("tasks.docs.task", task=self.tab))
            self._doc_values = list(package.task(self.tab).documents)
        self.docs.set_files(self._doc_values)

    def _toggle_prompt(self) -> None:
        if self._override:
            self.store.set_task_prompt(self.tab, None)
        else:
            self.store.set_task_prompt(self.tab, BUILTIN_PROMPTS[self.tab])
        self.refresh()

    def _flush_prompt(self) -> None:
        if self.tab != "project" and self._override:
            self.store.set_task_prompt(self.tab, self.prompt_edit.toPlainText())

    def _remove_doc(self, index: int) -> None:
        value = self._doc_values[index]
        self.remove_doc.emit(None if self.tab == "project" else self.tab, value)

    def _save(self) -> None:
        self._flush_prompt()
        self.saved.emit()


class GlobalPage(Screen):
    saved = Signal()
    back = Signal()

    def __init__(self, store: ConfigStore) -> None:
        super().__init__()
        self.store = store
        self.add(label(text("settings.global"), "Title"))
        self.add(label(text("global.lead"), "Lead"))
        self.editor = QPlainTextEdit()
        self.editor.setMinimumHeight(220)
        self.add(self.editor)
        self.message = label("", "Hint")
        self.add(self.message)
        self.add_row(
            button(text("settings.save"), "Primary", self._save),
            button(text("global.reset"), "Secondary", self._reset),
            button(text("settings.back"), "Ghost", self.back.emit))

    def load(self) -> None:
        self.editor.setPlainText(self.store.read_global_prompt())
        self.message.setText("")

    def _save(self) -> None:
        self.store.write_global_prompt(self.editor.toPlainText())
        self.saved.emit()

    def _reset(self) -> None:
        from maintain.zip_package import GLOBAL_PROMPT_TEMPLATE
        self.editor.setPlainText(GLOBAL_PROMPT_TEMPLATE)
        self.message.setText(text("global.reset.done"))


class PackagePage(Screen):
    saved = Signal(str)
    back = Signal()

    def __init__(self) -> None:
        super().__init__()
        from PySide6.QtWidgets import QRadioButton
        self.add(label(text("settings.package"), "Title"))
        self.zip_radio = QRadioButton(f"{text('package.zip')} — {text('package.zip.sub')}")
        self.folder_radio = QRadioButton(
            f"{text('package.folder')} — {text('package.folder.sub')}")
        self.add(self.zip_radio)
        self.add(self.folder_radio)
        self.add_row(
            button(text("settings.save"), "Primary", self._save),
            button(text("settings.back"), "Ghost", self.back.emit))

    def load(self, style: str) -> None:
        self.zip_radio.setChecked(style == "zip")
        self.folder_radio.setChecked(style == "folder")

    def _save(self) -> None:
        self.saved.emit("folder" if self.folder_radio.isChecked() else "zip")


class ChecksPage(Screen):
    saved = Signal(list)   # list[(name, command)]
    back = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.add(label(text("settings.checks"), "Title"))
        self.add(label(text("checks.lead"), "Lead"))
        self._rows_layout = QVBoxLayout()
        holder = QWidget()
        holder.setLayout(self._rows_layout)
        self.add(holder)
        self.add_row(button(text("checks.add"), "Secondary", lambda: self._add_row()))
        self.message = label("", "Bad")
        self.add(self.message)
        self.add_row(
            button(text("settings.save"), "Primary", self._save),
            button(text("settings.back"), "Ghost", self.back.emit))
        self._editors: list[tuple[QLineEdit, QLineEdit, QWidget]] = []

    def load(self, rows: list[tuple[str, str]]) -> None:
        for _, _, holder in self._editors:
            holder.deleteLater()
        self._editors = []
        self.message.setText("")
        for name, command in rows:
            self._add_row(name, command)

    def _add_row(self, name: str = "", command: str = "") -> None:
        row = QHBoxLayout()
        holder = QWidget()
        holder.setLayout(row)
        name_edit = QLineEdit(name)
        name_edit.setPlaceholderText(text("checks.name"))
        name_edit.setFixedWidth(140)
        command_edit = QLineEdit(command)
        command_edit.setPlaceholderText(text("checks.command"))
        remove = button("✕", "Danger", None)
        remove.setFixedWidth(30)
        row.addWidget(name_edit)
        row.addWidget(command_edit)
        row.addWidget(remove)
        entry = (name_edit, command_edit, holder)
        remove.clicked.connect(lambda: self._remove_row(entry))
        self._rows_layout.addWidget(holder)
        self._editors.append(entry)

    def _remove_row(self, entry: tuple) -> None:
        if entry in self._editors:
            self._editors.remove(entry)
            entry[2].deleteLater()

    def _save(self) -> None:
        rows = [(name.text(), command.text())
                for name, command, _ in self._editors
                if name.text().strip() or command.text().strip()]
        self.saved.emit(rows)


def documents_count(store: ConfigStore, task_key: str) -> int:
    package = store.config.package
    return len(package.documents) + len(package.task(task_key).documents)


def read_global(store: ConfigStore) -> str:
    return global_prompt_text(store.config.package, store.path.parent)
