"""Trusted verification command execution."""

from __future__ import annotations

import hashlib
import locale
import ntpath
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping

from .config import CommandSpec


_POLL_SECONDS = 0.05
_TERMINATE_GRACE_SECONDS = 0.35
_DRAIN_SECONDS = 1.0

# Verification runs with a deliberately bounded environment. These values are
# needed to find normal developer tools and their per-user configuration on
# Windows; omitting them makes commands such as npm.cmd, dotnet, and PowerShell
# behave differently under Maintain than in the user's terminal.
_ENVIRONMENT_KEYS = {
    "APPDATA",
    "COMSPEC",
    "HOME",
    "HOMEDRIVE",
    "HOMEPATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOCALAPPDATA",
    "PATH",
    "PATHEXT",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "PROGRAMW6432",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
    "WINDIR",
}


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


@dataclass(frozen=True)
class _LaunchPlan:
    """The resolved command and the lower-level arguments passed to Popen."""

    argv: tuple[str, ...]
    popen_args: tuple[str, ...] | str
    executable: str | None = None


def _verification_environment(
        source: Mapping[str, str] | None = None, *, platform: str | None = None) -> dict[str, str]:
    """Return the small, cross-platform environment allowed for verification."""
    source = os.environ if source is None else source
    windows = (platform or sys.platform) == "win32"
    environment: dict[str, str] = {}
    for key, value in source.items():
        normalized = key.upper() if windows else key
        if normalized in _ENVIRONMENT_KEYS:
            environment[normalized] = value
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def _project_python(
        repository: Path | None, *, platform: str | None = None,
        fallback: str | os.PathLike[str] | None = None) -> str:
    """Prefer a conventional project-local Python over Maintain's own runtime."""
    shown_fallback = str(fallback or sys.executable)
    if repository is None:
        return shown_fallback
    root = Path(repository).expanduser().resolve()
    windows = (platform or sys.platform) == "win32"
    relative_candidates = (
        (".venv/Scripts/python.exe", "venv/Scripts/python.exe", "env/Scripts/python.exe")
        if windows else
        (".venv/bin/python", "venv/bin/python", "env/bin/python")
    )
    for relative in relative_candidates:
        # Keep the venv entry-point path itself. On POSIX it is commonly a
        # symlink to the base interpreter; resolving it would discard the
        # virtual-environment semantics Python derives from that path.
        candidate = root / relative
        if candidate.is_file() and (windows or os.access(candidate, os.X_OK)):
            return str(candidate)
    return shown_fallback


def _quote_windows_batch_argument(value: str) -> str:
    """Quote one batch-file argument, including spaces and cmd metacharacters."""
    # This is the Windows argv quoting algorithm, with quoting forced even when
    # the value has no whitespace. Forced quotes keep cmd metacharacters such as
    # &, |, <, >, and parentheses inside a single literal argument.
    rendered = ['"']
    backslashes = 0
    for character in value:
        if character == "\\":
            backslashes += 1
            continue
        if character == '"':
            rendered.append("\\" * (backslashes * 2 + 1))
            rendered.append('"')
            backslashes = 0
            continue
        if backslashes:
            rendered.append("\\" * backslashes)
            backslashes = 0
        rendered.append(character)
    if backslashes:
        rendered.append("\\" * (backslashes * 2))
    rendered.append('"')
    return "".join(rendered)


def _windows_batch_command(comspec: str, argv: tuple[str, ...]) -> str:
    """Build the canonical ``cmd /d /v:off /s /c`` batch invocation."""
    prefix = subprocess.list2cmdline([comspec, "/d", "/v:off", "/s", "/c"])
    command = " ".join(_quote_windows_batch_argument(item) for item in argv)
    # /s removes exactly these outer quotes and leaves the quoted command and
    # arguments intact. Passing a string avoids a second list2cmdline pass.
    return f'{prefix} "{command}"'


