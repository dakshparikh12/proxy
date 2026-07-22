"""Task coverage gate for the v2 build-loop decompose pipeline.

Checks bidirectional coverage:
  1. Every criterion in the acceptance bundle appears in >=1 task's criterion_ids.
  2. Every task references a real criterion (no dangling).

Usage: python3 scripts/task_coverage.py <id>   e.g.  00  or  03
Exit 0 = fully covered; exit 1 = gaps (printed).
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "scripts"))

from coverage_gate import parse_criteria  # type: ignore[import-not-found]  # noqa: E402
from lib_spec import bundle_dir, slice_dir  # type: ignore[import-not-found]  # noqa: E402


def task_coverage(doc_id: str) -> int:
    """Check coverage and return 0 (ok) or 1 (gaps).

    Prints gap lists to stdout.
    """
    crit_path = bundle_dir(doc_id) / "criteria" / "criteria.yaml"
    tasks_path = slice_dir(doc_id) / "tasks.json"

    if not crit_path.exists():
        print(f"COVERAGE FAIL: no criteria at {crit_path}")
        return 1
    if not tasks_path.exists():
        print(f"COVERAGE FAIL: no tasks.json at {tasks_path} — run decompose first")
        return 1

    crits = parse_criteria(crit_path)
    known_crit_ids = {c["id"] for c in crits}

    data = json.loads(tasks_path.read_text())
    tasks = data.get("tasks", [])

    # Build a set of all criterion_ids referenced in tasks
    covered_crit_ids: set[str] = set()
    dangling: list[tuple[str, str]] = []  # (task_id, bad_criterion_id)

    for task in tasks:
        task_id: str = task.get("task_id", "<unknown>")
        for cid in task.get("criterion_ids", []):
            if cid in known_crit_ids:
                covered_crit_ids.add(cid)
            else:
                dangling.append((task_id, cid))

    uncovered = sorted(known_crit_ids - covered_crit_ids)

    print(f"=== TASK COVERAGE GATE — doc{doc_id} ===")
    print(
        f"criteria: {len(known_crit_ids)}  |  tasks: {len(tasks)}"
        f"  |  covered: {len(covered_crit_ids)}/{len(known_crit_ids)}"
    )

    ok = True

    if uncovered:
        ok = False
        print(f"\nUNCOVERED CRITERIA ({len(uncovered)}) — no task references these:")
        for cid in uncovered:
            print(f"  {cid}")

    if dangling:
        ok = False
        print(f"\nDANGLING CRITERION REFS ({len(dangling)}) — task references a non-existent criterion:")
        for task_id, cid in dangling:
            print(f"  {task_id} -> {cid}")

    if ok:
        print("\nCOVERAGE PASS: every criterion is covered, every task traces to a real criterion.")
        return 0

    print("\nCOVERAGE FAIL: gaps found.")
    return 1


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/task_coverage.py <id>  (e.g. 00 or 03)", file=sys.stderr)
        sys.exit(1)
    rc = task_coverage(sys.argv[1])
    sys.exit(rc)


if __name__ == "__main__":
    main()
