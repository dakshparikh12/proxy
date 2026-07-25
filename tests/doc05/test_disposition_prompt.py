"""Acceptance — the disposition prompt-bias + per-disposition curated toolbelt (05 §2.1/§2.2/§3.5).

Node ``workroom.disposition-prompt`` (evidence class ``[unit]``). §2.2 pins the
Workroom's *single moment of judgment* into the **cached system prompt** — one standing
disposition instruction ("If this can be answered with a straight lookup … answer it
quickly … Otherwise: plan, build, and verify.") + the standing law — steering the engine's
own quick-vs-deep call. **NO task-type router, NO intake classifier** (the engine decides).

§3.5 / CANONICAL §10.5 then pins the *curated per-disposition tool subset* (accuracy
degrades with every extra advertised tool): each disposition advertises exactly its subset,
NEVER the union:
  * quick / plan  — read + map only (no write, no propose_change)
  * critic / verifier — read + map + ``run_command`` (NO write/edit/ast_grep/propose_change)
  * worker — read + map + the full sandbox write set + the host ``propose_change``

DoD (the node's definition_of_done):
  * the cached system prompt carries the VERBATIM disposition opener + standing law;
  * a quick ask fires NO plan and advertises ONLY read+map tools;
  * a worker advertises the full write set + host ``propose_change``;
  * a verifier advertises ``run_command`` but NEVER ``write_file``/``propose_change``;
  * NOT done if a router/classifier picks the disposition, a read-only disposition can
    reach a write tool, or extended thinking is on the quick-ask fast path.

These run the REAL host path: everything comes from ``workroom.agent_config`` — the module
that owns the cached system-prompt prefix + the per-disposition allowed/disallowed policy.
"""
from __future__ import annotations

import inspect

import pytest

pytestmark = pytest.mark.isolation


# ---------------------------------------------------------------------------
# §2.2 — the cached system prompt carries the VERBATIM disposition opener
# ---------------------------------------------------------------------------

# The opener line, verbatim from §2.2 (must appear character-for-character).
_VERBATIM_OPENER = (
    "If this can be answered with a straight lookup, a single tool call, or one step — "
    "answer it quickly and simply, now. Otherwise: plan, build, and verify."
)


def test_disposition_opener_is_verbatim_in_the_cached_prefix() -> None:
    from workroom.agent_config import WORKROOM_SYSTEM_PREFIX

    assert _VERBATIM_OPENER in WORKROOM_SYSTEM_PREFIX, (
        "the cached system prompt must carry the §2.2 disposition opener VERBATIM — it is "
        "the single standing instruction that steers quick-vs-deep (no router)"
    )


def test_opener_constant_exposed_and_verbatim() -> None:
    """The opener is a named constant so the prompt and the tests share one source."""
    from workroom.agent_config import DISPOSITION_OPENER

    assert DISPOSITION_OPENER == _VERBATIM_OPENER


def test_standing_law_present_in_the_cached_prefix() -> None:
    """§2.2's standing law follows the opener: cite file:line, run the check, one
    clarifying question only when it changes the action, stage world-touching acts."""
    from workroom.agent_config import WORKROOM_SYSTEM_PREFIX

    lowered = WORKROOM_SYSTEM_PREFIX.lower()
    # the load-bearing law clauses (grounding, the check, the one question, staged draft)
    assert "file:line" in lowered, "standing law: cite file:line for every claim"
    assert "not found by this method" in lowered, "standing law: the honest-decline phrasing"
    assert "one" in lowered and "question" in lowered, (
        "standing law: ask ONE clarifying question only when it changes the action"
    )
    assert "draft" in lowered, "standing law: world-touching acts are staged as a draft"


def test_opener_precedes_the_standing_law() -> None:
    """The disposition opener is the OPENING line — it comes before the standing law."""
    from workroom.agent_config import DISPOSITION_OPENER, WORKROOM_SYSTEM_PREFIX

    idx_opener = WORKROOM_SYSTEM_PREFIX.find(DISPOSITION_OPENER)
    idx_law = WORKROOM_SYSTEM_PREFIX.lower().find("file:line")
    assert idx_opener != -1 and idx_law != -1
    assert idx_opener < idx_law, "the disposition opener must OPEN the prompt, before the law"


