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


# The per-model OUTPUT-token ceiling — each model's real ``max_tokens`` (Doc 05 §3.9
# "honest ceilings"). A self-clamp is only honest if the ceiling is the model's true max:
# a Sonnet request must never be allowed to ask for an Opus-sized output. These are the
# published maxima for the current model catalog (Opus 128K, Sonnet 64K, Haiku 64K).
# Defined ONCE here alongside the seat table so the whole workspace clamps against ONE
# source (never a per-service copy). RE-AUDIT on a model-catalog bump.
MODEL_OUTPUT_CEILINGS: dict[str, int] = {
    "claude-opus-4-8": 128_000,
    "claude-sonnet-4-6": 64_000,
    "claude-haiku-4-5": 64_000,
}

# The one global output-token env knob (Doc 05 §3.2/§3.9): ``MAX_OUTPUT_TOKENS`` sets the
# intended per-request output budget; each model self-clamps it down to its own ceiling.
_DEFAULT_MAX_OUTPUT_TOKENS = 32_000


def output_token_ceiling(model: str) -> int | None:
    """The output-token ceiling for a model id, or ``None`` if we hold no ceiling for it.

    ``None`` (an unknown/future model) means 'no known clamp' — the caller falls back to the
    env value alone rather than treating 0 as a ceiling (which would silently zero-out output).
    """
    return MODEL_OUTPUT_CEILINGS.get(model)


def _env_max_output_tokens() -> int:
    """Read the ``MAX_OUTPUT_TOKENS`` env knob (falling back to the default)."""
    raw = os.environ.get("MAX_OUTPUT_TOKENS")
    if raw is None or not raw.strip():
        return _DEFAULT_MAX_OUTPUT_TOKENS
    try:
        return int(raw)
    except ValueError:
        return _DEFAULT_MAX_OUTPUT_TOKENS


def max_output_tokens_for(model: str, *, env_max: int | None = None) -> int:
    """The ``min(env, model_ceiling)`` output-token self-clamp for one model (§3.2/§3.9).

    One global ``MAX_OUTPUT_TOKENS`` env (or the explicit ``env_max``) sets the intended
    output budget; this clamps it DOWN to the model's own ceiling so a request never asks a
    model for more than it can emit. A model with no known ceiling degrades to the env value
    alone (Rule 6 — never raises, never zeroes output).
    """
    env = env_max if env_max is not None else _env_max_output_tokens()
    ceiling = output_token_ceiling(model)
    if ceiling is None:
        return env
    return min(env, ceiling)
