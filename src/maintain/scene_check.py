"""Checks for Copilot-written Manim scenes, before anything runs.

The Explain reply is a program, not data (PRD 13.1). These checks
refuse the obvious dangers before the render: forbidden modules,
process and file calls, and paths outside the work folder. They are
static checks, not a sandbox.
"""

from __future__ import annotations

import ast
import re

from .errors import ProviderError

FORBIDDEN_MODULES = {
    "os", "sys", "subprocess", "socket", "shutil", "urllib", "http",
    "requests", "ftplib", "smtplib", "ctypes", "importlib", "pickle",
    "pathlib", "webbrowser", "multiprocessing", "signal", "tempfile",
}
FORBIDDEN_CALLS = {"open", "eval", "exec", "__import__", "compile", "input",
                   "breakpoint", "globals", "vars", "getattr", "setattr",
                   "delattr"}

_FENCE = re.compile(r"```(?:python|py)?[ \t]*\r?\n(.*?)```", re.DOTALL)


def extract_fenced_python(text: str) -> str:
    """Return the one fenced code block, or refuse with the reason."""
    blocks = [block for block in _FENCE.findall(text) if block.strip()]
    if not blocks:
        raise ProviderError(
            "The reply has no fenced code block. The scene must arrive as "
            "one fenced Python block.")
    if len(blocks) > 1:
        raise ProviderError(
            f"The reply has {len(blocks)} code blocks. The scene must arrive "
            "as one fenced Python block.")
    return blocks[0].strip() + "\n"


def _path_like(value: str) -> bool:
    if value.startswith(("/", "~", "\\\\")):
        return ("/" in value[1:] or "\\" in value[1:]) and len(value) > 2
    return bool(re.match(r"^[A-Za-z]:[\\/]", value))


def scene_faults(source: str) -> list[str]:
    """All faults in one pass, each with its line."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"line {exc.lineno}: the file is not valid Python "
                f"({exc.msg})"]
    faults: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in FORBIDDEN_MODULES:
                    faults.append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in FORBIDDEN_MODULES:
                faults.append(
                    f"line {node.lineno}: from {node.module} import …")
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "id", "")
            if name in FORBIDDEN_CALLS:
                faults.append(f"line {node.lineno}: call to {name}()")
            attribute = getattr(node.func, "attr", "")
            if attribute in {"system", "popen", "spawn", "fork"}:
                faults.append(f"line {node.lineno}: call to .{attribute}()")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _path_like(node.value):
                faults.append(
                    f"line {node.lineno}: path constant {node.value!r}")
    return faults


def scene_class_name(source: str) -> str:
    """The one Scene subclass, or refuse with the reason."""
    tree = ast.parse(source)
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                base_name = getattr(base, "id", getattr(base, "attr", ""))
                if base_name.endswith("Scene"):
                    names.append(node.name)
                    break
    if not names:
        raise ProviderError("The scene file has no Scene class.")
    if len(names) > 1:
        raise ProviderError(
            f"The scene file has {len(names)} Scene classes. One is required.")
    return names[0]


def checked_scene(text: str) -> tuple[str, str]:
    """Extract, check, and name the scene. Returns (source, class name)."""
    source = extract_fenced_python(text)
    faults = scene_faults(source)
    if faults:
        listed = "; ".join(faults[:4])
        raise ProviderError(f"The scene file is refused: {listed}")
    return source, scene_class_name(source)
