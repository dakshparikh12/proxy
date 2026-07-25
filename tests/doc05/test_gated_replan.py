"""Doc 05 §3.6.3 / §2.3.1 / §3.3⑦⑧ / §3.13-step-9 — the GATED-REPLAN turn: a mid-run
correction rewrites the TARGET plan unit (ID-preserving) and changes the outcome WITHOUT a
restart; a task forced into a loop stops at the ≤2 replan cap with an honest partial +
receipts; the gated replan is ID-preserving and capped at 8 remaining.

The node ``workroom.gated-replan`` (``services/workroom/src/workroom/big_build.py`` — the
gated-replan turn + correction-into-plan rewrite + no-progress detector on
:class:`BigBuildExecutor`). It builds ON the sequential executor:

  * **Correction-into-the-plan (§2.3.1 / §3.3⑧ / §3.6.3).** A mid-task human correction
    ("cap at 100/min") rewrites the live plan's TARGET unit (matched by id/title —
    ID-PRESERVING) BEFORE that unit executes, so the unit builds with the corrected outcome.
    Execution CONTINUES — no restart, finished units are never redone. The SDK's crude native
    mid-turn interrupt-string is NOT used; the correction rewrites the plan artifact.
  * **No-progress detection + bounded replan ≤2 (§3.3⑦ / §3.13-step-9).** Output-hash /
    action-effect similarity over the last N turns: a task producing the SAME effect (no new
    read-back commit) turn after turn is looping. A bounded replan (≤2) fires; when the replan
    cap is hit the build STOPS with an HONEST PARTIAL (``status='partial'``) carrying the
    receipts so far — never a deadlock, never a silent claim of done.
  * **Gated replan turn (§3.6.3).** Only if the plan ≥3 steps: after each subtask a
    ``max_turns:1``, no-tools "given what you just did, is the rest still right?" turn —
    ID-preserving (title match), capped at 8 remaining, best-effort (parse fail → keep the
    existing plan).

These tests drive the REAL host path with in-process fakes (a fake worker provider against a
REAL temp git repo; a fake replan provider). e2b is NOT installed; the E2B template bake is
the flagged Phase-3 residual, never faked here. NOT done if a correction restarts the build,
if no-progress can loop unbounded, or if the replan drops/duplicates subtask ids.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from contracts import AgentChunk, Bundle

from .test_big_build import FakeChat, FakeStore, _PLAN_UNITS
from .test_sequential_build import (
    FakeWorkerProvider,
    RealGitSandbox,
    _bundle,
    _init_repo,
    _seed_plan,
    _unit_id_from_prompt,
)


# ── a worker provider whose per-unit commit reads the LIVE plan unit's done-when ──


class OutcomeAwareProvider(FakeWorkerProvider):
    """A worker provider whose committed artifact encodes the LIVE plan unit's ``done_when``.

    The executor renders the (possibly corrected) plan unit into the subtask prompt; this
    provider commits a file whose CONTENT is that unit's rendered done-when line. So a
    correction that rewrote the unit's ``done_when`` BEFORE this turn ran changes the OUTCOME
    (the committed bytes) — proving the correction changed the build, not just the plan text.
    """

    def stream(self, prompt: str, query: Any) -> AsyncIterator[AgentChunk]:
        self.calls += 1
        self.seen_resume.append(getattr(query, "resume", None))
        self.seen_max_turns.append(int(getattr(query, "max_turns", 0) or 0))
        self.seen_prompts.append(prompt)
        self.seen_allowed_tools.append(tuple(getattr(query, "allowed_tools", ()) or ()))
        unit_id = _unit_id_from_prompt(prompt)
        done_when = _done_when_from_prompt(prompt)
        sandbox = self._sandbox
        sid = self._session_id

        async def gen() -> AsyncIterator[AgentChunk]:
            yield AgentChunk(type="INIT", metadata={"session_id": sid})
            rel = f"unit_{unit_id}.txt"
            # The committed artifact ENCODES the live unit's done-when — a correction that
            # rewrote it upstream lands in these bytes (the OUTCOME), no restart.
            await sandbox.commit(
                unit_id=unit_id,
                message=f"{unit_id}: {done_when}",
                files={rel: f"done_when={done_when}\n"},
            )
            self.units_committed.append(unit_id)
            yield AgentChunk(
                type="TOOL_USE",
                metadata={"name": "mcp__code__write_file", "input": {"path": rel}},
            )
            yield AgentChunk(type="RESULT", metadata={"session_id": sid, "total_cost_usd": 0.01})

        return gen()


def _done_when_from_prompt(prompt: str) -> str:
    """Extract the done-when line the executor rendered for this unit (the live outcome)."""
    for line in prompt.splitlines():
        if line.startswith("Done when:"):
            return line.split(":", 1)[1].strip()
    return "?"


# ── a looping worker: commits the SAME artifact every turn (no NEW progress) ──────


class LoopingProvider(FakeWorkerProvider):
    """A worker that produces NO new progress turn after turn — the loop the no-progress
    detector must catch. Every turn it performs the SAME action (same commit subject + same
    touched path) so the host-observed action-effect signature over the last N turns is
    IDENTICAL → a loop, not progress. (A per-turn nonce keeps the raw git SHA distinct so the
    real repo accepts each commit; the detector keys on the EFFECT signature, not the SHA — a
    model spinning on a task produces a distinct commit each time yet zero real progress.)
    """

    def stream(self, prompt: str, query: Any) -> AsyncIterator[AgentChunk]:
        self.calls += 1
        self.seen_resume.append(getattr(query, "resume", None))
        self.seen_max_turns.append(int(getattr(query, "max_turns", 0) or 0))
        self.seen_prompts.append(prompt)
        self.seen_allowed_tools.append(tuple(getattr(query, "allowed_tools", ()) or ()))
        unit_id = _unit_id_from_prompt(prompt)
        sandbox = self._sandbox
        sid = self._session_id
        nonce = self.calls

        async def gen() -> AsyncIterator[AgentChunk]:
            yield AgentChunk(type="INIT", metadata={"session_id": sid})
            # SAME action (subject) + SAME touched path EVERY turn — the effect never changes.
            # A per-turn nonce comment lets the real repo accept a distinct commit while the
            # host-observed action-effect signature (subject + files) stays identical.
            await sandbox.commit(
                unit_id=unit_id,
                message="spinning: no new effect",
                files={"loop.txt": f"STUCK — same action every turn\n# nonce {nonce}\n"},
            )
            self.units_committed.append(unit_id)
            yield AgentChunk(
                type="TOOL_USE",
                metadata={"name": "mcp__code__write_file", "input": {"path": "loop.txt"}},
            )
            yield AgentChunk(type="RESULT", metadata={"session_id": sid, "total_cost_usd": 0.01})

        return gen()


# ── a replan provider: the max_turns:1, no-tools gated-replan turn ────────────────


class FakeReplanProvider:
    """The ``max_turns:1`` no-tools gated-replan turn (§3.6.3): "is the rest still right?".

    Records the ``max_turns`` + ``allowed_tools`` it saw (so a test proves the turn is
    max_turns:1 + no-tools) and returns an ID-preserving replan amendment (``remove`` a stuck
    unit / ``reorder`` the remaining). Never fabricates new ids beyond ``add``.
    """

    name = "claude"

    def __init__(self, *, amendment: dict[str, Any] | None = None) -> None:
        self._amendment = amendment if amendment is not None else {"add": [], "remove": [], "reorder": []}
        self.calls = 0
        self.seen_max_turns: list[int] = []
        self.seen_allowed_tools: list[tuple[str, ...]] = []

    def matches(self, model: str) -> bool:  # pragma: no cover - seam parity
        return True

    def stream(self, prompt: str, query: Any) -> AsyncIterator[AgentChunk]:
        self.calls += 1
        self.seen_max_turns.append(int(getattr(query, "max_turns", 0) or 0))
        self.seen_allowed_tools.append(tuple(getattr(query, "allowed_tools", ()) or ()))
        import json as _json

        amendment = self._amendment

        async def gen() -> AsyncIterator[AgentChunk]:
            yield AgentChunk(type="INIT", metadata={"session_id": "replan-sess"})
            yield AgentChunk(type="TEXT", text=_json.dumps(amendment), metadata={"msg_id": "r1"})
            yield AgentChunk(type="RESULT", metadata={"session_id": "replan-sess", "total_cost_usd": 0.0})

        return gen()


class FakeCorrections:
    """The mid-run correction source (§2.3.1) — a queue of human corrections the executor
    drains before each remaining unit. Each correction targets a plan unit (by id/title) and
    rewrites its outcome fields (``done_when`` / ``verify``) — ID-PRESERVING, no restart.

    Models "Sam says 'cap at 100/min'" mid-build: the correction lands in the live plan's
    target unit BEFORE that unit executes.
    """

    def __init__(self, corrections: list[dict[str, Any]] | None = None) -> None:
        self._pending = list(corrections or [])
        self.drained: list[dict[str, Any]] = []

    async def drain(self) -> list[dict[str, Any]]:
        out = list(self._pending)
        self._pending.clear()
        self.drained.extend(out)
        return out


def _make_executor(store: FakeStore, chat: FakeChat, provider: Any, sandbox: RealGitSandbox, **kw: Any) -> Any:
    from workroom.big_build import BigBuildExecutor

    return BigBuildExecutor(provider=provider, store=store, chat=chat, sandbox=sandbox, **kw)


# ══════════════════════════════════════════════════════════════════════════════
# 1 · CORRECTION-INTO-THE-PLAN — rewrites the target unit, changes the outcome, NO restart
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_correction_rewrites_target_unit_changes_outcome_without_restart(tmp_path: Path) -> None:
    """A mid-run correction ("cap at 100/min") rewrites the TARGET plan unit (U3, ID-preserving)
    and CHANGES the outcome without a restart (§2.3.1 / §3.6.3). The corrected U3 builds with
    the new done-when; U1/U2 (already finished) are NEVER redone; U4 continues after."""
    store, chat = FakeStore(), FakeChat()
    run_id = str(uuid.uuid4())
    _seed_plan(store, run_id, session_id="sess-corr")
    repo = _init_repo(tmp_path / "clone")
    sandbox = RealGitSandbox(repo)
    provider = OutcomeAwareProvider(sandbox=sandbox, session_id="sess-corr")

    # Sam's correction lands mid-build targeting U3 — rewrites its outcome (was "default 100/min"
    # in the seed plan; the correction caps it at 100/min explicitly and flips the verify line).
    corrections = FakeCorrections(
        [{
            "target": "U3",
            "done_when": "per-user override loadable; HARD CAP 100/min enforced",
            "verify": "python -m app.config_check --assert-cap=100",
        }]
    )
    executor = _make_executor(store, chat, provider, sandbox, corrections=corrections)

    result = await executor.run(_bundle("Build the per-user rate-limiter"), run_id=run_id)

    # No restart: each unit ran exactly ONCE, in order, and the whole build finished.
    assert provider.units_committed == ["U1", "U2", "U3", "U4"], "no restart — every unit ran once, in order"
    assert result.status in ("done", "completed")

    # The OUTCOME changed: U3's committed artifact carries the CORRECTED done-when, not the seed.
    u3_content = (repo / "unit_U3.txt").read_text()
    assert "HARD CAP 100/min enforced" in u3_content, "the correction changed the build OUTCOME, not just the plan text"
    assert "default 100/min" not in u3_content, "the seed (pre-correction) outcome did NOT land"

    # ID-PRESERVING: the live plan still has exactly U1..U4 (no dropped/duplicated ids); U3 kept
    # its id, its outcome fields were rewritten.
    from workroom.big_build import Plan

    persisted = await store.get_progress(run_id=run_id)
    live_plan = Plan.from_persisted(persisted["plan"])
    assert [u.id for u in live_plan.units] == ["U1", "U2", "U3", "U4"], "ID-preserving — no dropped/duplicated ids"
    u3 = next(u for u in live_plan.units if u.id == "U3")
    assert u3.done_when == "per-user override loadable; HARD CAP 100/min enforced"
    assert u3.verify == "python -m app.config_check --assert-cap=100"

    # The correction was consumed from the live plan (not a native SDK interrupt-string).
    assert corrections.drained, "the correction landed into the live plan artifact"


@pytest.mark.asyncio
async def test_correction_is_id_preserving_never_drops_or_duplicates_units(tmp_path: Path) -> None:
    """A correction rewrites the target unit IN PLACE — it never drops, duplicates, or reorders
    the other units (§3.6.3 ID-preserving). The set of ids is invariant across the correction."""
    store, chat = FakeStore(), FakeChat()
    run_id = str(uuid.uuid4())
    _seed_plan(store, run_id, session_id="sess-idp")
    repo = _init_repo(tmp_path / "clone")
    sandbox = RealGitSandbox(repo)
    provider = OutcomeAwareProvider(sandbox=sandbox, session_id="sess-idp")
    corrections = FakeCorrections([{"target": "U2", "done_when": "429 + Retry-After; cap 100/min"}])
    executor = _make_executor(store, chat, provider, sandbox, corrections=corrections)

    await executor.run(_bundle("Build it"), run_id=run_id)

    from workroom.big_build import Plan

    persisted = await store.get_progress(run_id=run_id)
    ids = [u.id for u in Plan.from_persisted(persisted["plan"]).units]
    assert ids == ["U1", "U2", "U3", "U4"], "ids invariant — none dropped, none duplicated"
    assert len(ids) == len(set(ids)), "no duplicate ids"


@pytest.mark.asyncio
async def test_correction_targeting_a_finished_unit_does_not_restart_it(tmp_path: Path) -> None:
    """A correction that names an ALREADY-FINISHED unit does NOT re-run it (no restart) — the
    correction only affects units still to come; finished units are immutable (§2.3.1 no restart)."""
    store, chat = FakeStore(), FakeChat()
    run_id = str(uuid.uuid4())
    _seed_plan(store, run_id, session_id="sess-fin")
    repo = _init_repo(tmp_path / "clone")
    sandbox = RealGitSandbox(repo)

    # U1, U2 already finished + checkpointed durably (resume state).
    from workroom.big_build import SubtaskCheckpoint

    done = [SubtaskCheckpoint(unit_id="U1", commits=[{"sha": "a" * 40, "subject": "U1"}], published=True),
            SubtaskCheckpoint(unit_id="U2", commits=[{"sha": "b" * 40, "subject": "U2"}], published=True)]
    await store.set_progress(
        run_id=run_id,
        progress={"build": {"status": "running", "units_done": ["U1", "U2"],
                            "checkpoints": [cp.to_dict() for cp in done]}},
    )
    provider = OutcomeAwareProvider(sandbox=sandbox, session_id="sess-fin")
    # The correction names U1 (already finished) — it must NOT restart U1.
    corrections = FakeCorrections([{"target": "U1", "done_when": "REWRITTEN — must not run"}])
    executor = _make_executor(store, chat, provider, sandbox, corrections=corrections)

    await executor.run(_bundle("Build it"), run_id=run_id)

    # Only the UNFINISHED units ran — U1/U2 were NOT redone.
    assert provider.units_committed == ["U3", "U4"], "finished units are never restarted by a correction"


# ══════════════════════════════════════════════════════════════════════════════
# 2 · NO-PROGRESS DETECTION + BOUNDED REPLAN ≤2 → HONEST PARTIAL WITH RECEIPTS
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_loop_stops_at_replan_cap_with_honest_partial_and_receipts(tmp_path: Path) -> None:
    """A task forced into a loop STOPS at the ≤2 replan cap with an HONEST PARTIAL + the receipts
    so far (§3.3⑦ / §3.13-step-9). The no-progress detector catches the identical-effect loop,
    fires a bounded replan (≤2), and when the cap is hit returns ``status='partial'`` — never a
    deadlock (unbounded loop) and never a silent claim of done."""
    store, chat = FakeStore(), FakeChat()
    run_id = str(uuid.uuid4())
    _seed_plan(store, run_id, session_id="sess-loop")
    repo = _init_repo(tmp_path / "clone")
    sandbox = RealGitSandbox(repo)
    # Every turn commits the SAME effect → no new progress → a loop.
    provider = LoopingProvider(sandbox=sandbox, session_id="sess-loop")
    # The replan turn can't unstick it (returns an empty amendment each time) → the cap trips.
    replan = FakeReplanProvider(amendment={"add": [], "remove": [], "reorder": []})
    executor = _make_executor(store, chat, provider, sandbox, replan_provider=replan, replan_cap=2)

    result = await executor.run(_bundle("Build the impossible thing"), run_id=run_id)

    # HONEST PARTIAL — never a false done, never a hard failure hiding the work done.
    assert result.status == "partial", f"a capped loop returns an honest partial, got {result.status!r}"
    # RECEIPTS so far — the honest partial carries what actually happened (the real commits).
    assert result.checkpoints or result.units_done or (result.reason and "replan" in result.reason.lower()), \
        "the partial carries the receipts so far"
    assert result.reason and ("no progress" in result.reason.lower() or "replan" in result.reason.lower()), \
        "the partial names the no-progress/replan-cap reason plainly (Law 2)"

    # BOUNDED — the replan fired at most twice (≤2 cap), never unbounded. The loop did NOT
    # spin forever: the number of worker turns is bounded (never a deadlock).
    assert replan.calls <= 2, "bounded replan ≤2 — never an unbounded replan"
    assert provider.calls < 50, "the loop is bounded — it never spins forever (no deadlock)"


@pytest.mark.asyncio
async def test_no_progress_detector_does_not_false_trip_on_legit_progress(tmp_path: Path) -> None:
    """The no-progress detector must NOT false-trip on legitimately-progressing work (a risk the
    node names): a build where each unit lands a DISTINCT new effect runs to completion with ZERO
    replans (§3.3⑦ risk — 'must not false-trip on legitimately slow but progressing work')."""
    store, chat = FakeStore(), FakeChat()
    run_id = str(uuid.uuid4())
    _seed_plan(store, run_id, session_id="sess-ok")
    repo = _init_repo(tmp_path / "clone")
    sandbox = RealGitSandbox(repo)
    # Each unit lands a DISTINCT file/effect (real progress).
    provider = FakeWorkerProvider(sandbox=sandbox, session_id="sess-ok")
    replan = FakeReplanProvider()
    executor = _make_executor(store, chat, provider, sandbox, replan_provider=replan, replan_cap=2)

    result = await executor.run(_bundle("Build it"), run_id=run_id)

    assert result.status in ("done", "completed"), "legit progress runs to completion"
    assert result.units_done == [u["id"] for u in _PLAN_UNITS]
    assert replan.calls == 0, "no false-trip — zero replans on legitimately-progressing work"


@pytest.mark.asyncio
async def test_replan_never_deadlocks_on_persistent_loop(tmp_path: Path) -> None:
    """A persistent loop NEVER deadlocks — the executor always terminates (Law: never deadlock).
    Even if the replan can never unstick the task, the run RETURNS (a partial), it never hangs."""
    store, chat = FakeStore(), FakeChat()
    run_id = str(uuid.uuid4())
    _seed_plan(store, run_id, session_id="sess-dead")
    repo = _init_repo(tmp_path / "clone")
    sandbox = RealGitSandbox(repo)
    provider = LoopingProvider(sandbox=sandbox, session_id="sess-dead")
    replan = FakeReplanProvider(amendment={"add": [], "remove": [], "reorder": []})
    executor = _make_executor(store, chat, provider, sandbox, replan_provider=replan, replan_cap=2)

    # The mere fact this RETURNS (does not hang) is the anti-deadlock guarantee.
    result = await executor.run(_bundle("Never-ending task"), run_id=run_id)
    assert result.status in ("partial", "failed"), "a persistent loop terminates honestly, never deadlocks"


# ══════════════════════════════════════════════════════════════════════════════
# 3 · THE GATED-REPLAN TURN — max_turns:1, no-tools, ID-preserving, capped at 8 remaining
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_gated_replan_turn_is_max_turns_1_and_no_tools(tmp_path: Path) -> None:
    """The gated-replan turn is ``max_turns:1`` with NO tools (§3.6.3) — a cheap "is the rest
    still right?" check, never a full agent turn. It fires only when no-progress is detected."""
    store, chat = FakeStore(), FakeChat()
    run_id = str(uuid.uuid4())
    _seed_plan(store, run_id, session_id="sess-gate")
    repo = _init_repo(tmp_path / "clone")
    sandbox = RealGitSandbox(repo)
    provider = LoopingProvider(sandbox=sandbox, session_id="sess-gate")
    replan = FakeReplanProvider(amendment={"add": [], "remove": [], "reorder": []})
    executor = _make_executor(store, chat, provider, sandbox, replan_provider=replan, replan_cap=2)

    await executor.run(_bundle("Loop it"), run_id=run_id)

    assert replan.calls >= 1, "the gated-replan turn fired on the detected loop"
    assert all(mt == 1 for mt in replan.seen_max_turns), "the replan turn is max_turns:1 (§3.6.3)"
    assert all(t == () for t in replan.seen_allowed_tools), "the replan turn is no-tools (§3.6.3)"


