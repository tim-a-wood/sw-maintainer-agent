"""Human-mediated packet exchange: the person moves the files to Copilot."""

from __future__ import annotations

import dataclasses
import secrets
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from maintain.artifacts.output_zip import (inline_implementation_zip,
                                           zip_artifact_content)
from maintain.config import PackagePolicy
from maintain.errors import ProviderError
from maintain.models import ProviderCapabilities, ProviderRequest, ProviderResponse
from maintain.zip_package import PacketBuild, build_packet

from .base import Provider
from .command import parse_response


@dataclass(frozen=True)
class PacketHandoff:
    """One outbound packet waiting for the person to move it to Copilot."""

    request: ProviderRequest
    packet: PacketBuild
    reply_kind: str  # "json" or "zip"

    @property
    def zip_path(self) -> Path:
        return self.packet.zip_path

    @property
    def task_key(self) -> str:
        return self.packet.task_key


@dataclass(frozen=True)
class ManualReply:
    """The reply the person brought back from Copilot."""

    kind: str  # "json" or "zip"
    text: str = ""
    path: Path | None = None


class ManualExchangeCancelled(Exception):
    """The person stopped the exchange. The run pauses and can resume."""


Bridge = Callable[[PacketHandoff], ManualReply]
AttachmentSource = Callable[[ProviderRequest], Sequence[Path]]


class ManualUiProvider(Provider):
    """Builds one packet per exchange and validates the reply the person returns."""

    capabilities = ProviderCapabilities()

    def __init__(self, name: str, evidence_dir: Path) -> None:
        self.name = name
        self.evidence_dir = Path(evidence_dir)
        self.bridge: Bridge | None = None
        self.policy: PackagePolicy = PackagePolicy()
        self.repository: Path | None = None
        self.config_dir: Path | None = None
        self.attachment_source: AttachmentSource | None = None

    def configure(self, *, bridge: Bridge, policy: PackagePolicy, repository: Path,
                  config_dir: Path, attachment_source: AttachmentSource | None = None) -> None:
        self.bridge = bridge
        self.policy = policy
        self.repository = Path(repository)
        self.config_dir = Path(config_dir)
        self.attachment_source = attachment_source

    def preflight(self) -> None:
        if self.bridge is None or self.repository is None or self.config_dir is None:
            raise ProviderError(
                "This project uses the manual packet exchange. Start maintain-ui to run it.")

    def exchange(self, request: ProviderRequest) -> ProviderResponse:
        self.preflight()
        assert self.bridge is not None
        assert self.repository is not None and self.config_dir is not None
        attachments: Sequence[Path] = ()
        if self.attachment_source is not None:
            attachments = self.attachment_source(request)
        packet = build_packet(
            request, self._packet_dir(),
            policy=self.policy, repository=self.repository, config_dir=self.config_dir,
            attachments=attachments)
        reply_kind = "zip" if request.role == "implement" else "json"
        handoff = PacketHandoff(request=request, packet=packet, reply_kind=reply_kind)
        try:
            reply = self.bridge(handoff)
        except ManualExchangeCancelled as exc:
            raise ProviderError(
                "The person stopped the exchange. Continue the run to try again.") from exc
        return self._validated(request, reply, reply_kind)

    def _validated(self, request: ProviderRequest, reply: ManualReply,
                   reply_kind: str) -> ProviderResponse:
        conversation = f"manual-{request.role}-{request.task_id}-{secrets.token_hex(4)}"
        if reply.kind != reply_kind and not (
                reply_kind == "zip" and reply.kind == "json"):
            expected = ("the Markdown reply or the file maintain-output.zip"
                        if reply_kind == "zip" else "the JSON reply text")
            raise ProviderError(f"This step expects {expected}.")
        if reply.kind == "json" and reply_kind == "zip":
            # The Markdown reply carries the same implementation as the
            # ZIP; synthesize one so both shapes walk one code path.
            response = parse_response(reply.text, request, self.name)
            with tempfile.TemporaryDirectory(
                    prefix="maintain-inline-") as staging:
                synthesized = inline_implementation_zip(
                    response.content, request, Path(staging))
                content = zip_artifact_content(synthesized, request)
                stored = self._store_output_zip(synthesized, request)
            content["_maintain_output_zip"] = stored.name
            return ProviderResponse(
                schema_version=request.schema_version,
                run_id=request.run_id,
                task_id=request.task_id,
                role=request.role,
                content=content,
                provider=self.name,
                conversation_id=conversation,
            )
        if reply.kind == "json":
            response = parse_response(reply.text, request, self.name)
            return dataclasses.replace(response, conversation_id=conversation)
        if reply.path is None or not Path(reply.path).is_file():
            raise ProviderError("The reply file is missing.")
        content = zip_artifact_content(Path(reply.path), request)
        stored = self._store_output_zip(Path(reply.path), request)
        content["_maintain_output_zip"] = stored.name
        return ProviderResponse(
            schema_version=request.schema_version,
            run_id=request.run_id,
            task_id=request.task_id,
            role=request.role,
            content=content,
            provider=self.name,
            conversation_id=conversation,
        )

    def _packet_dir(self) -> Path:
        """A fresh directory per exchange keeps the audit inventory append-only."""
        root = self.evidence_dir / "packets"
        counter = 1
        while (root / f"exchange-{counter:03d}").exists():
            counter += 1
        return root / f"exchange-{counter:03d}"

    def _store_output_zip(self, source: Path, request: ProviderRequest) -> Path:
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        stem = f"manual-output-{request.task_id}"
        candidate = self.evidence_dir / f"{stem}.zip"
        counter = 2
        while candidate.exists():
            candidate = self.evidence_dir / f"{stem}-{counter}.zip"
            counter += 1
        shutil.copyfile(source, candidate)
        return candidate
