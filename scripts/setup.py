"""Install or update Maintain. One script for both; run it again any time.

scripts/setup.ps1 starts this file with the py launcher. It also runs
directly:  py -3 scripts\\setup.py

Every decision lives in small testable functions. Commands go through
one runner seam that the test suite replaces with a fake, so the logic
that failed on a real computer once can never regress silently again.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
MINIMUM = (3, 11)
# Manim's native dependencies publish no wheels for 3.14 yet.
EXPLAIN_CEILING = (3, 14)


@dataclass(frozen=True)
class Interpreter:
    version: tuple[int, int]
    path: str

    @property
    def label(self) -> str:
        return f"{self.version[0]}.{self.version[1]}"


class Runner:
    """The one seam between the decisions and the computer."""

    def run(self, argv: list[str], capture: bool = False):
        return subprocess.run(argv, capture_output=capture, text=True,
                              check=False)

    def which(self, name: str) -> str | None:
        return shutil.which(name)


def parse_py_list(text: str) -> list[Interpreter]:
    """Interpreters from `py -0p` output, old and new launcher formats:
    ` -V:3.13 *  C:\\...\\python.exe` or ` -3.12-64  C:\\...`."""
    found: list[Interpreter] = []
    for line in text.splitlines():
        match = re.match(
            r"\s*-(?:V:)?(\d+)\.(\d+)(?:-\d+)?\s+\*?\s*(\S.*?)\s*$", line)
        if match:
            found.append(Interpreter(
                (int(match.group(1)), int(match.group(2))),
                match.group(3)))
    return found


def explain_supported(version: tuple[int, int]) -> bool:
    return MINIMUM <= version < EXPLAIN_CEILING


def choose_interpreter(current: tuple[int, int],
                       listed: list[Interpreter]) -> Interpreter | None:
    """The newest installed Python that supports the video feature, when
    the current one does not. None keeps the current interpreter."""
    if explain_supported(current):
        return None
    candidates = [item for item in listed
                  if explain_supported(item.version)]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.version)


def list_interpreters(runner: Runner) -> list[Interpreter]:
    if runner.which("py") is None:
        return []
    try:
        listing = runner.run(["py", "-0p"], capture=True)
    except OSError:
        return []
    if listing.returncode != 0:
        return []
    return parse_py_list(listing.stdout or "")


def installed_package(runner: Runner, python: list[str]) -> str:
    """The pipx name of an existing Maintain install, or an empty
    string. The name keys every runpip call, so a probe against the
    wrong name can never hide an installed video feature again."""
    listing = runner.run([*python, "-m", "pipx", "list", "--short"],
                         capture=True)
    if listing.returncode != 0:
        return ""
    for line in (listing.stdout or "").splitlines():
        parts = line.split()
        if parts and parts[0] in {"sw-maintainer-agent", "maintain"}:
            return parts[0]
    return ""


def default_ask(prompt: str) -> str:
    if not sys.stdin.isatty():
        return "n"
    return input(prompt)


def main(runner: Runner | None = None,
         current: tuple[int, int] | None = None,
         platform: str | None = None,
         ask=default_ask) -> int:
    runner = runner or Runner()
    current = current or sys.version_info[:2]
    platform = platform or sys.platform
    label = f"{current[0]}.{current[1]}"
    print("== Maintain setup ==")

    print("1. Python check")
    if current < MINIMUM:
        print(f"   FAIL: Python {label} is too old. Install Python 3.11 "
              "or later from python.org. Then run this script again.")
        return 1
    print(f"   PASS: Python {label}")
    chosen = choose_interpreter(current, list_interpreters(runner))
    if chosen is not None:
        python = [chosen.path]
        print(f"   NOTE: The app installs with Python {chosen.label}, so "
              "the video feature works.")
    else:
        python = [sys.executable]
        if not explain_supported(current):
            print(f"   NOTE: The video feature needs Python 3.11 to 3.13; "
                  f"this computer has only {label}. The app installs "
                  "without the video feature. Install Python 3.13 and run "
                  "this script again to enable it.")

    print("2. pipx check")
    bootstrap = runner.run([*python, "-m", "pip", "install", "--user",
                            "--quiet", "--upgrade", "pipx"])
    if bootstrap.returncode != 0:
        print("   FAIL: pip could not install pipx. Read the output above.")
        return 1
    runner.run([*python, "-m", "pipx", "ensurepath"], capture=True)
    print("   PASS: pipx is ready")

    print("3. Maintain install or update (ui + explain)")
    package = installed_package(runner, python)
    updated = False
    if package:
        # Update day: pip in the app environment rebuilds Maintain and
        # touches only missing or outdated dependencies — no fresh
        # download of the satisfied ones.
        update = runner.run([*python, "-m", "pipx", "runpip", package,
                             "install", "--upgrade",
                             f"{REPOSITORY}[ui,explain]"])
        if update.returncode == 0:
            updated = True
            print("   PASS: Maintain is updated; satisfied dependencies "
                  "stayed in place")
        else:
            print("   The quick update did not finish. The script "
                  "reinstalls from a clean state.")
    if not updated:
        install = runner.run([*python, "-m", "pipx", "install", "--force",
                              f"{REPOSITORY}[ui,explain]"])
        if install.returncode != 0:
            print("   The full install did not finish. The script tries "
                  "the app without the video feature.")
            retry = runner.run([*python, "-m", "pipx", "install", "--force",
                                f"{REPOSITORY}[ui]"])
            if retry.returncode != 0:
                print("   FAIL: The Maintain install failed. Read the pipx "
                      "output above.")
                return 1
            print("   PASS: Maintain is installed without the video feature")
        else:
            print("   PASS: Maintain is installed")
    package = installed_package(runner, python) or "sw-maintainer-agent"

    print("4. ffmpeg check")
    if runner.which("ffmpeg"):
        print("   PASS: ffmpeg is present")
    elif runner.which("winget"):
        print("   ffmpeg is absent. The script installs it with winget.")
        winget = runner.run(["winget", "install", "--id", "Gyan.FFmpeg",
                             "-e", "--accept-source-agreements",
                             "--accept-package-agreements"])
        if winget.returncode != 0:
            print("   WARN: winget could not install ffmpeg. Install it "
                  "by hand: winget install ffmpeg")
    else:
        print("   WARN: ffmpeg and winget are absent. The video feature "
              "needs ffmpeg.")

    print("5. Start Menu shortcut")
    if platform != "win32":
        print("   NOTE: Not Windows; no shortcut is made.")
    else:
        where = runner.run([*python, "-m", "pipx", "environment", "--value",
                            "PIPX_BIN_DIR"], capture=True)
        bin_dir = (where.stdout or "").strip()
        if where.returncode != 0 or not bin_dir:
            print("   WARN: The pipx app folder is unknown. Start the app "
                  "with: maintain-ui")
        else:
            target = str(Path(bin_dir) / "maintain-ui.exe")
            script = (
                "$shell = New-Object -ComObject WScript.Shell; "
                "$lnk = $shell.CreateShortcut([System.IO.Path]::Combine("
                "$env:APPDATA, 'Microsoft', 'Windows', 'Start Menu', "
                "'Programs', 'Maintain.lnk')); "
                f"$lnk.TargetPath = '{target}'; "
                "$lnk.Save()")
            made = runner.run(["powershell", "-NoProfile", "-NonInteractive",
                               "-Command", script])
            if made.returncode == 0:
                print("   PASS: Maintain is in the Start Menu")
            else:
                print("   WARN: The shortcut was not made. Start the app "
                      "with: maintain-ui")

    print("6. Verification")

    def manim_version() -> str:
        shown = runner.run([*python, "-m", "pipx", "runpip", package,
                            "show", "manim"], capture=True)
        match = re.search(r"^Version:\s*(\S+)", shown.stdout or "",
                          re.MULTILINE)
        return match.group(1) if shown.returncode == 0 and match else ""

    version = manim_version()
    if version:
        print(f"   PASS: Manim {version} in the app environment")
    else:
        active = chosen.version if chosen is not None else current
        if explain_supported(active):
            answer = ask("   The video feature is off. Install it now? "
                         "[Y/n] ").strip().lower()
            if answer in ("", "y", "yes"):
                # Only the video dependencies are missing; pip adds
                # them into the existing environment.
                runner.run([*python, "-m", "pipx", "runpip", package,
                            "install", "--upgrade",
                            f"{REPOSITORY}[ui,explain]"])
                version = manim_version()
        if version:
            print(f"   PASS: Manim {version} in the app environment")
        else:
            print("   NOTE: The video feature is off. Manim is not in the "
                  "app environment.")

    print("")
    print("Done. Start the app with: maintain-ui")
    print("If the command is absent, open a new terminal first.")
    print("To update later, run this same script again.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