@pytest.mark.asyncio
async def test_gated_replan_is_id_preserving_and_capped_at_8_remaining(tmp_path: Path) -> None:
    """The gated replan is ID-PRESERVING and CAPPED AT 8 remaining (§3.6.3). A replan that tries
    to drop/duplicate ids or exceed 8 remaining units is clamped — the invariant holds."""
    store, chat = FakeStore(), FakeChat()
    run_id = str(uuid.uuid4())
    _seed_plan(store, run_id, session_id="sess-cap8")
    repo = _init_repo(tmp_path / "clone")
    sandbox = RealGitSandbox(repo)
    provider = LoopingProvider(sandbox=sandbox, session_id="sess-cap8")

    # A replan that tries to ADD many new units (would blow past 8 remaining) — must be clamped.
    fat_add = [
        {"id": f"X{i}", "title": f"extra {i}", "serves": "AC?", "files": ["x.py"],
         "done_when": "d", "verify": "v", "order": 10 + i}
        for i in range(12)
    ]
    replan = FakeReplanProvider(amendment={"add": fat_add, "remove": [], "reorder": []})
    executor = _make_executor(store, chat, provider, sandbox, replan_provider=replan, replan_cap=2)

    await executor.run(_bundle("Loop it"), run_id=run_id)

    from workroom.big_build import Plan

    persisted = await store.get_progress(run_id=run_id)
    live = Plan.from_persisted(persisted["plan"])
    ids = [u.id for u in live.units]
    # ID-PRESERVING: no duplicate ids anywhere in the replanned plan.
    assert len(ids) == len(set(ids)), "gated replan never duplicates ids"
    # CAPPED at 8 REMAINING: the count of not-yet-finished units never exceeds 8 after a replan.
    done_ids = {cp["unit_id"] for cp in (persisted.get("build") or {}).get("checkpoints", [])}
    remaining = [i for i in ids if i not in done_ids]
    assert len(remaining) <= 8, f"gated replan caps remaining units at 8, got {len(remaining)}"


