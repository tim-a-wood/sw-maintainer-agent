"""Strict project configuration without project-specific imports."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ConfigurationError, PolicyError
from .security import assert_no_secrets

CONFIG_NAME = ".maintain.json"


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"{label} must be an object.")
    return value


def _reject_unknown(value: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ConfigurationError(f"Unknown {label} key: {unknown[0]}")


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


@dataclass(frozen=True)
class CommandSpec:
    name: str
    argv: tuple[str, ...]
    timeout_seconds: int = 900
    paths: tuple[str, ...] = ()
    matlab: bool = False
    phase: str = "verify"


@dataclass(frozen=True)
class ProjectConfig:
    path: Path
    repository: Path
    name: str
    default_branch: str
    source_roots: tuple[str, ...]
    test_roots: tuple[str, ...]
    exclude_paths: tuple[str, ...]
    protected_paths: tuple[str, ...]
    providers: dict[str, dict[str, Any]]
    roles: dict[str, str]
    commands: tuple[CommandSpec, ...]
    max_attempts: int = 3
    max_changed_files: int = 20
    max_diff_bytes: int = 500_000
    max_prompt_bytes: int = 2_000_000
    max_response_bytes: int = 2_000_000
    max_file_bytes: int = 120_000
    max_command_log_bytes: int = 5_000_000
    minimum_free_disk_bytes: int = 100_000_000
    allow_new_files: bool = True
    allow_deletes: bool = False
    dependency_changes: str = "approval"
    runtime_root: Path = field(default_factory=lambda: Path.home() / ".maintain" / "runs")

    @classmethod
    def load(cls, path: Path) -> "ProjectConfig":
        path = path.expanduser().resolve()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigurationError(f"Cannot read configuration: {exc}") from exc
        try:
            assert_no_secrets(data, "configuration")
        except PolicyError as exc:
            raise ConfigurationError(str(exc)) from exc
        if data.get("schema_version") != 2:
            raise ConfigurationError("Configuration schema_version must be 2.")
        _reject_unknown(data, {"schema_version", "project", "repository", "providers",
                               "execution", "verification", "policy", "delivery", "audit", "ui"},
                        "top-level")
        project = _object(data.get("project", {}), "project")
        repository = _object(data.get("repository", {}), "repository")
        _reject_unknown(project, {"name", "description", "default_branch"}, "project")
        _reject_unknown(repository, {"root", "source_roots", "test_roots", "exclude_dirs",
                                     "exclude_paths", "generated_paths", "protected_paths"},
                        "repository")
        root = Path(str(repository.get("root", "."))).expanduser()
        if not root.is_absolute():
            root = path.parent / root
        root = root.resolve()
        if not (root / ".git").exists():
            raise ConfigurationError(f"Repository is not a Git worktree: {root}")
        source_roots = tuple(_strings(repository.get("source_roots")))
        test_roots = tuple(_strings(repository.get("test_roots")))
        for item in (*source_roots, *test_roots):
            candidate = (root / item).resolve()
            try:
                candidate.relative_to(root)
            except ValueError as exc:
                raise ConfigurationError(f"Repository root escapes the project: {item}") from exc
            if not candidate.exists():
                raise ConfigurationError(f"Configured repository root does not exist: {item}")
        provider_data = _object(data.get("providers", {}), "providers")
        _reject_unknown(provider_data, {"profiles", "roles"}, "providers")
        profiles = provider_data.get("profiles", {})
        roles = provider_data.get("roles", {})
        if not isinstance(profiles, dict) or not isinstance(roles, dict):
            raise ConfigurationError("Provider profiles and roles must be objects.")
        provider_common = {"type", "timeout_seconds"}
        provider_keys = {
            "codex_cli": {"executable", "model"},
            "openai_responses": {"endpoint", "api_key_env", "model"},
            "command": {"argv"},
            "file_exchange": {"exchange_dir"},
            "m365_copilot_browser": {"url", "browser", "profile_dir", "visible",
                                      "expected_tenant", "expected_identity", "allowed_hosts",
                                      "package_transport", "response_format",
                                      "selectors", "timeout_ms", "max_chunk_chars", "retention"},
            "chatgpt_browser": {"url", "browser", "profile_dir", "visible",
                                "expected_workspace", "expected_identity", "allowed_hosts",
                                "package_transport", "response_format",
                                "selectors", "timeout_ms", "max_chunk_chars", "retention",
                                "account_capabilities"},
        }
        for profile_name, raw_profile in profiles.items():
            profile = _object(raw_profile, f"provider profile {profile_name}")
            kind = str(profile.get("type", ""))
            if kind not in provider_keys:
                raise ConfigurationError(f"Unknown provider type in {profile_name}: {kind or 'missing'}")
            _reject_unknown(profile, provider_common | provider_keys[kind],
                            f"provider profile {profile_name}")
            if kind in {"chatgpt_browser", "m365_copilot_browser"}:
                allowed_hosts = profile.get("allowed_hosts", [])
                if (not isinstance(allowed_hosts, list) or
                        any(not isinstance(item, str) or not item.strip()
                            for item in allowed_hosts)):
                    raise ConfigurationError(
                        f"Browser provider profile {profile_name}.allowed_hosts must be a string list.")
            if kind == "chatgpt_browser":
                if "account_capabilities" not in profile:
                    raise ConfigurationError(
                        f"ChatGPT provider profile {profile_name} must declare account_capabilities.")
                capabilities = _object(profile["account_capabilities"],
                                       f"ChatGPT account capabilities {profile_name}")
                _reject_unknown(capabilities, {"available", "required"},
                                f"ChatGPT account capabilities {profile_name}")
                for key in ("available", "required"):
                    value = capabilities.get(key, [])
                    if (not isinstance(value, list) or
                            any(not isinstance(item, str) or not item.strip() for item in value)):
                        raise ConfigurationError(
                            f"ChatGPT account capabilities {profile_name}.{key} must be a string list.")
        verification = _object(data.get("verification", {}), "verification")
        _reject_unknown(verification, {"commands", "profiles", "feature_profile", "issue_profile",
                                       "matlab_required_paths"}, "verification")
        commands: list[CommandSpec] = []
        command_data = _object(verification.get("commands", {}), "verification.commands")
        for name, raw in command_data.items():
            raw = _object(raw, f"verification command {name}")
            _reject_unknown(raw, {"argv", "timeout_seconds", "paths", "matlab", "phase",
                                  "working_directory", "environment_allowlist", "required", "network"},
                            f"verification command {name}")
            argv = raw.get("argv", []) if isinstance(raw, dict) else []
            if not argv or not all(isinstance(x, str) and x for x in argv):
                raise ConfigurationError(f"Verification command {name!r} needs argv.")
            commands.append(CommandSpec(
                name=name,
                argv=tuple(argv),
                timeout_seconds=int(raw.get("timeout_seconds", 900)),
                paths=tuple(_strings(raw.get("paths"))),
                matlab=bool(raw.get("matlab", False)),
                phase=str(raw.get("phase", "verify")),
            ))
        execution = _object(data.get("execution", {}), "execution")
        audit = _object(data.get("audit", {}), "audit")
        policy = _object(data.get("policy", {}), "policy")
        delivery = _object(data.get("delivery", {}), "delivery")
        ui = _object(data.get("ui", {}), "ui")
        _reject_unknown(execution, {"workspace_strategy", "dirty_repository",
                                    "max_attempts_per_task", "max_changed_files", "max_diff_bytes",
                                    "command_timeout_seconds", "max_prompt_bytes", "max_response_bytes",
                                    "max_file_bytes", "max_command_log_bytes", "max_run_storage_bytes",
                                    "minimum_free_disk_bytes"}, "execution")
        _reject_unknown(audit, {"runtime_root", "runtime_dir", "retain_days", "hash_chain"}, "audit")
        _reject_unknown(policy, {"allow_new_files", "allow_deletes", "allow_dependency_changes",
                                 "allow_network_tests", "redact_patterns"}, "policy")
        _reject_unknown(delivery, {"mode", "commit_after_acceptance", "update_current_branch"},
                        "delivery")
        _reject_unknown(ui, {"color", "animation", "width", "max_width", "language_style"}, "ui")
        dependency_changes = str(policy.get("allow_dependency_changes", "approval"))
        if dependency_changes not in {"allow", "deny", "approval"}:
            raise ConfigurationError("allow_dependency_changes must be allow, deny, or approval.")
        runtime = Path(os.path.expandvars(str(audit.get("runtime_root", "~/.maintain/runs")))).expanduser()
        limits = {
            "max_attempts_per_task": int(execution.get("max_attempts_per_task", 3)),
            "max_changed_files": int(execution.get("max_changed_files", 20)),
            "max_diff_bytes": int(execution.get("max_diff_bytes", 500_000)),
            "max_prompt_bytes": int(execution.get("max_prompt_bytes", 2_000_000)),
            "max_response_bytes": int(execution.get("max_response_bytes", 2_000_000)),
            "max_file_bytes": int(execution.get("max_file_bytes", 120_000)),
            "max_command_log_bytes": int(execution.get("max_command_log_bytes", 5_000_000)),
            "minimum_free_disk_bytes": int(execution.get("minimum_free_disk_bytes", 100_000_000)),
        }
        if any(value < 1 for value in limits.values()):
            raise ConfigurationError("Execution limits must be positive integers.")
        return cls(
            path=path, repository=root, name=str(project.get("name") or root.name),
            default_branch=str(project.get("default_branch") or "main"),
            source_roots=source_roots,
            test_roots=test_roots,
            exclude_paths=tuple(_strings(repository.get("exclude_paths"))),
            protected_paths=tuple(_strings(repository.get("protected_paths"))),
            providers=profiles, roles={str(k): str(v) for k, v in roles.items()},
            commands=tuple(commands), max_attempts=limits["max_attempts_per_task"],
            max_changed_files=limits["max_changed_files"],
            max_diff_bytes=limits["max_diff_bytes"],
            max_prompt_bytes=limits["max_prompt_bytes"],
            max_response_bytes=limits["max_response_bytes"],
            max_file_bytes=limits["max_file_bytes"],
            max_command_log_bytes=limits["max_command_log_bytes"],
            minimum_free_disk_bytes=limits["minimum_free_disk_bytes"],
            allow_new_files=bool(policy.get("allow_new_files", True)),
            allow_deletes=bool(policy.get("allow_deletes", False)),
            dependency_changes=dependency_changes,
            runtime_root=runtime.resolve(),
        )


def find_config(start: Path) -> Path | None:
    current = start.expanduser().resolve()
    if current.is_file():
        current = current.parent
    for directory in (current, *current.parents):
        candidate = directory / CONFIG_NAME
        if candidate.is_file():
            return candidate
    return None


def default_config(repository: Path, provider: str = "codex") -> dict[str, Any]:
    profiles: dict[str, dict[str, Any]]
    selected: str
    if provider == "file-exchange":
        selected = "exchange"
        profiles = {selected: {"type": "file_exchange",
                               "exchange_dir": "~/.maintain/exchange"}}
    elif provider == "chatgpt-browser":
        selected = "chatgpt"
        profiles = {selected: {"type": "chatgpt_browser", "url": "https://chatgpt.com/",
                               "profile_dir": "~/.maintain/browser/chatgpt", "visible": True,
                               "expected_workspace": "SET_WORKSPACE_NAME",
                               "expected_identity": "SET_SIGNED_IN_IDENTITY",
                               "allowed_hosts": ["chatgpt.com"],
                               "account_capabilities": {"available": [], "required": []}}}
    elif provider == "m365-browser":
        selected = "m365"
        profiles = {selected: {"type": "m365_copilot_browser",
                               "url": "https://m365.cloud.microsoft/chat",
                               "profile_dir": "~/.maintain/browser/m365", "visible": True,
                               "expected_tenant": "SET_TENANT_NAME",
                               "expected_identity": "SET_SIGNED_IN_IDENTITY",
                               "allowed_hosts": ["m365.cloud.microsoft"]}}
    else:
        selected = "codex"
        profiles = {selected: {"type": "codex_cli"}}
    source_candidates = [name for name in ("src", "app", "lib", "backend", "frontend")
                         if (repository / name).exists()]
    test_candidates = [name for name in ("tests", "test", "spec")
                       if (repository / name).exists()]
    commands: dict[str, Any] = {
        "diff-check": {"argv": ["git", "diff", "--check"], "phase": "verify",
                       "timeout_seconds": 120}}
    pytest_declared = (repository / "pytest.ini").is_file()
    for name in ("tox.ini", "setup.cfg", "pyproject.toml"):
        manifest = repository / name
        if manifest.is_file():
            try:
                pytest_declared = pytest_declared or "pytest" in manifest.read_text(
                    encoding="utf-8", errors="ignore",
                ).casefold()
            except OSError:
                pass
    if pytest_declared:
        commands["tests"] = {"argv": ["{python}", "-m", "pytest"], "phase": "verify",
                             "timeout_seconds": 900}
    package_json = repository / "package.json"
    if package_json.is_file():
        try:
            package = json.loads(package_json.read_text(encoding="utf-8"))
            if "test" in package.get("scripts", {}):
                commands["npm-test"] = {"argv": ["npm", "test"], "phase": "verify",
                                        "timeout_seconds": 900}
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "schema_version": 2,
        "project": {"name": repository.name, "default_branch": "main"},
        "repository": {"root": ".", "source_roots": source_candidates,
                       "test_roots": test_candidates,
                       "exclude_paths": [], "protected_paths": []},
        "providers": {"profiles": profiles,
                      "roles": {"scope": selected, "implement": selected,
                                "review": selected, "summarize": selected}},
        "execution": {"workspace_strategy": "git_worktree", "dirty_repository": "reject",
                      "max_attempts_per_task": 3, "max_changed_files": 20,
                      "max_diff_bytes": 500000, "max_prompt_bytes": 2000000,
                      "max_response_bytes": 2000000, "max_file_bytes": 120000,
                      "max_command_log_bytes": 5000000,
                      "minimum_free_disk_bytes": 100000000},
        "verification": {"commands": commands},
        "policy": {"allow_new_files": True, "allow_deletes": False,
                   "allow_dependency_changes": "approval"},
        "audit": {"runtime_root": "~/.maintain/runs"},
        "ui": {"color": "auto", "animation": True, "max_width": 100},
    }
