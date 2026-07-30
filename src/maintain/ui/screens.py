"""All screens. One screen, one decision. Text comes from the STE catalog."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QRadioButton, QScrollArea,
                               QSpinBox, QVBoxLayout, QWidget)

from maintain.downloads import default_downloads
from maintain.issues import REASONS
from maintain.repository_memory import load_ui_settings, save_ui_settings

from maintain.history import IterationEvent, RunSummary
from maintain.models import RunRecord
from maintain.onedrive import (PENDING, SYNCED, OneDriveSettings,
                               onedrive_settings, publish_packet,
                               save_onedrive_settings)
from maintain.providers.manual_ui import PacketHandoff
from maintain.zip_package import global_prompt_text

from .config_store import BUILTIN_PROMPTS, ConfigStore
from .strings import text
from .widgets import (ChoiceButton, DiffHighlighter, DropZone, ElidedLabel,
                      FileChips, IconSquare, NumberBadge, PacketCard, Spinner,
                      StateChip, StatusLine, TimelineDot, button, label,
                      palette, run_state_chip)

TASK_TITLES = {"plan": "send.plan.title", "build": "send.build.title",
               "repair": "send.repair.title", "review": "send.review.title",
               "scan": "send.scan.title", "discuss": "send.discuss.title",
               "explain": "send.explain.title"}
TASK_STEPS = {"plan": "STEP 1 OF 5 — PLAN", "build": "STEP 2 OF 5 — BUILD",
              "repair": "STEP 2 OF 5 — BUILD", "review": "STEP 3 OF 5 — REVIEW",
              "scan": "ISSUE SCAN", "discuss": "ISSUE DISCUSSION",
              "explain": "CODE EXPLANATION"}

SEVERITY_CHIPS = {"high": ("issues.severity.high", "fail"),
                  "medium": ("issues.severity.medium", "warn"),
                  "low": ("issues.severity.low", "wait")}
SEVERITY_ICON_KINDS = {"high": "bad", "medium": "warn", "low": "neutral"}
ISSUE_STATUS_CHIPS = {"open": ("issues.status.open", "accent"),
                      "in_work": ("issues.status.in_work", "warn"),
                      "closed": ("issues.status.closed", "wait")}


class Screen(QWidget):
    """A scrollable page with a single column of content."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Screen")
        self._primary_action = None
        self._escape_action = None
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

    def set_keys(self, primary=None, escape=None) -> None:
        """Enter fires the primary action; Esc goes back. FR-P9."""
        self._primary_action = primary
        self._escape_action = escape

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if (key in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and self._primary_action is not None
                and not isinstance(QApplication.focusWidget(), QPlainTextEdit)):
            self._primary_action()
            return
        if key == Qt.Key.Key_Escape and self._escape_action is not None:
            self._escape_action()
            return
        super().keyPressEvent(event)

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
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()


class NotePanel(QFrame):
    """An inline note editor: the content stays visible while you write.

    The text survives a cancel; it clears only after a send. FR-E2."""

    send = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Card")
        column = QVBoxLayout(self)
        column.setContentsMargins(14, 12, 14, 12)
        column.setSpacing(8)
        self.question = label("", "Lead")
        column.addWidget(self.question)
        self.edit = QPlainTextEdit()
        self.edit.setFixedHeight(76)
        column.addWidget(self.edit)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(button(text("note.send"), "Primary", self._send))
        row.addWidget(button(text("note.cancel"), "Ghost", self.hide))
        row.addStretch(1)
        column.addLayout(row)
        self.hide()

    def open(self, question: str) -> None:
        self.question.setText(question)
        self.setVisible(True)
        self.edit.setFocus()

    def _send(self) -> None:
        value = self.edit.toPlainText().strip()
        if not value:
            return
        self.hide()
        self.edit.setPlainText("")
        self.send.emit(value)


class HomeScreen(Screen):
    new_change = Signal(str)     # mode
    open_history = Signal()
    open_settings = Signal()
    open_projects = Signal()
    open_issues = Signal()
    open_explain = Signal()
    continue_run = Signal(str)   # run_id

    def __init__(self, project_name: str, project_path: str) -> None:
        super().__init__()
        self.add(label(project_name, "Title"))
        self.add(label(project_path, "MonoHint"))
        self.momentum = label("", "Hint")
        self.momentum.setVisible(False)
        self.add(self.momentum)
        self.add_gap(4)
        self._continue = ChoiceButton("play", "", "", accent_kind="warn")
        self._continue.setVisible(False)
        self._continue.clicked.connect(self._emit_continue)
        self._continue_run_id = ""
        self.add(self._continue)
        self._issues_card: ChoiceButton | None = None
        for index, (icon, title_key, sub_key, slot) in enumerate((
                ("plus", "home.change", "home.change.sub",
                 lambda: self.new_change.emit("feature")),
                ("wrench", "home.fault", "home.fault.sub",
                 lambda: self.new_change.emit("issue")),
                ("film", "home.explain", "home.explain.sub",
                 self.open_explain.emit),
                ("bug", "home.issues", "home.issues.sub.none",
                 self.open_issues.emit),
                ("history", "home.history", "home.history.sub",
                 self.open_history.emit),
                ("folder", "home.projects", "home.projects.sub",
                 self.open_projects.emit),
                ("sliders", "home.settings", "home.settings.sub",
                 self.open_settings.emit))):
            if index == 3:
                self.add_gap(4)
            card = ChoiceButton(icon, text(title_key), text(sub_key))
            card.clicked.connect(slot)
            self.add(card)
            if title_key == "home.issues":
                self._issues_card = card

    def set_momentum(self, value: str) -> None:
        self.momentum.setText(value)
        self.momentum.setVisible(bool(value))

    def set_issue_count(self, count: int) -> None:
        if self._issues_card is None:
            return
        sub = (text("home.issues.sub.count", count=count) if count
               else text("home.issues.sub.none"))
        self._issues_card.set_texts(text("home.issues"), sub)

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
    open_checks = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.mode = "feature"
        self._title = label(text("describe.title"), "Title")
        self.add(self._title)
        self.request_edit = QPlainTextEdit()
        self.request_edit.setPlaceholderText(text("describe.placeholder"))
        self.request_edit.setFixedHeight(96)
        self.add(self.request_edit)
        self._recent_holder = QWidget()
        self._recent_row = QHBoxLayout(self._recent_holder)
        self._recent_row.setContentsMargins(0, 0, 0, 0)
        self._recent_row.setSpacing(6)
        self._recent_holder.setVisible(False)
        self.add(self._recent_holder)
        zone = DropZone(text("describe.drop.main"), text("describe.drop.sub"))
        zone.files_dropped.connect(self.add_files)
        zone.clicked.connect(self.import_requested.emit)
        self.add(zone)
        self.chips = FileChips()
        self.chips.removed.connect(self._remove)
        self.add(self.chips)
        self.message = StatusLine()
        self.add(self.message)
        self.checks_hint = button(text("describe.checks.hint"), "Ghost",
                                  self.open_checks.emit)
        self.checks_hint.setStyleSheet("padding: 2px 6px; font-size: 12px;")
        self.checks_hint.setVisible(False)
        self.add_row(self.checks_hint)
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

    def set_checks_hint(self, visible: bool) -> None:
        self.checks_hint.setVisible(visible)

    def set_recent(self, requests: list[str]) -> None:
        """FR-D8: the last requests, one click to reuse."""
        self.clear_layout(self._recent_row)
        for full in requests[:5]:
            short = " ".join(full.split())
            shown = short if len(short) <= 42 else short[:39] + "…"
            chip = button(shown, "Ghost",
                          lambda value=full: self.request_edit.setPlainText(
                              value))
            chip.setStyleSheet("padding: 3px 9px; font-size: 12px;")
            chip.setToolTip(full)
            self._recent_row.addWidget(chip)
        self._recent_row.addStretch(1)
        self._recent_holder.setVisible(bool(requests))

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