def test_prefix_still_cached_one_hour_and_names_proxy_only() -> None:
    """The prefix stays the 1-hour stable-cache prefix and leaks no internal name."""
    from workroom.agent_config import WORKROOM_CACHE_TTL_SECONDS, WORKROOM_SYSTEM_PREFIX

    assert WORKROOM_CACHE_TTL_SECONDS == 3600
    lowered = WORKROOM_SYSTEM_PREFIX.lower()
    assert "proxy" in lowered
    for internal in ("orchestrator", "scribe", "workroom"):
        assert internal not in lowered, (
            f"user-visible system prefix must not carry the internal name {internal!r}"
        )


# ---------------------------------------------------------------------------
# NO router / classifier — the disposition is prompt bias, decided by the engine
# ---------------------------------------------------------------------------

def test_no_router_or_classifier_symbol_exists() -> None:
    """DoD: NOT done if any task-type router / intake classifier exists. The disposition
    comes from prompt bias (+ optional speculative layer), never a code branch that maps
    an ask to a task type."""
    import workroom.agent_config as ac

    banned = ("route", "router", "classify", "classifier", "intake", "task_type", "tasktype")
    for name in dir(ac):
        low = name.lower()
        assert not any(b in low for b in banned), (
            f"agent_config exposes {name!r} — a router/classifier symbol reintroduces a "
            "de-facto router; the disposition must come from prompt bias, not a code branch"
        )


def test_no_if_ask_maps_to_disposition_in_source() -> None:
    """A de-facto router = code that inspects the ASK text and picks a disposition. The
    disposition policy must key off an EXPLICIT disposition argument, never parse the ask."""
    import workroom.agent_config as ac

    src = inspect.getsource(ac).lower()
    # These would be the fingerprints of an intake classifier reading the request text.
    for fingerprint in ("def classify", "def route", "intake", "task_type", "if ask", "in ask"):
        assert fingerprint not in src, (
            f"source contains {fingerprint!r} — a classifier over the ask reintroduces a router"
        )


# ---------------------------------------------------------------------------
# The 5 dispositions + the curated per-disposition tool policy (§3.5 / §10.5)
# ---------------------------------------------------------------------------

def test_disposition_names_are_the_five_spec_dispositions() -> None:
    from workroom.agent_config import DISPOSITIONS

    assert set(DISPOSITIONS) == {"quick", "plan", "critic", "verifier", "worker"}


# Exact expected advertised (allowed) tool sets per §3.5 / §10.5.
_MAP = {
    "mcp__code_intel__get_dependents",
    "mcp__code_intel__who_writes",
    "mcp__code_intel__list_entry_points",
    "mcp__code_intel__grep",
    "mcp__code_intel__read",
}
_READ = {
    "mcp__code__read_file",
    "mcp__code__list_files",
    "mcp__code__grep",
    "mcp__code__glob",
}
_RUN = {"mcp__code__run_command"}
_WRITE = {
    "mcp__code__run_command",
    "mcp__code__write_file",
    "mcp__code__edit_file",
    "mcp__code__ast_grep",
}
_PROPOSE = {"mcp__propose_change__propose_change"}


@pytest.mark.parametrize(
    ("disposition", "expected_allowed"),
    [
        ("quick", _READ | _MAP),
        ("plan", _READ | _MAP),
        ("critic", _READ | _MAP | _RUN),
        ("verifier", _READ | _MAP | _RUN),
        ("worker", _READ | _MAP | _WRITE | _PROPOSE),
    ],
)
def test_each_disposition_advertises_exactly_its_curated_subset(disposition, expected_allowed) -> None:
    """CANONICAL §10.5: allowed_tools is computed PER disposition — exactly its subset,
    never the union. This is the whole node's product point."""
    from workroom.agent_config import disposition_tool_policy

    policy = disposition_tool_policy(disposition)
    assert set(policy.allowed_tools) == expected_allowed, (
        f"{disposition} must advertise EXACTLY its curated subset (never the union): "
        f"got {sorted(set(policy.allowed_tools))}"
    )


def test_no_disposition_advertises_the_union() -> None:
    """No disposition may advertise the whole-Proxy union of tools (accuracy tax)."""
    from workroom.agent_config import DISPOSITIONS, disposition_tool_policy

    union = _READ | _MAP | _WRITE | _PROPOSE
    for d in DISPOSITIONS:
        allowed = set(disposition_tool_policy(d).allowed_tools)
        if d != "worker":
            assert allowed != union, f"{d} advertises the full union — the accuracy tax §10.5 forbids"


