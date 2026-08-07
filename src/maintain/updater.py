"""Apply one Maintain update, from outside the environment it replaces.

The app cannot rebuild its own private environment while it runs, so it
copies this file to a temporary folder and starts it there, detached,
then exits. This module therefore imports nothing from ``maintain`` and
nothing outside the standard library: the copy in the temporary folder
must run on its own, and it must hold no file in the environment it is
about to change.

Every step that touches the machine — waiting for the app, installing,
asking the runtime its version, starting the app again — takes the
callable it uses as an argument. That is what makes this testable off
Windows, which the PowerShell script it replaces never was.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPOSITORY_URL = "https://github.com/tim-a-wood/sw-maintainer-agent.git"
PACKAGE_NAME = "sw-maintainer-agent"
WAIT_SECONDS = 120.0


@dataclass
class UpdateResult:
    """What happened, in a shape a test can read and a log can print."""

    ok: bool
    reason: str = ""
    installed: str = ""
    wanted: str = ""
    lines: list[str] = field(default_factory=list)


def wanted_version(reference: str) -> str:
    """The plain version in a release reference: refs/tags/v1.2.3 -> 1.2.3.

    A reference that is not a version — a branch, in the smoke test —
    has no version to compare against, and returns empty. The update
    then only requires that the runtime answers at all.
    """
    tag = reference.rsplit("/", 1)[-1]
    candidate = tag[1:] if tag.startswith("v") else tag
    if not re.fullmatch(r"\d+(\.\d+)*", candidate):
        return ""
    return candidate


def requirement(reference: str, repository: str = REPOSITORY_URL) -> str:
    """The pip requirement for one release.

    pip fetches a tag directly, so the update needs no clone of its own
    and no second installer run.

    The bare VCS URL, not PEP 508's ``name @ git+url``: pip 24 reads
    that longer form as a file path and refuses it — "It looks like a
    path" — which a real update against a real environment showed at
    once. pip takes the project name from the fetched metadata.
    """
    tag = reference.rsplit("/", 1)[-1]
    return f"git+{repository}@{tag}"


def install_root() -> Path:
    """Where Maintain lives on this machine.

    The app runs from the private environment, so its own interpreter
    names the folder: ...\\Programs\\Maintain\\venv\\Scripts\\python.exe.
    A build that runs from somewhere else — a checkout, a test — falls
    back to the place the installer uses.
    """
    executable = Path(sys.executable).resolve()
    for parent in executable.parents:
        if (parent / "venv").is_dir() and parent.name.lower() == "maintain":
            return parent
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "Programs" / "Maintain"
    return Path.home() / ".maintain" / "app"


def runtime_path(install_root: Path) -> Path:
    """The private environment's interpreter."""
    root = Path(install_root)
    windows = root / "venv" / "Scripts" / "python.exe"
    return windows if os.name == "nt" else root / "venv" / "bin" / "python"


def no_window() -> dict:
    """The flag that keeps a console from flashing on Windows.

    `maintain.proc.hidden` does this for the app, but this file must
    not import from the package it replaces, so the flag is named
    here.
    """
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}


def process_alive(pid: int) -> bool:
    """True while the process is still there."""
    if pid <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, check=False, **no_window())
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_for_exit(pid: int, *, timeout: float = WAIT_SECONDS,
                  alive=None, sleep=None, clock=None) -> bool:
    """Wait for the app to close. True when it is gone."""
    # The seams resolve here, not in the signature: a default bound at
    # definition time cannot be replaced by a test or a caller.
    alive = alive or process_alive
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    if pid <= 0:
        return True
    deadline = clock() + timeout
    while clock() < deadline:
        if not alive(pid):
            return True
        sleep(0.5)
    return not alive(pid)