class ExchangeScreen(Screen):
    """One screen for the whole exchange: the packet out, the reply in."""

    reply_submitted = Signal(object)   # ManualReply
    kept_attachment = Signal(list)     # list[Path]
    import_reply = Signal()
    newest_download = Signal()
    add_attachments = Signal(list)       # list[Path] added to this packet
    remove_attachment = Signal(int)
    import_attachments = Signal()
    export_requested = Signal()
    scan_focus = Signal(str)
    link_state = Signal(str, str)        # state, message (internal, thread-safe)

    def __init__(self) -> None:
        super().__init__()
        self.handoff: PacketHandoff | None = None
        self.package_style = "zip"
        self.eyebrow = label("", "Eyebrow")
        self.add(self.eyebrow)
        self.title = label("", "Title")
        self.add(self.title)

        # ---- the send region ----
        send_frame = QFrame()
        send_frame.setObjectName("SendRegion")
        send_column = QVBoxLayout(send_frame)
        send_column.setContentsMargins(14, 12, 14, 12)
        send_column.setSpacing(9)
        send_head = QLabel(text("exchange.send.head").upper())
        send_head.setObjectName("SendHead")
        send_column.addWidget(send_head)
        self.send_lead = label(text("send.lead"), "Lead")
        send_column.addWidget(self.send_lead)
        focus_row = QHBoxLayout()
        focus_row.setSpacing(8)
        self.focus_edit = QLineEdit()
        self.focus_edit.setPlaceholderText(text("scan.ask.body"))
        self.focus_edit.returnPressed.connect(self._emit_focus)
        focus_row.addWidget(self.focus_edit, 1)
        focus_row.addWidget(button(text("scan.update"), "Secondary",
                                   self._emit_focus))
        self._focus_holder = QWidget()
        self._focus_holder.setLayout(focus_row)
        send_column.addWidget(self._focus_holder)
        self.card = PacketCard()
        send_column.addWidget(self.card)
        self.contents = label("", "Hint")
        send_column.addWidget(self.contents)
        self.show_global_button = button("GLOBAL.md", "Ghost", None)
        self.show_prompt_button = button("TASK.md", "Ghost", None)
        for control in (self.show_global_button, self.show_prompt_button):
            control.setStyleSheet("padding: 2px 6px; font-size: 11px;")
        contents_label = label(text("send.contents"), "Hint")
        contents_label.setWordWrap(False)
        contents_row = QHBoxLayout()
        contents_row.setSpacing(4)
        for widget in (contents_label, self.show_global_button,
                       self.show_prompt_button):
            contents_row.addWidget(widget)
        contents_row.addStretch(1)
        contents_holder = QWidget()
        contents_holder.setLayout(contents_row)
        send_column.addWidget(contents_holder)
        self.chips = FileChips()
        self.chips.removed.connect(self.remove_attachment.emit)
        send_column.addWidget(self.chips)
        attach_zone = DropZone(text("send.attach.drop"), slim=True)
        attach_zone.files_dropped.connect(self.add_attachments.emit)
        attach_zone.clicked.connect(self.import_attachments.emit)
        send_column.addWidget(attach_zone)
        self.link_button = button(text("send.copy_link"), "Secondary",
                                  self._copy_link)
        buttons_row = QHBoxLayout()
        buttons_row.setSpacing(10)
        for widget in (self.link_button,
                       button(text("send.copy_file"), "Secondary",
                              self._copy_file),
                       button(text("send.export"), "Secondary",
                              self.export_requested.emit)):
            buttons_row.addWidget(widget)
        buttons_row.addStretch(1)
        buttons_holder = QWidget()
        buttons_holder.setLayout(buttons_row)
        send_column.addWidget(buttons_holder)
        self.send_status = StatusLine()
        send_column.addWidget(self.send_status)
        self.add(send_frame)
        self.add_gap(2)

        # ---- the receive region ----
        receive_frame = QFrame()
        receive_frame.setObjectName("ReceiveRegion")
        receive_column = QVBoxLayout(receive_frame)
        receive_column.setContentsMargins(14, 12, 14, 12)
        receive_column.setSpacing(9)
        receive_head = QLabel(text("exchange.receive.head").upper())
        receive_head.setObjectName("ReceiveHead")
        receive_column.addWidget(receive_head)
        self.waiting_label = label("", "Hint")
        receive_column.addWidget(self.waiting_label)
        self.lead = label("", "Lead")
        receive_column.addWidget(self.lead)
        self.newest_button = button(text("exchange.newest"), "Primary",
                                    self.newest_download.emit)
        newest_row = QHBoxLayout()
        newest_row.addWidget(self.newest_button)
        newest_row.addStretch(1)
        newest_holder = QWidget()
        newest_holder.setLayout(newest_row)
        receive_column.addWidget(newest_holder)
        reply_zone = DropZone(text("receive.drop"), text("exchange.drop.sub"))
        reply_zone.setMinimumHeight(84)
        reply_zone.files_dropped.connect(self._dropped)
        reply_zone.clicked.connect(self.import_reply.emit)
        receive_column.addWidget(reply_zone)
        self.paste_button = button(text("receive.paste"), "Secondary",
                                   self._paste)
        paste_row = QHBoxLayout()
        paste_row.addWidget(self.paste_button)
        paste_row.addStretch(1)
        paste_holder = QWidget()
        paste_holder.setLayout(paste_row)
        receive_column.addWidget(paste_holder)
        self.status = StatusLine()
        receive_column.addWidget(self.status)
        self.add(receive_frame)
        self.add(label(text("exchange.copy.key"), "Hint"))
        self.link_state.connect(self._on_link_state)
        self._wait_start = 0.0
        self._wait_timer = QTimer(self)
        self._wait_timer.setInterval(1000)
        self._wait_timer.timeout.connect(self._tick_waiting)

    def show_handoff(self, handoff: PacketHandoff, attachment_names: list[str],
                     document_count: int, scan: bool = False) -> None:
        self.handoff = handoff
        again = ""
        request = handoff.request
        if handoff.task_key == "plan" and "round-" in request.task_id:
            again = " · AGAIN"
        self.eyebrow.setText(TASK_STEPS[handoff.task_key] + again)
        self.title.setText(text(TASK_TITLES[handoff.task_key]))
        self._focus_holder.setVisible(scan)
        self.update_packet(handoff.zip_path, attachment_names, document_count)
        zip_reply = handoff.reply_kind == "zip"
        lead_key = ("receive.lead.zip" if zip_reply
                    else "receive.lead.scene" if handoff.reply_kind == "scene"
                    else "receive.lead.json")
        self.lead.setText(text(lead_key))
        self.paste_button.setVisible(not zip_reply)
        self.send_status.set_state("plain", "")
        self.status.set_state("plain", "")
        self._wait_start = time.monotonic()
        self._tick_waiting()
        self._wait_timer.start()
        self.maybe_auto_link()

    def _tick_waiting(self) -> None:
        """FR-D5: a live sign of life while the person is in Copilot."""
        elapsed = max(0, int(time.monotonic() - self._wait_start))
        minutes, seconds = divmod(elapsed, 60)
        self.waiting_label.setText(
            text("exchange.waiting", time=f"{minutes}:{seconds:02d}"))

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.matches(QKeySequence.StandardKey.Copy):
            focus = QApplication.focusWidget()
            if not isinstance(focus, (QLineEdit, QPlainTextEdit)):
                if onedrive_settings().folder:
                    self._copy_link()
                else:
                    self._copy_file()
                return
        super().keyPressEvent(event)

    def update_packet(self, zip_path: Path, attachment_names: list[str],
                      document_count: int) -> None:
        size = zip_path.stat().st_size if zip_path.is_file() else 0
        self.card.set_packet(zip_path, size)
        documents = (f"documents/ — {document_count} · " if document_count else "")
        self.contents.setText(
            "TASK.md · GLOBAL.md · CODEBASE.md · MANIFEST.json · "
            f"{documents}attachments/ — {len(attachment_names)}")
        self.chips.set_files(attachment_names)

    def _emit_focus(self) -> None:
        self.scan_focus.emit(self.focus_edit.text().strip())

    # ---- send side ----

    def maybe_auto_link(self) -> None:
        """FR-P2: publish and copy the link alone when a packet appears."""
        if not load_ui_settings().get("auto_link", True):
            return
        if not onedrive_settings().folder:
            self.send_status.set_state("plain", text("send.link.unset"))
            return
        self._copy_link()

    def _copy_file(self) -> None:
        if self.handoff is None:
            return
        from PySide6.QtCore import QMimeData, QUrl
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(self.card.packet_path))])
        QGuiApplication.clipboard().setMimeData(mime)
        self.send_status.set_state("ok", text("send.file.copied"))

    def _copy_link(self) -> None:
        if self.handoff is None:
            return
        settings = onedrive_settings()
        packet = self.card.packet_path
        self.link_button.setEnabled(False)
        self.send_status.set_state("busy", text("send.link.copying"))
        expand = self.package_style == "folder"

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
            self.send_status.set_state("bad", value)
            return
        if value:
            QGuiApplication.clipboard().setText(value)
        if state == SYNCED:
            self.send_status.set_state(
                "ok", f"{text('send.link.done')} {text('send.link.paste')}")
        elif state == PENDING:
            self.send_status.set_state("warn", text("send.link.manual"))
        else:
            self.send_status.set_state(
                "plain", f"{text('send.link.paste')} {text('send.link.manual')}")

    def mark_exported(self, name: str) -> None:
        self.send_status.set_state("ok", text("send.exported", name=name))

    # ---- receive side ----

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
            self._wait_timer.stop()
            self.waiting_label.setText("")
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
    rescope_note = Signal(str)

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
            button(text("plan.rescope"), "Secondary",
                   lambda: self.note_panel.open(text("note.body.plan"))))
        self.note_panel = NotePanel()
        self.note_panel.send.connect(self.rescope_note.emit)
        self.add(self.note_panel)

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
    rescope_note = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.eyebrow = label("STEP 3 OF 5 — REVIEW", "Eyebrow")
        self.add(self.eyebrow)
        self.title = label("", "Title")
        self.add(self.title)
        self._cards = QVBoxLayout()
        self._cards.setSpacing(9)
        holder = QWidget()
        holder.setLayout(self._cards)
        self.add(holder)
        self.add_gap()
        self.add_row(button(text("findings.repair.button"), "Primary",
                            self.repair.emit),
                     button(text("findings.rescope"), "Ghost",
                            lambda: self.note_panel.open(
                                text("note.body.rescope"))))
        self.add(label(text("findings.repair.sub"), "Hint"))
        self.note_panel = NotePanel()
        self.note_panel.send.connect(self.rescope_note.emit)
        self.add(self.note_panel)

    def show_findings(self, findings: list[dict],
                      round_number: int = 1) -> None:
        self.eyebrow.setText("STEP 3 OF 5 — REVIEW"
                             + (f" · ROUND {round_number}"
                                if round_number > 1 else ""))
        self.note_panel.hide()
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
    rescope_note = Signal(str)
    retry = Signal()

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
        self.retry_button = button(text("test.retry"), "Secondary",
                                   self.retry.emit)
        self.rescope_button = button(
            text("test.rescope"), "Ghost",
            lambda: self.note_panel.open(text("note.body.rescope")))
        self.add_row(self.repair_button, self.retry_button, self.rescope_button)
        self.note_panel = NotePanel()
        self.note_panel.send.connect(self.rescope_note.emit)
        self.add(self.note_panel)
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
        self.retry_button.setVisible(visible)
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
    feedback_note = Signal(str)
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
        self.diff_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._diff_highlighter = DiffHighlighter(self.diff_view.document())
        self.add(self.diff_view)
        self.add_gap()
        self.add_row(button(text("save.accept"), "Primary", self.accept.emit))
        self.add(label(text("save.accept.sub"), "Hint"))
        self.add_gap(2)
        self.add_row(
            button(text("save.feedback"), "Secondary",
                   lambda: self.note_panel.open(text("note.body.feedback"))),
            button(text("test.run_again"), "Ghost", self.rerun.emit),
            button(text("save.discard"), "Danger", self.discard.emit))
        self.note_panel = NotePanel()
        self.note_panel.send.connect(self.feedback_note.emit)
        self.add(self.note_panel)

    def show_record(self, record: RunRecord, changed: list[str], diff: str) -> None:
        self.record = record
        self.note_panel.hide()
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
        tick = IconSquare("check", kind="ok", size=54, icon_size=26,
                          circle=True)
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
        # FR-D1: the win, in numbers.
        self.stats_card = QFrame()
        self.stats_card.setObjectName("Card")
        stats = QVBoxLayout(self.stats_card)
        stats.setContentsMargins(16, 12, 16, 12)
        stats.setSpacing(4)
        self.stat_files = QLabel("")
        self.stat_files.setObjectName("ChoiceTitle")
        self.stat_files.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.file_names = QLabel("")
        self.file_names.setObjectName("Hint")
        self.file_names.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stat_line = QLabel("")
        self.stat_line.setObjectName("Hint")
        self.stat_line.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for widget in (self.stat_files, self.file_names, self.stat_line):
            stats.addWidget(widget)
        self.add(self.stats_card)
        audit = label(text("done.audit"), "Lead")
        audit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.add(audit)
        self.message = StatusLine()
        self.add(self.message)
        self.add_gap()
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(button(text("done.new"), "Primary", self.new_change.emit))
        buttons.addWidget(button(text("done.merge"), "Secondary",
                                 self._copy_merge))
        buttons.addWidget(button(text("done.history"), "Ghost",
                                 self.open_history.emit))
        buttons.addStretch(1)
        holder2 = QWidget()
        holder2.setLayout(buttons)
        self.add(holder2)
        self._branch = ""

    def show_record(self, record: RunRecord, files: list[str] = (),
                    checks: int = 0, iterations: int = 0,
                    duration: str = "") -> None:
        self._branch = record.branch
        self.branch.setText(text("done.branch", branch=record.branch))
        self.message.set_state("plain", "")
        files = list(files)
        self.stat_files.setText(
            text("done.files.one") if len(files) == 1
            else text("done.files", count=len(files)))
        shown = " · ".join(files[:3]) + (" …" if len(files) > 3 else "")
        self.file_names.setText(shown)
        self.file_names.setVisible(bool(shown))
        checks_text = (text("done.checks.one") if checks == 1
                       else text("done.checks", count=checks))
        self.stat_line.setText(
            f"{checks_text} · "
            + text("done.steps", count=iterations, time=duration))

    def _copy_merge(self) -> None:
        if self._branch:
            QGuiApplication.clipboard().setText(
                f"git merge --no-ff {self._branch}")
            self.message.set_state("ok", text("done.merge.done"))


