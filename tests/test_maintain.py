"""End-to-end and unit tests for the Maintain MVP.

The end-to-end tests drive the real CLI through subprocess in a temporary Git
repository. The clipboard is simulated with MAINTAIN_CLIPBOARD_CMD and
Repomix with a small PATH shim, so the tests are hermetic.
"""

import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
MAINTAIN_PY = PROJECT_ROOT / "maintain" / "maintain.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("maintain_module", MAINTAIN_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load_module()


# Fixture repository content -------------------------------------------------

APP_PY = 'def greet():\n    return "Helo, world"\n'
TEST_PY = (
    "from app import greet\n"
    "\n"
    'assert greet() == "Hello, world", f"unexpected greeting: {greet()!r}"\n'
    'print("ok")\n'
)

REPOMIX_SHIM = """#!/usr/bin/env bash
out=""
prev=""
for a in "$@"; do
  if [ "$prev" = "--output" ]; then out="$a"; fi
  prev="$a"
done
if [ -z "$out" ]; then echo "no --output argument" >&2; exit 1; fi
printf '# Repomix stub output\\n' > "$out"
"""


# Chatbot response fixtures --------------------------------------------------

SCOPE_RESPONSE = """STATUS: SCOPE_COMPLETE

## Understanding

The startup greeting contains a typo ("Helo") and must read "Hello, world".

## Allowed Files

- app.py

## Proposed Changes

Correct the greeting string returned by `greet()` in `app.py`.

## Acceptance Criteria

- `greet()` returns "Hello, world".
- Running the test file exits successfully.

## Risks and Unknowns

- None identified.
"""

PATCH_WRONG = '''diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def greet():
-    return "Helo, world"
+    return "Hell, world"
'''

PATCH_WRONG_AGAIN = '''diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def greet():
-    return "Hell, world"
+    return "Helllo, world"
'''

PATCH_RIGHT = '''diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def greet():
-    return "Hell, world"
+    return "Hello, world"
'''

PATCH_DIRECT = '''diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def greet():
-    return "Helo, world"
+    return "Hello, world"
'''

PATCH_DOCSTRING = '''diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,3 @@
 def greet():
+    """Return the startup greeting."""
     return "Hello, world"
'''

PATCH_DISALLOWED = '''diff --git a/sneaky.py b/sneaky.py
new file mode 100644
--- /dev/null
+++ b/sneaky.py
@@ -0,0 +1 @@
+x = 1
'''

PATCH_BAD_CONTEXT = '''diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def greet():
-    return "Wrong context line"
+    return "Hello, world"
'''

PATCH_TWO_FILES = '''diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def greet():
-    return "Helo, world"
+    return "Hello, world"
diff --git a/extra.py b/extra.py
new file mode 100644
--- /dev/null
+++ b/extra.py
@@ -0,0 +1,2 @@
+def helper():
+    return True
'''

RESCOPE_RESPONSE_DISCARD = """STATUS: RESCOPED
EXISTING_WORK: DISCARD

## Revised Understanding

The greeting fix also requires a helper module that the original scope
excluded.

## Revised Allowed Files

- app.py
- extra.py

## Revised Acceptance Criteria

- `greet()` returns "Hello, world".
- `extra.py` defines `helper()` returning True.

## Revised Plan

Fix the greeting in app.py and create extra.py with the helper.

## Existing Work Assessment

The applied change conflicts with the revised plan; discard it and start
from the base commit.
"""

RESCOPE_RESPONSE_RETAIN = """STATUS: RESCOPED
EXISTING_WORK: RETAIN

## Revised Understanding

Revised understanding text.

## Revised Allowed Files

- app.py
- extra.py

## Revised Acceptance Criteria

- Revised criterion.

## Revised Plan

Revised plan text.

## Existing Work Assessment

Nothing has been applied yet; there is nothing to discard.
"""

PATCH_NEW_FILE_NO_MODE = '''diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def greet():
-    return "Helo, world"
+    return "Hello, world"
diff --git a/extra.py b/extra.py
--- /dev/null
+++ b/extra.py
@@ -0,0 +1,2 @@
+def helper():
+    return True
'''

RESCOPE_REQUIRED_RESPONSE = """STATUS: RESCOPE_REQUIRED

## Rescope Reason

The change requires a helper module that is not on the allowed file list.
"""


def impl_response(patch, summary="Adjust the greeting."):
    return (
        "STATUS: IMPLEMENTATION_COMPLETE\n"
        "\n"
        "## Summary\n"
        "\n" + summary + "\n"
        "\n"
        "## Patch\n"
        "\n"
        "```diff\n" + patch.rstrip("\n") + "\n```\n"
    )


def review_response(verdict, findings="1. Reviewed the cumulative diff."):
    return (
        f"VERDICT: {verdict}\n"
        "\n"
        "## Findings\n"
        "\n" + findings + "\n"
        "\n"
        "## Acceptance-Criteria Coverage\n"
        "\n"
        "- Each criterion was checked against the diff and test results.\n"
        "\n"
        "## Risks\n"
        "\n"
        "- None.\n"
    )


# Harness --------------------------------------------------------------------


class Harness:
    def __init__(self, tmp_path):
        self.repo = tmp_path / "repo"
        self.repo.mkdir()
        self.clip = tmp_path / "clipboard.txt"
        self.clip.write_text("", encoding="utf-8")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        shim = bin_dir / "repomix"
        shim.write_text(REPOMIX_SHIM, encoding="utf-8")
        shim.chmod(0o755)
        self.env = dict(os.environ)
        self.env["PATH"] = f"{bin_dir}{os.pathsep}{self.env.get('PATH', '')}"
        self.env["MAINTAIN_CLIPBOARD_CMD"] = f"cat {shlex.quote(str(self.clip))}"
        self.registry = tmp_path / "projects.json"
        self.env["MAINTAIN_REGISTRY_PATH"] = str(self.registry)
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test User")
        (self.repo / ".gitignore").write_text(
            ".maintain/\n__pycache__/\n.pytest_cache/\n", encoding="utf-8"
        )
        (self.repo / "app.py").write_text(APP_PY, encoding="utf-8")
        (self.repo / "test_app.py").write_text(TEST_PY, encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "initial")

    def git(self, *args):
        return subprocess.run(
            ["git", *args],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )

    def run(self, *args, input_text="", expect=0):
        proc = subprocess.run(
            [sys.executable, str(MAINTAIN_PY), *args],
            cwd=self.repo,
            env=self.env,
            input=input_text,
            capture_output=True,
            text=True,
        )
        if expect is not None:
            assert proc.returncode == expect, (
                f"maintain {' '.join(args)} exited {proc.returncode}\n"
                f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
            )
        return proc

    def set_clip(self, text):
        self.clip.write_text(text, encoding="utf-8")

    @property
    def mdir(self):
        return self.repo / ".maintain"

    def task_id(self):
        return (self.mdir / "current-task").read_text(encoding="utf-8").strip()

    def task_dir(self, task_id=None):
        return self.mdir / "tasks" / (task_id or self.task_id())

    def state(self, task_id=None):
        return json.loads(
            (self.task_dir(task_id) / "state.json").read_text(encoding="utf-8")
        )

    def config(self, **overrides):
        path = self.mdir / "config.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config.update(overrides)
        path.write_text(json.dumps(config), encoding="utf-8")

    def setup(self, **config_overrides):
        self.run("init")
        overrides = {"test_command": f"{shlex.quote(sys.executable)} test_app.py"}
        overrides.update(config_overrides)
        self.config(**overrides)


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path)


# End-to-end tests -----------------------------------------------------------


