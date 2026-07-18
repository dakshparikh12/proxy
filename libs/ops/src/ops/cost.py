"""Meeting cost accounting — two honest meters, one persisted row (A-006).

Two writers (the Scribe's bare Messages calls and the seam-based meter for wakes
+ Workroom) increment the same ``meeting_cost`` row; budget reads RELOAD the
persisted spend so a recycled orchestrator resumes from the accrued total and
never resets to 0.

The in-process :class:`MeetingCost` meter keeps the *listening baseline*
(transport + Scribe + orchestrator-idle) strictly separate from the *task
budget* (Workroom/Opus/E2B). Only the listening subset drives the degrade →
notes-only breaker; substantive task spend is governed solely by the pre-dispatch
estimate gate on :func:`dispatch_workroom` (A-006), never the listening breaker.

The module-level ``record_*``/``check_meeting_budget`` helpers are polymorphic on
their first argument: given a :class:`~libs.db.Database` they return a coroutine
(the async persisted path used by the harness boundary); given a raw psycopg
connection they run synchronously against ``meeting_cost_telemetry`` (the
per-micro-call telemetry read by the observability suite).
"""
from __future__ import annotations

from collections.abc import Coroutine
from dataclasses import dataclass, field
from typing import Any, overload

from libs.db import Database, repos

from .claim import claim_meeting

# Listening-breaker cap basis — fractions of the listening baseline SLA, applied
# to the LISTENING subset only (transport + Scribe + orch-idle), per A-006. Task
# / E2B / Opus spend never trips this breaker.
_LISTENING_SOFT_CAP_USD = 1.5
_LISTENING_HARD_CAP_USD = 3.0

# The listening roles whose spend accrues to the baseline, not the task budget.
_LISTENING_ROLES = frozenset({"scribe", "orchestrator_idle", "orch_idle", "transport"})


@dataclass
class BudgetBreaker:
    """The result of a budget check: the full three-meter sum + listening state."""

    total_usd: float
    listening_baseline_usd: float
    task_budget_usd: float
    listening_state: str  # "normal" | "degrade" | "notes_only"


@dataclass
class DispatchDecision:
    """The pre-dispatch estimate-gate outcome for a Workroom task."""

    dispatched: bool
    action: str  # "dispatch" | "ask_approval"
    estimate_usd: float
    remaining_usd: float


@dataclass
class MeetingCost:
    """A meeting's spend meter — a reloaded snapshot AND a live accumulator.

    Field order preserves the historical positional snapshot constructor
    (``MeetingCost(meeting_id, model_usd, cache_read_usd, cache_creation_usd,
    transport_usd, e2b_usd)``) while the ``accrue_*`` methods let the harness
    accumulate spend live and split it into the two honest meters.
    """

    meeting_id: Any = None
    model_usd: float = 0.0
    cache_read_usd: float = 0.0
    cache_creation_usd: float = 0.0
    transport_usd: float = 0.0
    e2b_usd: float = 0.0
    transport_rate_per_hour: float = 0.0

    # Live split accumulators (never positional — they are derived, not snapshot).
    _listening_model_usd: float = field(default=0.0, init=False)
    _task_model_usd: float = field(default=0.0, init=False)
    _task_budget_remaining_usd: float | None = field(default=None, init=False)

    @property
    def spent_usd(self) -> float:
        return (
            self.model_usd
            + self.cache_read_usd
            + self.cache_creation_usd
            + self.transport_usd
            + self.e2b_usd
        )

    # ── live accrual ──────────────────────────────────────────────────────
    def accrue_transport(self, *, elapsed_hours: float) -> None:
        """Accrue metered transport spend (bot + STT + TTS) — listening subset."""
        self.transport_usd += elapsed_hours * self.transport_rate_per_hour

    def accrue_model(self, *, role: str, usd: float) -> None:
        """Accrue model spend, splitting it into the listening vs task meter by role."""
        self.model_usd += usd
        if role.lower() in _LISTENING_ROLES:
            self._listening_model_usd += usd
        else:
            self._task_model_usd += usd

    def accrue_e2b(self, *, sandbox_seconds: float, rate_per_second: float) -> None:
        """Accrue E2B sandbox runtime — task budget only, never the baseline."""
        self.e2b_usd += sandbox_seconds * rate_per_second

    def set_task_budget(self, *, remaining_usd: float) -> None:
        """Set the remaining task budget the pre-dispatch estimate gate reads."""
        self._task_budget_remaining_usd = remaining_usd

    # ── the two honest meters ─────────────────────────────────────────────
    def listening_baseline_usd(self) -> float:
        """Transport + Scribe + orchestrator-idle — the listening SLA subset."""
        return self.transport_usd + self._listening_model_usd

    def task_budget_usd(self) -> float:
        """Workroom/Opus model spend + E2B runtime — the disclosed task budget."""
        return self._task_model_usd + self.e2b_usd

    def remaining_task_budget_usd(self) -> float | None:
        return self._task_budget_remaining_usd

    def check_meeting_budget(self) -> BudgetBreaker:
        """Return the full three-meter sum; the breaker state reads the listening subset only."""
        baseline = self.listening_baseline_usd()
        if baseline > _LISTENING_HARD_CAP_USD:
            state = "notes_only"
        elif baseline > _LISTENING_SOFT_CAP_USD:
            state = "degrade"
        else:
            state = "normal"
        return BudgetBreaker(
            total_usd=self.spent_usd,
            listening_baseline_usd=baseline,
            task_budget_usd=self.task_budget_usd(),
            listening_state=state,
        )


