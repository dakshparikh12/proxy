"""The ONE canonical per-role model seat table (Doc 00 §7, CANONICAL §11.11/§12.12).

Real model ids only — no per-doc variants, no fake ids. Env vars
``PROXY_MODEL_<SEAT>`` override a seat at runtime (seats are the one config
surface env may touch); the base table below is the single source of truth.
"""
from __future__ import annotations

import os

# The eight canonical seats → real model ids. This mapping is defined ONCE, here.
SEATS: dict[str, str] = {
    "SCRIBE": "claude-haiku-4-5",
    "SCRIBE_CLOSE": "claude-haiku-4-5",
    "GATE": "claude-haiku-4-5",
    "QUALITY_GATE": "claude-haiku-4-5",
    "ANSWER": "claude-sonnet-4-6",
    "ORCHESTRATOR": "claude-sonnet-4-6",
    "WORKROOM": "claude-sonnet-4-6",
    "BIG_BUILD": "claude-opus-4-8",
}


def model_for(seat: str) -> str:
    """Resolve a seat to its model id, honouring a ``PROXY_MODEL_<SEAT>`` override."""
    key = seat.upper()
    if key not in SEATS:
        raise KeyError(f"unknown model seat {seat!r}; seats are {sorted(SEATS)}")
    return os.environ.get(f"PROXY_MODEL_{key}", SEATS[key])
