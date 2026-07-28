"""All screens. One screen, one decision. Text comes from the STE catalog."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton,
                               QRadioButton, QScrollArea, QSpinBox,
                               QVBoxLayout, QWidget)

from maintain.history import IterationEvent, RunSummary
from maintain.models import RunRecord
from maintain.onedrive import (PENDING, SYNCED, OneDriveSettings,
                               onedrive_settings, publish_packet,
                               save_onedrive_settings)
from maintain.providers.manual_ui import PacketHandoff
from maintain.zip_package import global_prompt_text

from .config_store import BUILTIN_PROMPTS, ConfigStore
from .strings import text
from .widgets import (ChoiceButton, DropZone, FileChips, NumberBadge,
                      PacketCard, Spinner, StateChip, StatusLine, TimelineDot,
                      button, label, run_state_chip)

TASK_TITLES = {"plan": "send.plan.title", "build": "send.build.title",
               "repair": "send.repair.title", "review": "send.review.title"}
TASK_STEPS = {"plan": "STEP 1 OF 5 — PLAN", "build": "STEP 2 OF 5 — BUILD",
              "repair": "STEP 2 OF 5 — BUILD", "review": "STEP 3 OF 5 — REVIEW"}


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
        self.column.setContentsMargins(26, 20, 26, 24)
        self.column.setSpacing(11)
        self.column.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll)

    def add(self, widget: QWidget) -> QWidget:
        self.column.insertWidget(self.column.count() - 1, widget)
        return widget

    def add_gap(self, height: int = 6) -> None:
        spacer = QWidget()
        spacer.setFixedHeight(height)
        self.add(spacer)

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

    @staticmethod
    def clear_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()


class HomeScreen(Screen):
    new_change = Signal(str)     # mode
    open_history = Signal()
    open_settings = Signal()
    continue_run = Signal(str)   # run_id

    def __init__(self, project_name: str, project_path: str) -> None:
        super().__init__()
        self.add(label(project_name, "Title"))
        self.add(label(project_path, "MonoHint"))
        self.add_gap(4)
        self._continue = ChoiceButton("↻", "", "", accent_kind="warn")
        self._continue.setVisible(False)
        self._continue.clicked.connect(self._emit_continue)
        self._continue_run_id = ""
        self.add(self._continue)
        for glyph, title_key, sub_key, slot in (
                ("＋", "home.change", "home.change.sub",
                 lambda: self.new_change.emit("feature")),
                ("!", "home.fault", "home.fault.sub",
                 lambda: self.new_change.emit("issue")),
                ("↻", "home.history", "home.history.sub", self.open_history.emit),
                ("⚙", "home.settings", "home.settings.sub",
                 self.open_settings.emit)):
            card = ChoiceButton(glyph, text(title_key), text(sub_key))
            card.clicked.connect(slot)
            self.add(card)

    def set_resumable(self, summary: RunSummary | None, stage_name: str = "") -> None:
        if summary is None:
            self._continue.setVisible(False)
            return
        self._continue_run_id = summary.run_id
        self._continue.set_texts(
            text("home.continue", run=summary.run_id),
            text("home.continue.sub", stage=stage_name or summary.display_state))
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
        self.message = StatusLine()
        self.add(self.message)
        self.add_gap()
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
        self.message.set_state("plain", "")

    def add_files(self, paths: list[Path]) -> None:
        self.attachments.extend(Path(item) for item in paths)
        self.chips.set_files([item.name for item in self.attachments])

    def _remove(self, index: int) -> None:
        del self.attachments[index]
        self.chips.set_files([item.name for item in self.attachments])

    def _start(self) -> None:
        request = self.request_edit.toPlainText().strip()
        if not request:
            self.message.set_state("bad", text("describe.empty"))
            return
        self.message.set_state("plain", "")
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
        self.show_global_button = button("GLOBAL.md", "Ghost", None)
        self.show_prompt_button = button("TASK.md", "Ghost", None)
        for control in (self.show_global_button, self.show_prompt_button):
            control.setStyleSheet("padding: 2px 6px; font-size: 11px;")
        contents_label = label(text("send.contents"), "Hint")
        contents_label.setWordWrap(False)
        row = self.add_row(contents_label, self.show_global_button,
                           self.show_prompt_button)
        row.setSpacing(4)
        self.add_gap(2)
        self.add(label(text("send.attachments").upper(), "Eyebrow"))
        self.chips = FileChips()
        self.chips.removed.connect(self.remove_attachment.emit)
        self.add(self.chips)
        zone = DropZone(text("send.attach.drop"), slim=True)
        zone.files_dropped.connect(self.add_attachments.emit)
        self.add(zone)
        self.add_row(button(text("send.attach.add"), "Secondary",
                            self.import_attachments.emit))
        self.add_gap(2)
        self.link_button = button(text("send.copy_link"), "Primary", self._copy_link)
        self.add(self.link_button)
        self.add(label(text("send.copy_link.sub"), "Hint"))
        self.add_row(
            button(text("send.copy_file"), "Secondary", self._copy_file),
            button(text("send.export"), "Secondary", self.export_requested.emit))
        self.status = StatusLine()
        self.add(self.status)
        self.add_gap(2)
        self.continue_button = button(text("send.continue"), "Primary",
                                      self.continue_clicked.emit)
        self.continue_button.setEnabled(False)
        self.caption = label(text("send.continue.before"), "Hint")
        self.caption.setWordWrap(False)
        self.add_row(self.continue_button, self.caption)
        self.link_state.connect(self._on_link_state)

    def show_handoff(self, handoff: PacketHandoff, attachment_names: list[str],
                     document_count: int) -> None:
        self.handoff = handoff
        self._out_action = False
        again = ""
        request = handoff.request
        if handoff.task_key == "plan" and "round-" in request.task_id:
            again = " · AGAIN"
        self.eyebrow.setText(TASK_STEPS[handoff.task_key] + again)
        self.title.setText(text(TASK_TITLES[handoff.task_key]))
        self.update_packet(handoff.zip_path, attachment_names, document_count)
        self.status.set_state("plain", "")
        self.continue_button.setEnabled(False)
        self.caption.setText(text("send.continue.before"))

    def update_packet(self, zip_path: Path, attachment_names: list[str],
                      document_count: int) -> None:
        size = zip_path.stat().st_size if zip_path.is_file() else 0
        self.card.set_packet(zip_path, size)
        documents = (f"documents/ — {document_count} · " if document_count else "")
        self.contents.setText(
            "TASK.md · GLOBAL.md · CODEBASE.md · MANIFEST.json · "
            f"{documents}attachments/ — {len(attachment_names)}")
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
        self.status.set_state("ok", text("send.file.copied"))
        self._mark_out()

    def _copy_link(self) -> None:
        if self.handoff is None:
            return
        settings = onedrive_settings()
        packet = self.card.packet_path
        self.link_button.setEnabled(False)
        self.status.set_state("busy", text("send.link.copying"))
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
            self.status.set_state("bad", value)
            return
        if value:
            QGuiApplication.clipboard().setText(value)
        if state == SYNCED:
            self.status.set_state(
                "ok", f"{text('send.link.done')} {text('send.link.paste')}")
        elif state == PENDING:
            self.status.set_state("warn", text("send.link.manual"))
        else:
            self.status.set_state(
                "plain", f"{text('send.link.paste')} {text('send.link.manual')}")
        self._mark_out()

    def mark_exported(self, name: str) -> None:
        self.status.set_state("ok", text("send.exported", name=name))
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
        zone.setMinimumHeight(110)
        zone.files_dropped.connect(self._dropped)
        self.add(zone)
        self.paste_button = button(text("receive.paste"), "Primary", self._paste)
        self.import_button = button(text("receive.import"), "Secondary",
                                    self.import_requested.emit)
        self.add_row(self.paste_button, self.import_button)
        self.status = StatusLine()
        self.add(self.status)
        self.add_gap()
        self.add_row(button(text("receive.back"), "Ghost", self.back.emit))

    def show_handoff(self, handoff: PacketHandoff) -> None:
        self.handoff = handoff
        self.eyebrow.setText(TASK_STEPS[handoff.task_key])
        zip_reply = handoff.reply_kind == "zip"
        self.lead.setText(text("receive.lead.zip" if zip_reply else "receive.lead.json"))
        self.paste_button.setVisible(not zip_reply)
        self.import_button.setVisible(True)
        self.status.set_state("plain", "")

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
            self.status.set_state("busy", text("receive.checking"))
            self.reply_submitted.emit(result.reply)
            return
        if result.keep_as_attachment and path is not None:
            self.status.set_state("warn", text("receive.kept"))
            self.kept_attachment.emit([path])
        else:
            self.status.set_state(
                "bad", result.message or text("receive.clipboard.empty"))


class PlanCheckScreen(Screen):
    accept = Signal()
    rescope = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.add(label("STEP 1 OF 5 — PLAN", "Eyebrow"))
        self.title = label("", "Title")
        self.add(self.title)
        self._cards = QVBoxLayout()
        self._cards.setSpacing(9)
        holder = QWidget()
        holder.setLayout(self._cards)
        self.add(holder)
        self.add_gap()
        self.add_row(
            button(text("plan.accept"), "Primary", self.accept.emit),
            button(text("plan.rescope"), "Secondary", self.rescope.emit))

    def show_tasks(self, tasks: list[dict]) -> None:
        self.title.setText(text("plan.title.one") if len(tasks) == 1
                           else text("plan.title.many", count=len(tasks)))
        self.clear_layout(self._cards)
        for index, task in enumerate(tasks, start=1):
            card = QFrame()
            card.setObjectName("Card")
            row = QHBoxLayout(card)
            row.setContentsMargins(14, 12, 14, 12)
            row.setSpacing(12)
            row.addWidget(NumberBadge(index), 0, Qt.AlignmentFlag.AlignTop)
            column = QVBoxLayout()
            column.setSpacing(3)
            objective = label(str(task.get("objective", "")))
            font = objective.font()
            font.setBold(True)
            objective.setFont(font)
            column.addWidget(objective)
            files = ", ".join(map(str, task.get("allowed_files", [])))
            column.addWidget(label(text("plan.files", files=files), "MonoHint"))
            done = "; ".join(map(str, task.get("done_when", [])))
            column.addWidget(label(text("plan.done_when", text=done), "Hint"))
            row.addLayout(column, 1)
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
        self._cards.setSpacing(9)
        holder = QWidget()
        holder.setLayout(self._cards)
        self.add(holder)
        self.add_gap()
        self.add_row(button(text("findings.repair.button"), "Primary",
                            self.repair.emit))
        self.add(label(text("findings.repair.sub"), "Hint"))
        self.add_row(button(text("findings.rescope"), "Ghost", self.rescope.emit))

    def show_findings(self, findings: list[dict]) -> None:
        self.title.setText(text("findings.title.one") if len(findings) == 1
                           else text("findings.title.many", count=len(findings)))
        self.clear_layout(self._cards)
        for finding in findings:
            card = QFrame()
            card.setObjectName("Finding")
            box = QVBoxLayout(card)
            box.setContentsMargins(14, 12, 14, 12)
            box.setSpacing(5)
            head = QHBoxLayout()
            head.setSpacing(9)
            severity = str(finding.get("severity", "low"))
            pill = QLabel(severity.upper())
            pill.setObjectName("SevMinor" if severity == "low" else "SevMajor")
            head.addWidget(pill)
            location = label(f"{finding.get('file', '')}:{finding.get('line', '')}",
                             "MonoHint")
            head.addWidget(location)
            head.addStretch(1)
            box.addLayout(head)
            box.addWidget(label(f"{text('findings.evidence')} "
                                f"{finding.get('evidence', '')}", "Dim"))
            box.addWidget(label(f"{text('findings.repair')} "
                                f"{finding.get('remediation', '')}", "Dim"))
            self._cards.addWidget(card)


class TestScreen(Screen):
    repair = Signal()
    rescope = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.add(label("STEP 4 OF 5 — TEST", "Eyebrow"))
        self.add(label(text("test.title"), "Title"))
        self._rows = QVBoxLayout()
        self._rows.setSpacing(8)
        holder = QWidget()
        holder.setLayout(self._rows)
        self.add(holder)
        self.outcome = StatusLine()
        self.add(self.outcome)
        self.add_gap()
        self.repair_button = button(text("test.repair"), "Primary", self.repair.emit)
        self.rescope_button = button(text("test.rescope"), "Ghost", self.rescope.emit)
        self.add_row(self.repair_button, self.rescope_button)
        self._set_failed_controls(False)
        self._checks: dict[str, StateChip] = {}
        self._row_frames: dict[str, QVBoxLayout] = {}

    def reset(self, specs: list[tuple[str, str]] = ()) -> None:
        self.clear_layout(self._rows)
        self._checks = {}
        self._row_frames = {}
        self.outcome.set_state("plain", "")
        self._set_failed_controls(False)
        for name, command in specs:
            self._make_row(name, command, "WAIT", "wait")

    def _set_failed_controls(self, visible: bool) -> None:
        self.repair_button.setVisible(visible)
        self.rescope_button.setVisible(visible)

    def on_progress(self, phase: str, label_key: str, message: str) -> None:
        if label_key != "CHECK":
            return
        if phase == "start":
            name = message.split(" check", 1)[0].removeprefix("Run the ").strip()
            self._set_check(name, "RUN", "accent")
        elif phase == "complete":
            name = message.removeprefix("The ").removesuffix(" check passed").strip()
            self._set_check(name, "PASS", "pass")
        elif phase == "failed":
            name = message.removeprefix("The ").removesuffix(" check failed").strip()
            self._set_check(name, "FAIL", "fail")

    def _make_row(self, name: str, command: str, state: str, kind: str) -> None:
        card = QFrame()
        card.setObjectName("Card")
        outer = QVBoxLayout(card)
        outer.setContentsMargins(13, 10, 13, 10)
        outer.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(10)
        column = QVBoxLayout()
        column.setSpacing(1)
        title = label(name)
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        column.addWidget(title)
        if command:
            column.addWidget(label(command, "MonoHint"))
        head.addLayout(column, 1)
        badge = StateChip(state, kind)
        head.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(head)
        self._rows.addWidget(card)
        self._checks[name] = badge
        self._row_frames[name] = outer

    def _set_check(self, name: str, state: str, kind: str) -> None:
        if name not in self._checks:
            self._make_row(name, "", state, kind)
            return
        self._checks[name].set_state(state, kind)

    def show_failed(self, results: list[dict]) -> None:
        for result in results:
            name = str(result.get("name", "check"))
            passed = int(result.get("exit_code", 1) or 0) == 0
            self._set_check(name, "PASS" if passed else "FAIL",
                            "pass" if passed else "fail")
            if not passed:
                output = "\n".join(part for part in (
                    str(result.get("stdout", "")).strip(),
                    str(result.get("stderr", "")).strip()) if part)
                if output and name in self._row_frames:
                    view = QPlainTextEdit()
                    view.setObjectName("Code")
                    view.setReadOnly(True)
                    view.setPlainText(output[-4000:])
                    view.setFixedHeight(110)
                    self._row_frames[name].addWidget(view)
        self.outcome.set_state("bad", text("test.failed"))
        self._set_failed_controls(True)

    def mark_passed(self) -> None:
        self.outcome.set_state("ok", text("test.passed"))


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
        self.add_gap()
        self.add_row(button(text("save.accept"), "Primary", self.accept.emit))
        self.add(label(text("save.accept.sub"), "Hint"))
        self.add_gap(2)
        self.add_row(
            button(text("save.feedback"), "Secondary", self.feedback.emit),
            button(text("test.run_again"), "Ghost", self.rerun.emit),
            button(text("save.discard"), "Danger", self.discard.emit))

    def show_record(self, record: RunRecord, changed: list[str], diff: str) -> None:
        self.record = record
        self.title.setText(text("save.title.one") if len(changed) == 1
                           else text("save.title", count=len(changed)))
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
        self.add_gap(30)
        tick = QLabel("✓")
        tick.setObjectName("DoneTick")
        tick.setFixedSize(54, 54)
        tick.setAlignment(Qt.AlignmentFlag.AlignCenter)
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.addStretch(1)
        row.addWidget(tick)
        row.addStretch(1)
        self.add(holder)
        title = label(text("done.title"), "Title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.add(title)
        self.branch = label("", "Mono")
        self.branch.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.add(self.branch)
        audit = label(text("done.audit"), "Lead")
        audit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.add(audit)
        self.add_gap()
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(button(text("done.new"), "Primary", self.new_change.emit))
        buttons.addWidget(button(text("done.history"), "Ghost",
                                 self.open_history.emit))
        buttons.addStretch(1)
        holder2 = QWidget()
        holder2.setLayout(buttons)
        self.add(holder2)

    def show_record(self, record: RunRecord) -> None:
        self.branch.setText(text("done.branch", branch=record.branch))


class HistoryRow(QFrame):
    clicked = Signal()

    def __init__(self, summary: RunSummary) -> None:
        super().__init__()
        self.setObjectName("Choice")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        row = QHBoxLayout(self)
        row.setContentsMargins(15, 11, 15, 11)
        row.setSpacing(12)
        column = QVBoxLayout()
        column.setSpacing(1)
        request = " ".join(summary.request.split())[:64]
        title = QLabel(f"Run {summary.run_id}")
        title.setObjectName("ChoiceTitle")
        sub = QLabel(f"{request} · {summary.updated_at[:10]}")
        sub.setObjectName("ChoiceSub")
        column.addWidget(title)
        column.addWidget(sub)
        row.addLayout(column, 1)
        row.addWidget(run_state_chip(summary.display_state))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)


class HistoryScreen(Screen):
    open_run = Signal(str)
    back = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.add(label(text("history.title"), "Title"))
        self._rows = QVBoxLayout()
        self._rows.setSpacing(9)
        holder = QWidget()
        holder.setLayout(self._rows)
        self.add(holder)
        self.empty = label(text("history.empty"), "Lead")
        self.add(self.empty)
        self.add_gap()
        self.add_row(button(text("history.back"), "Ghost", self.back.emit))

    def show_runs(self, runs: list[RunSummary]) -> None:
        self.clear_layout(self._rows)
        self.empty.setVisible(not runs)
        for summary in runs:
            row = HistoryRow(summary)
            row.clicked.connect(
                lambda run_id=summary.run_id: self.open_run.emit(run_id))
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
        self._rows.setSpacing(0)
        holder = QWidget()
        holder.setLayout(self._rows)
        self.add(holder)
        self.add_gap()
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
        self.clear_layout(self._rows)
        last_index = len(timeline) - 1
        for index, event in enumerate(timeline):
            kind = ("revert" if event.kind == "revert"
                    else "current" if index == last_index
                    else "super" if event.superseded else "normal")
            row = QFrame()
            grid = QGridLayout(row)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(1)
            dot = TimelineDot(index + 1, kind, first=index == 0,
                              last=index == last_index)
            grid.addWidget(dot, 0, 0, 3, 1)
            head = QHBoxLayout()
            head.setSpacing(8)
            title = QLabel(event.label)
            font = title.font()
            font.setBold(not event.superseded)
            title.setFont(font)
            if event.superseded:
                title.setStyleSheet("color: palette(mid);")
            head.addWidget(title)
            if event.superseded:
                tag = QLabel(text("run.superseded").upper())
                tag.setObjectName("TagSuper")
                head.addWidget(tag)
            head.addStretch(1)
            if live and event.can_go_back and index < last_index:
                go = button(text("run.goback"), "Ghost", None)
                go.setStyleSheet("padding: 2px 6px; font-size: 11px;")
                go.clicked.connect(
                    lambda _=False, seq=event.sequence: self.go_back_to.emit(seq))
                head.addWidget(go)
            time_label = QLabel(event.time[11:16] or event.time[:10])
            time_label.setObjectName("Hint")
            head.addWidget(time_label)
            holder = QWidget()
            holder.setLayout(head)
            grid.addWidget(holder, 0, 1)
            if event.sub:
                sub = label(event.sub, "Hint")
                grid.addWidget(sub, 1, 1)
            pad = QWidget()
            pad.setFixedHeight(8)
            grid.addWidget(pad, 2, 1)
            grid.setColumnStretch(1, 1)
            self._rows.addWidget(row)


class BusyScreen(Screen):
    def __init__(self) -> None:
        super().__init__()
        self.add_gap(40)
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.addStretch(1)
        self.spinner = Spinner(30)
        self.spinner.start()
        row.addWidget(self.spinner)
        row.addStretch(1)
        self.add(holder)
        self.title = label(text("working.busy"), "Title")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.add(self.title)
        self.status = label("", "Lead")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.add(self.status)

    def show_message(self, message: str) -> None:
        self.spinner.start()
        self.title.setText(message or text("working.busy"))
        self.status.setText("")

    def on_progress(self, phase: str, label_key: str, message: str) -> None:
        if phase in {"start", "complete"}:
            self.status.setText(message)


class SettingsScreen(Screen):
    open_page = Signal(str)
    back = Signal()

    GLYPHS = {"onedrive": "⇅", "tasks": "≡", "global": "¶",
              "package": "▤", "checks": "✓"}

    def __init__(self) -> None:
        super().__init__()
        self.add(label(text("settings.title"), "Title"))
        for key in ("onedrive", "tasks", "global", "package", "checks"):
            card = ChoiceButton(self.GLYPHS[key], text("settings." + key),
                                text("settings." + key + ".sub"))
            card.clicked.connect(lambda page=key: self.open_page.emit(page))
            self.add(card)
        self.add_gap()
        self.add_row(button(text("settings.back"), "Ghost", self.back.emit))


class OneDrivePage(Screen):
    saved = Signal()
    back = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.add(label(text("settings.onedrive"), "Title"))
        self.add(label(text("onedrive.folder").upper(), "Eyebrow"))
        self.folder_edit = QLineEdit()
        self.add(self.folder_edit)
        self.add(label(text("onedrive.folder.hint"), "Hint"))
        self.add_row(button(text("onedrive.browse"), "Secondary", None))
        self.add_gap(2)
        self.add(label(text("onedrive.link").upper(), "Eyebrow"))
        self.link_edit = QLineEdit()
        self.link_edit.textChanged.connect(self._preview)
        self.add(self.link_edit)
        self.add(label(text("onedrive.link.hint"), "Hint"))
        self.example = label("", "MonoHint")
        self.add(self.example)
        self.add_gap(2)
        self.add(label(text("onedrive.timeout").upper(), "Eyebrow"))
        self.timeout_edit = QSpinBox()
        self.timeout_edit.setRange(10, 900)
        self.timeout_edit.setFixedWidth(120)
        self.add_row(self.timeout_edit)
        self.add(label(text("onedrive.timeout.hint"), "Hint"))
        self.add_gap()
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
        tabs.setSpacing(6)
        holder = QWidget()
        holder.setLayout(tabs)
        self._tab_buttons: dict[str, QPushButton] = {}
        for key in ("project", "plan", "build", "repair", "review"):
            name = text("tasks.project") if key == "project" else key.capitalize()
            control = button(name, "Secondary", lambda k=key: self.set_tab(k))
            control.setStyleSheet("padding: 5px 13px; font-size: 12px;")
            tabs.addWidget(control)
            self._tab_buttons[key] = control
        tabs.addStretch(1)
        self.add(holder)
        self.add_gap(2)
        self.section_title = label("", "Eyebrow")
        self.add(self.section_title)
        self.prompt_state = label("", "Lead")
        self.add(self.prompt_state)
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setFixedHeight(130)
        self.add(self.prompt_edit)
        self.prompt_toggle = button("", "Secondary", self._toggle_prompt)
        self.add_row(self.prompt_toggle)
        self.add_gap(2)
        self.docs_title = label("", "Eyebrow")
        self.add(self.docs_title)
        self.docs_hint = label("", "Hint")
        self.add(self.docs_hint)
        self.docs = FileChips()
        self.docs.removed.connect(self._remove_doc)
        self.add(self.docs)
        self.add_row(button(text("tasks.docs.add"), "Secondary",
                            lambda: self.add_doc.emit(
                                None if self.tab == "project" else self.tab)))
        self.add_gap()
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
        for key, control in self._tab_buttons.items():
            control.setObjectName("Primary" if key == self.tab else "Secondary")
            control.style().unpolish(control)
            control.style().polish(control)
        self.prompt_state.setVisible(not project)
        self.prompt_edit.setVisible(not project)
        self.prompt_toggle.setVisible(not project)
        if project:
            self.section_title.setText(text("tasks.docs.project").upper())
            self.docs_title.setText("")
            self.docs_hint.setText(text("tasks.docs.project.hint"))
            self._doc_values = list(package.documents)
        else:
            self._override, prompt = self.store.task_prompt(self.tab)
            self.section_title.setText(text("tasks.prompt", task=self.tab).upper())
            self.prompt_state.setText(text(
                "tasks.prompt.own" if self._override else "tasks.prompt.builtin"))
            self.prompt_edit.setPlainText(prompt)
            self.prompt_edit.setReadOnly(not self._override)
            self.prompt_toggle.setText(text(
                "tasks.prompt.reset" if self._override else "tasks.prompt.change"))
            self.docs_title.setText(text("tasks.docs.task", task=self.tab).upper())
            self.docs_hint.setText("")
            self._doc_values = list(package.task(self.tab).documents)
        self.docs_hint.setVisible(bool(self.docs_hint.text()))
        self.docs.set_files(self._doc_values)
        if not self._doc_values:
            self.docs_hint.setText(text("tasks.docs.none"))
            self.docs_hint.setVisible(True)

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
        self.add(label(text("settings.package"), "Title"))
        self.zip_radio, zip_card = self._option(
            text("package.zip"), text("package.zip.sub"))
        self.folder_radio, folder_card = self._option(
            text("package.folder"), text("package.folder.sub"))
        self.add(zip_card)
        self.add(folder_card)
        self.add_gap()
        self.add_row(
            button(text("settings.save"), "Primary", self._save),
            button(text("settings.back"), "Ghost", self.back.emit))

    @staticmethod
    def _option(title: str, sub: str) -> tuple[QRadioButton, QFrame]:
        card = QFrame()
        card.setObjectName("Card")
        column = QVBoxLayout(card)
        column.setContentsMargins(13, 11, 13, 11)
        column.setSpacing(2)
        radio = QRadioButton(title)
        column.addWidget(radio)
        sub_label = QLabel(sub)
        sub_label.setObjectName("ChoiceSub")
        sub_label.setWordWrap(True)
        sub_label.setContentsMargins(24, 0, 0, 0)
        column.addWidget(sub_label)
        return radio, card

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
        self._rows_layout.setSpacing(8)
        holder = QWidget()
        holder.setLayout(self._rows_layout)
        self.add(holder)
        self.add_row(button(text("checks.add"), "Secondary", lambda: self._add_row()))
        self.message = label("", "Bad")
        self.add(self.message)
        self.add_gap()
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
        row.setSpacing(8)
        holder = QWidget()
        holder.setLayout(row)
        name_edit = QLineEdit(name)
        name_edit.setPlaceholderText(text("checks.name"))
        name_edit.setFixedWidth(140)
        command_edit = QLineEdit(command)
        command_edit.setPlaceholderText(text("checks.command"))
        remove = button("×", "Danger", None)
        remove.setFixedSize(30, 32)
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
