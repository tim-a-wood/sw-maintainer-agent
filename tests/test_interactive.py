"""Tests that drive the CLI on a pty, the way a terminal does.

The end-to-end tests in test_maintain.py pipe stdin, which makes isatty()
false — and Maintain deliberately behaves differently then: no colour, no
menus, and setup never blocks on a prompt. That means the interactive paths,
which are most of what a user actually touches, are not exercised there.

These tests allocate a real pty so the interactive branches run. Keystrokes
are written up front; input() reads them a line at a time as the prompts
appear.
"""

import errno
import json
import os
import pty
import re
import select
import shlex
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
MAINTAIN_PY = PROJECT_ROOT / "maintain" / "maintain.py"

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

REPOMIX_SHIM = """#!/usr/bin/env bash
out=""; prev=""
for a in "$@"; do if [ "$prev" = "--output" ]; then out="$a"; fi; prev="$a"; done
if [ -z "$out" ]; then echo "no --output argument" >&2; exit 1; fi
printf '# Repomix stub output\\n' > "$out"
"""

APP_PY = 'def greet():\n    return "Helo, world"\n'
TEST_PY = (
    "from app import greet\n"
    'assert greet() == "Hello, world", f"unexpected greeting: {greet()!r}"\n'
    'print("ok")\n'
)

SCOPE_RESPONSE = """STATUS: SCOPE_COMPLETE

## Understanding

The greeting contains a typo and must read "Hello, world".

## Allowed Files

- app.py

## Proposed Changes

Correct the greeting string returned by `greet()`.

## Acceptance Criteria

- `greet()` returns "Hello, world".

## Risks and Unknowns

- None identified.
"""

PATCH = '''diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def greet():
-    return "Helo, world"
+    return "Hello, world"
'''

IMPL_RESPONSE = (
    "STATUS: IMPLEMENTATION_COMPLETE\n\n"
    "## Summary\n\nCorrect the greeting.\n\n"
    "## Patch\n\n```diff\n" + PATCH.rstrip("\n") + "\n```\n"
)

REVIEW_RESPONSE = """VERDICT: APPROVE

## Findings

1. The change is correct and minimal.

## Acceptance-Criteria Coverage

- The criterion was checked against the diff and the test results.

## Risks

- None.
"""


class Result:
    def __init__(self, code, out):
        self.code = code
        self.out = out
        # A pty wraps at the terminal width, so a phrase can straddle a line
        # break. `flat` is the same text with the wrapping undone.
        self.flat = re.sub(r"\s+", " ", out)

    def has(self, text):
        return re.sub(r"\s+", " ", text) in self.flat


