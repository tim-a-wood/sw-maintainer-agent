"""The pre-render check from PRD 13.3: refuse unsafe scene files."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

FORBIDDEN_MODULES = {
    "os", "sys", "subprocess", "socket", "shutil", "urllib", "http",
    "requests", "ftplib", "smtplib", "ctypes", "importlib", "pickle",
}
FORBIDDEN_CALLS = {"open", "eval", "exec", "__import__", "compile", "input"}


def check(path: Path) -> list[str]:
    faults: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in FORBIDDEN_MODULES:
                    faults.append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in FORBIDDEN_MODULES:
                faults.append(f"line {node.lineno}: from {node.module} import …")
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "id", "")
            if name in FORBIDDEN_CALLS:
                faults.append(f"line {node.lineno}: call to {name}()")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.startswith(("/", "~", "C:\\")):
                if not node.value.startswith(("/dev/null",)) and len(node.value) > 2:
                    if "/" in node.value[1:] or "\\" in node.value:
                        faults.append(
                            f"line {node.lineno}: path constant {node.value!r}")
    return faults


if __name__ == "__main__":
    target = Path(sys.argv[1])
    problems = check(target)
    if problems:
        print("REFUSED:")
        for problem in problems:
            print(" -", problem)
        raise SystemExit(1)
    print("PASS: the scene file has no forbidden imports, calls, or paths.")
