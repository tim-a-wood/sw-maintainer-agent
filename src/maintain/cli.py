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
from .presenter import Presenter
from .presenter import QuietPresenter
from .repository_memory import remember_repository, repository_for_cli


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
    init.add_argument("--provider", choices=["codex", "file-exchange", "chatgpt-browser",
                                              "m365-browser"], default="codex")
    init.add_argument("--yes", action="store_true", help="Write the displayed configuration")
    for name in ("feature", "issue"):
        item = commands.add_parser(name, help=f"Start a verified {name} workflow")
        item.add_argument("request", nargs="+", help="Required outcome or issue report")
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
    config_cmd.add_argument("--provider", choices=["codex", "file-exchange", "chatgpt-browser",
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
    commands.add_parser("doctor", help="Check configuration and providers")
    return root


def _config(args: argparse.Namespace) -> ProjectConfig:
    path = Path(args.config).expanduser() if args.config else find_config(Path(args.repo))
    if path is None:
        raise ConfigurationError(f"No {CONFIG_NAME} was found. Run maintain init.")
    config = ProjectConfig.load(path)
    remember_repository(config.repository)
    return config


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    presenter = None
    try:
        if args.command != "init":
            if args.repo is None and args.config:
                args.repo = str(Path(args.config).expanduser().resolve().parent)
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
            remember_repository(repository)
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
            presenter.run_header(args.command, request, config.name, _provider_label(config))
            record = engine.start(args.command, request)
            _summary(record, args.json_output, presenter, command_prefix=_command_prefix(config))
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
                shown = json.loads(config.path.read_text())
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
                        result = {"ready": True}
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
                        if result.get("model"):
                            facts.append(("Model", str(result["model"])))
                        presenter.outcome(
                            "Compatible" if args.action == "check" else "Ready",
                            (f"The {name} browser is compatible."
                             if args.action == "check"
                             else f"The {name} provider is ready."),
                            facts=facts, tone="success")
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
                presenter.outcome(
                    "Ready", "Maintain can start verified work.",
                    facts=[(name.replace("_", " "), result.title())
                           for name, result in checks.items()], tone="success")
        return 0
    except MaintainError as exc:
        _show_error(args, str(exc), presenter)
        return exc.exit_code
    except KeyboardInterrupt:
        _show_error(args, "The current operation was cancelled.", presenter,
                    "Use View runs, then resume to continue saved work.")
        return 130
    except (OSError, ValueError) as exc:
        _show_error(args, str(exc), presenter)
        return 1


def _load(config: ProjectConfig, run_id: str):
    from .models import RunRecord
    path = AuditStore(config.runtime_root, run_id).run_dir / "run.json"
    if not path.is_file():
        raise ConfigurationError(f"Run does not exist: {run_id}")
    record = RunRecord.from_dict(json.loads(path.read_text()))
    if Path(record.repository).resolve() != config.repository.resolve():
        raise ConfigurationError("The run belongs to a different project.")
    return record


def _run_values(config: ProjectConfig) -> list[dict]:
    values: list[dict] = []
    if not config.runtime_root.exists():
        return values
    for path in sorted(config.runtime_root.glob("*/run.json"), reverse=True):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if Path(str(value.get("repository", ""))).resolve() == config.repository.resolve():
                values.append(value)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return values


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
    return (f"maintain --repo {shlex.quote(str(config.repository))} "
            f"--config {shlex.quote(str(config.path))}")


def _summary(record, json_output: bool = False, presenter: Presenter | None = None,
             interactive: bool = False, command_prefix: str = "maintain") -> None:
    if json_output:
        print(json.dumps(record.to_dict(), sort_keys=True))
        return
    presenter = presenter or Presenter(animate=False)
    changed = record.evidence.get("changed_files", [])
    review = record.evidence.get("review", {})
    checks = record.evidence.get("tests", {}).get("commands", [])
    facts = [("Run", record.run_id)]
    elapsed = _elapsed(record.created_at, record.updated_at)
    if elapsed:
        facts.append(("Elapsed", elapsed))
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
        passed = sum(item.get("exit_code") == 0 for item in checks)
        facts.append(("Checks", f"{passed} of {len(checks)} passed"))
    if record.branch:
        facts.append(("Branch", record.branch))

    state = record.state
    if state == "awaiting_acceptance":
        presenter.outcome(
            "Review ready", "The change is implemented, reviewed, and tested.",
            facts=facts,
            actions=[] if interactive else [
                f"Review the change: {command_prefix} diff {record.run_id}",
                f"Accept it: {command_prefix} accept {record.run_id}",
            ],
            tone="accent",
        )
    elif state == "accepted":
        presenter.outcome(
            "Accepted", "The verified change is approved.", facts=facts,
            actions=[] if interactive else [
                f"Create the commit: {command_prefix} deliver {record.run_id}"
            ],
            tone="success")
    elif state == "delivered":
        commit = record.evidence.get("delivery", {}).get("commit", "")
        integrated = record.evidence.get("delivery", {}).get("integrated_branch", "")
        presenter.outcome(
            "Delivered",
            (f"The verified change is now on {integrated}." if integrated
             else "The verified commit is ready on its maintenance branch."),
            facts=[*facts, ("Commit", commit), ("Updated", integrated)], tone="success",
        )
    elif state == "cancelled":
        presenter.outcome("Cancelled", "The run stopped and its evidence was saved.",
                          facts=facts, tone="muted")
    elif state in {"needs_human", "needs_human_delivery"}:
        actions = (["Return to the menu and choose Continue saved work after you fix this item."]
                   if interactive else
                   [f"Fix the item above, then continue: {command_prefix} resume {record.run_id}",
                    f"View the saved evidence: {command_prefix} status {record.run_id}"])
        presenter.outcome(
            "Action needed", "This run is paused.", record.error, facts,
            actions,
            tone="warning",
        )
    elif state == "failed":
        presenter.outcome("Stopped", "Maintain could not complete this run.",
                          record.error, facts,
                          [f"View the saved evidence: {command_prefix} status {record.run_id}"],
                          tone="danger")
    else:
        presenter.outcome("Saved", f"The run is {state.replace('_', ' ')}.",
                          record.error, facts, tone="accent")


def _home(args: argparse.Namespace) -> int:
    if not sys.stdin.isatty():
        print("Choose feature, issue, resume, runs, or doctor.", file=sys.stderr)
        return 2
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
        )
        choice = presenter.ask("Choose", "1").casefold()
        if choice == "q":
            return 0
        if choice == "s":
            if config is not None:
                presenter.error("This project is already set up.")
                _pause(presenter)
                continue
            if _interactive_setup(args, presenter) == 0:
                continue
            _pause(presenter)
            continue
        if choice not in {"1", "2", "3", "4", "5"}:
            presenter.error("Choose 1, 2, 3, 4, 5, S, or Q.")
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
            presenter.run_header(mode, request, config.name, _provider_label(config))
            record = engine.start(mode, request)
            _summary(record, presenter=presenter, interactive=True)
            _interactive_run(engine, record, presenter)
            continue
        if choice == "3":
            selected = _choose_run(config, presenter)
            if selected is None:
                continue
            record = _load(config, selected)
            if record.state in {"awaiting_acceptance", "accepted"}:
                _summary(record, presenter=presenter, interactive=True)
                _interactive_run(engine, record, presenter)
            elif record.state == "needs_human_delivery":
                _summary(record, presenter=presenter, interactive=True)
                _interactive_delivery(engine, record, presenter)
            elif record.state in {"delivered", "failed", "cancelled"}:
                _summary(record, presenter=presenter, interactive=True)
                _pause(presenter)
            else:
                record = engine.resume(record.run_id)
                _summary(record, presenter=presenter, interactive=True)
                _interactive_run(engine, record, presenter)
            continue
        rows = [{key: str(value.get(key, ""))
                 for key in ("run_id", "state", "mode", "request")}
                for value in _run_values(config)]
        presenter.saved_runs(rows, selectable=False)
        _pause(presenter)


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


def _choose_run(config: ProjectConfig, presenter: Presenter) -> str | None:
    values = _continuable_values(config)
    if not values:
        presenter.outcome("Saved work", "There are no runs to continue.", tone="muted")
        _pause(presenter)
        return None
    rows = [{**{key: str(value.get(key, ""))
                for key in ("run_id", "state", "mode", "request")},
             "index": str(index)} for index, value in enumerate(values, 1)]
    presenter.saved_runs(rows, selectable=True)
    while True:
        choice = presenter.ask("Choose a run", "1").casefold()
        if choice in {"b", "q"}:
            return None
        try:
            return rows[int(choice) - 1]["run_id"]
        except (ValueError, IndexError):
            presenter.console.print("Choose a listed run number or B to go back.",
                                    style="warning")


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
        presenter.complete("ACCEPT", "Verified change accepted")
        try:
            record = engine.deliver(record.run_id)
            source_branch = str(record.evidence.get("source_branch", ""))
            if update_source and source_branch:
                record = engine.integrate(record.run_id, source_branch, confirmed=True)
                presenter.complete("UPDATE", f"Updated {source_branch}")
            elif update_source:
                presenter.failed("UPDATE", "Source checkout has no branch; kept the verified branch")
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
    presenter.menu_line("1", "Try the branch update again", "Default")
    presenter.menu_line("2", "Keep the verified branch only", "Finish without updating")
    presenter.menu_line("b", "Keep it saved", "Return to the menu", quiet=True)
    choice = presenter.ask("Choose", "1").casefold()
    if choice == "b":
        return
    try:
        if choice == "1":
            source_branch = str(record.evidence.get("source_branch", ""))
            if not source_branch:
                raise DeliveryError("The source checkout has no recorded branch.")
            record = engine.integrate(record.run_id, source_branch, confirmed=True)
            presenter.complete("UPDATE", f"Updated {source_branch}")
        elif choice == "2":
            record = engine.keep_delivered_branch(record.run_id)
            presenter.complete("DELIVER", "Kept the verified maintenance branch")
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
    presenter.section("ASSISTANT", "Model preference", f"Current: {current}")
    presenter.console.print()
    presenter.menu_line("1", "Change model", "Use the saved model list")
    presenter.menu_line("2", "Refresh and change", "Retrieve models from the browser")
    presenter.menu_line("3", "Check compatibility", "Inspect the browser without sending")
    presenter.menu_line("b", "Back", "", quiet=True)
    choice = presenter.ask("Choose", "1").casefold()
    if choice == "b":
        return
    if choice not in {"1", "2", "3"}:
        presenter.error("Choose 1, 2, 3, or B.")
        _pause(presenter)
        return
    command = ["--repo", str(config.repository)]
    if args.no_animation:
        command.append("--no-animation")
    if args.no_color:
        command.append("--no-color")
    if choice == "3":
        command.extend(["provider", "check", profile])
    else:
        command.extend(["provider", "model", profile])
        if choice == "2":
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
