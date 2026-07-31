"""Plan-trace + latency capture for the PLAN-QUALITY battery (pure observation).

The founder's bar: *look at the PLAN the orchestrator creates for every ask* —
which tools, in what order, with what inputs, and how fast the room hears
something. This module is the measurement half: a transparent tee around ANY
``agentkit.Provider`` (the proven ``ObservingProvider`` pattern from
``tests/eval/meeting_battery.py``, extended with WALL-CLOCK marks per chunk)
plus the pure derivations over one captured turn:

* the ordered TOOL_USE events (name + input) and TEXT deltas, each stamped with
  ``time.perf_counter()`` at arrival;
* the latency marks — t_wake→first-TEXT (ack latency), t_wake→first-tool,
  per-tool gaps, t_wake→turn-complete;
* the plan facts — tool_count, redundant-call count (the same tool called with
  the same input twice), ack-before-work (first spoken TEXT lands before any
  TOOL_RESULT does).

Nothing here changes engine behavior: chunks pass through unchanged, and every
derivation is a pure function over the recorded trace (unit-tested offline with
synthetic timelines in ``tests/eval/test_plan_quality.py``).

``LATENCY_BOUNDS`` is the deterministic per-ask-class assertion table for the
live tier — GENEROUS but REAL bounds, each documented on its entry. They bound
the *measurement machinery's* honesty bar, not the optimum: the first full run's
analysis is what drives the agentic (prompt/context/access) improvements.
"""
from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "LATENCY_BOUNDS",
    "PLATFORM_OVERHEAD_TOOLS",
    "ClassBounds",
    "TraceEvent",
    "TracingProvider",
    "TurnMetrics",
    "TurnTrace",
    "check_bounds",
    "derive_metrics",
    "render_trace",
]

#: Tool names that are PLATFORM overhead, not plan steps Proxy chose: the SDK
#: defers MCP tool schemas, so nearly every live turn opens with one
#: ``ToolSearch`` load call (observed live run 1, 2026-07-29 — it rode every
#: class). Its latency and count stay fully visible in the metrics (real cost,
#: reported as an improvement candidate); it is excluded ONLY from the
#: plan-cost bound comparison and from the ack-before-work "work"
#: classification, so those assertions grade Proxy's CHOSEN plan, not the
#: harness's tool-loading tax.
PLATFORM_OVERHEAD_TOOLS: frozenset[str] = frozenset({"ToolSearch"})


# ── The captured trace ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TraceEvent:
    """One observed chunk, stamped at arrival.

    ``kind`` is the chunk discriminator (``TEXT``/``TOOL_USE``/``TOOL_RESULT``/
    ``RESULT``/``ERROR``); ``t`` is the absolute ``time.perf_counter()`` second
    at which it arrived (callers subtract their own t_wake). ``TEXT`` events
    carry the NEW spoken delta (never the accumulated text twice); ``TOOL_USE``
    carries the real observed name + input — ground truth, never model-claimed.
    """

    kind: str
    t: float
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    text: str = ""
    is_error: bool = False
    #: TOOL_USE: the SDK call id; TOOL_RESULT: its ``tool_use_id`` — the
    #: correlation key that lets ack-before-work ignore overhead-tool results.
    call_id: str = ""


@dataclass
class TurnTrace:
    """Everything ONE provider turn actually did, in arrival order with times."""

    prompt: str
    t_start: float
    t_end: float | None = None
    events: list[TraceEvent] = field(default_factory=list)
    result_text: str = ""
    #: RESULT metadata (num_turns, total_cost_usd, ...) when the terminal chunk landed.
    result_meta: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    _texts: dict[str, str] = field(default_factory=dict)
    _msg_order: list[str] = field(default_factory=list)

    @property
    def addressed(self) -> str:
        """The volatile ask suffix of the turn prompt — the exact attribution key
        (the recent-notes block above it may CONTAIN other asks' lines)."""
        return self.prompt.rsplit("You were addressed:", 1)[-1]

    @property
    def response_text(self) -> str:
        """The turn's spoken text (accumulated per msg_id), result text fallback."""
        spoken = "\n".join(self._texts[m] for m in self._msg_order if self._texts[m].strip())
        return spoken or self.result_text

    def note_text(self, msg_id: str, accumulated: str) -> str:
        """Record the accumulated text for ``msg_id``; return the NEW delta."""
        prior = self._texts.get(msg_id, "")
        if msg_id not in self._texts:
            self._msg_order.append(msg_id)
        self._texts[msg_id] = accumulated
        return accumulated[len(prior):]


