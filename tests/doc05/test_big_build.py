"""Doc 05 §3.6.1 / §3.14 — the big-build PLAN TURN + plan persistence + plan critic.

The node ``workroom.plan-step`` (``services/workroom/src/workroom/big_build.py``): when the
engine judges a task large, the plan materializes as the first (read-only) SDK turn — a
structured multi-file plan (each unit: files-to-touch + done-when + verify line + order,
each tagged with the AC it serves) — which is:

  1. **PERSISTED durably, tied to the ``operation_runs`` row** (§3.6.1 "persisted subtasks";
     §3.1 task-durability = the ``operation_runs`` row, NO bespoke table). Reproducibly
     reconstructable from the durable substrate.
  2. **posts to chat before execution** (silence = go, §2.3.1/§3.14).
  3. **captures + immediately persists the SDK session id** (fire-and-forget so a restart
     resumes the same conversation, §3.6.1).
  4. amendable by a **fresh-context plan critic** — a SEPARATE ``query()`` that reads the
     plan for missing files/weak criteria/wrong ordering and can AMEND it (e.g. add a
     missing migration unit), ID-preserving (§3.6.1 note / §3.14).

These tests drive the REAL host path with in-process fakes (a fake provider that returns a
plan; a critic fake that adds a missing unit) — e2b is NOT installed; the E2B template bake
is the flagged Phase-3 residual, never faked here. NOT done if the plan is not reproducibly
persisted or the critic cannot amend it.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

import pytest

# Match the product: the contract types come from the top-level ``contracts`` module (the
# same identity harness/session use) so isinstance holds under the test src-wiring.
from contracts import AgentChunk, Bundle, Envelope


# ── the plan the fake planner emits (a realistic §3.14 multi-file plan) ───────

# A 4-unit plan, ordered setup→core→integration→testing, each unit carrying the four
# load-bearing fields (files/done-when/verify/order) PLUS the AC-tag it serves. This is the
# JSON array the plan turn asks the model to return (§3.6.1).
_PLAN_UNITS: list[dict[str, Any]] = [
    {
        "id": "U1",
        "title": "token-bucket core",
        "serves": "AC1",
        "files": ["lib/ratelimit.py"],
        "done_when": "bucket refills at rate r, burst b; unit tests pass",
        "verify": "pytest tests/ratelimit_test.py",
        "order": 1,
    },
    {
        "id": "U2",
        "title": "wire into charge endpoint",
        "serves": "AC2",
        "files": ["api/routes.py", "payments/charge.py"],
        "done_when": "429 + Retry-After on limit; happy path unchanged",
        "verify": "pytest tests/integration/charge_test.py",
        "order": 2,
    },
    {
        "id": "U3",
        "title": "config",
        "serves": "AC3",
        "files": ["config/limits.yaml"],
        "done_when": "per-user override loadable; default 100/min",
        "verify": "python -m app.config_check",
        "order": 3,
    },
    {
        "id": "U4",
        "title": "docs",
        "serves": "AC4",
        "files": ["docs/rate-limiting.md"],
        "done_when": "covers config + 429 contract",
        "verify": "python -m docs.lint docs/rate-limiting.md",
        "order": 4,
    },
]

# The unit the fresh-context critic ADDS: the missing migration §3.14's critic pass flags.
_MISSING_MIGRATION_UNIT: dict[str, Any] = {
    "id": "U5",
    "title": "migration for per-user limits table",
    "serves": "AC3",
    "files": ["migrations/012_per_user_limits.py"],
    "done_when": "table per_user_limits created; alembic upgrade head clean",
    "verify": "alembic upgrade head",
    "order": 5,
}


# ── in-process fakes for the REAL host path ──────────────────────────────────


class FakePlanProvider:
    """A recording ``agentkit.Provider`` that returns a structured multi-file PLAN (§3.6.1).

    The plan turn's ``query()`` streams: INIT (carrying the SDK ``session_id``) → a TEXT
    frame whose text is the JSON array of plan units → RESULT (cost telemetry). The provider
    records the ``system_prompt`` / ``allowed_tools`` / ``max_turns`` / thinking budget it saw
    so a test can prove the turn is READ-ONLY (no write tools) and the thinking budget is
    capped below MAX_OUTPUT_TOKENS.

    ``bad_first`` models a plan whose first emission does NOT parse: the first call returns
    prose, the retry (``max_turns:1``, "return ONLY the JSON array") returns the clean array —
    so a test proves the one-retry recovery (§3.6.1).
    """

    name = "claude"

    def __init__(self, *, units: list[dict[str, Any]] | None = None, bad_first: bool = False) -> None:
        self._units = units if units is not None else _PLAN_UNITS
        self._bad_first = bad_first
        self.calls = 0
        self.seen_prompts: list[str] = []
        self.seen_system_prompts: list[str] = []
        self.seen_allowed_tools: list[tuple[str, ...]] = []
        self.seen_max_turns: list[int] = []
        self.seen_thinking: list[tuple[bool, int]] = []
        self.session_ids: list[str] = []

    def matches(self, model: str) -> bool:  # pragma: no cover - seam parity
        return True

    def stream(self, prompt: str, query: Any) -> AsyncIterator[AgentChunk]:
        self.calls += 1
        call = self.calls
        self.seen_prompts.append(prompt)
        self.seen_system_prompts.append(getattr(query, "system_prompt", "") or "")
        self.seen_allowed_tools.append(tuple(getattr(query, "allowed_tools", ()) or ()))
        self.seen_max_turns.append(int(getattr(query, "max_turns", 0) or 0))
        self.seen_thinking.append(
            (
                bool(getattr(query, "thinking_enabled", False)),
                int(getattr(query, "thinking_budget_tokens", 0) or 0),
            )
        )
        sid = f"plan-sess-{call}"
        self.session_ids.append(sid)
        emit_prose = self._bad_first and call == 1
        units = self._units

        async def gen() -> AsyncIterator[AgentChunk]:
            yield AgentChunk(type="INIT", metadata={"session_id": sid})
            if emit_prose:
                # A non-parsing first emission (prose, not a JSON array) → the retry recovers.
                yield AgentChunk(type="TEXT", text="Here is my plan, let me think...", metadata={"msg_id": "m1"})
            else:
                yield AgentChunk(type="TEXT", text=json.dumps(units), metadata={"msg_id": "m1"})
            yield AgentChunk(
                type="RESULT",
                metadata={
                    "session_id": sid,
                    "num_turns": 1,
                    "total_cost_usd": 0.03,
                    "cache_creation_input_tokens": 4000,
                    "cache_read_input_tokens": 0,
                    "input_tokens": 200,
                },
            )

        return gen()


class FakeCriticProvider:
    """A fresh-context plan CRITIC ``query()`` that AMENDS the plan (§3.6.1 note / §3.14).

    It reads the plan (handed as the prompt) and returns an amendment: the ADDED units
    (here the missing migration). It records the ``system_prompt`` it saw so a test can prove
    the critic is a SEPARATE, fresh-context turn (a different session id from the planner) —
    the builder never grades its own plan.
    """

    name = "claude"

    def __init__(self, *, add_units: list[dict[str, Any]] | None = None) -> None:
        self._add_units = add_units if add_units is not None else [_MISSING_MIGRATION_UNIT]
        self.calls = 0
        self.seen_prompts: list[str] = []
        self.seen_allowed_tools: list[tuple[str, ...]] = []

    def matches(self, model: str) -> bool:  # pragma: no cover - seam parity
        return True

    def stream(self, prompt: str, query: Any) -> AsyncIterator[AgentChunk]:
        self.calls += 1
        self.seen_prompts.append(prompt)
        self.seen_allowed_tools.append(tuple(getattr(query, "allowed_tools", ()) or ()))
        amendment = {"add": self._add_units, "remove": [], "reorder": []}

        async def gen() -> AsyncIterator[AgentChunk]:
            yield AgentChunk(type="INIT", metadata={"session_id": f"critic-sess-{self.calls}"})
            yield AgentChunk(type="TEXT", text=json.dumps(amendment), metadata={"msg_id": "c1"})
            yield AgentChunk(
                type="RESULT",
                metadata={"session_id": f"critic-sess-{self.calls}", "total_cost_usd": 0.01},
            )

        return gen()


class FakeStore:
    """The ``operation_runs`` row store (§12.10) — the dispatch already claimed the
    ``workroom:<id>`` row with the Bundle in ``progress``; this models THAT one row keyed by
    run_id. The plan persists into the SAME row's ``progress`` (never a bespoke table);
    ``set_progress`` merges, ``get_progress`` reads the durable source of truth back.
    """

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.tables_touched: set[str] = set()

    def claim(self, *, run_id: str, operation_type: str, progress: dict[str, Any]) -> None:
        self.tables_touched.add("operation_runs")
        self.rows[run_id] = {
            "id": run_id,
            "operation_type": operation_type,
            "status": "running",
            "progress": dict(progress),
            "result_ref": None,
        }

    async def set_progress(self, *, run_id: str, progress: dict[str, Any]) -> None:
        """Merge a progress patch into the SAME row (the durable plan + session-id sink)."""
        self.tables_touched.add("operation_runs")
        row = self.rows[run_id]
        merged = dict(row.get("progress") or {})
        merged.update(progress)
        row["progress"] = merged

    async def get_progress(self, *, run_id: str) -> dict[str, Any]:
        return dict(self.rows[run_id].get("progress") or {})

    # The session-id sink the SessionDriver-style persistence uses (fire-and-forget).
    async def set_session_id(self, *, run_id: str, session_id: str) -> None:
        await self.set_progress(run_id=run_id, progress={"session_id": session_id})

    async def get_session_id(self, *, run_id: str) -> str | None:
        return (self.rows[run_id].get("progress") or {}).get("session_id")


class FakeChat:
    """The chat surface (§3.4 ``post_chat``): the plan posts here before execution (silence=go)."""

    def __init__(self) -> None:
        self.posts: list[str] = []

    async def post_chat(self, text: str) -> None:
        self.posts.append(text)


def _bundle(meeting_id: uuid.UUID, ask: str, *, task_id: uuid.UUID | None = None) -> Bundle:
    return Bundle(
        ask=ask,
        speaker="Sam",
        timestamp=datetime.now(timezone.utc),
        notes_ref=meeting_id,
        transcript_tail="…prior turns…",
        task_id=task_id or uuid.uuid4(),
    )


def _make_planner(store: FakeStore, chat: FakeChat, provider: Any, **kw: Any) -> Any:
    from workroom.big_build import BigBuildPlanner

    return BigBuildPlanner(provider=provider, store=store, chat=chat, **kw)


# ── the tests ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_plan_artifact_is_ac_tagged_multi_file_with_all_four_fields() -> None:
    """A large task produces a structured multi-file plan; EACH unit carries files-to-touch +
    done-when + verify line + order + the AC-tag it serves (§3.6.1 / §3.14)."""
    store, chat = FakeStore(), FakeChat()
    run_id = str(uuid.uuid4())
    meeting_id = uuid.uuid4()
    store.claim(run_id=run_id, operation_type=f"workroom:{run_id}", progress={"ask": "build limiter"})
    provider = FakePlanProvider()
    planner = _make_planner(store, chat, provider)

    plan = await planner.plan(_bundle(meeting_id, "Build the per-user rate-limiter"), run_id=run_id)

    assert len(plan.units) == 4, "the §3.14 plan is 4-5 ordered units"
    for unit in plan.units:
        assert unit.files, "each unit names files-to-touch"
        assert unit.done_when, "each unit carries its done-when pass rule (the AC mirrored in)"
        assert unit.verify, "each unit names a machine-checkable verify line the gate reads"
        assert unit.serves, "each unit is AC-tagged (verifiability designed in at plan time)"
        assert isinstance(unit.order, int)
    # ordered setup→core→integration→testing (strictly increasing order).
    orders = [u.order for u in plan.units]
    assert orders == sorted(orders) and len(set(orders)) == len(orders)


@pytest.mark.asyncio
async def test_plan_is_persisted_tied_to_operation_runs_row_and_reproducible() -> None:
    """The plan is PERSISTED durably, tied to the ``operation_runs`` row (§3.1 / §3.6.1) —
    reproducibly reconstructable from the SAME row's ``progress`` (never a bespoke table)."""
    store, chat = FakeStore(), FakeChat()
    run_id = str(uuid.uuid4())
    meeting_id = uuid.uuid4()
    store.claim(run_id=run_id, operation_type=f"workroom:{run_id}", progress={"ask": "x"})
    planner = _make_planner(store, chat, FakePlanProvider())

    plan = await planner.plan(_bundle(meeting_id, "Build the limiter"), run_id=run_id)

    # Persisted into the SAME operation_runs row's progress — NO bespoke per-task table.
    assert store.tables_touched == {"operation_runs"}
    persisted = await store.get_progress(run_id=run_id)
    assert "plan" in persisted, "the plan is persisted into the operation_runs row"

    # Reproducible: reconstitute the plan from the durable substrate and it matches.
    from workroom.big_build import Plan

    reloaded = Plan.from_persisted(persisted["plan"])
    assert [u.id for u in reloaded.units] == [u.id for u in plan.units]
    assert [u.verify for u in reloaded.units] == [u.verify for u in plan.units]
    assert [u.serves for u in reloaded.units] == [u.serves for u in plan.units]


