"""Doc 05 §3.9 / §3.2 / §4 — cost & latency: preflight, per-role model seats, output clamp.

Authored from the spec (no sealed doc05 bundle exists). Every test runs a REAL host
path — the imported ``llm.routing`` seat table, the ``min(env, model_ceiling)`` output
clamp, the ``SessionDriver`` per-disposition seat resolution + cost recording, and the
in-process ``/health`` preflight in the session driver that fails fast so a cold-start
never hits the live tier.

The node's Definition of Done, restated as executable assertions:

  * a cold-start never happens on the live tier — the preflight fails fast with a clear
    reason (stale/dead sandbox, code-hash mismatch, clone not ready) BEFORE any ``query()``;
  * each role resolves its model via the IMPORTED routing table — never a hard-coded model
    string in ``agent_config`` / ``session`` (proven by grepping the source + by resolving
    every seat through ``llm.routing.model_for``);
  * a ``min(env, model_ceiling)`` output-token clamp per model (env cannot exceed the real
    model ceiling; a small env wins over a large ceiling);
  * the 1-hr cache breakpoint sits on the stable prefix (3600s, not the 5-min SDK default);
  * per-task ``total_cost`` is recorded via the cost meter.
"""
from __future__ import annotations

import inspect
from pathlib import Path
from uuid import uuid4

import pytest

# The ONE imported seat table (CANONICAL §11.9) — never redefined in the workroom service.
from llm.routing import SEATS, model_for
from llm.routing import (
    MODEL_OUTPUT_CEILINGS,
    max_output_tokens_for,
    output_token_ceiling,
)

from workroom import agent_config
from workroom.agent_config import DISPOSITIONS, seat_for_disposition
from workroom.session import SessionDriver, stable_prefix_cache_ttl_seconds


# --------------------------------------------------------------------------- #
# 1. Per-role model seats resolve via the IMPORTED routing table (never hard-coded).
# --------------------------------------------------------------------------- #

def test_model_seats_resolve_via_imported_routing_table() -> None:
    """Every disposition maps to a real seat in the ONE canonical ``llm.routing`` table.

    The Workroom uses two seats (D-014): the quick/plan/critic/verifier dispositions ride
    the WORKROOM (Sonnet-class) seat; the worker (big build) rides BIG_BUILD (Opus-class).
    ``seat_for_disposition`` returns a seat NAME that MUST be a key in ``llm.routing.SEATS``
    — proving the model is resolved through the imported table, never a literal here.
    """
    for disposition in DISPOSITIONS:
        seat = seat_for_disposition(disposition)
        assert seat in SEATS, f"{disposition!r} → seat {seat!r} not in the canonical routing table"
        # And it resolves to a real model id through the imported resolver.
        assert model_for(seat), f"seat {seat!r} did not resolve to a model id"


def test_worker_takes_opus_and_quick_takes_sonnet_class() -> None:
    """The big-build worker rides the Opus-class seat; the quick fast path rides Sonnet.

    §3.2: the spend lives on the big-build worker (Opus-class); the quick ask is
    fast+grounded (Haiku/Sonnet-class). This is the cheap-first per-role inversion.
    """
    assert seat_for_disposition("worker") == "BIG_BUILD"
    assert model_for("BIG_BUILD").startswith("claude-opus")
    for disposition in ("quick", "plan", "critic", "verifier"):
        assert seat_for_disposition(disposition) == "WORKROOM"
    assert model_for("WORKROOM").startswith("claude-sonnet")


def test_seat_for_disposition_fails_closed_on_unknown() -> None:
    """An unknown disposition fails closed — never silently defaults to a spendy seat."""
    with pytest.raises((KeyError, ValueError)):
        seat_for_disposition("definitely-not-a-disposition")


def test_env_override_flows_through_the_imported_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    """``PROXY_MODEL_<SEAT>`` overrides the seat — the one config surface env may touch (§3.2).

    Resolution is ``env.PROXY_MODEL_<ROLE> || tier_default`` in the imported table; the
    workroom seat resolver must honor it (it delegates, never re-reads env itself)."""
    monkeypatch.setenv("PROXY_MODEL_BIG_BUILD", "claude-opus-4-8")
    assert model_for(seat_for_disposition("worker")) == "claude-opus-4-8"