class HistoryRow(QFrame):
    clicked = Signal()

    def __init__(self, summary: RunSummary) -> None:
        super().__init__()
        self.setObjectName("Choice")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        row = QHBoxLayout(self)
        row.setContentsMargins(13, 11, 15, 11)
        row.setSpacing(12)
        icon = ("wrench", "warn") if summary.mode == "issue" else ("plus", "accent")
        row.addWidget(IconSquare(icon[0], kind=icon[1], size=34, icon_size=18))
        column = QVBoxLayout()
        column.setSpacing(1)
        request = " ".join(summary.request.split())[:64]
        title = QLabel(request or f"Run {summary.run_id}")
        title.setObjectName("ChoiceTitle")
        sub = QLabel(f"{summary.run_id} · {summary.updated_at[:10]}")
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


PROJECT_STATE_CHIPS = {"ready": ("projects.state.ready", "pass"),
                       "setup": ("projects.state.setup", "warn"),
                       "no_git": ("projects.state.no_git", "wait"),
                       "missing": ("projects.state.missing", "fail")}
PROJECT_ICON_KINDS = {"ready": "ok", "setup": "warn", "no_git": "neutral",
                      "missing": "bad"}


class ProjectRowWidget(QFrame):
    open_requested = Signal()
    remove_requested = Signal()

    def __init__(self, name: str, path: str, status: str) -> None:
        super().__init__()
        self.setObjectName("Choice")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        row = QHBoxLayout(self)
        row.setContentsMargins(13, 11, 11, 11)
        row.setSpacing(12)
        row.addWidget(IconSquare(
            "folder", kind=PROJECT_ICON_KINDS.get(status, "bad"),
            size=34, icon_size=18))
        column = QVBoxLayout()
        column.setSpacing(1)
        title = QLabel(name)
        title.setObjectName("ChoiceTitle")
        sub = ElidedLabel(path, "MonoHint")
        column.addWidget(title)
        column.addWidget(sub)
        row.addLayout(column, 1)
        key, kind = PROJECT_STATE_CHIPS.get(status, ("projects.state.missing",
                                                     "fail"))
        row.addWidget(StateChip(text(key).upper(), kind))
        remove = QPushButton("×")
        remove.setObjectName("ChipRemove")
        remove.setFixedSize(22, 22)
        remove.setCursor(Qt.CursorShape.PointingHandCursor)
        remove.setAccessibleName(f"Remove {name} from the list")
        remove.clicked.connect(self.remove_requested.emit)
        row.addWidget(remove)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.open_requested.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.open_requested.emit()
            return
        super().keyPressEvent(event)


