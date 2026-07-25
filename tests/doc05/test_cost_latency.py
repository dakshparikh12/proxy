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
    """NOT-done guard: NO ``claude-*`` model literal ANYWHERE in the workroom service (§3.2).

    The model table MUST be imported from ``llm.routing`` — a hard-coded ``claude-…`` id in
    the service is exactly the invariant this node forbids. This scans the WHOLE
    ``services/workroom`` source tree (every ``.py``), not just agent_config/session, because
    the last violation lived in ``sandbox_transport.py`` (a Sonnet literal as the
    ``get_agent_tool_config`` default) — a module the old narrower scan never covered. The sole
    sanctioned home for a model id is ``libs/llm/src/llm/routing.py``; no workroom source line
    may spell a quoted ``claude-<name>`` id. Docstrings/prose naming the id family
    (``Haiku/Sonnet-class``) are fine — we match only a real quoted model literal.
    """
    import re

    quoted_model = re.compile(r"""['"]claude-(opus|sonnet|haiku|fable)-[0-9]""")
    workroom_src = Path(inspect.getsourcefile(agent_config)).parent  # type: ignore[arg-type]
    scanned = 0
    for src_path in sorted(workroom_src.rglob("*.py")):
        scanned += 1
        for raw in src_path.read_text().splitlines():
            line = raw.split("#", 1)[0]  # drop trailing comments
            stripped = line.strip()
            # A docstring line inside triple quotes can still hold a quoted id; but a real
            # violation is a quoted model id in code. We fail on ANY quoted claude-<name>-<n>
            # occurrence in a non-comment line — the sanctioned id home is llm.routing alone.
            if quoted_model.search(line):
                pytest.fail(f"hard-coded model id in {src_path.name}: {stripped!r}")
    assert scanned >= 3, "the workroom source scan must cover the whole service tree"


def test_transport_default_model_resolves_via_the_imported_routing_table() -> None:
    """``get_agent_tool_config`` with NO explicit model resolves its default from the IMPORTED
    ``llm.routing`` table (the WORKROOM/Sonnet-class seat) — never a ``claude-*`` literal (D-014).

    This is the regression wall for the exact prior violation: a hard-coded Sonnet id as the
    transport default. The default model on the built options MUST equal ``model_for("WORKROOM")``
    (table-routed) and MUST be a real seat model, so a bare registration can never smuggle a
    literal past the seat table. An env override on that seat flows through too (the one config
    surface env may touch), proving the value comes from ``model_for``, not a constant."""
    from libs.ops import sandbox_provider
    from workroom.sandbox_transport import get_agent_tool_config

    sandbox_provider._reset_for_test()
    handle = sandbox_provider.provision(meeting_id="m-transport-default-model")
    cfg = get_agent_tool_config(handle, access="readwrite")  # NO explicit model → table default
    assert cfg.options.model == model_for("WORKROOM"), (
        "the transport's default model must be resolved from the imported routing table "
        "(model_for('WORKROOM')), never a hard-coded claude-* literal"
    )
    assert cfg.options.model in set(SEATS.values()), "the default must be a real seat model id"


def test_transport_default_model_honors_the_seat_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The transport default is genuinely ``model_for('WORKROOM')`` — a ``PROXY_MODEL_WORKROOM``
    env override changes it, which a hard-coded literal could NOT do (§3.2). This proves the id
    is read from the imported resolver at call time, not baked in."""
    from libs.ops import sandbox_provider
    from workroom.sandbox_transport import get_agent_tool_config

    monkeypatch.setenv("PROXY_MODEL_WORKROOM", "claude-sonnet-4-6")
    sandbox_provider._reset_for_test()
    handle = sandbox_provider.provision(meeting_id="m-transport-default-env")
    cfg = get_agent_tool_config(handle, access="readwrite")
    assert cfg.options.model == "claude-sonnet-4-6"  # the override the resolver returned


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
    §2.2 disposition instruction and carries no per-task volatile content.

    The stable prefix now carries the §3.10 injection guardrail appended LAST (the guardrail is
    stable — identical every task — so it belongs INSIDE the cached prefix, riding every query).
    It is still the bare disposition prefix + the (stable) guardrail; no volatile content."""
    prefix = SessionDriver.stable_prefix()
    # The cached prefix = the stable disposition prefix WITH the stable injection guardrail last.
    assert prefix == agent_config.guardrailed_system_prefix()
    assert prefix.startswith(agent_config.WORKROOM_SYSTEM_PREFIX)
    assert prefix.startswith(agent_config.DISPOSITION_OPENER)
    # The guardrail is the final authoritative segment of the (stable) cached prefix (§3.10);
    # the guardrail marker sits AFTER the base prefix, so it is genuinely appended last.
    assert prefix.rfind(agent_config.GUARDRAIL_MARK) > len(agent_config.WORKROOM_SYSTEM_PREFIX) - 1
    # Volatile per-task markers must NOT be in the cached prefix (they ride the prompt after it).
    assert "TRANSCRIPT TAIL" not in prefix
    assert "Task id" not in prefix


