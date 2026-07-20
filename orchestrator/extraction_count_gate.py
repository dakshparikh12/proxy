#!/usr/bin/env python3
"""Independent extraction-count gate (Task 5) — the check nobody had.

Every prior gate verified that the requirements ALREADY in the bundle are covered. None verified the
bundle's requirement COUNT is complete — that whole normative obligations weren't silently missed
before anything downstream ever looked (the "garbage denominator": you can hit 100% coverage of a set
that was never the whole spec).

This gate runs ONCE per doc after generation: a FRESH-context agent (separate authority) reads the
raw spec with NO access to requirements.yaml and produces its OWN count of distinct normative
obligations (orchestrator/prompts/extraction_count.md). We compare that independent count to the
bundle's actual requirement count. If they materially disagree (> threshold, default 10%), we HALT
and write the discrepancy for founder review — we do NOT auto-resolve by regenerating, because a
material gap means a human must look at WHAT is actually different (missed obligations vs. over-split).

Runs on opus (model-routing: "extraction-count-audit") — completeness is the highest-stakes judgment.

Usage: python3 orchestrator/extraction_count_gate.py <doc> [--threshold 0.10]
Exit 0 = counts agree within threshold; 1 = material disagreement (HALT for founder review).
"""
from __future__ import annotations

import os
import pathlib
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from criteria_coverage_gate import parse_requirements  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
ORCH = pathlib.Path(__file__).resolve().parent
DEFAULT_THRESHOLD = 0.10

try:
    from model_routing import model_for  # type: ignore  # noqa: E402
except Exception:  # pragma: no cover - lands in Task 7
    def model_for(task_type: str) -> str:
        return "claude-opus-4-6"


def bundle_requirement_count(doc: str, base: pathlib.Path | None = None) -> int:
    base = base or (ROOT / "acceptance" / doc)
    reqs = base / "requirements" / "requirements.yaml"
    if not reqs.is_file():
        return 0
    return len(parse_requirements(reqs))


def _parse_count(stdout: str) -> int | None:
    """Extract the agent's final EXTRACTION_COUNT: <n> (last occurrence wins)."""
    hits = re.findall(r"EXTRACTION_COUNT:\s*(\d+)", stdout)
    return int(hits[-1]) if hits else None


def _real_spawn(doc: str) -> tuple[int | None, str]:
    """Run the fresh-context extraction-count agent; return (independent_count, full_output)."""
    from orchestrate import DOCS, run_agent
    spec = DOCS.get(doc, {}).get("spec", f"{doc}.md")
    res = run_agent("extraction_count.md", {"<DOC>": doc, "<SPEC>": spec},
                    timeout=60 * 20, max_turns=120, model=model_for("extraction-count-audit"),
                    phase=f"{doc} extraction-count audit")
    if getattr(res, "timed_out", False):
        return None, "extraction-count agent timed out"
    out = getattr(res, "stdout", "")
    return _parse_count(out), out


def run_extraction_gate(doc: str, *, spawn=None, threshold: float = DEFAULT_THRESHOLD,
                        base: pathlib.Path | None = None) -> dict:
    """Compare an independent spec recount against the bundle's requirement count.

    `spawn(doc) -> (independent_count, full_output)` is injectable for testing. Returns a verdict dict
    and writes evidence/<doc>-extraction-count.md for founder review."""
    spawn = spawn or _real_spawn
    bundle = bundle_requirement_count(doc, base)
    independent, out = spawn(doc)

    result: dict = {"doc": doc, "bundle_requirement_count": bundle,
                    "independent_count": independent, "threshold": threshold}
    if independent is None:
        result.update(halt=True, verdict="NO_COUNT",
                      reason="extraction-count agent produced no parseable EXTRACTION_COUNT line")
    elif bundle == 0:
        result.update(halt=True, verdict="NO_BUNDLE",
                      reason=f"no requirements.yaml requirement count for {doc}")
    else:
        rel = abs(independent - bundle) / bundle
        result["rel_diff"] = round(rel, 4)
        if rel > threshold:
            direction = "UNDER-extracted (bundle missing obligations)" if independent > bundle \
                else "OVER-extracted (bundle split beyond the spec)"
            result.update(halt=True, verdict="MATERIAL_DISAGREEMENT",
                          reason=(f"independent count {independent} vs bundle {bundle} "
                                  f"= {rel:.1%} > {threshold:.0%} threshold — bundle likely {direction}. "
                                  f"HALT for founder review; do NOT auto-regenerate."))
        else:
            result.update(halt=False, verdict="AGREE",
                          reason=f"independent {independent} vs bundle {bundle} = {rel:.1%} "
                                 f"within {threshold:.0%} threshold")

    # Persist the full independent enumeration + verdict for the founder to inspect on a HALT.
    ev = ROOT / "evidence"
    ev.mkdir(exist_ok=True)
    (ev / f"{doc}-extraction-count.md").write_text(
        f"# Extraction-count audit — {doc}\n\n"
        f"- bundle requirement count: {bundle}\n"
        f"- independent recount: {independent}\n"
        f"- verdict: {result['verdict']} — {result['reason']}\n\n"
        f"## Independent agent enumeration (spec-only, no bundle access)\n\n{out}\n"
    )
    return result


def main() -> None:
    import json
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    doc = args[0] if args else "doc02"
    threshold = DEFAULT_THRESHOLD
    if "--threshold" in sys.argv:
        threshold = float(sys.argv[sys.argv.index("--threshold") + 1])
    result = run_extraction_gate(doc, threshold=threshold)
    print(json.dumps({k: v for k, v in result.items()}, indent=1))
    print(f"\n{'HALT' if result['halt'] else 'PASS'}: {result['verdict']} — {result['reason']}")
    sys.exit(1 if result["halt"] else 0)


if __name__ == "__main__":
    main()
