#!/usr/bin/env python3
"""Grade experiment replies produced against build_fixtures.py packages.

Reads <work-dir>/manifest.json and <work-dir>/replies/<cell>/<n>.md, grades
each reply mechanically with Maintain's own parsers plus real `git apply` and
test runs in throwaway copies of the fixture repositories, and prints one
summary line per cell.

Usage:
    python3 experiments/grade.py <work-dir>
"""

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location("m", REPO_ROOT / "maintain" / "maintain.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

VERDICT_LINE_RE = re.compile(
    r"^[ \t>#]*\**[ \t]*VERDICT[ \t]*\**[ \t]*:[ \t]*\**[ \t]*"
    r"(APPROVED?|CHANGES_REQUIRED|RESCOPE)\b",
    re.MULTILINE | re.IGNORECASE,
)


def check_patch(repo, patch_text):
    with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False) as fh:
        fh.write(patch_text if patch_text.endswith("\n") else patch_text + "\n")
        name = fh.name
    proc = subprocess.run(
        ["git", "-C", str(repo), "apply", "--recount", "--check", name],
        capture_output=True, text=True,
    )
    return proc.returncode == 0, name


def fresh_copy(repo, dest_root, label):
    dest = dest_root / label
    shutil.copytree(repo, dest, ignore=shutil.ignore_patterns("__pycache__"))
    maintain_dir = dest / ".maintain"
    if maintain_dir.exists():
        shutil.rmtree(maintain_dir)
    return dest


def run_behavior(repo, behavior):
    proc = subprocess.run(
        behavior["cmd"], shell=True, cwd=str(repo),
        capture_output=True, text=True, timeout=120,
    )
    if "expect_rc" in behavior:
        return proc.returncode == behavior["expect_rc"]
    if "expect_stdout" in behavior:
        return behavior["expect_stdout"] in proc.stdout
    return proc.returncode == 0


def grade_patch_reply(text, info, scratch, label):
    result = {
        "marker": bool(m.find_marker(text, "STATUS",
                                     ["IMPLEMENTATION_COMPLETE", "RESCOPE_REQUIRED"])),
        "one_block": False, "raw_applies": False, "healed_applies": False,
        "in_scope": False, "behavior_ok": False, "deleted_ok": True,
    }
    blocks = m.extract_diff_blocks(text)
    result["one_block"] = len(blocks) == 1
    if not blocks:
        return result
    raw = blocks[0]
    repo = Path(info["repo"])
    work_copy = fresh_copy(repo, scratch, label)
    result["raw_applies"], _ = check_patch(work_copy, raw)
    healed = m.normalise_patch(raw if raw.endswith("\n") else raw + "\n")
    healed_ok, patch_file = check_patch(work_copy, healed)
    result["healed_applies"] = healed_ok
    try:
        result["in_scope"] = set(m.patch_paths(raw)) <= set(info["allowed"])
    except m.MaintainError:
        result["in_scope"] = False
    if healed_ok and result["in_scope"]:
        apply_proc = subprocess.run(
            ["git", "-C", str(work_copy), "apply", "--recount", patch_file],
            capture_output=True, text=True,
        )
        if apply_proc.returncode == 0:
            result["behavior_ok"] = run_behavior(work_copy, info["behavior"])
            for name in info.get("expect_deleted", []):
                if (work_copy / name).exists():
                    result["deleted_ok"] = False
    return result


def grade_review_reply(text, info):
    verdict = m.find_marker(text, "VERDICT",
                            ["APPROVED", "APPROVE", "CHANGES_REQUIRED", "RESCOPE"])
    if verdict and verdict.startswith("APPROVE"):
        verdict = "APPROVE"
    distinct = {
        ("APPROVE" if v.upper().startswith("APPROVE") else v.upper())
        for v in VERDICT_LINE_RE.findall(text)
    }
    sections = all(m.extract_section(text, s)
                   for s in ("Findings", "Acceptance-Criteria Coverage", "Risks"))
    return {
        "verdict": verdict,
        "expected": verdict == info["expected"],
        "consistent": len(distinct) == 1,
        "sections": sections,
    }


def grade_scope_reply(text, info):
    body = m.extract_section(text, "Allowed Files") or ""
    files = set(m.parse_path_bullets(body))
    required, tolerated = set(info["required"]), set(info["tolerated"])
    if files == required:
        outcome = "exact"
    elif files and required <= files <= tolerated:
        outcome = "ok"
    elif required <= files:
        outcome = "over"
    else:
        outcome = "broken"
    return {
        "marker": bool(m.find_marker(text, "STATUS", ["SCOPE_COMPLETE"])),
        "files": sorted(files), "outcome": outcome,
    }


def grade_rescope_reply(text, info):
    body = m.extract_section(text, "Revised Allowed Files") or ""
    files = set(m.parse_path_bullets(body))
    return {
        "status": bool(m.find_marker(text, "STATUS", ["RESCOPED"])),
        "work": m.find_marker(text, "EXISTING_WORK", ["RETAIN", "PARTIAL", "DISCARD"]),
        "files": sorted(files),
        "needed_present": set(info["needed"]) <= files,
        "criteria": bool(m.extract_section(text, "Revised Acceptance Criteria")),
    }


def main(work):
    work = Path(work).resolve()
    manifest = json.loads((work / "manifest.json").read_text(encoding="utf-8"))
    scratch = work / "behave"
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir()
    all_results = {}

    for cell, info in manifest["cells"].items():
        rows = []
        for reply in sorted((work / "replies" / cell).glob("*.md")):
            text = reply.read_text(encoding="utf-8")
            if info["type"] in ("impl", "fix"):
                row = grade_patch_reply(text, info, scratch, f"{cell}-{reply.stem}")
            elif info["type"] == "review":
                row = grade_review_reply(text, info)
            elif info["type"] == "scope":
                row = grade_scope_reply(text, info)
            else:
                row = grade_rescope_reply(text, info)
            row["sample"] = reply.stem
            rows.append(row)
        all_results[cell] = rows

        def rate(key, predicate=bool):
            hits = sum(1 for r in rows if predicate(r.get(key)))
            return f"{hits}/{len(rows)}"

        if info["type"] in ("impl", "fix"):
            print(f"{cell:8s} n={len(rows)}  one_block={rate('one_block')} "
                  f"raw_apply={rate('raw_applies')} healed_apply={rate('healed_applies')} "
                  f"in_scope={rate('in_scope')} behavior_pass={rate('behavior_ok')} "
                  f"deleted_ok={rate('deleted_ok')}")
        elif info["type"] == "review":
            counts = {}
            for r in rows:
                counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
            print(f"{cell:8s} n={len(rows)}  expected({info['expected']})={rate('expected')} "
                  f"consistent={rate('consistent')} sections={rate('sections')} "
                  f"verdicts={counts}")
        elif info["type"] == "scope":
            counts = {}
            for r in rows:
                counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
            print(f"{cell:8s} n={len(rows)}  marker={rate('marker')} outcomes={counts}")
        else:
            works = {}
            for r in rows:
                works[r["work"]] = works.get(r["work"], 0) + 1
            print(f"{cell:8s} n={len(rows)}  status={rate('status')} "
                  f"needed_files={rate('needed_present')} criteria={rate('criteria')} "
                  f"existing_work={works}")

    (work / "results.json").write_text(json.dumps(all_results, indent=2), encoding="utf-8")
    print(f"\ndetailed results: {work / 'results.json'}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "eval-work")
