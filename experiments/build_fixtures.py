#!/usr/bin/env python3
"""Build fixture repositories and handoff packages for the prompt experiments.

Creates, under a work directory, one small Git repository per scenario, drives
the real `maintain` CLI far enough to generate the package under test, applies
any variant surgery, and writes a manifest that grade.py consumes.

Usage:
    python3 experiments/build_fixtures.py <work-dir>
"""

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAINTAIN_PY = REPO_ROOT / "maintain" / "maintain.py"


def sh(cmd, cwd, env=None, input_text=""):
    full_env = dict(os.environ)
    full_env.update(env or {})
    proc = subprocess.run(
        cmd, cwd=str(cwd), env=full_env, input=input_text,
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"command failed ({proc.returncode}): {cmd}\n{proc.stdout}\n{proc.stderr}"
        )
    return proc


def git(cwd, *args):
    sh(["git", *args], cwd)


def maintain(repo, *args, clip=None, yes=False):
    env = {}
    if clip is not None:
        env["MAINTAIN_CLIPBOARD_CMD"] = f"cat {clip}"
    sh([sys.executable, str(MAINTAIN_PY), *args], repo, env=env,
       input_text="y\n" if yes else "")


def make_repo(work, name, files):
    repo = work / "repos" / name
    repo.mkdir(parents=True)
    (repo / ".gitignore").write_text(".maintain/\n__pycache__/\n", encoding="utf-8")
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "eval@example.com")
    git(repo, "config", "user.name", "Eval")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    return repo


def init_maintain(repo, test_command=None, context="Small Python 3.9+ project, standard library only. Tests use pytest."):
    maintain(repo, "init")
    config = repo / ".maintain" / "config.json"
    data = json.loads(config.read_text(encoding="utf-8"))
    data["test_command"] = test_command
    config.write_text(json.dumps(data), encoding="utf-8")
    (repo / ".maintain" / "project-context.md").write_text(
        f"# Project Context\n\n{context}\n", encoding="utf-8"
    )


def write_clip(work, name, text):
    path = work / "clips" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def task_file(repo, relative):
    matches = sorted((repo / ".maintain" / "tasks").glob(f"*/{relative}"))
    assert matches, f"no {relative} under {repo}"
    return matches[-1]


def save_package(work, cell, source):
    dest = work / "packages" / f"{cell}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return dest


def surgery(path, old, new):
    text = path.read_text(encoding="utf-8")
    assert old in text, f"surgery anchor missing in {path}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# Authored chatbot responses used to drive fixtures to the stage under test.

SCOPE_GREET = """STATUS: SCOPE_COMPLETE

## Understanding

app.py's greeting contains a typo ("Helo" instead of "Hello"), and a new helpers.py module must provide shout(text) returning the upper-cased text followed by "!". app.py should print shout(greeting()).

## Allowed Files

- app.py
- helpers.py

## Proposed Changes

app.py: correct the greeting string and print shout(greeting()) using the new module. helpers.py: new module with shout(text).

## Acceptance Criteria

- greeting() returns "Hello".
- helpers.shout("Hello") returns "HELLO!".
- Running app.py prints "HELLO!".

## Risks and Unknowns

- None.
"""

SCOPE_ROVER = """STATUS: SCOPE_COMPLETE

## Understanding

steps_remaining under-reports by one because of an off-by-one; additionally the STEP constant must be renamed STEP_SIZE everywhere it appears, and move()'s docstring must state that it returns the new position.

## Allowed Files

- rover.py

## Proposed Changes

rover.py: correct steps_remaining to TOTAL_BUDGET - used; rename STEP to STEP_SIZE at its definition and every use; expand move()'s docstring.

## Acceptance Criteria

- steps_remaining(3) returns 7.
- The constant is named STEP_SIZE and no reference to the old name STEP remains.
- move()'s docstring states that it returns the new position.
- python3 -m pytest -q passes.

## Risks and Unknowns

- None.
"""