class Terminal:
    """A repository plus a way to run `maintain` in a terminal against it."""

    def __init__(self, tmp_path):
        self.base = tmp_path
        self.repo = tmp_path / "repo"
        self.repo.mkdir()
        self.clip = tmp_path / "clipboard.txt"
        self.clip.write_text("", encoding="utf-8")
        self.attached = tmp_path / "attached.md"

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        shim = bin_dir / "repomix"
        shim.write_text(REPOMIX_SHIM, encoding="utf-8")
        shim.chmod(0o755)

        self.env = dict(os.environ)
        self.env["PATH"] = f"{bin_dir}{os.pathsep}{self.env.get('PATH', '')}"
        self.env["MAINTAIN_CLIPBOARD_CMD"] = f"cat {shlex.quote(str(self.clip))}"
        self.env["MAINTAIN_COPY_FILE_CMD"] = (
            f"cp {{path}} {shlex.quote(str(self.attached))}"
        )
        self.env["MAINTAIN_REGISTRY_PATH"] = str(tmp_path / "projects.json")
        self.env["GIT_CEILING_DIRECTORIES"] = str(tmp_path)

        self.git("init", "-q")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test User")
        (self.repo / ".gitignore").write_text(
            ".maintain/\n__pycache__/\n.pytest_cache/\n", encoding="utf-8")
        (self.repo / "app.py").write_text(APP_PY, encoding="utf-8")
        (self.repo / "test_app.py").write_text(TEST_PY, encoding="utf-8")
        (self.repo / "README.md").write_text("# Demo\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "initial")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True,
                              capture_output=True, text=True)

    def run(self, *args, keys="", cwd=None, timeout=60):
        master, slave = pty.openpty()
        attrs = termios.tcgetattr(slave)
        attrs[3] &= ~termios.ECHO       # do not echo our keystrokes into the output
        termios.tcsetattr(slave, termios.TCSANOW, attrs)
        proc = subprocess.Popen(
            [sys.executable, str(MAINTAIN_PY), *args],
            cwd=str(cwd or self.repo), env=self.env,
            stdin=slave, stdout=slave, stderr=slave, close_fds=True,
        )
        os.close(slave)
        if keys:
            os.write(master, keys.encode())
        chunks, deadline = [], time.time() + timeout
        while time.time() < deadline:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    data = os.read(master, 65536)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        break
                    raise
                if not data:
                    break
                chunks.append(data)
            elif proc.poll() is not None:
                break
        os.close(master)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            chunks.append(b"\n<<<the command did not finish>>>\n")
        text = ANSI.sub("", b"".join(chunks).decode("utf-8", "replace"))
        return Result(proc.returncode, text.replace("\r\n", "\n"))

    # -- helpers ------------------------------------------------------

    def setup(self, **overrides):
        """Initialise, skipping test setup so the tree stays clean.

        The fixture repository starts red — the typo is the bug the task
        fixes — so setup detects pytest, runs it, reports the failure, and
        asks. Enter accepts the detected command, n declines using a failing
        one, 3 skips.
        """
        self.run("init", keys="\nn\n3\n")
        config_path = self.repo / ".maintain" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["test_command"] = f"{shlex.quote(sys.executable)} test_app.py"
        config.update(overrides)
        config_path.write_text(json.dumps(config), encoding="utf-8")

    def set_clip(self, text):
        self.clip.write_text(text, encoding="utf-8")

    def attached_title(self):
        if not self.attached.exists():
            return None
        return self.attached.read_text(encoding="utf-8").split("\n", 1)[0]

    def state(self):
        current = self.repo / ".maintain" / "current-task"
        if not current.is_file() or not current.read_text().strip():
            return {}
        task_id = current.read_text(encoding="utf-8").strip()
        path = self.repo / ".maintain" / "tasks" / task_id / "state.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


@pytest.fixture
def term(tmp_path):
    return Terminal(tmp_path)


# -- tests ------------------------------------------------------------------


def test_the_whole_task_takes_one_keypress_per_step(term):
    """Enter at each stage should carry the work as far as it can go."""
    term.setup()
    result = term.run("new", "Fix the greeting")
    assert result.has("copied to your clipboard")
    assert term.attached_title() == "# Maintain Handoff — Scope"

    term.set_clip(SCOPE_RESPONSE)
    result = term.run(keys="\nq\n")
    assert "Scope response captured" in result.out
    assert "Generated implementation package" in result.out
    assert term.attached_title() == "# Maintain Handoff — Implementation"

    term.set_clip(IMPL_RESPONSE)
    result = term.run(keys="\nq\n")
    assert "Implementation response captured" in result.out
    assert "Press Enter to apply the patch and run the tests" in result.out

    result = term.run(keys="\nq\n")                 # one Enter, no y
    assert "Apply this patch to the working tree?" not in result.out
    assert "Patch applied" in result.out
    assert "Tests: PASSED" in result.out
    assert "Generated independent review package" in result.out
    assert term.attached_title() == "# Maintain Handoff — Independent Review"

    term.set_clip(REVIEW_RESPONSE)
    result = term.run(keys="\nq\n")
    assert "Review verdict: APPROVE" in result.out

    result = term.run(keys="\nq\n")
    assert "complete after" in result.out
    assert term.state() == {}


def test_resuming_re_copies_the_waiting_package(term):
    term.setup()
    term.run("new", "Fix the greeting")
    term.attached.unlink()
    result = term.run(keys="q\n")
    assert term.attached.is_file(), "resuming must put the package back"
    assert result.has("press Ctrl+V to attach the package")