def _prepare_command(
        argv: tuple[str, ...], environment: Mapping[str, str], *,
        platform: str | None = None,
        cwd: Path | str | None = None,
        resolver: Callable[..., str | None] = shutil.which) -> _LaunchPlan:
    """Resolve argv[0] once and adapt Windows batch files explicitly."""
    if not argv:
        raise ValueError("Verification command argv cannot be empty.")
    current_platform = platform or sys.platform
    path_module = ntpath if current_platform == "win32" else os.path
    command = argv[0]
    directory = path_module.dirname(command)
    shown_cwd = os.fspath(cwd) if cwd is not None else None
    if shown_cwd is not None and directory and not path_module.isabs(command):
        lookup = path_module.normpath(path_module.join(shown_cwd, command))
    else:
        lookup = command

    # Windows searches the child's current directory for a bare command, but
    # Python cannot reliably combine cwd and executable lookup there. Resolve
    # the worktree candidate ourselves, including PATHEXT (.CMD/.BAT), before
    # consulting PATH.
    resolved: str | None = None
    if current_platform == "win32" and shown_cwd is not None and not directory:
        local = path_module.join(shown_cwd, command)
        resolved = resolver(local, path=environment.get("PATH"))
    resolved = resolved or resolver(lookup, path=environment.get("PATH")) or lookup
    resolved_argv = (str(resolved), *argv[1:])
    if current_platform != "win32" or ntpath.splitext(str(resolved))[1].casefold() not in {
            ".bat", ".cmd"}:
        return _LaunchPlan(resolved_argv, resolved_argv)
    system_root = environment.get("SYSTEMROOT") or environment.get("WINDIR")
    comspec = environment.get("COMSPEC") or (
        ntpath.join(system_root, "System32", "cmd.exe") if system_root else "cmd.exe")
    return _LaunchPlan(
        resolved_argv,
        _windows_batch_command(comspec, resolved_argv),
        executable=comspec,
    )


