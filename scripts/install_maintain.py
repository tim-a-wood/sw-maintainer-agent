"""Install or remove Maintain on Windows, without PowerShell.

The PowerShell installer stopped working on a managed machine: the
policy there refuses an unsigned script, and `-ExecutionPolicy Bypass`
does not override a policy set by the organisation. The install and the
uninstall both failed, with no way round it from the person's side.

A batch file has no such gate, so install.cmd is three lines that find
Python and start this. Everything else is here, in the standard
library, so it can be read and tested like the rest of the project.

Run it with --uninstall to take Maintain off the machine.
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shortcut import write_shortcut  # noqa: E402

REPOSITORY_URL = "https://github.com/tim-a-wood/sw-maintainer-agent.git"
PACKAGE_NAME = "sw-maintainer-agent"
# FR-V17: the same identity the app sets on itself. Windows matches the
# running window to a shortcut carrying this id, and takes the taskbar
# icon and name from that shortcut. With no match it falls back to the
# process image — the Python launcher stub — which is the Python icon
# the taskbar kept showing. A test holds the two literals equal.
APP_USER_MODEL_ID = "Maintain.SimpleUI"
# Manim's native dependencies publish no wheels for 3.14, so a Python
# that can run the video feature is preferred over merely the newest.
PYTHON_PREFERENCE = ("3.13", "3.12", "3.11")
MINIMUM_PYTHON = (3, 11)


@dataclass
class Report:
    ok: bool
    reason: str = ""
    version: str = ""
    lines: list[str] = field(default_factory=list)


def install_root() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "Programs" / "Maintain"
    return Path.home() / ".maintain" / "app"


def runtime_path(root: Path) -> Path:
    if os.name == "nt":
        return Path(root) / "venv" / "Scripts" / "python.exe"
    return Path(root) / "venv" / "bin" / "python"


def scripts_dir(root: Path) -> Path:
    return runtime_path(root).parent


def ui_executable() -> str:
    """The window application inside the private environment."""
    return "maintain-ui.exe" if os.name == "nt" else "maintain-ui"


def no_window() -> dict:
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}


def _text(command: list[str], run=None) -> tuple[int, str]:
    run = run or subprocess.run
    try:
        result = run(command, capture_output=True, text=True, check=False,
                     timeout=1800, **no_window())
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return result.returncode, f"{result.stdout or ''}{result.stderr or ''}"


def python_version(command: list[str], *, run=None) -> tuple[int, ...]:
    """What this interpreter reports, or an empty tuple."""
    code, output = _text(
        command + ["-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
        run=run)
    if code:
        return ()
    match = re.search(r"(\d+)\.(\d+)", output)
    return tuple(int(part) for part in match.groups()) if match else ()


def find_python(*, run=None, which=shutil.which) -> list[str]:
    """A Python that can build the private environment.

    The launcher's version switches come first, newest usable version
    downwards, so the video feature works where it can.
    """
    candidates: list[list[str]] = []
    launcher = which("py")
    if launcher:
        candidates.extend([launcher, f"-{version}"] for version in PYTHON_PREFERENCE)
    for name in ("python3", "python"):
        found = which(name)
        if found:
            candidates.append([found])
    candidates.append([sys.executable])
    for command in candidates:
        version = python_version(command, run=run)
        if version and version >= MINIMUM_PYTHON:
            return command
    return []


def find_git(*, which=shutil.which) -> str:
    return which("git") or ""


def newest_release_tag(repository: str = REPOSITORY_URL, *, git: str = "git",
                       run=None) -> str:
    """The newest published release, so a fresh install is current."""
    code, output = _text([git, "ls-remote", "--tags", repository], run=run)
    if code:
        return ""
    best: tuple[int, ...] = ()
    best_tag = ""
    for line in output.splitlines():
        match = re.match(r"^[0-9a-fA-F]{40}\s+refs/tags/(v\d+(?:\.\d+)*)(\^\{\})?$",
                         line.strip())
        if not match:
            continue
        tag = match.group(1)
        parts = tuple(int(piece) for piece in tag[1:].split("."))
        if parts > best:
            best, best_tag = parts, tag
    return best_tag


def requirement(reference: str, repository: str = REPOSITORY_URL,
                extras: str = "") -> str:
    """The bare VCS URL: pip reads PEP 508's "name @ git+url" as a path."""
    tag = reference.rsplit("/", 1)[-1]
    suffix = f"#egg={PACKAGE_NAME}" if extras else ""
    return f"git+{repository}@{tag}{suffix}"