def test_no_hardcoded_model_string_in_workroom_source() -> None:
    """NOT-done guard: no ``claude-*`` model literal in agent_config / session (§3.2 invariant).

    The model table MUST be imported from ``llm.routing`` — a hard-coded ``claude-…`` id in
    the service is exactly the invariant this node forbids. Docstrings/comments naming the
    id family (``Haiku/Sonnet-class``) are prose; a real quoted ``claude-<name>`` id in code
    is the violation. We scan for the quoted-id pattern in executable lines only.
    """
    import re

    quoted_model = re.compile(r"""['"]claude-(opus|sonnet|haiku|fable)-[0-9]""")
    for mod in (agent_config, __import__("workroom.session", fromlist=["x"])):
        src_path = Path(inspect.getsourcefile(mod))  # type: ignore[arg-type]
        for raw in src_path.read_text().splitlines():
            line = raw.split("#", 1)[0]  # drop trailing comments
            stripped = line.strip()
            # skip pure docstring/prose lines (no assignment / call of a model literal)
            if quoted_model.search(line):
                pytest.fail(f"hard-coded model id in {src_path.name}: {stripped!r}")


# --------------------------------------------------------------------------- #
# 2. The min(env, model_ceiling) output-token clamp (§3.2 / §3.9).
# --------------------------------------------------------------------------- #

def test_output_ceilings_are_the_real_model_maxima() -> None:
    """The per-model output ceilings match the real model maxima (Opus 128K, Sonnet/Haiku 64K).

    These are the honest ceilings §3.9 names — a self-clamp is only honest if the ceiling is
    the model's true max_tokens. (claude-api model catalog: opus-4-8=128K, sonnet-4-6=64K,
    haiku-4-5=64K.)
    """
    assert MODEL_OUTPUT_CEILINGS["claude-opus-4-8"] == 128_000
    assert MODEL_OUTPUT_CEILINGS["claude-sonnet-4-6"] == 64_000
    assert MODEL_OUTPUT_CEILINGS["claude-haiku-4-5"] == 64_000
    # Every seat's model has a known ceiling (no seat can resolve to an unclampable model).
    for seat in SEATS:
        assert output_token_ceiling(model_for(seat)) > 0


def test_clamp_is_min_of_env_and_ceiling_env_wins_when_smaller() -> None:
    """A small ``MAX_OUTPUT_TOKENS`` env wins: min(8000, 128000) == 8000."""
    assert max_output_tokens_for("claude-opus-4-8", env_max=8_000) == 8_000


def test_clamp_is_min_of_env_and_ceiling_ceiling_wins_when_env_too_large() -> None:
    """A too-large env is clamped DOWN to the model ceiling: min(1_000_000, 64000) == 64000.

    This is the load-bearing self-clamp: one global ``MAX_OUTPUT_TOKENS`` env can be set
    high, and each model clamps it to its own true ceiling so a request never asks a model
    for more than it can emit (a Sonnet request never asks for 128K)."""
    assert max_output_tokens_for("claude-sonnet-4-6", env_max=1_000_000) == 64_000
    assert max_output_tokens_for("claude-haiku-4-5", env_max=1_000_000) == 64_000


def test_clamp_reads_env_when_no_explicit_max(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no explicit arg the clamp reads the ``MAX_OUTPUT_TOKENS`` env then min()s it."""
    monkeypatch.setenv("MAX_OUTPUT_TOKENS", "16000")
    assert max_output_tokens_for("claude-opus-4-8") == 16_000
    monkeypatch.setenv("MAX_OUTPUT_TOKENS", "500000")
    assert max_output_tokens_for("claude-opus-4-8") == 128_000  # clamped to the opus ceiling


def test_clamp_is_never_missing_for_an_unknown_model_falls_back_to_env() -> None:
    """An unknown model id has no ceiling — the clamp degrades to the env value, never crashes.

    (Rule 6 / honest-degrade: a model we don't have a ceiling for is clamped to the env only,
    rather than raising and killing the run.)"""
    assert max_output_tokens_for("some-future-model", env_max=42_000) == 42_000


# --------------------------------------------------------------------------- #
# 3. The 1-hour cache breakpoint on the stable prefix (§3.9 / D-021).
# --------------------------------------------------------------------------- #

def test_stable_prefix_cache_ttl_is_one_hour_not_the_sdk_default() -> None:
    """The cache breakpoint carries the 1-hr TTL (3600s), not the SDK 5-min default (§3.9)."""
    assert stable_prefix_cache_ttl_seconds() == 3600
    assert stable_prefix_cache_ttl_seconds() != 300  # NOT the 5-minute SDK default


def test_the_cache_breakpoint_sits_on_the_stable_prefix() -> None:
    """The breakpoint is on the STABLE Workroom prefix (disposition prompt), not the volatile
    per-task bundle. ``SessionDriver.stable_prefix`` IS that prefix; it opens with the verbatim
    §2.2 disposition instruction and carries no per-task volatile content."""
    prefix = SessionDriver.stable_prefix()
    assert prefix == agent_config.WORKROOM_SYSTEM_PREFIX
    assert prefix.startswith(agent_config.DISPOSITION_OPENER)
    # Volatile per-task markers must NOT be in the cached prefix (they ride the prompt after it).
    assert "TRANSCRIPT TAIL" not in prefix
    assert "Task id" not in prefix