class ProjectsScreen(Screen):
    open_project = Signal(str)
    remove_project = Signal(str)
    new_project = Signal()
    add_folder = Signal()
    back = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.add(label(text("projects.title"), "Title"))
        self._rows = QVBoxLayout()
        self._rows.setSpacing(9)
        holder = QWidget()
        holder.setLayout(self._rows)
        self.add(holder)
        self.empty = label(text("projects.empty"), "Lead")
        self.add(self.empty)
        self.add_gap(2)
        self.add_row(
            button(text("projects.new"), "Primary", self.new_project.emit),
            button(text("projects.add"), "Secondary", self.add_folder.emit))
        self.add_gap()
        self.add_row(button(text("history.back"), "Ghost", self.back.emit))

    def show_rows(self, rows) -> None:
        self.clear_layout(self._rows)
        self.empty.setVisible(not rows)
        for project in rows:
            widget = ProjectRowWidget(project.name, str(project.path),
                                      project.status)
            widget.open_requested.connect(
                lambda p=str(project.path): self.open_project.emit(p))
            widget.remove_requested.connect(
                lambda p=str(project.path): self.remove_project.emit(p))
            self._rows.addWidget(widget)


class IssueRowWidget(QFrame):
    clicked = Signal()

    def __init__(self, issue) -> None:
        super().__init__()
        self.setObjectName("Choice")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        row = QHBoxLayout(self)
        row.setContentsMargins(13, 11, 15, 11)
        row.setSpacing(12)
        row.addWidget(IconSquare(
            "bug", kind=SEVERITY_ICON_KINDS.get(issue.severity, "neutral"),
            size=34, icon_size=18))
        column = QVBoxLayout()
        column.setSpacing(1)
        title = QLabel(issue.title)
        title.setObjectName("ChoiceTitle")
        source = text("issues.source." + issue.source)
        place = f"{issue.file}:{issue.line}" if issue.file else issue.id
        sub = ElidedLabel(f"{source} · {place}", "MonoHint")
        column.addWidget(title)
        column.addWidget(sub)
        row.addLayout(column, 1)
        severity_key, severity_kind = SEVERITY_CHIPS.get(
            issue.severity, SEVERITY_CHIPS["low"])
        row.addWidget(StateChip(text(severity_key).upper(), severity_kind))
        status_key, status_kind = ISSUE_STATUS_CHIPS.get(
            issue.status, ISSUE_STATUS_CHIPS["open"])
        row.addWidget(StateChip(text(status_key).upper(), status_kind))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)


