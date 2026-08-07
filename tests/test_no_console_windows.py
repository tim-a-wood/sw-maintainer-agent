"""Every child process starts without a console window (NFR).

A windowed app on Windows opens a command window for each child
process unless the creation flags forbid it. Console flashes came
back twice from the field, so the rule is now enforced on the
source: each subprocess call passes ``**hidden()``, or it appears
below with the reason it names its own flags.
"""

from __future__ import annotations

import ast
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "src" / "maintain"

LAUNCHERS = {"run", "Popen", "call", "check_call", "check_output"}

# (file, the function that holds the call): why it sets its own flags.
ALLOWED = {
    ("runner.py", "run"):
        "Adds the no-window flag to its process group flag, so one "
        "stop signal reaches the whole check tree.",
    ("runner.py", "_taskkill_tree"):
        "Passes the no-window flag to taskkill directly.",
    ("ui/app.py", "_apply_update"):
        "The updater shows its progress on purpose: the app is "
        "closing, and a silent reinstall would look like nothing "
        "happened.",
    ("updater.py", "process_alive"):
        "The updater must not import from the package it replaces, so "
        "it names the no-window flag itself, through no_window().",
}


def _calls_with_owners(tree: ast.AST) -> list[tuple[ast.Call, str]]:
    """Every subprocess launch with the function that holds it."""
    found: list[tuple[ast.Call, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(node):
            if (isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and child.func.attr in LAUNCHERS
                    and getattr(child.func.value, "id", "") == "subprocess"):
                found.append((child, node.name))
    return found


def test_every_subprocess_call_hides_its_console():
    offenders: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        relative = path.relative_to(SOURCE_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call, owner in _calls_with_owners(tree):
            if any(isinstance(keyword.value, ast.Call)
                   and getattr(keyword.value.func, "id", "") == "hidden"
                   for keyword in call.keywords if keyword.arg is None):
                continue
            if (relative, owner) in ALLOWED:
                continue
            offenders.append(f"{relative}:{call.lineno} in {owner}()")
    assert not offenders, (
        "These subprocess calls would flash a console window on "
        "Windows. Pass **hidden(), or add the call's function to "
        f"ALLOWED in this test with its reason: {offenders}")


def test_allowances_stay_honest():
    """An allowance must point at code that still exists and still
    names its own flags. A stale entry would hide a real fault."""
    for (relative, owner), reason in ALLOWED.items():
        assert reason.strip(), (relative, owner)
        path = SOURCE_ROOT / relative
        assert path.is_file(), relative
        tree = ast.parse(path.read_text(encoding="utf-8"))
        owners = {name for _, name in _calls_with_owners(tree)}
        assert owner in owners, f"{relative} has no subprocess call in {owner}()"
        source = path.read_text(encoding="utf-8")
        assert "creationflags" in source, relative