def test_usage_version_and_unknown_command(h):
    out = h.run("--help").stdout
    assert "maintain init" in out and "maintain paste" in out
    assert "0.1.0" in h.run("--version").stdout
    assert h.run("frobnicate", expect=2).returncode == 2


def test_package_is_runnable_as_a_module():
    """The Windows launcher runs `python -m maintain`."""
    proc = subprocess.run(
        [sys.executable, "-m", "maintain", "--version"],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == f"maintain {mod.VERSION}"


def test_installer_files_are_present():
    """The installer is what users run; keep its pieces together."""
    for relative in (
        "install-or-update-windows.cmd",
        "uninstall-windows.cmd",
        "scripts/install-windows.ps1",
        "scripts/uninstall-windows.ps1",
        "scripts/install-unix.sh",
        "which-maintain-windows.cmd",
        "scripts/which-maintain.ps1",
        "assets/maintain.ico.b64",
    ):
        assert (PROJECT_ROOT / relative).is_file(), relative
    # The Windows installer verifies the runtime against pyproject's version.
    version = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{mod.VERSION}"' in version


def test_windows_scripts_are_pure_ascii():
    """Windows PowerShell reads .ps1 as ANSI unless it finds a BOM.

    A UTF-8 em dash then decodes to a smart quote, which PowerShell treats
    as a string delimiter, and the rest of the file is swallowed - the
    script fails to parse before it runs a single line.
    """
    for relative in (
        "scripts/install-windows.ps1",
        "scripts/uninstall-windows.ps1",
        "scripts/which-maintain.ps1",
        "install-or-update-windows.cmd",
        "uninstall-windows.cmd",
        "which-maintain-windows.cmd",
    ):
        text = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
        offenders = sorted({character for character in text if ord(character) > 127})
        assert not offenders, f"{relative} has non-ASCII characters: {offenders}"


POWERSHELL = shutil.which("pwsh") or shutil.which("powershell") or "/opt/pwsh/pwsh"


@pytest.mark.skipif(
    not Path(POWERSHELL).is_file() if Path(POWERSHELL).is_absolute() else False,
    reason="PowerShell is not available",
)
@pytest.mark.parametrize(
    "script",
    [
        "scripts/install-windows.ps1",
        "scripts/uninstall-windows.ps1",
        "scripts/which-maintain.ps1",
    ],
)
def test_powershell_scripts_parse(script):
    """Parse each installer script; a syntax error means it never runs."""
    path = PROJECT_ROOT / script
    command = (
        "$errors = $null; "
        f"[System.Management.Automation.Language.Parser]::ParseFile('{path}', "
        "[ref]$null, [ref]$errors) | Out-Null; "
        "if ($errors) { $errors | ForEach-Object { "
        "Write-Output ('line ' + $_.Extent.StartLineNumber + ': ' + $_.Message) }; exit 1 }"
    )
    proc = subprocess.run(
        [POWERSHELL, "-NoProfile", "-Command", command],
        capture_output=True, text=True,
    )
    if proc.returncode == 127 or "not found" in proc.stderr.lower():
        pytest.skip("PowerShell is not available")
    assert proc.returncode == 0, f"{script} does not parse:\n{proc.stdout}"


def test_windows_here_strings_close_at_column_zero():
    """A here-string terminator must start the line or the string runs on."""
    text = (PROJECT_ROOT / "scripts/install-windows.ps1").read_text(encoding="utf-8")
    opens = text.count("@'\n")
    closes = len([line for line in text.split("\n") if line.startswith("'@")])
    assert opens == closes, f"{opens} here-strings opened, {closes} closed at column 0"


def test_command_aliases_match_their_originals(h):
    h.setup()
    h.run("start", "Fix the greeting")          # alias for `new`
    assert h.state()["stage"] == "awaiting_scope_response"
    h.set_clip(SCOPE_RESPONSE)
    h.run("paste")                              # alias for `capture`
    assert h.state()["stage"] == "scope_captured"
    h.run("continue")                           # alias for `next`
    assert h.state()["stage"] == "awaiting_implementation_response"


def test_home_screen_shows_task_and_next_action(h):
    # Inside a repository that is not set up yet: offer to do it.
    out = h.run(input_text="q\n").stdout
    assert "MAINTAIN" in out
    assert "Not set up for Maintain yet" in out

    h.setup()
    out = h.run(input_text="q\n").stdout
    assert "Active task" in out and "None" in out

    h.run("new", "Fix the greeting shown at startup")
    out = h.run(input_text="q\n").stdout
    assert "Fix the greeting shown at startup" in out
    assert "SCOPE" in out and "IMPLEMENT" in out      # the workflow trail
    assert "Waiting for the chatbot's scope reply" in out
    assert "once you have copied the chatbot's reply" in out
    assert "Copy the package again" in out            # no navigating to folders
    assert "Open its folder" in out

    # One keypress carries the whole round: the reply is captured, the next
    # package is generated, and it is handed to the clipboard.
    h.set_clip(SCOPE_RESPONSE)
    out = h.run(input_text="\n").stdout
    assert "Scope response captured" in out
    assert "Generated implementation package" in out
    assert h.state()["stage"] == "awaiting_implementation_response"
    assert (h.repo / h.state()["current_export"]).is_file()


def run_outside_repo(h, cwd, *args, input_text=""):
    """Run maintain from a directory that is not a Git repository."""
    return subprocess.run(
        [sys.executable, str(MAINTAIN_PY), *args],
        cwd=cwd, env=h.env, input=input_text, capture_output=True, text=True,
    )


def test_first_launch_offers_to_link_or_create(h, tmp_path):
    empty = tmp_path / "elsewhere"
    empty.mkdir()
    out = run_outside_repo(h, empty, input_text="q\n").stdout
    assert "None linked yet" in out
    assert "Link a repository" in out
    assert "Create a project" in out


def test_first_launch_can_create_a_project(h, tmp_path):
    empty = tmp_path / "elsewhere"
    empty.mkdir()
    destination = tmp_path / "brand-new"
    proc = run_outside_repo(
        h, empty, input_text=f"c\n{destination}\nBrand New\nq\n"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Created Brand New" in proc.stdout
    assert (destination / "README.md").is_file()
    assert (destination / ".gitignore").is_file()
    log = subprocess.run(
        ["git", "-C", str(destination), "log", "--oneline"],
        capture_output=True, text=True,
    )
    assert "Initial commit" in log.stdout
    registry = json.loads(h.registry.read_text(encoding="utf-8"))
    assert any(entry["name"] == "Brand New" for entry in registry["projects"])


def test_first_launch_can_link_an_existing_repository(h, tmp_path):
    empty = tmp_path / "elsewhere"
    empty.mkdir()
    proc = run_outside_repo(h, empty, input_text=f"l\n{h.repo}\nq\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Linked" in proc.stdout
    registry = json.loads(h.registry.read_text(encoding="utf-8"))
    assert any(entry["path"] == str(h.repo.resolve()) for entry in registry["projects"])


def test_later_launches_list_configured_projects(h, tmp_path):
    empty = tmp_path / "elsewhere"
    empty.mkdir()
    run_outside_repo(h, empty, input_text=f"l\n{h.repo}\nq\n")
    second = tmp_path / "second-project"
    run_outside_repo(h, empty, input_text=f"c\n{second}\nSecond\nq\n")

    out = run_outside_repo(h, empty, input_text="q\n").stdout
    assert "YOUR PROJECTS" in out
    # Match on the paths: names like "repo" also occur in the menu wording.
    assert str(second.resolve()) in out and str(h.repo.resolve()) in out
    # Most recently opened first, and each row explains its state.
    assert out.index(str(second.resolve())) < out.index(str(h.repo.resolve()))
    assert "no active task" in out or "not set up" in out


def test_picker_rejects_a_folder_that_is_not_a_repository(h, tmp_path):
    empty = tmp_path / "elsewhere"
    empty.mkdir()
    plain = tmp_path / "just-a-folder"
    plain.mkdir()
    out = run_outside_repo(h, empty, input_text=f"l\n{plain}\nq\n").stdout
    assert "not a Git repository" in out
    if h.registry.exists():
        assert json.loads(h.registry.read_text(encoding="utf-8"))["projects"] == []


def test_opening_a_project_records_it_and_shows_its_task(h, tmp_path):
    h.setup()
    h.run("new", "Fix the greeting")          # registers h.repo by being inside it
    empty = tmp_path / "elsewhere"
    empty.mkdir()
    out = run_outside_repo(h, empty, input_text="1\nq\n").stdout
    assert "Fix the greeting" in out                     # the chosen project's task
    assert "Waiting for the chatbot's scope reply" in out


def test_resume_rebuilds_a_package_that_was_never_written(h):
    """Package generation can fail after the task exists (no Repomix).

    Resuming must not tell you to upload a file that is not there.
    """
    h.setup()
    h.run("new", "Fix the greeting")
    package = h.task_dir() / "scope" / "package.md"
    export = h.repo / h.state()["current_export"]
    package.unlink()
    export.unlink()

    out = h.run(input_text="q\n").stdout          # bare `maintain`
    assert "was missing, so it was rebuilt" in out
    assert package.is_file() and export.is_file()

    # `maintain next` repairs it too, rather than only reporting.
    package.unlink()
    export.unlink()
    out = h.run("next").stdout
    assert "generated again" in out
    assert package.is_file() and export.is_file()


def test_resume_rebuilds_a_missing_review_package(h):
    h.setup()
    h.run("new", "Fix the greeting")
    h.set_clip(SCOPE_RESPONSE)
    h.run("capture")
    h.run("next")
    h.set_clip(impl_response(PATCH_DIRECT))
    h.run("capture")
    h.run("apply", input_text="y\n")
    h.run("next")                                  # review package
    export = h.repo / h.state()["current_export"]
    review = h.task_dir() / "rounds" / "01" / "review-package.md"
    export.unlink()
    review.unlink()

    out = h.run(input_text="q\n").stdout
    assert "review package was missing" in out
    assert export.is_file() and review.is_file()


def test_package_is_put_on_the_clipboard_when_generated(h, tmp_path):
    """The file itself goes to the clipboard, so Ctrl+V attaches it."""
    copied = tmp_path / "copied.txt"
    h.env["MAINTAIN_COPY_FILE_CMD"] = f"printf '%s' {{path}} > {shlex.quote(str(copied))}"
    h.setup()
    out = h.run("new", "Fix the greeting").stdout
    assert "copied to your clipboard" in out
    assert "press Ctrl+V to attach" in out
    # The path handed over is the export the user must upload.
    assert copied.read_text(encoding="utf-8").endswith(
        h.state()["current_export"].split("/")[-1]
    )


def test_every_waiting_stage_hands_its_package_to_the_clipboard(h, tmp_path):
    """Scope, implementation and review packages all reach the clipboard.

    Only `new` used to copy; the packages generated afterwards printed a
    path and left the user to go and find the file. Every stage that waits
    on the chatbot must hand over its own package.
    """
    copied = tmp_path / "copied.txt"
    h.env["MAINTAIN_COPY_FILE_CMD"] = f"printf '%s' {{path}} > {shlex.quote(str(copied))}"
    h.setup()

    def clipboard_holds_current_export():
        assert copied.is_file(), "no package was copied to the clipboard"
        return Path(copied.read_text(encoding="utf-8")).name == Path(
            h.state()["current_export"]
        ).name

    out = h.run("new", "Fix the greeting").stdout
    assert "copied to your clipboard" in out
    assert clipboard_holds_current_export()

    h.set_clip(SCOPE_RESPONSE)
    h.run("capture")
    copied.unlink()
    out = h.run("next").stdout                       # implementation package
    assert "copied to your clipboard" in out
    assert clipboard_holds_current_export()

    h.set_clip(impl_response(PATCH_DIRECT))
    h.run("capture")
    h.run("apply", input_text="y\n")
    copied.unlink()
    out = h.run("next").stdout                       # review package
    assert "copied to your clipboard" in out
    assert clipboard_holds_current_export()


def test_the_package_reaches_the_clipboard_from_a_path_with_spaces(tmp_path):
    """Repositories live where the user put them, spaces included."""
    workspace = tmp_path / "my projects"
    workspace.mkdir()
    h = Harness(workspace)
    copied = tmp_path / "copied.txt"
    h.env["MAINTAIN_COPY_FILE_CMD"] = f"cp {{path}} {shlex.quote(str(copied))}"
    h.setup()
    out = h.run("new", "Fix the greeting").stdout
    assert "copied to your clipboard" in out
    assert copied.is_file(), "an unquoted path splits on the space and copies nothing"
    assert "Maintain Handoff" in copied.read_text(encoding="utf-8")


def test_clipboard_copy_failure_falls_back_to_instructions(h):
    h.env["MAINTAIN_COPY_FILE_CMD"] = "exit 1"
    h.setup()
    out = h.run("new", "Fix the greeting").stdout
    assert "Upload that file" in out
    assert "copied to your clipboard" not in out


def test_home_screen_enter_applies_without_a_second_confirmation(h):
    """The menu names the action, so Enter is the confirmation."""
    h.setup()
    h.run("new", "Fix the greeting")
    h.set_clip(SCOPE_RESPONSE)
    h.run("capture")
    h.run("next")
    h.set_clip(impl_response(PATCH_DIRECT))
    h.run("capture")

    out = h.run(input_text="\nq\n").stdout        # one Enter, no y
    assert "Press Enter to apply the patch and run the tests" in out
    assert "Apply this patch to the working tree?" not in out
    assert "Patch applied" in out and "Tests: PASSED" in out
    # ...and it carried straight on to the next package.
    assert "Generated independent review package" in out
    assert h.state()["stage"] == "awaiting_review_response"


def test_apply_still_confirms_when_run_as_a_command(h):
    """Typed blind at the shell, `maintain apply` keeps its safety prompt."""
    h.setup()
    h.run("new", "Fix the greeting")
    h.set_clip(SCOPE_RESPONSE)
    h.run("capture")
    h.run("next")
    h.set_clip(impl_response(PATCH_DIRECT))
    h.run("capture")

    out = h.run("apply", input_text="\n").stdout
    assert "Apply this patch to the working tree?" in out
    assert "Patch not applied" in out
    assert 'Helo, world' in (h.repo / "app.py").read_text(encoding="utf-8")


def test_resuming_a_waiting_task_puts_the_package_back_on_the_clipboard(h, tmp_path):
    """Coming back to a task should not mean going to find the file again."""
    copied = tmp_path / "copied.txt"
    h.env["MAINTAIN_COPY_FILE_CMD"] = f"printf '%s' {{path}} > {shlex.quote(str(copied))}"
    h.setup()
    h.run("new", "Fix the greeting")
    copied.unlink()

    out = h.run(input_text="q\n").stdout               # resume, then quit
    assert copied.is_file(), "resuming must re-copy the waiting package"
    assert Path(copied.read_text(encoding="utf-8")).name == Path(
        h.state()["current_export"]
    ).name
    assert "press Ctrl+V to attach the package" in out


def test_home_screen_can_recopy_the_package(h, tmp_path):
    copied = tmp_path / "copied.txt"
    h.env["MAINTAIN_COPY_FILE_CMD"] = f"printf '%s' {{path}} > {shlex.quote(str(copied))}"
    h.setup()
    h.run("new", "Fix the greeting")
    copied.unlink()
    out = h.run(input_text="c\nq\n").stdout      # resume, then re-copy
    assert "Copied" in out and "clipboard" in out
    assert copied.is_file(), "pressing c must put the file back on the clipboard"


def test_output_is_plain_text_when_not_a_terminal(h):
    """Captured output must stay verbatim — packages quote it back."""
    h.setup()
    out = h.run("new", "Fix the greeting").stdout
    assert "\x1b[" not in out
    long_line = [line for line in out.split("\n") if line.startswith("Next:")][0]
    assert long_line.endswith("run `maintain paste`.")  # not wrapped


def test_init_requires_git_repository(tmp_path):
    bare = tmp_path / "not-a-repo"
    bare.mkdir()
    proc = subprocess.run(
        [sys.executable, str(MAINTAIN_PY), "init"],
        cwd=bare,
        env={**os.environ, "GIT_CEILING_DIRECTORIES": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "No Git repository" in proc.stdout


def test_init_creates_layout_and_refuses_reinit(h):
    h.run("init")
    assert (h.mdir / "config.json").exists()
    assert (h.mdir / "project-context.md").exists()
    assert (h.mdir / "current-task").exists()
    assert (h.mdir / "tasks").is_dir()
    assert json.loads((h.mdir / "config.json").read_text())["maximum_rounds"] == 3
    proc = h.run("init", expect=1)
    assert "already initialised" in proc.stdout


def test_commands_require_initialisation_and_task(h):
    assert "not initialised" in h.run("status", expect=1).stdout
    h.setup()
    assert "No active task" in h.run("status").stdout
    assert "No active task" in h.run("capture", expect=1).stdout
    assert "No active task" in h.run("next", expect=1).stdout
    assert "A task request is required" in h.run("new", expect=1).stdout


def test_full_workflow_with_fix_round_and_review_round(h):
    h.setup()

    # 1-2. Create the task; the scope package is generated immediately.
    out = h.run("new", "Correct the greeting shown at startup").stdout
    task_id = h.task_id()
    assert f"Created task: {task_id}" in out
    scope_package = h.task_dir() / "scope" / "package.md"
    assert scope_package.exists()
    package_text = scope_package.read_text(encoding="utf-8")
    assert "Correct the greeting shown at startup" in package_text
    assert "Repomix stub output" in package_text
    assert "app.py" in package_text  # repository structure listing
    export = h.task_dir() / "exports" / f"maintain-{task_id}-scope.md"
    assert export.exists()
    assert h.state()["stage"] == "awaiting_scope_response"
    assert h.state()["base_commit"]

    # 3. Capture the scope response.
    h.set_clip(SCOPE_RESPONSE)
    out = h.run("capture").stdout
    assert "Scope response captured" in out
    state = h.state()
    assert state["stage"] == "scope_captured"
    assert state["allowed_files"] == ["app.py"]
    assert "Hello, world" in state["acceptance_criteria"]

    # 4. Generate the implementation package (round 1).
    out = h.run("next").stdout
    assert "implementation package" in out.lower()
    impl_package = h.task_dir() / "rounds" / "01" / "implementation-package.md"
    assert impl_package.exists()
    text = impl_package.read_text(encoding="utf-8")
    assert "STATUS: IMPLEMENTATION_COMPLETE" in text
    assert "- app.py" in text
    # The full repository structure is included so the implementer can rely
    # on files outside the allowed list existing.
    assert "test_app.py" in text
    assert h.state()["implementation_round"] == 1

    # 5. Capture a wrong implementation and apply it; tests fail.
    h.set_clip(impl_response(PATCH_WRONG))
    out = h.run("capture").stdout
    assert "Implementation response captured" in out
    assert (h.task_dir() / "rounds" / "01" / "implementation.patch").exists()
    assert h.state()["stage"] == "implementation_captured"

    out = h.run("apply", input_text="y\n").stdout
    assert "Patch applied" in out
    assert "Tests: FAILED" in out
    state = h.state()
    assert state["stage"] == "tests_failed"
    assert state["test_status"] == "failed"
    results = h.task_dir() / "rounds" / "01" / "test-results.txt"
    assert "unexpected greeting" in results.read_text(encoding="utf-8")

    # 6. Generate the correction package (round 2) and fix the greeting.
    out = h.run("next").stdout
    assert "correction package" in out.lower()
    fix_package = h.task_dir() / "rounds" / "02" / "fix-package.md"
    assert fix_package.exists()
    fix_text = fix_package.read_text(encoding="utf-8")
    assert "unexpected greeting" in fix_text  # test output is included
    assert "Hell, world" in fix_text  # cumulative diff is included
    assert h.state()["rounds_this_scope"] == 2

    h.set_clip(impl_response(PATCH_RIGHT, summary="Fix the remaining typo."))
    h.run("capture")
    out = h.run("apply", input_text="y\n").stdout
    assert "Tests: PASSED" in out
    assert h.state()["stage"] == "ready_for_review"

    # 7. Generate the review package; reviewer requires changes.
    out = h.run("next").stdout
    assert "review package" in out.lower()
    review_package = h.task_dir() / "rounds" / "02" / "review-package.md"
    review_text = review_package.read_text(encoding="utf-8")
    assert "VERDICT: APPROVE" in review_text
    assert "Hello, world" in review_text  # current file contents
    assert "Exit code: 0" in review_text  # test results

    h.set_clip(review_response("CHANGES_REQUIRED", "1. greet() needs a docstring."))
    out = h.run("capture").stdout
    assert "CHANGES_REQUIRED" in out
    assert h.state()["stage"] == "review_changes_required"

    # 8. Correction round 3 addresses the review feedback.
    out = h.run("next").stdout
    fix3 = h.task_dir() / "rounds" / "03" / "fix-package.md"
    assert fix3.exists()
    assert "docstring" in fix3.read_text(encoding="utf-8")

    h.set_clip(impl_response(PATCH_DOCSTRING, summary="Add the requested docstring."))
    h.run("capture")
    out = h.run("apply", input_text="y\n").stdout
    assert "Tests: PASSED" in out

    # 9. Review again; approve; close.
    h.run("next")
    h.set_clip(review_response("APPROVE"))
    out = h.run("capture").stdout
    assert "APPROVE" in out
    assert h.state()["stage"] == "review_approved"

    out = h.run("next", input_text="y\n").stdout
    assert "complete" in out.lower()
    assert h.state(task_id)["stage"] == "complete"
    assert h.task_id() == ""

    # Final working tree contains the cumulative change, uncommitted.
    final = (h.repo / "app.py").read_text(encoding="utf-8")
    assert final == (
        'def greet():\n'
        '    """Return the startup greeting."""\n'
        '    return "Hello, world"\n'
    )

    # 10. Every artifact is preserved.
    base = h.task_dir(task_id)
    for relative in [
        "request.md",
        "state.json",
        "scope/package.md",
        "scope/response.md",
        "rounds/01/implementation-package.md",
        "rounds/01/implementation-response.md",
        "rounds/01/implementation.patch",
        "rounds/01/test-results.txt",
        "rounds/02/fix-package.md",
        "rounds/02/implementation-response.md",
        "rounds/02/implementation.patch",
        "rounds/02/test-results.txt",
        "rounds/02/review-package.md",
        "rounds/02/review-response.md",
        "rounds/03/fix-package.md",
        "rounds/03/implementation-response.md",
        "rounds/03/implementation.patch",
        "rounds/03/test-results.txt",
        "rounds/03/review-package.md",
        "rounds/03/review-response.md",
        f"exports/maintain-{task_id}-scope.md",
        f"exports/maintain-{task_id}-implement-01.md",
        f"exports/maintain-{task_id}-fix-02.md",
        f"exports/maintain-{task_id}-fix-03.md",
        f"exports/maintain-{task_id}-review-02.md",
        f"exports/maintain-{task_id}-review-03.md",
    ]:
        assert (base / relative).exists(), f"missing artifact: {relative}"


def test_capture_rejects_invalid_responses_and_preserves_them(h):
    h.setup()
    h.run("new", "Fix the greeting")
    h.set_clip(SCOPE_RESPONSE)
    h.run("capture")
    h.run("next")

    round_dir = h.task_dir() / "rounds" / "01"

    # Empty clipboard.
    h.set_clip("")
    proc = h.run("capture", expect=1)
    assert "clipboard is empty" in proc.stdout

    # Missing response marker.
    h.set_clip("Sorry, I cannot help with that.\n")
    proc = h.run("capture", expect=1)
    assert "marker is missing" in proc.stdout
    rejected = round_dir / "implementation-response-rejected-01.md"
    assert rejected.exists()
    assert "Sorry" in rejected.read_text(encoding="utf-8")
    assert h.state()["stage"] == "awaiting_implementation_response"

    # No diff block.
    h.set_clip("STATUS: IMPLEMENTATION_COMPLETE\n\nNo patch, sorry.\n")
    proc = h.run("capture", expect=1)
    assert "No diff block" in proc.stdout
    assert (round_dir / "implementation-response-rejected-02.md").exists()

    # More than one diff block.
    two_blocks = (
        "STATUS: IMPLEMENTATION_COMPLETE\n\n```diff\n" + PATCH_DIRECT.rstrip("\n")
        + "\n```\n\n```diff\n" + PATCH_DOCSTRING.rstrip("\n") + "\n```\n"
    )
    h.set_clip(two_blocks)
    proc = h.run("capture", expect=1)
    assert "More than one diff block" in proc.stdout

    # A valid response still captures afterwards.
    h.set_clip(impl_response(PATCH_DIRECT))
    h.run("capture")
    assert h.state()["stage"] == "implementation_captured"


def test_apply_rejects_files_outside_scope_then_recovers(h):
    h.setup()
    h.run("new", "Fix the greeting")
    h.set_clip(SCOPE_RESPONSE)
    h.run("capture")
    h.run("next")

    h.set_clip(impl_response(PATCH_DISALLOWED, summary="Add sneaky module."))
    h.run("capture")
    proc = h.run("apply", expect=1)
    assert "outside the approved scope" in proc.stdout
    assert "sneaky.py" in proc.stdout
    assert not (h.repo / "sneaky.py").exists()
    state = h.state()
    assert state["stage"] == "patch_rejected"
    assert state["last_failure"] == "patch"

    # The correction package carries the validation error to the chatbot.
    h.run("next")
    fix_package = h.task_dir() / "rounds" / "02" / "fix-package.md"
    text = fix_package.read_text(encoding="utf-8")
    assert "NOT applied" in text
    assert "sneaky.py" in text

    h.set_clip(impl_response(PATCH_DIRECT))
    h.run("capture")
    out = h.run("apply", input_text="y\n").stdout
    assert "Tests: PASSED" in out


def test_apply_records_patch_that_does_not_apply(h):
    h.setup()
    h.run("new", "Fix the greeting")
    h.set_clip(SCOPE_RESPONSE)
    h.run("capture")
    h.run("next")

    h.set_clip(impl_response(PATCH_BAD_CONTEXT))
    h.run("capture")
    proc = h.run("apply", expect=1)
    assert "does not apply" in proc.stdout
    assert h.state()["stage"] == "patch_rejected"
    assert (h.repo / "app.py").read_text(encoding="utf-8") == APP_PY


def test_declining_confirmation_leaves_patch_unapplied(h):
    h.setup()
    h.run("new", "Fix the greeting")
    h.set_clip(SCOPE_RESPONSE)
    h.run("capture")
    h.run("next")
    h.set_clip(impl_response(PATCH_DIRECT))
    h.run("capture")

    out = h.run("apply", input_text="n\n").stdout
    assert "Patch not applied" in out
    assert (h.repo / "app.py").read_text(encoding="utf-8") == APP_PY
    assert h.state()["stage"] == "implementation_captured"

    out = h.run("apply", input_text="y\n").stdout
    assert "Tests: PASSED" in out


def test_round_limit_stops_for_manual_intervention(h):
    h.setup(maximum_rounds=2)
    h.run("new", "Fix the greeting")
    h.set_clip(SCOPE_RESPONSE)
    h.run("capture")
    h.run("next")

    h.set_clip(impl_response(PATCH_WRONG))
    h.run("capture")
    h.run("apply", input_text="y\n")
    assert h.state()["stage"] == "tests_failed"

    h.run("next")  # round 2 correction package
    h.set_clip(impl_response(PATCH_WRONG_AGAIN))
    h.run("capture")
    h.run("apply", input_text="y\n")
    assert h.state()["stage"] == "tests_failed"

    out = h.run("next").stdout
    assert "Maximum implementation rounds reached." in out
    assert "manual intervention" in out.lower()
    assert "No further package has been generated." in out
    assert h.state()["stage"] == "round_limit_reached"
    assert not (h.task_dir() / "rounds" / "03").exists()

    # Repeating `next` keeps the same safe stop.
    out = h.run("next").stdout
    assert "Maximum implementation rounds reached." in out

    # Raising the limit in state.json re-enables the loop.
    state_path = h.task_dir() / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["maximum_rounds"] = 3
    state_path.write_text(json.dumps(state), encoding="utf-8")
    out = h.run("next").stdout
    assert "correction package" in out.lower()
    assert (h.task_dir() / "rounds" / "03" / "fix-package.md").exists()


def test_rescope_requested_by_implementer(h):
    h.setup()
    h.run("new", "Fix the greeting")
    h.set_clip(SCOPE_RESPONSE)
    h.run("capture")
    h.run("next")

    h.set_clip(RESCOPE_REQUIRED_RESPONSE)
    out = h.run("capture").stdout
    assert "rescope" in out.lower()
    assert h.state()["stage"] == "rescope_needed"

    out = h.run("next").stdout
    assert "rescope package" in out.lower()
    package = h.task_dir() / "rescopes" / "01" / "package.md"
    text = package.read_text(encoding="utf-8")
    assert "Reported by the implementer" in text
    assert "helper module" in text

    # No changes were applied, so capture needs no keep/reset decision.
    h.set_clip(RESCOPE_RESPONSE_RETAIN)
    out = h.run("capture").stdout
    assert "Scope revised" in out
    state = h.state()
    assert state["stage"] == "rescoped"
    assert state["scope_revision"] == 2
    assert state["allowed_files"] == ["app.py", "extra.py"]
    assert state["rounds_this_scope"] == 0

    # Implementation continues under the revised scope in a new round.
    out = h.run("next").stdout
    assert (h.task_dir() / "rounds" / "02" / "implementation-package.md").exists()


def test_rescope_from_review_with_reset_and_completion(h):
    h.setup()
    h.run("new", "Fix the greeting and add a helper")
    h.set_clip(SCOPE_RESPONSE)
    h.run("capture")
    h.run("next")

    # Round 1: greeting fixed, tests pass, reviewer requests a rescope.
    h.set_clip(impl_response(PATCH_DIRECT))
    h.run("capture")
    h.run("apply", input_text="y\n")
    assert h.state()["stage"] == "ready_for_review"
    h.run("next")
    h.set_clip(review_response("RESCOPE", "1. A helper module outside scope is required."))
    out = h.run("capture").stdout
    assert "RESCOPE" in out
    assert h.state()["stage"] == "rescope_needed"

    h.run("next")
    rescope_package = (h.task_dir() / "rescopes" / "01" / "package.md").read_text(
        encoding="utf-8"
    )
    assert "Reported by the independent reviewer" in rescope_package
    assert "Hello, world" in rescope_package  # cumulative diff included

    # Capture the revised scope; DISCARD defaults to reset.
    h.set_clip(RESCOPE_RESPONSE_DISCARD)
    out = h.run("capture", input_text="\n").stdout
    assert "reset to the base commit" in out
    assert (h.repo / "app.py").read_text(encoding="utf-8") == APP_PY
    state = h.state()
    assert state["stage"] == "rescoped"
    assert state["scope_revision"] == 2
    assert state["provisional_changes"] is False

    # Round 2 under the revised scope: both files in one patch.
    h.run("next")
    package = (
        h.task_dir() / "rounds" / "02" / "implementation-package.md"
    ).read_text(encoding="utf-8")
    assert "- extra.py" in package

    h.set_clip(impl_response(PATCH_TWO_FILES, summary="Fix greeting and add helper."))
    h.run("capture")
    out = h.run("apply", input_text="y\n").stdout
    assert "Tests: PASSED" in out
    assert (h.repo / "extra.py").exists()

    # The cumulative diff for review includes the new file.
    h.run("next")
    review_package = (
        h.task_dir() / "rounds" / "02" / "review-package.md"
    ).read_text(encoding="utf-8")
    assert "extra.py" in review_package

    task_id = h.task_id()
    h.set_clip(review_response("APPROVE"))
    h.run("capture")
    h.run("next", input_text="y\n")
    assert h.state(task_id)["stage"] == "complete"
    assert h.task_id() == ""
    assert (h.repo / "extra.py").read_text(encoding="utf-8") == (
        "def helper():\n    return True\n"
    )


def test_second_task_blocked_while_first_is_active(h):
    h.setup()
    h.run("new", "First task")
    proc = h.run("new", "Second task", expect=1)
    assert "still active" in proc.stdout


def test_status_reports_state_and_next_action(h):
    h.setup()
    h.run("new", "Fix the greeting")
    out = h.run("status").stdout
    assert "Waiting for the chatbot's scope reply" in out
    assert "Implementation round: 0 of 3" in out
    assert "Scope revision: 1" in out
    assert "Next action:" in out
    assert "Package to upload:" in out

    h.set_clip(SCOPE_RESPONSE)
    h.run("capture")
    h.run("next")
    out = h.run("status").stdout
    assert "Waiting for the chatbot's patch (round 1)" in out
    assert "Implementation round: 1 of 3" in out


def test_scaffolding_creates_a_working_c_harness(tmp_path):
    """The scaffold must be a real check, not a placeholder."""
    testing = mod.testing_module()
    project = tmp_path / "chello"
    project.mkdir()
    (project / "README.md").write_text("# chello\n", encoding="utf-8")

    result = testing.scaffold_tests(project, "c")
    assert result["command"] == "make test"
    assert (project / "Makefile").is_file()
    assert (project / "tests" / "run-tests.sh").is_file()

    # With no source yet it fails: that is the goal the first task must meet.
    assert not testing.verify_command(project, result["command"])["ok"]

    (project / "hello.c").write_text(
        '#include <stdio.h>\n\nint main(void) { printf("Hello World!\\n"); return 0; }\n',
        encoding="utf-8",
    )
    assert testing.verify_command(project, result["command"])["ok"]


def test_repomix_is_launched_by_resolved_path(tmp_path, monkeypatch):
    """npm installs repomix.cmd on Windows, which a bare name cannot launch.

    CreateProcess only tries .exe for "repomix", so the executable must be
    resolved through PATHEXT first and invoked by its full path.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # Stand in for repomix.cmd: only findable via which(), not by bare name.
    shim = bin_dir / "repomix-real"
    shim.write_text(REPOMIX_SHIM, encoding="utf-8")
    shim.chmod(0o755)

    monkeypatch.setattr(mod.shutil, "which", lambda name: str(shim)
                        if name == "repomix" else None)
    assert mod.resolve_repomix() == str(shim)

    recorded = {}
    real_run = mod.run

    def spy(cmd, cwd=None):
        recorded["argv0"] = cmd[0]
        return real_run(cmd, cwd=cwd)

    monkeypatch.setattr(mod, "run", spy)
    project = tmp_path / "project"
    project.mkdir()
    output = mod.run_repomix(project, {})
    assert recorded["argv0"] == str(shim), "must invoke the resolved path"
    assert "Repomix stub output" in output


def test_repomix_missing_explains_the_stale_path_case(tmp_path, monkeypatch):
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    with pytest.raises(mod.MaintainError) as error:
        mod.resolve_repomix()
    assert "not installed or not on PATH" in str(error.value)
    assert "open a new terminal" in (error.value.next_action or "")


def test_detection_recognises_common_ecosystems(tmp_path):
    testing = mod.testing_module()
    node = tmp_path / "node"
    node.mkdir()
    (node / "package.json").write_text('{"scripts": {"test": "jest"}}', encoding="utf-8")
    assert testing.detect_test_command(node)["command"] == "npm test"

    rust = tmp_path / "rust"
    rust.mkdir()
    (rust / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    assert testing.detect_test_command(rust)["command"] == "cargo test"

    make = tmp_path / "make"
    make.mkdir()
    (make / "Makefile").write_text("test:\n\t@echo ok\n", encoding="utf-8")
    assert testing.detect_test_command(make)["command"] == "make test"

    empty = tmp_path / "empty"
    empty.mkdir()
    assert testing.detect_test_command(empty) is None


def test_setup_detects_how_the_project_is_tested(h):
    """A project is not set up until Maintain knows how to verify it."""
    h.run("init")                                  # no test_command written
    config = json.loads((h.mdir / "config.json").read_text(encoding="utf-8"))
    # The fixture has test_app.py, so setup finds it without being told.
    assert config["test_command"], "init should have detected the Python tests"
    assert "pytest" in config["test_command"] or "unittest" in config["test_command"]


def test_task_creation_configures_tests_when_setup_did_not(h):
    h.setup(test_command=None)
    out = h.run("new", "Fix the greeting").stdout
    assert "no test command" in out.lower()
    config = json.loads((h.mdir / "config.json").read_text(encoding="utf-8"))
    assert config["test_command"], "creating a task should have configured tests"


def test_no_test_command_records_not_configured(h, tmp_path):
    # A project with nothing testable: detection finds nothing to configure.
    (h.repo / "test_app.py").unlink()
    h.git("add", "-A")
    h.git("commit", "-qm", "remove tests")
    h.setup(test_command=None)
    out = h.run("new", "Fix the greeting").stdout
    assert "NOT_CONFIGURED" in out
    h.run("new", "unused", expect=1)               # one active task at a time
    h.set_clip(SCOPE_RESPONSE)
    h.run("capture")
    h.run("next")
    h.set_clip(impl_response(PATCH_DIRECT))
    h.run("capture")
    out = h.run("apply", input_text="y\n").stdout
    assert "NOT_CONFIGURED" in out
    state = h.state()
    assert state["test_status"] == "not_configured"
    assert state["stage"] == "ready_for_review"
    results = h.task_dir() / "rounds" / "01" / "test-results.txt"
    assert "NOT_CONFIGURED" in results.read_text(encoding="utf-8")


HARDEN_SCOPE_RESPONSE = """STATUS: SCOPE_COMPLETE

## Understanding

app.py is covered only on the happy path; the greeting has no boundary or
end-to-end tests. Hardening adds exact-value and CLI-level tests.

## Allowed Files

- test_hardening.py

## Proposed Changes

test_hardening.py: exact-value assertions for greet() and an end-to-end
subprocess invocation of the module.

## Acceptance Criteria

- The hardening gate command passes.
- No coverage-exclusion pragmas are added to logic.
- Every new test asserts an exact expected value.

## Risks and Unknowns

- None.
"""

HARDEN_TEST_PATCH = '''diff --git a/test_hardening.py b/test_hardening.py
new file mode 100644
--- /dev/null
+++ b/test_hardening.py
@@ -0,0 +1,5 @@
+from app import greet
+
+
+def test_exact_value():
+    assert greet() == "Hello, world"
'''


def test_harden_workflow_uses_gate_command_and_targets(h):
    h.setup()
    # Complete an ordinary task first so harden has targets to derive.
    h.run("new", "Fix the greeting")
    h.set_clip(SCOPE_RESPONSE)
    h.run("capture")
    h.run("next")
    h.set_clip(impl_response(PATCH_DIRECT))
    h.run("capture")
    h.run("apply", input_text="y\n")
    h.run("next")
    h.set_clip(review_response("APPROVE"))
    h.run("capture")
    h.run("next", input_text="y\n")
    # The user commits accepted work before starting the next task.
    h.git("add", "-A")
    h.git("commit", "-qm", "task 1 accepted")

    gate = f"{shlex.quote(sys.executable)} -c \"print('HARDEN GATE OK')\""
    h.config(harden_command=gate)
    out = h.run("harden", "focus on boundaries").stdout
    assert "Created task:" in out
    task_id = h.task_id()
    state = h.state()
    assert state["kind"] == "harden"

    package = (
        h.task_dir() / "scope" / "package.md"
    ).read_text(encoding="utf-8")
    assert "Test Hardening Scope" in package
    assert "- app.py" in package        # derived target (non-test files only)
    assert "test_app.py\n" in package   # visible in repo structure
    assert "HARDEN GATE OK" in package  # gate command shown to the scoper
    assert "focus on boundaries" in package

    h.set_clip(HARDEN_SCOPE_RESPONSE)
    h.run("capture")
    h.run("next")
    h.set_clip(impl_response(HARDEN_TEST_PATCH, summary="Add hardening tests."))
    h.run("capture")
    out = h.run("apply", input_text="y\n").stdout
    assert "Tests: PASSED" in out
    results = (
        h.task_dir(task_id) / "rounds" / "01" / "test-results.txt"
    ).read_text(encoding="utf-8")
    assert "HARDEN GATE OK" in results  # the gate ran, not test_command


def test_harden_packages_include_read_only_target_contents(h):
    """Hardening tests assert against the targets, so their contents must ship.

    The targets stay off the allowed list (they must not be modified), which
    is exactly why packing only the allowed files left the implementer
    unable to write exact-value assertions.
    """
    h.setup()
    h.run("new", "Fix the greeting")
    h.set_clip(SCOPE_RESPONSE)
    h.run("capture")
    h.run("next")
    h.set_clip(impl_response(PATCH_DIRECT))
    h.run("capture")
    h.run("apply", input_text="y\n")
    h.run("next")
    h.set_clip(review_response("APPROVE"))
    h.run("capture")
    h.run("next", input_text="y\n")
    h.git("add", "-A")
    h.git("commit", "-qm", "accepted")

    h.run("harden")
    assert h.state()["harden_targets"] == ["app.py"]
    h.set_clip(HARDEN_SCOPE_RESPONSE)
    h.run("capture")
    h.run("next")

    package = (
        h.task_dir() / "rounds" / "01" / "implementation-package.md"
    ).read_text(encoding="utf-8")
    assert "READ-ONLY" in package
    # Repomix is stubbed in these tests, so assert the wiring: app.py is a
    # context file even though only test_hardening.py is writable.
    assert "- test_hardening.py" in package
    state = h.state()
    assert "app.py" not in state["allowed_files"]

    # A patch touching the read-only target is still rejected.
    h.set_clip(impl_response(PATCH_DOCSTRING, summary="Edit the target."))
    h.run("capture")
    proc = h.run("apply", expect=1)
    assert "outside the approved scope" in proc.stdout


def test_harden_does_not_warn_about_the_work_it_is_hardening(h):
    """Closing a task leaves the change uncommitted, by design.

    Running `harden` next is the documented flow, so the tree is expected to
    be dirty — warning about it, and demanding a confirmation, means the
    workflow objects to its own output.
    """
    h.setup()
    h.run("new", "Fix the greeting")
    h.set_clip(SCOPE_RESPONSE)
    h.run("capture")
    h.run("next")
    h.set_clip(impl_response(PATCH_DIRECT))
    h.run("capture")
    h.run("apply", input_text="y\n")
    h.run("next")
    h.set_clip(review_response("APPROVE"))
    h.run("capture")
    h.run("next", input_text="y\n")
    assert h.git("status", "--porcelain").stdout.strip(), "the change is uncommitted"

    out = h.run("harden").stdout                    # no confirmation supplied
    assert "Created task:" in out
    assert "Continue creating the task?" not in out
    assert "Hardening the uncommitted work" in out
    assert " M app.py" in out                       # says what it is hardening


def test_harden_without_completed_tasks_targets_whole_repo(h):
    h.setup()
    h.run("harden")
    package = (h.task_dir() / "scope" / "package.md").read_text(encoding="utf-8")
    assert "whole repository" in package


# Unit tests -----------------------------------------------------------------


def test_parse_path_bullets_handles_common_bullet_shapes():
    body = (
        "- `src/a.py`\n"
        "- src/b.py — modified for the new behaviour\n"
        "1. c.py\n"
        "* d.py,\n"
        "+ **e.py**\n"
        "not a bullet line\n"
    )
    assert mod.parse_path_bullets(body) == [
        "src/a.py",
        "src/b.py",
        "c.py",
        "d.py",
        "e.py",
    ]


def test_extract_section_keeps_subsections():
    text = (
        "## Proposed Changes\n\n"
        "### snake.html (new file)\n\ndetails here\n\n"
        "## Next Section\n\nother\n"
    )
    body = mod.extract_section(text, "Proposed Changes")
    assert "snake.html" in body
    assert "details here" in body
    assert "other" not in body


def test_extract_section_is_case_insensitive_and_bounded():
    text = (
        "## Allowed files:\n\nbody line\n\n"
        "### Nested heading\n\nnested body\n\n"
        "## Next Section\n\nother\n"
    )
    section = mod.extract_section(text, "Allowed Files")
    assert "body line" in section
    assert "other" not in section


def test_find_marker_tolerates_bold_and_quotes():
    assert (
        mod.find_marker("**STATUS: SCOPE_COMPLETE**", "STATUS", ["SCOPE_COMPLETE"])
        == "SCOPE_COMPLETE"
    )
    assert (
        mod.find_marker("> VERDICT: **CHANGES_REQUIRED**", "VERDICT",
                        ["APPROVE", "CHANGES_REQUIRED", "RESCOPE"])
        == "CHANGES_REQUIRED"
    )
    assert mod.find_marker("status: rescoped", "STATUS", ["RESCOPED"]) == "RESCOPED"
    assert (
        mod.find_marker("## Verdict: CHANGES_REQUIRED", "VERDICT",
                        ["APPROVE", "CHANGES_REQUIRED", "RESCOPE"])
        == "CHANGES_REQUIRED"
    )
    assert mod.find_marker("no marker here", "STATUS", ["SCOPE_COMPLETE"]) is None


def test_review_validation_accepts_approved_variant():
    data = mod.validate_review_response(None, review_response("APPROVED"))
    assert data["verdict"] == "APPROVE"


def test_extract_diff_blocks_counts_blocks():
    one = "before\n```diff\ndiff --git a/x b/x\n```\nafter\n"
    assert len(mod.extract_diff_blocks(one)) == 1
    two = one + "\n```diff\ndiff --git a/y b/y\n```\n"
    assert len(mod.extract_diff_blocks(two)) == 2
    assert mod.extract_diff_blocks("no fences") == []


def test_diff_blocks_are_recognised_whatever_the_fence_says():
    """Chatbots label a diff several ways; the body is what decides."""
    body = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b"
    for label in ("diff", "patch", "", "git-diff", "udiff"):
        text = f"before\n```{label}\n{body}\n```\nafter\n"
        assert len(mod.extract_diff_blocks(text)) == 1, label
    # A fenced block that is not a diff is not mistaken for one.
    assert mod.extract_diff_blocks("```python\nx = 1\n```\n") == []
    assert mod.extract_diff_blocks("```\njust some notes\n```\n") == []


def test_a_non_diff_fenced_block_survives_stripping():
    """Only the patch is replaced; example code in the summary is kept."""
    text = ("```python\nkeep = 1\n```\n\n"
            "```patch\ndiff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n```\n")
    stripped = mod.strip_diff_blocks(text)
    assert "keep = 1" in stripped
    assert "diff --git" not in stripped
    assert "patch omitted" in stripped


def test_patch_fence_reaches_apply(h):
    """A ```patch fence is as good as ```diff, all the way through."""
    h.setup()
    h.run("new", "Fix the greeting")
    h.set_clip(SCOPE_RESPONSE)
    h.run("capture")
    h.run("next")
    h.set_clip(impl_response(PATCH_DIRECT).replace("```diff", "```patch"))
    out = h.run("capture").stdout
    assert "Implementation response captured" in out
    assert "Patch applied" in h.run("apply", input_text="y\n").stdout


def test_patch_paths_extracts_and_validates():
    assert mod.patch_paths(PATCH_TWO_FILES) == ["app.py", "extra.py"]
    quoted = 'diff --git "a/dir with space/f.py" "b/dir with space/f.py"\n'
    assert mod.patch_paths(quoted) == ["dir with space/f.py"]
    with pytest.raises(mod.MaintainError):
        mod.patch_paths("--- app.py\n+++ app.py\n@@ -1 +1 @@\n-x\n+y\n")
    with pytest.raises(mod.MaintainError):
        mod.patch_paths("this is not a diff at all")


def test_new_file_patch_without_mode_line_applies(h):
    """Chatbots often omit `new file mode`; capture must restore it."""
    h.setup()
    h.run("new", "Fix the greeting and add a helper")
    scope = SCOPE_RESPONSE.replace("- app.py", "- app.py\n- extra.py")
    h.set_clip(scope)
    h.run("capture")
    h.run("next")
    h.set_clip(impl_response(PATCH_NEW_FILE_NO_MODE))
    h.run("capture")
    patch_text = (
        h.task_dir() / "rounds" / "01" / "implementation.patch"
    ).read_text(encoding="utf-8")
    assert "new file mode 100644" in patch_text
    out = h.run("apply", input_text="y\n").stdout
    assert "Tests: PASSED" in out
    assert (h.repo / "extra.py").exists()


MULTILINE_APP_PY = (
    "def greeting():\n"
    '    return "Helo, world"\n'
    "\n"
    "\n"
    'if __name__ == "__main__":\n'
    "    print(greeting())\n"
)

PATCH_NO_TRAILING_CONTEXT = '''diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def greeting():
-    return "Helo, world"
+    return "Hello, world!"
'''


def test_patch_without_trailing_context_is_healed(h):
    """git apply rejects a hunk that ends on a changed line mid-file.

    Chatbots trim trailing context routinely, so the apply step repairs it
    from the working tree rather than burning a correction round.
    """
    (h.repo / "app.py").write_text(MULTILINE_APP_PY, encoding="utf-8")
    (h.repo / "test_app.py").write_text(
        "from app import greeting\n\n\n"
        'def test_greeting():\n    assert greeting() == "Hello, world!"\n',
        encoding="utf-8",
    )
    h.git("add", "-A")
    h.git("commit", "-qm", "multi-line fixture")
    h.setup()
    h.run("new", "Fix the greeting")
    h.set_clip(SCOPE_RESPONSE)
    h.run("capture")
    h.run("next")
    h.set_clip(impl_response(PATCH_NO_TRAILING_CONTEXT))
    h.run("capture")

    # The captured patch is rejected by git as-is...
    patch_file = h.task_dir() / "rounds" / "01" / "implementation.patch"
    raw = subprocess.run(
        ["git", "apply", "--recount", "--check", str(patch_file)],
        cwd=h.repo, capture_output=True, text=True,
    )
    assert raw.returncode != 0

    # ...but apply heals it and the tests pass.
    out = h.run("apply", input_text="y\n").stdout
    assert "added trailing context" in out
    assert "Tests: PASSED" in out
    assert (h.repo / "app.py").read_text(encoding="utf-8") == MULTILINE_APP_PY.replace(
        '"Helo, world"', '"Hello, world!"'
    )


def test_add_trailing_context_leaves_valid_patches_alone(tmp_path):
    (tmp_path / "app.py").write_text(MULTILINE_APP_PY, encoding="utf-8")
    # Already has trailing context: unchanged.
    with_context = PATCH_NO_TRAILING_CONTEXT.replace(
        '+    return "Hello, world!"\n', '+    return "Hello, world!"\n \n'
    ).replace("@@ -1,2 +1,2 @@", "@@ -1,3 +1,3 @@")
    assert mod.add_trailing_context(with_context, tmp_path) == with_context
    # A created file has no old side to extend.
    assert mod.add_trailing_context(PATCH_DISALLOWED, tmp_path) == PATCH_DISALLOWED
    # A hunk that already runs to end of file is left alone.
    at_eof = (
        "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n"
        "@@ -6,1 +6,1 @@\n-    print(greeting())\n+    print(greeting().upper())\n"
    )
    assert mod.add_trailing_context(at_eof, tmp_path) == at_eof
    # Healing appends exactly the real next line of the file.
    healed = mod.add_trailing_context(PATCH_NO_TRAILING_CONTEXT, tmp_path)
    assert "@@ -1,3 +1,3 @@" in healed
    assert healed.endswith('+    return "Hello, world!"\n \n')


def test_normalise_patch_inserts_missing_mode_lines():
    fixed = mod.normalise_patch(PATCH_NEW_FILE_NO_MODE)
    assert fixed.count("new file mode 100644") == 1
    assert "diff --git a/extra.py b/extra.py\nnew file mode 100644\n--- /dev/null" in fixed
    # Modified-file sections are untouched.
    assert "diff --git a/app.py b/app.py\n--- a/app.py" in fixed
    # Patches that already carry the metadata are unchanged.
    assert mod.normalise_patch(PATCH_TWO_FILES) == PATCH_TWO_FILES
    deletion = (
        "diff --git a/gone.py b/gone.py\n"
        "--- a/gone.py\n"
        "+++ /dev/null\n"
        "@@ -1,1 +0,0 @@\n"
        "-x = 1\n"
    )
    assert "deleted file mode 100644" in mod.normalise_patch(deletion)


def test_unsafe_path_reasons():
    assert mod.unsafe_path_reason("/etc/passwd")
    assert mod.unsafe_path_reason("../outside.py")
    assert mod.unsafe_path_reason(".git/hooks/pre-commit")
    assert mod.unsafe_path_reason(".maintain/state.json")
    assert mod.unsafe_path_reason("src/ok.py") is None