# ---------------------------------------------------------------------------
# quick / plan — read + map ONLY (no write, no propose_change), NO plan fires
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("disposition", ["quick", "plan"])
def test_read_only_dispositions_carry_no_write_and_no_propose_change(disposition) -> None:
    from workroom.agent_config import disposition_tool_policy

    policy = disposition_tool_policy(disposition)
    allowed = set(policy.allowed_tools)
    # no sandbox write tools advertised
    for w in ("mcp__code__write_file", "mcp__code__edit_file", "mcp__code__ast_grep"):
        assert w not in allowed, f"{disposition} must not advertise the write tool {w}"
    # no host propose_change advertised
    assert "mcp__propose_change__propose_change" not in allowed, (
        f"{disposition} must not advertise propose_change (read-only)"
    )
    # and the write set is BLOCKED via disallowed_tools (allowed_tools does not filter MCP)
    disallowed = set(policy.disallowed_tools)
    for w in ("mcp__code__write_file", "mcp__code__edit_file", "mcp__code__ast_grep",
              "mcp__code__run_command", "mcp__propose_change__propose_change"):
        assert w in disallowed, (
            f"{disposition} must BLOCK {w} via disallowed_tools (allowed_tools does not "
            "filter MCP tools — the block must be structural, §3.8)"
        )


def test_quick_advertises_only_read_plus_map() -> None:
    """DoD: a quick ask advertises ONLY read + map tools."""
    from workroom.agent_config import disposition_tool_policy

    allowed = set(disposition_tool_policy("quick").allowed_tools)
    assert allowed == _READ | _MAP


def test_quick_fires_no_plan() -> None:
    """DoD: a quick ask fires no plan. The quick disposition is single-turn (no plan turn):
    its ``max_turns`` is small and it never carries a plan/critic step."""
    from workroom.agent_config import disposition_tool_policy

    policy = disposition_tool_policy("quick")
    assert policy.plan is False, "the quick disposition must not fire a plan (§2.1 fast path)"


# ---------------------------------------------------------------------------
# critic / verifier — read + map + run_command, but NEVER write / propose_change
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("disposition", ["critic", "verifier"])
def test_verifier_advertises_run_command_but_never_writes(disposition) -> None:
    """DoD: a verifier advertises run_command (it re-runs tests as evidence) but NEVER
    write_file / edit_file / ast_grep / propose_change (a verifier never edits what it grades)."""
    from workroom.agent_config import disposition_tool_policy

    policy = disposition_tool_policy(disposition)
    allowed = set(policy.allowed_tools)
    assert "mcp__code__run_command" in allowed, (
        f"{disposition} must advertise run_command to re-run the check as evidence (§3.7)"
    )
    for forbidden in ("mcp__code__write_file", "mcp__code__edit_file", "mcp__code__ast_grep",
                      "mcp__propose_change__propose_change"):
        assert forbidden not in allowed, (
            f"{disposition} must NEVER advertise {forbidden} — a verifier never edits the "
            "artifact it grades (§3.7)"
        )
    # structurally blocked too
    disallowed = set(policy.disallowed_tools)
    for forbidden in ("mcp__code__write_file", "mcp__code__edit_file", "mcp__code__ast_grep",
                      "mcp__propose_change__propose_change"):
        assert forbidden in disallowed, (
            f"{disposition} must BLOCK {forbidden} via disallowed_tools (structural, §3.8)"
        )


# ---------------------------------------------------------------------------
# worker — the full read + map + write set + host propose_change
# ---------------------------------------------------------------------------

def test_worker_advertises_full_write_set_plus_propose_change() -> None:
    """DoD: a worker advertises the full sandbox write set + the host propose_change."""
    from workroom.agent_config import disposition_tool_policy

    allowed = set(disposition_tool_policy("worker").allowed_tools)
    for w in ("mcp__code__run_command", "mcp__code__write_file",
              "mcp__code__edit_file", "mcp__code__ast_grep"):
        assert w in allowed, f"worker must advertise the sandbox write tool {w}"
    assert "mcp__propose_change__propose_change" in allowed, (
        "worker must advertise the HOST-side propose_change (the one sanctioned write, §3.8)"
    )


def test_worker_does_not_block_its_own_write_tools() -> None:
    """The worker's write tools must NOT appear in disallowed_tools (only the §3.4
    host-built-in block-list rides there for the worker)."""
    from workroom.agent_config import SDK_LOCAL_TOOLS, disposition_tool_policy

    policy = disposition_tool_policy("worker")
    disallowed = set(policy.disallowed_tools)
    for w in ("mcp__code__write_file", "mcp__code__edit_file", "mcp__code__ast_grep",
              "mcp__code__run_command", "mcp__propose_change__propose_change"):
        assert w not in disallowed, f"worker must not block its own write tool {w}"
    # the §3.4 host-built-in backstop still rides for every disposition
    assert set(SDK_LOCAL_TOOLS) <= disallowed, "the §3.4 host-built-in block-list still applies"


