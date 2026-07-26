from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from maintain.errors import ProviderError
from maintain.models import ProviderRequest
from maintain.providers import command as command_module
from maintain.providers.command import CommandProvider, FileExchangeProvider


def test_command_provider_preflight_accepts_an_available_executable() -> None:
    provider = CommandProvider("helper", [sys.executable, "--version"])

    provider.preflight()


def test_command_provider_preflight_rejects_a_missing_executable() -> None:
    provider = CommandProvider(
        "helper", ["maintain-command-that-does-not-exist-2d71bdb2"])

    with pytest.raises(
            ProviderError,
            match="helper command executable is unavailable"):
        provider.preflight()


def test_file_exchange_preflight_creates_and_cleans_its_probe(
        tmp_path: Path) -> None:
    exchange_dir = tmp_path / "external-exchange"
    provider = FileExchangeProvider("handoff", exchange_dir)

    provider.preflight()

    assert exchange_dir.is_dir()
    assert list(exchange_dir.iterdir()) == []


def test_file_exchange_preflight_rejects_an_unwritable_directory(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def deny_probe(*_args, **_kwargs):
        raise PermissionError("write access denied")

    monkeypatch.setattr(command_module.tempfile, "NamedTemporaryFile", deny_probe)
    provider = FileExchangeProvider("handoff", tmp_path / "external-exchange")

    with pytest.raises(
            ProviderError,
            match="handoff exchange directory is not writable"):
        provider.preflight()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows cmd.exe")
def test_command_provider_executes_a_windows_batch_shim(tmp_path: Path) -> None:
    script = tmp_path / "assistant shim.cmd"
    script.write_text(
        "@echo off\r\n"
        'if not "%~1"=="value with spaces & meta" exit /b 31\r\n'
        'echo {"schema_version":1,"run_id":"run-1","task_id":"task-1",'
        '"role":"scope","content":{"ok":true},"conversation_id":""}\r\n',
        encoding="ascii",
    )
    provider = CommandProvider(
        "batch-helper", [str(script), "value with spaces & meta"])
    request = ProviderRequest(
        schema_version=1,
        run_id="run-1",
        task_id="task-1",
        role="scope",
        instructions="",
        payload={},
    )

    provider.preflight()
    response = provider.exchange(request)

    assert response.content == {"ok": True}
