"""The local scene checks: manifest, copy, pace, geometry, and sheet."""

from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from maintain.config import ProjectConfig, default_config
from maintain.issue_packets import (SideExchange, build_side_packet,
                                    explain_attachments, explain_dir,
                                    explain_request)
from maintain.render import contact_sheet
from maintain.scene_probe import PROBE_SOURCE, probe_scene
from maintain.scene_quality import (copy_faults, pace_faults, quality_findings,
                                    scene_beats, scene_texts)

MANIM_PYTHON = Path("/opt/manim-venv/bin/python")

GOOD_SOURCE = (
    "from manim import Scene, Text, FadeIn\n"
    "\n"
    'BEATS = [\n'
    '    ("The code accepts this record.", 4.0),\n'
    '    ("Only correct records go to the plan.", 26.0),\n'
    "]\n"
    "\n"
    "\n"
    "class DemoScene(Scene):\n"
    "    def construct(self):\n"
    '        self.play(FadeIn(Text("The code accepts this record.")))\n'
    "        self.wait(3.0)\n"
)


def _git(repository: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repository), *args], check=True,
                   capture_output=True)


def _config(tmp_path: Path) -> ProjectConfig:
    repository = tmp_path / "project"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    (repository / "app.py").write_text('VALUE = "before"\n', encoding="utf-8")
    _git(repository, "add", "app.py")
    _git(repository, "config", "user.name", "T")
    _git(repository, "config", "user.email", "t@example.invalid")
    _git(repository, "commit", "-m", "initial")
    data = default_config(repository, "manual-ui")
    data["audit"] = {"runtime_root": str(tmp_path / "runtime")}
    path = repository / ".maintain.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return ProjectConfig.load(path)


# ----- manifest and texts -----

def test_scene_beats_reads_the_literal_list():
    assert scene_beats(GOOD_SOURCE) == [
        ("The code accepts this record.", 4.0),
        ("Only correct records go to the plan.", 26.0)]
    assert scene_beats("x = 1\n") is None
    assert scene_beats("BEATS = compute()\n") is None
    assert scene_beats('BEATS = [("a", "b")]\n') is None


def test_scene_texts_are_ordered_and_unique():
    assert scene_texts(GOOD_SOURCE) == ["The code accepts this record."]


# ----- copy -----

def test_copy_faults_flag_the_ste_breaks():
    long_sentence = ("The code takes the record and then converts the speed "
                     "and then checks the bounds and then returns the "
                     "prepared record to the caller for the plan.")
    assert any("words" in fault for fault in copy_faults([long_sentence]))
    assert any("passive" in fault
               for fault in copy_faults(["The record is rejected."]))
    assert any("utilize" in fault
               for fault in copy_faults(["Utilize the checks."]))
    assert copy_faults(['output: "speed out of range: 200.0 kt"']) == []
    assert copy_faults(["The code rejects the record."]) == []


# ----- pace -----

def test_pace_faults_flag_short_fast_and_total():
    assert any("minimum" in fault
               for fault in pace_faults([("A short text.", 1.0),
                                         ("More text here.", 30.0)]))
    fast = "This text has far too many characters for two seconds on screen."
    assert any("characters each second" in fault
               for fault in pace_faults([(fast, 2.0), ("", 30.0)]))
    assert any("add up" in fault for fault in pace_faults([("Short.", 4.0)]))
    assert pace_faults([("The story.", 4.0), ("", 28.0)]) == []


def test_quality_findings_demand_the_manifest():
    findings = quality_findings("from manim import Scene\n"
                                "class S(Scene):\n    pass\n")
    assert any("BEATS" in finding for finding in findings)
    assert quality_findings(GOOD_SOURCE) == []


# ----- geometry: the pure check and the live probe -----

def _probe_namespace() -> dict:
    namespace: dict = {"__name__": "probe_module"}
    exec(PROBE_SOURCE, namespace)
    return namespace


def test_probe_check_records_finds_frame_and_card_faults():
    check = _probe_namespace()["check_records"]
    inside = {"name": "'ok'", "play": 1, "left": -1.0, "right": 1.0,
              "top": 0.5, "bottom": -0.5, "card": None}
    off_frame = dict(inside, name="'edge'", right=7.3)
    card = {"left": -1.0, "right": 1.0, "top": 0.4, "bottom": -0.4}
    wide = {"name": "'wide'", "play": 2, "left": -2.0, "right": 2.0,
            "top": 0.3, "bottom": -0.3, "card": card}
    faults = check([inside, off_frame, wide, wide])
    assert len(faults) == 2
    assert any("leaves the frame" in fault for fault in faults)
    assert any("wider than its card" in fault for fault in faults)


@pytest.mark.skipif(not MANIM_PYTHON.is_file(),
                    reason="no Manim environment on this machine")
def test_probe_scene_reports_the_overflowing_card(tmp_path):
    overflow = (
        "from manim import Scene, Text, RoundedRectangle, VGroup, FadeIn\n"
        "\n"
        'BEATS = [("A card.", 3.0)]\n'
        "\n"
        "\n"
        "class OverflowScene(Scene):\n"
        "    def construct(self):\n"
        "        box = RoundedRectangle(width=2.0, height=0.6)\n"
        '        body = Text("a very long line that cannot fit the card")\n'
        "        self.play(FadeIn(VGroup(box, body)))\n"
    )
    findings = probe_scene(overflow, tmp_path / "probe", "OverflowScene",
                           python_command=str(MANIM_PYTHON))
    assert any("wider than its card" in finding for finding in findings)

    clean = probe_scene(GOOD_SOURCE, tmp_path / "probe-clean", "DemoScene",
                        python_command=str(MANIM_PYTHON))
    assert clean == []


def test_probe_scene_is_silent_without_a_probe_python(tmp_path):
    findings = probe_scene(GOOD_SOURCE, tmp_path / "probe", "DemoScene",
                           python_command="no-such-python")
    assert findings == []


# ----- the contact sheet -----

@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")
def test_contact_sheet_makes_one_png(tmp_path):
    video = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi",
         "-i", "testsrc=duration=2:size=64x64:rate=60", str(video)],
        check=True, capture_output=True)
    sheet = contact_sheet(video, tmp_path)
    assert sheet is not None and sheet.is_file()
    assert contact_sheet(tmp_path / "absent.mp4", tmp_path) is None


# ----- the packet carries the guides and the contract -----

def test_explain_packet_ships_the_guides(tmp_path):
    names = [path.name for path in explain_attachments()]
    assert names == ["EXAMPLE-SCENE.md", "PITFALLS.md"]
    config = _config(tmp_path)
    request = explain_request(config, ["app.py"], "goal", "")
    exchange = SideExchange(kind="explain", request=request,
                            directory=explain_dir(config, request.run_id)
                            / "packets")
    packet = build_side_packet(exchange, config, [])
    with zipfile.ZipFile(packet.zip_path) as archive:
        members = archive.namelist()
        task_text = archive.read("TASK.md").decode()
    assert "attachments/PITFALLS.md" in members
    assert "attachments/EXAMPLE-SCENE.md" in members
    assert "BEATS" in task_text
    assert "title band" in task_text


def test_explain_request_carries_the_findings(tmp_path):
    config = _config(tmp_path)
    request = explain_request(
        config, ["app.py"], "goal", "",
        findings=["Beat 2 shows text for 1 s; the minimum is 3 s."])
    assert request.payload["lint_findings"] == [
        "Beat 2 shows text for 1 s; the minimum is 3 s."]
    assert "lint_findings" in request.instructions