class IssuesScreen(Screen):
    open_issue = Signal(str)
    add_issue = Signal()
    scan = Signal()
    back = Signal()

    FILTERS = ("all", "open", "in_work", "closed")

    def __init__(self) -> None:
        super().__init__()
        self.add(label(text("issues.title"), "Title"))
        self._filter = "open"
        self._issues: list = []
        tabs = QHBoxLayout()
        tabs.setSpacing(6)
        holder = QWidget()
        holder.setLayout(tabs)
        self._tab_buttons: dict[str, QPushButton] = {}
        for key in self.FILTERS:
            control = button(text("issues.filter." + key), "Secondary",
                             lambda k=key: self.set_filter(k))
            control.setStyleSheet("padding: 5px 13px; font-size: 12px;")
            tabs.addWidget(control)
            self._tab_buttons[key] = control
        tabs.addStretch(1)
        self.add(holder)
        self._rows = QVBoxLayout()
        self._rows.setSpacing(9)
        rows_holder = QWidget()
        rows_holder.setLayout(self._rows)
        self.add(rows_holder)
        self.empty = label(text("issues.empty"), "Lead")
        self.add(self.empty)
        self.add_gap(2)
        self.add_row(
            button(text("issues.scan"), "Primary", self.scan.emit),
            button(text("issues.add"), "Secondary", self.add_issue.emit))
        self.add_gap()
        self.add_row(button(text("history.back"), "Ghost", self.back.emit))

    def set_filter(self, key: str) -> None:
        self._filter = key
        self._render()

    def show_issues(self, issues: list) -> None:
        self._issues = list(issues)
        self._render()

    def _matches(self, issue) -> bool:
        return self._filter == "all" or issue.status == self._filter

    def _render(self) -> None:
        for key, control in self._tab_buttons.items():
            control.setObjectName("Primary" if key == self._filter
                                  else "Secondary")
            control.style().unpolish(control)
            control.style().polish(control)
        self.clear_layout(self._rows)
        shown = [issue for issue in self._issues if self._matches(issue)]
        self.empty.setVisible(not shown)
        for issue in shown:
            row = IssueRowWidget(issue)
            row.clicked.connect(
                lambda issue_id=issue.id: self.open_issue.emit(issue_id))
            self._rows.addWidget(row)


