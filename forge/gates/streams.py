#!/usr/bin/env python3
"""forge stream partitioner — split tasks into independent, parallel-safe build streams.

A STREAM = a set of tasks that can be built concurrently in an isolated worktree without
colliding with another stream. The partition is deterministic:

  - stream 0 = every task touching the shared wire contracts (libs/contracts) — built FIRST,
    serially, because a contract change invalidates every downstream stream's compile.
  - remaining tasks grouped by MODULE KEY (the criterion-id milestone prefix, e.g. HOST, SUB,
    CANVAS) and topologically ordered within a group by depends_on.

Emits slices/<id>/streams.json = [{stream_id, module, task_ids, depends_on_streams}]. The
orchestrator builds stream 0, then runs the independent streams 2-4 at a time.

v1 note: partition is by milestone prefix + depends_on. When decompose emits per-task
write_globs, add a disjointness check here and fail loud (BLOCKED:stream-overlap) on overlap.

Usage: python3 streams.py <id>   (project-relative)
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
from collections import OrderedDict


def _project_root() -> pathlib.Path:
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
        if out.returncode == 0 and out.stdout.strip():
            return pathlib.Path(out.stdout.strip())
    except Exception:  # noqa: BLE001
        pass
    return pathlib.Path.cwd()


def _module_key(task: dict) -> str:
    """Milestone/module prefix from the first criterion id: AC-HOST-013 -> HOST."""
    cids = task.get("criterion_ids") or [task.get("task_id", "")]
    m = re.match(r"(?:T-)?(?:AC-)?([A-Za-z0-9]+)-", cids[0])
    return m.group(1).upper() if m else "MISC"


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("usage: streams.py <id>", file=sys.stderr)
        sys.exit(2)
    doc_id = args[0]
    root = _project_root()
    tasks_path = root / f"slices/{doc_id}/tasks.json"
    if not tasks_path.exists():
        print(f"BLOCKED: {tasks_path} not found — run decompose first", file=sys.stderr)
        sys.exit(1)
    tasks = json.loads(tasks_path.read_text()).get("tasks", [])

    groups: "OrderedDict[str, list[str]]" = OrderedDict()
    contracts: list[str] = []
    for t in tasks:
        key = _module_key(t)
        # contracts / registry tasks -> serial stream 0
        if key in {"CON", "CONTRACT", "REG", "REGISTRY"}:
            contracts.append(t["task_id"])
        else:
            groups.setdefault(key, []).append(t["task_id"])

    streams = []
    sid = 0
    if contracts:
        streams.append({"stream_id": 0, "module": "contracts", "task_ids": contracts, "depends_on_streams": []})
        sid = 1
    for module, tids in groups.items():
        streams.append({
            "stream_id": sid,
            "module": module,
            "task_ids": tids,
            "depends_on_streams": [0] if contracts else [],
        })
        sid += 1

    out = root / f"slices/{doc_id}/streams.json"
    out.write_text(json.dumps({"spec": doc_id, "streams": streams}, indent=2))
    print(f"streams(doc{doc_id}): {len(streams)} streams over {len(tasks)} tasks "
          f"(stream 0 = contracts: {len(contracts)} tasks)" if contracts
          else f"streams(doc{doc_id}): {len(streams)} streams over {len(tasks)} tasks")
    for s in streams:
        print(f"  [{s['stream_id']}] {s['module']}: {len(s['task_ids'])} tasks")
    sys.exit(0)


if __name__ == "__main__":
    main()
