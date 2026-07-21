"""Authenticated local Codex provider."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import asdict
from pathlib import Path

from maintain.errors import ProviderError
from maintain.models import ProviderCapabilities, ProviderRequest
from maintain.security import assert_no_secrets

from .base import Provider
from .command import parse_response


class CodexProvider(Provider):
    name = "codex_cli"
    capabilities = ProviderCapabilities(can_edit_workspace=True)

    def __init__(self, executable: str = "", model: str = "", timeout_seconds: int = 900,
                 evidence_dir: Path | None = None) -> None:
        bundled = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
        self.executable = executable or shutil.which("codex") or (str(bundled) if bundled.is_file() else "")
        self.model, self.timeout_seconds = model, timeout_seconds
        self.evidence_dir = evidence_dir

    def preflight(self) -> None:
        if not self.executable:
            raise ProviderError("Codex is not installed.")
        result = subprocess.run([self.executable, "login", "status"], capture_output=True,
                                timeout=15, check=False)
        if result.returncode:
            raise ProviderError("Codex is not signed in.")

    def exchange(self, request: ProviderRequest):
        with tempfile.TemporaryDirectory(prefix="maintain-codex-") as directory:
            output = Path(directory) / "response.json"
            prompt = (request.instructions + "\nReturn only this JSON envelope.\n" +
                      json.dumps(asdict(request), ensure_ascii=False))
            command = [self.executable, "exec", "--ephemeral", "--skip-git-repo-check",
                       "--sandbox", "read-only", "--color", "never", "--cd", directory,
                       "--output-last-message", str(output)]
            if self.model:
                command += ["--model", self.model]
            command.append(prompt)
            result = subprocess.run(command, text=True, capture_output=True,
                                    timeout=self.timeout_seconds, check=False)
            if result.returncode or not output.is_file():
                raise ProviderError(f"Codex failed: {(result.stderr or result.stdout)[-600:]}")
            return self._parse_or_repair(output.read_text(encoding="utf-8"), request, Path(directory))

    def exchange_in_workspace(self, request: ProviderRequest, worktree: Path):
        """Use workspace-write only for the implementation role."""
        if request.role != "implement":
            raise ProviderError("Workspace editing is permitted only for implementation.")
        with tempfile.TemporaryDirectory(prefix="maintain-codex-output-") as directory:
            output = Path(directory) / "response.json"
            prompt = (
                request.instructions
                + "\nEdit only the allowed files in this isolated worktree. Do not run tests, "
                  "builds, network tools, installers, or MATLAB. The trusted local runner performs "
                  "all checks. Return only the required JSON envelope after editing.\n"
                + json.dumps(asdict(request), ensure_ascii=False)
            )
            command = [self.executable, "exec", "--ephemeral", "--skip-git-repo-check",
                       "--sandbox", "workspace-write", "--color", "never", "--cd", str(worktree),
                       "--output-last-message", str(output)]
            if self.model:
                command += ["--model", self.model]
            command.append(prompt)
            try:
                result = subprocess.run(command, text=True, capture_output=True,
                                        timeout=self.timeout_seconds, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ProviderError(f"Codex implementation stopped: {exc}") from exc
            if result.returncode or not output.is_file():
                raise ProviderError(f"Codex implementation failed: {(result.stderr or result.stdout)[-600:]}")
            return self._parse_or_repair(output.read_text(encoding="utf-8"), request, worktree)

    def _parse_or_repair(self, raw: str, request: ProviderRequest, working_directory: Path):
        """Preserve malformed output and allow one bounded schema-repair turn."""
        try:
            return parse_response(_extract_json(raw), request, self.name)
        except ProviderError:
            self._save_raw(request, raw, "invalid")
        content = {"summary": "Describe the completed provider action."}
        if request.role == "implement" and request.payload.get("mode") == "issue":
            allowed = request.payload.get("task", {}).get("allowed_files", [])
            content["root_cause"] = {
                "statement": "Replace with the code-grounded cause of the reported behavior.",
                "evidence_paths": allowed[:1],
            }
        envelope = {
            "schema_version": request.schema_version,
            "run_id": request.run_id,
            "task_id": request.task_id,
            "role": request.role,
            "content": content,
            "conversation_id": "",
        }
        with tempfile.TemporaryDirectory(prefix="maintain-codex-repair-") as directory:
            output = Path(directory) / "response.json"
            prompt = (
                "Return only one valid JSON object. Do not edit files or run commands. "
                "Use this exact envelope identity. Replace all placeholder text with factual "
                "content grounded in the supplied task and files. Preserve root_cause and "
                "evidence_paths when they are present.\nEnvelope:\n"
                + json.dumps(envelope, ensure_ascii=False)
                + "\nOriginal request:\n"
                + json.dumps(asdict(request), ensure_ascii=False)
            )
            command = [self.executable, "exec", "--ephemeral", "--skip-git-repo-check",
                       "--sandbox", "read-only", "--color", "never",
                       "--cd", str(working_directory), "--output-last-message", str(output), prompt]
            result = subprocess.run(command, text=True, capture_output=True,
                                    timeout=self.timeout_seconds, check=False)
            if result.returncode or not output.is_file():
                raise ProviderError("Codex schema repair failed.")
            repaired = output.read_text(encoding="utf-8")
            self._save_raw(request, repaired, "repair")
            return parse_response(_extract_json(repaired), request, self.name)

    def _save_raw(self, request: ProviderRequest, raw: str, label: str) -> None:
        if self.evidence_dir is None:
            return
        assert_no_secrets(raw, "Codex raw response")
        directory = self.evidence_dir / "codex"
        directory.mkdir(parents=True, exist_ok=True)
        safe_task = "".join(character if character.isalnum() or character in "-_" else "-"
                            for character in request.task_id)
        (directory / f"{request.role}-{safe_task}-{label}.txt").write_text(raw, encoding="utf-8")


def _extract_json(raw: str) -> str:
    stripped = raw.strip()
    if "```" in stripped:
        for candidate in stripped.split("```")[1::2]:
            candidate = candidate.removeprefix("json").strip()
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                continue
    return stripped