class IssueDetailScreen(Screen):
    save = Signal()
    repair = Signal(str)
    discuss_note = Signal(str, str)   # issue id, question
    close_reason = Signal(str, str)   # issue id, reason
    reopen = Signal(str)
    remove = Signal(str)
    back = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.issue_id = ""
        self.eyebrow = label("", "Eyebrow")
        self.add(self.eyebrow)
        head = QHBoxLayout()
        head.setSpacing(9)
        self.status_chip = StateChip("", "wait")
        self.severity_chip = StateChip("", "wait")
        head.addWidget(self.severity_chip)
        head.addWidget(self.status_chip)
        head.addStretch(1)
        head_holder = QWidget()
        head_holder.setLayout(head)
        self.add(head_holder)
        # FR-P6: the decisions come first; the fields follow.
        self.repair_button = button(text("issue.repair"), "Secondary",
                                    lambda: self.repair.emit(self.issue_id))
        self.discuss_button = button(
            text("issue.discuss"), "Secondary",
            lambda: self.note_panel.open(text("discuss.ask.body")))
        self.close_button = button(text("issue.close"), "Secondary",
                                   self._toggle_reasons)
        self.reopen_button = button(text("issue.reopen"), "Secondary",
                                    lambda: self.reopen.emit(self.issue_id))
        self.remove_button = button(text("issue.remove"), "Danger",
                                    lambda: self.remove.emit(self.issue_id))
        self._actions_holder = QWidget()
        actions = QHBoxLayout(self._actions_holder)
        actions.setSpacing(8)
        actions.setContentsMargins(0, 0, 0, 0)
        for control in (self.repair_button, self.discuss_button,
                        self.close_button, self.reopen_button,
                        self.remove_button):
            actions.addWidget(control)
        actions.addStretch(1)
        self.add(self._actions_holder)
        self._reasons_holder = QWidget()
        reasons = QHBoxLayout(self._reasons_holder)
        reasons.setSpacing(6)
        reasons.setContentsMargins(0, 0, 0, 0)
        reasons.addWidget(label(text("issue.close.pick"), "Hint"))
        for reason in REASONS:
            control = button(text("issues.reason." + reason), "Secondary",
                             lambda r=reason: self.close_reason.emit(
                                 self.issue_id, r))
            control.setStyleSheet("padding: 4px 10px; font-size: 12px;")
            reasons.addWidget(control)
        reasons.addStretch(1)
        self._reasons_holder.setVisible(False)
        self.add(self._reasons_holder)
        self.repair_hint = label(text("issue.repair.sub"), "Hint")
        self.add(self.repair_hint)
        self.note_panel = NotePanel()
        self.note_panel.send.connect(
            lambda note: self.discuss_note.emit(self.issue_id, note))
        self.add(self.note_panel)
        self.add(label(text("issue.field.title").upper(), "Eyebrow"))
        self.title_edit = QLineEdit()
        self.add(self.title_edit)
        self.add(label(text("issue.field.severity").upper(), "Eyebrow"))
        radios = QHBoxLayout()
        radios.setSpacing(14)
        self.radio_high = QRadioButton(text("issues.severity.high"))
        self.radio_medium = QRadioButton(text("issues.severity.medium"))
        self.radio_low = QRadioButton(text("issues.severity.low"))
        for control in (self.radio_high, self.radio_medium, self.radio_low):
            radios.addWidget(control)
        radios.addStretch(1)
        radio_holder = QWidget()
        radio_holder.setLayout(radios)
        self.add(radio_holder)
        self.add(label(text("issue.field.detail").upper(), "Eyebrow"))
        self.detail_edit = QPlainTextEdit()
        self.detail_edit.setFixedHeight(110)
        self.add(self.detail_edit)
        self.location = label("", "MonoHint")
        self.add(self.location)
        self.snippet_view = QPlainTextEdit()
        self.snippet_view.setObjectName("Code")
        self.snippet_view.setReadOnly(True)
        self.snippet_view.setFixedHeight(64)
        self.add(self.snippet_view)
        self.notes_title = label(text("issue.notes").upper(), "Eyebrow")
        self.add(self.notes_title)
        self._notes = QVBoxLayout()
        self._notes.setSpacing(7)
        notes_holder = QWidget()
        notes_holder.setLayout(self._notes)
        self.add(notes_holder)
        self.message = StatusLine()
        self.add(self.message)
        self.add_gap(2)
        self.add_row(
            button(text("issue.save"), "Primary", self.save.emit),
            button(text("history.back"), "Ghost", self.back.emit))

    def _toggle_reasons(self) -> None:
        self._reasons_holder.setVisible(not self._reasons_holder.isVisible())

    def severity(self) -> str:
        if self.radio_high.isChecked():
            return "high"
        if self.radio_low.isChecked():
            return "low"
        return "medium"

    def load(self, issue) -> None:
        """issue=None starts a new, human-entered issue."""
        existing = issue is not None
        self.issue_id = issue.id if existing else ""
        self.message.set_state("plain", "")
        self._actions_holder.setVisible(existing)
        self._reasons_holder.setVisible(False)
        self.note_panel.hide()
        if existing:
            source = text("issues.source." + issue.source)
            self.eyebrow.setText(
                text("issue.eyebrow", id=issue.id, source=source).upper())
            severity_key, severity_kind = SEVERITY_CHIPS.get(
                issue.severity, SEVERITY_CHIPS["low"])
            self.severity_chip.set_state(text(severity_key).upper(),
                                         severity_kind)
            if issue.status == "closed":
                reason = text("issues.reason." + issue.closed_reason)
                self.status_chip.set_state(reason.upper(), "wait")
            else:
                status_key, status_kind = ISSUE_STATUS_CHIPS[issue.status]
                self.status_chip.set_state(text(status_key).upper(),
                                           status_kind)
        else:
            self.eyebrow.setText(text("issue.new.title").upper())
            self.severity_chip.set_state("", "wait")
            self.status_chip.set_state("", "wait")
        self.severity_chip.setVisible(existing)
        self.status_chip.setVisible(existing)
        self.title_edit.setText(issue.title if existing else "")
        self.detail_edit.setPlainText(issue.detail if existing else "")
        severity = issue.severity if existing else "medium"
        {"high": self.radio_high, "medium": self.radio_medium,
         "low": self.radio_low}[severity].setChecked(True)
        place = (f"{issue.file}:{issue.line}" if existing and issue.file else "")
        self.location.setText(
            f"{text('issue.location')}: {place}" if place else "")
        self.location.setVisible(bool(place))
        snippet = issue.snippet if existing else ""
        self.snippet_view.setPlainText(snippet)
        self.snippet_view.setVisible(bool(snippet.strip()))
        self.clear_layout(self._notes)
        notes = list(issue.notes) if existing else []
        self.notes_title.setVisible(bool(notes))
        for note in notes:
            card = QFrame()
            card.setObjectName("Card")
            box = QVBoxLayout(card)
            box.setContentsMargins(12, 9, 12, 9)
            box.setSpacing(2)
            stamp = str(note.get("time", ""))[:16].replace("T", " ")
            box.addWidget(label(f"{note.get('author', '')} · {stamp}", "Hint"))
            body = label(str(note.get("text", "")))
            box.addWidget(body)
            self._notes.addWidget(card)
        closed = existing and issue.status == "closed"
        self.repair_button.setVisible(existing and not closed)
        self.discuss_button.setVisible(existing and not closed)
        self.repair_hint.setVisible(existing and not closed)
        self.close_button.setVisible(existing and not closed)
        self.reopen_button.setVisible(closed)
        self.remove_button.setVisible(existing)


