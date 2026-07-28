"""Working out how to test a project, and setting it up when it cannot.

Local verification is the one hard gate in the workflow, so a project is not
really set up until Maintain knows how to run its tests. This detects the
usual conventions, scaffolds a minimal harness when a project has none, and
checks that whatever command results actually runs.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

PYTEST_SMOKE = '''"""Smoke tests.

Maintain runs this suite after every applied patch. Replace this file, or
add beside it, as the project grows: a change that breaks these tests will
be sent back for correction automatically.
"""


def test_project_layout():
    """The repository has the files the project expects."""
    from pathlib import Path

    assert Path("README.md").is_file()
'''

UNITTEST_SMOKE = '''"""Smoke tests.

Maintain runs this suite after every applied patch. Replace this file, or
add beside it, as the project grows: a change that breaks these tests will
be sent back for correction automatically.
"""

import unittest
from pathlib import Path


class ProjectLayoutTest(unittest.TestCase):
    def test_readme_is_present(self):
        self.assertTrue(Path("README.md").is_file())


if __name__ == "__main__":
    unittest.main()
'''

C_MAKEFILE = """\
CC ?= cc
CFLAGS ?= -Wall -Wextra -std=c11

SOURCES := $(filter-out $(wildcard tests/*.c), $(wildcard *.c src/*.c))

.PHONY: test build clean

build:
\t@mkdir -p build
\t@if [ -n "$(SOURCES)" ]; then $(CC) $(CFLAGS) -o build/app $(SOURCES); fi

test: build
\t@sh tests/run-tests.sh

clean:
\t@rm -rf build
"""

C_TEST_RUNNER = """\
#!/bin/sh
# Maintain runs this after every applied patch. Add checks as the project
# grows: a failure here sends the change back for correction.
set -eu

if [ ! -x build/app ]; then
    echo "build/app was not produced by the build" >&2
    exit 1
fi

output=$(./build/app)
echo "program output: $output"

if [ -z "$output" ]; then
    echo "the program printed nothing" >&2
    exit 1
fi

echo "ok"
"""

NODE_SMOKE = """\
// Maintain runs this after every applied patch. Add checks as the project
// grows: a failure here sends the change back for correction.
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");

test("the project has a README", () => {
  assert.ok(fs.existsSync("README.md"));
});
"""


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def has_files(root: Path, pattern: str, limit: int = 1) -> bool:
    found = 0
    for path in root.rglob(pattern):
        if any(part in {".git", ".maintain", "node_modules", "venv", ".venv", "build"}
               for part in path.parts):
            continue
        found += 1
        if found >= limit:
            return True
    return False


def detect_test_command(root: Path) -> Optional[dict]:
    """The project's own way of running tests, if it has one."""
    package_json = root / "package.json"
    if package_json.is_file():
        try:
            data = json.loads(read(package_json))
            script = (data.get("scripts") or {}).get("test", "")
        except (json.JSONDecodeError, AttributeError):
            script = ""
        if script and "no test specified" not in script:
            return {"command": "npm test", "reason": "package.json defines a test script"}

    if (root / "Cargo.toml").is_file():
        return {"command": "cargo test", "reason": "Cargo.toml is present"}
    if (root / "go.mod").is_file():
        return {"command": "go test ./...", "reason": "go.mod is present"}
    if (root / "pom.xml").is_file():
        return {"command": "mvn -q test", "reason": "pom.xml is present"}
    for gradle in ("build.gradle", "build.gradle.kts"):
        if (root / gradle).is_file():
            return {"command": "gradle test", "reason": f"{gradle} is present"}

    makefile = next((root / name for name in ("Makefile", "makefile")
                     if (root / name).is_file()), None)
    if makefile is not None and re.search(r"^test\s*:", read(makefile), re.MULTILINE):
        return {"command": "make test", "reason": "the Makefile has a test target"}

    python_markers = (
        (root / "pytest.ini").is_file()
        or (root / "tox.ini").is_file()
        or "[tool.pytest" in read(root / "pyproject.toml")
        or has_files(root, "test_*.py")
        or has_files(root, "*_test.py")
    )
    if python_markers:
        command = "python3 -m pytest -q" if pytest_available() else "python3 -m unittest discover -q"
        return {"command": command, "reason": "the project has Python tests"}

    if (root / "CMakeLists.txt").is_file():
        return {"command": "ctest --test-dir build --output-on-failure",
                "reason": "CMakeLists.txt is present"}
    return None


def pytest_available() -> bool:
    for executable in ("python3", "python"):
        if not shutil.which(executable):
            continue
        proc = subprocess.run(
            [executable, "-m", "pytest", "--version"], capture_output=True,
            stdin=subprocess.DEVNULL, text=True,
        )
        if proc.returncode == 0:
            return True
    return False


def guess_language(root: Path) -> str:
    """What the project mostly looks like, for choosing a harness."""
    if (root / "package.json").is_file() or has_files(root, "*.js") or has_files(root, "*.ts"):
        return "node"
    if has_files(root, "*.py"):
        return "python"
    if has_files(root, "*.c") or has_files(root, "*.h"):
        return "c"
    if has_files(root, "*.cpp") or has_files(root, "*.cc"):
        return "c"
    return "unknown"


def scaffold_tests(root: Path, language: str) -> dict:
    """Create a minimal but real test setup. Returns the command to use."""
    created = []

    if language == "python":
        tests = root / "tests"
        tests.mkdir(exist_ok=True)
        use_pytest = pytest_available()
        target = tests / "test_smoke.py"
        if not target.exists():
            target.write_text(PYTEST_SMOKE if use_pytest else UNITTEST_SMOKE,
                              encoding="utf-8")
            created.append(target)
        command = ("python3 -m pytest -q" if use_pytest
                   else "python3 -m unittest discover -s tests -q")
        return {"command": command, "created": created}

    if language == "node":
        tests = root / "tests"
        tests.mkdir(exist_ok=True)
        target = tests / "smoke.test.js"
        if not target.exists():
            target.write_text(NODE_SMOKE, encoding="utf-8")
            created.append(target)
        return {"command": "node --test tests/", "created": created}

    if language == "c":
        makefile = root / "Makefile"
        if not makefile.exists():
            makefile.write_text(C_MAKEFILE, encoding="utf-8")
            created.append(makefile)
        tests = root / "tests"
        tests.mkdir(exist_ok=True)
        runner = tests / "run-tests.sh"
        if not runner.exists():
            runner.write_text(C_TEST_RUNNER, encoding="utf-8")
            runner.chmod(0o755)
            created.append(runner)
        return {"command": "make test", "created": created}

    raise ValueError(language)


def verify_command(root: Path, command: str, timeout: int = 120) -> dict:
    """Run the command once so a broken one is caught during setup.

    stdin is closed: setup is a sequence of prompts, and a command that
    reads stdin would consume the answers meant for them.
    """
    try:
        proc = subprocess.run(
            command, shell=True, cwd=str(root), capture_output=True,
            stdin=subprocess.DEVNULL, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": f"timed out after {timeout} seconds"}
    except OSError as exc:
        return {"ok": False, "output": str(exc)}
    output = (proc.stdout or "") + (proc.stderr or "")
    return {"ok": proc.returncode == 0, "output": output.strip(),
            "code": proc.returncode}
