#!/usr/bin/env python3
"""Maintain — a small local handoff and workflow coordinator.

Maintain coordinates a controlled software-maintenance workflow across
separate chatbot conversations.  It generates one Markdown handoff file per
workflow stage, captures the chatbot response from the clipboard, validates
implementation patches, applies them through Git, runs local tests, and
supports bounded correction and rescope loops.

Maintain is not an AI agent and does not communicate with a chatbot
automatically.

Commands:
    maintain init             Create Maintain configuration and project context
    maintain new "<request>"  Create a task and generate its scope package
    maintain capture          Read and store the expected chatbot response
    maintain next             Generate the next appropriate handoff package
    maintain apply            Validate, confirm, apply and test a patch
    maintain status           Display current task state and next action
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

VERSION = "0.1.0"

APP_DIR_NAME = ".maintain"
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

DEFAULT_CONFIG = {
    "test_command": None,
    "maximum_rounds": 3,
    "repomix_args": [],
}

# Workflow stages -----------------------------------------------------------

S_AWAIT_SCOPE = "awaiting_scope_response"
S_SCOPE_CAPTURED = "scope_captured"
S_AWAIT_IMPL = "awaiting_implementation_response"
S_IMPL_CAPTURED = "implementation_captured"
S_PATCH_REJECTED = "patch_rejected"
S_TESTS_FAILED = "tests_failed"
S_READY_FOR_REVIEW = "ready_for_review"
S_AWAIT_REVIEW = "awaiting_review_response"
S_REVIEW_APPROVED = "review_approved"
S_REVIEW_CHANGES = "review_changes_required"
S_RESCOPE_NEEDED = "rescope_needed"
S_AWAIT_RESCOPE = "awaiting_rescope_response"
S_RESCOPED = "rescoped"
S_LIMIT_REACHED = "round_limit_reached"
S_COMPLETE = "complete"

STAGE_LABELS = {
    S_AWAIT_SCOPE: "Waiting for scope response",
    S_SCOPE_CAPTURED: "Scope captured",
    S_AWAIT_IMPL: "Waiting for implementation response",
    S_IMPL_CAPTURED: "Implementation captured",
    S_PATCH_REJECTED: "Patch rejected",
    S_TESTS_FAILED: "Tests failed",
    S_READY_FOR_REVIEW: "Ready for review",
    S_AWAIT_REVIEW: "Waiting for review response",
    S_REVIEW_APPROVED: "Review approved",
    S_REVIEW_CHANGES: "Review requested changes",
    S_RESCOPE_NEEDED: "Rescope required",
    S_AWAIT_RESCOPE: "Waiting for rescope response",
    S_RESCOPED: "Scope revised",
    S_LIMIT_REACHED: "Round limit reached",
    S_COMPLETE: "Complete",
}

STAGE_NEXT_ACTIONS = {
    S_AWAIT_SCOPE: "Upload the scope package to a fresh chatbot conversation, copy the complete reply, then run `maintain capture`.",
    S_SCOPE_CAPTURED: "Run `maintain next` to generate the implementation package.",
    S_AWAIT_IMPL: "Upload the package to a fresh chatbot conversation, copy the complete reply, then run `maintain capture`.",
    S_IMPL_CAPTURED: "Run `maintain apply` to validate, apply and test the captured patch.",
    S_PATCH_REJECTED: "Run `maintain next` to generate a correction package.",
    S_TESTS_FAILED: "Run `maintain next` to generate a correction package.",
    S_READY_FOR_REVIEW: "Run `maintain next` to generate the review package.",
    S_AWAIT_REVIEW: "Upload the review package to a fresh chatbot conversation, copy the complete reply, then run `maintain capture`.",
    S_REVIEW_APPROVED: "Run `maintain next` to close the task.",
    S_REVIEW_CHANGES: "Run `maintain next` to generate a correction package.",
    S_RESCOPE_NEEDED: "Run `maintain next` to generate the rescope package.",
    S_AWAIT_RESCOPE: "Upload the rescope package to a fresh chatbot conversation, copy the complete reply, then run `maintain capture`.",
    S_RESCOPED: "Run `maintain next` to generate the implementation package for the revised scope.",
    S_LIMIT_REACHED: "Manual intervention required. Finish the change by hand, or raise \"maximum_rounds\" in the task's state.json and run `maintain next`.",
    S_COMPLETE: "No further action. Create a new task with `maintain new \"<request>\"`.",
}


class MaintainError(RuntimeError):
    """A safe, expected failure with a suggested next manual action."""

    def __init__(self, message: str, next_action: Optional[str] = None):
        super().__init__(message)
        self.next_action = next_action


def say(message: str = "") -> None:
    print(message)


# Subprocess helpers --------------------------------------------------------


def run(cmd: list, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def run_shell_combined(command: str, cwd: Path) -> subprocess.CompletedProcess:
    """Run a shell command capturing stdout and stderr interleaved."""
    return subprocess.run(
        command,
        shell=True,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def git_run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return run(["git", *args], cwd=root)


def git_out(root: Path, *args: str) -> str:
    proc = git_run(root, *args)
    if proc.returncode != 0:
        raise MaintainError(
            "git {} failed:\n{}".format(" ".join(args), (proc.stderr or proc.stdout).strip())
        )
    return proc.stdout


# Repository / configuration ------------------------------------------------


def find_repo_root() -> Path:
    proc = run(["git", "rev-parse", "--show-toplevel"])
    if proc.returncode != 0:
        raise MaintainError(
            "No Git repository is present here. Maintain must run inside a Git repository.",
            "Change into the repository you want to maintain, or create one with `git init`.",
        )
    return Path(proc.stdout.strip())


def maintain_dir(root: Path) -> Path:
    return root / APP_DIR_NAME


def require_initialised(root: Path) -> Path:
    directory = maintain_dir(root)
    if not directory.is_dir():
        raise MaintainError(
            "This repository is not initialised for Maintain.",
            "Run `maintain init` first.",
        )
    return directory


def load_config(root: Path) -> dict:
    config = dict(DEFAULT_CONFIG)
    path = maintain_dir(root) / "config.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise MaintainError(
                f"{path} is not valid JSON: {exc}",
                "Fix the configuration file and run the command again.",
            )
        if isinstance(data, dict):
            config.update(data)
    return config


def current_task_id(root: Path) -> Optional[str]:
    path = maintain_dir(root) / "current-task"
    if not path.exists():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def set_current_task(root: Path, task_id: Optional[str]) -> None:
    (maintain_dir(root) / "current-task").write_text(
        (task_id or "") + ("\n" if task_id else ""), encoding="utf-8"
    )


# Task ----------------------------------------------------------------------


class Task:
    def __init__(self, root: Path, task_id: str):
        self.root = root
        self.id = task_id
        self.dir = maintain_dir(root) / "tasks" / task_id
        self.state_file = self.dir / "state.json"
        if self.state_file.exists():
            self.state = json.loads(self.state_file.read_text(encoding="utf-8"))
        else:
            self.state = {}

    # Paths
    @property
    def scope_dir(self) -> Path:
        return self.dir / "scope"

    @property
    def rounds_dir(self) -> Path:
        return self.dir / "rounds"

    def round_dir(self, number: int) -> Path:
        return self.rounds_dir / f"{number:02d}"

    @property
    def rescopes_dir(self) -> Path:
        return self.dir / "rescopes"

    def rescope_dir(self, number: int) -> Path:
        return self.rescopes_dir / f"{number:02d}"

    @property
    def exports_dir(self) -> Path:
        return self.dir / "exports"

    @property
    def request_file(self) -> Path:
        return self.dir / "request.md"

    def rel(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return str(path)

    # State accessors
    @property
    def stage(self) -> str:
        return self.state.get("stage", "")

    @property
    def base_commit(self) -> str:
        return self.state.get("base_commit", "")

    @property
    def implementation_round(self) -> int:
        return int(self.state.get("implementation_round", 0))

    @property
    def rounds_this_scope(self) -> int:
        return int(self.state.get("rounds_this_scope", 0))

    @property
    def maximum_rounds(self) -> int:
        return int(self.state.get("maximum_rounds", DEFAULT_CONFIG["maximum_rounds"]))

    @property
    def allowed_files(self) -> list:
        return list(self.state.get("allowed_files", []))

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.state, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.state_file)

    def request_text(self) -> str:
        if self.request_file.exists():
            return self.request_file.read_text(encoding="utf-8").strip()
        return "(request not recorded)"


def load_active_task(root: Path) -> Task:
    task_id = current_task_id(root)
    if not task_id:
        raise MaintainError(
            "No active task.",
            'Create one with `maintain new "<request>"`.',
        )
    task = Task(root, task_id)
    if not task.state:
        raise MaintainError(
            f"The active task {task_id} has no state file ({task.rel(task.state_file)}).",
            "The task storage is damaged. Clear .maintain/current-task or restore state.json.",
        )
    return task


def stage_label(task: Task) -> str:
    label = STAGE_LABELS.get(task.stage, task.stage or "Unknown")
    if task.stage in (S_AWAIT_IMPL, S_IMPL_CAPTURED, S_AWAIT_REVIEW) and task.implementation_round:
        label += f" (round {task.implementation_round})"
    return label


def stage_next_action(task: Task) -> str:
    return STAGE_NEXT_ACTIONS.get(task.stage, "Run `maintain status` to inspect the task.")


# Interaction ---------------------------------------------------------------


def confirm(prompt: str) -> bool:
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


def ask_keep_or_reset(recommendation: str) -> str:
    default = {"RETAIN": "keep", "DISCARD": "reset"}.get(recommendation)
    prompt = "Keep the current task changes or reset them to the base commit? [keep/reset]"
    if default:
        prompt += f" (default: {default})"
    for _ in range(5):
        try:
            answer = input(prompt + " ").strip().lower()
        except EOFError:
            answer = ""
            if not default:
                break
        if not answer and default:
            return default
        if answer in ("keep", "k"):
            return "keep"
        if answer in ("reset", "r"):
            return "reset"
    raise MaintainError(
        "A keep/reset decision is required to continue after a rescope.",
        "Run `maintain capture` again with the rescope response in the clipboard.",
    )


# Clipboard -----------------------------------------------------------------


def read_clipboard() -> str:
    """Read text from the system clipboard.

    Order: MAINTAIN_CLIPBOARD_CMD override, pyperclip, platform tools.
    """
    override = os.environ.get("MAINTAIN_CLIPBOARD_CMD")
    if override:
        proc = subprocess.run(
            override,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            raise MaintainError(
                f"MAINTAIN_CLIPBOARD_CMD failed (exit {proc.returncode}):\n{proc.stderr.strip()}"
            )
        return proc.stdout

    try:
        import pyperclip  # type: ignore

        return pyperclip.paste()
    except Exception:
        pass

    candidates = [
        ["pbpaste"],
        ["wl-paste", "--no-newline"],
        ["xclip", "-selection", "clipboard", "-o"],
        ["xsel", "-b"],
        ["powershell.exe", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
    ]
    for cmd in candidates:
        if shutil.which(cmd[0]):
            proc = run(cmd)
            if proc.returncode == 0:
                return proc.stdout
    raise MaintainError(
        "Could not read the clipboard: no clipboard mechanism is available.",
        "Install pyperclip (`pip install pyperclip`) or set MAINTAIN_CLIPBOARD_CMD to a "
        "command that prints the clipboard contents (for example `powershell.exe -NoProfile "
        "-Command Get-Clipboard -Raw` under WSL).",
    )


def normalise_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text.startswith("\ufeff"):
        text = text[1:]
    if not text.endswith("\n"):
        text += "\n"
    return text


def next_free(path: Path, tag: str) -> Path:
    number = 1
    while True:
        candidate = path.with_name(f"{path.stem}-{tag}-{number:02d}{path.suffix}")
        if not candidate.exists():
            return candidate
        number += 1


# Response parsing ----------------------------------------------------------

DIFF_BLOCK_RE = re.compile(
    r"^[ \t]{0,3}(?P<fence>`{3,})diff[^\n]*\n(?P<body>.*?)\n[ \t]{0,3}(?P=fence)[ \t]*$",
    re.MULTILINE | re.DOTALL,
)


def find_marker(text: str, name: str, values: list) -> Optional[str]:
    alternatives = "|".join(sorted(values, key=len, reverse=True))
    pattern = re.compile(
        rf"^[ \t>#]*\**[ \t]*{name}[ \t]*\**[ \t]*:[ \t]*\**[ \t]*({alternatives})\b",
        re.MULTILINE | re.IGNORECASE,
    )
    match = pattern.search(text)
    return match.group(1).upper() if match else None


def extract_section(text: str, heading: str) -> Optional[str]:
    """Return the body of the section with the given heading.

    The body runs until the next heading of the same or a higher level, so
    deeper subheadings (for example ### under ##) stay inside the section.
    """
    heading_pattern = re.compile(
        rf"^(#{{2,4}})[ \t]*\**[ \t]*{re.escape(heading)}[ \t]*\**[ \t]*:?[ \t]*\n",
        re.MULTILINE | re.IGNORECASE,
    )
    match = heading_pattern.search(text)
    if not match:
        return None
    level = len(match.group(1))
    stop = re.compile(rf"^#{{1,{level}}}[ \t]", re.MULTILINE)
    end = stop.search(text, match.end())
    body = text[match.end() : end.start()] if end else text[match.end() :]
    return body.strip()


def normalise_path(value: str) -> str:
    value = value.strip().strip("`\"'").replace("\\", "/").strip()
    while value.startswith("./"):
        value = value[2:]
    return value.rstrip("/")


def parse_path_bullets(body: str) -> list:
    paths = []
    for line in body.splitlines():
        match = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$", line)
        if not match:
            continue
        item = match.group(1).strip().strip("*_").strip()
        if not item:
            continue
        token = item.split()[0]
        token = normalise_path(token.rstrip(",;:"))
        if token and token not in paths:
            paths.append(token)
    return paths


def unsafe_path_reason(path: str) -> Optional[str]:
    if path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        return "absolute path"
    parts = path.split("/")
    if ".." in parts:
        return "path traversal"
    if parts[0] in (".git", APP_DIR_NAME):
        return f"inside {parts[0]}/"
    return None


def extract_diff_blocks(text: str) -> list:
    return [match.group("body") for match in DIFF_BLOCK_RE.finditer(text)]


def strip_diff_blocks(text: str) -> str:
    return DIFF_BLOCK_RE.sub("[patch omitted — preserved in the task's round directory]", text)


# Chatbots often omit the metadata line git requires for created or deleted
# files; without it `git apply` mis-parses /dev/null as a path. Restoring the
# line is mechanical and does not alter the change itself.
_MISSING_NEW_MODE_RE = re.compile(r"^(diff --git [^\n]+\n)(?=--- /dev/null$)", re.MULTILINE)
_MISSING_DELETED_MODE_RE = re.compile(
    r"^(diff --git [^\n]+\n)(?=--- (?:\"a/[^\n]+\"|a/[^\n]+)\n\+\+\+ /dev/null$)",
    re.MULTILINE,
)


def normalise_patch(patch: str) -> str:
    patch = _MISSING_NEW_MODE_RE.sub(r"\g<1>new file mode 100644\n", patch)
    patch = _MISSING_DELETED_MODE_RE.sub(r"\g<1>deleted file mode 100644\n", patch)
    return patch


def patch_paths(patch: str) -> list:
    """Extract repository-relative file paths from a unified Git diff."""
    paths = []

    def add(path: str) -> None:
        cleaned = normalise_path(path)
        if cleaned and cleaned not in paths:
            paths.append(cleaned)

    saw_git_header = False
    for match in re.finditer(
        r'^diff --git (?:"a/([^"]+)"|a/(\S+)) (?:"b/([^"]+)"|b/(\S+))[ \t]*$',
        patch,
        re.MULTILINE,
    ):
        saw_git_header = True
        for group in match.groups():
            if group:
                add(group)

    if not saw_git_header:
        bad = []
        for match in re.finditer(r'^(---|\+\+\+) ("?)(\S+)\2', patch, re.MULTILINE):
            path = match.group(3)
            if path == "/dev/null":
                continue
            if path.startswith(("a/", "b/")):
                add(path[2:])
            else:
                bad.append(path)
        if bad:
            raise MaintainError(
                "The patch is not in Git unified-diff format: file headers must use a/ and "
                "b/ prefixes (`diff --git a/path b/path`, `--- a/path`, `+++ b/path`). "
                "Offending headers: " + ", ".join(sorted(set(bad))[:5])
            )

    if not paths:
        raise MaintainError(
            "No file paths could be extracted from the patch; it does not look like a "
            "unified Git diff."
        )
    return sorted(paths)


# Repository context --------------------------------------------------------


def project_context(root: Path) -> str:
    path = maintain_dir(root) / "project-context.md"
    if path.exists():
        content = path.read_text(encoding="utf-8").strip()
        if content:
            return content
    return "(no project context provided)"


def repo_structure(root: Path) -> str:
    lines = git_out(root, "ls-files").splitlines()
    if len(lines) > 800:
        remainder = len(lines) - 800
        lines = lines[:800] + [f"... ({remainder} more files not listed)"]
    return "```\n" + "\n".join(lines) + "\n```"


def run_repomix(root: Path, config: dict, include: Optional[list] = None) -> str:
    handle, tmp_name = tempfile.mkstemp(prefix="maintain-repomix-", suffix=".md")
    os.close(handle)
    tmp_path = Path(tmp_name)
    command = [
        "repomix",
        "--output",
        str(tmp_path),
        "--style",
        "markdown",
        "--quiet",
        "--ignore",
        f"{APP_DIR_NAME}/**",
    ]
    extra = config.get("repomix_args") or []
    if isinstance(extra, list):
        command.extend(str(arg) for arg in extra)
    if include:
        command.extend(["--include", ",".join(include)])
    try:
        proc = run(command, cwd=root)
    except FileNotFoundError:
        tmp_path.unlink(missing_ok=True)
        raise MaintainError(
            "Repomix is not installed or not on PATH.",
            "Install Node.js, then run `npm install -g repomix`, and retry.",
        )
    if proc.returncode != 0:
        tmp_path.unlink(missing_ok=True)
        raise MaintainError(
            f"Repomix failed (exit {proc.returncode}):\n{(proc.stderr or proc.stdout).strip()}",
            "Fix the Repomix problem and run `maintain next` to retry the package.",
        )
    try:
        content = tmp_path.read_text(encoding="utf-8", errors="replace").strip()
    finally:
        tmp_path.unlink(missing_ok=True)
    if not content:
        raise MaintainError("Repomix produced no output.")
    return content


def repomix_for_allowed_files(root: Path, config: dict, allowed: list) -> str:
    existing = [path for path in allowed if (root / path).exists() and "," not in path]
    if not existing:
        return (
            "(None of the allowed files exist in the repository yet; "
            "they are all new files to be created.)"
        )
    return run_repomix(root, config, include=existing)


def cumulative_diff(task: Task) -> str:
    return git_out(task.root, "diff", task.base_commit, "--")


def changed_files(task: Task) -> list:
    output = git_out(task.root, "diff", "--name-only", task.base_commit, "--")
    return [line.strip() for line in output.splitlines() if line.strip()]


def wrapped_diff(diff: str) -> str:
    if not diff.strip():
        return "(no changes have been applied in this task yet)"
    return f"````diff\n{diff.rstrip()}\n````"


def wrapped_output(text: str) -> str:
    return f"````\n{text.rstrip()}\n````"


def files_snapshot(task: Task, files: list) -> str:
    if not files:
        return "(no files have changed yet)"
    parts = []
    for name in files:
        path = task.root / name
        if not path.exists():
            parts.append(f"### {name}\n\n(deleted by this task)")
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            parts.append(f"### {name}\n\n(binary or unreadable file)")
            continue
        parts.append(f"### {name}\n\n````\n{body.rstrip()}\n````")
    return "\n\n".join(parts)


# Package generation --------------------------------------------------------


def render_template(name: str, mapping: dict) -> str:
    template = (TEMPLATE_DIR / name).read_text(encoding="utf-8")
    return re.sub(
        r"\{\{(\w+)\}\}",
        lambda match: str(mapping.get(match.group(1), match.group(0))),
        template,
    )


def write_export(task: Task, export_name: str, content: str) -> Path:
    task.exports_dir.mkdir(parents=True, exist_ok=True)
    export_path = task.exports_dir / export_name
    export_path.write_text(content, encoding="utf-8")
    task.state["current_export"] = task.rel(export_path)
    return export_path


def announce_package(task: Task, export_path: Path, note: str) -> None:
    say(note)
    say(f"Package: {task.rel(export_path)}")
    say(
        "Next: Upload the package to a fresh chatbot conversation, copy the complete "
        "reply, then run `maintain capture`."
    )


def base_mapping(task: Task) -> dict:
    return {
        "task_id": task.id,
        "base_commit": task.base_commit[:10],
        "request": task.request_text(),
        "scope_revision": str(task.state.get("scope_revision", 1)),
        "maximum_rounds": str(task.maximum_rounds),
        "approved_scope": task.state.get("scope_summary") or "(no scope summary captured)",
        "acceptance_criteria": task.state.get("acceptance_criteria")
        or "(no acceptance criteria captured)",
        "allowed_files": "\n".join(f"- {path}" for path in task.allowed_files)
        or "(no allowed files)",
    }


def build_scope_package(task: Task, config: dict) -> Path:
    mapping = base_mapping(task)
    mapping.update(
        {
            "project_context": project_context(task.root),
            "repo_structure": repo_structure(task.root),
            "repomix_context": run_repomix(task.root, config),
        }
    )
    content = render_template("scope.md", mapping)
    task.scope_dir.mkdir(parents=True, exist_ok=True)
    (task.scope_dir / "package.md").write_text(content, encoding="utf-8")
    return write_export(task, f"maintain-{task.id}-scope.md", content)


def build_implementation_package(task: Task, config: dict, round_number: int) -> str:
    mapping = base_mapping(task)
    mapping.update(
        {
            "round": str(round_number),
            "cumulative_diff": wrapped_diff(cumulative_diff(task)),
            "repomix_context": repomix_for_allowed_files(task.root, config, task.allowed_files),
        }
    )
    return render_template("implement.md", mapping)


def latest_feedback(task: Task) -> str:
    kind = task.state.get("last_failure")
    round_dir = task.round_dir(task.implementation_round)
    if kind == "patch":
        error = task.state.get("last_error") or "(no error recorded)"
        return (
            "The previous patch failed validation and was NOT applied to the "
            "repository:\n\n" + wrapped_output(error)
        )
    if kind == "tests":
        results = round_dir / "test-results.txt"
        output = (
            results.read_text(encoding="utf-8") if results.exists() else "(no test output recorded)"
        )
        return "Tests failed after the previous patch was applied. Recorded output:\n\n" + wrapped_output(output)
    if kind == "review":
        response = round_dir / "review-response.md"
        text = (
            response.read_text(encoding="utf-8")
            if response.exists()
            else "(review response missing)"
        )
        return "The independent reviewer returned CHANGES_REQUIRED. Full review:\n\n" + text.strip()
    return "(no failure details were recorded)"


def latest_implementation_summary(task: Task) -> str:
    for number in range(task.implementation_round, 0, -1):
        response = task.round_dir(number) / "implementation-response.md"
        if response.exists():
            text = strip_diff_blocks(response.read_text(encoding="utf-8")).strip()
            return f"From round {number:02d}:\n\n{text}"
    return "(no previous implementation response)"


def implementation_summaries(task: Task) -> str:
    parts = []
    for number in range(1, task.implementation_round + 1):
        response = task.round_dir(number) / "implementation-response.md"
        if response.exists():
            text = strip_diff_blocks(response.read_text(encoding="utf-8")).strip()
            parts.append(f"### Round {number:02d}\n\n{text}")
    return "\n\n".join(parts) or "(no implementation responses recorded)"


def build_fix_package(task: Task, config: dict, round_number: int) -> str:
    mapping = base_mapping(task)
    mapping.update(
        {
            "round": str(round_number),
            "cumulative_diff": wrapped_diff(cumulative_diff(task)),
            "repomix_context": repomix_for_allowed_files(task.root, config, task.allowed_files),
            "feedback": latest_feedback(task),
            "previous_summary": latest_implementation_summary(task),
        }
    )
    return render_template("fix.md", mapping)


def build_review_package(task: Task, config: dict) -> str:
    round_dir = task.round_dir(task.implementation_round)
    results = round_dir / "test-results.txt"
    test_results = (
        wrapped_output(results.read_text(encoding="utf-8"))
        if results.exists()
        else "(no test results recorded)"
    )
    files = changed_files(task)
    mapping = base_mapping(task)
    mapping.update(
        {
            "round": str(task.implementation_round),
            "cumulative_diff": wrapped_diff(cumulative_diff(task)),
            "changed_files": files_snapshot(task, files),
            "test_results": test_results,
            "implementation_summaries": implementation_summaries(task),
        }
    )
    return render_template("review.md", mapping)


def build_rescope_package(task: Task, config: dict) -> str:
    trigger = task.state.get("rescope_trigger") or {}
    trigger_path = task.dir / trigger.get("path", "") if trigger.get("path") else None
    if trigger_path and trigger_path.exists():
        trigger_text = trigger_path.read_text(encoding="utf-8").strip()
        source = trigger.get("source", "unknown")
        label = {
            "implementation": "Reported by the implementer",
            "review": "Reported by the independent reviewer",
        }.get(source, "Reported")
        trigger_content = f"{label}:\n\n{strip_diff_blocks(trigger_text)}"
    else:
        trigger_content = "(no trigger details recorded)"

    scope_source = task.state.get("scope_source")
    scope_path = task.dir / scope_source if scope_source else None
    current_scope = (
        scope_path.read_text(encoding="utf-8").strip()
        if scope_path and scope_path.exists()
        else "(current scope response missing)"
    )

    mapping = base_mapping(task)
    mapping.update(
        {
            "project_context": project_context(task.root),
            "current_scope": current_scope,
            "trigger": trigger_content,
            "cumulative_diff": wrapped_diff(cumulative_diff(task)),
            "repo_structure": repo_structure(task.root),
            "repomix_context": run_repomix(task.root, config),
        }
    )
    return render_template("rescope.md", mapping)


# Capture validation and state transitions ----------------------------------


def validate_scope_response(task: Task, text: str) -> dict:
    if not find_marker(text, "STATUS", ["SCOPE_COMPLETE"]):
        raise MaintainError(
            'The response marker is missing: expected a line "STATUS: SCOPE_COMPLETE".'
        )
    allowed_body = extract_section(text, "Allowed Files")
    if allowed_body is None:
        raise MaintainError('The required section "## Allowed Files" is missing.')
    warnings = []
    allowed = []
    for path in parse_path_bullets(allowed_body):
        reason = unsafe_path_reason(path)
        if reason:
            warnings.append(f"warning: ignored allowed file {path!r} ({reason})")
        else:
            allowed.append(path)
    if not allowed:
        raise MaintainError('The "## Allowed Files" section lists no usable file paths.')
    criteria = extract_section(text, "Acceptance Criteria")
    if not criteria:
        raise MaintainError('The required section "## Acceptance Criteria" is missing or empty.')
    understanding = extract_section(text, "Understanding") or ""
    proposed = extract_section(text, "Proposed Changes") or ""
    for name, value in (("Understanding", understanding), ("Proposed Changes", proposed)):
        if not value:
            warnings.append(f'warning: section "## {name}" is missing or empty.')
    if extract_section(text, "Risks and Unknowns") is None:
        warnings.append('warning: section "## Risks and Unknowns" is missing.')
    summary_parts = []
    if understanding:
        summary_parts.append(f"### Understanding\n\n{understanding}")
    if proposed:
        summary_parts.append(f"### Proposed changes\n\n{proposed}")
    return {
        "allowed": allowed,
        "criteria": criteria.strip(),
        "summary": "\n\n".join(summary_parts) or "(no scope summary provided)",
        "warnings": warnings,
    }


def commit_scope_response(task: Task, data: dict, target: Path) -> list:
    task.state.update(
        {
            "allowed_files": data["allowed"],
            "acceptance_criteria": data["criteria"],
            "scope_summary": data["summary"],
            "scope_source": task.rel(target).replace(
                task.rel(task.dir) + "/", "", 1
            ),
            "stage": S_SCOPE_CAPTURED,
        }
    )
    messages = list(data["warnings"])
    messages.append("Scope response captured.")
    messages.append(f"Allowed files ({len(data['allowed'])}):")
    messages.extend(f"  - {path}" for path in data["allowed"])
    messages.append(
        "Review the scope yourself before continuing — Maintain does not approve it for you."
    )
    messages.append("Next: Run `maintain next` to generate the implementation package.")
    return messages


def validate_implementation_response(task: Task, text: str) -> dict:
    status = find_marker(text, "STATUS", ["IMPLEMENTATION_COMPLETE", "RESCOPE_REQUIRED"])
    if not status:
        raise MaintainError(
            "The response marker is missing: expected a line "
            '"STATUS: IMPLEMENTATION_COMPLETE" or "STATUS: RESCOPE_REQUIRED".'
        )
    if status == "RESCOPE_REQUIRED":
        return {"kind": "rescope"}
    blocks = extract_diff_blocks(text)
    if not blocks:
        raise MaintainError(
            "No diff block is present in the implementation response. The response must "
            "contain exactly one fenced ```diff block with a unified Git diff."
        )
    if len(blocks) > 1:
        raise MaintainError(
            f"More than one diff block is present ({len(blocks)} found). The response "
            "must contain exactly one fenced ```diff block."
        )
    patch = normalise_patch(blocks[0])
    if not patch.endswith("\n"):
        patch += "\n"
    paths = patch_paths(patch)  # validates basic diff shape early
    return {"kind": "patch", "patch": patch, "paths": paths}


def commit_implementation_response(task: Task, data: dict, target: Path) -> list:
    round_number = task.implementation_round
    if data["kind"] == "rescope":
        task.state.update(
            {
                "stage": S_RESCOPE_NEEDED,
                "rescope_trigger": {
                    "source": "implementation",
                    "round": round_number,
                    "path": task.rel(target).replace(task.rel(task.dir) + "/", "", 1),
                },
            }
        )
        return [
            f"The implementer requested a rescope (round {round_number}).",
            "Next: Run `maintain next` to generate the rescope package.",
        ]
    patch_file = task.round_dir(round_number) / "implementation.patch"
    if patch_file.exists():
        patch_file.rename(next_free(patch_file, "superseded"))
    patch_file.write_text(data["patch"], encoding="utf-8")
    task.state["stage"] = S_IMPL_CAPTURED
    return [
        f"Implementation response captured (round {round_number}).",
        f"Patch: {task.rel(patch_file)} ({len(data['paths'])} file(s): "
        + ", ".join(data["paths"])
        + ")",
        "Next: Run `maintain apply` to validate, apply and test the patch.",
    ]


def validate_review_response(task: Task, text: str) -> dict:
    verdict = find_marker(text, "VERDICT", ["APPROVED", "APPROVE", "CHANGES_REQUIRED", "RESCOPE"])
    if not verdict:
        raise MaintainError(
            "The response marker is missing: expected a line "
            '"VERDICT: APPROVE", "VERDICT: CHANGES_REQUIRED" or "VERDICT: RESCOPE".'
        )
    if verdict.startswith("APPROVE"):
        verdict = "APPROVE"
    warnings = []
    for section in ("Findings", "Acceptance-Criteria Coverage", "Risks"):
        if extract_section(text, section) is None:
            warnings.append(f'warning: review section "## {section}" is missing.')
    return {"verdict": verdict, "warnings": warnings}


def commit_review_response(task: Task, data: dict, target: Path) -> list:
    verdict = data["verdict"]
    task.state["review_verdict"] = verdict
    messages = list(data["warnings"])
    if verdict == "APPROVE":
        task.state["stage"] = S_REVIEW_APPROVED
        messages.append("Review verdict: APPROVE.")
        messages.append("Next: Run `maintain next` to close the task.")
    elif verdict == "CHANGES_REQUIRED":
        task.state["stage"] = S_REVIEW_CHANGES
        task.state["last_failure"] = "review"
        messages.append("Review verdict: CHANGES_REQUIRED.")
        messages.append("Next: Run `maintain next` to generate a correction package.")
    else:  # RESCOPE
        task.state.update(
            {
                "stage": S_RESCOPE_NEEDED,
                "rescope_trigger": {
                    "source": "review",
                    "round": task.implementation_round,
                    "path": task.rel(target).replace(task.rel(task.dir) + "/", "", 1),
                },
            }
        )
        messages.append("Review verdict: RESCOPE.")
        messages.append("Next: Run `maintain next` to generate the rescope package.")
    return messages


def validate_rescope_response(task: Task, text: str) -> dict:
    if not find_marker(text, "STATUS", ["RESCOPED"]):
        raise MaintainError(
            'The response marker is missing: expected a line "STATUS: RESCOPED".'
        )
    work = find_marker(text, "EXISTING_WORK", ["RETAIN", "PARTIAL", "DISCARD"])
    if not work:
        raise MaintainError(
            "The response marker is missing: expected a line "
            '"EXISTING_WORK: RETAIN", "EXISTING_WORK: PARTIAL" or "EXISTING_WORK: DISCARD".'
        )
    allowed_body = extract_section(text, "Revised Allowed Files")
    if allowed_body is None:
        raise MaintainError('The required section "## Revised Allowed Files" is missing.')
    warnings = []
    allowed = []
    for path in parse_path_bullets(allowed_body):
        reason = unsafe_path_reason(path)
        if reason:
            warnings.append(f"warning: ignored allowed file {path!r} ({reason})")
        else:
            allowed.append(path)
    if not allowed:
        raise MaintainError('The "## Revised Allowed Files" section lists no usable file paths.')
    criteria = extract_section(text, "Revised Acceptance Criteria")
    if not criteria:
        raise MaintainError(
            'The required section "## Revised Acceptance Criteria" is missing or empty.'
        )
    understanding = extract_section(text, "Revised Understanding") or ""
    plan = extract_section(text, "Revised Plan") or ""
    assessment = extract_section(text, "Existing Work Assessment") or ""
    for name, value in (
        ("Revised Understanding", understanding),
        ("Revised Plan", plan),
        ("Existing Work Assessment", assessment),
    ):
        if not value:
            warnings.append(f'warning: section "## {name}" is missing or empty.')
    summary_parts = []
    if understanding:
        summary_parts.append(f"### Understanding\n\n{understanding}")
    if plan:
        summary_parts.append(f"### Plan\n\n{plan}")
    return {
        "work": work,
        "allowed": allowed,
        "criteria": criteria.strip(),
        "summary": "\n\n".join(summary_parts) or "(no revised scope summary provided)",
        "assessment": assessment,
        "warnings": warnings,
    }


def reset_task_changes(task: Task) -> None:
    """Restore every file changed since the base commit to its base state."""
    for name in changed_files(task):
        exists_in_base = (
            git_run(task.root, "cat-file", "-e", f"{task.base_commit}:{name}").returncode == 0
        )
        if exists_in_base:
            git_out(task.root, "checkout", task.base_commit, "--", name)
        else:
            git_run(task.root, "reset", "-q", "--", name)
            path = task.root / name
            if path.exists():
                path.unlink()


def commit_rescope_response(task: Task, data: dict, target: Path) -> list:
    task.state.update(
        {
            "allowed_files": data["allowed"],
            "acceptance_criteria": data["criteria"],
            "scope_summary": data["summary"],
            "scope_source": task.rel(target).replace(task.rel(task.dir) + "/", "", 1),
            "scope_revision": int(task.state.get("scope_revision", 1)) + 1,
            "rounds_this_scope": 0,
            "review_verdict": None,
            "test_status": None,
            "last_error": None,
            "last_failure": None,
            "stage": S_RESCOPED,
        }
    )
    messages = list(data["warnings"])
    diff = cumulative_diff(task)
    if diff.strip():
        say(f"Rescope recommendation: EXISTING_WORK: {data['work']}")
        if data["assessment"]:
            say("Existing work assessment:")
            say(data["assessment"])
        choice = ask_keep_or_reset(data["work"])
        if choice == "reset":
            reset_task_changes(task)
            task.state["provisional_changes"] = False
            messages.append("Task changes were reset to the base commit.")
        else:
            task.state["provisional_changes"] = True
            messages.append("Current task changes were kept in the working tree.")
    else:
        messages.append("No task changes are present in the working tree; nothing to retain or reset.")
    messages.append(
        f"Scope revised (revision {task.state['scope_revision']}). "
        f"Allowed files: {len(data['allowed'])}."
    )
    messages.append(
        "Next: Run `maintain next` to generate the implementation package for the revised scope."
    )
    return messages


# Commands ------------------------------------------------------------------


def cmd_init() -> None:
    root = find_repo_root()
    directory = maintain_dir(root)
    if directory.exists():
        raise MaintainError(
            f"Maintain is already initialised at {directory}.",
            "Edit .maintain/config.json and .maintain/project-context.md as needed.",
        )
    (directory / "tasks").mkdir(parents=True)
    (directory / "config.json").write_text(
        json.dumps(DEFAULT_CONFIG, indent=2) + "\n", encoding="utf-8"
    )
    (directory / "project-context.md").write_text(PROJECT_CONTEXT_STARTER, encoding="utf-8")
    (directory / "current-task").write_text("", encoding="utf-8")
    say(f"Initialised Maintain in {directory}")
    say("")
    say("Created:")
    say("  .maintain/config.json         — set \"test_command\" (for example \"pytest\")")
    say("  .maintain/project-context.md  — describe project rules and conventions")
    say("  .maintain/current-task")
    say("  .maintain/tasks/")
    say("")
    if not shutil.which("repomix"):
        say("warning: Repomix was not found on PATH. Install Node.js and run")
        say("         `npm install -g repomix` before creating a task.")
        say("")
    say("Consider adding .maintain/ to .gitignore if you do not want task")
    say("artifacts in version control.")
    say("")
    say('Next: Edit the two files above, then run `maintain new "<request>"`.')


def make_task_id(root: Path) -> str:
    today = datetime.now().strftime("%Y%m%d")
    tasks = maintain_dir(root) / "tasks"
    sequence = 0
    if tasks.is_dir():
        for entry in tasks.iterdir():
            match = re.match(rf"^{today}-(\d+)$", entry.name)
            if match:
                sequence = max(sequence, int(match.group(1)))
    return f"{today}-{sequence + 1:03d}"


def cmd_new(request: str) -> None:
    root = find_repo_root()
    require_initialised(root)
    config = load_config(root)

    active = current_task_id(root)
    if active:
        existing = Task(root, active)
        if existing.state and existing.stage != S_COMPLETE:
            raise MaintainError(
                f"Task {active} is still active ({stage_label(existing)}). "
                "Maintain supports one active task at a time.",
                "Finish the active task first, or clear .maintain/current-task to abandon it.",
            )

    head = git_run(root, "rev-parse", "HEAD")
    if head.returncode != 0:
        raise MaintainError(
            "The repository has no commits yet. Maintain records the current commit as "
            "the task base.",
            "Create an initial commit and run the command again.",
        )
    base_commit = head.stdout.strip()

    dirty = [
        line
        for line in git_out(root, "status", "--porcelain").splitlines()
        if line.strip() and not line[3:].startswith(APP_DIR_NAME)
    ]
    if dirty:
        say("The working tree has uncommitted changes:")
        for line in dirty[:10]:
            say(f"  {line}")
        if len(dirty) > 10:
            say(f"  ... and {len(dirty) - 10} more")
        say("Packages will describe the repository as it is now, and patches will be")
        say("validated against it.")
        if not confirm("Continue creating the task?"):
            raise MaintainError("Task creation aborted.", "Commit or stash your changes first.")

    task = Task(root, make_task_id(root))
    task.dir.mkdir(parents=True)
    task.request_file.write_text(request.strip() + "\n", encoding="utf-8")
    task.state = {
        "task_id": task.id,
        "created": datetime.now().isoformat(timespec="seconds"),
        "stage": S_AWAIT_SCOPE,
        "base_commit": base_commit,
        "implementation_round": 0,
        "rounds_this_scope": 0,
        "maximum_rounds": int(config.get("maximum_rounds") or 3),
        "scope_revision": 1,
        "allowed_files": [],
        "acceptance_criteria": "",
        "scope_summary": "",
        "scope_source": None,
        "test_status": None,
        "review_verdict": None,
        "provisional_changes": False,
        "last_error": None,
        "last_failure": None,
        "rescope_trigger": None,
        "current_export": None,
    }
    task.save()
    set_current_task(root, task.id)

    try:
        export_path = build_scope_package(task, config)
    except MaintainError as exc:
        task.save()
        raise MaintainError(
            f"The task was created, but the scope package could not be generated:\n{exc}",
            (exc.next_action or "")
            + " Then run `maintain next` to retry generating the scope package.",
        )
    task.save()

    say(f"Created task: {task.id}")
    say(f"Package: {task.rel(export_path)}")
    say(
        "Next: Upload the package to a fresh chatbot conversation, copy the complete "
        "reply, then run `maintain capture`."
    )


def cmd_capture() -> None:
    root = find_repo_root()
    require_initialised(root)
    task = load_active_task(root)

    handlers = {
        S_AWAIT_SCOPE: (
            task.scope_dir / "response.md",
            validate_scope_response,
            commit_scope_response,
        ),
        S_AWAIT_IMPL: (
            task.round_dir(task.implementation_round) / "implementation-response.md",
            validate_implementation_response,
            commit_implementation_response,
        ),
        S_AWAIT_REVIEW: (
            task.round_dir(task.implementation_round) / "review-response.md",
            validate_review_response,
            commit_review_response,
        ),
        S_AWAIT_RESCOPE: (
            task.rescope_dir(int(task.state.get("current_rescope") or 1)) / "response.md",
            validate_rescope_response,
            commit_rescope_response,
        ),
    }
    handler = handlers.get(task.stage)
    if handler is None:
        raise MaintainError(
            f"No chatbot response is expected right now (stage: {stage_label(task)}).",
            stage_next_action(task),
        )
    target, validate, commit = handler

    text = read_clipboard()
    if not text or not text.strip():
        raise MaintainError(
            "The clipboard is empty.",
            "Copy the chatbot's complete reply to the clipboard and run `maintain capture` again.",
        )
    text = normalise_text(text)

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        preserved = next_free(target, "superseded")
        target.rename(preserved)
        say(f"note: the existing response was preserved as {task.rel(preserved)}")
    target.write_text(text, encoding="utf-8")  # store the raw response before parsing

    try:
        data = validate(task, text)
    except MaintainError as exc:
        rejected = next_free(target, "rejected")
        target.rename(rejected)
        raise MaintainError(
            str(exc),
            f"The captured text was preserved at {task.rel(rejected)}. Copy a corrected "
            "response to the clipboard and run `maintain capture` again.",
        )

    messages = commit(task, data, target)
    task.save()
    for message in messages:
        say(message)


def start_round(task: Task, config: dict, kind: str) -> None:
    number = task.implementation_round + 1
    round_dir = task.round_dir(number)
    round_dir.mkdir(parents=True, exist_ok=True)
    if kind == "implementation":
        content = build_implementation_package(task, config, number)
        package_path = round_dir / "implementation-package.md"
        export_name = f"maintain-{task.id}-implement-{number:02d}.md"
        note = f"Generated implementation package (round {number})."
    else:
        content = build_fix_package(task, config, number)
        package_path = round_dir / "fix-package.md"
        export_name = f"maintain-{task.id}-fix-{number:02d}.md"
        note = (
            f"Generated correction package (round {number}; "
            f"{task.rounds_this_scope + 1} of {task.maximum_rounds} for this scope)."
        )
    package_path.write_text(content, encoding="utf-8")
    export_path = write_export(task, export_name, content)
    task.state.update(
        {
            "implementation_round": number,
            "rounds_this_scope": task.rounds_this_scope + 1,
            "stage": S_AWAIT_IMPL,
            "last_error": None,
        }
    )
    task.save()
    announce_package(task, export_path, note)


def generate_review_package(task: Task, config: dict) -> None:
    number = task.implementation_round
    round_dir = task.round_dir(number)
    round_dir.mkdir(parents=True, exist_ok=True)
    content = build_review_package(task, config)
    (round_dir / "review-package.md").write_text(content, encoding="utf-8")
    export_path = write_export(task, f"maintain-{task.id}-review-{number:02d}.md", content)
    task.state["stage"] = S_AWAIT_REVIEW
    task.save()
    announce_package(
        task,
        export_path,
        f"Generated independent review package (round {number}). Use a fresh "
        "conversation — the reviewer must not share context with the implementer.",
    )


def generate_rescope_package(task: Task, config: dict) -> None:
    number = 1
    if task.rescopes_dir.is_dir():
        existing = [entry for entry in task.rescopes_dir.iterdir() if entry.is_dir()]
        number = len(existing) + 1
    rescope_dir = task.rescope_dir(number)
    rescope_dir.mkdir(parents=True, exist_ok=True)
    content = build_rescope_package(task, config)
    (rescope_dir / "package.md").write_text(content, encoding="utf-8")
    export_path = write_export(task, f"maintain-{task.id}-rescope-{number:02d}.md", content)
    task.state.update({"current_rescope": number, "stage": S_AWAIT_RESCOPE})
    task.save()
    announce_package(task, export_path, f"Generated rescope package ({number:02d}).")


def stop_at_round_limit(task: Task) -> None:
    task.state["stage"] = S_LIMIT_REACHED
    task.save()
    say("Maximum implementation rounds reached.")
    say("The task requires manual intervention.")
    say("No further package has been generated.")
    say("")
    say(f"Rounds used for this scope: {task.rounds_this_scope} of {task.maximum_rounds}.")
    say("Options: finish the change by hand, or raise \"maximum_rounds\" in")
    say(f"{task.rel(task.state_file)} and run `maintain next`.")


def close_task(task: Task) -> None:
    if not confirm("The reviewer approved the change. Close the task?"):
        say("Task left open. Run `maintain next` when you are ready to close it.")
        return
    task.state["stage"] = S_COMPLETE
    task.save()
    set_current_task(task.root, None)
    say(f"Task {task.id} complete after {task.implementation_round} implementation round(s).")
    if task.state.get("provisional_changes"):
        say("The applied changes remain uncommitted in your working tree. Review and")
        say("commit them yourself — Maintain does not create commits.")
    say(f"All packages, responses, patches and results are preserved under {task.rel(task.dir)}.")


def cmd_next() -> None:
    root = find_repo_root()
    require_initialised(root)
    config = load_config(root)
    task = load_active_task(root)
    stage = task.stage

    if stage == S_AWAIT_SCOPE:
        if not (task.scope_dir / "package.md").exists():
            export_path = build_scope_package(task, config)
            task.save()
            announce_package(task, export_path, "Generated scope package.")
        else:
            say(f"Waiting for the scope response. {stage_next_action(task)}")
            if task.state.get("current_export"):
                say(f"Package: {task.state['current_export']}")
    elif stage in (S_SCOPE_CAPTURED, S_RESCOPED):
        start_round(task, config, "implementation")
    elif stage in (S_PATCH_REJECTED, S_TESTS_FAILED, S_REVIEW_CHANGES, S_LIMIT_REACHED):
        if task.rounds_this_scope >= task.maximum_rounds:
            stop_at_round_limit(task)
        else:
            start_round(task, config, "fix")
    elif stage == S_READY_FOR_REVIEW:
        generate_review_package(task, config)
    elif stage == S_RESCOPE_NEEDED:
        generate_rescope_package(task, config)
    elif stage == S_REVIEW_APPROVED:
        close_task(task)
    elif stage in (S_AWAIT_IMPL, S_AWAIT_REVIEW, S_AWAIT_RESCOPE):
        say(f"{stage_label(task)}. {stage_next_action(task)}")
        if task.state.get("current_export"):
            say(f"Package: {task.state['current_export']}")
    elif stage == S_IMPL_CAPTURED:
        say(f"A patch is already captured. {stage_next_action(task)}")
    elif stage == S_COMPLETE:
        say(f"Task {task.id} is complete. {STAGE_NEXT_ACTIONS[S_COMPLETE]}")
    else:
        raise MaintainError(f"Unknown task stage: {stage!r}.", "Inspect the task's state.json.")


def record_patch_failure(task: Task, message: str) -> None:
    task.state.update(
        {
            "last_error": message[:8000],
            "last_failure": "patch",
            "stage": S_PATCH_REJECTED,
        }
    )
    task.save()
    raise MaintainError(
        message,
        "The patch was NOT applied. Run `maintain next` to generate a correction "
        "package that includes this error.",
    )


def run_tests(task: Task, config: dict) -> None:
    round_dir = task.round_dir(task.implementation_round)
    results_path = round_dir / "test-results.txt"
    command = config.get("test_command")
    timestamp = datetime.now().isoformat(timespec="seconds")

    if not command:
        results_path.write_text(
            "Test status: NOT_CONFIGURED\n"
            f"Recorded: {timestamp}\n\n"
            "No test command is configured in .maintain/config.json.\n",
            encoding="utf-8",
        )
        task.state.update({"test_status": "not_configured", "stage": S_READY_FOR_REVIEW})
        say("")
        say("warning: no test command is configured; the result was recorded as")
        say('NOT_CONFIGURED. Set "test_command" in .maintain/config.json to run tests.')
        say("Next: Run `maintain next` to generate the review package.")
        return

    say(f"Running tests: {command}")
    try:
        proc = run_shell_combined(command, task.root)
        exit_code = proc.returncode
        output = proc.stdout or ""
    except OSError as exc:
        exit_code = -1
        output = f"The test command could not run: {exc}"
    results_path.write_text(
        f"Command: {command}\nExit code: {exit_code}\nRecorded: {timestamp}\n\n{output}",
        encoding="utf-8",
    )
    tail = output.strip().splitlines()[-25:]
    if tail:
        say("--- test output (tail) ---")
        for line in tail:
            say(line)
        say("--------------------------")
    say(f"Full output: {task.rel(results_path)}")

    if exit_code == 0:
        task.state.update({"test_status": "passed", "stage": S_READY_FOR_REVIEW})
        say("Tests: PASSED")
        say("Next: Run `maintain next` to generate the review package.")
    else:
        task.state.update(
            {"test_status": "failed", "stage": S_TESTS_FAILED, "last_failure": "tests"}
        )
        say(f"Tests: FAILED (exit code {exit_code})")
        say("Next: Run `maintain next` to generate a correction package.")


def cmd_apply() -> None:
    root = find_repo_root()
    require_initialised(root)
    config = load_config(root)
    task = load_active_task(root)

    if task.stage != S_IMPL_CAPTURED:
        raise MaintainError(
            f"There is no captured patch to apply (stage: {stage_label(task)}).",
            stage_next_action(task),
        )
    round_number = task.implementation_round
    patch_file = task.round_dir(round_number) / "implementation.patch"
    if not patch_file.exists():
        raise MaintainError(
            f"The patch file is missing: {task.rel(patch_file)}.",
            "Capture the implementation response again with `maintain capture`.",
        )
    patch_text = patch_file.read_text(encoding="utf-8")

    # 1. Verify the repository is still compatible with the recorded base state.
    head = git_out(root, "rev-parse", "HEAD").strip()
    if head != task.base_commit:
        say(
            f"warning: HEAD ({head[:10]}) is no longer the recorded base commit "
            f"({task.base_commit[:10]}). The patch was produced against the base state."
        )
        if not confirm("Continue anyway?"):
            raise MaintainError(
                "Apply aborted: the working tree changed incompatibly.",
                f"Return the repository to commit {task.base_commit[:10]} (plus this "
                "task's changes), or abandon the task and create a new one.",
            )

    # 2. Extract the changed file paths.
    try:
        files = patch_paths(patch_text)
    except MaintainError as exc:
        record_patch_failure(task, str(exc))
        return

    unsafe = [(f, unsafe_path_reason(f)) for f in files if unsafe_path_reason(f)]
    if unsafe:
        record_patch_failure(
            task,
            "The patch touches unsafe paths: "
            + "; ".join(f"{name} ({reason})" for name, reason in unsafe),
        )
        return

    # 3. Reject files outside the approved scope.
    allowed = set(task.allowed_files)
    disallowed = [f for f in files if f not in allowed]
    if disallowed:
        record_patch_failure(
            task,
            "The patch changes files outside the approved scope.\n"
            "Disallowed: " + ", ".join(disallowed) + "\n"
            "Approved files: " + (", ".join(sorted(allowed)) or "(none)"),
        )
        return

    # 4. Check that the patch applies cleanly.
    check = git_run(root, "apply", "--recount", "--check", str(patch_file))
    if check.returncode != 0:
        record_patch_failure(
            task,
            "git apply --check failed; the patch does not apply to the current "
            "working tree:\n" + (check.stderr or check.stdout).strip(),
        )
        return

    # 5. Display a patch summary.
    say(f"Patch for round {round_number} — {len(files)} file(s), all within the approved scope:")
    for name in files:
        say(f"  - {name}")
    stat = git_run(root, "apply", "--recount", "--stat", str(patch_file))
    if stat.returncode == 0 and stat.stdout.strip():
        say(stat.stdout.rstrip())
    say("git apply --check passed.")

    # 6. Request confirmation.
    if not confirm("Apply this patch to the working tree?"):
        say("Patch not applied. Nothing was changed.")
        say("Run `maintain apply` again when you are ready.")
        return

    # 7. Apply the patch.
    apply_proc = git_run(root, "apply", "--recount", str(patch_file))
    if apply_proc.returncode != 0:
        record_patch_failure(
            task,
            "git apply failed:\n" + (apply_proc.stderr or apply_proc.stdout).strip(),
        )
        return
    say(f"Patch applied ({len(files)} file(s)).")

    # Track new files so the cumulative task diff includes them.
    untracked = set(
        git_out(root, "ls-files", "--others", "--exclude-standard").splitlines()
    )
    for name in files:
        if name in untracked:
            git_run(root, "add", "-N", "--", name)

    task.state["provisional_changes"] = True
    task.state["last_failure"] = None

    # 8-9. Run the configured test command and record the complete output.
    run_tests(task, config)
    task.save()


def cmd_status() -> None:
    root = find_repo_root()
    require_initialised(root)
    task_id = current_task_id(root)
    if not task_id:
        say("No active task.")
        say('Next action: Create one with `maintain new "<request>"`.')
        return
    task = Task(root, task_id)
    if not task.state:
        raise MaintainError(
            f"The active task {task_id} has no readable state.",
            "Clear .maintain/current-task or restore the task's state.json.",
        )

    tests_display = {
        "passed": "Passed",
        "failed": "Failed",
        "not_configured": "NOT_CONFIGURED",
        None: "Not run",
    }.get(task.state.get("test_status"), str(task.state.get("test_status")))

    say(f"Task: {task.id}")
    say(f"Request: {task.request_text().splitlines()[0] if task.request_text() else ''}")
    say(f"Stage: {stage_label(task)}")
    say(f"Implementation round: {task.rounds_this_scope} of {task.maximum_rounds}")
    if task.implementation_round != task.rounds_this_scope:
        say(f"Total rounds across scope revisions: {task.implementation_round}")
    say(f"Scope revision: {task.state.get('scope_revision', 1)}")
    say(f"Tests: {tests_display}")
    say(f"Review verdict: {task.state.get('review_verdict') or '—'}")
    if task.stage in (S_AWAIT_SCOPE, S_AWAIT_IMPL, S_AWAIT_REVIEW, S_AWAIT_RESCOPE):
        if task.state.get("current_export"):
            say(f"Package to upload: {task.state['current_export']}")
    head = git_run(root, "rev-parse", "HEAD")
    if head.returncode == 0 and head.stdout.strip() != task.base_commit and task.stage != S_COMPLETE:
        say(
            f"note: HEAD ({head.stdout.strip()[:10]}) differs from the task base commit "
            f"({task.base_commit[:10]})."
        )
    say(f"Next action: {stage_next_action(task)}")


PROJECT_CONTEXT_STARTER = """\
# Project Context

Everything in this file is included in every handoff package. Describe the
rules a chatbot must follow when scoping, implementing, or reviewing changes
in this repository. Replace the placeholders below.

## About this project

(What the project does, the main technologies, and the entry points.)

## Conventions

(Code style, naming, error handling, and formatting rules.)

## Testing

(How tests are organised and what must pass before a change is acceptable.)

## Boundaries

(Areas that must not be touched, generated files, and known fragile spots.)
"""


USAGE = f"""\
Maintain {VERSION} — local handoff and workflow coordinator for
chatbot-assisted software maintenance.

Usage:
  maintain init             Create Maintain configuration and project context
  maintain new "<request>"  Create a task and generate its scope package
  maintain capture          Read and store the expected chatbot response
                            from the clipboard
  maintain next             Generate the next appropriate handoff package
  maintain apply            Validate, confirm, apply and test a patch
  maintain status           Display current task state and next action

Workflow:
  scope -> implement -> apply and test -> review
  (with bounded correction rounds and rescope support)
"""


def main(argv: Optional[list] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        say(USAGE)
        return 0
    if args[0] in ("-V", "--version", "version"):
        say(f"maintain {VERSION}")
        return 0

    command = args[0]
    try:
        if command == "init":
            cmd_init()
        elif command == "new":
            request = " ".join(args[1:]).strip()
            if not request:
                raise MaintainError(
                    "A task request is required.",
                    'Usage: maintain new "Correct the greeting shown at startup"',
                )
            cmd_new(request)
        elif command == "capture":
            cmd_capture()
        elif command == "next":
            cmd_next()
        elif command == "apply":
            cmd_apply()
        elif command == "status":
            cmd_status()
        else:
            say(USAGE)
            say(f"Unknown command: {command}")
            return 2
    except MaintainError as exc:
        say(f"Error: {exc}")
        if exc.next_action:
            say(f"Next: {exc.next_action}")
        return 1
    except KeyboardInterrupt:
        say("\nAborted.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
