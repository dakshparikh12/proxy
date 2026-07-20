#!/usr/bin/env python3
"""Fresh-context reality/negative critics (Task 4) — the judgment half of the ladder's cassette tiers.

Called by ladder_runner for the `reality` and `negative` tiers. Each critic invocation is a FRESH
claude session (zero shared context with the builder or with any other critic run) that judges ONE
criterion with the REASON-FIRST prompt (orchestrator/prompts/reality_critic.md): it must state,
before reading any implementation, what a genuine implementation of this specific criterion must
concretely do, THEN compare the code against that independently-formed bar.

A criterion's reality/negative tier is marked done only after TWO CONSECUTIVE clean passes from TWO
DIFFERENT fresh invocations (a single failure resets the streak). Total rounds are capped at 4: if
two-in-a-row is not reached by then, that is a real signal (spec problem or genuine defect) and the
critic HALTS for founder review rather than looping forever.

Mechanical tiers NEVER reach this module (ladder_runner routes only reality/negative here) — so the
"lint/unit/integration spawn no agent" rule (Task 6.2) holds structurally.
"""
from __future__ import annotations

import os
import pathlib
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ladder_schema_gate import parse_manifest  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
ORCH = pathlib.Path(__file__).resolve().parent

MAX_ROUNDS = 4
CONSECUTIVE_CLEAN_REQUIRED = 2
CRITIC_TIMEOUT = 60 * 15
CRITIC_MAX_TURNS = 60

# Reality/negative critics run on sonnet (Task 7 model-routing: "reality-critic"/"negative-critic").
# Task 7 replaces this constant with a read from orchestrator/model-routing.json.
try:
    sys.path.insert(0, str(ORCH))
    from model_routing import model_for  # type: ignore  # noqa: E402
except Exception:  # pragma: no cover - model_routing lands in Task 7
    def model_for(task_type: str) -> str:
        return "claude-sonnet-4-6"

TIER_INSTRUCTION = {
    "reality": (
        "Verify the SUCCESS path. A genuine implementation must build the real vendor request and "
        "drive it through the real call_external seam, then parse the REAL response (per the success "
        "cassette) into the product's domain object. Refute if the seam is stubbed/Mock()d away, if "
        "the request shape would not be accepted by the real vendor, or if the response is faked."
    ),
    "negative": (
        "Verify the FAILURE path. A genuine implementation must, when the vendor errors / times out / "
        "returns malformed data (per the negative cassette), execute its REAL error path and degrade "
        "honestly — surface the failure, never silently proceed, never corrupt state. Refute if the "
        "error path is mocked away, swallowed, or cannot actually be reached."
    ),
}


def _raw_criterion_block(doc: str, criterion_id: str, base: pathlib.Path | None = None) -> str:
    """Slice the raw YAML block for one criterion out of criteria.yaml (from its `- criterion_id:`
    line up to the next criterion or EOF). Gives the critic the criterion verbatim, no lossy re-emit."""
    base = base or (ROOT / "acceptance" / doc)
    path = base / "criteria" / "criteria.yaml"
    if not path.is_file():
        return f"(criteria.yaml not found for {doc})"
    lines = path.read_text().splitlines()
    start = None
    for i, ln in enumerate(lines):
        if re.match(rf"\s*-\s*criterion_id:\s*{re.escape(criterion_id)}\b", ln):
            start = i
            break
    if start is None:
        return f"(criterion {criterion_id} not found in {doc})"
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"\s*-\s*criterion_id:\s*\S+", lines[j]):
            end = j
            break
    return "\n".join(lines[start:end])


def _cassette_paths(doc: str, criterion: dict, base: pathlib.Path | None = None) -> str:
    base = base or (ROOT / "acceptance" / doc)
    manifest = parse_manifest(base / "dependency_manifest.yaml")
    info = manifest.get(criterion.get("dependency_class", ""), {})
    glob = info.get("cassette", "") if info else ""
    if not glob:
        return "(no cassette glob declared in dependency_manifest.yaml)"
    hits = sorted(str(p.relative_to(ROOT)) for p in ROOT.glob(glob))
    return "\n".join(hits) if hits else f"(glob {glob} — none recorded yet)"