SCOPE_LEGACY = """STATUS: SCOPE_COMPLETE

## Understanding

legacy_utils.py duplicates utils.py and is deprecated; it must be deleted and app.py must import shout from utils instead.

## Allowed Files

- app.py
- legacy_utils.py

## Proposed Changes

app.py: import shout from utils. legacy_utils.py: delete the file entirely.

## Acceptance Criteria

- legacy_utils.py no longer exists in the repository.
- app.py imports shout from utils and greet_loud() still returns "HELLO!".
- python3 -m pytest -q passes.

## Risks and Unknowns

- None.
"""

SCOPE_PRICING = """STATUS: SCOPE_COMPLETE

## Understanding

apply_discount currently returns the total unchanged. Orders of 100.00 or more must receive a 10% discount, rounded to 2 decimal places; smaller orders stay unchanged.

## Allowed Files

- pricing.py

## Proposed Changes

pricing.py: return the discounted total, rounded to 2 decimals, when the order qualifies; otherwise return the total unchanged.

## Acceptance Criteria

- apply_discount(50) == 50 (below the threshold, unchanged).
- apply_discount(200) == 180.0.
- apply_discount(100) == 90.0 (the boundary is inclusive).
- apply_discount(149.99) == 134.99 (the discounted amount is rounded to 2 decimals).
- python3 -m pytest -q passes.

## Risks and Unknowns

- The committed tests do not exercise every criterion; the criteria are authoritative.
"""

SCOPE_GREETFIX = """STATUS: SCOPE_COMPLETE

## Understanding

The greeting must read exactly "Hello, world!" but currently reads "Helo, world".

## Allowed Files

- app.py

## Proposed Changes

app.py: correct the string returned by greeting().

## Acceptance Criteria

- greeting() returns "Hello, world!".
- python3 -m pytest -q passes.

## Risks and Unknowns

- None.
"""

SCOPE_TAXAPP = """STATUS: SCOPE_COMPLETE

## Understanding

checkout.total_with_tax must return the total plus 8% sales tax, with the rate defined as TAX_RATE = 0.08 in a separate rates.py module.

## Allowed Files

- checkout.py

## Proposed Changes

checkout.py: implement total_with_tax(total) as round(total * (1 + rates.TAX_RATE), 2).

## Acceptance Criteria

- rates.py defines TAX_RATE = 0.08.
- total_with_tax(100) == 108.0.
- python3 -m pytest -q passes.

## Risks and Unknowns

- None.
"""


def impl_response(patch, summary):
    return (
        "STATUS: IMPLEMENTATION_COMPLETE\n\n## Summary\n\n" + summary
        + "\n\n## Patch\n\n```diff\n" + patch.rstrip("\n") + "\n```\n"
    )


PRICING_PATCHES = {
    "boundary": (
        "diff --git a/pricing.py b/pricing.py\n"
        "--- a/pricing.py\n+++ b/pricing.py\n@@ -1,2 +1,4 @@\n"
        " def apply_discount(total):\n"
        "+    if total > 100:\n"
        "+        return round(total * 0.9, 2)\n"
        "     return total\n"
    ),
    "clean": (
        "diff --git a/pricing.py b/pricing.py\n"
        "--- a/pricing.py\n+++ b/pricing.py\n@@ -1,2 +1,4 @@\n"
        " def apply_discount(total):\n"
        "+    if total >= 100:\n"
        "+        return round(total * 0.9, 2)\n"
        "     return total\n"
    ),
    "rounding": (
        "diff --git a/pricing.py b/pricing.py\n"
        "--- a/pricing.py\n+++ b/pricing.py\n@@ -1,2 +1,4 @@\n"
        " def apply_discount(total):\n"
        "+    if total >= 100:\n"
        "+        return round(total) * 0.9\n"
        "     return total\n"
    ),
}

GREETFIX_WRONG_PATCH = (
    "diff --git a/app.py b/app.py\n"
    "--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,2 @@\n"
    " def greeting():\n"
    '-    return "Helo, world"\n'
    '+    return "Hello, world"\n'
)

TAXAPP_RESCOPE_REQUIRED = """STATUS: RESCOPE_REQUIRED

## Rescope Reason

The acceptance criteria require rates.py to define TAX_RATE, but rates.py does not exist and is not on the Allowed Files list, so I cannot create it. The scope must be revised to allow creating rates.py (alongside checkout.py) before the task can be implemented.
"""

