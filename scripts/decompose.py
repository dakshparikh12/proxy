"""Decompose a sealed acceptance bundle into a slices/<id>/tasks.json file.

Usage: python3 scripts/decompose.py <id>   e.g.  00  or  03

Each task corresponds to one acceptance criterion and its acceptance command
derives from the criterion's test_ids (e.g. T-CMP-001 -> cmp_001), NOT from
the criterion_id itself (AC-CMP-001 would be the wrong source).

[B5] docs 00-03 are already built; all tasks are VERIFICATION tasks
(mode="verify").  An unbuilt doc would use mode="build".
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent

# Insert scripts/ so we can import lib_spec and coverage_gate
sys.path.insert(0, str(ROOT / "scripts"))

from coverage_gate import parse_criteria, parse_requirements  # type: ignore[import-not-found]  # noqa: E402
from lib_spec import bundle_dir, slice_dir  # type: ignore[import-not-found]  # noqa: E402

_CRITICALITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}


def _test_id_to_k_fragment(test_id: str) -> str:
    """Convert a test_id to a -k fragment.

    T-CMP-001 -> cmp_001
    AC-CMP-001 -> cmp_001
    """
    # Strip leading T- or AC- prefix
    frag = re.sub(r"^(?:T|AC)-", "", test_id)
    # Lowercase and replace dashes with underscores
    return frag.lower().replace("-", "_")


def _build_acceptance(criterion_id: str) -> dict:  # type: ignore[type-arg]
    """Build the acceptance dict for a criterion.

    The -k selector is derived from the CRITERION id, because the acceptance
    tests are named after the criterion they verify (``test_ac_m1_004`` ==
    ``AC-M1-004``) — NOT after the bundle's ``test_ids`` field, which can drift
    (e.g. AC-M1-004 carries ``test_ids: [T-M1-006]`` while its test is
    ``test_ac_m1_004``). This is a no-op wherever test_ids already align with the
    criterion number (all of doc00), and repairs the cases where they don't.
    A criterion with no matching test collects nothing (pytest exit 5), which is
    the honest "no acceptance test exists yet" signal — not a silent pass.
    """
    frag = _test_id_to_k_fragment(criterion_id)
    return {"cmd": f'pytest -q -k "{frag}"'}


def decompose(doc_id: str) -> None:
    """Write slices/<id>/tasks.json from the acceptance bundle for doc_id."""
    base = bundle_dir(doc_id)
    req_path = base / "requirements" / "requirements.yaml"
    crit_path = base / "criteria" / "criteria.yaml"

    reqs = parse_requirements(req_path)
    crits = parse_criteria(crit_path)

    # Sort: P0 first, then P1, then P2, then unknown; within each tier by criterion_id
    def sort_key(c: dict) -> tuple[int, str]:  # type: ignore[type-arg]
        tier = _CRITICALITY_ORDER.get(c["criticality"], 99)
        return (tier, c["id"])

    crits_sorted = sorted(crits, key=sort_key)

    tasks = []
    for c in crits_sorted:
        crit_id: str = c["id"]
        # requirement_ids: the criterion's authority_refs that are real requirements
        req_ids = [ref for ref in c["refs"] if ref in reqs]
        acceptance = _build_acceptance(crit_id)
        task = {
            "task_id": f"T-{crit_id}",
            "title": crit_id,
            "criterion_ids": [crit_id],
            "requirement_ids": req_ids,
            "acceptance": acceptance,
            "depends_on": [],
            "passes": False,
        }
        tasks.append(task)

    out_dir = slice_dir(doc_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "tasks.json"

    # Preserve verified progress: re-decomposing must NOT wipe passes:true flags for
    # tasks that still exist (same task_id). Without this, any run of the acceptance
    # suite (which invokes decompose) resets the ledger and the DONE predicate can
    # never latch. New/renamed tasks default to passes:false.
    if out_path.exists():
        try:
            prev = json.loads(out_path.read_text())
            passed = {
                t["task_id"] for t in prev.get("tasks", []) if t.get("passes") is True
            }
        except (json.JSONDecodeError, KeyError, OSError):
            passed = set()
        for t in tasks:
            if t["task_id"] in passed:
                t["passes"] = True

    # [B5] docs 00-03 have their services/libs code built; mode = "verify"
    # An unbuilt doc would use mode = "build"
    payload = {
        "spec": doc_id,
        "mode": "verify",
        "tasks": tasks,
    }
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {len(tasks)} tasks to {out_path}")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/decompose.py <id>  (e.g. 00 or 03)", file=sys.stderr)
        sys.exit(1)
    decompose(sys.argv[1])


if __name__ == "__main__":
    main()