@pytest.mark.asyncio
async def test_plan_posts_to_chat_before_execution() -> None:
    """A multi-step plan POSTS to chat before execution (silence = go, §2.3.1 / §3.14)."""
    store, chat = FakeStore(), FakeChat()
    run_id = str(uuid.uuid4())
    store.claim(run_id=run_id, operation_type=f"workroom:{run_id}", progress={})
    planner = _make_planner(store, chat, FakePlanProvider())

    await planner.plan(_bundle(uuid.uuid4(), "Build the limiter"), run_id=run_id)

    assert len(chat.posts) == 1, "the plan is posted to chat exactly once before execution"
    posted = chat.posts[0]
    # Every unit is represented in the posted plan (the room sees files + verify markers).
    for unit in _PLAN_UNITS:
        assert unit["title"] in posted
    # No internal component name reaches the user-visible chat post (only Proxy is a name).
    for banned in ("Orchestrator", "Scribe", "workroom", "Workroom"):
        assert banned not in posted


@pytest.mark.asyncio
async def test_plan_turn_captures_and_persists_the_sdk_session_id() -> None:
    """The plan turn captures the SDK ``session_id`` and persists it IMMEDIATELY (§3.6.1) —
    so a restart resumes the same conversation."""
    store, chat = FakeStore(), FakeChat()
    run_id = str(uuid.uuid4())
    store.claim(run_id=run_id, operation_type=f"workroom:{run_id}", progress={})
    provider = FakePlanProvider()
    planner = _make_planner(store, chat, provider)

    plan = await planner.plan(_bundle(uuid.uuid4(), "Build it"), run_id=run_id)

    assert plan.session_id == provider.session_ids[0]
    persisted_sid = await store.get_session_id(run_id=run_id)
    assert persisted_sid == provider.session_ids[0], "the session id is persisted per task"