async def record_model_cost(
    db: Database,
    meeting_id: Any,
    *,
    model_usd: float = 0.0,
    cache_read_usd: float = 0.0,
    cache_creation_usd: float = 0.0,
) -> None:
    """Increment model spend (+ the prompt-cache read/creation split)."""
    async with db.acquire() as conn:
        await repos.cost.record_cost(
            conn,
            meeting_id,
            model_usd=model_usd,
            cache_read_usd=cache_read_usd,
            cache_creation_usd=cache_creation_usd,
        )


_TELEMETRY_DDL = """
CREATE TABLE IF NOT EXISTS meeting_cost_telemetry (
    id BIGSERIAL PRIMARY KEY,
    meeting_id TEXT NOT NULL,
    total_cost_usd NUMERIC NOT NULL,
    cache_read_usd NUMERIC NOT NULL DEFAULT 0,
    cache_creation_usd NUMERIC NOT NULL DEFAULT 0,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def _record_micro_call_telemetry(
    conn: Any,
    *,
    meeting_id: str,
    total_cost_usd: float,
    cache_read_usd: float,
    cache_creation_usd: float,
) -> None:
    """Persist one micro-call's total + cache-read/creation split (sync path)."""
    conn.execute(_TELEMETRY_DDL)
    conn.execute(
        "INSERT INTO meeting_cost_telemetry "
        "(meeting_id, total_cost_usd, cache_read_usd, cache_creation_usd) "
        "VALUES (%s, %s, %s, %s)",
        (meeting_id, total_cost_usd, cache_read_usd, cache_creation_usd),
    )


def record_micro_call_cost(
    target: Any,
    meeting_id: Any = None,
    model_usd: float = 0.0,
    *,
    total_cost_usd: float | None = None,
    cache_read_usd: float = 0.0,
    cache_creation_usd: float = 0.0,
) -> Any:
    """Record seam-metered micro-call spend.

    ``Database`` first arg → the async persisted path (returns a coroutine to
    await); a raw psycopg connection → the synchronous ``meeting_cost_telemetry``
    writer carrying the cache-read/creation split.
    """
    if isinstance(target, Database):
        return record_model_cost(target, meeting_id, model_usd=model_usd)
    _record_micro_call_telemetry(
        target,
        meeting_id=str(meeting_id),
        total_cost_usd=float(total_cost_usd if total_cost_usd is not None else model_usd),
        cache_read_usd=cache_read_usd,
        cache_creation_usd=cache_creation_usd,
    )
    return None


async def _check_meeting_budget_async(db: Database, meeting_id: Any) -> MeetingCost:
    """Reload the persisted spend — never resets to 0 at the recovery moment."""
    async with db.acquire() as conn:
        row = await repos.cost.get_cost(conn, meeting_id)
    if row is None:
        return MeetingCost(meeting_id, 0.0, 0.0, 0.0, 0.0, 0.0)
    return MeetingCost(
        meeting_id=meeting_id,
        model_usd=float(row["model_usd"]),
        cache_read_usd=float(row["cache_read_usd"]),
        cache_creation_usd=float(row["cache_creation_usd"]),
        transport_usd=float(row["transport_usd"]),
        e2b_usd=float(row["e2b_usd"]),
    )


@overload
def check_meeting_budget(
    target: Database, meeting_id: Any = None
) -> Coroutine[Any, Any, MeetingCost]: ...
@overload
def check_meeting_budget(target: Any, meeting_id: Any = None) -> dict[str, float]: ...
def check_meeting_budget(target: Any, meeting_id: Any = None) -> Any:
    """Read a meeting's aggregated spend.

    ``Database`` first arg → a coroutine reloading the persisted ``meeting_cost``
    row; a raw psycopg connection → a synchronous aggregate over
    ``meeting_cost_telemetry`` as ``{"total_cost_usd": <sum>}``.
    """
    if isinstance(target, Database):
        return _check_meeting_budget_async(target, meeting_id)
    cur = target.execute(
        "SELECT COALESCE(SUM(total_cost_usd), 0) FROM meeting_cost_telemetry "
        "WHERE meeting_id = %s",
        (str(meeting_id),),
    )
    total = cur.fetchone()[0]
    return {"total_cost_usd": float(total)}


def dispatch_workroom(
    db: Any = None,
    meeting_id: str | None = None,
    task_id: str | None = None,
    *,
    cost: MeetingCost | None = None,
    estimate_usd: float | None = None,
) -> Any:
    """Dispatch a Workroom task.

    With ``cost``/``estimate_usd`` → the synchronous pre-dispatch estimate gate:
    an estimate over the remaining task budget asks approval instead of
    dispatching (A-006). With a ``Database`` → the async path claiming a
    ``workroom:<id>`` operation_runs row.
    """
    if cost is not None and estimate_usd is not None:
        remaining = cost.remaining_task_budget_usd()
        remaining_val = remaining if remaining is not None else 0.0
        if estimate_usd > remaining_val:
            return DispatchDecision(
                dispatched=False,
                action="ask_approval",
                estimate_usd=estimate_usd,
                remaining_usd=remaining_val,
            )
        return DispatchDecision(
            dispatched=True,
            action="dispatch",
            estimate_usd=estimate_usd,
            remaining_usd=remaining_val,
        )
    return claim_meeting(db, str(meeting_id), f"workroom:{task_id}")
