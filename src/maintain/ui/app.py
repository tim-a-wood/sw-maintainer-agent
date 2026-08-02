"""The main window: navigation, bridge wiring, and dialogs."""

from __future__ import annotations

import contextlib
import shutil
import threading
from pathlib import Path

from PySide6.QtCore import QPoint, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit,
                               QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
                               QPushButton, QStackedWidget, QVBoxLayout,
                               QWidget)

from maintain.config import ProjectConfig
from maintain.downloads import default_downloads, newest_reply
from maintain.errors import MaintainError
from maintain.gates import GateDecision
from maintain.explain_store import (DISCARDED as EXPLAIN_DISCARDED,
                                    FAILED as EXPLAIN_FAILED,
                                    PASSED as EXPLAIN_PASSED,
                                    WAITING as EXPLAIN_WAITING,
                                    is_stale, load_explain_states,
                                    restore_request, resumable_explain,
                                    save_explain_state)
from maintain.issue_packets import (SideExchange, build_side_packet,
                                    discuss_reply, discuss_request,
                                    explain_dir, explain_request,
                                    scan_candidates, scan_request,
                                    side_packet_dir)
from maintain.issues import (CLOSED, IssueCandidate, display_order,
                             related_open_issues)
from maintain.models import RunRecord, RunState, utc_now
from maintain.providers.command import parse_response
from maintain.render import render_scene
from maintain.scene_check import scene_class_name
from maintain.scene_probe import probe_scene
from maintain.scene_quality import quality_findings
from maintain.providers.manual_ui import PacketHandoff
from maintain.repository_memory import load_ui_settings, save_ui_settings
from maintain.zip_package import PacketBuild

from . import theme as theme_module

from . import projects as project_ops
from .config_store import ConfigStore
from .controller import Controller
from .bridge import check_reply
from .screens import (BusyScreen, ChecksPage, DescribeScreen, DoneScreen,
                      DownloadsPage, ExchangeScreen, ExplainResultScreen,
                      ExplainScreen, ExplainSettingsPage, FindingsScreen,
                      GlobalPage, HistoryScreen, HomeScreen, IssueDetailScreen,
                      IssuesScreen, PackagePage, PlanCheckScreen,
                      ProjectsScreen, RunDetailScreen, SaveScreen,
                      ScanCheckScreen, SettingsScreen, TasksPage, TestScreen,
                      documents_count)
from .strings import text
from .widgets import StageHeader, ToastStack

STAGE_FOR_TASK = {"plan": 0, "build": 1, "repair": 1, "review": 2}


def chip_name(name: str, limit: int = 24) -> str:
    """The foot chip keeps its width; long project names elide hard."""
    return name if len(name) <= limit else name[:limit - 1] + "…"


@contextlib.contextmanager
def busy_pointer():
    """The wait cursor for any handler that can take a visible moment."""
    QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        yield
    finally:
        QGuiApplication.restoreOverrideCursor()


def _pip_install_manim() -> bool:
    """Install the video feature into the app's own environment.

    PySide6-Addons carries the in-window player. The pin matches the
    version that already runs, so pip never replaces Qt files that the
    open app holds locked.
    """
    import subprocess
    import sys as sys_module

    import PySide6

    from maintain.proc import hidden
    completed = subprocess.run(
        [sys_module.executable, "-m", "pip", "install", "manim==0.20.1",
         f"PySide6-Addons=={PySide6.__version__}"],
        capture_output=True, text=True, check=False, **hidden())
    return completed.returncode == 0


def saved_theme() -> str:
    value = str(load_ui_settings().get("theme", "dark"))
    return value if value in {"light", "dark"} else "dark"


