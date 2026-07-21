"""Explicit, recoverable migration from proposal-only schema version 1."""

from __future__ import annotations

import json
from pathlib import Path
from .audit import atomic_write
from .config import CONFIG_NAME, default_config
from .errors import ConfigurationError
from .models import utc_now


def migrate_v1(path: Path, provider: str = "codex") -> tuple[Path, Path]:
    path = path.expanduser().resolve()
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Cannot read legacy configuration: {exc}") from exc
    if old.get("schema_version") != 1:
        raise ConfigurationError("Only schema version 1 can use this migration.")
    backup = path.with_name(f"{CONFIG_NAME}.v1.backup")
    if backup.exists():
        raise ConfigurationError(f"Migration backup already exists: {backup}")
    repository = old.get("repository", {})
    project = old.get("project", {})
    root_value = Path(str(repository.get("root", "."))).expanduser()
    root = (path.parent / root_value).resolve() if not root_value.is_absolute() else root_value.resolve()
    new = default_config(root, provider)
    new["project"].update({"name": str(project.get("name") or root.name),
                           "description": str(project.get("description") or "")})
    for key in ("source_roots", "test_roots", "exclude_paths", "protected_paths"):
        if isinstance(repository.get(key), list):
            new["repository"][key] = repository[key]
    old_runtime = root / str(old.get("audit", {}).get("runtime_dir", ".maintain/runs"))
    legacy_runs = []
    if old_runtime.is_dir():
        for record_path in sorted(old_runtime.glob("*/run.json")):
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                record = {}
            legacy_runs.append({"run_id": record.get("run_id", record_path.parent.name),
                                "old_status": record.get("status", "unknown"),
                                "classification": "legacy_proposal_only",
                                "source": str(record_path)})
    report = {"migrated_at": utc_now(), "source_schema": 1, "target_schema": 2,
              "legacy_runs": legacy_runs,
              "statement": "Legacy runs did not implement, review, or verify repository changes."}
    report_path = root / ".maintain" / "migration-report.json"
    atomic_write(backup, path.read_bytes())
    atomic_write(path, json.dumps(new, indent=2).encode() + b"\n")
    atomic_write(report_path, json.dumps(report, indent=2).encode() + b"\n")
    return backup, report_path
