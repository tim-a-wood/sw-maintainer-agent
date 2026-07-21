"""Cross-platform exclusive file locks with explicit stale recovery."""

from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path

from .errors import RecoveryError
from .models import utc_now


class FileLock:
    def __init__(self, path: Path, purpose: str, wait_seconds: float = 0) -> None:
        self.path, self.purpose, self.acquired = path, purpose, False
        self.wait_seconds = wait_seconds

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        value = {"pid": os.getpid(), "host": socket.gethostname(), "purpose": self.purpose,
                 "created_at": utc_now()}
        deadline = time.monotonic() + self.wait_seconds
        while True:
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                break
            except FileExistsError as exc:
                if time.monotonic() < deadline:
                    time.sleep(0.05)
                    continue
                owner = self.describe()
                raise RecoveryError(f"Lock is active: {self.path} ({owner})") from exc
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        self.acquired = True

    def release(self) -> None:
        if self.acquired:
            try:
                self.path.unlink()
            finally:
                self.acquired = False

    def describe(self) -> str:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return f"pid {value.get('pid')} on {value.get('host')} for {value.get('purpose')}"
        except (OSError, json.JSONDecodeError):
            return "owner is unknown"

    def break_stale(self) -> dict:
        if not self.path.exists():
            raise RecoveryError("The lock does not exist.")
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if value.get("host") == socket.gethostname() and _pid_exists(int(value.get("pid", -1))):
            raise RecoveryError("The lock owner is still running.")
        self.path.unlink()
        return value

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.release()


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
