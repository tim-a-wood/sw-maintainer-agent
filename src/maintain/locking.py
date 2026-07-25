"""Cross-platform exclusive file locks with conservative stale recovery."""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any

from .errors import RecoveryError
from .models import utc_now


class FileLock:
    def __init__(self, path: Path, purpose: str, wait_seconds: float = 0) -> None:
        self.path, self.purpose, self.acquired = path, purpose, False
        self.wait_seconds = wait_seconds
        self._identity: tuple[int, int, int, int] | None = None

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
                if self._recover_stale_same_host():
                    continue
                if time.monotonic() < deadline:
                    time.sleep(0.05)
                    continue
                owner = self.describe()
                raise RecoveryError(f"Lock is active: {self.path} ({owner})") from exc
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        # Capture identity after the write handle closes. Windows can defer the
        # final last-write timestamp until close, so an in-handle snapshot may
        # otherwise make release mistake its own lock for a replacement.
        self._identity = _stat_identity(self.path.stat())
        self.acquired = True

    def release(self) -> None:
        if self.acquired:
            try:
                if self._identity is not None:
                    self._unlink_if_unchanged(self._identity)
            finally:
                self.acquired = False
                self._identity = None

    def describe(self) -> str:
        try:
            value, _ = self._owner_snapshot()
            return f"pid {value.get('pid')} on {value.get('host')} for {value.get('purpose')}"
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return "owner is unknown"

    def break_stale(self) -> dict[str, Any]:
        if not self.path.exists():
            raise RecoveryError("The lock does not exist.")
        try:
            value, identity = self._owner_snapshot()
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RecoveryError("The lock owner cannot be verified.") from exc
        if not _same_host(value.get("host")):
            raise RecoveryError("A lock from another host cannot be verified as stale.")
        pid = _owner_pid(value)
        if pid is None:
            raise RecoveryError("The lock owner PID is invalid.")
        if _pid_exists(pid):
            raise RecoveryError("The lock owner is still running.")
        if not self._unlink_if_unchanged(identity):
            raise RecoveryError("The lock changed while stale recovery was attempted.")
        return value

    def _recover_stale_same_host(self) -> bool:
        """Remove only a well-formed same-host lock whose process is gone."""
        try:
            value, identity = self._owner_snapshot()
            pid = _owner_pid(value)
            if not _same_host(value.get("host")) or pid is None or _pid_exists(pid):
                return False
            return self._unlink_if_unchanged(identity)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return False

    def _owner_snapshot(self) -> tuple[dict[str, Any], tuple[int, int, int, int]]:
        with self.path.open("rb") as stream:
            data = stream.read()
            stat = os.fstat(stream.fileno())
        value = json.loads(data.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Lock metadata must be an object.")
        return value, _stat_identity(stat)

    def _unlink_if_unchanged(self, identity: tuple[int, int, int, int]) -> bool:
        try:
            if _stat_identity(self.path.stat()) != identity:
                return False
            self.path.unlink()
            return True
        except FileNotFoundError:
            # The owner released the name; acquisition should simply retry.
            return True
        except OSError:
            return False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.release()


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int]:
    return value.st_dev, value.st_ino, value.st_mtime_ns, value.st_size


def _same_host(value: object) -> bool:
    return isinstance(value, str) and value.casefold() == socket.gethostname().casefold()


def _owner_pid(value: dict[str, Any]) -> int | None:
    pid = value.get("pid")
    return pid if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 else None


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _windows_pid_exists(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _windows_pid_exists(pid: int) -> bool:
    """Check a Windows PID without sending it a console-control signal."""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        # Access denied proves that a protected process owns the PID. Invalid
        # parameter is the normal response for a PID that no longer exists.
        return ctypes.get_last_error() == 5
    except (AttributeError, OSError, TypeError, ValueError):
        # When existence cannot be proven safely, retain the lock.
        return True