class _WindowsJob:
    """Best-effort Windows Job Object that kills descendants when closed."""

    def __init__(self, handle: object, close_handle: Callable[[object], object]) -> None:
        self._handle = handle
        self._close_handle = close_handle

    @classmethod
    def attach(cls, process: subprocess.Popen) -> "_WindowsJob | None":
        if os.name != "nt":
            return None
        try:
            import ctypes
            from ctypes import wintypes

            class _BasicLimitInformation(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class _IoCounters(ctypes.Structure):
                _fields_ = [
                    ("ReadOperationCount", ctypes.c_uint64),
                    ("WriteOperationCount", ctypes.c_uint64),
                    ("OtherOperationCount", ctypes.c_uint64),
                    ("ReadTransferCount", ctypes.c_uint64),
                    ("WriteTransferCount", ctypes.c_uint64),
                    ("OtherTransferCount", ctypes.c_uint64),
                ]

            class _ExtendedLimitInformation(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", _BasicLimitInformation),
                    ("IoInfo", _IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateJobObjectW.restype = wintypes.HANDLE
            kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
            kernel32.SetInformationJobObject.restype = wintypes.BOOL
            kernel32.SetInformationJobObject.argtypes = (
                wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
            kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
            kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

            handle = kernel32.CreateJobObjectW(None, None)
            if not handle:
                return None
            information = _ExtendedLimitInformation()
            information.BasicLimitInformation.LimitFlags = 0x00002000
            configured = kernel32.SetInformationJobObject(
                handle, 9, ctypes.byref(information), ctypes.sizeof(information))
            process_handle = wintypes.HANDLE(getattr(process, "_handle"))
            if not configured or not kernel32.AssignProcessToJobObject(handle, process_handle):
                kernel32.CloseHandle(handle)
                return None
            return cls(handle, kernel32.CloseHandle)
        except (AttributeError, OSError, TypeError, ValueError):
            return None

    def close(self) -> None:
        if self._handle is not None:
            self._close_handle(self._handle)
            self._handle = None


def _taskkill_tree(process: subprocess.Popen, environment: Mapping[str, str]) -> None:
    system_root = environment.get("SYSTEMROOT") or environment.get("WINDIR")
    executable = (ntpath.join(system_root, "System32", "taskkill.exe")
                  if system_root else "taskkill.exe")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.run(
            [executable, "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            timeout=2, creationflags=flags,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _terminate_process_tree(
        process: subprocess.Popen, environment: Mapping[str, str],
        windows_job: _WindowsJob | None) -> None:
    """Stop a verification process and all descendants with bounded waits."""
    if os.name == "nt":
        if windows_job is not None:
            windows_job.close()
        else:
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT)
            except (AttributeError, OSError, ValueError):
                pass
            try:
                process.wait(timeout=_TERMINATE_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                _taskkill_tree(process, environment)
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
        return

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.wait(timeout=_TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    # The direct process can exit before descendants. Always address the group.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass


def _output_text(value: bytes | str | None, encoding: str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode(encoding or locale.getpreferredencoding(False), errors="replace")


def _bounded_communicate(process: subprocess.Popen) -> tuple[str, str]:
    """Drain output without letting inherited descendant pipes block forever."""
    try:
        stdout, stderr = process.communicate(timeout=_DRAIN_SECONDS)
        return _output_text(stdout, process.encoding), _output_text(stderr, process.encoding)
    except subprocess.TimeoutExpired as exc:
        stdout = _output_text(exc.output, process.encoding)
        stderr = _output_text(exc.stderr, process.encoding)
        for stream in (process.stdout, process.stderr, process.stdin):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        try:
            process.wait(timeout=_TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        return stdout, stderr


class CommandRunner:
    def __init__(
            self, max_output_bytes: int = 5_000_000, *,
            source_repository: Path | None = None,
            fallback_python: str | os.PathLike[str] | None = None) -> None:
        self.max_output_bytes = max_output_bytes
        self.python_executable = _project_python(
            source_repository, fallback=fallback_python or sys.executable)

    def run(self, spec: CommandSpec, worktree: Path,
            cancel_event: threading.Event | None = None) -> CommandResult:
        started = time.monotonic()
        environment = _verification_environment()
        worktree = worktree.resolve()
        working_directory = (worktree / spec.working_directory).resolve()
        try:
            working_directory.relative_to(worktree)
        except ValueError:
            return CommandResult(
                spec.name,
                spec.argv,
                127,
                "",
                "Verification working directory escapes the isolated workspace.",
                round(time.monotonic() - started, 3),
                spec.matlab,
            )
        argv = tuple(self.python_executable if item == "{python}" else
                     str(worktree) if item == "{repository}" else item for item in spec.argv)
        process: subprocess.Popen | None = None
        windows_job: _WindowsJob | None = None
        try:
            launch = _prepare_command(argv, environment, cwd=working_directory)
            argv = launch.argv
            popen_options: dict[str, object] = {}
            if os.name == "nt":
                popen_options["creationflags"] = getattr(
                    subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            else:
                popen_options["start_new_session"] = True
            process = subprocess.Popen(
                launch.popen_args, executable=launch.executable,
                cwd=working_directory, env=environment, text=True, errors="replace",
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                **popen_options,
            )
            windows_job = _WindowsJob.attach(process)
            deadline = started + spec.timeout_seconds
            while True:
                try:
                    stdout, stderr = process.communicate(timeout=_POLL_SECONDS)
                    code = int(process.returncode or 0)
                    break
                except subprocess.TimeoutExpired:
                    cancelled = cancel_event is not None and cancel_event.is_set()
                    timed_out = time.monotonic() >= deadline
                    if not cancelled and not timed_out:
                        continue
                    _terminate_process_tree(process, environment, windows_job)
                    stdout, stderr = _bounded_communicate(process)
                    if cancelled:
                        code = 130
                        stderr = (stderr + "\nCommand was cancelled.").lstrip()
                    else:
                        code = 124
                        stderr = (stderr + "\nCommand timed out.").lstrip()
                    break
        except OSError as exc:
            code, stdout, stderr = 127, "", str(exc)
        except BaseException:
            if process is not None:
                _terminate_process_tree(process, environment, windows_job)
                _bounded_communicate(process)
            raise
        finally:
            if windows_job is not None:
                windows_job.close()
        output_bytes = len(stdout.encode()) + len(stderr.encode())
        if output_bytes > self.max_output_bytes:
            allowance = max(0, self.max_output_bytes // 2)
            stdout = stdout.encode()[:allowance].decode(errors="replace")
            stderr = stderr.encode()[:allowance].decode(errors="replace")
            stderr += "\nCommand output exceeded the configured limit."
            code = 125
        fingerprint = hashlib.sha256(
            "\n".join(f"{key}={environment[key]}" for key in sorted(environment)).encode()
        ).hexdigest()
        output_hash = hashlib.sha256((stdout + "\0" + stderr).encode()).hexdigest()
        return CommandResult(spec.name, argv, code, stdout, stderr,
                             round(time.monotonic() - started, 3), spec.matlab,
                             fingerprint, output_hash)
