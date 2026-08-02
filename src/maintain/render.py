"""Run the local Manim render for one checked scene file."""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .proc import hidden

RENDER_TIMEOUT_SECONDS = 600
SHEET_TIMEOUT_SECONDS = 120
OUTPUT_TAIL_BYTES = 4000


@dataclass(frozen=True)
class RenderResult:
    ok: bool
    message: str
    output_tail: str = ""
    video: Path | None = None
    sheet: Path | None = None


def resolve_manim_command(command: str) -> str:
    """The app environment's own manim wins for the default command.

    pipx exposes only the application's entry points on PATH, so the
    manim script installed by the explain extra lives beside the app's
    interpreter and `which` cannot see it."""
    if command != "manim" or shutil.which(command):
        return command
    name = "manim.exe" if sys.platform == "win32" else "manim"
    sibling = Path(sys.executable).with_name(name)
    if sibling.exists():
        return str(sibling)
    return command


def manim_available(command: str) -> bool:
    return shutil.which(command) is not None or Path(command).exists()


def _absent_message(manim_command: str,
                    version: tuple[int, int] | None = None) -> str:
    """Why Manim is absent, with the Python 3.14 cause named when it applies."""
    version = version or sys.version_info[:2]
    message = (f"Manim is absent. The command is not found: {manim_command}. ")
    if version >= (3, 14):
        return message + (
            f"Manim needs Python 3.11 to 3.13; this computer runs "
            f"{version[0]}.{version[1]}. Install Python 3.13, then run "
            "scripts/setup.ps1 again.")
    return message + ("Install it with: pip install maintain[explain], "
                      "and: winget install ffmpeg")


def contact_sheet(video: Path, work_dir: Path) -> Path | None:
    """One PNG with a frame each three seconds; best effort."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return None
    sheet = Path(work_dir) / "sheet.png"
    try:
        completed = subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-i", str(video),
             "-vf", "select='not(mod(n,180))',scale=480:-2,tile=4x4",
             "-frames:v", "1", str(sheet)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=SHEET_TIMEOUT_SECONDS,
            check=False, **hidden())
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0 or not sheet.is_file():
        return None
    return sheet


def render_scene(source: str, work_dir: Path, *, manim_command: str,
                 scene_class: str, quality: str = "-qh") -> RenderResult:
    """Write the scene into its own folder and render it there."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    scene_path = work_dir / "scene.py"
    scene_path.write_text(source, encoding="utf-8")
    manim_command = resolve_manim_command(manim_command)
    if not manim_available(manim_command):
        return RenderResult(ok=False, message=_absent_message(manim_command))
    try:
        completed = subprocess.run(
            [manim_command, quality, "scene.py", scene_class],
            cwd=work_dir, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=RENDER_TIMEOUT_SECONDS, check=False, **hidden())
    except subprocess.TimeoutExpired:
        return RenderResult(ok=False,
                            message="The render passed the time limit "
                                    f"({RENDER_TIMEOUT_SECONDS} seconds).")
    except OSError as exc:
        return RenderResult(ok=False, message=f"The render did not start: {exc}")
    tail = (completed.stdout + "\n" + completed.stderr)[-OUTPUT_TAIL_BYTES:]
    if completed.returncode != 0:
        return RenderResult(ok=False,
                            message="The render failed.", output_tail=tail)
    videos = sorted((work_dir / "media").rglob(f"{scene_class}.mp4"))
    if not videos:
        return RenderResult(ok=False,
                            message="The render ended but made no video.",
                            output_tail=tail)
    return RenderResult(ok=True, message="The render is complete.",
                        output_tail=tail, video=videos[-1],
                        sheet=contact_sheet(videos[-1], work_dir))
