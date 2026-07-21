"""Maintain command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from . import __version__
from .audit import AuditStore, atomic_write, cleanup_runs
from .config import CONFIG_NAME, ProjectConfig, default_config, find_config
from .engine import WorkflowEngine
from .errors import ConfigurationError, MaintainError
from .presenter import Presenter
from .presenter import QuietPresenter


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="maintain", description="Complete verified maintenance work.")
    root.add_argument("--version", action="version", version=f"Maintain {__version__}")
    root.add_argument("--repo", default=".", help="Target repository or path inside it")
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
    audit.add_argument("--older-than-days", type=int, default=365)
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
    provider.add_argument("action", choices=["list", "doctor", "login"], nargs="?", default="list")
    provider.add_argument("profile", nargs="?")
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
    return ProjectConfig.load(path)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    presenter = None
    try:
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
            print("Proposed configuration:")
            print(rendered, end="")
            if not args.yes:
                if not sys.stdin.isatty():
                    raise ConfigurationError("Run init again with --yes to write this configuration.")
                if input("Write this configuration? [y/N]: ").strip().casefold() not in {"y", "yes"}:
                    raise ConfigurationError("Configuration was not written.")
            atomic_write(path, rendered.encode())
            print(f"Created {path}")
            return 0
        if args.command == "config" and args.action in {"upgrade", "migrate"}:
            from .migration import migrate_v1
            path = Path(args.config).expanduser() if args.config else find_config(Path(args.repo))
            if path is None:
                raise ConfigurationError(f"No {CONFIG_NAME} was found.")
            backup, report = migrate_v1(path, args.provider)
            print(f"Configuration migrated. Backup: {backup}")
            print(f"Legacy run report: {report}")
            return 0
        config = _config(args)
        presenter = (QuietPresenter() if args.json_output else
                     Presenter(animate=not args.no_animation, no_color=args.no_color))
        engine = WorkflowEngine(config, presenter)
        if args.command in {"feature", "issue"}:
            request = " ".join(args.request)
            presenter.run_header(args.command, request, config.name, _provider_label(config))
            record = engine.start(args.command, request)
            _summary(record, args.json_output, presenter)
        elif args.command == "resume":
            record = engine.resume(args.run_id)
            _summary(record, args.json_output, presenter)
        elif args.command == "accept":
            record = engine.accept(args.run_id)
            _summary(record, args.json_output, presenter)
        elif args.command == "deliver":
            record = engine.deliver(args.run_id)
            if args.current_branch:
                record = engine.integrate(args.run_id, args.current_branch,
                                          confirmed=args.confirm_current_branch)
            _summary(record, args.json_output, presenter)
        elif args.command == "feedback":
            record = engine.feedback(args.run_id, " ".join(args.message))
            _summary(record, args.json_output, presenter)
        elif args.command == "cancel":
            _summary(engine.cancel(args.run_id), args.json_output, presenter)
        elif args.command == "status":
            record = _load(config, args.run_id)
            gates = engine.gate_status(record)
            if args.json_output:
                print(json.dumps({**record.to_dict(), "gates": gates}, sort_keys=True))
            else:
                _summary(record, presenter=presenter)
                presenter.gates(gates)
        elif args.command == "audit":
            if args.action == "cleanup":
                removed = cleanup_runs(config.runtime_root, args.older_than_days)
                print(f"Removed {len(removed)} expired unaccepted run(s).")
                return 0
            if not args.run_id:
                raise ConfigurationError("A run ID is required.")
            audit_store = AuditStore(config.runtime_root, args.run_id)
            result = audit_store.verify()
            if args.action == "verify":
                presenter.outcome("Verified", "The audit record is complete and unchanged.",
                                  facts=[("Run", args.run_id),
                                         ("Events", str(result["events"]))], tone="success")
            else:
                output = Path(args.output) if args.output else Path(f"{args.run_id}-audit.zip")
                exported = audit_store.export(output)
                presenter.outcome("Exported", "The verified audit package is ready.",
                                  facts=[("Run", args.run_id), ("File", str(exported))],
                                  tone="success")
        elif args.command == "diff":
            record = _load(config, args.run_id)
            from .workspace import git
            if record.state == "delivered":
                print(git(Path(record.worktree), "diff", "--binary", record.base_commit, "HEAD"))
            else:
                print(engine.workspaces.diff(Path(record.worktree)).text)
        elif args.command == "evidence":
            presenter.console.print_json(
                json.dumps(_load(config, args.run_id).evidence, indent=2))
        elif args.command == "config":
            if args.action == "show":
                presenter.console.print_json(
                    json.dumps(json.loads(config.path.read_text()), indent=2))
            else:
                presenter.outcome("Valid", "The project configuration is valid.",
                                  facts=[("Project", config.name)], tone="success")
        elif args.command == "provider":
            if args.action == "list":
                rows = [(role, profile,
                         str(config.providers.get(profile, {}).get("type", "missing")))
                        for role, profile in sorted(config.roles.items())]
                presenter.provider_assignments(rows)
            else:
                names = [args.profile] if args.profile else sorted(set(config.roles.values()))
                for name in names:
                    if name not in config.providers:
                        raise ConfigurationError(f"Provider profile does not exist: {name}")
                    provider = engine.provider_builder(name, config.providers[name],
                                                       config.runtime_root.parent / "browser")
                    if args.action == "login" and not provider.capabilities.browser_automation:
                        raise ConfigurationError("Login is available only for browser providers.")
                    if args.action == "login":
                        provider.login()
                    provider.preflight()
                    presenter.outcome("Ready", f"The {name} provider is ready.",
                                      tone="success")
        elif args.command == "workspace":
            if args.action == "list" and config.runtime_root.exists():
                for path in sorted(config.runtime_root.glob("*/run.json"), reverse=True):
                    value = json.loads(path.read_text())
                    print(f"{value['run_id']}  {value['state']}  {value['worktree']}")
            elif args.action in {"open", "cleanup"}:
                if not args.run_id:
                    raise ConfigurationError("A run ID is required.")
                record = _load(config, args.run_id)
                if args.action == "open":
                    print(record.worktree)
                else:
                    from .workspace import git
                    git(config.repository, "worktree", "remove", record.worktree)
                    print(f"Removed workspace: {args.run_id}")
        elif args.command == "runs":
            rows = []
            if config.runtime_root.exists():
                for path in sorted(config.runtime_root.glob("*/run.json"), reverse=True):
                    value = json.loads(path.read_text())
                    if args.state and value["state"] != args.state:
                        continue
                    rows.append({key: str(value.get(key, ""))
                                 for key in ("run_id", "state", "mode", "request")})
            presenter.saved_runs(rows)
        elif args.command == "doctor":
            checks = engine.doctor()
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
    return RunRecord.from_dict(json.loads(path.read_text()))


def _summary(record, json_output: bool = False, presenter: Presenter | None = None) -> None:
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
            actions=[f"Review the change: maintain diff {record.run_id}",
                     f"Accept it: maintain accept {record.run_id}"],
            tone="accent",
        )
    elif state == "accepted":
        presenter.outcome(
            "Accepted", "The verified change is approved.", facts=facts,
            actions=[f"Create the commit: maintain deliver {record.run_id}"], tone="success")
    elif state == "delivered":
        commit = record.evidence.get("delivery", {}).get("commit", "")
        presenter.outcome("Delivered", "The verified commit is ready.",
                          facts=[*facts, ("Commit", commit)], tone="success")
    elif state == "cancelled":
        presenter.outcome("Cancelled", "The run stopped and its evidence was saved.",
                          facts=facts, tone="muted")
    elif state in {"needs_human", "needs_human_delivery"}:
        presenter.outcome(
            "Action needed", "This run is paused.", record.error, facts,
            [f"Fix the item above, then continue: maintain resume {record.run_id}",
             f"View the saved evidence: maintain status {record.run_id}"],
            tone="warning",
        )
    elif state == "failed":
        presenter.outcome("Stopped", "Maintain could not complete this run.",
                          record.error, facts,
                          [f"View the saved evidence: maintain status {record.run_id}"],
                          tone="danger")
    else:
        presenter.outcome("Saved", f"The run is {state.replace('_', ' ')}.",
                          record.error, facts, tone="accent")


def _home(args: argparse.Namespace) -> int:
    if not sys.stdin.isatty():
        print("Choose feature, issue, resume, runs, or doctor.", file=sys.stderr)
        return 2
    presenter = Presenter(animate=not args.no_animation, no_color=args.no_color)
    project, provider = _home_context(args)
    presenter.home(project, provider)
    choice = presenter.ask("Select an option", "1")
    global_args = ["--repo", args.repo]
    if args.config:
        global_args += ["--config", args.config]
    if args.no_animation:
        global_args.append("--no-animation")
    if args.no_color:
        global_args.append("--no-color")
    if choice == "1":
        presenter.console.print()
        request = presenter.ask("What should change?")
        return main([*global_args, "feature", request])
    if choice == "2":
        presenter.console.print()
        request = presenter.ask("What is wrong?")
        return main([*global_args, "issue", request])
    if choice == "3":
        presenter.console.print()
        run_id = presenter.ask("Run ID")
        return main([*global_args, "resume", run_id])
    if choice == "4":
        return main([*global_args, "runs"])
    if choice.lower() == "q":
        return 0
    presenter.error("Choose 1, 2, 3, 4, or q.")
    return 2


def _home_context(args: argparse.Namespace) -> tuple[str, str]:
    try:
        config = _config(args)
    except (MaintainError, OSError, ValueError):
        return "", "Setup required"
    return config.name, _provider_label(config)


def _provider_label(config: ProjectConfig) -> str:
    kinds = {config.providers.get(profile, {}).get("type", "")
             for profile in config.roles.values()}
    labels = {
        "chatgpt_browser": "ChatGPT",
        "m365_copilot_browser": "Microsoft 365 Copilot",
        "codex_cli": "Codex",
        "openai_responses": "OpenAI Responses",
        "file_exchange": "File exchange",
        "command": "Enterprise assistant",
    }
    shown = [labels.get(kind, kind.replace("_", " ").title()) for kind in sorted(kinds) if kind]
    return ", ".join(shown)


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