def installed_version(runtime: Path, *, run=None) -> str:
    if not Path(runtime).is_file():
        return ""
    code, output = _text(
        [str(runtime), "-c", "import maintain; print(maintain.__version__)"],
        run=run)
    return "" if code else output.strip().splitlines()[-1] if output.strip() else ""


LAUNCHER = """@echo off
setlocal
set "MAINTAIN_HOME=%~dp0"
"%MAINTAIN_HOME%venv\\Scripts\\maintain.exe" %*
set MAINTAIN_EXIT=%ERRORLEVEL%
if not "%MAINTAIN_EXIT%"=="0" (
  echo.
  echo Maintain stopped with exit code %MAINTAIN_EXIT%.
  pause
)
exit /b %MAINTAIN_EXIT%
"""

UI_LAUNCHER = """@echo off
setlocal
set "MAINTAIN_HOME=%~dp0"
start "" "%MAINTAIN_HOME%venv\\Scripts\\maintain-ui.exe" %*
"""


def write_launchers(root: Path) -> tuple[Path, Path]:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    cli = root / "Maintain.cmd"
    cli.write_text(LAUNCHER, encoding="utf-8")
    ui = root / "Maintain-UI.cmd"
    ui.write_text(UI_LAUNCHER, encoding="utf-8")
    return cli, ui


def write_icon(root: Path, source: Path) -> Path:
    """The icon ships as base64 text, so it survives a text checkout."""
    target = Path(root) / "maintain.ico"
    raw = base64.b64decode(Path(source).read_text(encoding="utf-8").strip())
    target.write_bytes(raw)
    return target


def desktop_dir() -> Path:
    profile = os.environ.get("USERPROFILE") or str(Path.home())
    return Path(profile) / "Desktop"


def start_menu_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Maintain"


def make_shortcuts(root: Path, icon: Path) -> list[Path]:
    """Desktop and Start Menu entries, for the app and for the console.

    The app comes first and carries the plain name: a person who wants
    Maintain wants the window, not a command prompt.
    """
    root = Path(root)
    made: list[Path] = []
    home = os.environ.get("USERPROFILE") or str(Path.home())
    # FR-V17: the window shortcut points straight at the executable, not
    # at the .cmd. A batch file puts cmd.exe between the shortcut and
    # the app: a console flashes, and Windows cannot tie the shortcut to
    # the window it eventually produces.
    entries = [
        ("Maintain.lnk", scripts_dir(root) / ui_executable(),
         "Maintain - guided software changes with Copilot"),
        ("Maintain Console.lnk", root / "Maintain.cmd",
         "Maintain in a terminal"),
    ]
    for folder in (desktop_dir(), start_menu_dir()):
        for name, target, description in entries:
            try:
                made.append(write_shortcut(
                    folder / name, str(target), working_dir=home,
                    icon=f"{icon},0", description=description,
                    app_id=APP_USER_MODEL_ID))
            except OSError:
                continue
    return made


