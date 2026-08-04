"""The application controller: engine worker threads and run operations."""

from __future__ import annotations

import contextlib
import threading
import traceback
from pathlib import Path
from typing import Callable, Sequence

from PySide6.QtCore import QObject, Signal

from maintain.config import ProjectConfig
from maintain.engine import WorkflowEngine
from maintain.errors import MaintainError
from maintain.history import IterationEvent, RunSummary, list_runs, run_timeline
from maintain.issues import IssueStore
from maintain.models import ProviderRequest, RunRecord, RunState
from maintain.presenter import QuietPresenter
from maintain.provider_factory import build_provider
from maintain.providers.manual_ui import ManualUiProvider, PacketHandoff
from maintain.zip_package import PacketBuild, build_packet

from .bridge import UiBridge


class NotifyingIssueStore(IssueStore):
    """The issue store, with a notice when the engine touches it alone."""

    notice: Callable[[str, int, str], None] | None = None

    def capture(self, candidates, *, source: str, run_id: str = ""):
        result = super().capture(candidates, source=source, run_id=run_id)
        if self.notice and source in {"review", "test"} and result.touched:
            self.notice("captured", len(result.touched), "")
        return result

    def close_for_run(self, run_id: str, keep_fingerprints=frozenset()):
        closed = super().close_for_run(run_id, keep_fingerprints)
        if self.notice and closed:
            self.notice("closed", len(closed), closed[0].title)
        return closed


class QtPresenter(QuietPresenter):
    """Forward engine progress into Qt signals for the status line and Test screen."""

    def __init__(self, emit: Callable[[str, str, str], None]) -> None:
        self._emit = emit

    def complete(self, label: str, message: str) -> None:
        self._emit("complete", label, message)

    def failed(self, label: str, message: str) -> None:
        self._emit("failed", label, message)

    @contextlib.contextmanager
    def progress(self, label: str, message: str):
        self._emit("start", label, message)
        yield