@pytest.mark.asyncio
async def test_replan_parse_failure_keeps_existing_plan_best_effort(tmp_path: Path) -> None:
    """A non-parsing replan verdict KEEPS the existing plan (§3.6.3 best-effort: "parse fail →
    keep the existing plan") — it never crashes and never corrupts the live plan."""

    class GarbageReplanProvider(FakeReplanProvider):
        def stream(self, prompt: str, query: Any) -> AsyncIterator[AgentChunk]:
            self.calls += 1
            self.seen_max_turns.append(int(getattr(query, "max_turns", 0) or 0))
            self.seen_allowed_tools.append(tuple(getattr(query, "allowed_tools", ()) or ()))

            async def gen() -> AsyncIterator[AgentChunk]:
                yield AgentChunk(type="INIT", metadata={"session_id": "r"})
                yield AgentChunk(type="TEXT", text="I'm not sure, maybe keep going?", metadata={"msg_id": "r"})
                yield AgentChunk(type="RESULT", metadata={"session_id": "r", "total_cost_usd": 0.0})

            return gen()

    store, chat = FakeStore(), FakeChat()
    run_id = str(uuid.uuid4())
    _seed_plan(store, run_id, session_id="sess-garbage")
    repo = _init_repo(tmp_path / "clone")
    sandbox = RealGitSandbox(repo)
    provider = LoopingProvider(sandbox=sandbox, session_id="sess-garbage")
    replan = GarbageReplanProvider(amendment={"add": [], "remove": [], "reorder": []})
    executor = _make_executor(store, chat, provider, sandbox, replan_provider=replan, replan_cap=2)

    # Best-effort: a garbage replan verdict does not crash; the run still terminates honestly.
    result = await executor.run(_bundle("Loop it"), run_id=run_id)
    from workroom.big_build import Plan

    persisted = await store.get_progress(run_id=run_id)
    live = Plan.from_persisted(persisted["plan"])
    # The existing plan's original ids survive a garbage replan (kept, not corrupted).
    assert {"U1", "U2", "U3", "U4"} <= {u.id for u in live.units}, "parse fail keeps the existing plan"
    assert result.status in ("partial", "failed"), "still terminates honestly"


