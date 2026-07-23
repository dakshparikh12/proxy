#!/usr/bin/env python3
"""forge coverage gate — the deterministic coverage GUARANTEE (both-ways closure).

Proves, as a decidable boolean, that the sealed acceptance bundle covers the spec and
that the work covers the bundle:

    requirement  <->  criterion  <->  task

- every requirement is covered by >=1 criterion   (nothing in the spec is un-criterion'd)
- every criterion traces to a real requirement     (no floating/authorityless criterion)
- every criterion is covered by >=1 task           (nothing to build is un-tasked)
- every task's criterion_ids are real              (no orphan task)

Exit 0 = fully closed both ways; nonzero = gaps (printed). The task half is skipped
(with a note) until tasks.json exists, so this doubles as the pre-decompose seal gate.

Project-relative: resolves the bundle + slice under the CURRENT project (git root or cwd),
NOT the plugin install dir. Dependency-free (no pyyaml).

Usage: python3 coverage.py <id>        e.g.  00   (bundle = acceptance/doc00/, slice = slices/00/)
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys


def _project_root() -> pathlib.Path:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True
        )
        if out.returncode == 0 and out.stdout.strip():
            return pathlib.Path(out.stdout.strip())
    except Exception:  # noqa: BLE001
        pass
    return pathlib.Path.cwd()


def parse_requirements(path: pathlib.Path) -> dict[str, str]:
    reqs: dict[str, str] = {}
    cur = None
    for line in path.read_text().splitlines():
        m = re.match(r"\s*-?\s*requirement_id:\s*(\S+)", line)
        if m:
            cur = m.group(1).strip().strip('"')
            reqs[cur] = "?"
            continue
        c = re.match(r"\s*criticality:\s*(\S+)", line)
        if c and cur:
            reqs[cur] = c.group(1).strip()
    return reqs


def parse_criteria(path: pathlib.Path) -> list[dict]:
    crits: list[dict] = []
    cur = None
    in_refs = in_tids = False
    for line in path.read_text().splitlines():
        m = re.match(r"\s*-?\s*criterion_id:\s*(\S+)", line)
        if m:
            in_refs = in_tids = False
            cur = {"id": m.group(1).strip().strip('"'), "refs": [], "test_ids": []}
            crits.append(cur)
            continue
        if cur is None:
            continue
        r = re.match(r"\s*authority_refs:\s*\[(.*)\]", line)
        if r:
            in_refs = in_tids = False
            cur["refs"] = [x.strip().strip('"') for x in r.group(1).split(",") if x.strip()]
            continue
        if re.match(r"\s*authority_refs:\s*$", line):
            in_refs, in_tids = True, False
            continue
        t = re.match(r"\s*test_ids:\s*\[(.*)\]", line)
        if t:
            in_refs = in_tids = False
            cur["test_ids"] = [x.strip().strip('"') for x in t.group(1).split(",") if x.strip()]
            continue
        if re.match(r"\s*test_ids:\s*$", line):
            in_tids, in_refs = True, False
            continue
        li = re.match(r"\s*-\s*(\S+)\s*$", line)
        if li and in_refs:
            cur["refs"].append(li.group(1).strip().strip('"'))
            continue
        if li and in_tids:
            cur["test_ids"].append(li.group(1).strip().strip('"'))
            continue
        if re.match(r"\s*\w+:", line) and not li:
            in_refs = in_tids = False
    return crits


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("usage: coverage.py <id>   e.g. 00", file=sys.stderr)
        sys.exit(2)
    doc_id = args[0]
    root = _project_root()
    bundle = root / f"acceptance/doc{doc_id}"
    reqs_path = bundle / "requirements/requirements.yaml"
    crit_path = bundle / "criteria/criteria.yaml"
    if not reqs_path.exists() or not crit_path.exists():
        print(f"GATE FAIL: bundle not found at {bundle}", file=sys.stderr)
        sys.exit(1)

    reqs = parse_requirements(reqs_path)
    crits = parse_criteria(crit_path)
    fails: list[str] = []

    # req <-> crit
    covered_reqs = {r for c in crits for r in c["refs"]}
    uncovered = sorted(set(reqs) - covered_reqs)
    dangling = sorted({r for c in crits for r in c["refs"]} - set(reqs))
    authorityless = sorted(c["id"] for c in crits if not c["refs"])
    if uncovered:
        fails.append(f"UNCOVERED REQUIREMENTS ({len(uncovered)}): {uncovered[:8]}")
    if dangling:
        fails.append(f"DANGLING CRITERION REFS ({len(dangling)}): {dangling[:8]}")
    if authorityless:
        fails.append(f"AUTHORITYLESS CRITERIA ({len(authorityless)}): {authorityless[:8]}")

    # crit <-> task  (only when tasks.json exists)
    tasks_path = root / f"slices/{doc_id}/tasks.json"
    task_note = ""
    if tasks_path.exists():
        tasks = json.loads(tasks_path.read_text()).get("tasks", [])
        crit_ids = {c["id"] for c in crits}
        covered_crits = {cid for t in tasks for cid in t.get("criterion_ids", [])}
        uncovered_crits = sorted(crit_ids - covered_crits)
        orphan_refs = sorted({cid for t in tasks for cid in t.get("criterion_ids", [])} - crit_ids)
        if uncovered_crits:
            fails.append(f"UNCOVERED CRITERIA ({len(uncovered_crits)}): {uncovered_crits[:8]}")
        if orphan_refs:
            fails.append(f"ORPHAN TASK->CRITERION REFS ({len(orphan_refs)}): {orphan_refs[:8]}")
        task_note = f" | tasks: {len(tasks)}"
    else:
        task_note = " | tasks: (none yet — task closure deferred)"

    print(f"coverage(doc{doc_id}): requirements {len(reqs)} | criteria {len(crits)}{task_note}")
    if fails:
        print("GATE FAIL:")
        for f in fails:
            print(f"  - {f}")
        sys.exit(1)
    print("GATE PASS: requirement<->criterion<->task closure holds both ways.")
    sys.exit(0)


if __name__ == "__main__":
    main()
