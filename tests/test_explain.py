"""M5: the Explain loop — scene checks, packets, and the render runner."""

from __future__ import annotations

import json
import stat
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from maintain.config import ProjectConfig, default_config
from maintain.engine import PROVIDER_SAFETY_HEADER
from maintain.errors import ProviderError
from maintain.issue_packets import (SideExchange, build_side_packet,
                                    explain_dir, explain_request)
from maintain.render import render_scene
from maintain.scene_check import (checked_scene, extract_fenced_python,
                                  scene_class_name, scene_faults)

GOOD_SCENE = (
    "from manim import Scene, Text, FadeIn\n"
    "\n"
    "\n"
    "class DemoScene(Scene):\n"
    "    def construct(self):\n"
    '        self.play(FadeIn(Text("src/loader.py")))\n'
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


# ----- extraction and checks -----

def test_extract_fenced_python_needs_exactly_one_block():
    text = f"Here is the scene.\n\n```python\n{GOOD_SCENE}```\nDone."
    assert extract_fenced_python(text).startswith("from manim")
    with pytest.raises(ProviderError):
        extract_fenced_python("no code here")
    with pytest.raises(ProviderError):
        extract_fenced_python("```python\na = 1\n```\n```python\nb = 2\n```")


def test_scene_faults_refuse_the_dangerous_patterns():
    assert scene_faults(GOOD_SCENE) == []
    cases = {
        "import os\n": "import os",
        "from subprocess import run\n": "from subprocess",
        "x = open('f')\n": "open()",
        "import numpy\nnumpy.os.system('x')\n": ".system()",
        "p = '/etc/passwd'\n": "path constant",
        "p = 'C:\\\\Users\\\\x'\n": "path constant",
        "def f(:\n": "not valid Python",
    }
    for source, expected in cases.items():
        faults = scene_faults(source)
        assert faults, source
        assert any(expected in fault for fault in faults), (source, faults)


def test_scene_class_name_needs_exactly_one_scene():
    assert scene_class_name(GOOD_SCENE) == "DemoScene"
    with pytest.raises(ProviderError):
        scene_class_name("x = 1\n")
    two = GOOD_SCENE + "\n\nclass OtherScene(Scene):\n    pass\n"
    with pytest.raises(ProviderError):
        scene_class_name(two)


def test_checked_scene_returns_source_and_class():
    source, name = checked_scene(f"```python\n{GOOD_SCENE}```")
    assert name == "DemoScene" and "construct" in source
    with pytest.raises(ProviderError):
        checked_scene("```python\nimport os\n```")


# ----- request and packet -----

def test_explain_request_carries_files_and_safety_header(tmp_path):
    config = _config(tmp_path)
    request = explain_request(config, ["app.py"], "Explain the value.",
                              "a new developer")
    assert request.role == "explain" and request.task_id == "explain"
    assert request.instructions.startswith(PROVIDER_SAFETY_HEADER)
    assert request.payload["candidate_files"][0]["path"] == "app.py"
    assert request.payload["audience"] == "a new developer"
    with pytest.raises(ProviderError):
        explain_request(config, ["missing.py"], "goal", "")


def test_explain_repair_request_carries_the_error(tmp_path):
    config = _config(tmp_path)
    request = explain_request(config, ["app.py"], "goal", "",
                              previous_scene=GOOD_SCENE,
                              render_error="Boom on line 3")
    assert request.payload["previous_scene"] == GOOD_SCENE
    assert request.payload["render_error"] == "Boom on line 3"
    assert "corrected complete scene" in request.instructions


def test_every_task_type_has_a_builtin_prompt():
    from maintain.config import PACKET_TASK_KEYS
    from maintain.ui.config_store import BUILTIN_PROMPTS
    assert set(BUILTIN_PROMPTS) == set(PACKET_TASK_KEYS)


def test_explain_prompt_override_lands_in_the_packet(tmp_path):
    from maintain.ui.config_store import ConfigStore
    config = _config(tmp_path)
    ConfigStore(config).set_task_prompt(
        "explain", "Explain only the gust rules, for the safety board.")
    config = ProjectConfig.load(config.path)
    request = explain_request(config, ["app.py"], "goal", "")
    exchange = SideExchange(kind="explain", request=request,
                            directory=explain_dir(config, request.run_id)
                            / "packets")
    packet = build_side_packet(exchange, config, [])
    with zipfile.ZipFile(packet.zip_path) as archive:
        task_text = archive.read("TASK.md").decode()
    assert "Explain only the gust rules, for the safety board." in task_text
    assert PROVIDER_SAFETY_HEADER in task_text
    assert "Animate relationships" not in task_text


def test_explain_packet_contract_and_members(tmp_path):
    config = _config(tmp_path)
    request = explain_request(config, ["app.py"], "Explain the value.", "")
    exchange = SideExchange(kind="explain", request=request,
                            directory=explain_dir(config, request.run_id)
                            / "packets")
    packet = build_side_packet(exchange, config, [])
    assert packet.task_key == "explain"
    with zipfile.ZipFile(packet.zip_path) as archive:
        task_text = archive.read("TASK.md").decode()
        assert "one fenced code block" in task_text
        assert "exactly one `Scene` subclass" in task_text
        assert "class ModuleExplainScene(Scene):" in task_text
        assert "GLOBAL.md" in archive.namelist()[1]


# ----- the render runner (stub command, no Manim needed) -----

def _stub(tmp_path: Path, body: str) -> str:
    script = tmp_path / "fakemanim"
    script.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def test_render_scene_reports_missing_command(tmp_path):
    result = render_scene(GOOD_SCENE, tmp_path / "work",
                          manim_command="no-such-manim",
                          scene_class="DemoScene")
    assert not result.ok and "Manim is absent" in result.message


def test_render_scene_success_finds_the_video(tmp_path):
    stub = _stub(tmp_path,
                 'mkdir -p media/videos/scene/1080p60\n'
                 'echo video > "media/videos/scene/1080p60/$3.mp4"\n')
    result = render_scene(GOOD_SCENE, tmp_path / "work", manim_command=stub,
                          scene_class="DemoScene")
    assert result.ok, result.message
    assert result.video is not None and result.video.name == "DemoScene.mp4"
    assert (tmp_path / "work" / "scene.py").read_text(
        encoding="utf-8") == GOOD_SCENE


def test_render_scene_failure_returns_the_tail(tmp_path):
    stub = _stub(tmp_path, 'echo "Boom on line 3" 1>&2\nexit 1\n')
    result = render_scene(GOOD_SCENE, tmp_path / "work", manim_command=stub,
                          scene_class="DemoScene")
    assert not result.ok and result.message == "The render failed."
    assert "Boom on line 3" in result.output_tail


# ----- the reply check path -----

def test_check_reply_scene_kind_validates_before_anything_moves():
    from maintain.ui.bridge import check_reply
    handoff = SimpleNamespace(reply_kind="scene", request=None)
    good = check_reply(handoff, text=f"Sure.\n```python\n{GOOD_SCENE}```")
    assert good.valid and good.reply.kind == "scene"
    assert good.reply.text.startswith("from manim")

    refused = check_reply(handoff, text="```python\nimport os\n```")
    assert not refused.valid and "refused" in refused.message

    empty = check_reply(handoff, text="   ")
    assert not empty.valid