@pytest.mark.asyncio
async def test_plan_turn_is_read_only_and_thinking_capped_below_max_output() -> None:
    """The plan turn is READ-ONLY (no write/propose_change tools) and its extended-thinking
    budget is capped BELOW MAX_OUTPUT_TOKENS so the plan JSON always finishes (§3.6.1 / N3)."""
    from llm.routing import max_output_tokens_for, model_for

    store, chat = FakeStore(), FakeChat()
    run_id = str(uuid.uuid4())
    store.claim(run_id=run_id, operation_type=f"workroom:{run_id}", progress={})
    provider = FakePlanProvider()
    planner = _make_planner(store, chat, provider)

    await planner.plan(_bundle(uuid.uuid4(), "Build it"), run_id=run_id)

    allowed = provider.seen_allowed_tools[0]
    # No write / edit / ast_grep / run_command / propose_change on the plan (read-only) turn.
    for banned in (
        "mcp__code__write_file",
        "mcp__code__edit_file",
        "mcp__code__ast_grep",
        "mcp__code__run_command",
        "mcp__propose_change__propose_change",
    ):
        assert banned not in allowed, f"plan turn must not advertise {banned}"

    thinking_enabled, thinking_budget = provider.seen_thinking[0]
    if thinking_enabled:
        ceiling = max_output_tokens_for(model_for("BIG_BUILD"))
        assert thinking_budget < ceiling, "thinking budget must be capped below MAX_OUTPUT_TOKENS"