class TracingProvider:
    """A transparent tee around ANY ``agentkit.Provider``: chunks pass through
    unchanged; every turn lands in :attr:`traces` (call order) with wall-clock
    marks per event. The timing extension of the battery's ``ObservingProvider``."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.traces: list[TurnTrace] = []

    async def stream(self, prompt: str, query: Any) -> AsyncIterator[Any]:
        trace = TurnTrace(prompt=prompt, t_start=time.perf_counter())
        self.traces.append(trace)
        try:
            async for chunk in self._inner.stream(prompt, query):
                now = time.perf_counter()
                ctype = str(getattr(chunk, "type", ""))
                meta = getattr(chunk, "metadata", {}) or {}
                if ctype == "TEXT":
                    delta = trace.note_text(
                        str(meta.get("msg_id", "")), getattr(chunk, "text", "") or ""
                    )
                    if delta:
                        trace.events.append(TraceEvent(kind="TEXT", t=now, text=delta))
                elif ctype == "TOOL_USE":
                    trace.events.append(
                        TraceEvent(
                            kind="TOOL_USE",
                            t=now,
                            name=str(meta.get("name", "")),
                            input=dict(meta.get("input", {}) or {}),
                            call_id=str(meta.get("id", "")),
                        )
                    )
                elif ctype == "TOOL_RESULT":
                    trace.events.append(
                        TraceEvent(
                            kind="TOOL_RESULT",
                            t=now,
                            is_error=bool(meta.get("is_error", False)),
                            call_id=str(meta.get("tool_use_id", "")),
                        )
                    )
                elif ctype == "RESULT":
                    trace.result_text = getattr(chunk, "text", "") or ""
                    trace.result_meta = dict(meta)
                    trace.events.append(TraceEvent(kind="RESULT", t=now))
                elif ctype == "ERROR":
                    if trace.error is None:
                        trace.error = str(meta.get("message", "")) or "provider error"
                    trace.events.append(
                        TraceEvent(kind="ERROR", t=now, text=trace.error or "", is_error=True)
                    )
                yield chunk
        except Exception as exc:
            if trace.error is None:
                trace.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            trace.t_end = time.perf_counter()


# ── Pure derivations (unit-tested against synthetic timelines) ────────────────


@dataclass(frozen=True, slots=True)
class TurnMetrics:
    """The derived plan/latency facts for one turn, relative to the ask's t_wake.

    ``None`` marks mean "never happened" (never spoke / never called a tool) —
    honest absences, never zero-filled.
    """

    ack_latency_s: float | None
    first_tool_latency_s: float | None
    complete_latency_s: float
    tool_gaps_s: tuple[float, ...]
    tool_count: int
    #: How many of ``tool_count`` are PLATFORM overhead (``PLATFORM_OVERHEAD_TOOLS``)
    #: — visible cost, excluded from the plan-cost bound and the "work" in
    #: ack-before-work.
    overhead_calls: int
    redundant_calls: int
    ack_before_work: bool
    spoke: bool
    tool_sequence: tuple[str, ...]
    sdk_num_turns: int | None
    error: str | None


def canonical_call(name: str, tool_input: dict[str, Any]) -> str:
    """The redundancy key: tool name + its input, canonically serialized."""
    return f"{name}:{json.dumps(tool_input, sort_keys=True, default=str)}"


def derive_metrics(trace: TurnTrace, *, t_wake: float) -> TurnMetrics:
    """Derive one turn's plan/latency metrics from its captured trace.

    * ``ack_latency_s`` — t_wake → the first spoken TEXT delta.
    * ``first_tool_latency_s`` — t_wake → the first TOOL_USE.
    * ``complete_latency_s`` — t_wake → the stream's end (``t_end``; the last
      event's stamp when the stream never closed cleanly).
    * ``tool_gaps_s`` — the wall-clock gaps between consecutive TOOL_USE events.
    * ``redundant_calls`` — TOOL_USE events whose (name, input) was already seen
      in this turn (the same lookup done twice is a plan defect).
    * ``ack_before_work`` — the first spoken TEXT landed before the first
      SUBSTANTIVE TOOL_RESULT did (results of ``PLATFORM_OVERHEAD_TOOLS`` calls
      are not "work"; an unattributable result is treated as substantive,
      conservatively). True too when the turn spoke and no substantive tool
      result ever landed; False when the turn never spoke.
    """
    first_text: float | None = None
    first_tool: float | None = None
    first_tool_result: float | None = None
    tool_times: list[float] = []
    seen_calls: set[str] = set()
    redundant = 0
    overhead = 0
    overhead_ids: set[str] = set()
    sequence: list[str] = []
    for event in trace.events:
        if event.kind == "TEXT" and first_text is None:
            first_text = event.t
        elif event.kind == "TOOL_USE":
            if first_tool is None:
                first_tool = event.t
            tool_times.append(event.t)
            sequence.append(event.name)
            if event.name in PLATFORM_OVERHEAD_TOOLS:
                overhead += 1
                if event.call_id:
                    overhead_ids.add(event.call_id)
            key = canonical_call(event.name, event.input)
            if key in seen_calls:
                redundant += 1
            seen_calls.add(key)
        elif event.kind == "TOOL_RESULT" and first_tool_result is None:
            if event.call_id and event.call_id in overhead_ids:
                continue  # a tool-loading result is not "work"
            first_tool_result = event.t
    end = trace.t_end
    if end is None:
        end = trace.events[-1].t if trace.events else trace.t_start
    spoke = first_text is not None
    if not spoke:
        ack_before_work = False
    elif first_tool_result is None:
        ack_before_work = True
    else:
        assert first_text is not None
        ack_before_work = first_text < first_tool_result
    num_turns_raw = trace.result_meta.get("num_turns")
    return TurnMetrics(
        ack_latency_s=(first_text - t_wake) if first_text is not None else None,
        first_tool_latency_s=(first_tool - t_wake) if first_tool is not None else None,
        complete_latency_s=end - t_wake,
        tool_gaps_s=tuple(b - a for a, b in zip(tool_times, tool_times[1:])),
        tool_count=len(tool_times),
        overhead_calls=overhead,
        redundant_calls=redundant,
        ack_before_work=ack_before_work,
        spoke=spoke,
        tool_sequence=tuple(sequence),
        sdk_num_turns=int(num_turns_raw) if isinstance(num_turns_raw, int) else None,
        error=trace.error,
    )


def render_trace(trace: TurnTrace, *, t_wake: float, max_events: int = 60) -> str:
    """A compact, human/judge-readable rendering of one turn's plan trace.

    Every line is ground truth from the tee — never model-claimed. Inputs are
    canonically serialized and truncated; TEXT deltas are shown as short spans.
    """
    lines: list[str] = []
    for event in trace.events[:max_events]:
        mark = f"+{event.t - t_wake:6.2f}s"
        if event.kind == "TOOL_USE":
            raw = json.dumps(event.input, sort_keys=True, default=str)
            lines.append(f"{mark} TOOL_USE   {event.name} {raw[:160]}")
        elif event.kind == "TEXT":
            span = " ".join(event.text.split())
            lines.append(f"{mark} TEXT       {span[:100]!r}")
        elif event.kind == "TOOL_RESULT":
            flag = " (is_error)" if event.is_error else ""
            lines.append(f"{mark} TOOL_RESULT{flag}")
        elif event.kind == "RESULT":
            lines.append(f"{mark} RESULT     (turn complete)")
        elif event.kind == "ERROR":
            lines.append(f"{mark} ERROR      {event.text[:160]}")
    if len(trace.events) > max_events:
        lines.append(f"... ({len(trace.events) - max_events} more events)")
    return "\n".join(lines)


# ── The deterministic per-class latency/plan bounds (live-tier assertions) ────


@dataclass(frozen=True, slots=True)
class ClassBounds:
    """The deterministic assertion bounds for one ask class (documented, generous, real).

    ``ack_s`` bounds t_wake→first spoken TEXT; ``complete_s`` bounds t_wake→
    turn-complete; ``max_tool_calls``/``max_redundant`` bound the plan's cost;
    ``require_ack_before_work`` asserts the product prompt's ack-first mandate
    ("your first words are always the acknowledgment") where the class makes it
    deterministic. ``note`` documents WHY each bound sits where it does.
    """

    ack_s: float
    complete_s: float
    max_tool_calls: int
    max_redundant: int
    require_ack_before_work: bool
    note: str


#: Every bound is GENEROUS but REAL: the SDK subprocess spawn alone costs ~2-5s
#: on the subscription CLI path, first tokens a few seconds more — so ack bounds
#: sit at 20-30s (an ack slower than that is a product defect at any temperature),
#: and completion bounds scale with the class's legitimate work. Redundancy and
#: tool-count ceilings encode "minimal-sufficient plan" as hard numbers.
LATENCY_BOUNDS: dict[str, ClassBounds] = {
    "quick-answer": ClassBounds(
        ack_s=20.0,
        complete_s=75.0,
        max_tool_calls=2,
        max_redundant=0,
        require_ack_before_work=True,
        note=(
            "A quick question gets a quick answer: little-to-no tooling (<=2 calls "
            "tolerates one defensive lookup), zero redundancy, ack strictly before "
            "any tool result. 20s ack / 75s complete cover CLI spawn + one lookup."
        ),
    ),
    "grounded-lookup": ClassBounds(
        ack_s=25.0,
        complete_s=120.0,
        max_tool_calls=6,
        max_redundant=1,
        require_ack_before_work=True,
        note=(
            "One grounded fact from the clone: grep + a bounded read (+ retries on "
            "a miss) is <=6 calls; one redundant call tolerated for a re-grep after "
            "a read; ack first, 2 minutes end-to-end is generous for one fact."
        ),
    ),
    "meeting-control": ClassBounds(
        ack_s=25.0,
        complete_s=90.0,
        max_tool_calls=3,
        max_redundant=0,
        require_ack_before_work=False,
        note=(
            "Mute/post-chat: the verb itself (+ at most a lookup for a posted note) "
            "is <=3 calls, never redundant. ack-first NOT asserted: executing the "
            "verb before the short spoken ack is compliant behavior for control asks."
        ),
    ),
    "sandbox-exec": ClassBounds(
        ack_s=30.0,
        complete_s=240.0,
        max_tool_calls=8,
        max_redundant=2,
        require_ack_before_work=True,
        note=(
            "Run-it-and-tell-me: read the code + execute in E2B (+ a retry) is <=8 "
            "calls; sandbox roundtrips justify 4 minutes; ack must land before any "
            "tool result — the room hears 'on it' while the run happens."
        ),
    ),
    "research-style": ClassBounds(
        ack_s=30.0,
        complete_s=300.0,
        max_tool_calls=14,
        max_redundant=2,
        require_ack_before_work=True,
        note=(
            "A multi-file walk-through: several greps + batch reads across the "
            "request path is <=14 calls with <=2 redundant; 5 minutes bounds a "
            "focused research pass (heavier work belongs in a background worker)."
        ),
    ),
    "clarify": ClassBounds(
        ack_s=25.0,
        complete_s=90.0,
        max_tool_calls=4,
        max_redundant=1,
        require_ack_before_work=True,
        note=(
            "The FIRST turn of an ambiguous ask: surface the fork (ask which, or "
            "name every candidate) — a couple of lookups at most to name the "
            "candidates precisely; the fork must be heard fast (90s)."
        ),
    ),
    "concurrent": ClassBounds(
        ack_s=150.0,
        complete_s=240.0,
        max_tool_calls=8,
        max_redundant=2,
        require_ack_before_work=False,
        note=(
            "Two asks back-to-back, one mouth: the second turn's first spoken delta "
            "PARKS on the speak lock until the first finishes (by design), so ack "
            "covers a full first turn (150s) and ack-before-work is nondeterministic "
            "for the parked turn — not asserted. Bounds apply to EACH ask's turn."
        ),
    ),
    "reconnect": ClassBounds(
        ack_s=25.0,
        complete_s=90.0,
        max_tool_calls=4,
        max_redundant=1,
        require_ack_before_work=True,
        note=(
            "An ask referencing discussion Proxy verifiably missed (a gap in its "
            "notes): the honest answer names the gap — little tooling, fast, no "
            "fabricated recall."
        ),
    ),
    "cant-do": ClassBounds(
        ack_s=20.0,
        complete_s=75.0,
        max_tool_calls=2,
        max_redundant=0,
        require_ack_before_work=True,
        note=(
            "An honest decline (prod restarts, deploys, prod metrics): the first "
            "words ARE the decline (the prompt forbids 'on it' before a gate), so "
            "speech precedes any tool result; near-zero tooling, fast."
        ),
    ),
    "multi-step-build": ClassBounds(
        ack_s=30.0,
        complete_s=300.0,
        max_tool_calls=14,
        max_redundant=2,
        require_ack_before_work=True,
        note=(
            "Sketch-a-change asks: read the relevant files, compose a concrete plan "
            "aloud — <=14 calls, 5 minutes. NO write access is mounted, so any "
            "claimed application of the change is a judge-visible failure."
        ),
    ),
    # TODO(DRAFT-TOOL): the world-touching PR-draft class is SCAFFOLDED ONLY —
    # ``in_meeting`` exposes no DRAFT_TOOLS yet (verified 2026-07-29: no draft
    # module/tool names in services/in-meeting/src/in_meeting). When the DRAFT-TOOL
    # task lands its staged-draft toolbelt, mint pr-draft scenarios (generator
    # class spec is already scaffolded) and judge draft-gate compliance
    # mechanically (a draft staged, NOTHING world-touching executed directly).
    "pr-draft": ClassBounds(
        ack_s=30.0,
        complete_s=300.0,
        max_tool_calls=12,
        max_redundant=2,
        require_ack_before_work=False,
        note=(
            "SCAFFOLD (see TODO above): world-touching change asks must stage a "
            "draft behind a human click, never act directly. Bounds provisional "
            "until DRAFT_TOOLS exist."
        ),
    ),
}


def check_bounds(metrics: TurnMetrics, ask_class: str) -> list[str]:
    """The deterministic per-class violations for one turn (empty = clean).

    Every message names the observed value AND the bound, so a violation reads
    as evidence on its own.
    """
    bounds = LATENCY_BOUNDS.get(ask_class)
    if bounds is None:
        return [f"unknown ask class {ask_class!r} — no bounds table entry"]
    violations: list[str] = []
    if metrics.error is not None:
        violations.append(f"turn errored: {metrics.error}")
    if metrics.ack_latency_s is None:
        violations.append("never spoke: no TEXT delta reached the room")
    elif metrics.ack_latency_s > bounds.ack_s:
        violations.append(
            f"ack latency {metrics.ack_latency_s:.2f}s > {bounds.ack_s:.0f}s bound"
        )
    if metrics.complete_latency_s > bounds.complete_s:
        violations.append(
            f"turn-complete {metrics.complete_latency_s:.2f}s > {bounds.complete_s:.0f}s bound"
        )
    chosen_calls = metrics.tool_count - metrics.overhead_calls
    if chosen_calls > bounds.max_tool_calls:
        violations.append(
            f"{chosen_calls} chosen tool calls (+{metrics.overhead_calls} platform overhead) "
            f"> {bounds.max_tool_calls} bound (sequence: {', '.join(metrics.tool_sequence)})"
        )
    if metrics.redundant_calls > bounds.max_redundant:
        violations.append(
            f"{metrics.redundant_calls} redundant calls (same tool+input twice) "
            f"> {bounds.max_redundant} bound"
        )
    if bounds.require_ack_before_work and metrics.spoke and not metrics.ack_before_work:
        violations.append(
            "ack-after-work: the first TOOL_RESULT landed before the first spoken TEXT"
        )
    return violations
