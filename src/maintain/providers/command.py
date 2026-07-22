"""Structured command and file-exchange providers."""

from __future__ import annotations

import json
import hashlib
import subprocess
import time
from dataclasses import asdict
from pathlib import Path

from maintain.errors import ProviderError
from maintain.models import ProviderCapabilities, ProviderRequest, ProviderResponse

from .base import Provider


def parse_response(raw: str, request: ProviderRequest, provider: str) -> ProviderResponse:
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise TypeError("The response envelope must be an object.")
        value.pop("provider", None)
        response = ProviderResponse(provider=provider, **value)
        if (not isinstance(response.content, dict)
                or not isinstance(response.conversation_id, str)):
            raise TypeError("The response content and conversation ID have invalid types.")
    except (json.JSONDecodeError, TypeError, AttributeError) as exc:
        raise ProviderError(f"{provider} returned an invalid response envelope.") from exc
    if (response.schema_version != request.schema_version or response.run_id != request.run_id
            or response.task_id != request.task_id or response.role != request.role):
        raise ProviderError(f"{provider} returned an envelope for a different task.")
    return response


class CommandProvider(Provider):
    capabilities = ProviderCapabilities()

    def __init__(self, name: str, argv: list[str], timeout_seconds: int = 900) -> None:
        self.name, self.argv, self.timeout_seconds = name, argv, timeout_seconds

    def exchange(self, request: ProviderRequest) -> ProviderResponse:
        try:
            result = subprocess.run(self.argv, input=json.dumps(asdict(request)), text=True,
                                    capture_output=True, timeout=self.timeout_seconds,
                                    shell=False, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProviderError(f"{self.name} could not run: {exc}") from exc
        if result.returncode:
            raise ProviderError(f"{self.name} failed: {result.stderr[-600:]}")
        return parse_response(result.stdout, request, self.name)


class FileExchangeProvider(Provider):
    capabilities = ProviderCapabilities()

    def __init__(self, name: str, exchange_dir: Path, timeout_seconds: int = 3600) -> None:
        self.name, self.exchange_dir, self.timeout_seconds = name, exchange_dir, timeout_seconds

    def exchange(self, request: ProviderRequest) -> ProviderResponse:
        self.exchange_dir.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(asdict(request), sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(serialized.encode()).hexdigest()[:10]
        stem = f"{request.run_id}-{request.task_id}-{request.role}-{digest}"
        outbound, inbound = self.exchange_dir / f"{stem}.request.json", self.exchange_dir / f"{stem}.response.json"
        temporary = outbound.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(request), indent=2), encoding="utf-8")
        temporary.replace(outbound)
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            if inbound.is_file():
                return parse_response(inbound.read_text(encoding="utf-8"), request, self.name)
            time.sleep(0.25)
        raise ProviderError(f"Timed out while waiting for {inbound.name}.")