class MainWindow(QMainWindow):
    explain_render_done = Signal(object)   # RenderResult, from the worker
    explain_render_step = Signal(str, str)  # phase, step text
    manim_install_done = Signal(bool, object)  # ok, resume arguments

    def __init__(self, config: ProjectConfig) -> None:
        super().__init__()
        self.setWindowTitle(f"{text('app.title')} — {config.name}")
        # Height covers the run-name head, the tallest screen content
        # (700px, pinned by test), and the foot bar.
        self.resize(640, 866)
        self.setMinimumSize(520, 620)
        self._theme = saved_theme()
        self.apply_theme(self._theme, persist=False)
        self.store = ConfigStore(config)
        self.controller = Controller(config)
        self.current_handoff: PacketHandoff | None = None
        self.current_record: RunRecord | None = None
        self._in_test = False
        self._side: dict | None = None
        self._pending_issue_links: list[str] = []
        self._explain: dict | None = None
        self.explain_render_done.connect(self._explain_render_done)
        self.explain_render_step.connect(
            lambda phase, step: self.explain_result.on_render_step(phase, step))
        self.manim_install_done.connect(self._manim_install_done)
        self._busy_timer = QTimer(self)
        self._busy_timer.setSingleShot(True)
        self._busy_timer.timeout.connect(self._busy_now)

        central = QWidget()
        column = QVBoxLayout(central)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        # FR-N2: the run's name heads the workflow screens — full
        # width, never squashed — and a click still edits it.
        self._run_head = QWidget()
        head_row = QHBoxLayout(self._run_head)
        head_row.setContentsMargins(12, 6, 12, 0)
        head_row.setSpacing(0)
        self.run_name = QPushButton("")
        self.run_name.setObjectName("Ghost")
        self.run_name.setCursor(Qt.CursorShape.PointingHandCursor)
        self.run_name.setToolTip(text("foot.name.tip"))
        self.run_name.clicked.connect(
            lambda checked=False: self._rename_run(
                getattr(self, "_foot_run_id", "")))
        head_row.addWidget(self.run_name)
        head_row.addStretch(1)
        self._run_head.setVisible(False)
        self._run_head_on = False
        self._current_screen = ""
        column.addWidget(self._run_head)
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
        self._toasts = ToastStack(self)
        self.setAcceptDrops(True)
        paste = QShortcut(QKeySequence.StandardKey.Paste, self)
        paste.activated.connect(self._paste_anywhere)
        self.show_home()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "_toasts"):
            self._toasts.reposition()

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self.exchange.reply_open and event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        """FR-G1: the reply lands on any screen and still arrives."""
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls()
                 if url.isLocalFile()]
        if paths and self.exchange.reply_open:
            self._route_reply(path=paths[0])
            event.acceptProposedAction()

    def _paste_anywhere(self) -> None:
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QPlainTextEdit)):
            focus.paste()
            return
        if not self.exchange.reply_open:
            return
        # A file copied in the file manager pastes like a drop (FR-G1).
        mime = QGuiApplication.clipboard().mimeData()
        if mime is not None and mime.hasUrls():
            paths = [Path(url.toLocalFile()) for url in mime.urls()
                     if url.isLocalFile()]
            if paths:
                self._route_reply(path=paths[0])
                return
        self._route_reply(
            clipboard_text=QGuiApplication.clipboard().text())

    def _route_reply(self, path=None, clipboard_text: str = "") -> None:
        if self.stack.currentWidget() is not self.exchange:
            self.show_screen("exchange")
        if path is not None:
            self.exchange.check(path=path)
        else:
            self.exchange.check(clipboard_text=clipboard_text)

    def closeEvent(self, event) -> None:  # noqa: N802
        """A running engine pauses and settles before the window dies.
        FR-N1: unnamed work gets one naming question on the way out;
        named work closes without one."""
        if self.controller.busy:
            self.controller.stop()
            self.controller.wait_settled()
            pending = getattr(self, "_rename_pending", None)
            if pending:
                self.controller.set_run_name(pending[0], pending[1])
                self._rename_pending = None
            summary = self.controller.resumable_run()
            if summary is not None and not summary.name:
                value = self.ask_line(text("stop.name.title"),
                                      text("stop.name.body"))
                if value:
                    self.controller.set_run_name(summary.run_id, value)
        super().closeEvent(event)

    # ----- construction -----

    def _foot_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("FootBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 6, 10, 6)
        self.foot_project = QPushButton("")
        self.foot_project.setObjectName("FootProject")
        self.foot_project.setCursor(Qt.CursorShape.PointingHandCursor)
        self.foot_project.clicked.connect(self._pick_project)
        row.addWidget(self.foot_project)
        self._set_project_chip(self.store.config.name)
        self.foot_label = QLabel("")
        self.foot_label.setObjectName("FootLabel")
        row.addWidget(self.foot_label)
        row.addStretch(1)
        self.foot_home = QPushButton(text("foot.home"))
        self.foot_home.setObjectName("Ghost")
        self.foot_home.setCursor(Qt.CursorShape.PointingHandCursor)
        self.foot_home.clicked.connect(self.show_home)
        row.addWidget(self.foot_home)
        # The theme toggle is one symbol: the mode a click switches to.
        self.foot_theme = QPushButton("")
        self.foot_theme.setObjectName("Ghost")
        self.foot_theme.setCursor(Qt.CursorShape.PointingHandCursor)
        self.foot_theme.setFixedWidth(34)
        self.foot_theme.clicked.connect(self.toggle_theme)
        self._set_theme_symbol(self._theme)
        row.addWidget(self.foot_theme)
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
        self.projects = ProjectsScreen()
        self.issues_list = IssuesScreen()
        self.issue_detail = IssueDetailScreen()
        self.scan_check = ScanCheckScreen()
        self.explain = ExplainScreen()
        self.explain_result = ExplainResultScreen()
        self.page_explain = ExplainSettingsPage()
        self.describe = DescribeScreen()
        self.exchange = ExchangeScreen()
        self.exchange.package_style = config.package.style
        self.plan_check = PlanCheckScreen()
        self.findings = FindingsScreen()
        self.test = TestScreen()
        self.save = SaveScreen()
        self.done = DoneScreen()
        self.history = HistoryScreen()
        self.run_detail = RunDetailScreen()
        self.busy = BusyScreen()
        self.settings = SettingsScreen()
        self.page_downloads = DownloadsPage()
        self.page_tasks = TasksPage(self.store)
        self.page_global = GlobalPage(self.store)
        self.page_package = PackagePage()
        self.page_checks = ChecksPage()
        for name, screen in [
                ("home", self.home), ("projects", self.projects),
                ("issues", self.issues_list), ("issue", self.issue_detail),
                ("scan-check", self.scan_check),
                ("explain", self.explain),
                ("explain-result", self.explain_result),
                ("set-explain", self.page_explain),
                ("describe", self.describe),
                ("exchange", self.exchange),
                ("plan", self.plan_check), ("findings", self.findings),
                ("test", self.test), ("save", self.save), ("done", self.done),
                ("history", self.history), ("run", self.run_detail),
                ("busy", self.busy), ("settings", self.settings),
                ("set-downloads", self.page_downloads),
                ("set-tasks", self.page_tasks), ("set-global", self.page_global),
                ("set-package", self.page_package),
                ("set-checks", self.page_checks)]:
            self.screens[name] = screen
            self.stack.addWidget(screen)

        self.home.new_change.connect(self._new_change)
        self.home.open_history.connect(self.show_history)
        self.home.open_settings.connect(lambda: self.show_screen("settings"))
        self.home.open_projects.connect(self.show_projects)
        self.home.open_issues.connect(self.show_issues)
        self.home.open_explain.connect(self.show_explain)
        self.home.continue_run.connect(self._continue_run)
        self.home.continue_explain.connect(self._resume_explain)

        self.issues_list.open_issue.connect(self._open_issue)
        self.issues_list.add_issue.connect(self._new_issue)
        self.issues_list.scan.connect(self._start_scan)
        self.issues_list.back.connect(self.show_home)

        self.issue_detail.save.connect(self._save_issue)
        self.issue_detail.repair.connect(self._repair_issue)
        self.issue_detail.discuss_note.connect(self._discuss_send)
        self.issue_detail.close_reason.connect(self._close_issue_with)
        self.issue_detail.open_run.connect(self._open_run)
        self.issue_detail.reopen.connect(self._reopen_issue)
        self.issue_detail.remove.connect(self._remove_issue)
        self.issue_detail.back.connect(self.show_issues)

        self.scan_check.add_selected.connect(self._scan_accept)
        self.scan_check.discard.connect(self._scan_discard)

        self.explain.start.connect(self._start_explain)
        self.explain.back.connect(self.show_home)
        self.explain.import_requested.connect(self._import_explain_files)
        self.explain.open_videos.connect(self._open_last_video_dir)
        self.explain.open_past.connect(self._open_past_explain)
        self.explain.update_past.connect(self._update_explain)
        self.explain_result.open_video.connect(self._open_explain_video)
        self.explain_result.open_folder.connect(self._open_explain_folder)
        self.explain_result.repair.connect(self._repair_explain)
        self.explain_result.done.connect(self.show_home)
        self.page_explain.saved.connect(self._explain_settings_saved)
        self.page_explain.back.connect(lambda: self.show_screen("settings"))

        self.projects.open_project.connect(self._open_project)
        self.projects.remove_project.connect(self._remove_project)
        self.projects.new_project.connect(self._new_project)
        self.projects.add_folder.connect(self._add_folder)
        self.projects.back.connect(self.show_home)

        self.describe.start.connect(self._start_run)
        self.describe.back.connect(self.show_home)
        self.describe.import_requested.connect(self._import_run_files)
        self.describe.pick_issue.connect(self._repair_issue)
        self.describe.repair_selected.connect(self._repair_issues)
        self.describe.open_issues_list.connect(self.show_issues)
        self.describe.open_checks.connect(
            lambda: self._open_settings_page("checks"))

        self.exchange.show_global_button.clicked.connect(self._show_global_text)
        self.exchange.show_prompt_button.clicked.connect(self._show_prompt_text)
        self.exchange.add_attachments.connect(self._add_packet_files)
        self.exchange.remove_attachment.connect(self._remove_packet_file)
        self.exchange.import_attachments.connect(self._import_packet_files)
        self.exchange.export_requested.connect(self._export_packet)
        self.exchange.reply_submitted.connect(self._reply_submitted)
        self.exchange.kept_attachment.connect(self._keep_for_next_packet)
        self.exchange.import_reply.connect(self._import_reply)
        self.exchange.newest_download.connect(self._open_newest_download)
        self.exchange.scan_focus.connect(self._update_scan_focus)

        self.plan_check.accept.connect(self._accept_plan)
        self.plan_check.rescope_note.connect(
            lambda note: self._answer_gate(GateDecision("rescope", note)))
        self.findings.repair.connect(self._start_repair)
        self.findings.rescope_note.connect(
            lambda note: self._answer_gate(GateDecision("rescope", note)))
        self.test.repair.connect(self._start_repair)
        self.test.rescope_note.connect(
            lambda note: self._answer_gate(GateDecision("rescope", note)))
        self.test.retry.connect(lambda: self._answer_gate(GateDecision("retry")))

        self.save.accept.connect(self._accept_and_save)
        self.save.feedback_note.connect(self._feedback_send)
        self.save.discard.connect(self._discard)
        self.save.rerun.connect(self._rerun_checks)

        self.done.new_change.connect(lambda: self._new_change("feature"))
        self.done.open_history.connect(self.show_history)
        self.done.explain_change.connect(self._explain_delivered_change)

        self.history.open_run.connect(self._open_run)
        self.history.back.connect(self.show_home)
        self.run_detail.back.connect(self.show_history)
        self.run_detail.copy_note.connect(self._copy_run_note)
        self.run_detail.go_back_to.connect(self._go_back_to)
        self.run_detail.undo_last.connect(
            lambda: self._go_back_to(self.run_detail.undo_target))

        self.settings.back.connect(self.show_home)
        self.settings.open_page.connect(self._open_settings_page)
        for page in (self.page_downloads, self.page_tasks, self.page_global,
                     self.page_package, self.page_checks):
            page.back.connect(lambda: self.show_screen("settings"))
        self.page_downloads.saved.connect(self._settings_saved)
        self.page_downloads.browse.connect(self._browse_downloads_folder)
        self.page_tasks.saved.connect(self._settings_saved)
        self.page_tasks.add_doc.connect(self._add_document)
        self.page_tasks.remove_doc.connect(self._remove_document)
        self.page_global.saved.connect(self._settings_saved)
        self.page_package.saved.connect(self._package_saved)
        self.page_checks.saved.connect(self._checks_saved)

        self.describe.set_keys(self.describe._start, self.show_home)
        self.exchange.set_keys(self._open_newest_download)
        self.plan_check.set_keys(self.plan_check.accept.emit)
        self.findings.set_keys(self.findings.repair.emit)
        self.test.set_keys(self.test.repair.emit)
        self.save.set_keys(self.save.accept.emit)
        self.explain.set_keys(self.explain._start, self.show_home)
        self.issue_detail.set_keys(self.issue_detail.save.emit,
                                   self.show_issues)
        self.issues_list.set_keys(escape=self.show_home)
        self.history.set_keys(escape=self.show_home)
        self.projects.set_keys(escape=self.show_home)
        self.settings.set_keys(escape=self.show_home)

    def _wire_controller(self) -> None:
        bridge = self.controller.bridge
        bridge.packet_ready.connect(self._packet_ready)
        bridge.plan_ready.connect(self._plan_ready)
        bridge.findings_ready.connect(self._findings_ready)
        bridge.checks_failed.connect(self._checks_failed)
        self.controller.progress_event.connect(self._progress)
        self.controller.run_settled.connect(self._run_settled)
        self.controller.run_error.connect(self._run_failed)
        self.controller.issues_notice.connect(self._issues_notice)

    # ----- theme -----

    def apply_theme(self, name: str, persist: bool = True) -> None:
        palette = theme_module.palette_for(name == "dark")
        application = QApplication.instance()
        if application is not None:
            # Re-polishing every widget takes a visible moment; freeze
            # repaints and show the wait cursor while it happens.
            built = self.centralWidget() is not None
            with busy_pointer():
                if built:
                    self.setUpdatesEnabled(False)
                try:
                    application.setPalette(theme_module.qt_palette(palette))
                    application.setStyleSheet(theme_module.stylesheet(palette))
                finally:
                    if built:
                        self.setUpdatesEnabled(True)
        self._theme = name
        if persist:
            values = load_ui_settings()
            values["theme"] = name
            save_ui_settings(values)
        if hasattr(self, "foot_theme"):
            self._set_theme_symbol(name)
        if hasattr(self, "stage_header"):
            self.stage_header.update()
        self.update()

    def _set_theme_symbol(self, name: str) -> None:
        dark = name == "dark"
        self.foot_theme.setText(text(
            "theme.symbol.light" if dark else "theme.symbol.dark"))
        self.foot_theme.setToolTip(text(
            "theme.to_light" if dark else "theme.to_dark"))

    def toggle_theme(self) -> None:
        self.apply_theme("light" if self._theme == "dark" else "dark")

    # ----- navigation -----

    RUN_FLOW_SCREENS = frozenset(
        {"exchange", "plan", "findings", "test", "save", "done", "busy"})

    def show_screen(self, name: str) -> None:
        self._current_screen = name
        if name != "busy":
            self._busy_timer.stop()
        if name not in self.RUN_FLOW_SCREENS:
            self.stage_header.setVisible(False)
        self._run_head.setVisible(
            self._run_head_on and name in self.RUN_FLOW_SCREENS)
        if name == "exchange" and self.current_handoff is not None:
            step = text("wait.step." + self.current_handoff.task_key)
            self.setWindowTitle(text("app.waiting", step=step))
        else:
            self.setWindowTitle(
                f"{text('app.title')} — {self.store.config.name}")
        self.stack.setCurrentWidget(self.screens[name])

    def show_busy(self, message: str = "") -> None:
        """FR-P3: the busy screen shows only when the work takes long."""
        self.busy.show_message(message or text("working.busy"))
        self._busy_timer.start(600)

    def _busy_now(self) -> None:
        if self.controller.busy:
            self.show_screen("busy")

    def show_home(self) -> None:
        self._set_run_footer(False)
        self._pending_issue_links = []
        runs = self.controller.runs()   # one scan feeds the whole screen
        self.home.set_resumable(self.controller.resumable_run(runs))
        self.home.set_resumable_explain(
            None if self._side is not None
            else resumable_explain(self.store.config))
        issues = self.controller.issues.load()
        self.home.set_issue_count(
            sum(1 for issue in issues if issue.status != CLOSED),
            sum(1 for issue in issues if issue.status == CLOSED))
        self.home.set_momentum(self._momentum_line(runs))
        self.show_screen("home")

    def _momentum_line(self, runs: list | None = None) -> str:
        saved = [item for item in
                 (runs if runs is not None else self.controller.runs())
                 if item.state == "delivered"]
        if not saved:
            return ""
        from datetime import date, timedelta
        last = saved[0].updated_at[:10]
        today = date.today()
        when = (text("when.today") if last == today.isoformat()
                else text("when.yesterday")
                if last == (today - timedelta(days=1)).isoformat() else last)
        if len(saved) == 1:
            return text("home.momentum.one", when=when)
        return text("home.momentum", count=len(saved), when=when)

    def show_history(self) -> None:
        with busy_pointer():
            self.history.show_runs(self.controller.runs())
        self.show_screen("history")

    def show_projects(self) -> None:
        with busy_pointer():
            self.projects.show_rows(project_ops.project_rows())
        self.show_screen("projects")

    # ----- projects -----

    def load_project(self, config: ProjectConfig) -> None:
        """Switch the whole window to another project."""
        if self.controller.busy:
            self.toast(text("projects.busy"))
            return
        same = (Path(config.repository).resolve()
                == Path(self.store.config.repository).resolve())
        if same:
            # The open project again: navigate, never rebuild.
            self.show_home()
            return
        with busy_pointer():
            # The screens stay; only the stores, the controller, and the
            # project-bound texts change. A switch costs milliseconds.
            project_ops.add_existing(config.repository)
            self.store = ConfigStore(config)
            self.controller = Controller(config)
            self.current_handoff = None
            self.current_record = None
            self._in_test = False
            self._side = None
            self._explain = None
            self._open_run_id = ""
            self._wire_controller()
            self.home.set_project(config.name, str(config.repository))
            self.page_tasks.store = self.store
            self.page_global.store = self.store
            self.exchange.package_style = config.package.style
            self.exchange.reply_open = False
            self.stage_header.setVisible(False)
            self.setWindowTitle(f"{text('app.title')} — {config.name}")
            self._set_project_chip(config.name)
            self.show_home()

    def _set_project_chip(self, name: str) -> None:
        self.foot_project.setText(f"{chip_name(name)} ▾")
        tip = text("foot.project.tip")
        self.foot_project.setToolTip(
            f"{name}\n{tip}" if len(name) > 24 else tip)

    def _project_menu(self) -> QMenu:
        """One click on the foot chip lists every project; one more switches."""
        menu = QMenu(self)
        current = Path(self.store.config.repository).resolve()
        rows = project_ops.project_rows()
        if not any(Path(row.path).resolve() == current for row in rows):
            rows.insert(0, project_ops.ProjectRow(
                path=current, name=self.store.config.name,
                status=project_ops.READY))
        for row in rows:
            label = row.name
            if row.status != project_ops.READY:
                label = f"{row.name} — {text('projects.state.' + row.status)}"
            action = menu.addAction(label)
            if Path(row.path).resolve() == current:
                action.setCheckable(True)
                action.setChecked(True)
            else:
                action.triggered.connect(
                    lambda checked=False, value=str(row.path):
                    self._open_project(value))
        menu.addSeparator()
        menu.addAction(text("projects.all"), self.show_projects)
        return menu

    def _pick_project(self) -> None:
        menu = self._project_menu()
        menu.aboutToHide.connect(menu.deleteLater)
        corner = self.foot_project.mapToGlobal(QPoint(0, 0))
        menu.popup(QPoint(corner.x(),
                          corner.y() - menu.sizeHint().height() - 4))

    def _open_project(self, path_value: str) -> None:
        if self.controller.busy:
            self.toast(text("projects.busy"))
            return
        path = Path(path_value)
        status = project_ops.classify(path)
        if status == project_ops.MISSING:
            self.toast(text("projects.missing.open"))
            return
        if status == project_ops.NO_SOURCE_CONTROL:
            self.toast(text("projects.no_git.open"))
            return
        if status == project_ops.NEEDS_SETUP:
            if not self.ask_confirm(text("projects.setup.title"),
                                    text("projects.setup.body"),
                                    text("projects.setup.yes"), text("stop.no")):
                return
            try:
                project_ops.ensure_config(path)
            except MaintainError as exc:
                self.show_error(str(exc))
                return
        try:
            config = project_ops.load_project_config(path)
        except MaintainError as exc:
            self.show_error(str(exc))
            return
        self.load_project(config)
        self.toast(text("projects.opened", name=config.name))

    def _new_project(self) -> None:
        parent = self.pick_directory()
        if not parent:
            return
        name = self.ask_text(text("projects.new.title"), text("projects.new.body"))
        if not name:
            return
        try:
            created = project_ops.create_project_dir(Path(parent), name)
        except MaintainError as exc:
            self.show_error(str(exc))
            return
        self.toast(text("projects.created", name=created.name))
        self.show_projects()

    def _add_folder(self) -> None:
        selected = self.pick_directory()
        if not selected:
            return
        row = project_ops.add_existing(Path(selected))
        self.toast(text("projects.added", name=row.name))
        self.show_projects()

    def _remove_project(self, path_value: str) -> None:
        path = Path(path_value)
        if not self.ask_confirm(text("projects.remove.title", name=path.name),
                                text("projects.remove.body"),
                                text("projects.remove.yes"), text("stop.no")):
            return
        project_ops.remove_project(path)
        self.toast(text("projects.removed", name=path.name))
        self.show_projects()

    # ----- issues -----

    def show_issues(self) -> None:
        self.issues_list.show_issues(
            display_order(self.controller.issues.load()))
        self._set_run_footer(False)
        self.show_screen("issues")

    def _open_issue(self, issue_id: str) -> None:
        try:
            issue = self.controller.issues.get(issue_id)
        except MaintainError as exc:
            self.toast(str(exc))
            return
        run_name = (self._run_name_on_disk(issue.runs[-1]) or ""
                    if issue.runs else "")
        self.issue_detail.load(issue, run_name=run_name)
        self.show_screen("issue")

    def _new_issue(self) -> None:
        self.issue_detail.load(None)
        self.show_screen("issue")

    def _save_issue(self) -> None:
        title = self.issue_detail.title_edit.text().strip()
        if not title:
            self.issue_detail.message.set_state("bad", text("issue.title.empty"))
            return
        detail = self.issue_detail.detail_edit.toPlainText()
        severity = self.issue_detail.severity()
        reference = self.issue_detail.reference_edit.text().strip()
        store = self.controller.issues
        try:
            if self.issue_detail.issue_id:
                issue = store.update(self.issue_detail.issue_id, title=title,
                                     detail=detail, severity=severity,
                                     external_ref=reference)
            else:
                issue = store.add(title=title, detail=detail,
                                  severity=severity, external_ref=reference)
        except MaintainError as exc:
            self.show_error(str(exc))
            return
        run_name = (self._run_name_on_disk(issue.runs[-1]) or ""
                    if issue.runs else "")
        self.issue_detail.load(issue, run_name=run_name)
        self.toast(text("issue.saved"))

    def _close_issue_with(self, issue_id: str, reason: str) -> None:
        self.controller.issues.close(issue_id, reason)
        self.toast(text("issue.closed", id=issue_id))
        self.show_issues()

    def _reopen_issue(self, issue_id: str) -> None:
        self.controller.issues.reopen(issue_id)
        self.toast(text("issue.reopened", id=issue_id))
        self._open_issue(issue_id)

    def _remove_issue(self, issue_id: str) -> None:
        if not self.ask_confirm(text("issue.remove.title", id=issue_id),
                                text("issue.remove.body"),
                                text("issue.remove"), text("stop.no")):
            return
        self.controller.issues.delete(issue_id)
        self.toast(text("issue.removed", id=issue_id))
        self.show_issues()

    def _repair_issue(self, issue_id: str) -> None:
        """FR-I9: picking one issue offers its relatives — the issues
        the scanner grouped with it, or its same-file neighbors."""
        if self.controller.busy:
            self.toast(text("issues.busy"))
            return
        try:
            picked = self.controller.issues.get(issue_id)
        except MaintainError as exc:
            self.toast(str(exc))
            return
        relatives = related_open_issues(self.controller.issues.load(), picked)
        ids = [issue_id]
        if relatives:
            label = picked.group or picked.file
            lines = "\n".join(f"• {item.title}" for item in relatives)
            body = f"{text('issues.related.body', label=label)}\n\n{lines}"
            if self.ask_confirm(
                    text("issues.related.title"), body,
                    text("describe.issues.together",
                         count=len(relatives) + 1),
                    text("issues.related.only")):
                ids += [item.id for item in relatives]
        self._repair_issues(ids)

    def _repair_issues(self, issue_ids: list) -> None:
        """FR-I8: one run repairs several related faults together; the
        delivery closes every one of them."""
        if self.controller.busy:
            self.toast(text("issues.busy"))
            return
        issues = []
        for issue_id in issue_ids:
            try:
                issues.append(self.controller.issues.get(str(issue_id)))
            except MaintainError as exc:
                self.toast(str(exc))
                return
        if not issues:
            return
        self._new_change("issue")
        blocks = []
        for index, issue in enumerate(issues, start=1):
            parts = [issue.title if len(issues) == 1
                     else f"{index}. {issue.title}"]
            if issue.detail.strip():
                parts.append(issue.detail.strip())
            if issue.file:
                parts.append(f"Location: {issue.file}:{issue.line}")
            if issue.snippet.strip():
                parts.append(f"The cited code: {issue.snippet.strip()}")
            blocks.append("\n\n".join(parts))
        header = ("" if len(issues) == 1 else
                  f"Repair these {len(issues)} related faults together.\n\n")
        self.describe.request_edit.setPlainText(header + "\n\n".join(blocks))
        self._pending_issue_links = [issue.id for issue in issues]

    # ----- scan and discuss (run-less packet loops) -----

    def _start_scan(self) -> None:
        if self.controller.busy:
            self.toast(text("issues.busy"))
            return
        focus = ""
        config = self.store.config
        known = [issue for issue in self.controller.issues.load()
                 if issue.status != CLOSED]
        try:
            with busy_pointer():
                request = scan_request(config, focus, known)
                exchange = SideExchange(
                    kind="scan", request=request,
                    directory=side_packet_dir(config, request.run_id))
                packet = build_side_packet(exchange, config, [])
        except MaintainError as exc:
            self.show_error(str(exc))
            return
        self._show_side(exchange, packet)

    def _discuss_send(self, issue_id: str, question: str) -> None:
        if self.controller.busy:
            self.toast(text("issues.busy"))
            return
        try:
            issue = self.controller.issues.get(issue_id)
        except MaintainError as exc:
            self.toast(str(exc))
            return
        self.controller.issues.add_note(issue_id, "you", question)
        config = self.store.config
        try:
            request = discuss_request(config, issue, question)
            exchange = SideExchange(
                kind="discuss", request=request,
                directory=side_packet_dir(config, request.run_id),
                issue_id=issue_id)
            packet = build_side_packet(exchange, config, [])
        except MaintainError as exc:
            self.show_error(str(exc))
            return
        self._show_side(exchange, packet)

    def _show_side(self, exchange: SideExchange, packet,
                   reply_kind: str = "json") -> None:
        handoff = PacketHandoff(request=exchange.request, packet=packet,
                                reply_kind=reply_kind)
        self._side = {"exchange": exchange, "attachments": [],
                      "handoff": handoff}
        self.current_handoff = handoff
        self.stage_header.setVisible(False)
        self._set_run_footer(True, exchange.request.run_id)
        self.foot_history.setVisible(False)
        self.exchange.show_handoff(handoff, [],
                                   documents_count(self.store, exchange.kind),
                                   scan=exchange.kind == "scan")
        self.show_screen("exchange")

    def _end_side(self) -> None:
        self._side = None
        self.exchange.reply_open = False
        self._set_run_footer(False)

    def _side_reply(self, reply) -> None:
        side = self._side
        exchange: SideExchange = side["exchange"]
        if exchange.kind == "explain":
            self._explain_reply(reply.text)
            return
        try:
            response = parse_response(reply.text, exchange.request, "manual")
            if exchange.kind == "scan":
                candidates = scan_candidates(response.content,
                                             self.store.config.repository)
            else:
                parsed = discuss_reply(response.content)
        except MaintainError as exc:
            self.exchange.status.set_state("bad", str(exc))
            return
        if exchange.kind == "scan":
            known = self.controller.issues.known_fingerprints()
            fresh = [item for item in candidates
                     if item.fingerprint not in known]
            dropped = len(candidates) - len(fresh)
            if not fresh:
                self._end_side()
                self.toast(text("scan.check.known.one") if dropped == 1
                           else text("scan.check.known", count=dropped)
                           if dropped else text("scan.added", count=0))
                self.show_issues()
                return
            side["candidates"] = fresh
            side["run_id"] = exchange.request.run_id
            self.scan_check.show_candidates(fresh, dropped)
            self.show_screen("scan-check")
            return
        issue_id = exchange.issue_id
        store = self.controller.issues
        store.add_note(issue_id, "copilot", parsed.reply)
        if parsed.severity:
            issue = store.get(issue_id)
            if (parsed.severity != issue.severity
                    and self.ask_confirm(
                        text("discuss.severity.title"),
                        text("discuss.severity.body",
                             severity=text("issues.severity." + parsed.severity)),
                        text("discuss.severity.yes"), text("stop.no"))):
                store.update(issue_id, severity=parsed.severity,
                             actor="copilot")
        self._end_side()
        self.toast(text("discuss.applied"))
        self._open_issue(issue_id)

    def _scan_accept(self, indexes: list) -> None:
        side = self._side or {}
        candidates = side.get("candidates", [])
        chosen = [candidates[index] for index in indexes
                  if 0 <= index < len(candidates)]
        result = self.controller.issues.capture(
            chosen, source="scan", run_id=side.get("run_id", ""))
        self._end_side()
        self.toast(text("scan.added.one") if len(result.touched) == 1
                   else text("scan.added", count=len(result.touched)))
        self.show_issues()

    def _scan_discard(self) -> None:
        self._end_side()
        self.toast(text("scan.discarded"))
        self.show_issues()

    # ----- explain (run-less packet loop with a local render) -----

    def show_explain(self) -> None:
        self.explain.reset()
        self.explain.audience_edit.setText(
            str(load_ui_settings().get("explain_audience", "")))
        finished = [state for state in load_explain_states(self.store.config)
                    if state.get("status") == EXPLAIN_PASSED
                    and not state.get("superseded_by")][:6]
        memo: dict = {}   # one hash per file across all the rows
        self.explain.set_history(
            [dict(state, stale=is_stale(self.store.config, state, memo))
             for state in finished])
        newest = self._newest_video()
        self._last_video_dir = newest.parent if newest else None
        from datetime import datetime
        when = (datetime.fromtimestamp(newest.stat().st_mtime)
                .strftime("%Y-%m-%d") if newest else "")
        self.explain.set_last_video(when)
        self._set_run_footer(False)
        self.show_screen("explain")

    def _newest_video(self):
        root = Path(self.store.config.runtime_root).parent / "explain"
        try:
            videos = list(root.rglob("*.mp4"))
        except OSError:
            return None
        return max(videos, key=lambda item: item.stat().st_mtime,
                   default=None)

    def _open_last_video_dir(self) -> None:
        if getattr(self, "_last_video_dir", None):
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self._last_video_dir)))

    def _import_explain_files(self) -> None:
        paths = self.pick_files()
        if paths:
            self.explain.add_files([Path(item) for item in paths])

    def _relative_sources(self, files: list) -> list | None:
        repository = self.store.config.repository.resolve()
        values: list[str] = []
        for item in files:
            try:
                values.append(Path(item).resolve()
                              .relative_to(repository).as_posix())
            except ValueError:
                self.toast(text("explain.outside", name=Path(item).name))
                return None
        return values

    def _start_explain(self, files: list, goal: str, audience: str) -> None:
        if self.controller.busy:
            self.toast(text("issues.busy"))
            return
        if self._offer_manim_install(files, goal, audience):
            return
        if self.explain.include_code.isChecked():
            files = self._with_project_code(files)
        sources = self._relative_sources(files)
        if sources is None:
            return
        if audience.strip():
            values = load_ui_settings()
            values["explain_audience"] = audience.strip()
            save_ui_settings(values)
        self._launch_explain(sources, goal, audience)

    def _launch_explain(self, sources: list, goal: str, audience: str,
                        previous_scene: str = "",
                        render_error: str = "",
                        findings: list | None = None,
                        updates: str = "") -> None:
        config = self.store.config
        try:
            request = explain_request(
                config, sources, goal, audience,
                previous_scene=previous_scene, render_error=render_error,
                findings=findings or ())
            exchange = SideExchange(
                kind="explain", request=request,
                directory=explain_dir(config, request.run_id) / "packets")
            packet = build_side_packet(exchange, config, [])
        except MaintainError as exc:
            self.show_error(str(exc))
            return
        self._explain = {"sources": sources, "goal": goal,
                         "audience": audience, "run_id": request.run_id,
                         "source": "", "tail": "", "video": None,
                         "sheet": None, "findings": [], "updates": updates,
                         "dir": explain_dir(config, request.run_id)}
        # FR-X2: the exchange survives the application; a restart shows
        # it on the home screen until a scene reply or a discard.
        save_explain_state(config, request.run_id,
                           status=EXPLAIN_WAITING, goal=goal,
                           audience=audience, sources=list(sources),
                           created_at=utc_now(),
                           packet=str(packet.zip_path), request=request)
        self._show_side(exchange, packet, reply_kind="scene")

    def _explain_reply(self, source: str) -> None:
        state = self._explain or {}
        state["source"] = source
        state["findings"] = quality_findings(source)
        work = Path(state["dir"]) / "render"
        probe_dir = Path(state["dir"]) / "probe"
        self._end_side()
        self.explain_result.show_running(str(work))
        self.explain_result.show_findings(state["findings"])
        self.show_screen("explain-result")
        command = str(load_ui_settings().get("manim_command", "manim"))
        class_name = scene_class_name(source)

        def work_thread() -> None:
            self.explain_render_step.emit("start", text("step.render.probe"))
            geometry = probe_scene(source, probe_dir, class_name)
            self.explain_render_step.emit("complete", "")
            self.explain_render_step.emit("start", text("step.render.video"))
            result = render_scene(source, work, manim_command=command,
                                  scene_class=class_name)
            self.explain_render_step.emit(
                "complete" if result.ok else "failed", "")
            self.explain_render_done.emit((geometry, result))

        threading.Thread(target=work_thread, daemon=True,
                         name="maintain-render").start()

    def _offer_manim_install(self, files: list, goal: str,
                             audience: str) -> bool:
        """Ask to install the video feature before the Copilot round
        starts, so the person never renders into a dead end. True when
        an install is now running (the flow resumes after it)."""
        import sys as sys_module
        from maintain.render import manim_available, resolve_manim_command
        command = resolve_manim_command(
            str(load_ui_settings().get("manim_command", "manim")))
        if manim_available(command):
            return False
        if sys_module.version_info[:2] >= (3, 14):
            return False   # the render message names the version cause
        if not self.ask_confirm(text("explain.install.title"),
                                text("explain.install.body"),
                                text("explain.install.yes"), text("stop.no")):
            return False
        self.busy.show_message(text("explain.installing"))
        self.show_screen("busy")

        def work() -> None:
            self.manim_install_done.emit(
                _pip_install_manim(), (files, goal, audience))

        threading.Thread(target=work, daemon=True,
                         name="maintain-manim-install").start()
        return True

    def _manim_install_done(self, ok: bool, resume) -> None:
        files, goal, audience = resume
        if ok:
            self.toast(text("explain.install.done"))
            self._start_explain(files, goal, audience)
        else:
            self.show_error(text("explain.install.failed"))
            self.show_explain()

    def _explain_render_done(self, outcome) -> None:
        state = self._explain or {}
        geometry, result = outcome
        findings = list(state.get("findings", [])) + list(geometry)
        state["findings"] = findings
        if result.ok:
            state["video"] = result.video
            state["sheet"] = result.sheet
            self.explain_result.show_passed(result.sheet, result.video)
        else:
            state["tail"] = result.output_tail
            self.explain_result.show_failed(result.message, result.output_tail)
        if state.get("run_id"):
            save_explain_state(
                self.store.config, state["run_id"],
                status=EXPLAIN_PASSED if result.ok else EXPLAIN_FAILED,
                video=str(result.video) if result.ok and result.video else "",
                sheet=str(result.sheet) if result.ok and result.sheet else "",
                finished_at=utc_now())
            # FR-X4: a passed update retires the video it replaces.
            if result.ok and state.get("updates"):
                save_explain_state(self.store.config, state["updates"],
                                   superseded_by=state["run_id"])
        self.explain_result.show_findings(findings)
        self._attention()

    def _resume_explain(self, run_id: str) -> None:
        """FR-X2: reopen a waiting exchange with its original packet and
        ids, so a reply Copilot already wrote still validates."""
        state = next((item for item in load_explain_states(self.store.config)
                      if item.get("run_id") == run_id), None)
        if not state:
            return
        request = restore_request(state)
        packet_path = Path(str(state.get("packet", "")))
        if request is None or not packet_path.is_file():
            self.show_error(text("explain.resume.gone"))
            self.show_home()
            return
        config = self.store.config
        import zipfile as zipfile_module
        try:
            with zipfile_module.ZipFile(packet_path) as archive:
                members = tuple(archive.namelist())
        except (OSError, zipfile_module.BadZipFile):
            members = ()
        packet = PacketBuild(
            zip_path=packet_path, sha256="",
            bytes=packet_path.stat().st_size, task_key="explain",
            members=members)
        exchange = SideExchange(
            kind="explain", request=request,
            directory=explain_dir(config, run_id) / "packets")
        self._explain = {"sources": list(state.get("sources", [])),
                         "goal": str(state.get("goal", "")),
                         "audience": str(state.get("audience", "")),
                         "run_id": run_id, "source": "", "tail": "",
                         "video": None, "sheet": None, "findings": [],
                         "dir": explain_dir(config, run_id)}
        self._show_side(exchange, packet, reply_kind="scene")

    def _open_past_explain(self, run_id: str) -> None:
        """FR-X3: a finished explanation opens at its video."""
        state = next((item for item in load_explain_states(self.store.config)
                      if item.get("run_id") == run_id), None)
        video = str(state.get("video", "")) if state else ""
        if video and Path(video).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(video))
            return
        directory = explain_dir(self.store.config, run_id)
        if directory.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def _update_explain(self, run_id: str) -> None:
        """FR-X4: redo a stale explanation over the current files. The
        goal and audience stay; the packet is built fresh."""
        if self.controller.busy:
            self.toast(text("issues.busy"))
            return
        state = next((item for item in load_explain_states(self.store.config)
                      if item.get("run_id") == run_id), None)
        if not state:
            return
        sources = [str(item) for item in state.get("sources", [])]
        self._launch_explain(sources, str(state.get("goal", "")),
                             str(state.get("audience", "")),
                             updates=run_id)

    def _open_explain_video(self) -> None:
        state = self._explain or {}
        if state.get("video"):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(state["video"])))

    def _open_explain_folder(self) -> None:
        state = self._explain or {}
        if state.get("dir"):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(state["dir"])))

    def _repair_explain(self) -> None:
        state = self._explain or {}
        if not state:
            return
        self._launch_explain(state["sources"], state["goal"],
                             state["audience"],
                             previous_scene=state.get("source", ""),
                             render_error=state.get("tail", ""),
                             findings=state.get("findings", []),
                             updates=state.get("updates", ""))

    def _explain_settings_saved(self) -> None:
        values = load_ui_settings()
        values["manim_command"] = self.page_explain.value()
        save_ui_settings(values)
        self._refresh_explain_state()
        self.toast(text("settings.saved"))
        self.show_screen("settings")

    def _set_stage(self, index: int) -> None:
        self.stage_header.setVisible(True)
        self.stage_header.set_stage(index)

    def _run_name_on_disk(self, run_id: str) -> str | None:
        """The stored name — or None when no run record exists, as for
        the run-less side flows (scan, discuss, explain)."""
        import json
        path = self.store.config.runtime_root / run_id / "run.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return str(data.get("name", ""))

    def _set_run_footer(self, active: bool, run_id: str = "") -> None:
        self.foot_history.setVisible(active)
        self.foot_stop.setVisible(active)
        side = self._side is not None
        self.foot_label.setText(
            text("app.footer.side") if active and run_id and side
            else text("app.footer") if active and run_id
            else "Maintain")
        tip = ""
        if active and run_id:
            tip = run_id if side else text("app.footer.tip", run=run_id)
        self.foot_label.setToolTip(tip)
        # FR-N2: the open workflow heads its screens with the run's
        # name; a click edits it. Side flows have no record and no name.
        name = self._run_name_on_disk(run_id) if active and run_id else None
        pending = getattr(self, "_rename_pending", None)
        if pending and pending[0] == run_id:
            name = pending[1]
        self._foot_run_id = run_id if name is not None else ""
        self._run_head_on = name is not None
        self.run_name.setText(
            chip_name(name, 42) if name else text("foot.name.unset"))
        self._run_head.setVisible(
            self._run_head_on
            and self._current_screen in self.RUN_FLOW_SCREENS)

    # ----- run start and resume -----

    def _new_change(self, mode: str) -> None:
        self._pending_issue_links = []
        self.describe.reset(mode)
        self.describe.set_recent(
            list(load_ui_settings().get("recent_requests", [])))
        self.describe.set_checks_hint(not any(
            name != "diff-check" for name, _ in self.store.checks()))
        if mode == "issue":
            self.describe.set_issue_choices(display_order(
                [item for item in self.controller.issues.load()
                 if item.status != CLOSED]))
        self.show_screen("describe")

    def _project_code_files(self) -> list[Path]:
        """Every source and test file, for the include-code choice."""
        from maintain.context import project_code_paths
        config = self.store.config
        with busy_pointer():
            return project_code_paths(
                config.repository, config.source_roots + config.test_roots,
                config.exclude_paths, config.max_file_bytes)

    def _with_project_code(self, attachments: list) -> list:
        code = self._project_code_files()
        known = {Path(item).resolve() for item in attachments}
        fresh = [item for item in code if item.resolve() not in known]
        if fresh:
            self.toast(text("code.added.one") if len(fresh) == 1
                       else text("code.added", count=len(fresh)))
        return [*attachments, *fresh]

    def _start_run(self, mode: str, request: str, attachments: list) -> None:
        if self.describe.include_code.isChecked():
            attachments = self._with_project_code(attachments)
        # Only hand-written requests join the recent chips. A composed
        # tracker repair replayed from a chip would capture its whole
        # block as one nonsense described issue.
        hand_written = not self._pending_issue_links
        if mode == "issue" and not self._pending_issue_links:
            # FR-I6: a described fault lands in the tracker and closes
            # with the run that repairs it. The same words again reuse
            # the same tracked issue.
            captured = self.controller.issues.capture(
                [IssueCandidate(title=" ".join(request.split())[:72],
                                detail=request, kind="described")],
                source="described")
            if captured.touched:
                self._pending_issue_links = [captured.touched[0]]
        if self.controller.start_run(mode, request, attachments):
            if hand_written:
                self._remember_request(request)
            self._set_stage(0)
            self.show_busy(text("working.plan"))

    def _continue_run(self, run_id: str) -> None:
        # The run may already be open and waiting in this window — a
        # walk to Settings and back must not turn Continue into a dead
        # click. Return to the screen the run waits on, with the name
        # head, the stage header, and the Stop button restored.
        if (self.controller.busy and self.current_handoff is not None
                and self.current_handoff.request.run_id == run_id):
            self._set_run_footer(True, run_id)
            self._set_stage(STAGE_FOR_TASK.get(
                self.current_handoff.task_key, 0))
            self.show_screen(getattr(self, "_wait_screen", "exchange"))
            return
        summary = next((item for item in self.controller.runs()
                        if item.run_id == run_id), None)
        if summary is not None and summary.state == str(RunState.AWAITING_ACCEPTANCE):
            record = self._load_record(run_id)
            if record is not None:
                self._show_save(record)
                return
        if self.controller.resume(run_id):
            self._set_run_footer(True, run_id)
            self.show_busy()

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

    def _attention(self) -> None:
        """FR-F3: flash the taskbar when input is needed and the window
        is in the background."""
        if not self.isActiveWindow():
            QApplication.alert(self, 0)

    def _packet_ready(self, handoff: PacketHandoff) -> None:
        self.current_handoff = handoff
        self._wait_screen = "exchange"
        for issue_id in self._pending_issue_links:
            self.controller.issues.link_run(issue_id,
                                            handoff.request.run_id)
            self.controller.issues.set_in_work(issue_id)
        self._pending_issue_links = []
        self._set_run_footer(True, handoff.request.run_id)
        self._set_stage(STAGE_FOR_TASK[handoff.task_key])
        self.exchange.show_handoff(handoff, self._packet_names(),
                                   documents_count(self.store,
                                                   handoff.task_key))
        self.show_screen("exchange")
        self._attention()

    def _packet_names(self) -> list[str]:
        if self._side is not None:
            return [Path(item).name for item in self._side["attachments"]]
        return [Path(item).name for item in
                (*self.controller.run_attachments, *self.controller.packet_extras)]

    def _add_packet_files(self, paths: list) -> None:
        if self._side is not None:
            self._side["attachments"].extend(Path(item) for item in paths)
        else:
            self.controller.packet_extras.extend(Path(item) for item in paths)
        self._rebuild_packet()

    def _remove_packet_file(self, index: int) -> None:
        if self._side is not None:
            del self._side["attachments"][index]
            self._rebuild_packet()
            return
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
        with busy_pointer():
            self._rebuild_packet_now()

    def _rebuild_packet_now(self) -> None:
        try:
            if self._side is not None:
                exchange: SideExchange = self._side["exchange"]
                packet = build_side_packet(exchange, self.store.config,
                                           self._side["attachments"])
                handoff = PacketHandoff(request=exchange.request,
                                        packet=packet, reply_kind="json")
                self._side["handoff"] = handoff
                self.current_handoff = handoff
                self.exchange.handoff = handoff
            else:
                self.controller.rebuild_packet(self.current_handoff)
        except MaintainError as exc:
            self.toast(str(exc))
            return
        self.exchange.update_packet(
            self.current_handoff.zip_path, self._packet_names(),
            documents_count(self.store, self.current_handoff.task_key))
        self.toast(text("send.updated"))

    def _export_packet(self) -> None:
        if self.current_handoff is None:
            return
        destination = self.pick_save(self.current_handoff.zip_path.name)
        if destination:
            with busy_pointer():
                shutil.copyfile(self.current_handoff.zip_path, destination)
            self.exchange.mark_exported(Path(destination).name)

    def _import_reply(self) -> None:
        paths = self.pick_files()
        if paths:
            self.exchange.check(path=Path(paths[0]))

    def _open_newest_download(self) -> None:
        """FR-P1: take the reply from the Downloads folder, newest first."""
        if self.current_handoff is None:
            return
        values = load_ui_settings()
        root = Path(str(values.get("downloads_path") or default_downloads()))
        try:
            since = self.current_handoff.zip_path.stat().st_mtime - 5.0
        except OSError:
            since = 0.0
        found = newest_reply(root, since)
        if found is None:
            self.exchange.status.set_state("warn", text("exchange.newest.none"))
            return
        result = check_reply(self.current_handoff, path=found)
        if result.valid:
            self.exchange.status.set_state("busy", text("receive.checking"))
            self._reply_submitted(result.reply)
        elif result.message:
            self.exchange.status.set_state("bad",
                                           f"{found.name}: {result.message}")
        else:
            self.exchange.status.set_state(
                "warn", text("exchange.newest.wrong", name=found.name))

    def _update_scan_focus(self, focus: str) -> None:
        """FR-P7: aim the scan from the exchange screen itself."""
        if self._side is None or self._side["exchange"].kind != "scan":
            return
        config = self.store.config
        known = [issue for issue in self.controller.issues.load()
                 if issue.status != CLOSED]
        try:
            request = scan_request(config, focus, known)
            exchange = SideExchange(
                kind="scan", request=request,
                directory=side_packet_dir(config, request.run_id))
            packet = build_side_packet(exchange, config,
                                       self._side["attachments"])
        except MaintainError as exc:
            self.show_error(str(exc))
            return
        handoff = PacketHandoff(request=request, packet=packet,
                                reply_kind="json")
        self._side.update({"exchange": exchange, "handoff": handoff})
        self.current_handoff = handoff
        self.exchange.handoff = handoff
        self.exchange.update_packet(handoff.zip_path, self._packet_names(),
                                    documents_count(self.store, "scan"))
        self.toast(text("send.updated"))

    def _issues_notice(self, kind: str, count: int, label: str) -> None:
        """FR-P8 + FR-H3: name what the engine closed."""
        if kind == "captured":
            self.toast(text("issues.captured", count=count))
        elif count == 1:
            self.toast(text("issues.closed.one", title=label))
        else:
            self.toast(text("issues.closed.more", title=label,
                            count=count - 1))
        issues = self.controller.issues.load()
        self.home.set_issue_count(
            sum(1 for issue in issues if issue.status != CLOSED),
            sum(1 for issue in issues if issue.status == CLOSED))

    def _reply_submitted(self, reply) -> None:
        if self._side is not None:
            self._side_reply(reply)
            return
        task_key = (self.current_handoff.task_key
                    if self.current_handoff else "")
        self.controller.answer_reply(reply)
        if task_key in {"plan", "build", "repair", "review"}:
            self.toast(text("exchange.accepted." + task_key))
        self.show_busy()

    def _remember_request(self, request: str) -> None:
        values = load_ui_settings()
        recent = [request] + [item for item in values.get(
            "recent_requests", []) if item != request]
        values["recent_requests"] = recent[:5]
        save_ui_settings(values)

    def _keep_for_next_packet(self, paths: list) -> None:
        self.controller.run_attachments.extend(Path(item) for item in paths)

    # ----- bridge: gates -----

    def _plan_ready(self, record: RunRecord, tasks: list) -> None:
        self.current_record = record
        self._wait_screen = "plan"
        self._set_stage(0)
        self.plan_check.show_tasks(tasks)
        self.show_screen("plan")

    def _findings_ready(self, record: RunRecord, findings: list) -> None:
        self.current_record = record
        self._wait_screen = "findings"
        self._set_stage(2)
        self.findings.show_findings(findings, max(1, record.attempt))
        self.show_screen("findings")

    def _checks_failed(self, record: RunRecord, results: list) -> None:
        self.current_record = record
        self._wait_screen = "test"
        self._set_stage(3)
        self.test.show_failed(results)
        self.show_screen("test")
        self._attention()

    def _accept_plan(self) -> None:
        self.toast(text("beat.plan.accepted"))
        self._answer_gate(GateDecision("accept"))

    def _start_repair(self) -> None:
        self.toast(text("beat.repair.starts"))
        self._answer_gate(GateDecision("repair"))

    def _answer_gate(self, decision: GateDecision) -> None:
        self.controller.answer_decision(decision)
        self.show_busy()

    # ----- progress and completion -----

    def _progress(self, phase: str, label_key: str, message: str) -> None:
        self.busy.on_progress(phase, label_key, message)
        if label_key == "CHECK":
            if not self._in_test:
                self._in_test = True
                self.test.reset(self.store.checks())
                self._set_stage(3)
                self.show_screen("test")
            self.test.on_progress(phase, label_key, message)

    def _run_settled(self, record: RunRecord) -> None:
        self.current_record = record
        self._in_test = False
        pending = getattr(self, "_rename_pending", None)
        if pending and pending[0] == record.run_id:
            # The mid-run rename lands now, on settled ground.
            self.controller.set_run_name(record.run_id, pending[1])
            record.name = pending[1]
            self._rename_pending = None
        own_stop = bool(getattr(self, "_name_after_stop", False))
        if own_stop:
            self._name_after_stop = False
            # FR-N1: named work stays named without a question; only an
            # unnamed run asks. The engine wrote its final state before
            # this signal, so the name lands on settled ground.
            if not record.name:
                value = self.ask_line(text("stop.name.title"),
                                      text("stop.name.body"))
                if value:
                    self.controller.set_run_name(record.run_id, value)
                    record.name = value
        if self.controller.busy:
            # A stale settle: its queued signal landed after the next
            # operation began (stop, then start at once). Screens and
            # pending issue links belong to the operation in flight.
            return
        state = RunState(record.state)
        if state in {RunState.AWAITING_ACCEPTANCE, RunState.DELIVERED}:
            self._attention()
        if state is RunState.AWAITING_ACCEPTANCE:
            if self.stack.currentWidget() is self.test:
                self.test.mark_passed()
                QTimer.singleShot(600, lambda: self._show_save(record))
            else:
                self._show_save(record)
        elif state is RunState.DELIVERED:
            self.exchange.reply_open = False
            delivered = sum(1 for item in self.controller.runs()
                            if item.state == "delivered")
            iterations = self._build_rounds(record.run_id)
            issues_closed = sum(
                1 for item in self.controller.issues.load()
                if record.run_id in item.runs and item.status == CLOSED)
            self.done.show_record(
                record, files=self.controller.changed_files(record),
                checks=len(record.evidence.get("tests", {})
                           .get("commands", [])),
                iterations=iterations,
                duration=self._run_duration(record),
                first=delivered == 1,
                note=self._change_note(record, iterations=iterations),
                issues_closed=issues_closed)
            self._set_run_footer(False)
            self._set_stage(5)
            self.stage_header.setVisible(False)
            self.show_screen("done")
        elif state is RunState.CANCELLED:
            self.toast(text("discard.done"))
            self.show_home()
        elif state is RunState.NEEDS_HUMAN:
            # Your own stop is not an error; the engine's third-person
            # halt text ("The person stopped…") stays out of the toast.
            self.toast(text("paused.body") if own_stop
                       else (record.error or text("paused.body")))
            self.show_home()
        else:
            self.show_home()

    def _build_rounds(self, run_id: str) -> int:
        """What a person counts as iterations: one per applied build or
        repair. The raw timeline also logs the plan, the review, and
        the save."""
        rounds = sum(1 for item in self.controller.timeline(run_id)
                     if item.kind in {"build_applied", "repair_applied"})
        return max(rounds, 1)

    def _change_note(self, record: RunRecord,
                     iterations: int | None = None) -> str:
        """FR-G3: a paste-ready status line for one saved change."""
        files = self.controller.changed_files(record)
        checks = len(record.evidence.get("tests", {}).get("commands", []))
        if iterations is None:
            iterations = self._build_rounds(record.run_id)
        request = " ".join(record.request.split())
        return (
            f"Saved: {request}\n"
            f"Files ({len(files)}): {', '.join(files)}\n"
            f"Checks passed: {checks} · Build rounds: {iterations} · "
            f"{self._run_duration(record)}\n"
            f"Branch: {record.branch}\n")

    def _explain_delivered_change(self) -> None:
        """FR-G2: from the win to its video, pre-filled."""
        record = self.current_record
        if record is None:
            return
        repository = self.store.config.repository
        files = [repository / item
                 for item in self.controller.changed_files(record)
                 if (repository / item).is_file()]
        request = " ".join(record.request.split())[:120]
        self.show_explain()
        self.explain.goal_edit.setPlainText(
            text("explain.change.goal", request=request))
        self.explain.add_files(files)

    def _copy_run_note(self) -> None:
        record = (self._load_record(self._open_run_id)
                  if getattr(self, "_open_run_id", "") else None)
        if record is not None:
            QGuiApplication.clipboard().setText(self._change_note(record))
            self.toast(text("done.note.done"))

    def _run_duration(self, record: RunRecord) -> str:
        from datetime import datetime
        try:
            delta = (datetime.fromisoformat(record.updated_at)
                     - datetime.fromisoformat(record.created_at))
        except (TypeError, ValueError):
            return "-"
        seconds = max(0, int(delta.total_seconds()))
        if seconds < 60:
            return f"{seconds} s"
        minutes, _ = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes} min"
        hours, minutes = divmod(minutes, 60)
        return f"{hours} h {minutes:02d} min"

    def _show_save(self, record: RunRecord) -> None:
        self._set_run_footer(True, record.run_id)
        self._set_stage(4)
        changed = self.controller.changed_files(record)
        self.save.show_record(record, changed, self.controller.diff_text(record))
        self.show_screen("save")

    def _run_failed(self, message: str) -> None:
        self._in_test = False
        self.show_error(message)
        if not self.controller.busy:   # a stale signal never steals the screen
            self.show_home()

    # ----- save actions -----

    def _accept_and_save(self) -> None:
        if self.current_record is None:
            return
        if self.controller.accept_and_deliver(self.current_record.run_id):
            self.show_busy()

    def _feedback_send(self, note: str) -> None:
        if self.current_record is None:
            return
        if self.controller.feedback(self.current_record.run_id, note):
            self.show_busy()

    def _discard(self) -> None:
        if self.current_record is None:
            return
        if not self.ask_confirm(text("discard.title"),
                                text("discard.body",
                                     run=self.current_record.run_id),
                                text("discard.yes"), text("discard.no")):
            return
        if self.controller.discard(self.current_record.run_id):
            self.show_busy()

    def _rerun_checks(self) -> None:
        if self.current_record is None:
            return
        if self.controller.rerun_checks(self.current_record.run_id):
            self.show_busy(text("working.checks"))

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
        self.show_screen("run")

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
            self.show_busy()

    # ----- stop -----

    def _stop_run(self) -> None:
        if self._side is not None:
            kind = self._side["exchange"].kind
            issue_id = self._side["exchange"].issue_id
            if kind == "explain" and self._explain:
                save_explain_state(self.store.config,
                                   self._explain["run_id"],
                                   status=EXPLAIN_DISCARDED)
            self._end_side()
            self.toast(text("scan.discarded" if kind == "scan"
                            else "explain.discarded" if kind == "explain"
                            else "discuss.discarded"))
            if kind == "discuss" and issue_id:
                self._open_issue(issue_id)
            elif kind == "explain":
                self.show_home()
            else:
                self.show_issues()
            return
        if not self.ask_confirm(text("stop.title"), text("stop.body"),
                                text("stop.yes"), text("stop.no")):
            return
        # FR-N1: the settled record knows whether a name exists, so the
        # prompt comes after the stop — and only for unnamed work.
        self._name_after_stop = True
        self.controller.stop()

    # ----- settings -----

    def _open_settings_page(self, page: str) -> None:
        if page == "downloads":
            self.page_downloads.load()
        elif page == "tasks":
            self.page_tasks.set_tab("project")
        elif page == "global":
            self.page_global.load()
        elif page == "package":
            self.page_package.load(self.store.config.package.style)
        elif page == "checks":
            self.page_checks.load(self.store.checks())
        elif page == "explain":
            self.page_explain.load(
                str(load_ui_settings().get("manim_command", "manim")))
            self._refresh_explain_state()
        self.show_screen(f"set-{page}")

    def _refresh_explain_state(self) -> None:
        from maintain.render import manim_available, resolve_manim_command
        command = resolve_manim_command(
            str(load_ui_settings().get("manim_command", "manim")))
        self.page_explain.show_state(manim_available(command), command)

    def _settings_saved(self) -> None:
        self._after_config_change()
        self.toast(text("settings.saved"))
        self.show_screen("settings")

    def _browse_downloads_folder(self) -> None:
        selected = self.pick_directory()
        if selected:
            self.page_downloads.downloads_edit.setText(selected)
            self.page_downloads.message.setText("")

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
        failure = ""
        for item in paths:   # the dialog selects many; every one lands
            try:
                self.store.add_document(Path(item), task)
            except MaintainError as exc:
                failure = str(exc)
        self._after_config_change()
        self.page_tasks.refresh()
        if failure:
            self.show_error(failure)

    def _remove_document(self, task, value: str) -> None:
        self.store.remove_document(value, task)
        self._after_config_change()
        self.page_tasks.refresh()

    def _after_config_change(self) -> None:
        self.exchange.package_style = self.store.config.package.style
        # A style change while a packet is open re-shows and re-copies
        # the packet in the new style; unchanged styles are a no-op.
        self.exchange.refresh_style()
        if not self.controller.busy:
            self.controller = Controller(self.store.config)
            self._wire_controller()
        else:
            self.controller.config = self.store.config

    def _show_global_text(self) -> None:
        from .screens import read_global
        self.show_text_dialog("GLOBAL.md", read_global(self.store))

    def _show_prompt_text(self) -> None:
        if self.current_handoff is None:
            return
        self.show_text_dialog("TASK.md", self.current_handoff.request.instructions)

    def show_text_dialog(self, title: str, content: str) -> None:
        from PySide6.QtWidgets import QDialog, QPlainTextEdit, QVBoxLayout
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(520, 420)
        column = QVBoxLayout(dialog)
        view = QPlainTextEdit()
        view.setObjectName("Code")
        view.setReadOnly(True)
        view.setPlainText(content)
        column.addWidget(view)
        dialog.exec()

    # ----- dialogs (overridable in tests) -----

    def ask_note(self, title: str, body: str,
                 allow_empty: bool = False) -> str | None:
        value, accepted = QInputDialog.getMultiLineText(self, title, body)
        value = value.strip()
        if not accepted:
            return None
        return value if value or allow_empty else None

    def ask_line(self, title: str, body: str,
                 value: str = "") -> str | None:
        """One short line of text, or None for cancel and empty alike."""
        entered, accepted = QInputDialog.getText(self, title, body,
                                                 text=value)
        entered = entered.strip()
        return entered if accepted and entered else None

    def _rename_run(self, run_id: str) -> None:
        """FR-N2: the name is editable right where it shows, on the open
        workflow. While the engine is busy the write waits for the
        settle, so it never races the engine's own run.json writes."""
        if not run_id:
            return
        stored = self._run_name_on_disk(run_id)
        if stored is None:
            return
        pending = getattr(self, "_rename_pending", None)
        current = (pending[1] if pending and pending[0] == run_id
                   else stored)
        value = self.ask_line(text("stop.name.title"),
                              text("stop.name.body"), value=current)
        if not value or value == current:
            return
        if self.controller.busy:
            self._rename_pending = (run_id, value)
        else:
            self.controller.set_run_name(run_id, value)
        self._set_run_footer(True, run_id)

    def ask_choice(self, title: str, body: str,
                   options: list[str]) -> str | None:
        value, accepted = QInputDialog.getItem(self, title, body, options,
                                               0, False)
        return value if accepted and value else None

    def ask_confirm(self, title: str, body: str, yes: str, no: str) -> bool:
        # The callers word both choices ("Repair 2 together" / "Only
        # this one"); stock Yes/No buttons would drop that meaning.
        box = QMessageBox(QMessageBox.Icon.Question, title, body,
                          parent=self)
        accept = box.addButton(yes, QMessageBox.ButtonRole.YesRole)
        box.addButton(no, QMessageBox.ButtonRole.NoRole)
        box.setDefaultButton(accept)
        box.exec()
        return box.clickedButton() is accept

    def pick_files(self) -> list[str]:
        paths, _ = QFileDialog.getOpenFileNames(self, text("describe.import"))
        return list(paths)

    def pick_directory(self) -> str:
        return QFileDialog.getExistingDirectory(self, text("projects.add"))

    def ask_text(self, title: str, body: str) -> str | None:
        value, accepted = QInputDialog.getText(self, title, body)
        value = value.strip()
        return value if accepted and value else None

    def pick_save(self, name: str) -> str:
        path, _ = QFileDialog.getSaveFileName(self, text("send.export"), name)
        return path

    def show_error(self, message: str) -> None:
        QMessageBox.warning(self, text("error.title"), message)

    def toast(self, message: str) -> None:
        self._toasts.push(message)

    def _import_run_files(self) -> None:
        paths = self.pick_files()
        if paths:
            self.describe.add_files([Path(item) for item in paths])
