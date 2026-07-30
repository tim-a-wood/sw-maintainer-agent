"""Geometry probe: run the scene without output and measure the boxes.

The probe script is standalone — it runs with the Python that has
Manim, which can be a different environment than the app. It records
the box of every object after each play call, then reports text that
leaves the frame or text wider than its card. Any probe trouble means
no findings; the probe never blocks the render.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

PROBE_TIMEOUT_SECONDS = 240

PROBE_SOURCE = '''"""Standalone geometry probe for one Manim scene file."""
import json
import sys

FRAME_HALF_WIDTH = 7.11
FRAME_HALF_HEIGHT = 4.0
MARGIN = 0.15
SLACK = 0.06
MAX_FAULTS = 10


def check_records(records):
    faults = []
    seen = set()
    for r in records:
        if len(faults) >= MAX_FAULTS:
            break
        name, play = r["name"], r["play"]
        if (r["right"] > FRAME_HALF_WIDTH - MARGIN
                or r["left"] < -(FRAME_HALF_WIDTH - MARGIN)
                or r["top"] > FRAME_HALF_HEIGHT - MARGIN
                or r["bottom"] < -(FRAME_HALF_HEIGHT - MARGIN)):
            key = (name, "frame")
            if key not in seen:
                seen.add(key)
                faults.append("The text %s leaves the frame at play %d."
                              % (name, play))
        card = r.get("card")
        if card and (r["left"] < card["left"] - SLACK
                     or r["right"] > card["right"] + SLACK
                     or r["top"] > card["top"] + SLACK
                     or r["bottom"] < card["bottom"] - SLACK):
            key = (name, "card")
            if key not in seen:
                seen.add(key)
                faults.append("The text %s is wider than its card at "
                              "play %d." % (name, play))
    return faults


def _box(m):
    try:
        return {"left": float(m.get_left()[0]),
                "right": float(m.get_right()[0]),
                "top": float(m.get_top()[1]),
                "bottom": float(m.get_bottom()[1])}
    except Exception:
        return None


def _walk(m, card, play, records):
    kind = type(m).__name__
    box = _box(m)
    if box is None or box["right"] - box["left"] <= 0:
        return
    if "Text" in kind:
        label = repr(getattr(m, "text", "")[:32] or kind)
        records.append(dict(box, name=label, play=play, card=card))
    children = list(getattr(m, "submobjects", []))
    inner_card = card
    if children and type(children[0]).__name__ in (
            "Rectangle", "RoundedRectangle", "Square"):
        first_box = _box(children[0])
        if first_box:
            inner_card = first_box
    for child in children:
        _walk(child, inner_card, play, records)


def main():
    scene_name = sys.argv[1]
    from manim import config
    config.dry_run = True
    config.disable_caching = True
    config.verbosity = "ERROR"
    import runpy
    module = runpy.run_path("scene.py")
    base = module[scene_name]
    records = []
    state = {"play": 0}

    class Probe(base):
        def play(self, *args, **kwargs):
            base.play(self, *args, **kwargs)
            state["play"] += 1
            for top in list(self.mobjects):
                _walk(top, None, state["play"], records)

    Probe().render()
    print(json.dumps({"findings": check_records(records)}))


if __name__ == "__main__":
    main()
'''


def probe_available() -> bool:
    return importlib.util.find_spec("manim") is not None


def probe_scene(source: str, work_dir: Path, scene_class: str, *,
                python_command: str | None = None) -> list[str]:
    """Geometry findings for one scene; empty when the probe cannot run."""
    if python_command is None:
        if not probe_available():
            return []
        python_command = sys.executable
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "scene.py").write_text(source, encoding="utf-8")
    (work_dir / "probe.py").write_text(PROBE_SOURCE, encoding="utf-8")
    try:
        completed = subprocess.run(
            [python_command, "probe.py", scene_class], cwd=work_dir,
            capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0:
        return []
    for line in reversed(completed.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                return []
            findings = data.get("findings")
            if isinstance(findings, list):
                return [str(item) for item in findings][:10]
            return []
    return []
