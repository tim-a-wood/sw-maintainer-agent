"""Run the local Manim render for one checked scene file."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

RENDER_TIMEOUT_SECONDS = 600
OUTPUT_TAIL_BYTES = 4000


@dataclass(frozen=True)
class RenderResult:
    ok: bool
    message: str
    output_tail: str = ""
    video: Path | None = None


def manim_available(command: str) -> bool:
    return shutil.which(command) is not None


def render_scene(source: str, work_dir: Path, *, manim_command: str,
                 scene_class: str, quality: str = "-qh") -> RenderResult:
    """Write the scene into its own folder and render it there."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    scene_path = work_dir / "scene.py"
    scene_path.write_text(source, encoding="utf-8")
    if not manim_available(manim_command):
        return RenderResult(
            ok=False,
            message=f"Manim is absent. The command is not found: "
                    f"{manim_command}. Install it with: "
                    "pip install maintain[explain], and: winget install ffmpeg")
    try:
        completed = subprocess.run(
            [manim_command, quality, "scene.py", scene_class],
            cwd=work_dir, capture_output=True, text=True,
            timeout=RENDER_TIMEOUT_SECONDS)
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
                        output_tail=tail, video=videos[-1])