class Controller(QObject):
    """Owns the engine, the bridge, and the one background operation at a time."""

    progress_event = Signal(str, str, str)   # phase, label, message
    run_settled = Signal(object)             # RunRecord after an operation returns
    run_error = Signal(str)                  # unexpected failure text
    busy_changed = Signal(bool)
    issues_notice = Signal(str, int, str)    # kind, count, first title

    def __init__(self, config: ProjectConfig) -> None:
        super().__init__()
        self.config = config
        self.bridge = UiBridge()
        self.run_attachments: list[Path] = []
        self.packet_extras: list[Path] = []
        self._thread: threading.Thread | None = None
        self.issues = NotifyingIssueStore(runtime_root=config.runtime_root,
                                          repository=config.repository)
        self.issues.notice = self.issues_notice.emit
        self.engine = WorkflowEngine(
            config,
            presenter=QtPresenter(self.progress_event.emit),
            provider_builder=self._build_provider,
            gates=self.bridge.gates(),
            issues=self.issues,
        )

    # ----- provider wiring -----

    def _build_provider(self, name: str, provider_config: dict, evidence_dir: Path):
        provider = build_provider(name, provider_config, evidence_dir)
        if isinstance(provider, ManualUiProvider):
            provider.configure(
                bridge=self.bridge.provider_bridge,
                policy=self.config.package,
                repository=self.config.repository,
                config_dir=self.config.path.parent,
                attachment_source=self._attachments_for,
            )
        return provider

    def _attachments_for(self, request: ProviderRequest) -> Sequence[Path]:
        return [*self.run_attachments, *self.packet_extras]

    def rebuild_packet(self, handoff: PacketHandoff) -> PacketBuild:
        """Rebuild the current packet after an attachment change (FR-T6)."""
        return build_packet(
            handoff.request, handoff.zip_path.parent,
            policy=self.config.package,
            repository=self.config.repository,
            config_dir=self.config.path.parent,
            attachments=self._attachments_for(handoff.request),
        )

    # ----- background operations -----

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _spawn(self, operation: Callable[[], RunRecord]) -> bool:
        if self.busy:
            return False

        def work() -> None:
            try:
                record = operation()
            except MaintainError as exc:
                self.run_error.emit(str(exc))
            except Exception:  # noqa: BLE001 - surfaced verbatim, never swallowed
                self.run_error.emit(traceback.format_exc(limit=8))
            else:
                self.run_settled.emit(record)
            finally:
                self.busy_changed.emit(False)

        self._thread = threading.Thread(target=work, daemon=True,
                                        name="maintain-engine")
        self.busy_changed.emit(True)
        self._thread.start()
        return True

    def start_run(self, mode: str, request: str,
                  attachments: Sequence[Path]) -> bool:
        self.run_attachments = [Path(item) for item in attachments]
        self.packet_extras = []
        return self._spawn(lambda: self.engine.start(mode, request))

    def resume(self, run_id: str) -> bool:
        return self._spawn(lambda: self.engine.resume(run_id))

    def continue_run(self, record: RunRecord) -> bool:
        return self._spawn(lambda: self.engine.run(record))

    def accept_and_deliver(self, run_id: str) -> bool:
        def work() -> RunRecord:
            self.engine.accept(run_id)
            return self.engine.deliver(run_id)
        return self._spawn(work)

    def feedback(self, run_id: str, note: str) -> bool:
        return self._spawn(lambda: self.engine.feedback(run_id, note))

    def discard(self, run_id: str) -> bool:
        return self._spawn(lambda: self.engine.cancel(run_id))

    def revert_and_continue(self, run_id: str, sequence: int) -> bool:
        def work() -> RunRecord:
            record = self.engine.revert_to(run_id, sequence)
            return self.engine.run(record)
        return self._spawn(work)

    def rerun_checks(self, run_id: str) -> bool:
        """Run the checks again by going back to the newest check anchor."""
        anchors = [item for item in self.timeline(run_id)
                   if item.kind in {"review_approved", "checks_passed"}
                   and item.can_go_back]
        if not anchors:
            return False
        return self.revert_and_continue(run_id, anchors[-1].sequence)

    # ----- answers from the UI thread -----

    def answer_reply(self, reply) -> None:
        self.packet_extras = []
        self.bridge.answer(reply)

    def answer_decision(self, decision) -> None:
        self.bridge.answer(decision)

    def stop(self) -> None:
        self.bridge.stop()

    def wait_settled(self, timeout: float = 5.0) -> None:
        """Let the engine thread finish before the process goes away."""
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout)

    def set_run_name(self, run_id: str, name: str) -> None:
        """The person's own label on a paused run. Call only after the
        engine settles, so the write never races the engine's own."""
        import json
        path = Path(self.config.runtime_root).expanduser() / run_id / "run.json"
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            record["name"] = name.strip()
            path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        except (OSError, json.JSONDecodeError):
            pass

    # ----- read-only data -----

    def runs(self) -> list[RunSummary]:
        return list_runs(self.config.runtime_root, self.config.repository)

    def timeline(self, run_id: str) -> list[IterationEvent]:
        return run_timeline(self.config.runtime_root, run_id)

    def resumable_run(self, runs: list[RunSummary] | None = None) -> RunSummary | None:
        for summary in (runs if runs is not None else self.runs()):
            if not summary.closed and summary.state != str(RunState.AWAITING_ACCEPTANCE):
                return summary
            if summary.state == str(RunState.AWAITING_ACCEPTANCE):
                return summary
        return None

    def current_branch(self) -> str:
        from maintain.workspace import git
        try:
            return git(self.config.repository, "branch", "--show-current")
        except Exception:   # noqa: BLE001 - display only
            return ""

    def integrate(self, run_id: str, branch: str) -> None:
        """Fast-forward the person's branch onto the delivered commit.

        The engine refuses when the working tree is dirty, when another
        branch is checked out, or when the branch moved since the run
        started. Every refusal reaches the person as words."""
        self.engine.integrate(run_id, branch, confirmed=True)

    def diff_text(self, record: RunRecord) -> str:
        """The verified diff, read from the newest recorded artifact so the
        UI thread never waits on a git subprocess; git is the fallback."""
        try:
            artifacts = (Path(self.config.runtime_root).expanduser()
                         / record.run_id / "artifacts")
            candidates = sorted(artifacts.rglob("actual.diff"),
                                key=lambda path: path.stat().st_mtime)
            if candidates:
                return candidates[-1].read_text(encoding="utf-8")
        except OSError:
            pass
        try:
            return self.engine.workspaces.diff(Path(record.worktree)).text
        except Exception:  # noqa: BLE001 - display-only
            return ""

    def changed_files(self, record: RunRecord) -> list[str]:
        value = record.evidence.get("changed_files", [])
        return [str(item) for item in value] if isinstance(value, (list, tuple)) else []