WRITE_PERMISSION_RULE = (
    "\n- The Allowed Files list is a write permission, not a reading list: do NOT\n"
    "  list files that are only needed as context and will not change."
)

EVIDENCE_RULE_OLD = (
    "One bullet per acceptance criterion stating whether it is met and how you\n"
    "verified it from the material above."
)
EVIDENCE_RULE_NEW = (
    "One bullet per acceptance criterion stating whether it is met. For each\n"
    "criterion, quote the exact diff or file lines that decide it, and when the\n"
    "criterion states a concrete input and output, compute the actual result of\n"
    "the shown code for that input step by step before deciding."
)


def build(work):
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    manifest = {"cells": {}}

    def cell(name, package, kind, n, **extra):
        manifest["cells"][name] = {
            "package": str(package), "type": kind, "n": n, **extra,
        }

    pytest_cmd = "python3 -m pytest -q"

    # --- S1: named-file scope request (anchor) ---
    webapp = make_repo(work, "webapp", {
        "util.py": 'def decorate(text):\n    return f"*** {text} ***"\n',
        "app.py": (
            "from util import decorate\n\n\n"
            "def greeting():\n    return \"Helo, world\"\n\n\n"
            "if __name__ == \"__main__\":\n    print(decorate(greeting()))\n"
        ),
        "test_app.py": (
            "from app import greeting\n\n\n"
            "def test_greeting():\n    assert greeting() == \"Hello, world\"\n"
        ),
        "README.md": "# webapp\n\nGreets the world.\n",
        "docs/usage.md": "# Usage\n\nRun python3 app.py.\n",
    })
    init_maintain(webapp)
    maintain(webapp, "new", "Fix the greeting typo in app.py so the test suite passes.")
    pkg = save_package(work, "S1-cur", task_file(webapp, "scope/package.md"))
    cell("S1-cur", pkg, "scope", 4,
         required=["app.py"], tolerated=["app.py", "test_app.py"])

    # --- S2: vague crash-report scope request; A current, B +write-permission ---
    cliapp = make_repo(work, "cliapp", {
        "formatter.py": 'def banner(text):\n    return f"== {text} =="\n',
        "cli.py": (
            "import sys\n\nfrom formatter import banner\n\n\n"
            "def main():\n    name = sys.argv[1]\n"
            "    print(banner(f\"Hello, {name}\"))\n    return 0\n\n\n"
            "if __name__ == \"__main__\":\n    sys.exit(main())\n"
        ),
        "test_cli.py": (
            "from formatter import banner\n\n\n"
            "def test_banner():\n    assert banner(\"x\") == \"== x ==\"\n"
        ),
        "README.md": "# cliapp\n\nGreets whoever is named on the command line.\n",
        "docs/notes.md": "# Notes\n\nInternal notes.\n",
    })
    init_maintain(cliapp, test_command=pytest_cmd)
    maintain(cliapp, "new",
             "Running the tool with no arguments crashes with IndexError; it "
             "should print a usage message and exit with code 2 instead. Keep "
             "the test suite passing.")
    pkg_a = save_package(work, "S2-A", task_file(cliapp, "scope/package.md"))
    cell("S2-A", pkg_a, "scope", 6,
         required=["cli.py"], tolerated=["cli.py", "test_cli.py"])
    pkg_b = work / "packages" / "S2-B.md"
    pkg_b.write_text(pkg_a.read_text(encoding="utf-8"), encoding="utf-8")
    surgery(pkg_b, "so include test files when tests need\n  to change.",
            "so include test files when tests need\n  to change." + WRITE_PERMISSION_RULE)
    cell("S2-B", pkg_b, "scope", 6,
         required=["cli.py"], tolerated=["cli.py", "test_cli.py"])

    # --- I1: new-file implementation (anchor) ---
    greet = make_repo(work, "greet", {
        "app.py": (
            "def greeting():\n    return \"Helo\"\n\n\n"
            "if __name__ == \"__main__\":\n    print(greeting())\n"
        ),
    })
    init_maintain(greet)
    maintain(greet, "new",
             "Fix the greeting typo in app.py and add a helpers module: "
             "helpers.py providing shout(text) which returns the upper-cased "
             "text followed by one exclamation mark. app.py should print "
             "shout(greeting()).")
    maintain(greet, "capture", clip=write_clip(work, "greet-scope", SCOPE_GREET))
    maintain(greet, "next")
    pkg = save_package(work, "I1-cur", task_file(greet, "rounds/01/implementation-package.md"))
    cell("I1-cur", pkg, "impl", 5, repo=str(greet),
         allowed=["app.py", "helpers.py"],
         behavior={"cmd": "python3 app.py", "expect_stdout": "HELLO!"})

    # --- I2: multi-hunk modification of one file ---
    rover = make_repo(work, "rover", {
        "rover.py": (
            '"""Rover movement helpers."""\n\n'
            "STEP = 2\n\nTOTAL_BUDGET = 10\n\n\n"
            "def move(position, steps):\n"
            '    """Move the rover."""\n'
            "    return position + steps * STEP\n\n\n"
            "def steps_remaining(used):\n"
            "    return TOTAL_BUDGET - used - 1\n\n\n"
            "def path(position, count):\n"
            "    points = []\n"
            "    for _ in range(count):\n"
            "        position = move(position, 1)\n"
            "        points.append(position)\n"
            "    return points\n"
        ),
        "test_rover.py": (
            "from rover import move, path, steps_remaining\n\n\n"
            "def test_move():\n    assert move(0, 3) == 6\n\n\n"
            "def test_steps_remaining():\n    assert steps_remaining(3) == 7\n\n\n"
            "def test_path():\n    assert path(0, 3) == [2, 4, 6]\n"
        ),
    })
    init_maintain(rover, test_command=pytest_cmd)
    maintain(rover, "new",
             "steps_remaining reports one step too few (with 3 used it must "
             "return 7). Fix it; also rename the module constant STEP to "
             "STEP_SIZE everywhere, and expand move()'s docstring to say it "
             "returns the new position. Keep the tests passing.")
    maintain(rover, "capture", clip=write_clip(work, "rover-scope", SCOPE_ROVER))
    maintain(rover, "next")
    pkg = save_package(work, "I2-cur", task_file(rover, "rounds/01/implementation-package.md"))
    cell("I2-cur", pkg, "impl", 8, repo=str(rover), allowed=["rover.py"],
         behavior={"cmd": pytest_cmd, "expect_rc": 0})

    # --- I3: deletion plus import rewrite ---
    legacy = make_repo(work, "legacy", {
        "utils.py": 'def shout(text):\n    return text.upper() + "!"\n',
        "legacy_utils.py": 'def shout(text):\n    return text.upper() + "!"\n',
        "app.py": (
            "from legacy_utils import shout\n\n\n"
            "def greet_loud():\n    return shout(\"hello\")\n"
        ),
        "test_app.py": (
            "from app import greet_loud\n\n\n"
            "def test_greet_loud():\n    assert greet_loud() == \"HELLO!\"\n"
        ),
    })
    init_maintain(legacy, test_command=pytest_cmd)
    maintain(legacy, "new",
             "Delete the deprecated legacy_utils.py and switch app.py to "
             "import shout from utils. The tests must keep passing.")
    maintain(legacy, "capture", clip=write_clip(work, "legacy-scope", SCOPE_LEGACY))
    maintain(legacy, "next")
    pkg = save_package(work, "I3-cur", task_file(legacy, "rounds/01/implementation-package.md"))
    cell("I3-cur", pkg, "impl", 5, repo=str(legacy),
         allowed=["app.py", "legacy_utils.py"],
         behavior={"cmd": pytest_cmd, "expect_rc": 0},
         expect_deleted=["legacy_utils.py"])

    # --- R1/R2/R3: review packages over planted / clean pricing implementations ---
    def pricing_repo(name, patch_key, summary):
        repo = make_repo(work, name, {
            "pricing.py": "def apply_discount(total):\n    return total\n",
            "test_pricing.py": (
                "from pricing import apply_discount\n\n\n"
                "def test_small_orders_unchanged():\n"
                "    assert apply_discount(50) == 50\n\n\n"
                "def test_large_orders_discounted():\n"
                "    assert apply_discount(200) == 180.0\n"
            ),
        })
        init_maintain(repo, test_command=pytest_cmd)
        maintain(repo, "new", "Apply a 10 percent discount for orders of 100.00 or more, rounded to 2 decimals.")
        maintain(repo, "capture", clip=write_clip(work, f"{name}-scope", SCOPE_PRICING))
        maintain(repo, "next")
        maintain(repo, "capture",
                 clip=write_clip(work, f"{name}-impl",
                                 impl_response(PRICING_PATCHES[patch_key], summary)))
        maintain(repo, "apply", yes=True)
        maintain(repo, "next")
        return save_package(work, name, task_file(repo, "rounds/01/review-package.md"))

    pkg = pricing_repo("R1-cur", "boundary", "Implemented the discount for qualifying orders.")
    cell("R1-cur", pkg, "review", 4, expected="CHANGES_REQUIRED")
    pkg = pricing_repo("R2-cur", "clean", "Implemented the discount for qualifying orders.")
    cell("R2-cur", pkg, "review", 7, expected="APPROVE")
    pkg_a = pricing_repo("R3-A", "rounding", "Implemented the discount with rounding for qualifying orders.")
    cell("R3-A", pkg_a, "review", 7, expected="CHANGES_REQUIRED")
    pkg_b = work / "packages" / "R3-B.md"
    pkg_b.write_text(pkg_a.read_text(encoding="utf-8"), encoding="utf-8")
    surgery(pkg_b, EVIDENCE_RULE_OLD, EVIDENCE_RULE_NEW)
    cell("R3-B", pkg_b, "review", 7, expected="CHANGES_REQUIRED")

    # --- F1: correction package after a failing round ---
    greetfix = make_repo(work, "greetfix", {
        "app.py": "def greeting():\n    return \"Helo, world\"\n",
        "test_app.py": (
            "from app import greeting\n\n\n"
            "def test_greeting():\n    assert greeting() == \"Hello, world!\"\n"
        ),
    })
    init_maintain(greetfix, test_command=pytest_cmd)
    maintain(greetfix, "new", 'The greeting must read exactly "Hello, world!". Fix it so the test suite passes.')
    maintain(greetfix, "capture", clip=write_clip(work, "greetfix-scope", SCOPE_GREETFIX))
    maintain(greetfix, "next")
    maintain(greetfix, "capture",
             clip=write_clip(work, "greetfix-impl",
                             impl_response(GREETFIX_WRONG_PATCH, "Corrected the typo.")))
    maintain(greetfix, "apply", yes=True)  # tests fail: missing "!"
    maintain(greetfix, "next")
    pkg = save_package(work, "F1-cur", task_file(greetfix, "rounds/02/fix-package.md"))
    cell("F1-cur", pkg, "fix", 7, repo=str(greetfix), allowed=["app.py"],
         behavior={"cmd": pytest_cmd, "expect_rc": 0})

    # --- RS1: rescope package after a scope contradiction ---
    taxapp = make_repo(work, "taxapp", {
        "checkout.py": "def total_with_tax(total):\n    raise NotImplementedError\n",
        "test_checkout.py": (
            "from checkout import total_with_tax\n\n\n"
            "def test_tax():\n    assert total_with_tax(100) == 108.0\n"
        ),
    })
    init_maintain(taxapp, test_command=pytest_cmd)
    maintain(taxapp, "new",
             "Add sales tax: total_with_tax(total) in checkout.py must apply "
             "the TAX_RATE defined in rates.py.")
    maintain(taxapp, "capture", clip=write_clip(work, "taxapp-scope", SCOPE_TAXAPP))
    maintain(taxapp, "next")
    maintain(taxapp, "capture",
             clip=write_clip(work, "taxapp-impl", TAXAPP_RESCOPE_REQUIRED))
    maintain(taxapp, "next")
    pkg = save_package(work, "RS1-cur", task_file(taxapp, "rescopes/01/package.md"))
    cell("RS1-cur", pkg, "rescope", 6, needed=["checkout.py", "rates.py"])

    (work / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for name, info in manifest["cells"].items():
        (work / "replies" / name).mkdir(parents=True, exist_ok=True)
    total = sum(info["n"] for info in manifest["cells"].values())
    print(f"built {len(manifest['cells'])} cells, {total} planned samples")
    print(f"manifest: {work / 'manifest.json'}")


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "eval-work")