class ScanCheckScreen(Screen):
    add_selected = Signal(list)   # indexes into the shown candidates
    discard = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.add(label("ISSUE SCAN", "Eyebrow"))
        self.title = label("", "Title")
        self.add(self.title)
        self.known = label("", "Hint")
        self.add(self.known)
        self._rows = QVBoxLayout()
        self._rows.setSpacing(9)
        holder = QWidget()
        holder.setLayout(self._rows)
        self.add(holder)
        self.message = StatusLine()
        self.add(self.message)
        self.add_gap()
        self.add_row(
            button(text("scan.check.add"), "Primary", self._add),
            button(text("scan.discard"), "Ghost", self.discard.emit))
        self._boxes: list[QCheckBox] = []

    def show_candidates(self, candidates: list, known_dropped: int) -> None:
        self.title.setText(
            text("scan.check.title.one") if len(candidates) == 1
            else text("scan.check.title.many", count=len(candidates)))
        self.known.setText(
            text("scan.check.known", count=known_dropped) if known_dropped
            else "")
        self.known.setVisible(bool(known_dropped))
        self.message.set_state("plain", "")
        self.clear_layout(self._rows)
        self._boxes = []
        for candidate in candidates:
            card = QFrame()
            card.setObjectName("Card")
            row = QHBoxLayout(card)
            row.setContentsMargins(12, 10, 12, 10)
            row.setSpacing(10)
            box = QCheckBox()
            box.setChecked(candidate.verified)
            row.addWidget(box, 0, Qt.AlignmentFlag.AlignTop)
            column = QVBoxLayout()
            column.setSpacing(2)
            title = QLabel(candidate.title)
            title.setObjectName("ChoiceTitle")
            title.setWordWrap(True)
            column.addWidget(title)
            place = (f"{candidate.file}:{candidate.line}" if candidate.file
                     else "")
            if place:
                column.addWidget(ElidedLabel(place, "MonoHint"))
            if candidate.detail:
                column.addWidget(label(candidate.detail, "Dim"))
            if not candidate.verified:
                column.addWidget(label(text("scan.check.unverified"), "Bad"))
            row.addLayout(column, 1)
            severity_key, severity_kind = SEVERITY_CHIPS.get(
                candidate.severity, SEVERITY_CHIPS["low"])
            row.addWidget(StateChip(text(severity_key).upper(), severity_kind),
                          0, Qt.AlignmentFlag.AlignTop)
            self._rows.addWidget(card)
            self._boxes.append(box)

    def _add(self) -> None:
        selected = [index for index, box in enumerate(self._boxes)
                    if box.isChecked()]
        if not selected:
            self.message.set_state("bad", text("scan.check.none"))
            return
        self.add_selected.emit(selected)


class ExplainScreen(Screen):
    start = Signal(list, str, str)   # files, goal, audience
    back = Signal()
    import_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.add(label(text("explain.title"), "Title"))
        self.add(label(text("explain.goal").upper(), "Eyebrow"))
        self.goal_edit = QPlainTextEdit()
        self.goal_edit.setPlaceholderText(text("explain.goal.placeholder"))
        self.goal_edit.setFixedHeight(70)
        self.add(self.goal_edit)
        self.add(label(text("explain.audience").upper(), "Eyebrow"))
        self.audience_edit = QLineEdit()
        self.audience_edit.setPlaceholderText(
            text("explain.audience.placeholder"))
        self.add(self.audience_edit)
        self.add(label(text("explain.files").upper(), "Eyebrow"))
        zone = DropZone(text("explain.drop.main"), text("explain.drop.sub"))
        zone.files_dropped.connect(self.add_files)
        zone.clicked.connect(self.import_requested.emit)
        self.add(zone)
        self.chips = FileChips()
        self.chips.removed.connect(self._remove)
        self.add(self.chips)
        self.message = StatusLine()
        self.add(self.message)
        self.add_gap()
        self.add_row(
            button(text("explain.start"), "Primary", self._start),
            button(text("receive.back"), "Ghost", self.back.emit))
        self.files: list[Path] = []

    def reset(self) -> None:
        self.goal_edit.setPlainText("")
        self.audience_edit.setText("")
        self.files = []
        self.chips.set_files([])
        self.message.set_state("plain", "")

    def add_files(self, paths: list) -> None:
        self.files.extend(Path(item) for item in paths)
        self.chips.set_files([item.name for item in self.files])

    def _remove(self, index: int) -> None:
        del self.files[index]
        self.chips.set_files([item.name for item in self.files])

    def _start(self) -> None:
        goal = self.goal_edit.toPlainText().strip()
        if not goal:
            self.message.set_state("bad", text("explain.goal.empty"))
            return
        if not self.files:
            self.message.set_state("bad", text("explain.files.empty"))
            return
        self.message.set_state("plain", "")
        self.start.emit(list(self.files), goal,
                        self.audience_edit.text().strip())