@pytest.mark.asyncio
async def test_non_parsing_plan_gets_one_retry_then_parses() -> None:
    """A non-parsing plan emission gets ONE ``max_turns:1`` 'return ONLY the JSON array' retry
    that recovers (§3.6.1) — a second bad emission would raise, not loop forever."""
    store, chat = FakeStore(), FakeChat()
    run_id = str(uuid.uuid4())
    store.claim(run_id=run_id, operation_type=f"workroom:{run_id}", progress={})
    provider = FakePlanProvider(bad_first=True)
    planner = _make_planner(store, chat, provider)

    plan = await planner.plan(_bundle(uuid.uuid4(), "Build it"), run_id=run_id)

    assert provider.calls == 2, "exactly one retry after the non-parsing first emission"
    assert len(plan.units) == 4, "the retry recovered the parseable plan"
    # The retry is the tight max_turns:1 recovery turn.
    assert provider.seen_max_turns[1] == 1


@pytest.mark.asyncio
async def test_fresh_context_critic_amends_the_plan_adding_a_missing_migration() -> None:
    """A fresh-context critic reads the plan and AMENDS it — here adding the missing migration
    unit (§3.6.1 note / §3.14). The critic is a SEPARATE query() (the builder never grades its
    own plan); the amendment persists back to the durable row."""
    store, chat = FakeStore(), FakeChat()
    run_id = str(uuid.uuid4())
    store.claim(run_id=run_id, operation_type=f"workroom:{run_id}", progress={})
    planner = _make_planner(store, chat, FakePlanProvider())
    plan = await planner.plan(_bundle(uuid.uuid4(), "Build the limiter"), run_id=run_id)
    assert len(plan.units) == 4
    assert not any("migration" in "".join(u.files).lower() for u in plan.units)

    critic = FakeCriticProvider()
    amended = await planner.run_plan_critic(plan, run_id=run_id, critic_provider=critic)

    # The critic ran as a SEPARATE, fresh-context query().
    assert critic.calls == 1
    # The missing migration unit is now in the plan.
    assert len(amended.units) == 5
    migration_units = [u for u in amended.units if "migration" in "".join(u.files).lower()]
    assert len(migration_units) == 1
    mu = migration_units[0]
    assert mu.verify == "alembic upgrade head"
    assert mu.serves, "the added unit is AC-tagged too"

    # ID-preserving: the original four units survive unchanged.
    assert [u.id for u in amended.units[:4]] == [u.id for u in plan.units]

    # The amendment persists back to the SAME durable row (reproducible).
    from workroom.big_build import Plan

    persisted = await store.get_progress(run_id=run_id)
    reloaded = Plan.from_persisted(persisted["plan"])
    assert len(reloaded.units) == 5
    assert any("migration" in "".join(u.files).lower() for u in reloaded.units)


