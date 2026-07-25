from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from maintain.config import CommandSpec
from maintain.errors import RecoveryError
from maintain.locking import FileLock
from maintain.runner import (
    CommandRunner,
    _prepare_command,
    _project_python,
    _quote_windows_batch_argument,
    _verification_environment,
)


class CommandRunnerPlatformTests(unittest.TestCase):
    def test_windows_environment_retains_tool_discovery_and_user_paths(self):
        source = {
            "Path": r"C:\Tools",
            "SystemRoot": r"C:\Windows",
            "ComSpec": r"C:\Windows\System32\cmd.exe",
            "PATHEXT": ".COM;.EXE;.BAT;.CMD",
            "USERPROFILE": r"C:\Users\Example",
            "APPDATA": r"C:\Users\Example\AppData\Roaming",
            "LOCALAPPDATA": r"C:\Users\Example\AppData\Local",
            "SECRET_TOKEN": "must-not-leak",
        }
        environment = _verification_environment(source, platform="win32")
        self.assertEqual(environment["PATH"], r"C:\Tools")
        self.assertEqual(environment["COMSPEC"], r"C:\Windows\System32\cmd.exe")
        self.assertEqual(environment["PATHEXT"], ".COM;.EXE;.BAT;.CMD")
        self.assertEqual(environment["USERPROFILE"], r"C:\Users\Example")
        self.assertEqual(environment["APPDATA"], r"C:\Users\Example\AppData\Roaming")
        self.assertEqual(environment["LOCALAPPDATA"], r"C:\Users\Example\AppData\Local")
        self.assertNotIn("SECRET_TOKEN", environment)

    def test_windows_batch_command_is_resolved_and_cmd_quoted(self):
        environment = {
            "PATH": r"C:\Program Files\nodejs",
            "COMSPEC": r"C:\Windows\System32\cmd.exe",
        }

        def resolve(command, *, path=None):
            self.assertEqual(command, "npm")
            self.assertEqual(path, environment["PATH"])
            return r"C:\Program Files\nodejs\npm.CMD"

        plan = _prepare_command(
            ("npm", "test", r"C:\repo with spaces", "value&other"),
            environment,
            platform="win32",
            resolver=resolve,
        )
        self.assertEqual(plan.argv[0], r"C:\Program Files\nodejs\npm.CMD")
        self.assertEqual(plan.executable, environment["COMSPEC"])
        self.assertEqual(
            plan.popen_args,
            r'C:\Windows\System32\cmd.exe /d /v:off /s /c '
            r'""C:\Program Files\nodejs\npm.CMD" "test" '
            r'"C:\repo with spaces" "value&other""',
        )

    def test_windows_worktree_batch_is_resolved_before_path(self):
        environment = {
            "PATH": r"C:\Tools",
            "COMSPEC": r"C:\Windows\System32\cmd.exe",
        }
        calls = []

        def resolve(command, *, path=None):
            calls.append((command, path))
            if command == r"C:\repo with spaces\gradlew":
                return r"C:\repo with spaces\gradlew.bat"
            return None

        plan = _prepare_command(
            ("gradlew", "test"),
            environment,
            platform="win32",
            cwd=r"C:\repo with spaces",
            resolver=resolve,
        )
        self.assertEqual(
            calls, [(r"C:\repo with spaces\gradlew", environment["PATH"])])
        self.assertEqual(plan.argv[0], r"C:\repo with spaces\gradlew.bat")
        self.assertEqual(plan.executable, environment["COMSPEC"])

    @unittest.skipUnless(os.name == "nt", "requires Windows cmd.exe")
    def test_windows_runner_executes_repository_local_cmd(self):
        with tempfile.TemporaryDirectory(prefix="maintain runner ") as directory:
            root = Path(directory)
            script = root / "verify command.cmd"
            script.write_text(
                "@echo off\r\n"
                'if not "%~1"=="value with spaces & meta" exit /b 31\r\n'
                "echo batch-ok\r\n",
                encoding="utf-8",
            )
            result = CommandRunner().run(
                CommandSpec(
                    "batch", (script.name, "value with spaces & meta"),
                    timeout_seconds=10,
                ),
                root,
            )
        self.assertEqual(
            result.exit_code, 0,
            msg=f"stdout={result.stdout!r}; stderr={result.stderr!r}",
        )
        self.assertEqual(result.stdout.strip(), "batch-ok")
        self.assertTrue(result.argv[0].lower().endswith("verify command.cmd"))

    def test_windows_batch_quoting_preserves_a_trailing_backslash(self):
        self.assertEqual(
            _quote_windows_batch_argument("C:\\repository\\"),
            '"C:\\repository\\\\"',
        )

    def test_native_command_uses_the_resolved_executable(self):
        plan = _prepare_command(
            ("git", "status"), {"PATH": "/tools"}, platform="linux",
            resolver=lambda command, **_: "/tools/git",
        )
        self.assertEqual(plan.argv, ("/tools/git", "status"))
        self.assertEqual(plan.popen_args, plan.argv)
        self.assertIsNone(plan.executable)

    def test_runner_honors_a_safe_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "packages" / "service"
            nested.mkdir(parents=True)
            result = CommandRunner(fallback_python=sys.executable).run(
                CommandSpec(
                    "nested",
                    (
                        "{python}",
                        "-c",
                        (
                            "from pathlib import Path;"
                            "raise SystemExit(0 if Path.cwd().name == 'service' else 1)"
                        ),
                    ),
                    timeout_seconds=10,
                    working_directory="packages/service",
                ),
                root,
            )
        self.assertEqual(
            result.exit_code, 0,
            msg=f"stdout={result.stdout!r}; stderr={result.stderr!r}",
        )

    def test_project_python_is_preferred_over_explicit_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = (Path(".venv/Scripts/python.exe") if sys.platform == "win32"
                        else Path(".venv/bin/python"))
            interpreter = root / relative
            interpreter.parent.mkdir(parents=True)
            interpreter.write_bytes(b"test launcher")
            if sys.platform != "win32":
                interpreter.chmod(0o700)
            runner = CommandRunner(
                source_repository=root, fallback_python="/fallback/python")
            self.assertEqual(
                runner.python_executable, str(root.resolve() / relative))

    def test_windows_project_python_discovery_is_platform_neutral(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            interpreter = root / ".venv" / "Scripts" / "python.exe"
            interpreter.parent.mkdir(parents=True)
            interpreter.write_bytes(b"test launcher")
            self.assertEqual(
                _project_python(root, platform="win32", fallback="fallback.exe"),
                str(root.resolve() / ".venv" / "Scripts" / "python.exe"),
            )

    def test_timeout_kills_descendants_and_returns_promptly(self):
        with tempfile.TemporaryDirectory() as directory:
            command = (
                "import subprocess,sys,time;"
                "subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)']);"
                "time.sleep(20)"
            )
            started = time.monotonic()
            result = CommandRunner(fallback_python=sys.executable).run(
                CommandSpec(
                    "tree-timeout", ("{python}", "-c", command), timeout_seconds=1),
                Path(directory),
            )
            elapsed = time.monotonic() - started
        self.assertEqual(result.exit_code, 124)
        self.assertIn("timed out", result.stderr)
        self.assertLess(elapsed, 3)

    def test_cancellation_uses_the_same_bounded_tree_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            cancelled = threading.Event()
            cancelled.set()
            started = time.monotonic()
            result = CommandRunner(fallback_python=sys.executable).run(
                CommandSpec(
                    "cancel", ("{python}", "-c", "import time; time.sleep(20)"),
                    timeout_seconds=30,
                ),
                Path(directory),
                cancel_event=cancelled,
            )
            elapsed = time.monotonic() - started
        self.assertEqual(result.exit_code, 130)
        self.assertIn("cancelled", result.stderr)
        self.assertLess(elapsed, 2)


class FileLockRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "operation.lock"

    def tearDown(self):
        self.temporary.cleanup()

    def write_owner(self, *, pid: int, host: str | None = None):
        value = {
            "pid": pid,
            "host": host or socket.gethostname(),
            "purpose": "previous operation",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        self.path.write_text(json.dumps(value), encoding="utf-8")
        return value

    def test_acquire_recovers_same_host_dead_pid(self):
        self.write_owner(pid=987654321)
        lock = FileLock(self.path, "replacement")
        with patch("maintain.locking._pid_exists", return_value=False):
            lock.acquire()
        owner = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(owner["pid"], os.getpid())
        self.assertEqual(owner["purpose"], "replacement")
        lock.release()
        self.assertFalse(self.path.exists())

    def test_acquire_preserves_same_host_live_pid(self):
        original = self.write_owner(pid=1234)
        lock = FileLock(self.path, "replacement")
        with patch("maintain.locking._pid_exists", return_value=True):
            with self.assertRaisesRegex(RecoveryError, "Lock is active"):
                lock.acquire()
        self.assertEqual(
            json.loads(self.path.read_text(encoding="utf-8")), original)

    def test_acquire_preserves_foreign_host_lock(self):
        original = self.write_owner(pid=987654321, host="different-host.example")
        lock = FileLock(self.path, "replacement")
        with patch("maintain.locking._pid_exists") as pid_exists:
            with self.assertRaisesRegex(RecoveryError, "Lock is active"):
                lock.acquire()
        pid_exists.assert_not_called()
        self.assertEqual(
            json.loads(self.path.read_text(encoding="utf-8")), original)

    def test_acquire_preserves_unverifiable_lock(self):
        self.path.write_text("not json", encoding="utf-8")
        with self.assertRaisesRegex(RecoveryError, "owner is unknown"):
            FileLock(self.path, "replacement").acquire()
        self.assertEqual(self.path.read_text(encoding="utf-8"), "not json")

    def test_break_stale_refuses_foreign_lock(self):
        original = self.write_owner(pid=987654321, host="different-host.example")
        with self.assertRaisesRegex(RecoveryError, "another host"):
            FileLock(self.path, "replacement").break_stale()
        self.assertEqual(
            json.loads(self.path.read_text(encoding="utf-8")), original)

    def test_release_does_not_remove_a_replaced_lock(self):
        lock = FileLock(self.path, "original")
        lock.acquire()
        replacement = {
            "pid": 4321,
            "host": socket.gethostname(),
            "purpose": "replacement owner with different metadata",
        }
        self.path.write_text(json.dumps(replacement), encoding="utf-8")
        lock.release()
        self.assertTrue(self.path.exists())
        self.assertEqual(
            json.loads(self.path.read_text(encoding="utf-8")), replacement)


if __name__ == "__main__":
    unittest.main()
