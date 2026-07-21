"""Trusted verification command execution."""

from __future__ import annotations

import os
import hashlib
import subprocess
import threading
import time
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import CommandSpec


@dataclass(frozen=True)
class CommandResult:
    name: str
    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    matlab: bool
    environment_fingerprint: str = ""
    output_sha256: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class CommandRunner:
    def __init__(self, max_output_bytes: int = 5_000_000) -> None:
        self.max_output_bytes = max_output_bytes

    def run(self, spec: CommandSpec, worktree: Path,
            cancel_event: threading.Event | None = None) -> CommandResult:
        started = time.monotonic()
        env = {key: value for key, value in os.environ.items()
               if key in {"PATH", "SYSTEMROOT", "TMPDIR", "TEMP", "LANG", "LC_ALL"}}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            argv = tuple(sys.executable if item == "{python}" else
                         str(worktree) if item == "{repository}" else item for item in spec.argv)
            process = subprocess.Popen(list(argv), cwd=worktree, env=env, text=True,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       shell=False)
            deadline = started + spec.timeout_seconds
            while True:
                try:
                    stdout, stderr = process.communicate(timeout=0.05)
                    code = process.returncode
                    break
                except subprocess.TimeoutExpired:
                    if cancel_event is not None and cancel_event.is_set():
                        process.terminate()
                        try:
                            stdout, stderr = process.communicate(timeout=1)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            stdout, stderr = process.communicate()
                        code = 130
                        stderr = (stderr + "\nCommand was cancelled.").lstrip()
                        break
                    if time.monotonic() >= deadline:
                        process.kill()
                        stdout, stderr = process.communicate()
                        code = 124
                        stderr = (stderr + "\nCommand timed out.").lstrip()
                        break
        except OSError as exc:
            code, stdout, stderr = 127, "", str(exc)
        except BaseException:
            if "process" in locals() and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
            raise
        output_bytes = len(stdout.encode()) + len(stderr.encode())
        if output_bytes > self.max_output_bytes:
            allowance = max(0, self.max_output_bytes // 2)
            stdout = stdout.encode()[:allowance].decode(errors="replace")
            stderr = stderr.encode()[:allowance].decode(errors="replace")
            stderr += "\nCommand output exceeded the configured limit."
            code = 125
        fingerprint = hashlib.sha256("\n".join(sorted(env)).encode()).hexdigest()
        output_hash = hashlib.sha256((stdout + "\0" + stderr).encode()).hexdigest()
        return CommandResult(spec.name, argv, code, stdout, stderr,
                             round(time.monotonic() - started, 3), spec.matlab,
                             fingerprint, output_hash)