@pytest.mark.asyncio
async def test_gated_replan_only_when_plan_has_at_least_3_steps(tmp_path: Path) -> None:
    """The gated replan fires ONLY if the plan has ≥3 steps (§3.6.3). A 2-unit plan never runs a
    replan turn even under a loop — the cheap self-scaling is reserved for real multi-step plans."""
    store, chat = FakeStore(), FakeChat()
    run_id = str(uuid.uuid4())
    # Seed a 2-unit plan (below the ≥3 gate).
    store.claim(run_id=run_id, operation_type=f"workroom:{run_id}", progress={"ask": "small"})
    from workroom.big_build import Plan, PlanUnit

    two = [PlanUnit.from_raw(u, fallback_order=i + 1) for i, u in enumerate(_PLAN_UNITS[:2])]
    store.rows[run_id]["progress"]["plan"] = Plan(units=two, session_id="sess-2", ask="small").to_persisted()
    store.rows[run_id]["progress"]["session_id"] = "sess-2"

    repo = _init_repo(tmp_path / "clone")
    sandbox = RealGitSandbox(repo)
    provider = LoopingProvider(sandbox=sandbox, session_id="sess-2")
    replan = FakeReplanProvider(amendment={"add": [], "remove": [], "reorder": []})
    executor = _make_executor(store, chat, provider, sandbox, replan_provider=replan, replan_cap=2)

    await executor.run(_bundle("Loop a 2-step plan"), run_id=run_id)

    assert replan.calls == 0, "the gated replan is gated on plan ≥3 steps — a 2-unit plan never replans"