def _parse_verdict(stdout: str) -> tuple[bool, str]:
    """Extract the final CRITIC: CLEAN / CRITIC: REFUTED verdict. Default to refuted if absent."""
    clean = re.search(r"CRITIC:\s*CLEAN\b", stdout)
    refuted = re.search(r"CRITIC:\s*REFUTED:\s*(.*)", stdout)
    # last verdict wins
    if refuted and (not clean or refuted.start() > clean.start()):
        return False, refuted.group(1).strip()[:400]
    if clean:
        return True, "clean"
    return False, "no explicit verdict line — treated as refuted (critic must affirm CLEAN)"


def _real_spawn(doc: str, criterion: dict, tier: str, rnd: int,
                base: pathlib.Path | None = None) -> dict:
    """One fresh-context critic invocation via the hardened run_agent primitive."""
    from orchestrate import run_agent  # lazy: keep agent machinery off the mechanical-tier path
    spec = _spec_for(doc)
    subs = {
        "<DOC>": doc,
        "<SPEC>": spec,
        "<TIER>": tier,
        "<TIER_INSTRUCTION>": TIER_INSTRUCTION.get(tier, ""),
        "<CRITERION_YAML>": _raw_criterion_block(doc, criterion["id"], base),
        "<CASSETTE_PATHS>": _cassette_paths(doc, criterion, base),
    }
    res = run_agent("reality_critic.md", subs, timeout=CRITIC_TIMEOUT, max_turns=CRITIC_MAX_TURNS,
                    model=model_for(f"{tier}-critic"), phase=f"{doc} {tier}-critic {criterion['id']} r{rnd}")
    if getattr(res, "timed_out", False):
        return {"clean": False, "reason": f"critic round {rnd} timed out", "timed_out": True}
    clean, reason = _parse_verdict(getattr(res, "stdout", ""))
    return {"clean": clean, "reason": reason, "round": rnd}


def _spec_for(doc: str) -> str:
    try:
        from orchestrate import DOCS
        return DOCS[doc]["spec"]
    except Exception:
        return f"{doc}.md"


def run_critic(doc: str, criterion: dict, tier: str, *, have_cassette: bool = False,
               spawn=None, max_rounds: int = MAX_ROUNDS, base: pathlib.Path | None = None) -> dict:
    """Judge one criterion's reality/negative tier. Requires TWO CONSECUTIVE clean passes from TWO
    DIFFERENT fresh invocations; caps at `max_rounds`; halts for founder review if not reached.

    Returns {passed: bool, reason: str, rounds: int, consecutive_clean: int,
             halt_for_founder: bool, history: [...]}. `spawn` is injectable for testing."""
    spawn = spawn or (lambda d, c, t, r: _real_spawn(d, c, t, r, base))
    consecutive = 0
    history: list[dict] = []
    for rnd in range(1, max_rounds + 1):
        verdict = spawn(doc, criterion, tier, rnd)   # a NEW fresh session every round
        history.append(verdict)
        if verdict.get("clean"):
            consecutive += 1
            if consecutive >= CONSECUTIVE_CLEAN_REQUIRED:
                return {"passed": True, "reason": f"{consecutive} consecutive clean passes from "
                        f"{consecutive} distinct fresh critics", "rounds": rnd,
                        "consecutive_clean": consecutive, "halt_for_founder": False, "history": history}
        else:
            consecutive = 0   # a single refutation breaks the streak — two must be truly consecutive
    last = history[-1]["reason"] if history else "no rounds ran"
    return {"passed": False, "halt_for_founder": True, "consecutive_clean": consecutive,
            "reason": (f"no {CONSECUTIVE_CLEAN_REQUIRED} consecutive clean critic passes in "
                       f"{max_rounds} rounds — genuine signal (spec problem or real defect), halting "
                       f"for founder review, not looping. Last refutation: {last}"),
            "rounds": max_rounds, "history": history}


def main() -> None:
    import json
    if len(sys.argv) < 4:
        print("usage: ladder_critics.py <doc> <criterion_id> <reality|negative>")
        sys.exit(2)
    doc, cid, tier = sys.argv[1], sys.argv[2], sys.argv[3]
    from ladder_schema_gate import parse_criteria
    crits = {c["id"]: c for c in parse_criteria(ROOT / "acceptance" / doc / "criteria" / "criteria.yaml")}
    if cid not in crits:
        print(f"criterion {cid} not found in {doc}")
        sys.exit(2)
    verdict = run_critic(doc, crits[cid], tier)
    print(json.dumps(verdict, indent=1))
    sys.exit(0 if verdict["passed"] else 1)


if __name__ == "__main__":
    main()