def test_propose_change_is_host_side_only_never_a_sandbox_code_tool() -> None:
    """CANONICAL §11.7: propose_change is a HOST in-process MCP tool (writes GCS + Postgres),
    NEVER one of the sandbox `code` transport tools (the sandbox is egress/credential-less)."""
    from workroom.agent_config import PROPOSE_CHANGE_TOOL

    assert PROPOSE_CHANGE_TOOL == "mcp__propose_change__propose_change"
    assert not PROPOSE_CHANGE_TOOL.startswith("mcp__code__"), (
        "propose_change must NOT be namespaced under the sandbox `code` server"
    )


# ---------------------------------------------------------------------------
# The §3.4 isolation triad still rides EVERY disposition (host-built-in block-list)
# ---------------------------------------------------------------------------

def test_every_disposition_carries_the_host_builtin_blocklist() -> None:
    from workroom.agent_config import DISPOSITIONS, SDK_LOCAL_TOOLS, disposition_tool_policy

    for d in DISPOSITIONS:
        disallowed = set(disposition_tool_policy(d).disallowed_tools)
        assert set(SDK_LOCAL_TOOLS) <= disallowed, (
            f"{d} must carry the §3.4 host-built-in block-list (Task, Bash, Read, …) as backstop"
        )


# ---------------------------------------------------------------------------
# Extended thinking is OFF the quick-ask fast path (latency-toxic) — DoD hard rule
# ---------------------------------------------------------------------------

def test_extended_thinking_off_the_quick_ask_fast_path() -> None:
    """DoD / §2.2 risk: extended thinking must stay OFF the quick-ask fast path. The
    quick, critic, verifier, and worker(sandbox-edit) dispositions run thinking OFF; only a
    deliberate plan/build-planning turn earns it (D-022), and even then never on quick."""
    from workroom.agent_config import disposition_extended_thinking_enabled

    assert disposition_extended_thinking_enabled("quick") is False, (
        "extended thinking must be OFF on the quick-ask fast path (latency-toxic, §2.2)"
    )


@pytest.mark.parametrize("disposition", ["quick", "critic", "verifier"])
def test_extended_thinking_off_for_fast_and_verify_paths(disposition) -> None:
    from workroom.agent_config import disposition_extended_thinking_enabled

    assert disposition_extended_thinking_enabled(disposition) is False, (
        f"extended thinking must be OFF for the {disposition} disposition (§2.2 / D-022)"
    )


def test_thinking_policy_agrees_thinking_off_for_quick_on_any_model() -> None:
    """Cross-check against the shared thinking_policy (D-022): the quick disposition's role
    never earns thinking even on an Opus-class model — the fast path is never latency-taxed."""
    from agentkit import thinking_policy

    from workroom.agent_config import disposition_role

    role = disposition_role("quick")
    enabled, _budget = thinking_policy("claude-opus-4-8", role)
    assert enabled is False, "the quick disposition's role must never enable thinking (fast path)"


# ---------------------------------------------------------------------------
# The policy is a real, typed object built on the host path (not a dict / stub)
# ---------------------------------------------------------------------------

def test_policy_object_is_typed_and_allowed_disjoint_from_the_blocked_write_set() -> None:
    """A read-only disposition must never have a tool in BOTH allowed and disallowed —
    the advertised set and the blocked set are coherent (no tool advertised then blocked)."""
    from workroom.agent_config import DISPOSITIONS, disposition_tool_policy

    for d in DISPOSITIONS:
        policy = disposition_tool_policy(d)
        allowed = set(policy.allowed_tools)
        disallowed = set(policy.disallowed_tools)
        overlap = allowed & disallowed
        assert not overlap, (
            f"{d}: a tool is both advertised and blocked ({sorted(overlap)}) — incoherent policy"
        )


def test_unknown_disposition_is_rejected_never_defaulted_to_write() -> None:
    """An unknown disposition must fail closed (never silently fall through to a write set)."""
    from workroom.agent_config import disposition_tool_policy

    with pytest.raises((ValueError, KeyError)):
        disposition_tool_policy("root")  # a made-up disposition must not resolve to worker
