"""Deep step monitor for the whole-meeting harness — extends ``plan_trace`` for the
full pipeline picture + a human-readable markdown trace.

``plan_trace.TracingProvider`` already tees every provider turn with wall-clock marks
and captures ``result_meta`` (where ``total_cost_usd`` + ``num_turns`` live). This
module adds the meeting-level layer the whole-meeting smoke needs:

* a WALL-CLOCK event log spanning the whole run (transcription events in; the
  pre-meeting index build; meeting start/consent; barge-in cuts) — the things that
  happen OUTSIDE a provider turn, recorded via :class:`MeetingMonitor`;
* per-turn COST surfaced off ``result_meta`` (``total_cost_usd``) — visible, never
  hidden;
* an ORDERED per-step tool log (name + input + result + per-step timing) — already
  the ``TurnTrace.events`` stream, rendered readably;
* which REAL EDGES fired — Claude (any turn ran), E2B/sandbox (``mcp__sandbox__*``
  called), code-server (``mcp__code_intel__*``), drafts (``mcp__drafts__*``) — derived
  from the tool sequences + the recording seams;
* turn→ask attribution via the same ``rsplit("You were addressed:")`` key the batteries
  use, so each ask's turn(s) are pulled out and rendered together.

Everything here is pure observation over recorded ground truth — it changes no engine
behavior. ``render_markdown`` produces the readable per-meeting trace a human eyeballs.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from tests.eval.plan_trace import (
    TracingProvider,
    TurnTrace,
    derive_metrics,
    render_trace,
)

__all__ = [
    "EdgeReport",
    "MeetingEvent",
    "MeetingMonitor",
    "TurnCost",
    "edges_from_traces",
    "render_markdown",
    "turn_cost",
]


# ── Meeting-level wall-clock event log (outside-a-turn things) ────────────────


@dataclass(frozen=True, slots=True)
class MeetingEvent:
    """One meeting-level event stamped with wall-clock seconds from monitor start."""

    t: float
    kind: str          # e.g. "prime", "meeting-start", "consent", "transcript-in",
    detail: str        #       "barge-in-cut", "ask-landed", "ask-drained"


class MeetingMonitor:
    """Records meeting-level events with wall-clock stamps (the run's spine).

    ``t0`` is set at construction; every :meth:`mark` records ``time.perf_counter()``
    relative to it, so the markdown trace shows one coherent timeline for the whole
    meeting alongside the per-turn traces from the :class:`TracingProvider`.
    """

    def __init__(self) -> None:
        self.t0 = time.perf_counter()
        self.events: list[MeetingEvent] = []

    def mark(self, kind: str, detail: str = "") -> float:
        now = time.perf_counter() - self.t0
        self.events.append(MeetingEvent(t=now, kind=kind, detail=detail))
        return now

    def wall(self) -> float:
        return time.perf_counter() - self.t0


# ── Per-turn cost (surfaced off result_meta) ──────────────────────────────────


@dataclass(frozen=True, slots=True)
class TurnCost:
    """The cost/turns facts for one provider turn (``None`` = the SDK didn't report)."""

    cost_usd: float | None
    sdk_num_turns: int | None


def turn_cost(trace: TurnTrace) -> TurnCost:
    """Pull ``total_cost_usd`` + ``num_turns`` off the turn's RESULT metadata."""
    meta = trace.result_meta or {}
    cost = meta.get("total_cost_usd")
    turns = meta.get("num_turns")
    return TurnCost(
        cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
        sdk_num_turns=int(turns) if isinstance(turns, int) else None,
    )


# ── Which real edges fired ────────────────────────────────────────────────────


@dataclass(slots=True)
class EdgeReport:
    """Which of the four real edges verifiably fired across the whole meeting."""

    claude: bool = False        # any provider turn ran (streamed real chunks)
    code_server: bool = False   # a mcp__code_intel__* tool was called
    sandbox_e2b: bool = False   # a mcp__sandbox__* tool was called
    drafts: bool = False        # a mcp__drafts__* tool was called
    code_calls: int = 0
    sandbox_calls: int = 0
    draft_calls: int = 0
    turn_count: int = 0

    def summary(self) -> str:
        def flag(ok: bool) -> str:
            return "FIRED" if ok else "not fired"
        return (
            f"Claude: {flag(self.claude)} ({self.turn_count} turns) | "
            f"code-server: {flag(self.code_server)} ({self.code_calls} calls) | "
            f"E2B/sandbox: {flag(self.sandbox_e2b)} ({self.sandbox_calls} calls) | "
            f"drafts: {flag(self.drafts)} ({self.draft_calls} calls)"
        )


def edges_from_traces(traces: list[TurnTrace]) -> EdgeReport:
    """Derive the edge report from the captured provider traces (ground truth)."""
    rep = EdgeReport()
    rep.turn_count = len(traces)
    for trace in traces:
        if trace.events:
            rep.claude = True
        for ev in trace.events:
            if ev.kind != "TOOL_USE":
                continue
            name = ev.name
            if name.startswith("mcp__code_intel__"):
                rep.code_server = True
                rep.code_calls += 1
            elif name.startswith("mcp__sandbox__"):
                rep.sandbox_e2b = True
                rep.sandbox_calls += 1
            elif name.startswith("mcp__drafts__"):
                rep.drafts = True
                rep.draft_calls += 1
    return rep


# ── The readable markdown trace ───────────────────────────────────────────────


def _fmt_cost(c: TurnCost) -> str:
    parts = []
    if c.cost_usd is not None:
        parts.append(f"${c.cost_usd:.4f}")
    if c.sdk_num_turns is not None:
        parts.append(f"{c.sdk_num_turns} sdk-turns")
    return " · ".join(parts) if parts else "(no cost metadata)"


def render_markdown(
    *,
    meeting: Any,
    monitor: MeetingMonitor,
    traces: list[TurnTrace],
    t_wakes: dict[str, float],
    ask_traces: dict[str, list[TurnTrace]],
    transport_calls: list[tuple[str, dict[str, Any]]],
    staged_drafts: list[dict[str, Any]],
    edges: EdgeReport,
    scored: list[Any] | None = None,
    prime_note: str = "",
) -> str:
    """The human-eyeball per-meeting markdown trace.

    ``meeting`` is a ``GeneratedMeeting``; ``t_wakes``/``ask_traces`` key by ask id;
    ``scored`` is the optional list of judged asks (``ScoredAsk``-shaped: ``.ask_id``
    or ``.result.ask_id``, ``.score``, ``.reason``, ``.passed``). Every rendered value
    is ground truth from the recorded seams — never model-claimed.
    """
    out: list[str] = []
    out.append(f"# Whole-meeting e2e trace — {meeting.id}")
    out.append("")
    out.append(f"- Meeting type: **{meeting.meeting_type}** — {meeting.title}")
    out.append(f"- Repo: `{meeting.repo_name}` @ `{meeting.repo_sha}`")
    out.append(f"- Participants: {', '.join(meeting.participants)}")
    out.append(f"- Transcript: {len(meeting.lines)} lines · {len(meeting.asks)} planted asks")
    if prime_note:
        out.append(f"- Priming: {prime_note}")
    out.append("")
    out.append("## Real edges")
    out.append("")
    out.append(f"> {edges.summary()}")
    out.append("")

    # Cost roll-up.
    costs = [turn_cost(t) for t in traces]
    total_cost = sum(c.cost_usd for c in costs if c.cost_usd is not None)
    reported = [c for c in costs if c.cost_usd is not None]
    out.append("## Cost")
    out.append("")
    out.append(
        f"- {len(reported)}/{len(traces)} turns reported cost · "
        f"total **${total_cost:.4f}** across the meeting"
    )
    out.append("")

    # The meeting-level wall-clock spine.
    out.append("## Meeting timeline (wall-clock, s from run start)")
    out.append("")
    out.append("```")
    for ev in monitor.events:
        out.append(f"+{ev.t:7.2f}s  {ev.kind:<16} {ev.detail}")
    out.append("```")
    out.append("")

    # Per-ask deep dive.
    scored_by_id: dict[str, Any] = {}
    for s in scored or []:
        sid = getattr(s, "ask_id", None) or getattr(getattr(s, "result", None), "ask_id", None)
        if sid:
            scored_by_id[str(sid)] = s

    out.append("## Per-ask deep dive")
    out.append("")
    for ask in meeting.asks:
        out.append(f"### [{ask.id}] {ask.kind}"
                   + (f" · nuance:{ask.nuance}" if ask.nuance else "")
                   + (" · CAN'T-DO" if ask.cant_do else ""))
        out.append("")
        out.append(f"- **Ask** (@ts {ask.ts:.0f}, {ask.speaker}): {ask.ask}")
        out.append(f"- **Gold (judge-only)**: {ask.gold}")
        if ask.require_transport:
            out.append(f"- Requires transport: {', '.join(ask.require_transport)}")
        traces_for = ask_traces.get(ask.id, [])
        t_wake = t_wakes.get(ask.id)
        if not traces_for:
            out.append("- **Woke?** NO turn attributed to this ask "
                       "(it never woke, or attribution missed).")
            out.append("")
            continue
        out.append(f"- **Woke?** yes — {len(traces_for)} turn(s) attributed")
        for ti, trace in enumerate(traces_for):
            base = t_wake if t_wake is not None else trace.t_start
            metrics = derive_metrics(trace, t_wake=base)
            cost = turn_cost(trace)
            out.append("")
            out.append(f"  Turn {ti + 1} — {_fmt_cost(cost)}")
            ack = (f"{metrics.ack_latency_s:.2f}s"
                   if metrics.ack_latency_s is not None else "never spoke")
            out.append(f"  - ack latency: {ack}")
            out.append(f"  - complete latency: {metrics.complete_latency_s:.2f}s · "
                       f"tools: {metrics.tool_count} (overhead {metrics.overhead_calls}, "
                       f"redundant {metrics.redundant_calls})")
            out.append(f"  - tool sequence: {', '.join(metrics.tool_sequence) or '(none)'}")
            spoken = " ".join(trace.response_text.split())
            out.append(f"  - **spoken**: {spoken[:600] or '(no spoken text)'}")
            if metrics.error:
                out.append(f"  - **ERROR**: {metrics.error}")
            out.append("")
            out.append("  ```")
            for ln in render_trace(trace, t_wake=base, max_events=50).splitlines():
                out.append("  " + ln)
            out.append("  ```")
        if ask.id in scored_by_id:
            s = scored_by_id[ask.id]
            verdict = "PASS" if getattr(s, "passed", False) else "FAIL"
            out.append("")
            out.append(f"- **Judge**: {verdict} ({getattr(s, 'score', 0.0):.2f}) — "
                       f"{getattr(s, 'reason', '')}")
        out.append("")

    # Transport + drafts ground truth.
    out.append("## Meeting-control verbs recorded (fake transport, real handler path)")
    out.append("")
    if transport_calls:
        out.append("```")
        for verb, payload in transport_calls:
            body = json.dumps(payload, default=str)
            out.append(f"{verb}: {body[:300]}")
        out.append("```")
    else:
        out.append("_(none recorded)_")
    out.append("")
    out.append("## Drafts staged (recording stage seam — DB persistence faked)")
    out.append("")
    if staged_drafts:
        out.append("```")
        for d in staged_drafts:
            out.append(json.dumps(d, default=str)[:400])
        out.append("```")
    else:
        out.append("_(none staged)_")
    out.append("")
    return "\n".join(out)


# Re-export the tee so callers import one module.
Tracing = TracingProvider
