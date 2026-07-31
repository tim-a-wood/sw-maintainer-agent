"""ConfigStore edges the settings journey does not reach."""

from __future__ import annotations

import json

import pytest

from maintain.errors import ConfigurationError

from test_ui_app import _project


def _store(tmp_path):
    from maintain.ui.config_store import ConfigStore
    return ConfigStore(_project(tmp_path))


def test_absolute_global_prompt_path_is_used_as_given(tmp_path, monkeypatch):
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    store = _store(tmp_path)
    target = tmp_path / "elsewhere" / "GLOBAL.md"
    data = store.load_raw()
    data.setdefault("package", {})["global_prompt"] = str(target)
    store.save_raw(data)
    assert store.global_prompt_path() == target
    store.write_global_prompt("# Absolute rules\n")
    assert target.read_text(encoding="utf-8") == "# Absolute rules\n"
    assert store.read_global_prompt() == "# Absolute rules\n"


def test_task_prompt_reports_a_missing_override_file(tmp_path, monkeypatch):
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    store = _store(tmp_path)
    from maintain.ui.config_store import BUILTIN_PROMPTS
    assert store.task_prompt("review") == (False, BUILTIN_PROMPTS["review"])
    store.set_task_prompt("review", "My review rules.")
    assert store.task_prompt("review") == (True, "My review rules.")
    prompt_file = store.path.parent / ".maintain-prompts" / "review.md"
    prompt_file.unlink()
    overridden, content = store.task_prompt("review")
    assert overridden is True and content == ""
    with pytest.raises(ConfigurationError):
        store.set_task_prompt("nonsense", "x")


def test_set_checks_preserves_reproduce_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("MAINTAIN_SETTINGS_PATH", str(tmp_path / "settings.json"))
    store = _store(tmp_path)
    data = store.load_raw()
    data.setdefault("verification", {}).setdefault("commands", {})["repro"] = {
        "argv": ["python", "-c", "pass"], "phase": "reproduce"}
    store.save_raw(data)

    store.set_checks([("unit", "python -c pass")])
    saved = json.loads(store.path.read_text(encoding="utf-8"))
    commands = saved["verification"]["commands"]
    assert commands["repro"]["phase"] == "reproduce"
    assert commands["unit"]["phase"] == "verify"
    # The verify-phase list itself was replaced, not merged.
    assert ("unit", "python -c pass") in store.checks()

    with pytest.raises(ConfigurationError):
        store.set_checks([("", "python -c pass")])
    with pytest.raises(ConfigurationError):
        store.set_checks([("named", "   ")])