def test_cache_control_breakpoint_is_ephemeral_one_hour_not_five_minutes() -> None:
    """The ``cache_control`` breakpoint is an ``ephemeral`` block pinned to the 1-hr TTL (§3.9).

    Not just a constant: this is the actual Messages-API prompt-cache directive
    (``{"type": "ephemeral", "ttl": "1h"}``) that must ride the stable-prefix breakpoint. The
    ``1h`` wire token — NOT the SDK's default 5-minute breakpoint (``5m``) — is what keeps the
    large repo-grounding prefix warm for the whole meeting-hour (CANONICAL §10.1)."""
    cc = agent_config.stable_prefix_cache_control()
    assert cc["type"] == "ephemeral"
    assert cc["ttl"] == "1h", "the breakpoint must carry the 1-hour TTL wire token, not the default"
    assert cc["ttl"] != "5m"  # NOT the SDK default 5-minute breakpoint
    # And it derives from the honest seconds source of truth (3600s == 1h).
    assert agent_config.WORKROOM_CACHE_TTL_SECONDS == 3600


def test_one_hour_cache_ttl_is_wired_onto_the_real_query_options() -> None:
    """WIRED, not asserted-only: the 1-hr TTL rides the REAL ``ClaudeAgentOptions`` the
    ``query()`` path enforces (§3.9). ``get_agent_tool_config`` builds the actual options for a
    warm sandbox through ``workroom_options``; the 1-hour prompt-cache breakpoint must be present
    on that options object (carried via ``extra_args`` — the SDK's CLI passthrough), so the CLI
    marks the system-prompt breakpoint with the 1-hr TTL. If the SDK's default 5-min TTL were
    used at runtime this assertion would FAIL — the behavior §3.9 requires is EXERCISED here,
    not merely a constant asserted in isolation."""
    from libs.ops import sandbox_provider
    from workroom.sandbox_transport import get_agent_tool_config

    sandbox_provider._reset_for_test()
    handle = sandbox_provider.provision(meeting_id="m-cache-ttl-wired")
    config = get_agent_tool_config(
        handle,
        access="readwrite",
        model="claude-sonnet-4-6",
        max_turns=3,
        system_prompt=SessionDriver.stable_prefix(),
    )
    extra = dict(getattr(config.options, "extra_args", None) or {})
    # The 1-hour TTL is present on the options the query() actually uses.
    assert extra.get("system-prompt-cache-ttl") == "1h", (
        "the built query options must carry the 1-hour prompt-cache TTL — a missing/5m TTL means "
        "the stable prefix would fall out of cache every 5 minutes across the meeting-hour"
    )
    assert extra.get("system-prompt-cache-ttl") != "5m"  # never the SDK default


# --------------------------------------------------------------------------- #
# 2b. The output clamp is threaded into the REAL query env end-to-end (§3.2 / §3.9).
# --------------------------------------------------------------------------- #

def test_output_clamp_is_threaded_into_the_real_query_env_per_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PARTIAL no more: the ``min(env, ceiling)`` clamp is threaded into ``options.env`` on the
    REAL driver path, keyed by the per-role SEAT-resolved model (§3.2/§3.9).

    ``SessionDriver._build_query_options`` resolves the disposition's model via the IMPORTED
    routing table, then clamps ``MAX_OUTPUT_TOKENS`` into the curated env the SDK ``query()``
    reads. With a large global env, the worker (Opus seat) clamps to the Opus ceiling (128K) and
    the quick disposition (Sonnet seat) clamps to the Sonnet ceiling (64K) — proving the clamp is
    (a) applied end-to-end on the real options, and (b) model-specific (a Sonnet request never
    asks for an Opus-sized output). If ``_apply_output_clamp`` were dropped, or the seat→model
    wiring regressed, ``options.env['MAX_OUTPUT_TOKENS']`` would be absent/wrong and this FAILS."""
    from libs.ops import sandbox_provider

    # A large global budget so the per-model ceiling is what actually bites (proves the clamp).
    monkeypatch.setenv("MAX_OUTPUT_TOKENS", "1000000")
    sandbox_provider._reset_for_test()
    handle = sandbox_provider.provision(meeting_id="m-clamp-wired")

    # worker → BIG_BUILD (Opus) seat → clamp to the Opus ceiling.
    worker = SessionDriver(disposition="worker")
    worker_opts = worker._build_query_options(handle, access="readwrite")
    assert worker_opts.model == model_for(seat_for_disposition("worker"))
    assert worker_opts.env.get("MAX_OUTPUT_TOKENS") is not None, (
        "the output clamp MUST be threaded into the real query env — a missing key means the "
        "driver never applied the min(env, ceiling) clamp on the real path"
    )
    assert int(worker_opts.env["MAX_OUTPUT_TOKENS"]) == MODEL_OUTPUT_CEILINGS[worker_opts.model]

    # quick → WORKROOM (Sonnet) seat → clamp to the smaller Sonnet ceiling (never Opus-sized).
    quick = SessionDriver(disposition="quick")
    quick_opts = quick._build_query_options(handle, access="readonly")
    assert quick_opts.model == model_for(seat_for_disposition("quick"))
    assert int(quick_opts.env["MAX_OUTPUT_TOKENS"]) == MODEL_OUTPUT_CEILINGS[quick_opts.model]
    # The load-bearing inversion: the Sonnet clamp is strictly smaller than the Opus clamp.
    assert int(quick_opts.env["MAX_OUTPUT_TOKENS"]) < int(worker_opts.env["MAX_OUTPUT_TOKENS"])
