"""``scribe/rolling_summary.py`` — regenerate cached Segment B + the cadence trigger.

Segment B is **not** the full growing ledger (that would bust the cache every
window and grow unbounded) — it is a compact rolling summary regenerated on a
**cadence** by its own cheap Haiku call, so between refreshes the prefix is
byte-stable and every micro-call hits the cache (§3.2).

Two responsibilities:

* :func:`regenerate_rolling_summary` — one ``messages.create`` (same ``PROXY_MODEL_SCRIBE``
  seat as the Scribe) that reads the *current notes object* (never the raw
  transcript) and rewrites the compact summary. Routed through the single
  ``libs.http.call_external`` seam like every external call.
* :func:`rolling_summary_due` — the cadence trigger: fire when **either** ``N≈20``
  note-deltas have applied (AC-SCRIBE-05) **or** ``≈90s`` have elapsed since the
  last refresh (AC-SCRIBE-06), whichever comes first. On refresh the counter and
  the clock both reset (:meth:`SummaryState.mark_refreshed`).

The refresh runs **off the hot path** — it never blocks the serial Scribe consumer
(AC-SCRIBE-07). :func:`maybe_refresh_in_background` schedules it as a fire-and-forget
task so window[N+1] proceeds without waiting for the (in-flight) refresh call.

The cadence thresholds are **physics** (Law 4: code owns physics, not judgment):
tunable via ``PROXY_ROLLING_SUMMARY_EVERY_N_DELTAS`` / ``PROXY_ROLLING_SUMMARY_MAX_AGE_S``
with the §3.2 defaults ``N=20`` / ``90s``.
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from libs.llm.src.llm.routing import model_for

T = TypeVar("T")

_SCRIBE_SEAT = "SCRIBE"  # same cheap Haiku seat as the Scribe (§3.2)
_SERVICE = "anthropic"
SUMMARY_MAX_TOKENS: int = 400

# --- Cadence thresholds (physics, §3.2). Defaults N=20 deltas / 90s. -----------
DEFAULT_ROLLING_SUMMARY_EVERY_N_DELTAS: int = 20
DEFAULT_ROLLING_SUMMARY_MAX_AGE_S: float = 90.0


def rolling_summary_every_n_deltas() -> int:
    """Delta-count threshold N — env override or the §3.2 default of 20."""
    raw = os.environ.get("PROXY_ROLLING_SUMMARY_EVERY_N_DELTAS")
    return int(raw) if raw is not None else DEFAULT_ROLLING_SUMMARY_EVERY_N_DELTAS


def rolling_summary_max_age_s() -> float:
    """Elapsed-time threshold (seconds) — env override or the §3.2 default of 90."""
    raw = os.environ.get("PROXY_ROLLING_SUMMARY_MAX_AGE_S")
    return float(raw) if raw is not None else DEFAULT_ROLLING_SUMMARY_MAX_AGE_S


# The rolling-summary generator prompt (§3.2). Fixed, version-controlled text.
ROLLING_SUMMARY_PROMPT: str = (
    "You maintain a running summary of a live meeting for a note-taking system. "
    "Given the current structured notes (topics, open decisions, unresolved questions, "
    "active action items, the current goal/blocker), write a compact plain-text summary "
    "(<= ~250 tokens) of the meeting SO FAR that gives a reader enough context to interpret "
    "the next few sentences. Cover what is being decided and what is still open. "
    "Order entries by their stable id; do NOT include timestamps, wall-clock, or counts. "
    "Prose only — no preamble, no headers."
)


class CallExternal(Protocol):
    """Structural type of ``libs.http.call_external`` — the sole external-call seam."""

    async def __call__(
        self,
        op: Callable[[], Awaitable[T]],
        *,
        service: str,
        unit_cost_usd: float = 0.0,
    ) -> Any: ...


@dataclass
class SummaryState:
    """Per-meeting cadence state: deltas applied and time since the last refresh.

    ``deltas_since`` counts note-deltas applied since the last refresh; ``last_refresh_s``
    is the monotonic timestamp of that refresh. Both reset on :meth:`mark_refreshed`.
    """

    last_refresh_s: float = 0.0
    deltas_since: int = 0

    def note_delta_applied(self, n: int = 1) -> None:
        """Record ``n`` applied note-deltas (drives the delta-count trigger)."""
        self.deltas_since += n

    def mark_refreshed(self, now_s: float) -> None:
        """Reset the cadence after a refresh fires: counter to 0, clock to ``now_s``."""
        self.deltas_since = 0
        self.last_refresh_s = now_s


def rolling_summary_due(
    state: SummaryState,
    *,
    now_s: float,
    every_n_deltas: int | None = None,
    max_age_s: float | None = None,
) -> bool:
    """True when a rolling-summary refresh is due — EITHER threshold trips (§3.2).

    Fires when ``deltas_since >= N`` (AC-SCRIBE-05) OR ``now_s - last_refresh_s >= max_age``
    (AC-SCRIBE-06), whichever comes first. Thresholds default to the env-configured
    §3.2 values (N=20 / 90s). Pure and deterministic: ``now_s`` is injected so a
    frozen clock exercises exactly one trigger at a time.
    """
    n = every_n_deltas if every_n_deltas is not None else rolling_summary_every_n_deltas()
    age = max_age_s if max_age_s is not None else rolling_summary_max_age_s()
    if state.deltas_since >= n:
        return True
    if (now_s - state.last_refresh_s) >= age:
        return True
    return False


def summary_model() -> str:
    """The summary generator model id — same ``PROXY_MODEL_SCRIBE`` seat as the Scribe."""
    model: str = model_for(_SCRIBE_SEAT)
    return model


def build_summary_request(notes_text: str, *, model: str | None = None) -> dict[str, Any]:
    """Assemble the ``messages.create`` params for the Segment B regeneration (pure).

    Reads the *current notes object* (stable-ordered, rendered to text) — never the
    raw transcript — and asks for a compact prose rewrite. No tool: the summary is
    free plain text (it is not a schema-shaped delta).
    """
    return {
        "model": model if model is not None else summary_model(),
        "max_tokens": SUMMARY_MAX_TOKENS,
        "system": ROLLING_SUMMARY_PROMPT,
        "messages": [{"role": "user", "content": notes_text}],
    }


def _extract_text(resp: Any) -> str:
    """Join the text blocks of a Messages response into the new Segment B string."""
    text: str = "".join(
        str(b.text) for b in resp.content if getattr(b, "type", None) == "text"
    )
    return text.strip()


async def regenerate_rolling_summary(
    notes_text: str,
    *,
    call_external: CallExternal,
    client: Any,
    model: str | None = None,
) -> str:
    """Regenerate Segment B from the notes text via ONE ``messages.create`` (through the seam).

    ``notes_text`` is ``notes.render_for_summary()`` (stable-ordered) produced by the
    caller — this module reads the notes object's rendering, not the raw transcript.
    Returns the new compact summary; the caller swaps it into the prefix so only
    Segment B's cache re-creates while Segment A keeps hitting.
    """
    request = build_summary_request(notes_text, model=model)

    async def _op() -> Any:
        return await client.messages.create(**request)

    outcome = await call_external(_op, service=_SERVICE)
    resp = getattr(outcome, "value", outcome)
    return _extract_text(resp)


def maybe_refresh_in_background(
    state: SummaryState,
    refresh: Callable[[], Awaitable[Any]],
    *,
    now_s: float,
    every_n_deltas: int | None = None,
    max_age_s: float | None = None,
) -> "asyncio.Task[Any] | None":
    """If a refresh is due, schedule it OFF the hot path and return immediately (AC-SCRIBE-07).

    On trigger the cadence is reset *synchronously* (so a concurrent window does not
    re-fire the same refresh) and the actual regeneration is launched as a
    fire-and-forget ``asyncio`` task — the serial Scribe consumer proceeds to the
    next window without awaiting the (possibly slow) refresh call. Returns the task
    (for the caller/test to await/track) or ``None`` when no refresh was due.
    """
    if not rolling_summary_due(
        state, now_s=now_s, every_n_deltas=every_n_deltas, max_age_s=max_age_s
    ):
        return None
    # Reset the cadence up front so the next window's due-check sees a fresh state
    # and cannot double-schedule the same refresh while this one is in flight.
    state.mark_refreshed(now_s)
    return asyncio.ensure_future(refresh())


__all__ = [
    "SUMMARY_MAX_TOKENS",
    "DEFAULT_ROLLING_SUMMARY_EVERY_N_DELTAS",
    "DEFAULT_ROLLING_SUMMARY_MAX_AGE_S",
    "ROLLING_SUMMARY_PROMPT",
    "CallExternal",
    "SummaryState",
    "rolling_summary_every_n_deltas",
    "rolling_summary_max_age_s",
    "rolling_summary_due",
    "summary_model",
    "build_summary_request",
    "regenerate_rolling_summary",
    "maybe_refresh_in_background",
]