def test_the_menu_offers_to_copy_again_and_open_the_folder(term):
    term.setup()
    term.run("new", "Fix the greeting")
    term.attached.unlink()
    result = term.run(keys="c\nq\n")
    assert "Copy the package again" in result.out
    assert term.attached.is_file()
    assert result.has("Copied")


def test_a_failing_round_leads_straight_into_the_correction_package(term):
    term.setup()
    term.run("new", "Fix the greeting")
    term.set_clip(SCOPE_RESPONSE)
    term.run(keys="\nq\n")
    wrong = IMPL_RESPONSE.replace('+    return "Hello, world"',
                                  '+    return "Hell, world"')
    term.set_clip(wrong)
    term.run(keys="\nq\n")
    term.attached.unlink()

    result = term.run(keys="\nq\n")
    assert "Tests: FAILED" in result.out
    assert "Generated correction package" in result.out
    assert "Correction" in (term.attached_title() or "")


def test_setup_reports_a_failing_suite_without_claiming_there_is_none(term):
    """These prompts only appear on a terminal, so they are only tested here."""
    result = term.run("init", keys="\nn\n3\n")
    assert "Found how this project is tested" in result.out
    assert result.has("that command did not pass")
    assert result.has("Maintain still needs a way to verify patches locally")
    assert not result.has("This project has no tests Maintain can run")
    config = json.loads(
        (term.repo / ".maintain" / "config.json").read_text(encoding="utf-8")
    )
    assert config["test_command"] is None      # option 3, skip


def test_setup_says_when_pytest_collected_nothing_rather_than_failed(term):
    """pytest exits 5 on an empty collection; that is not a failing suite.

    A test_*.py file that only asserts at import passes when run directly
    but is collected as nothing by pytest, and calling that a failure sends
    people looking for a bug that is not there.
    """
    (term.repo / "app.py").write_text(
        'def greet():\n    return "Hello, world"\n', encoding="utf-8")
    term.git("commit", "-aqm", "green")

    result = term.run("init", keys="\n2\n" + f"{shlex.quote(sys.executable)} test_app.py\n")
    assert result.has("found no tests to collect")
    assert not result.has("that command did not pass")
    assert not result.has("This project has no tests Maintain can run")
    config = json.loads(
        (term.repo / ".maintain" / "config.json").read_text(encoding="utf-8")
    )
    assert "test_app.py" in config["test_command"]


def test_a_moved_head_is_still_queried_from_the_menu(term):
    """Pressing Enter stands in for routine confirmations, not warnings."""
    term.setup()
    term.run("new", "Fix the greeting")
    term.set_clip(SCOPE_RESPONSE)
    term.run(keys="\nq\n")
    term.set_clip(IMPL_RESPONSE)
    term.run(keys="\nq\n")
    (term.repo / "unrelated.txt").write_text("moved on\n", encoding="utf-8")
    term.git("add", "-A")
    term.git("commit", "-qm", "unrelated")

    result = term.run(keys="\nn\n")     # Enter runs apply; the warning still asks
    assert result.has("no longer the recorded base commit")
    assert "Continue anyway?" in result.out
    assert "Patch applied" not in result.out


def test_the_home_screen_survives_a_corrupt_state_file(term):
    term.setup()
    term.run("new", "Fix the greeting")
    task_id = (term.repo / ".maintain" / "current-task").read_text().strip()
    state = term.repo / ".maintain" / "tasks" / task_id / "state.json"
    state.write_text("{ not json", encoding="utf-8")

    result = term.run(keys="q\n")
    assert "Traceback" not in result.out
    assert "not valid JSON" in result.out
    assert result.code == 1


def test_the_picker_lists_configured_projects(term, tmp_path):
    term.setup()
    term.run("new", "Fix the greeting")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    result = term.run(keys="q\n", cwd=elsewhere)
    assert "repo" in result.out
    assert "<<<the command did not finish>>>" not in result.out
