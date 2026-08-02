"""Validated edits to the project configuration and its prompt files."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from maintain.audit import atomic_write
from maintain.config import PACKET_TASK_KEYS, ProjectConfig
from maintain.engine import (IMPLEMENT_INSTRUCTIONS, REVIEW_INSTRUCTIONS,
                             SCOPE_INSTRUCTIONS)
from maintain.errors import ConfigurationError
from maintain.issue_packets import (DISCUSS_INSTRUCTIONS,
                                    EXPLAIN_INSTRUCTIONS,
                                    SCAN_INSTRUCTIONS)
from maintain.zip_package import GLOBAL_PROMPT_TEMPLATE


def _split_command(command: str) -> list[str]:
    """One command line into argv, keeping Windows backslashes whole.
    POSIX shlex eats them, so C:\\tools\\python.exe came back mangled."""
    if os.name != "nt":
        return shlex.split(command)
    tokens = shlex.split(command, posix=False)
    return [token[1:-1] if len(token) >= 2 and token[0] == token[-1]
            and token[0] in "\"'" else token for token in tokens]


def _join_command(argv: list[str]) -> str:
    if os.name != "nt":
        return shlex.join(argv)
    return subprocess.list2cmdline(argv)

BUILTIN_PROMPTS = {
    "plan": SCOPE_INSTRUCTIONS,
    "build": IMPLEMENT_INSTRUCTIONS,
    "repair": IMPLEMENT_INSTRUCTIONS,
    "review": REVIEW_INSTRUCTIONS,
    "scan": SCAN_INSTRUCTIONS,
    "discuss": DISCUSS_INSTRUCTIONS,
    "explain": EXPLAIN_INSTRUCTIONS,
}

PROMPT_DIR = ".maintain-prompts"


class ConfigStore:
    """Read-modify-write for .maintain.json with validation before replace."""

    def __init__(self, config: ProjectConfig) -> None:
        self.config = config
        self.path = config.path

    def load_raw(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save_raw(self, data: dict[str, Any]) -> ProjectConfig:
        rendered = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        with tempfile.NamedTemporaryFile(
                "w", suffix=".json", prefix=".maintain-validate-",
                dir=self.path.parent, delete=False, encoding="utf-8") as temporary:
            temporary.write(rendered)
            temporary_path = Path(temporary.name)
        try:
            ProjectConfig.load(temporary_path)
        finally:
            temporary_path.unlink(missing_ok=True)
        atomic_write(self.path, rendered.encode())
        self.config = ProjectConfig.load(self.path)
        return self.config

    def update_package(self, mutate: Callable[[dict[str, Any]], None]) -> ProjectConfig:
        data = self.load_raw()
        package = data.setdefault("package", {})
        package.setdefault("style", "zip")
        package.setdefault("global_prompt", "GLOBAL.md")
        package.setdefault("documents", [])
        tasks = package.setdefault("tasks", {})
        for key in PACKET_TASK_KEYS:
            tasks.setdefault(key, {"prompt": None, "documents": []})
        mutate(package)
        return self.save_raw(data)

    # ----- global prompt -----

    def global_prompt_path(self) -> Path:
        candidate = Path(self.config.package.global_prompt).expanduser()
        if not candidate.is_absolute():
            candidate = self.path.parent / candidate
        return candidate

    def read_global_prompt(self) -> str:
        path = self.global_prompt_path()
        if path.is_file():
            return path.read_text(encoding="utf-8")
        return GLOBAL_PROMPT_TEMPLATE

    def write_global_prompt(self, content: str) -> None:
        atomic_write(self.global_prompt_path(), content.encode())

    # ----- task prompts -----

    def task_prompt(self, task: str) -> tuple[bool, str]:
        """(overridden, effective text) for one task type."""
        policy = self.config.package.task(task)
        if not policy.prompt:
            return False, BUILTIN_PROMPTS[task]
        candidate = Path(policy.prompt).expanduser()
        for base in (self.config.repository, self.path.parent):
            resolved = candidate if candidate.is_absolute() else base / candidate
            if resolved.is_file():
                return True, resolved.read_text(encoding="utf-8")
        return True, ""

    def set_task_prompt(self, task: str, content: str | None) -> ProjectConfig:
        """content=None returns the task to the built-in prompt."""
        if task not in PACKET_TASK_KEYS:
            raise ConfigurationError(f"Unknown packet task type: {task}")
        if content is None:
            return self.update_package(
                lambda package: package["tasks"][task].update({"prompt": None}))
        prompt_dir = self.path.parent / PROMPT_DIR
        prompt_path = prompt_dir / f"{task}.md"
        atomic_write(prompt_path, content.encode())
        relative = f"{PROMPT_DIR}/{task}.md"
        return self.update_package(
            lambda package: package["tasks"][task].update({"prompt": relative}))

    # ----- documents -----

    def add_document(self, path: Path, task: str | None = None) -> ProjectConfig:
        value = self._document_value(path)

        def mutate(package: dict[str, Any]) -> None:
            target = (package["documents"] if task is None
                      else package["tasks"][task]["documents"])
            if value not in target:
                target.append(value)

        return self.update_package(mutate)

    def remove_document(self, value: str, task: str | None = None) -> ProjectConfig:
        def mutate(package: dict[str, Any]) -> None:
            target = (package["documents"] if task is None
                      else package["tasks"][task]["documents"])
            if value in target:
                target.remove(value)

        return self.update_package(mutate)

    def _document_value(self, path: Path) -> str:
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_file():
            raise ConfigurationError(f"The document does not exist: {path}")
        try:
            return resolved.relative_to(self.config.repository.resolve()).as_posix()
        except ValueError:
            return str(resolved)

    # ----- style and checks -----

    def set_style(self, style: str) -> ProjectConfig:
        return self.update_package(lambda package: package.update({"style": style}))

    def checks(self) -> list[tuple[str, str]]:
        return [(spec.name, _join_command(spec.argv))
                for spec in self.config.commands]

    def set_checks(self, rows: list[tuple[str, str]]) -> ProjectConfig:
        data = self.load_raw()
        existing = data.setdefault("verification", {}).setdefault("commands", {})
        commands: dict[str, Any] = {
            name: value for name, value in existing.items()
            if isinstance(value, dict) and value.get("phase", "verify") != "verify"
        }
        for name, command in rows:
            name = name.strip()
            argv = _split_command(command.strip()) if command.strip() else []
            if not name or not argv:
                raise ConfigurationError("Each check needs a name and a command.")
            preserved = existing.get(name, {}) if isinstance(existing.get(name), dict) else {}
            commands[name] = {**preserved, "argv": argv, "phase": "verify"}
        data["verification"]["commands"] = commands
        return self.save_raw(data)
