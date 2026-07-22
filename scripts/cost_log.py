"""Cost logging for the v2 build-loop.

Appends one JSON line per call to evidence/cost/<id>.jsonl.
Honor COST_LOG_DIR env override so tests don't dirty evidence/.

CLI: python3 scripts/cost_log.py <id> <phase> <tokens> <cost_usd> <wall_s>
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time

ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent


def _log_dir() -> pathlib.Path:
    override = os.environ.get("COST_LOG_DIR")
    if override:
        return pathlib.Path(override)
    return ROOT / "evidence" / "cost"


def _log_path(id: str) -> pathlib.Path:  # noqa: A002
    return _log_dir() / f"{id}.jsonl"


def append(id: str, phase: str, tokens: int, cost_usd: float, wall_s: float) -> None:  # noqa: A002
    """Write one JSON line to evidence/cost/<id>.jsonl (or COST_LOG_DIR/<id>.jsonl)."""
    record: dict[str, float | int | str] = {
        "ts": time.time(),
        "phase": phase,
        "tokens": tokens,
        "cost_usd": cost_usd,
        "wall_s": wall_s,
    }
    log_path = _log_path(id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as fh:
        fh.write(json.dumps(record) + "\n")


def spent_usd(id: str) -> float:  # noqa: A002
    """Return the sum of cost_usd across all entries in <id>.jsonl; 0.0 if file absent."""
    log_path = _log_path(id)
    if not log_path.exists():
        return 0.0
    total = 0.0
    for line in log_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        total += float(record.get("cost_usd", 0.0))
    return total


def main() -> None:
    """CLI entry point.

    Append mode: cost_log.py <id> <phase> <tokens> <cost_usd> <wall_s>
    Query mode:  cost_log.py --query-spent <id>   (prints the total USD spent)
    """
    args = sys.argv[1:]
    if args and args[0] == "--query-spent":
        if len(args) < 2:  # noqa: PLR2004
            print("Usage: python3 scripts/cost_log.py --query-spent <id>", file=sys.stderr)
            sys.exit(1)
        print(f"{spent_usd(args[1]):.6f}")
        return

    if len(args) != 5:  # noqa: PLR2004
        print(
            "Usage: python3 scripts/cost_log.py <id> <phase> <tokens> <cost_usd> <wall_s>",
            file=sys.stderr,
        )
        sys.exit(1)
    doc_id, phase, tokens_str, cost_str, wall_str = args
    append(doc_id, phase, int(tokens_str), float(cost_str), float(wall_str))


if __name__ == "__main__":
    main()
