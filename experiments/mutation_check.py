#!/usr/bin/env python3
"""Advisory mutation check for a Maintain-managed repository.

Applies small source mutations one at a time and reports how many the test
suite catches. A surviving mutant is a behaviour the suite executes but does
not pin — the gap that line coverage cannot see.

Mutations are applied to a throwaway copy of the repository, never to the
working tree, so an interrupted run cannot strand a mutation in your source.

Usage:
    python3 experiments/mutation_check.py [--repo DIR] [--sample N]
                                          [--timeout SECONDS] [FILE ...]

With no FILE arguments the targets are read from the repository's completed
Maintain tasks (the same derivation `maintain harden` uses). The test command
comes from .maintain/config.json — "test_command" by default, or
"harden_command" with --gate.

Exit status is 0 when every sampled mutant is caught, 1 otherwise, so the
check can be chained into a gate command once you trust its results.
"""

import argparse
import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# (pattern, replacement); each match site becomes one mutant.
RULES = [
    (r"==", "!="),
    (r"!=", "=="),
    (r"(?<![<>=!])>=", ">"),
    (r"(?<![<>=!])<=", "<"),
    (r"(?<![<>=!])>(?!=)", ">="),
    (r"(?<![<>=!])<(?!=)", "<="),
    (r"\+ 1\b", "+ 2"),
    (r"- 1\b", "- 2"),
    (r"\bTrue\b", "False"),
    (r"\bFalse\b", "True"),
    (r"\.strip\(\)", ""),
    (r"\.rstrip\(\)", ""),
    (r"\.lstrip\(\)", ""),
    (r"\breverse=True\b", "reverse=False"),
    (r"\bor\b", "and"),
]


def docstring_lines(source: str) -> set:
    """Line numbers (1-based) occupied by docstrings.

    Mutating prose produces equivalent mutants — survivors that no test
    could ever catch — so those lines are excluded from the mutant pool.
    """
    covered = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return covered
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                covered.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return covered


def load_config(repo: Path) -> dict:
    path = repo / ".maintain" / "config.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def derive_targets(repo: Path) -> list:
    """Non-test files touched by completed, non-hardening Maintain tasks."""
    targets = []
    for state_file in sorted((repo / ".maintain" / "tasks").glob("*/state.json")):
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if state.get("stage") != "complete" or state.get("kind") == "harden":
            continue
        for path in state.get("allowed_files", []):
            if Path(path).name.startswith("test_"):
                continue
            if path not in targets and (repo / path).exists():
                targets.append(path)
    return targets


def build_mutants(repo: Path, targets: list) -> list:
    mutants = []
    for target in targets:
        source = (repo / target).read_text(encoding="utf-8")
        skip = docstring_lines(source)
        for index, line in enumerate(source.split("\n")):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or (index + 1) in skip:
                continue
            for pattern, replacement in RULES:
                if re.search(pattern, line):
                    mutated = re.sub(pattern, replacement, line, count=1)
                    if mutated != line:
                        mutants.append((target, index, line, mutated))
    return mutants


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("files", nargs="*", help="target files (default: derived)")
    parser.add_argument("--repo", default=".", help="repository root (default: .)")
    parser.add_argument("--sample", type=int, default=40,
                        help="maximum mutants to test (default: 40, 0 for all)")
    parser.add_argument("--timeout", type=int, default=30,
                        help="seconds per mutant before it counts as caught (default: 30)")
    parser.add_argument("--gate", action="store_true",
                        help="use harden_command instead of test_command")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    config = load_config(repo)
    command = config.get("harden_command" if args.gate else "test_command")
    if not command:
        key = "harden_command" if args.gate else "test_command"
        print(f"error: no {key} configured in {repo / '.maintain' / 'config.json'}",
              file=sys.stderr)
        return 2

    targets = args.files or derive_targets(repo)
    if not targets:
        print("error: no target files given and none derived from completed tasks",
              file=sys.stderr)
        return 2
    missing = [target for target in targets if not (repo / target).is_file()]
    if missing:
        print("error: target file(s) not found in " + str(repo) + ": " + ", ".join(missing),
              file=sys.stderr)
        return 2

    mutants = build_mutants(repo, targets)
    if not mutants:
        print("error: no mutants could be generated for the given targets", file=sys.stderr)
        return 2
    if args.sample and len(mutants) > args.sample:
        step = len(mutants) // args.sample
        sample = mutants[::step][: args.sample]
    else:
        sample = mutants

    with tempfile.TemporaryDirectory(prefix="maintain-mutation-") as tmp:
        work = Path(tmp) / repo.name
        shutil.copytree(
            repo, work,
            ignore=shutil.ignore_patterns(
                ".git", ".maintain", "__pycache__", ".pytest_cache", ".coverage", "site"
            ),
        )

        def run_suite() -> int:
            proc = subprocess.run(
                command, shell=True, cwd=str(work),
                capture_output=True, text=True, timeout=args.timeout,
            )
            return proc.returncode

        try:
            if run_suite() != 0:
                print("error: the suite is not green before mutating; fix that first",
                      file=sys.stderr)
                return 2
        except subprocess.TimeoutExpired:
            print(f"error: the suite did not finish within {args.timeout}s; raise --timeout",
                  file=sys.stderr)
            return 2

        print(f"targets: {', '.join(targets)}")
        print(f"{len(mutants)} mutants generated, testing {len(sample)}\n")

        killed, hangs, survived = 0, 0, []
        for number, (target, index, original_line, mutated_line) in enumerate(sample, 1):
            path = work / target
            original = (repo / target).read_text(encoding="utf-8")
            lines = original.split("\n")
            lines[index] = mutated_line
            path.write_text("\n".join(lines), encoding="utf-8")
            try:
                caught = run_suite() != 0
            except subprocess.TimeoutExpired:
                caught, hangs = True, hangs + 1
            path.write_text(original, encoding="utf-8")

            if caught:
                killed += 1
            else:
                survived.append((target, index + 1, original_line.strip(), mutated_line.strip()))
            print(f"  [{number}/{len(sample)}] {target}:{index + 1} "
                  f"{'caught' if caught else 'SURVIVED'}", flush=True)

    percent = 100 * killed // len(sample)
    print(f"\nmutation score: {killed}/{len(sample)} caught ({percent}%)"
          + (f", {hangs} as hangs" if hangs else ""))
    if survived:
        print("\nsurvivors — executed by the suite but not pinned by any assertion:")
        for target, line_number, before, after in survived:
            print(f"  {target}:{line_number}")
            print(f"      {before}")
            print(f"   -> {after}")
        print("\nSome survivors may be equivalent mutants (a change with no observable\n"
              "effect). Read each one before treating it as a missing test.")
        return 1
    print("\nno survivors.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