def install(reference: str = "", *, root: Path | None = None,
            repository: str = REPOSITORY_URL, icon_source: Path | None = None,
            run=None, which=shutil.which) -> Report:
    lines: list[str] = []
    root = Path(root) if root is not None else install_root()

    python = find_python(run=run, which=which)
    if not python:
        return Report(False, "Python 3.11 or later is required. Install it "
                             "from https://www.python.org/downloads/windows/ "
                             "and run this again.", lines=lines)
    lines.append(f"Python: {' '.join(python)}")
    # FR-V18: say at install time when the video feature cannot work.
    # Manim publishes no wheels above 3.13, and a person who only has
    # 3.14 found this out much later, at the render, with a message
    # that named a PowerShell script they could not run.
    chosen = python_version(python, run=run)
    if chosen and chosen > (3, 13):
        lines.append(
            f"Note: this Python is {chosen[0]}.{chosen[1]}. Everything works "
            "except the video feature of Explain code, which needs 3.11 to "
            "3.13. Install Python 3.13 from python.org and run this again "
            "to add it.")

    git = find_git(which=which)
    if not git:
        return Report(False, "Git is required. Install Git for Windows from "
                             "https://git-scm.com/download/win and run this "
                             "again.", lines=lines)

    if not reference:
        reference = newest_release_tag(repository, git=git, run=run)
        if not reference:
            return Report(False, "No published release was found to install.",
                          lines=lines)
    lines.append(f"Release: {reference}")

    runtime = runtime_path(root)
    # FR-V18: an environment already here is kept, which is right for
    # an update and wrong for the one case that matters — an
    # environment built on a Python that cannot make videos, when a
    # Python that can has since been installed. Re-running the
    # installer had no effect there, however many times it was run.
    existing = python_version([str(runtime)], run=run) if runtime.is_file() else ()
    if existing and chosen and existing > (3, 13) and chosen <= (3, 13):
        lines.append(
            f"The environment is on Python {existing[0]}.{existing[1]}, which "
            f"cannot make videos. Building it again on "
            f"{chosen[0]}.{chosen[1]}.")
        shutil.rmtree(root / "venv", ignore_errors=True)
    if not runtime.is_file():
        lines.append("Making the private Python environment...")
        code, output = _text(python + ["-m", "venv", str(root / "venv")], run=run)
        if code:
            return Report(False, f"The private Python environment could not be "
                                 f"made. {output.strip()}", lines=lines)
    if not runtime.is_file():
        return Report(False, f"No Python environment appeared at {runtime}.",
                      lines=lines)

    lines.append("Installing Maintain...")
    code, output = _text([str(runtime), "-m", "pip", "install", "--upgrade",
                          "--disable-pip-version-check", "--no-input",
                          f"{requirement(reference, repository)}"], run=run)
    lines.append(output.strip())
    if code:
        return Report(False, f"The install failed. {output.strip()}", lines=lines)

    version = installed_version(runtime, run=run)
    if not version:
        return Report(False, "Maintain installed but does not answer. "
                             "Run this again.", lines=lines)
    lines.append(f"Installed: {version}")

    write_launchers(root)
    icon = None
    source = icon_source or (Path(__file__).resolve().parent.parent
                             / "assets" / "maintain.ico.b64")
    if Path(source).is_file():
        icon = write_icon(root, Path(source))
    if icon is not None:
        made = make_shortcuts(root, icon)
        lines.append(f"Shortcuts: {len(made)}")
    return Report(True, version=version, lines=lines)


def uninstall(*, root: Path | None = None) -> Report:
    """Take it off the machine: the environment, then every shortcut."""
    root = Path(root) if root is not None else install_root()
    lines: list[str] = []
    removed = 0
    for folder in (desktop_dir(), start_menu_dir()):
        for name in ("Maintain.lnk", "Maintain Console.lnk", "Maintain UI.lnk"):
            target = folder / name
            if target.exists():
                try:
                    target.unlink()
                    removed += 1
                except OSError:
                    lines.append(f"This file stayed: {target}")
    if start_menu_dir().is_dir() and not any(start_menu_dir().iterdir()):
        start_menu_dir().rmdir()
    lines.append(f"Shortcuts removed: {removed}")
    if root.is_dir():
        shutil.rmtree(root, ignore_errors=True)
    if root.is_dir():
        return Report(False, f"Some files stayed in {root}. Close Maintain "
                             "and run this again.", lines=lines)
    lines.append(f"Removed: {root}")
    return Report(True, lines=lines)


def main(argv: list[str] | None = None, *, stream=None,
         wait_for_reader=None) -> int:
    parser = argparse.ArgumentParser(description="Install or remove Maintain.")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--reference", default="",
                        help="a release such as refs/tags/v1.2.3")
    parser.add_argument("--repository", default=REPOSITORY_URL)
    parser.add_argument("--install-root", default="")
    options = parser.parse_args(argv)

    out = stream if stream is not None else sys.stdout
    root = Path(options.install_root) if options.install_root else install_root()
    print("", file=out)
    print("{ MAINTAIN }  " + ("REMOVE" if options.uninstall else "INSTALL"),
          file=out)
    print("", file=out)

    report = (uninstall(root=root) if options.uninstall
              else install(options.reference, root=root,
                           repository=options.repository))
    for line in report.lines:
        if line:
            print(line, file=out)
    if report.ok:
        if not options.uninstall:
            print("", file=out)
            print(f"Maintain {report.version} is installed.", file=out)
            print("Open it from the Maintain icon on your desktop.", file=out)
        else:
            print("Maintain is removed.", file=out)
        return 0
    print("", file=out)
    print(report.reason, file=out)
    if wait_for_reader is not None:
        wait_for_reader()
    elif not os.environ.get("CI"):
        try:
            input("Press Enter to close this window")
        except (EOFError, KeyboardInterrupt, OSError):
            pass
    return 1


if __name__ == "__main__":   # pragma: no cover - the batch shim's entry
    raise SystemExit(main())
