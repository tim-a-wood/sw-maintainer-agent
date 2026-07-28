"""Maintain command-line interface."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from . import __version__
from .audit import AuditStore, atomic_write, cleanup_runs
from .config import CONFIG_NAME, ProjectConfig, default_config, find_config
from .engine import WorkflowEngine
from .errors import ConfigurationError, DeliveryError, MaintainError
from .models import RunRecord, RunState
from .presenter import Presenter, run_state_label
from .presenter import QuietPresenter
from .project_creation import create_project
from .references import validate_reference
from .repository_memory import (
    default_reference_for,
    forget_repository,
    load_last_repository,
    load_recent_projects,
    remember_repository,
    repository_for_cli,
    repository_root,
    select_file,
    select_folder,
    set_default_reference,
)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="maintain", description="Complete verified maintenance work.")
    root.add_argument("--version", action="version", version=f"Maintain {__version__}")
    root.add_argument("--repo", help="Target repository or path inside it")
    root.add_argument("--config", help="Configuration file")
    root.add_argument("--no-animation", action="store_true")
    root.add_argument("--no-color", action="store_true")
    root.add_argument("--json", action="store_true", dest="json_output",
                      help="Write machine-readable output")
    commands = root.add_subparsers(dest="command")
    init = commands.add_parser("init", help="Create a version 2 project configuration")
    init.add_argument("repository", nargs="?", default=".")
    init.add_argument("--provider", choices=["codex", "manual-ui", "file-exchange", "chatgpt-browser",
                                              "m365-browser"], default="codex")
    init.add_argument("--yes", action="store_true", help="Write the displayed configuration")
    for name in ("feature", "issue"):
        item = commands.add_parser(name, help=f"Start a verified {name} workflow")
        item.add_argument("request", nargs="+", help="Required outcome or issue report")
        reference_choice = item.add_mutually_exclusive_group()
        reference_choice.add_argument(
            "--reference",
            help="One local file or HTTPS link to provide directly to Microsoft 365 Copilot",
        )
        reference_choice.add_argument(
            "--no-reference",
            action="store_true",
            help="Skip this project's saved default reference for this run",
        )
        item.add_argument(
            "--save-reference",
            action="store_true",
            help="Use --reference as this project's default for future runs",
        )
    project = commands.add_parser("project", help="List, switch, add, forget, or create projects")
    project.add_argument(
        "action",
        choices=["list", "open", "add", "forget", "new"],
        nargs="?",
        default="list",
    )
    project.add_argument("value", nargs="?", help="Project path, name, or list number")
    project.add_argument(
        "--provider",
        choices=["codex", "manual-ui", "file-exchange", "chatgpt-browser", "m365-browser"],
        default="m365-browser",
    )
    project.add_argument("--name", help="Display name for a new project")
    project.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        default=argparse.SUPPRESS,
        help="Write machine-readable output",
    )
    resume = commands.add_parser("resume", help="Resume a saved workflow")
    resume.add_argument("run_id")
    accept = commands.add_parser("accept", help="Accept an unchanged verified workflow")
    accept.add_argument("run_id")
    deliver = commands.add_parser("deliver", help="Create the verified commit after acceptance")
    deliver.add_argument("run_id")
    deliver.add_argument("--current-branch", metavar="BRANCH")
    deliver.add_argument("--confirm-current-branch", action="store_true")
    feedback = commands.add_parser("feedback", help="Send acceptance feedback into repair")
    feedback.add_argument("run_id")
    feedback.add_argument("message", nargs="+")
    cancel = commands.add_parser("cancel", help="Stop a run and retain its evidence")
    cancel.add_argument("run_id")
    status = commands.add_parser("status", help="Show one saved workflow")
    status.add_argument("run_id")
    status.add_argument("--json", action="store_true", dest="json_output",
                        default=argparse.SUPPRESS)
    audit = commands.add_parser("audit", help="Verify or export audit evidence")
    audit.add_argument("action", choices=["verify", "export", "cleanup"])
    audit.add_argument("run_id", nargs="?")
    audit.add_argument("--output", metavar="ZIP", help="Archive path for export")
    audit.add_argument("--older-than-days", type=int)
    diff = commands.add_parser("diff", help="Show the actual worktree diff")
    diff.add_argument("run_id")
    evidence = commands.add_parser("evidence", help="Show review, test, and delivery evidence")
    evidence.add_argument("run_id")
    config_cmd = commands.add_parser("config", help="Validate or show effective configuration")
    config_cmd.add_argument("action", choices=["validate", "show", "upgrade", "migrate"], nargs="?",
                            default="validate")
    config_cmd.add_argument("--provider", choices=["codex", "manual-ui", "file-exchange", "chatgpt-browser",
                                                    "m365-browser"], default="codex")
    provider = commands.add_parser("provider", help="Inspect provider readiness")
    provider.add_argument("action",
                          choices=["list", "doctor", "login", "check", "models", "model"],
                          nargs="?", default="list")
    provider.add_argument("profile", nargs="?")
    provider.add_argument("value", nargs="?", help="Model name for the model action")
    provider.add_argument("--refresh", action="store_true", help="Retrieve models from the web UI")
    workspace = commands.add_parser("workspace", help="Inspect or remove isolated workspaces")
    workspace.add_argument("action", choices=["list", "open", "cleanup"], nargs="?", default="list")
    workspace.add_argument("run_id", nargs="?")
    runs = commands.add_parser("runs", help="List saved workflows")
    runs.add_argument("--state")
    packet = commands.add_parser(
        "package", help="Build one plan packet ZIP for a manual Copilot exchange")
    packet.add_argument("request", nargs="+", help="The change to plan")
    packet.add_argument("--output", help="Destination directory for the packet ZIP")
    packet.add_argument("--attach", action="append", default=[], metavar="FILE",
                        help="Add a file to the packet attachments")
    commands.add_parser("doctor", help="Check configuration and providers")
    return root


def _config(args: argparse.Namespace) -> ProjectConfig:
    repository = Path(args.repo).expanduser().resolve()
    path = Path(args.config).expanduser() if args.config else None
    if path is None:
        entry = next(
            (
                project for project in load_recent_projects()
                if project.path.resolve() == repository
            ),
            None,
        )
        if (
            entry is not None
            and entry.config_path is not None
            and entry.config_path.is_file()
        ):
            path = entry.config_path
    if path is None:
        path = find_config(repository)
    if path is None:
        raise ConfigurationError(f"No {CONFIG_NAME} was found. Run maintain init.")
    config = ProjectConfig.load(path)
    remember_repository(config.repository, config_path=config.path)
    return config


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    presenter = None
    try:
        if args.command == "project":
            return _project_command(args)
        if args.command != "init":
            if args.repo is None and args.config:
                args.repo = str(Path(args.config).expanduser().resolve().parent)
            if args.command is None and args.repo is None and load_last_repository() is None:
                if not sys.stdin.isatty() or args.json_output:
                    raise ConfigurationError(
                        "No project has been selected. Use maintain project new PATH, "
                        "maintain project open PATH, or launch Maintain interactively."
                    )
                args.repo = ""
            else:
                args.repo = str(repository_for_cli(
                    args.repo, interactive=sys.stdin.isatty() and not args.json_output))
        if args.command is None:
            return _home(args)
        if args.command == "init":
            repository = Path(args.repository).expanduser().resolve()
            if not (repository / ".git").exists():
                raise ConfigurationError("The target must be a Git repository.")
            path = repository / CONFIG_NAME
            if path.exists():
                raise ConfigurationError(f"Configuration already exists: {path}")
            candidate = default_config(repository, args.provider)
            rendered = json.dumps(candidate, indent=2) + "\n"
            with tempfile.NamedTemporaryFile("w", suffix=".json", prefix=".maintain-validate-",
                                             dir=repository, delete=False) as temporary:
                temporary.write(rendered)
                temporary_path = Path(temporary.name)
            try:
                ProjectConfig.load(temporary_path)
            finally:
                temporary_path.unlink(missing_ok=True)
            if not args.yes:
                print("Proposed configuration:")
                print(rendered, end="")
                if not sys.stdin.isatty():
                    raise ConfigurationError("Run init again with --yes to write this configuration.")
                if input("Write this configuration? [y/N]: ").strip().casefold() not in {"y", "yes"}:
                    raise ConfigurationError("Configuration was not written.")
            atomic_write(path, rendered.encode())
            if args.json_output:
                print(json.dumps({"created": str(path), "provider": args.provider}, sort_keys=True))
            else:
                print(f"Created {path}")
            remember_repository(repository, config_path=path)
            return 0
        if args.command == "config" and args.action in {"upgrade", "migrate"}:
            from .migration import migrate_v1
            path = Path(args.config).expanduser() if args.config else find_config(Path(args.repo))
            if path is None:
                raise ConfigurationError(f"No {CONFIG_NAME} was found.")
            backup, report = migrate_v1(path, args.provider)
            if args.json_output:
                print(json.dumps({"backup": str(backup), "report": str(report)}, sort_keys=True))
            else:
                print(f"Configuration migrated. Backup: {backup}")
                print(f"Legacy run report: {report}")
            return 0
        config = _config(args)
        presenter = QuietPresenter() if args.json_output else _presenter_for(args, config)
        engine = WorkflowEngine(config, presenter)
        if args.command in {"feature", "issue"}:
            request = " ".join(args.request)
            saved_reference = default_reference_for(config.repository)
            uses_copilot = _uses_m365_copilot(config)
            reference = (
                args.reference
                or (saved_reference if uses_copilot and not args.no_reference else None)
            )
            if args.save_reference and not args.reference:
                raise ConfigurationError("--save-reference requires --reference.")
            if args.reference and not uses_copilot:
                raise ConfigurationError(
                    "Copilot references require a Microsoft 365 Copilot provider.")
            if reference:
                reference = validate_reference(reference).source
            if args.save_reference:
                set_default_reference(config.repository, reference)
            elif args.reference is None and reference and reference != saved_reference:
                set_default_reference(config.repository, reference)
            presenter.run_header(args.command, request, config.name, _provider_label(config))
            record = engine.start(args.command, request, reference=reference)
            _summary(record, args.json_output, presenter, command_prefix=_command_prefix(config))
        elif args.command == "package":
            from .context import ContextSelector
            from .engine import PROVIDER_SAFETY_HEADER, SCOPE_INSTRUCTIONS
            from .models import ProviderRequest
            from .zip_package import build_packet
            request_text = " ".join(args.request)
            selector = ContextSelector(config.repository,
                                       config.source_roots + config.test_roots,
                                       config.exclude_paths, config.max_file_bytes)
            context = selector.select(request_text)
            payload = {
                "mode": "feature", "request": request_text,
                "project_policy": {
                    "allow_new_files": config.allow_new_files,
                    "allow_deletes": config.allow_deletes,
                    "source_roots": list(config.source_roots),
                    "test_roots": list(config.test_roots),
                },
                "context_expansions": [],
                "repository_map": selector.repository_map(),
                "candidate_files": [{"path": x.path, "sha256": x.sha256, "bytes": x.bytes,
                                     "content": x.content} for x in context],
            }
            stamp = __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S")
            packet_request = ProviderRequest(
                1, f"packet-{stamp}", "scope-1", "scope",
                f"{PROVIDER_SAFETY_HEADER}\n\n{SCOPE_INSTRUCTIONS}", payload)
            output_dir = Path(args.output).expanduser() if args.output else Path.cwd()
            build = build_packet(packet_request, output_dir,
                                 policy=config.package, repository=config.repository,
                                 config_dir=config.path.parent,
                                 attachments=[Path(item).expanduser()
                                              for item in args.attach])
            if args.json_output:
                print(json.dumps({"packet": str(build.zip_path), "sha256": build.sha256,
                                  "bytes": build.bytes,
                                  "members": list(build.members)}, sort_keys=True))
            else:
                print(f"Created {build.zip_path}")
                print("Give this packet to Copilot. It contains the plan task.")
        elif args.command == "resume":
            record = engine.resume(args.run_id)
            _summary(record, args.json_output, presenter, command_prefix=_command_prefix(config))
        elif args.command == "accept":
            record = engine.accept(args.run_id)
            _summary(record, args.json_output, presenter, command_prefix=_command_prefix(config))
        elif args.command == "deliver":
            existing = _load(config, args.run_id)
            record = (existing if existing.state in {"delivered", "needs_human_delivery"}
                      else engine.deliver(args.run_id))
            if args.current_branch:
                record = engine.integrate(args.run_id, args.current_branch,
                                          confirmed=args.confirm_current_branch)
            _summary(record, args.json_output, presenter, command_prefix=_command_prefix(config))
        elif args.command == "feedback":
            record = engine.feedback(args.run_id, " ".join(args.message))
            _summary(record, args.json_output, presenter, command_prefix=_command_prefix(config))
        elif args.command == "cancel":
            _summary(engine.cancel(args.run_id), args.json_output, presenter,
                     command_prefix=_command_prefix(config))
        elif args.command == "status":
            record = _load(config, args.run_id)
            gates = engine.gate_status(record)
            if args.json_output:
                print(json.dumps({**record.to_dict(), "gates": gates}, sort_keys=True))
            else:
                _summary(record, presenter=presenter, command_prefix=_command_prefix(config))
                presenter.gates(gates)
                presenter.verification_results(_verification_rows(record))
        elif args.command == "audit":
            if args.action == "cleanup":
                removed = cleanup_runs(
                    config.runtime_root, args.older_than_days or config.retain_days,
                    repository=config.repository,
                )
                if args.json_output:
                    print(json.dumps({"removed": removed}, sort_keys=True))
                else:
                    print(f"Removed {len(removed)} expired unaccepted run(s).")
                return 0
            if not args.run_id:
                raise ConfigurationError("A run ID is required.")
            _load(config, args.run_id)
            audit_store = AuditStore(config.runtime_root, args.run_id)
            result = audit_store.verify()
            if args.action == "verify":
                if args.json_output:
                    print(json.dumps({"run_id": args.run_id, "verified": True, **result},
                                     sort_keys=True))
                else:
                    presenter.outcome("Verified", "The audit record is complete and unchanged.",
                                      facts=[("Run", args.run_id),
                                             ("Events", str(result["events"]))], tone="success")
            else:
                output = Path(args.output) if args.output else Path(f"{args.run_id}-audit.zip")
                exported = audit_store.export(output)
                if args.json_output:
                    print(json.dumps({"run_id": args.run_id, "file": str(exported)}, sort_keys=True))
                else:
                    presenter.outcome("Exported", "The verified audit package is ready.",
                                      facts=[("Run", args.run_id), ("File", str(exported))],
                                      tone="success")
        elif args.command == "diff":
            record = _load(config, args.run_id)
            from .workspace import git
            if record.state == "delivered":
                commit = str(record.evidence.get("delivery", {}).get("commit", ""))
                if not commit:
                    raise ConfigurationError("The delivered commit is missing.")
                shown_diff = git(config.repository, "diff", "--binary", record.base_commit, commit)
            else:
                shown_diff = engine.workspaces.diff(Path(record.worktree)).text
            print(json.dumps({"run_id": record.run_id, "diff": shown_diff}, sort_keys=True)
                  if args.json_output else shown_diff)
        elif args.command == "evidence":
            shown = _load(config, args.run_id).evidence
            if args.json_output:
                print(json.dumps(shown, sort_keys=True))
            else:
                presenter.console.print_json(json.dumps(shown, indent=2))
        elif args.command == "config":
            if args.action == "show":
                shown = json.loads(config.path.read_text(encoding="utf-8"))
                if args.json_output:
                    print(json.dumps(shown, sort_keys=True))
                else:
                    presenter.console.print_json(json.dumps(shown, indent=2))
            else:
                if args.json_output:
                    print(json.dumps({"valid": True, "project": config.name}, sort_keys=True))
                else:
                    presenter.outcome("Valid", "The project configuration is valid.",
                                      facts=[("Project", config.name)], tone="success")
        elif args.command == "provider":
            if args.action == "list":
                rows = [(role, profile,
                         str(config.providers.get(profile, {}).get("type", "missing")))
                        for role, profile in sorted(config.roles.items())]
                if args.json_output:
                    print(json.dumps([{"role": role, "profile": profile, "provider": kind}
                                      for role, profile, kind in rows], sort_keys=True))
                else:
                    presenter.provider_assignments(rows)
            elif args.action in {"models", "model"}:
                result = _provider_models(args, config, presenter, engine)
                if args.json_output:
                    print(json.dumps(result, sort_keys=True))
            else:
                names = [args.profile] if args.profile else sorted(set(config.roles.values()))
                readiness = []
                for name in names:
                    if name not in config.providers:
                        raise ConfigurationError(f"Provider profile does not exist: {name}")
                    provider = engine.provider_builder(name, config.providers[name],
                                                       config.runtime_root.parent / "browser")
                    if args.action in {"login", "check"} and not (
                            provider.capabilities.browser_automation):
                        raise ConfigurationError(
                            f"{args.action.title()} is available only for browser providers.")
                    if args.action == "login":
                        provider.login()
                        if not hasattr(provider, "compatibility_check"):
                            raise ConfigurationError(
                                "This browser provider cannot verify the signed-in session.")
                        result = provider.compatibility_check(
                            require_selected_model=False)
                    elif args.action == "check":
                        if not hasattr(provider, "compatibility_check"):
                            raise ConfigurationError(
                                "This browser provider cannot run a compatibility check.")
                        result = provider.compatibility_check()
                    else:
                        provider.preflight()
                        result = {"ready": True}
                    readiness.append({"profile": name, **result})
                    if not args.json_output:
                        facts = []
                        if result.get("layout"):
                            facts.append(("Layout", str(result["layout"])))
                        if result.get("model_available") is False:
                            facts.append((
                                "Model",
                                f"{result.get('configured_model') or 'Configured model'} is unavailable",
                            ))
                        elif result.get("model"):
                            facts.append(("Model", str(result["model"])))
                        if args.action == "login":
                            label = "Sign-in ready"
                            title = f"{name} is signed in. The browser controls are ready."
                            actions = (
                                [
                                    "Update the model list before you start work: "
                                    + _shell_command([
                                        "maintain", "--repo", str(config.repository),
                                        "--config", str(config.path),
                                        "provider", "model", name, "--refresh",
                                    ])
                                ]
                                if result.get("model_available") is False else []
                            )
                            tone = "warning" if actions else "success"
                        elif args.action == "check":
                            label = "Browser ready"
                            title = f"The {name} browser and model are ready."
                            actions = []
                            tone = "success"
                        else:
                            label = "Assistant ready"
                            title = f"{name} is ready."
                            actions = []
                            tone = "success"
                        presenter.outcome(
                            label, title, facts=facts, actions=actions, tone=tone)
                if args.json_output:
                    print(json.dumps(readiness, sort_keys=True))
        elif args.command == "workspace":
            if args.action == "list":
                values = _run_values(config)
                if args.json_output:
                    print(json.dumps([{"run_id": value["run_id"], "state": value["state"],
                                       "worktree": value["worktree"]} for value in values],
                                     sort_keys=True))
                else:
                    for value in values:
                        print(f"{value['run_id']}  {value['state']}  {value['worktree']}")
            elif args.action in {"open", "cleanup"}:
                if not args.run_id:
                    raise ConfigurationError("A run ID is required.")
                record = _load(config, args.run_id)
                if args.action == "open":
                    if not Path(record.worktree).is_dir():
                        raise ConfigurationError("This run no longer has a local workspace.")
                    print(json.dumps({"worktree": record.worktree}, sort_keys=True)
                          if args.json_output else record.worktree)
                else:
                    engine.cleanup_workspace(record.run_id)
                    if args.json_output:
                        print(json.dumps({"removed": args.run_id}, sort_keys=True))
                    else:
                        print(f"Removed workspace: {args.run_id}")
        elif args.command == "runs":
            rows = [{key: str(value.get(key, ""))
                     for key in ("run_id", "state", "mode", "request")}
                    for value in _run_values(config)
                    if not args.state or value["state"] == args.state]
            if args.json_output:
                print(json.dumps(rows, sort_keys=True))
            else:
                presenter.saved_runs(rows)
        elif args.command == "doctor":
            checks = engine.doctor()
            if args.json_output:
                print(json.dumps(checks, sort_keys=True))
            else:
                command_count = len(config.commands)
                diff_only = _diff_only_verification(config)
                facts = [
                    ("Project setup",
                     "The configuration, Git, workspace, storage, and audit checks passed"),
                    ("Assistant", "The assistant connection checks passed"),
                    (
                        "Check setup",
                        f"Maintain found {command_count} configured "
                        f"check{'s' if command_count != 1 else ''}. "
                        "Maintain did not run them",
                    ),
                ]
                if diff_only:
                    facts.append((
                        "Coverage",
                        "Maintain found only a diff-format check. It did not find project tests",
                    ))
                presenter.outcome(
                    "Ready to start", "Maintain can start this change.",
                    "Maintain did not run the project checks.",
                    facts=facts,
                    actions=(
                        ["Add a project test before you use these results to verify behavior."]
                        if diff_only else []
                    ),
                    tone="success")
        return 0
    except MaintainError as exc:
        _show_error(args, str(exc), presenter)
        return exc.exit_code
    except (KeyboardInterrupt, EOFError):
        _show_error(args, "The current operation was cancelled.", presenter,
                    "Use View runs, then resume to continue saved work.")
        return 130
    except (OSError, ValueError) as exc:
        _show_error(args, str(exc), presenter)
        return 1


def _project_command(args: argparse.Namespace) -> int:
    """Handle project registry actions without requiring an active repository."""

    action = args.action
    projects = load_recent_projects()
    if action == "list":
        active = load_last_repository()
        rows = [
            _project_row(entry, index, active=active)
            for index, entry in enumerate(projects, 1)
        ]
        if args.json_output:
            print(json.dumps(rows, sort_keys=True))
        else:
            presenter = _presenter_for(args)
            presenter.section("PROJECTS", "Recent Maintain projects")
            presenter.console.print()
            if not rows:
                presenter.console.print(
                    "No projects are saved yet. Create one with `maintain project new PATH`.",
                    style="muted",
                )
            for row in rows:
                presenter.menu_line(
                    row["index"],
                    row["name"],
                    f"{'Active · ' if row['active'] else ''}{row['status']} · {row['path']}",
                )
        return 0

    if action == "new":
        destination_value = args.value
        if not destination_value:
            if args.json_output or not sys.stdin.isatty():
                raise ConfigurationError("The new action requires a project path.")
            destination_value = _presenter_for(args).ask("New project folder")
        if not destination_value:
            raise ConfigurationError("The new project path cannot be empty.")
        created = create_project(
            Path(destination_value), provider=args.provider, name=args.name)
        remember_repository(created.repository, config_path=created.config_path)
        result = {
            "action": "new",
            "project": str(created.repository),
            "config": str(created.config_path),
            "provider": args.provider,
        }
        _project_result(args, "Project created", result)
        return 0

    value = args.value or args.repo
    entry = _resolve_project(value, projects, require_registered=action == "forget")
    if action == "forget":
        if not forget_repository(entry.path):
            raise ConfigurationError(f"Project is not remembered: {entry.path}")
        _project_result(args, "Project forgotten", {
            "action": "forget",
            "project": str(entry.path),
        })
        return 0

    root = repository_root(entry.path)
    if root is None:
        raise ConfigurationError(f"Not a Git repository: {entry.path}")
    config_path = _project_config_path(args, root, entry)
    if config_path is not None:
        loaded = ProjectConfig.load(config_path)
        if loaded.repository.resolve() != root.resolve():
            raise ConfigurationError(
                f"The project configuration belongs to {loaded.repository}, not {root}.")
    remember_repository(root, config_path=config_path)
    _project_result(args, "Project selected", {
        "action": action,
        "project": str(root),
        "config": str(config_path) if config_path else None,
        "configured": config_path is not None,
    })
    return 0


def _project_row(
        entry, index: int, *, active: Path | None = None) -> dict[str, object]:
    if not entry.exists:
        status = "Missing"
    elif not entry.valid:
        status = "Not a Git repository"
    elif not entry.configured:
        status = "Setup required"
    else:
        try:
            config_path = entry.config_path or entry.path / CONFIG_NAME
            ProjectConfig.load(config_path)
            status = "Ready"
        except (MaintainError, OSError, ValueError):
            status = "Setup needs attention"
    return {
        "index": str(index),
        "name": entry.name,
        "path": str(entry.path),
        "status": status,
        "last_opened_at": entry.last_opened_at,
        "configured": entry.configured,
        "default_reference": entry.default_reference,
        "active": active is not None and entry.path.resolve() == active.resolve(),
    }


def _resolve_project(value, projects, *, require_registered: bool):
    if not value:
        raise ConfigurationError("Specify a project path, name, or list number.")
    shown = str(value).strip()
    if not shown:
        raise ConfigurationError("Specify a project path, name, or list number.")
    if shown.isdigit():
        index = int(shown)
        if not 1 <= index <= len(projects):
            raise ConfigurationError("Choose a project number shown by maintain project list.")
        return projects[index - 1]

    path_candidate = Path(shown).expanduser().resolve()
    path_matches = [
        entry for entry in projects if entry.path.resolve() == path_candidate
    ]
    if path_matches:
        return path_matches[0]
    name_matches = [
        entry for entry in projects if entry.name.casefold() == shown.casefold()
    ]
    if len(name_matches) == 1:
        return name_matches[0]
    if len(name_matches) > 1:
        raise ConfigurationError(
            "More than one remembered project has that name; use its path or list number.")
    if require_registered:
        raise ConfigurationError(f"Project is not remembered: {shown}")

    root = repository_root(path_candidate)
    if root is None:
        raise ConfigurationError(f"Not a Git repository: {shown}")
    from .repository_memory import ProjectEntry
    return ProjectEntry(
        path=root,
        name=root.name,
        last_opened_at="",
        config_path=None,
        exists=True,
        valid=True,
        configured=(root / CONFIG_NAME).is_file(),
    )


def _project_config_path(args: argparse.Namespace, repository: Path, entry) -> Path | None:
    if args.config:
        candidate = Path(args.config).expanduser().resolve()
    elif entry.config_path and entry.config_path.is_file():
        candidate = entry.config_path
    elif (repository / CONFIG_NAME).is_file():
        candidate = repository / CONFIG_NAME
    else:
        return None
    return candidate.resolve()


def _project_result(args: argparse.Namespace, title: str, result: dict[str, object]) -> None:
    if args.json_output:
        print(json.dumps(result, sort_keys=True))
        return
    facts = [
        ("Project", str(result["project"])),
    ]
    if result.get("config"):
        facts.append(("Configuration", str(result["config"])))
    presenter = _presenter_for(args)
    presenter.outcome(title, title + ".", facts=facts, tone="success")


def _load(config: ProjectConfig, run_id: str):
    path = AuditStore(config.runtime_root, run_id).run_dir / "run.json"
    if not path.is_file():
        raise ConfigurationError(f"Run does not exist: {run_id}")
    record = RunRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
    if Path(record.repository).resolve() != config.repository.resolve():
        raise ConfigurationError("The run belongs to a different project.")
    return record


def _run_values(config: ProjectConfig) -> list[dict]:
    values: list[dict] = []
    if not config.runtime_root.exists():
        return values
    valid_states = {str(state) for state in RunState}
    for path in config.runtime_root.glob("*/run.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            required = ("run_id", "state", "mode", "request", "repository")
            if (not isinstance(value, dict)
                    or any(not isinstance(value.get(key), str) or not value[key]
                           for key in required)
                    or value["state"] not in valid_states
                    or Path(value["repository"]).resolve() != config.repository.resolve()):
                continue
            values.append(value)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return sorted(
        values,
        key=lambda value: (
            str(value.get("updated_at") or value.get("created_at") or ""),
            str(value["run_id"]),
        ),
        reverse=True,
    )


def _continuable_values(config: ProjectConfig) -> list[dict]:
    terminal = {"delivered", "failed", "cancelled"}
    return [value for value in _run_values(config) if value.get("state") not in terminal]


def _presenter_for(args: argparse.Namespace, config: ProjectConfig | None = None) -> Presenter:
    animation = not args.no_animation and (config.ui_animation if config else True)
    no_color = args.no_color or bool(config and config.ui_color == "never")
    max_width = config.ui_max_width if config else 96
    force_color = bool(config and config.ui_color == "always" and not args.no_color)
    return Presenter(animate=animation, no_color=no_color, max_width=max_width,
                     force_color=force_color)


def _command_prefix(config: ProjectConfig) -> str:
    return _shell_command([
        "maintain", "--repo", str(config.repository),
        "--config", str(config.path),
    ])


def _shell_command(argv: list[str], *, windows: bool | None = None) -> str:
    """Render a copyable POSIX-shell or Windows PowerShell command."""
    if windows is None:
        windows = sys.platform == "win32"
    if windows:
        return "& " + " ".join(
            "'" + value.replace("'", "''") + "'" for value in argv)
    return shlex.join(argv)


def _diff_only_verification(config: ProjectConfig) -> bool:
    """Identify the safe fallback that checks formatting but no project behavior."""
    return (
        len(config.commands) == 1
        and config.commands[0].argv == ("git", "diff", "--check")
    )


def _verification_rows(record: RunRecord) -> list[dict[str, str]]:
    """Return compact, deduplicated local-command evidence for display."""
    groups: list[tuple[str, object]] = []
    completed = record.evidence.get("completed_tasks", [])
    if isinstance(completed, list):
        for item in completed:
            if isinstance(item, dict):
                groups.append((str(item.get("task_id") or "Task"), item.get("tests", {})))
    current_task = "Current task"
    if record.tasks and 0 <= record.task_index < len(record.tasks):
        current_task = str(record.tasks[record.task_index].get("id") or current_task)
    groups.append((current_task, record.evidence.get("tests", {})))

    rows: list[dict[str, str]] = []
    seen: set[tuple[object, ...]] = set()
    for task_id, tests in groups:
        if not isinstance(tests, dict):
            continue
        commands = tests.get("commands", [])
        if not isinstance(commands, list):
            continue
        for result in commands:
            if not isinstance(result, dict):
                continue
            signature = (
                task_id,
                result.get("name"),
                result.get("exit_code"),
                result.get("output_sha256"),
                result.get("duration_seconds"),
            )
            if signature in seen:
                continue
            seen.add(signature)
            exit_code = result.get("exit_code")
            duration = result.get("duration_seconds")
            try:
                shown_duration = f"{float(duration):.1f}s"
            except (TypeError, ValueError):
                shown_duration = ""
            rows.append({
                "task": task_id,
                "name": str(result.get("name") or "Unnamed command"),
                "result": "Passed" if exit_code == 0 else "Failed",
                "exit_code": "" if exit_code is None else str(exit_code),
                "duration": shown_duration,
            })
    return rows


def _recovery_guidance(record: RunRecord) -> str:
    """Explain the smallest safe action before an interactive retry."""
    if record.evidence.get("pause_reason") == "repair_limit":
        return (
            "Review the saved diff and failed checks. If you continue, Maintain will "
            "try one more automatic repair."
        )
    error = record.error.casefold()
    if any(token in error for token in (
            "browser", "sign in", "signed in", "model", "assistant", "provider")):
        return (
            "Open Assistant settings. Sign in or reconnect. Do the compatibility "
            "check. Then return to this run."
        )
    if any(token in error for token in (
            "pytest", "verification command", "project python", "timed out", "matlab")):
        return (
            "Run the failed check in the project environment. Correct its dependencies "
            "or configuration. Then continue this run."
        )
    if "disk space" in error:
        return "Make more disk space. Then return to this run."
    return (
        "Correct the error shown above. Maintain will use the saved workspace and evidence."
    )


def _display_timestamp(value: str) -> str:
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    except ValueError:
        return value


def _summary(record, json_output: bool = False, presenter: Presenter | None = None,
             interactive: bool = False, command_prefix: str = "maintain") -> None:
    if json_output:
        print(json.dumps(record.to_dict(), sort_keys=True))
        return
    presenter = presenter or Presenter(animate=False)
    changed = record.evidence.get("changed_files", [])
    review = record.evidence.get("review", {})
    checks = _verification_rows(record)
    facts = [("Run", record.run_id)]
    elapsed = _elapsed(record.created_at, record.updated_at)
    if elapsed:
        facts.append(("Elapsed", elapsed))
    if record.updated_at:
        facts.append(("Updated", _display_timestamp(record.updated_at)))
    if record.tasks:
        completed = len(record.evidence.get("completed_tasks", []))
        facts.append(("Tasks", f"{completed} of {len(record.tasks)} complete"))
    if changed:
        shown = ", ".join(changed[:3])
        if len(changed) > 3:
            shown += f" and {len(changed) - 3} more"
        facts.append(("Changed", shown))
    if review.get("decision"):
        decision = str(review["decision"])
        facts.append(("Review", "Approved" if decision == "approve"
                      else decision.replace("_", " ").title()))
    if checks:
        passed = sum(item["result"] == "Passed" for item in checks)
        facts.append(("Checks", f"{passed} of {len(checks)} passed"))
    reference = record.evidence.get("copilot_reference")
    if isinstance(reference, dict) and reference.get("name"):
        facts.append((
            "Copilot reference",
            str(reference["name"]) + (
                " (link)" if reference.get("kind") == "url" else " (attached file)"
            ),
        ))
    if record.branch:
        facts.append(("Branch", record.branch))

    state = record.state
    if state == "awaiting_acceptance":
        presenter.outcome(
            "Ready for review", "The change passed the review and all local checks.",
            facts=facts,
            actions=[] if interactive else [
                f"Review the change: {command_prefix} diff {record.run_id}",
                f"Approve the change: {command_prefix} accept {record.run_id}",
            ],
            tone="accent",
        )
    elif state == "accepted":
        presenter.outcome(
            "Approved", "You approved the verified change.", facts=facts,
            actions=[] if interactive else [
                f"Create the commit: {command_prefix} deliver {record.run_id}"
            ],
            tone="success")
    elif state == "delivered":
        commit = record.evidence.get("delivery", {}).get("commit", "")
        integrated = record.evidence.get("delivery", {}).get("integrated_branch", "")
        presenter.outcome(
            "Delivered",
            (f"Maintain added the verified change to {integrated}." if integrated
             else "Maintain created the verified commit on its maintenance branch."),
            facts=[*facts, ("Commit", commit), ("Integrated into", integrated)], tone="success",
        )
    elif state == "cancelled":
        presenter.outcome("Cancelled", "Maintain stopped the run and saved its evidence.",
                          facts=facts, tone="muted")
    elif state in {"needs_human", "needs_human_delivery"}:
        guidance = _recovery_guidance(record)
        actions = (
            [guidance, "Do not try again until you complete this action."]
            if interactive else
            [
                guidance,
                f"Continue after you correct the error: {command_prefix} resume {record.run_id}",
                f"View the run details: {command_prefix} status {record.run_id}",
            ]
        )
        presenter.outcome(
            "Action needed", "Maintain needs your action before it can continue.",
            record.error or (
                "Maintain did not save error details. Review the run evidence before you try again."
            ),
            facts,
            actions,
            tone="warning",
        )
    elif state == "failed":
        presenter.outcome("Stopped", "Maintain stopped this run before completion.",
                          record.error, facts,
                          [f"View the run details: {command_prefix} status {record.run_id}"],
                          tone="danger")
    else:
        state_messages = {
            "created": "Maintain has not started this run.",
            "preparing": "Maintain is preparing the project and assistant.",
            "workspace_ready": "Maintain is ready to plan the change.",
            "context_expanding": "Maintain is finding the project files for this change.",
            "scoping": "Maintain is planning the change.",
            "tasks_ready": "The change plan is ready.",
            "implementing": "Maintain is creating the change.",
            "implemented": "Maintain is ready to review the change.",
            "reviewing": "Maintain is reviewing the change.",
            "changes_requested": "The review found changes to make.",
            "repairing": "Maintain is correcting the change.",
            "testing": "Maintain is running the local checks.",
            "test_failed": "A local check failed.",
            "verified": "The change passed all local checks.",
            "delivering": "Maintain is creating the verified commit.",
        }
        presenter.outcome("Saved", state_messages.get(
            state, "Maintain saved the run at its current step."),
                          record.error, facts, tone="accent")


def _home(args: argparse.Namespace) -> int:
    if not sys.stdin.isatty():
        print("Choose feature, issue, resume, runs, or doctor.", file=sys.stderr)
        return 2
    if not args.repo:
        presenter = _presenter_for(args)
        selected = _interactive_project_picker(args, presenter, initial=True)
        if selected is None:
            return 0
        args.repo = str(selected)
    while True:
        config_error = ""
        try:
            config = _config(args)
        except (MaintainError, OSError, ValueError) as exc:
            config = None
            if find_config(Path(args.repo)) is not None:
                config_error = str(exc)
        presenter = _presenter_for(args, config)
        values = _continuable_values(config) if config else []
        presenter.home(
            config.name if config else "",
            _provider_label(config) if config else "Setup required",
            len(values), configured=config is not None, setup_issue=config_error,
            repository=str(Path(args.repo).expanduser().resolve()),
            branch=_current_branch(Path(args.repo)),
            assistant_settings=_uses_browser_assistant(config) if config else False,
        )
        choice = presenter.ask("Choose", "1" if config is not None else "S").casefold()
        if choice == "q":
            return 0
        if choice == "p":
            selected = _interactive_project_picker(args, presenter)
            if selected is not None:
                args.repo = str(selected)
                args.config = None
            continue
        if choice == "n":
            selected = _interactive_create_project(args, presenter)
            if selected is not None:
                args.repo = str(selected)
                args.config = None
            continue
        if choice == "s":
            if config is not None:
                presenter.error("This project is already set up.")
                _pause(presenter)
                continue
            if _interactive_setup(args, presenter) == 0:
                continue
            _pause(presenter)
            continue
        available_choices = {"1", "2", "3", "4"}
        if config is not None and _uses_browser_assistant(config):
            available_choices.add("5")
        if choice not in available_choices:
            shown = "1, 2, 3, 4, 5" if "5" in available_choices else "1, 2, 3, 4"
            presenter.error(f"Choose {shown}, P, N, S, or Q.")
            continue
        if config is None:
            presenter.error("Set up this project before starting work.",
                            "Choose S from the menu.")
            _pause(presenter)
            continue
        engine = WorkflowEngine(config, presenter)
        if choice == "5":
            _interactive_assistant_settings(args, config, presenter)
            continue
        if choice in {"1", "2"}:
            mode = "feature" if choice == "1" else "issue"
            presenter.console.print()
            request = presenter.ask("What should change?" if mode == "feature" else "What is wrong?")
            if not request:
                presenter.error("Describe the required outcome before starting.")
                _pause(presenter)
                continue
            reference = _interactive_copilot_reference(config, presenter)
            presenter.run_header(mode, request, config.name, _provider_label(config))
            record = engine.start(mode, request, reference=reference)
            _summary(record, presenter=presenter, interactive=True)
            _interactive_run(engine, record, presenter)
            continue
        if choice == "3":
            selected = _choose_run(config, presenter)
            if selected is None:
                continue
            record = _load(config, selected)
            _continue_saved_run(engine, record, presenter)
            continue
        _interactive_history(engine, config, presenter)


def _interactive_project_picker(
        args: argparse.Namespace, presenter: Presenter, *, initial: bool = False) -> Path | None:
    """Choose, browse, create, or forget a recent project."""

    while True:
        projects = load_recent_projects()
        active = load_last_repository()
        presenter.section(
            "PROJECTS",
            "Choose a project" if projects else "Start your first project",
            "The most recently opened project is listed first." if projects else "",
        )
        presenter.console.print()
        for index, entry in enumerate(projects, 1):
            row = _project_row(entry, index, active=active)
            reference = " · Reference saved" if entry.default_reference else ""
            active_label = "Active · " if row["active"] else ""
            presenter.menu_line(
                str(index), entry.name,
                f"{active_label}{row['status']} · {entry.path}{reference}",
            )
        if projects:
            presenter.console.print()
        presenter.menu_line("b", "Browse for an existing project", "Add a Git repository")
        presenter.menu_line("n", "Create a new project", "Start from a blank repository")
        if projects:
            presenter.menu_line("f", "Forget a project", "Remove it from this list")
        presenter.menu_line(
            "q" if initial else "x",
            "Quit" if initial else "Back",
            "",
            quiet=True,
        )
        default = "1" if projects else "N"
        choice = presenter.ask("Choose", default).casefold()
        if (initial and choice == "q") or (not initial and choice in {"x", "q"}):
            return None
        if choice == "n":
            created = _interactive_create_project(args, presenter)
            if created is not None:
                return created
            continue
        if choice == "b":
            selected = select_folder("Select an existing Git project", allow_new=False)
            if selected is None:
                continue
            root = repository_root(selected)
            if root is None:
                presenter.error(
                    "That folder is not inside a Git repository.",
                    "Choose an existing Git project or create a new one.",
                )
                continue
            config_path = root / CONFIG_NAME if (root / CONFIG_NAME).is_file() else None
            remember_repository(root, config_path=config_path)
            return root
        if choice == "f" and projects:
            selected = presenter.ask("Project number to forget")
            try:
                index = int(selected)
                if not 1 <= index <= len(projects):
                    raise IndexError
            except (ValueError, IndexError):
                presenter.error("Choose a listed project number.")
                continue
            forgotten = projects[index - 1]
            forget_repository(forgotten.path)
            presenter.complete("PROJECT", f"Forgot {forgotten.name}")
            continue
        try:
            index = int(choice)
            if not 1 <= index <= len(projects):
                raise IndexError
            selected = projects[index - 1]
        except (ValueError, IndexError):
            presenter.error("Choose a listed project, B, N, or F.")
            continue
        if not selected.valid:
            presenter.error(
                f"{selected.path} is not an available Git repository.",
                "Choose F to forget it, or browse to its new location.",
            )
            continue
        remember_repository(selected.path, config_path=selected.config_path)
        return selected.path


def _interactive_create_project(
        args: argparse.Namespace, presenter: Presenter) -> Path | None:
    presenter.section(
        "NEW PROJECT",
        "Create a blank Maintain project",
        "This creates a Git repository, README, and Maintain configuration.",
    )
    presenter.console.print()
    name = presenter.ask("Project name")
    if not name:
        return None
    active = Path(args.repo).expanduser().resolve() if args.repo else None
    parent = active.parent if active is not None else Path.cwd()
    destination = presenter.ask("Project folder", str(parent / name))
    if not destination:
        return None
    presenter.console.print()
    presenter.menu_line("1", "Microsoft 365 Copilot", "Recommended for this workflow")
    presenter.menu_line("2", "ChatGPT", "Browser automation")
    presenter.menu_line("3", "Codex", "Local CLI")
    presenter.menu_line("4", "File exchange", "Manual assistant handoff")
    presenter.menu_line("b", "Back", "", quiet=True)
    choice = presenter.ask("Assistant", "1").casefold()
    if choice == "b":
        return None
    provider = {
        "1": "m365-browser",
        "2": "chatgpt-browser",
        "3": "codex",
        "4": "file-exchange",
    }.get(choice)
    if provider is None:
        presenter.error("Choose 1, 2, 3, 4, or B.")
        return None
    try:
        created = create_project(Path(destination), provider=provider, name=name)
    except MaintainError as exc:
        presenter.error(str(exc), "Choose a new, empty project path and try again.")
        _pause(presenter)
        return None
    remember_repository(created.repository, config_path=created.config_path)
    presenter.outcome(
        "Project created",
        f"{name} is ready to use.",
        facts=[
            ("Folder", str(created.repository)),
            ("Branch", "main"),
            ("Assistant", provider.replace("-", " ").title()),
        ],
        actions=["Use Assistant settings if this is your first browser sign-in."],
        tone="success",
    )
    _pause(presenter)
    return created.repository


def _interactive_copilot_reference(
        config: ProjectConfig, presenter: Presenter) -> str | None:
    if not _uses_m365_copilot(config):
        return None
    saved = default_reference_for(config.repository) or ""
    presenter.console.print()
    presenter.console.print("OPTIONAL COPILOT REFERENCE", style="brand")
    presenter.console.print(
        "Provide one local file or HTTPS link as read-only background material.",
        style="muted",
    )
    while True:
        reference = presenter.ask(
            "Reference (- for none, B to browse, CLEAR to forget)", saved)
        if reference == "-":
            return None
        if reference.casefold() == "clear":
            set_default_reference(config.repository, None)
            saved = ""
            presenter.complete("REFERENCE", "Cleared the project default")
            continue
        if reference.casefold() == "b":
            selected = select_file("Select a Copilot reference file")
            if selected is None:
                return None
            reference = str(selected)
        if not reference:
            return None
        try:
            validated = validate_reference(reference)
        except MaintainError as exc:
            presenter.error(str(exc), "Choose one readable file up to 10 MB or an HTTPS link.")
            continue
        normalized = validated.source
        if reference == saved and normalized != saved:
            set_default_reference(config.repository, normalized)
        elif normalized != saved:
            keep = presenter.ask("Use this as the default for this project?", "N").casefold()
            if keep in {"y", "yes"}:
                set_default_reference(config.repository, normalized)
        return normalized


def _uses_m365_copilot(config: ProjectConfig) -> bool:
    return any(
        config.providers.get(profile, {}).get("type") == "m365_copilot_browser"
        for profile in set(config.roles.values())
    )


def _uses_browser_assistant(config: ProjectConfig) -> bool:
    return any(
        config.providers.get(profile, {}).get("type")
        in {"chatgpt_browser", "m365_copilot_browser"}
        for profile in set(config.roles.values())
    )


def _current_branch(repository: Path) -> str:
    root = repository_root(repository)
    if root is None:
        return ""
    from .workspace import git
    return git(root, "branch", "--show-current", check=False) or "detached HEAD"


def _interactive_setup(args: argparse.Namespace, presenter: Presenter) -> int:
    presenter.section("SETUP", "Choose the assistant")
    presenter.console.print()
    presenter.menu_line("1", "Microsoft 365 Copilot", "Browser automation")
    presenter.menu_line("2", "ChatGPT", "Browser automation")
    presenter.menu_line("3", "Codex", "Local CLI")
    presenter.menu_line("b", "Back", "", quiet=True)
    choice = presenter.ask("Choose", "1").casefold()
    if choice == "b":
        return 1
    provider = {"1": "m365-browser", "2": "chatgpt-browser", "3": "codex"}.get(choice)
    if provider is None:
        presenter.error("Choose 1, 2, 3, or B.")
        return 1
    repository = Path(args.repo).expanduser().resolve()
    while not (repository / ".git").exists() and repository != repository.parent:
        repository = repository.parent
    existing = find_config(repository)
    if existing is not None:
        try:
            version = json.loads(existing.read_text(encoding="utf-8")).get("schema_version")
        except (OSError, json.JSONDecodeError) as exc:
            presenter.error("The existing project configuration cannot be read.", str(exc))
            return 1
        if version == 1:
            return main(["--repo", str(repository), "config", "upgrade",
                         "--provider", provider])
        presenter.error(
            "The existing project configuration is invalid.",
            f"Correct or remove {existing}, then choose setup again.",
        )
        return 1
    init_args = ["init", str(repository), "--provider", provider, "--yes"]
    created = main(init_args)
    if created or provider not in {"m365-browser", "chatgpt-browser"}:
        return created
    profile = "m365" if provider == "m365-browser" else "chatgpt"
    login_args = ["--repo", str(repository)]
    if args.no_animation:
        login_args.append("--no-animation")
    if args.no_color:
        login_args.append("--no-color")
    login_args.extend(["provider", "login", profile])
    logged_in = main(login_args)
    if logged_in:
        return logged_in
    model_args = ["--repo", str(repository)]
    if args.no_animation:
        model_args.append("--no-animation")
    if args.no_color:
        model_args.append("--no-color")
    model_args.extend(["provider", "model", profile, "--refresh"])
    return main(model_args)


def _choose_run(
        config: ProjectConfig, presenter: Presenter, *, history: bool = False) -> str | None:
    values = _run_values(config) if history else _continuable_values(config)
    if not values:
        presenter.outcome(
            "History" if history else "Saved work",
            "There are no saved runs." if history else "There are no runs to continue.",
            tone="muted",
        )
        _pause(presenter)
        return None
    rows = [{**{key: str(value.get(key, ""))
                for key in ("run_id", "state", "mode", "request")},
             "index": str(index)} for index, value in enumerate(values, 1)]
    presenter.saved_runs(
        rows, selectable=True, selection_purpose="inspect" if history else "continue")
    while True:
        choice = presenter.ask("Choose a run to inspect" if history else "Choose a run", "1")
        choice = choice.casefold()
        if choice in {"b", "q"}:
            return None
        try:
            index = int(choice)
            if not 1 <= index <= len(rows):
                raise IndexError
            return rows[index - 1]["run_id"]
        except (ValueError, IndexError):
            presenter.console.print("Choose a listed run number or B to go back.",
                                    style="warning")


def _continue_saved_run(
        engine: WorkflowEngine, record: RunRecord, presenter: Presenter, *,
        show_summary: bool = True) -> RunRecord:
    """Continue only after the user sees the saved state and explicitly chooses it."""
    if record.state in {"awaiting_acceptance", "accepted"}:
        if show_summary:
            _summary(record, presenter=presenter, interactive=True)
        _interactive_run(engine, record, presenter)
        return record
    if record.state == "needs_human":
        return _interactive_resume(
            engine, record, presenter, show_summary=show_summary)
    if record.state == "needs_human_delivery":
        if show_summary:
            _summary(record, presenter=presenter, interactive=True)
        _interactive_delivery(engine, record, presenter)
        return record
    if record.state in {"delivered", "failed", "cancelled"}:
        if show_summary:
            _summary(record, presenter=presenter, interactive=True)
        _pause(presenter)
        return record
    try:
        resumed = engine.resume(record.run_id)
    except MaintainError as exc:
        presenter.error(str(exc), "The run remains saved; review its status before retrying.")
        _pause(presenter)
        return record
    _summary(resumed, presenter=presenter, interactive=True)
    if resumed.state in {"awaiting_acceptance", "accepted"}:
        _interactive_run(engine, resumed, presenter)
    else:
        _pause(presenter)
    return resumed


def _interactive_resume(
        engine: WorkflowEngine, record: RunRecord, presenter: Presenter, *,
        show_summary: bool = True) -> RunRecord:
    """Show a paused run and require confirmation before provider preflight or mutation."""
    if show_summary:
        _summary(record, presenter=presenter, interactive=True)
    presenter.console.print()
    repair_cycle = record.evidence.get("pause_reason") == "repair_limit"
    prompt = (
        "Start one more automatic repair now?"
        if repair_cycle else
        "Have you completed the required action?"
    )
    while True:
        choice = presenter.ask(prompt, "N").casefold()
        if choice in {"", "n", "no", "b"}:
            return record
        if choice in {"y", "yes"}:
            break
        presenter.console.print("Select Y to try again. Select N to keep the run.",
                                style="warning")
    try:
        resumed = engine.resume(record.run_id)
    except MaintainError as exc:
        presenter.error(str(exc), "Maintain saved the run. Correct the error before you try again.")
        _pause(presenter)
        return record
    _summary(resumed, presenter=presenter, interactive=True)
    if resumed.state in {"awaiting_acceptance", "accepted"}:
        _interactive_run(engine, resumed, presenter)
    elif resumed.state == "needs_human_delivery":
        _interactive_delivery(engine, resumed, presenter)
    else:
        _pause(presenter)
    return resumed


def _interactive_history(
        engine: WorkflowEngine, config: ProjectConfig, presenter: Presenter) -> None:
    """Let users inspect every saved run without changing it merely by selecting it."""
    while True:
        selected = _choose_run(config, presenter, history=True)
        if selected is None:
            return
        try:
            record = _load(config, selected)
            presenter.section(
                "RUN DETAILS",
                record.request,
                f"{record.mode.title()} · {run_state_label(record.state)}",
            )
            _summary(record, presenter=presenter, interactive=True)
            presenter.console.print()
            presenter.console.print("LAST ERROR", style="muted")
            presenter.console.print(
                record.error or "None saved.",
                style="warning" if record.error else "muted",
                markup=False,
            )
            presenter.gates(engine.gate_status(record))
            presenter.verification_results(_verification_rows(record))
            presenter.console.print()
            presenter.console.print("SAVED EVIDENCE", style="muted")
            presenter.console.print(
                str(AuditStore(config.runtime_root, record.run_id).run_dir),
                style="label",
                markup=False,
            )
        except (MaintainError, OSError, ValueError) as exc:
            presenter.error(str(exc), "This saved run could not be inspected.")
            _pause(presenter)
            continue

        presenter.console.print()
        continuable = record.state not in {"delivered", "failed", "cancelled"}
        if continuable:
            presenter.menu_line("c", "Continue this run", "Explicitly resume or finish it")
        presenter.menu_line("b", "Back to history", "", quiet=True)
        while True:
            choice = presenter.ask("Choose", "B").casefold()
            if choice == "b":
                break
            if choice == "c" and continuable:
                _continue_saved_run(
                    engine, record, presenter, show_summary=False)
                return
            presenter.console.print(
                "Choose C to continue this run or B to return."
                if continuable else "Choose B to return.",
                style="warning",
            )


def _interactive_run(engine: WorkflowEngine, record, presenter: Presenter) -> None:
    while record.state in {"awaiting_acceptance", "accepted"}:
        presenter.console.print()
        if record.state == "awaiting_acceptance":
            presenter.console.print("FINISH THIS RUN", style="brand")
            presenter.console.print()
            presenter.menu_line("1", "Accept and update this branch", "Default")
            presenter.menu_line("2", "Review the diff", "Optional")
            presenter.menu_line("3", "Request another change", "Send feedback")
            presenter.menu_line("4", "Keep a verified branch only", "Do not update this branch")
            presenter.menu_line("b", "Keep it saved", "Return to the menu", quiet=True)
            choice = presenter.ask("Choose", "1").casefold()
            if choice == "2":
                presenter.console.print()
                presenter.console.print(engine.workspaces.diff(Path(record.worktree)).text,
                                        markup=False)
                _pause(presenter)
                continue
            if choice == "3":
                message = presenter.ask("What should be different?")
                if not message:
                    presenter.error("Feedback cannot be empty.")
                    continue
                record = engine.feedback(record.run_id, message)
                _summary(record, presenter=presenter, interactive=True)
                continue
            if choice == "b":
                return
            if choice not in {"1", "4"}:
                presenter.error("Choose 1, 2, 3, 4, or B.")
                continue
            update_source = choice == "1"
            try:
                record = engine.accept(record.run_id)
            except MaintainError as exc:
                presenter.error(str(exc))
                _pause(presenter)
                return
        else:
            presenter.console.print("FINISH THIS ACCEPTED RUN", style="brand")
            presenter.console.print()
            presenter.menu_line("1", "Create commit and update this branch", "Default")
            presenter.menu_line("4", "Keep a verified branch only", "Do not update this branch")
            presenter.menu_line("b", "Keep it saved", "Return to the menu", quiet=True)
            choice = presenter.ask("Choose", "1").casefold()
            if choice == "b":
                return
            if choice not in {"1", "4"}:
                presenter.error("Choose 1, 4, or B.")
                continue
            update_source = choice == "1"
        presenter.complete("APPROVE", "You approved the verified change")
        try:
            record = engine.deliver(record.run_id)
            source_branch = str(record.evidence.get("source_branch", ""))
            if update_source and source_branch:
                record = engine.integrate(record.run_id, source_branch, confirmed=True)
                presenter.complete("UPDATE", f"Added the change to {source_branch}")
            elif update_source:
                presenter.failed(
                    "UPDATE",
                    "The source checkout has no branch. Maintain kept the verified branch",
                )
        except MaintainError as exc:
            presenter.error(str(exc))
            _pause(presenter)
            return
        _summary(record, presenter=presenter, interactive=True)
        _pause(presenter)
        return


def _interactive_delivery(engine: WorkflowEngine, record, presenter: Presenter) -> None:
    presenter.console.print()
    presenter.console.print("FINISH THE BRANCH UPDATE", style="brand")
    presenter.console.print()
    presenter.menu_line("1", "I fixed the branch issue; try again", "")
    presenter.menu_line("2", "Keep the verified branch only", "Finish without updating")
    presenter.menu_line("b", "Keep it saved", "Return to the menu", quiet=True)
    choice = presenter.ask("Choose", "B").casefold()
    if choice == "b":
        return
    try:
        if choice == "1":
            source_branch = str(record.evidence.get("source_branch", ""))
            if not source_branch:
                raise DeliveryError("The source checkout has no recorded branch.")
            record = engine.integrate(record.run_id, source_branch, confirmed=True)
            presenter.complete("UPDATE", f"Added the change to {source_branch}")
        elif choice == "2":
            record = engine.keep_delivered_branch(record.run_id)
            presenter.complete("DELIVER", "Kept the verified change on its maintenance branch")
        else:
            presenter.error("Choose 1, 2, or B.")
            _pause(presenter)
            return
    except MaintainError as exc:
        presenter.error(str(exc))
    _summary(record, presenter=presenter, interactive=True)
    _pause(presenter)


def _pause(presenter: Presenter) -> None:
    presenter.console.print()
    presenter.ask("Press Enter to return")


def _provider_label(config: ProjectConfig) -> str:
    labels = {
        "chatgpt_browser": "ChatGPT",
        "m365_copilot_browser": "Microsoft 365 Copilot",
        "codex_cli": "Codex",
        "openai_responses": "OpenAI Responses",
        "file_exchange": "File exchange",
        "command": "Enterprise assistant",
    }
    shown: list[str] = []
    for profile in sorted(set(config.roles.values())):
        profile_config = config.providers.get(profile, {})
        kind = str(profile_config.get("type", ""))
        if not kind:
            continue
        label = labels.get(kind, kind.replace("_", " ").title())
        model = str(profile_config.get("model") or "").strip()
        shown.append(f"{label} · {model}" if model else label)
    return ", ".join(shown)


def _provider_models(args: argparse.Namespace, config: ProjectConfig, presenter,
                     engine: WorkflowEngine) -> dict[str, object]:
    browser_profiles = [name for name, profile in config.providers.items()
                        if profile.get("type") in {"chatgpt_browser", "m365_copilot_browser"}]
    profile_name = args.profile
    if not profile_name:
        if len(browser_profiles) != 1:
            raise ConfigurationError("Specify the browser provider profile.")
        profile_name = browser_profiles[0]
    if profile_name not in browser_profiles:
        raise ConfigurationError(f"Browser provider profile does not exist: {profile_name}")
    profile = config.providers[profile_name]
    models = [str(item) for item in profile.get("available_models", [])]
    current = str(profile.get("model") or "").strip()
    if args.refresh:
        provider = engine.provider_builder(
            profile_name, profile, config.runtime_root.parent / "browser")
        if not hasattr(provider, "available_models"):
            raise ConfigurationError("This provider cannot retrieve browser models.")
        models = provider.available_models()
        if current not in models:
            current = ""
        _write_provider_models(config, profile_name, models, current)
    if args.action == "models":
        if args.value:
            raise ConfigurationError("The models action does not accept a model name.")
        if not models:
            raise ConfigurationError("No model list is saved. Use provider models --refresh.")
        if not args.json_output:
            presenter.section("MODELS", f"Available models for {profile_name}",
                              f"Current preference: {current or 'Not selected'}")
            presenter.console.print()
            for index, model in enumerate(models, 1):
                presenter.menu_line(str(index), model, "Selected" if model == current else "")
        return {"profile": profile_name, "models": models, "selected": current or None}
    if args.value:
        selected = args.value.strip()
    else:
        if args.json_output:
            raise ConfigurationError("Provide a model name when using --json.")
        if not models:
            raise ConfigurationError("No model list is saved. Use provider model --refresh.")
        presenter.section("MODEL", f"Choose the model for {profile_name}",
                          "Maintain selects this model for every new conversation.")
        presenter.console.print()
        for index, model in enumerate(models, 1):
            presenter.menu_line(str(index), model, "Current" if model == current else "")
        default = str(models.index(current) + 1) if current in models else "1"
        choice = presenter.ask("Choose", default)
        try:
            selected = models[int(choice) - 1]
        except (ValueError, IndexError) as exc:
            raise ConfigurationError("Choose a listed model number.") from exc
    if selected not in models:
        raise ConfigurationError(
            f"Model {selected!r} is not in the saved list. Refresh the available models.")
    _write_provider_models(config, profile_name, models, selected)
    if not args.json_output:
        presenter.outcome("Saved", f"{selected} will be used for every conversation.",
                          facts=[("Profile", profile_name), ("Model", selected)], tone="success")
    return {"profile": profile_name, "models": models, "selected": selected}


def _write_provider_models(config: ProjectConfig, profile_name: str, models: list[str],
                           selected: str) -> None:
    data = json.loads(config.path.read_text(encoding="utf-8"))
    profile = data["providers"]["profiles"][profile_name]
    profile["available_models"] = models
    if selected:
        profile["model"] = selected
    else:
        profile.pop("model", None)
    rendered = json.dumps(data, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", suffix=".json", prefix=".maintain-model-",
                                     dir=config.path.parent, delete=False) as temporary:
        temporary.write(rendered)
        temporary_path = Path(temporary.name)
    try:
        ProjectConfig.load(temporary_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    atomic_write(config.path, rendered.encode())


def _interactive_assistant_settings(args: argparse.Namespace, config: ProjectConfig,
                                    presenter: Presenter) -> None:
    profiles = [name for name, profile in config.providers.items()
                if profile.get("type") in {"chatgpt_browser", "m365_copilot_browser"}]
    if not profiles:
        presenter.error("Model settings are available only for browser assistants.")
        _pause(presenter)
        return
    profile = profiles[0]
    if len(profiles) > 1:
        presenter.section("ASSISTANT", "Choose a browser profile")
        presenter.console.print()
        for index, name in enumerate(profiles, 1):
            presenter.menu_line(str(index), name, "")
        try:
            profile = profiles[int(presenter.ask("Choose", "1")) - 1]
        except (ValueError, IndexError):
            presenter.error("Choose a listed profile number.")
            _pause(presenter)
            return
    current = str(config.providers[profile].get("model") or "Not selected")
    presenter.section("ASSISTANT", "Browser assistant", f"Current model: {current}")
    presenter.console.print()
    presenter.menu_line("1", "Sign in or reconnect", "Verify the browser session")
    presenter.menu_line("2", "Change model", "Use the saved model list")
    presenter.menu_line("3", "Refresh and change", "Retrieve models from the browser")
    presenter.menu_line("4", "Check compatibility", "Inspect the browser without sending")
    presenter.menu_line("b", "Back", "", quiet=True)
    choice = presenter.ask("Choose", "1").casefold()
    if choice == "b":
        return
    if choice not in {"1", "2", "3", "4"}:
        presenter.error("Choose 1, 2, 3, 4, or B.")
        _pause(presenter)
        return
    command = ["--repo", str(config.repository)]
    if args.no_animation:
        command.append("--no-animation")
    if args.no_color:
        command.append("--no-color")
    if choice == "1":
        command.extend(["provider", "login", profile])
    elif choice == "4":
        command.extend(["provider", "check", profile])
    else:
        command.extend(["provider", "model", profile])
        if choice == "3":
            command.append("--refresh")
    main(command)
    _pause(presenter)


def _elapsed(start: str, end: str) -> str:
    try:
        seconds = max(0.0, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds())
    except (TypeError, ValueError):
        return ""
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{int(seconds // 60)}m {seconds % 60:.0f}s"


def _show_error(args: argparse.Namespace, message: str, presenter: Presenter | None = None,
                hint: str = "") -> None:
    if getattr(args, "json_output", False):
        print(json.dumps({"error": message}), file=sys.stderr)
        return
    if not hint:
        lowered = message.casefold()
        if "uncommitted changes" in lowered or "dirty" in lowered:
            hint = "Commit or stash the repository changes, then try again."
        elif "schema_version" in lowered:
            hint = "Upgrade the project configuration, then try again."
        elif "tenant or workspace" in lowered or "signed-in identity" in lowered:
            hint = "Complete the browser-provider setup, then try again."
        else:
            hint = "Correct the item above, then try again."
    error_presenter = Presenter(
        stream=sys.stderr, animate=False, no_color=getattr(args, "no_color", False))
    error_presenter.error(message, hint)


if __name__ == "__main__":
    raise SystemExit(main())