class ExplainResultScreen(Screen):
    open_video = Signal()
    open_folder = Signal()
    repair = Signal()
    done = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.add(label("CODE EXPLANATION", "Eyebrow"))
        self.add(label(text("explain.result.title"), "Title"))
        self.check_chip = StateChip("PASS", "pass")
        self.render_chip = StateChip("RUN", "accent")
        for name_key, chip in (("explain.check.name", self.check_chip),
                               ("explain.render.name", self.render_chip)):
            card = QFrame()
            card.setObjectName("Card")
            row = QHBoxLayout(card)
            row.setContentsMargins(13, 10, 13, 10)
            title = label(text(name_key))
            font = title.font()
            font.setBold(True)
            title.setFont(font)
            row.addWidget(title, 1)
            row.addWidget(chip)
            self.add(card)
        self.status = StatusLine()
        self.add(self.status)
        self.folder_label = ElidedLabel("", "MonoHint")
        self.add(self.folder_label)
        self.sheet_view = QLabel()
        self.sheet_view.setVisible(False)
        self.sheet_view.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sheet_view.setScaledContents(False)
        self.sheet_view.mousePressEvent = lambda event: self.open_video.emit()
        self.add(self.sheet_view)
        self.tail_view = QPlainTextEdit()
        self.tail_view.setObjectName("Code")
        self.tail_view.setReadOnly(True)
        self.tail_view.setFixedHeight(120)
        self.tail_view.setVisible(False)
        self.add(self.tail_view)
        self.add_gap()
        self.video_button = button(text("explain.open.video"), "Primary",
                                   self.open_video.emit)
        self.repair_button = button(text("explain.repair"), "Primary",
                                    self.repair.emit)
        self.add_row(self.video_button, self.repair_button)
        self.repair_hint = label(text("explain.repair.sub"), "Hint")
        self.add(self.repair_hint)
        self.add_row(
            button(text("explain.open.folder"), "Secondary",
                   self.open_folder.emit),
            button(text("explain.done"), "Ghost", self.done.emit))
        self.add(label(text("explain.saved.note"), "Hint"))
        self._output_tail = ""

    def show_running(self, folder: str) -> None:
        self.render_chip.set_state("RUN", "accent")
        self.status.set_state("busy", text("explain.render.running"))
        self.folder_label.setText(folder)
        self._output_tail = ""
        self.sheet_view.setVisible(False)
        self.tail_view.setVisible(False)
        self.video_button.setVisible(False)
        self.repair_button.setVisible(False)
        self.repair_hint.setVisible(False)

    def show_passed(self, sheet=None) -> None:
        self.render_chip.set_state("PASS", "pass")
        self.status.set_state("ok", text("explain.render.passed"))
        if sheet:
            pixmap = QPixmap(str(sheet))
            if not pixmap.isNull():
                self.sheet_view.setPixmap(pixmap.scaledToWidth(
                    560, Qt.TransformationMode.SmoothTransformation))
                self.sheet_view.setVisible(True)
        self.video_button.setVisible(True)
        self.repair_button.setVisible(False)
        self.repair_hint.setVisible(False)

    def show_failed(self, message: str, tail: str) -> None:
        self.render_chip.set_state("FAIL", "fail")
        self.status.set_state("bad", message or text("explain.render.failed"))
        self._output_tail = tail[-4000:]
        self.tail_view.setPlainText(self._output_tail)
        self.tail_view.setVisible(bool(tail.strip()))
        self.video_button.setVisible(False)
        self.repair_button.setVisible(True)
        self.repair_hint.setVisible(True)

    def show_findings(self, findings: list) -> None:
        """Quality findings never block; they show above the output tail."""
        if not findings:
            return
        block = (text("explain.findings.head") + "\n"
                 + "\n".join(f"- {item}" for item in findings))
        if self._output_tail.strip():
            block = block + "\n\n" + self._output_tail
        self.tail_view.setPlainText(block)
        self.tail_view.setVisible(True)
        self.repair_button.setVisible(True)
        self.repair_hint.setVisible(True)


class ExplainSettingsPage(Screen):
    saved = Signal()
    back = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.add(label(text("settings.explain"), "Title"))
        self.add(label(text("explain.set.command").upper(), "Eyebrow"))
        self.command_edit = QLineEdit()
        self.add(self.command_edit)
        self.add(label(text("explain.set.command.hint"), "Hint"))
        self.add(label(text("explain.set.install"), "MonoHint"))
        self.add_gap()
        self.add_row(
            button(text("settings.save"), "Primary", self.saved.emit),
            button(text("settings.back"), "Ghost", self.back.emit))

    def load(self, command: str) -> None:
        self.command_edit.setText(command)

    def value(self) -> str:
        return self.command_edit.text().strip() or "manim"


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
                title.setStyleSheet(f"color: {palette().faint};")
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

    ICON_NAMES = {"onedrive": "cloud", "tasks": "file-text", "global": "globe",
                  "package": "box", "checks": "check-circle",
                  "explain": "film"}

    def __init__(self) -> None:
        super().__init__()
        self.add(label(text("settings.title"), "Title"))
        for key in ("onedrive", "tasks", "global", "package", "checks",
                    "explain"):
            card = ChoiceButton(self.ICON_NAMES[key], text("settings." + key),
                                text("settings." + key + ".sub"))
            card.clicked.connect(lambda page=key: self.open_page.emit(page))
            self.add(card)
        self.add_gap()
        self.add_row(button(text("settings.back"), "Ghost", self.back.emit))


class OneDrivePage(Screen):
    saved = Signal()
    back = Signal()
    browse = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.add(label(text("settings.onedrive"), "Title"))
        self.add(label(text("onedrive.folder").upper(), "Eyebrow"))
        self.folder_edit = QLineEdit()
        self.add(self.folder_edit)
        self.add(label(text("onedrive.folder.hint"), "Hint"))
        self.add_row(button(text("onedrive.browse"), "Secondary",
                            self.browse.emit))
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
        self.add_gap(2)
        self.autolink_box = QCheckBox(text("onedrive.autolink"))
        self.add(self.autolink_box)
        self.add(label(text("exchange.downloads").upper(), "Eyebrow"))
        self.downloads_edit = QLineEdit()
        self.add(self.downloads_edit)
        self.add(label(text("exchange.downloads.hint"), "Hint"))
        self.add_gap()
        self.add_row(
            button(text("settings.save"), "Primary", self._save),
            button(text("settings.back"), "Ghost", self.back.emit))

    def load(self) -> None:
        settings = onedrive_settings()
        self.folder_edit.setText(settings.folder)
        self.link_edit.setText(settings.link_base)
        self.timeout_edit.setValue(settings.timeout_seconds)
        values = load_ui_settings()
        self.autolink_box.setChecked(bool(values.get("auto_link", True)))
        self.downloads_edit.setText(
            str(values.get("downloads_path") or default_downloads()))

    def _preview(self, value: str) -> None:
        base = value.strip().rstrip("/")
        self.example.setText(
            text("onedrive.example", link=f"{base}/maintain-run-plan.zip") if base else "")

    def _save(self) -> None:
        save_onedrive_settings(OneDriveSettings(
            folder=self.folder_edit.text().strip(),
            link_base=self.link_edit.text().strip(),
            timeout_seconds=int(self.timeout_edit.value())))
        values = load_ui_settings()
        values["auto_link"] = self.autolink_box.isChecked()
        values["downloads_path"] = self.downloads_edit.text().strip()
        save_ui_settings(values)
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
        for key in ("project", "plan", "build", "repair", "review", "scan",
                    "discuss", "explain"):
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
            holder.setParent(None)
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
