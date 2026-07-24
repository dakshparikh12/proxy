"""Scribe cost writes — the bare Messages call records model + cache split.

The Scribe is a bare ``messages.create`` (§3.2), so its per-call cost is NOT read
from an SDK ``total_cost_usd`` field (that only exists on the close-pass
``generateStructured`` result) — it is computed from the response ``usage`` token
counts against the fixed Haiku-4.5 rate card (AC-COST-01/02/03). The write goes
straight to ``meeting_cost`` via :func:`record_scribe_cost`; it deliberately does
NOT traverse the ``libs.http.call_external`` provider seam (AC-COST-11 — the cost
path holds no provider import).
"""
from __future__ import annotations

from typing import Any

from libs.db import Database
from libs.ops import record_model_cost

# ── Haiku-4.5 rate card (USD per token), §12.7 / AC-COST-01 ──────────────────
# Uncached input and output are the base rates; a cache WRITE (first time a prefix
# is cached) costs 1.25× input; a cache READ (a steady-state prefix hit) costs 0.10×
# input. These are exact — a unit-property test pins each value and its derivation.
HAIKU_INPUT: float = 1.00e-6
HAIKU_OUTPUT: float = 5.00e-6
HAIKU_CACHE_WRITE: float = HAIKU_INPUT * 1.25  # 1.25e-6
HAIKU_CACHE_READ: float = HAIKU_INPUT * 0.10  # 1.00e-7


def _usage_field(usage: Any, name: str) -> int:
    """Read a usage token count, defaulting an absent/None field to 0.

    The response ``usage`` object may be an SDK model (attributes) or a plain dict
    (a recorded cassette body); a non-cache call omits the ``cache_*`` fields
    entirely, so a missing field is honestly 0 — never an AttributeError/KeyError
    (AC-COST-03).
    """
    if isinstance(usage, dict):
        value = usage.get(name)
    else:
        value = getattr(usage, name, None)
    return int(value) if value is not None else 0


def scribe_call_cost_usd(usage: Any) -> float:
    """Compute ONE Scribe micro-call's USD cost from its response ``usage``.

    The four terms are input, output, cache-write (``cache_creation_input_tokens``),
    and cache-read (``cache_read_input_tokens``) each at their Haiku rate. Absent
    cache fields default to 0 (AC-COST-03/03-NEG), so a non-cache call is simply
    ``input * HAIKU_INPUT + output * HAIKU_OUTPUT``.
    """
    input_tokens = _usage_field(usage, "input_tokens")
    output_tokens = _usage_field(usage, "output_tokens")
    cache_write = _usage_field(usage, "cache_creation_input_tokens")
    cache_read = _usage_field(usage, "cache_read_input_tokens")
    return (
        input_tokens * HAIKU_INPUT
        + output_tokens * HAIKU_OUTPUT
        + cache_write * HAIKU_CACHE_WRITE
        + cache_read * HAIKU_CACHE_READ
    )


def scribe_cost_split(usage: Any) -> tuple[float, float, float]:
    """Split one call's cost into ``(model_usd, cache_read_usd, cache_creation_usd)``.

    ``model_usd`` carries the uncached input + output spend; the two cache terms are
    recorded separately so ``meeting_cost``'s prompt-cache split reflects real spend.
    This is the shape :func:`record_scribe_cost` writes to Postgres.
    """
    input_tokens = _usage_field(usage, "input_tokens")
    output_tokens = _usage_field(usage, "output_tokens")
    cache_write = _usage_field(usage, "cache_creation_input_tokens")
    cache_read = _usage_field(usage, "cache_read_input_tokens")
    model_usd = input_tokens * HAIKU_INPUT + output_tokens * HAIKU_OUTPUT
    cache_read_usd = cache_read * HAIKU_CACHE_READ
    cache_creation_usd = cache_write * HAIKU_CACHE_WRITE
    return model_usd, cache_read_usd, cache_creation_usd


async def record_scribe_cost(
    db: Database,
    *,
    meeting_id: Any,
    model_usd: float,
    cache_read_usd: float = 0.0,
    cache_creation_usd: float = 0.0,
) -> None:
    """Increment meeting_cost.model_usd and record the prompt-cache split."""
    await record_model_cost(
        db,
        meeting_id,
        model_usd=model_usd,
        cache_read_usd=cache_read_usd,
        cache_creation_usd=cache_creation_usd,
    )


async def record_scribe_cost_from_usage(
    db: Database, *, meeting_id: Any, usage: Any
) -> float:
    """Derive one micro-call's cost from its ``usage`` and write it to ``meeting_cost``.

    The single wire point from the real Scribe call to the durable cost ledger: it
    computes the model + cache split from the response usage token counts
    (:func:`scribe_cost_split`) and increments the meeting's row. Returns the total
    USD recorded so the caller can log/telemeter it. Straight to Postgres — no
    provider seam in the cost path (AC-COST-11).
    """
    model_usd, cache_read_usd, cache_creation_usd = scribe_cost_split(usage)
    await record_scribe_cost(
        db,
        meeting_id=meeting_id,
        model_usd=model_usd,
        cache_read_usd=cache_read_usd,
        cache_creation_usd=cache_creation_usd,
    )
    return model_usd + cache_read_usd + cache_creation_usd


__all__ = [
    "HAIKU_INPUT",
    "HAIKU_OUTPUT",
    "HAIKU_CACHE_WRITE",
    "HAIKU_CACHE_READ",
    "scribe_call_cost_usd",
    "scribe_cost_split",
    "record_scribe_cost",
    "record_scribe_cost_from_usage",
]