def installed_version(runtime: Path, *, run=None) -> str:
    """What the installed runtime says it is, or empty.

    This is the one honest answer to "did the update take?". The script
    this replaces asked PowerShell's ``$?`` after calling the installer,
    which reports the installer's last statement and not its outcome —
    so a failed install reported success and started the old version
    again.
    """
    run = run or subprocess.run
    if not Path(runtime).is_file():
        return ""
    try:
        result = run([str(runtime), "-c",
                      "import maintain; print(maintain.__version__)"],
                     capture_output=True, text=True, check=False, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def install(runtime: Path, reference: str, *, repository: str = REPOSITORY_URL,
            run=None) -> tuple[bool, str]:
    """Put the release into the existing environment.

    An update is not a fresh install: the environment, the shortcuts,
    and the launcher are already there, so only the package changes.
    """
    run = run or subprocess.run
    command = [str(runtime), "-m", "pip", "install", "--upgrade",
               "--disable-pip-version-check", "--no-input",
               requirement(reference, repository)]
    try:
        result = run(command, capture_output=True, text=True, check=False,
                     timeout=1800)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    output = f"{result.stdout or ''}{result.stderr or ''}".strip()
    if result.returncode != 0:
        return False, output or f"pip stopped with code {result.returncode}."
    return True, output


def launcher_path(install_root: Path) -> Path:
    return Path(install_root) / "Maintain.cmd"


def relaunch(install_root: Path, *, popen=None) -> bool:
    """Start the app again. A missing launcher is not a failed update."""
    popen = popen or subprocess.Popen
    launcher = launcher_path(install_root)
    if not launcher.is_file():
        return False
    creation = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if os.name == "nt" else 0
    try:
        popen([str(launcher)], creationflags=creation, close_fds=True)
    except OSError:
        return False
    return True


def update(reference: str, install_root: Path, *, app_pid: int = 0,
           repository: str = REPOSITORY_URL, no_relaunch: bool = False,
           run=None, popen=None, alive=None, sleep=None,
           clock=None) -> UpdateResult:
    """Wait, install, then check what is really installed."""
    lines: list[str] = []

    def say(message: str) -> None:
        lines.append(message)

    wanted = wanted_version(reference)
    runtime = runtime_path(install_root)
    say(f"Release: {reference}")
    say(f"Wanted: {wanted}")

    if app_pid > 0:
        say(f"Waiting for the app (process {app_pid}) to close...")
        if not wait_for_exit(app_pid, alive=alive, sleep=sleep, clock=clock):
            return UpdateResult(
                False, "The app did not close. Close Maintain and update again.",
                wanted=wanted, lines=lines)

    before = installed_version(runtime, run=run)
    say(f"Installed now: {before or 'not found'}")
    if not runtime.is_file():
        return UpdateResult(
            False,
            f"No Maintain environment at {runtime}. Install again from "
            "the GitHub releases page.",
            wanted=wanted, lines=lines)

    say("Installing the release...")
    ok, output = install(runtime, reference, repository=repository, run=run)
    if output:
        say(output)
    if not ok:
        return UpdateResult(False, f"The install failed. {output}".strip(),
                            installed=before, wanted=wanted, lines=lines)

    after = installed_version(runtime, run=run)
    if not after:
        return UpdateResult(
            False, "The install finished but no Maintain runtime answers.",
            wanted=wanted, lines=lines)
    if wanted and after != wanted:
        return UpdateResult(
            False,
            f"The install finished but the version is still {after}, "
            f"not {wanted}.",
            installed=after, wanted=wanted, lines=lines)

    say(f"Installed: {after}")
    if not no_relaunch and relaunch(install_root, popen=popen):
        say("Starting Maintain...")
    return UpdateResult(True, installed=after, wanted=wanted, lines=lines)


def log_path(install_root: Path) -> Path:
    return Path(install_root) / "update.log"


def write_log(install_root: Path, result: UpdateResult) -> Path:
    """Keep the record. The console window closes with this process."""
    path = log_path(install_root)
    body = list(result.lines)
    body.append("The update is complete." if result.ok else "The update failed.")
    if result.reason:
        body.append(result.reason)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(body) + "\n\n")
    except OSError:
        pass
    return path


def main(argv: list[str] | None = None, *, stream=None,
         wait_for_reader=None) -> int:
    parser = argparse.ArgumentParser(description="Apply one Maintain update.")
    parser.add_argument("--reference", required=True,
                        help="the release, such as refs/tags/v1.2.3")
    parser.add_argument("--install-root", required=True)
    parser.add_argument("--app-process-id", type=int, default=0)
    parser.add_argument("--repository", default=REPOSITORY_URL)
    parser.add_argument("--no-relaunch", action="store_true")
    options = parser.parse_args(argv)

    out = stream if stream is not None else sys.stdout
    install_root = Path(options.install_root)
    print("", file=out)
    print("{ MAINTAIN }  UPDATE", file=out)
    print("", file=out)

    result = update(options.reference, install_root,
                    app_pid=options.app_process_id,
                    repository=options.repository,
                    no_relaunch=options.no_relaunch)
    for line in result.lines:
        print(line, file=out)
    path = write_log(install_root, result)
    if result.ok:
        print("The update is complete.", file=out)
        return 0
    print("", file=out)
    print("The update failed.", file=out)
    print(result.reason, file=out)
    print(f"Update log: {path}", file=out)
    print("Send that file to report this.", file=out)
    if wait_for_reader is not None:
        wait_for_reader()
    elif not os.environ.get("CI"):
        try:
            input("Press Enter to close this window")
        except (EOFError, KeyboardInterrupt, OSError):
            # No console to hold open. The log is the record.
            pass
    return 1


if __name__ == "__main__":   # pragma: no cover - the temporary copy's entry
    raise SystemExit(main())