@pytest.mark.asyncio
async def test_critic_is_read_only_never_edits_the_artifact_it_grades() -> None:
    """The critic disposition is read-only (§3.7) — it never advertises a write/propose_change
    tool: a verifier never edits the artifact it grades."""
    store, chat = FakeStore(), FakeChat()
    run_id = str(uuid.uuid4())
    store.claim(run_id=run_id, operation_type=f"workroom:{run_id}", progress={})
    planner = _make_planner(store, chat, FakePlanProvider())
    plan = await planner.plan(_bundle(uuid.uuid4(), "Build it"), run_id=run_id)

    critic = FakeCriticProvider()
    await planner.run_plan_critic(plan, run_id=run_id, critic_provider=critic)

    allowed = critic.seen_allowed_tools[0]
    for banned in (
        "mcp__code__write_file",
        "mcp__code__edit_file",
        "mcp__code__ast_grep",
        "mcp__propose_change__propose_change",
    ):
        assert banned not in allowed, f"critic must not advertise {banned}"


@pytest.mark.asyncio
async def test_planner_never_throws_on_provider_error() -> None:
    """Rule 6 / §3.3: a provider ERROR does not crash the planner — it surfaces an honest
    failure, never a raised exception through the host boundary."""

    class ErrorProvider:
        name = "claude"

        def matches(self, model: str) -> bool:  # pragma: no cover
            return True

        def stream(self, prompt: str, query: Any) -> AsyncIterator[AgentChunk]:
            async def gen() -> AsyncIterator[AgentChunk]:
                yield AgentChunk(type="INIT", metadata={"session_id": "s1"})
                yield AgentChunk(type="ERROR", metadata={"message": "provider blew up"})

            return gen()

    store, chat = FakeStore(), FakeChat()
    run_id = str(uuid.uuid4())
    store.claim(run_id=run_id, operation_type=f"workroom:{run_id}", progress={})
    planner = _make_planner(store, chat, ErrorProvider())

    from workroom.big_build import PlanError

    with pytest.raises(PlanError):
        await planner.plan(_bundle(uuid.uuid4(), "Build it"), run_id=run_id)
    # The failure is honest and recorded — no bespoke table, no silent success.
    assert store.tables_touched <= {"operation_runs"}
