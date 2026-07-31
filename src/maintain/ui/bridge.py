"""The thread bridge between the engine and the UI.

The engine runs in a worker thread. Its provider bridge and its gates block
on this object until the person answers in the UI thread. Stop releases any
pending wait and pauses the run through the engine's normal pause path.
"""

from __future__ import annotations

import json
import queue
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from maintain.artifacts.output_zip import zip_artifact_content
from maintain.errors import ProviderError
from maintain.gates import GateDecision, GateStop, WorkflowGates
from maintain.models import RunRecord
from maintain.providers.command import parse_response
from maintain.providers.manual_ui import (ManualExchangeCancelled, ManualReply,
                                          PacketHandoff)

_STOP = object()


@dataclass(frozen=True)
class ReplyCheck:
    """The UI-side pre-validation of a reply the person brought back."""

    reply: ManualReply | None
    message: str
    keep_as_attachment: bool = False

    @property
    def valid(self) -> bool:
        return self.reply is not None


def check_reply(handoff: PacketHandoff, *, text: str = "",
                path: Path | None = None) -> ReplyCheck:
    """Validate a candidate reply without touching the run. FR-V1..FR-V3."""
    request = handoff.request
    if handoff.reply_kind == "zip":
        if path is None:
            return ReplyCheck(None, "This step expects the file maintain-output.zip.")
        try:
            zip_artifact_content(Path(path), request)
        except ProviderError as exc:
            if Path(path).suffix.lower() != ".zip":
                return ReplyCheck(None, "", keep_as_attachment=True)
            return ReplyCheck(None, str(exc))
        except (OSError, ValueError, zipfile.BadZipFile):
            # BadZipFile subclasses Exception alone, so it is named here.
            if Path(path).suffix.lower() == ".zip":
                return ReplyCheck(None, "The tool cannot read this ZIP file.")
            return ReplyCheck(None, "", keep_as_attachment=True)
        return ReplyCheck(ManualReply(kind="zip", path=Path(path)), "")
    source = text
    if path is not None:
        try:
            source = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ReplyCheck(None, "", keep_as_attachment=True)
    if not source.strip():
        return ReplyCheck(None, "First copy the reply in Copilot.")
    if handoff.reply_kind == "scene":
        from maintain.scene_check import checked_scene
        try:
            extracted, _ = checked_scene(source)
        except ProviderError as exc:
            return ReplyCheck(None, str(exc))
        return ReplyCheck(ManualReply(kind="scene", text=extracted), "")
    candidate = _envelope_text(source)
    if candidate is None:
        if path is not None:
            return ReplyCheck(None, "", keep_as_attachment=True)
        return ReplyCheck(None, "This is not the reply. The tool expects the JSON reply.")
    try:
        parse_response(candidate, request, "manual")
    except ProviderError as exc:
        return ReplyCheck(None, str(exc))
    return ReplyCheck(ManualReply(kind="json", text=candidate), "")


_JSON_FENCE = re.compile(r"```(?:json)?[ \t]*\r?\n(.*?)```", re.DOTALL)


def _envelope_text(source: str) -> str | None:
    """The JSON envelope in the reply: bare, or inside one fenced block."""
    try:
        json.loads(source)
        return source
    except json.JSONDecodeError:
        pass
    for match in _JSON_FENCE.finditer(source):
        block = match.group(1)
        try:
            json.loads(block)
            return block
        except json.JSONDecodeError:
            continue
    return None


class UiBridge(QObject):
    """One pending question at a time: a packet exchange or a gate decision."""

    packet_ready = Signal(object)          # PacketHandoff
    plan_ready = Signal(object, list)      # RunRecord, tasks
    findings_ready = Signal(object, list)  # RunRecord, findings
    checks_failed = Signal(object, list)   # RunRecord, results

    def __init__(self) -> None:
        super().__init__()
        self._answers: queue.Queue = queue.Queue()
        self._stopped = False

    # ----- engine thread side -----

    def provider_bridge(self, handoff: PacketHandoff) -> ManualReply:
        self.packet_ready.emit(handoff)
        value = self._wait()
        if value is _STOP:
            raise ManualExchangeCancelled()
        assert isinstance(value, ManualReply)
        return value

    def gates(self) -> WorkflowGates:
        bridge = self

        class BridgeGates(WorkflowGates):
            def plan_review(self, record: RunRecord, tasks: list) -> GateDecision:
                bridge.plan_ready.emit(record, list(tasks))
                return bridge._decision()

            def review_findings(self, record: RunRecord, findings: list) -> GateDecision:
                bridge.findings_ready.emit(record, list(findings))
                return bridge._decision()

            def test_failure(self, record: RunRecord, results: list) -> GateDecision:
                bridge.checks_failed.emit(record, list(results))
                return bridge._decision()

        return BridgeGates()

    def _decision(self) -> GateDecision:
        value = self._wait()
        if value is _STOP:
            raise GateStop()
        assert isinstance(value, GateDecision)
        return value

    def _wait(self) -> Any:
        self._drain()
        self._stopped = False
        return self._answers.get()

    def _drain(self) -> None:
        try:
            while True:
                self._answers.get_nowait()
        except queue.Empty:
            pass

    # ----- UI thread side -----

    def answer(self, value: ManualReply | GateDecision) -> None:
        self._answers.put(value)

    def stop(self) -> None:
        """Release the pending wait. The engine pauses the run for later."""
        self._stopped = True
        self._answers.put(_STOP)
