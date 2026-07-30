"""Whole-meeting e2e SMOKE orchestrator — prime → generate → play → monitor → judge.

The single entry that runs ONE full meeting live on the REAL engine, exactly as the
task frames it: "manual meeting testing, automated, minus the audio". It:

1. PRIMES a real repo (cal.com by default) to a SCRATCH clone outside the git tree,
   builds the pre-meeting map on the subscription (or degrades to prime-only), and
   extracts real repo facts;
2. GENERATES one long, messy, grounded meeting of a given type (~15-25 asks incl. the
   3 nuance triggers + a big coding task + a can't-do) via ONE bounded subscription call;
3. PLAYS it in real time on the REAL engine + intake (barge-in cut + feed_transcript,
   no drain between lines) with the real code server, real E2B (when live), and the
   real drafts tool over a recording stage seam;
4. MONITORS deeply (per-step trace + cost + edges) and JUDGES aggressively
   (deterministic grounding/gate + harsh deepeval + arc read);
5. writes the READABLE markdown trace to ``$CLAUDE_JOB_DIR/tmp/e2e/<meeting>.md``.

Run as a gated pytest node (see ``test_e2e_meeting.py``) so the workspace imports
resolve; or call :func:`run_smoke` directly from such a node.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["SmokeResult", "output_dir", "run_smoke"]


def output_dir() -> Path:
    job_dir = os.environ.get("CLAUDE_JOB_DIR")
    base = Path(job_dir) if job_dir else Path("/tmp")
    d = base / "tmp" / "e2e"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass(slots=True)
class SmokeResult:
    trace_path: Path
    meeting_id: str
    edges: Any
    judgements: list[Any]
    arc: Any
    prime_degraded: bool


async def run_smoke(
    *,
    repo: str = "calcom",
    meeting_type: str = "technical",
    compression: float = 0.04,
    max_wait_s: float = 0.5,
    live_e2b: bool = True,
    build_map: bool = True,
    threshold: float = 0.7,
) -> SmokeResult:
    """Run the whole pipeline for ONE meeting and write the readable trace.

    ``live_e2b`` provisions a REAL E2B sandbox (needs ``E2B_API_KEY``; a provision
    fault surfaces rather than fakes). ``build_map`` attempts the real subscription
    map-build (degrades to prime-only honestly).
    """
    from tests.eval.generate_meetings import generate_meeting
    from tests.eval.judge_meeting import attribute_traces, judge_meeting
    from tests.eval.meeting_monitor import edges_from_traces, render_markdown
    from tests.eval.meeting_player import play_meeting
    from tests.eval.prime_repo import prime_repo

    # 1) PRIME.
    print(f"[smoke] priming {repo} ...")
    primed = await prime_repo(repo, build_map=build_map)
    map_note = (
        f"map built ({len(primed.map_text)} chars, degraded={primed.map_degraded})"
        if primed.map_text else "prime-only (no map; grounding via code server)"
    )
    print(f"[smoke] primed {primed.name} @ {primed.sha[:12]} — {map_note}")

    # 2) GENERATE.
    print(f"[smoke] generating a {meeting_type} meeting ...")
    meeting = await generate_meeting(
        meeting_type=meeting_type,
        repo_facts=primed.facts.brief(),
        repo_name=primed.name,
        repo_sha=primed.sha,
        meeting_id=f"{meeting_type}-{primed.name}",
    )

    # 3) PLAY (real-time, real engine + intake).
    print(f"[smoke] playing {len(meeting.lines)} lines "
          f"(compression={compression}, live_e2b={live_e2b}) ...")
    player = await play_meeting(
        meeting,
        clone_path=primed.clone_path,
        map_text=primed.map_text,
        compression=compression,
        max_wait_s=max_wait_s,
        live_e2b=live_e2b,
    )
    traces = player.traced.traces
    edges = edges_from_traces(traces)
    print(f"[smoke] play done — {edges.summary()} | barge-cuts={player.barge_cuts}")

    # 4) MONITOR + JUDGE.
    ask_traces = attribute_traces(traces, meeting.asks)
    judgements, checks, arc = judge_meeting(
        meeting=meeting,
        ask_traces=ask_traces,
        t_wakes=player.t_wakes,
        clone_path=primed.clone_path,
        transport_calls=player.transport.calls,
        staged_drafts=player.stage.staged,
        threshold=threshold,
    )
    passed = sum(1 for j in judgements if j.passed)
    print(f"[smoke] judged — {passed}/{len(judgements)} asks pass (threshold {threshold}); "
          f"arc {arc.score:.2f}")

    # 5) WRITE the readable trace.
    staged_summaries = [
        {"draft_id": d.draft_id, "kind": d.kind, "summary": d.summary,
         "has_files": bool(d.files), "has_diff": bool(d.unified_diff)}
        for d in player.stage.staged
    ]
    md = render_markdown(
        meeting=meeting,
        monitor=player.monitor,
        traces=traces,
        t_wakes=player.t_wakes,
        ask_traces=ask_traces,
        transport_calls=player.transport.calls,
        staged_drafts=staged_summaries,
        edges=edges,
        scored=judgements,
        prime_note=map_note,
    )
    # Append the deterministic checks + the arc read.
    md += "\n## Deterministic checks\n\n"
    for c in checks:
        line = (f"- [{c.ask_id}] {c.kind}: grounding "
                f"{'clean' if c.grounding_clean else 'UNRESOLVED ' + str(c.unresolved)}"
                f"; gate {'clean' if c.gate_clean else 'LEAK'}")
        if c.citations:
            line += f"; cites {c.resolved}/{len(c.citations)} resolved"
        if c.transport_ok is not None:
            line += f"; transport {'ok' if c.transport_ok else 'MISSING'}"
        if c.staged_draft:
            line += "; staged-draft"
        md += line + "\n"
    md += f"\n## Meeting arc (judge)\n\n{arc.summary}\n"

    out = output_dir() / f"{meeting.id}.md"
    out.write_text(md, encoding="utf-8")
    print(f"[smoke] trace written: {out}")

    return SmokeResult(
        trace_path=out, meeting_id=meeting.id, edges=edges,
        judgements=judgements, arc=arc, prime_degraded=primed.map_degraded,
    )
