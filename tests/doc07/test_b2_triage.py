"""B2 — triage. Criteria: AC-PME-01, AC-PME-01-NEG, AC-PME-06, AC-PME-06-NEG."""
from __future__ import annotations

import copy
import uuid

import pytest
from harness.post_meeting.config import PostMeetingConfig
from harness.post_meeting.models import Tier
from harness.post_meeting.triage import (
    DRAFT_TIER_CONDITIONS,
    TRIAGE_SCHEMA,
    apply_tier_floor,
    build_prompt,
    coerce_tier,
    run_triage,
)

from libs.llm.src.llm.structured import StructuredOutputError, StructuredResult

from ._support import FakeActionItem, FakeFinalNotes

pytestmark = pytest.mark.asyncio

MEETING = uuid.uuid4()
CFG = PostMeetingConfig()


# ── seam doubles ──────────────────────────────────────────────────────────
async def passthrough_call_external(op, *, service, **kwargs):
    """Stands in for libs.http.call_external: still CALLS op, never replaces it.

    The sealed mock_boundary forbids replacing the seam. This double preserves the
    seam's contract (invoke the op, wrap the result) so the request construction and the
    caller both really execute; only the network is absent. The cassette-backed `reality`
    rung is what proves the real vendor accepts the request, and it is not run here.
    """
    return await op()


def caller_returning(verdicts, *, cost=0.01):
    async def _c(*, model, prompt, output_schema, tool_name):
        assert output_schema is TRIAGE_SCHEMA
        assert tool_name == "emit_triage"
        return StructuredResult(data={"verdicts": verdicts}, total_cost_usd=cost)

    return _c


def caller_raising(exc):
    async def _c(*, model, prompt, output_schema, tool_name):
        raise exc

    return _c


def _v(ref, tier, met=True, **extra):
    return {"item_ref": ref, "tier": tier, "draft_conditions_met": met, **extra}


ITEMS = [("m#0", "bump the retry ceiling on checkout to 5"), ("m#1", "look at the spike")]


# ── AC-PME-01 · exactly one tier per item; close record unmodified ────────
async def test_ac_pme_01_every_item_receives_exactly_one_tier():
    caller = caller_returning(
        [_v("m#0", "ticket+plan+draft"), _v("m#1", "question", met=False)]
    )
    res = await run_triage(
        ITEMS, caller=caller, call_external=passthrough_call_external, config=CFG
    )
    assert res.ok
    assert set(res.verdicts) == {"m#0", "m#1"}
    assert res.untiered == []
    assert res.verdicts["m#0"].tier is Tier.TICKET_PLAN_DRAFT
    assert res.verdicts["m#1"].tier is Tier.QUESTION


async def test_ac_pme_01_duplicate_verdict_cannot_upgrade_an_item():
    caller = caller_returning(
        [_v("m#0", "ticket"), _v("m#0", "ticket+plan+draft"), _v("m#1", "ticket")]
    )
    res = await run_triage(
        ITEMS, caller=caller, call_external=passthrough_call_external, config=CFG
    )
    assert res.verdicts["m#0"].tier is Tier.TICKET, "a duplicate verdict upgraded an item"


async def test_ac_pme_01_verdict_for_unknown_item_is_discarded():
    caller = caller_returning(
        [_v("m#0", "ticket"), _v("m#1", "ticket"), _v("m#99", "ticket+plan+draft")]
    )
    res = await run_triage(
        ITEMS, caller=caller, call_external=passthrough_call_external, config=CFG
    )
    assert set(res.verdicts) == {"m#0", "m#1"}


async def test_ac_pme_01_close_record_is_not_touched_by_triage():
    notes = FakeFinalNotes(action_items=[FakeActionItem("a", owner="Sam")])
    before = copy.deepcopy(notes)
    caller = caller_returning([_v("m#0", "ticket"), _v("m#1", "ticket")])
    await run_triage(ITEMS, caller=caller, call_external=passthrough_call_external, config=CFG)
    assert notes == before


async def test_ac_pme_01_no_items_is_not_an_error():
    res = await run_triage(
        [], caller=caller_raising(AssertionError("must not call")),
        call_external=passthrough_call_external, config=CFG,
    )
    assert res.ok and res.verdicts == {}


# ── AC-PME-01-NEG · failed / malformed call assigns no tier ───────────────
@pytest.mark.negative
@pytest.mark.parametrize(
    "exc",
    [
        StructuredOutputError("500 from vendor"),
        StructuredOutputError("request timed out"),
        StructuredOutputError("no emit_triage tool_use block"),
    ],
    ids=["5xx", "timeout", "truncated"],
)
async def test_ac_pme_01_neg_failed_call_assigns_no_tier(exc):
    res = await run_triage(
        ITEMS, caller=caller_raising(exc), call_external=passthrough_call_external, config=CFG
    )
    assert res.ok is False
    assert res.verdicts == {}, "a tier was assigned from a failed response"
    assert set(res.untiered) == {"m#0", "m#1"}


@pytest.mark.negative
async def test_ac_pme_01_neg_failure_does_not_default_to_informational():
    res = await run_triage(
        ITEMS,
        caller=caller_raising(StructuredOutputError("boom")),
        call_external=passthrough_call_external,
        config=CFG,
    )
    tiers = [v.tier for v in res.verdicts.values()]
    assert Tier.INFORMATIONAL not in tiers
    assert tiers == [], "failure must assign nothing, not a silent default tier"


@pytest.mark.negative
async def test_ac_pme_01_neg_malformed_payload_yields_no_tiers():
    async def bad(*, model, prompt, output_schema, tool_name):
        return StructuredResult(data={"not_verdicts": []})

    res = await run_triage(
        ITEMS, caller=bad, call_external=passthrough_call_external, config=CFG
    )
    assert res.ok is False
    assert res.verdicts == {}
    assert set(res.untiered) == {"m#0", "m#1"}


@pytest.mark.negative
async def test_ac_pme_01_neg_untiered_items_are_reported_for_no_planning():
    """An item with no usable verdict must be visibly untiered so B4 never plans it."""
    caller = caller_returning([_v("m#0", "ticket")])  # m#1 missing entirely
    res = await run_triage(
        ITEMS, caller=caller, call_external=passthrough_call_external, config=CFG
    )
    assert res.untiered == ["m#1"]
    assert "m#1" not in res.verdicts


# ── AC-PME-06 · failing a draft-tier condition drops at least one tier ────
@pytest.mark.parametrize("failed", list(DRAFT_TIER_CONDITIONS))
async def test_ac_pme_06_each_violated_condition_drops_a_tier(failed):
    """One labelled item per condition the criterion names; none may keep the draft tier."""
    caller = caller_returning(
        [_v("m#0", "ticket+plan+draft", met=False, failed_conditions=[failed])]
    )
    res = await run_triage(
        [ITEMS[0]], caller=caller, call_external=passthrough_call_external, config=CFG
    )
    v = res.verdicts["m#0"]
    assert v.tier is not Tier.TICKET_PLAN_DRAFT
    assert v.tier is Tier.TICKET_PLAN, "must drop exactly one tier, not collapse"
    assert v.dropped is True


async def test_ac_pme_06_conditions_met_keeps_the_draft_tier():
    caller = caller_returning([_v("m#0", "ticket+plan+draft", met=True)])
    res = await run_triage(
        [ITEMS[0]], caller=caller, call_external=passthrough_call_external, config=CFG
    )
    assert res.verdicts["m#0"].tier is Tier.TICKET_PLAN_DRAFT


async def test_ac_pme_06_floor_only_ever_lowers():
    for tier in Tier:
        out, _ = apply_tier_floor(tier, draft_conditions_met=False, draft_tier_enabled=False)
        assert list(Tier).index(out) <= list(Tier).index(tier), "the floor raised a tier"


async def test_ac_pme_06_draft_tier_disabled_removes_the_tier_entirely():
    off = PostMeetingConfig(draft_tier_enabled=False)
    caller = caller_returning([_v("m#0", "ticket+plan+draft", met=True)])
    res = await run_triage(
        [ITEMS[0]], caller=caller, call_external=passthrough_call_external, config=off
    )
    assert res.verdicts["m#0"].tier is Tier.TICKET_PLAN
    assert "ticket+plan+draft" not in build_prompt([ITEMS[0]], draft_tier_enabled=False)


async def test_ac_pme_06_no_rule_table_maps_item_text_to_a_tier():
    """Law 4: the tier comes from the model, never from code keyed on item text.

    Two items with completely different text and the SAME model verdict must land on the
    same tier — if code were pattern-matching the text, they would diverge.
    """
    for text in ("bump the retry ceiling to 5", "rewrite the entire retry architecture"):
        caller = caller_returning([_v("x", "ticket+plan+draft", met=True)])
        res = await run_triage(
            [("x", text)], caller=caller, call_external=passthrough_call_external, config=CFG
        )
        assert res.verdicts["x"].tier is Tier.TICKET_PLAN_DRAFT
    # The conditions are prompt text, not code.
    prompt = build_prompt(ITEMS, draft_tier_enabled=True)
    for cond in DRAFT_TIER_CONDITIONS:
        assert cond in prompt


# ── AC-PME-06-NEG · failed/malformed judge never grants the draft tier ────
@pytest.mark.negative
async def test_ac_pme_06_neg_out_of_enum_tier_is_rejected_not_coerced():
    caller = caller_returning(
        [_v("m#0", "ticket+draft", met=True), _v("m#1", "DRAFT!!", met=True)]
    )
    res = await run_triage(
        ITEMS, caller=caller, call_external=passthrough_call_external, config=CFG
    )
    assert res.verdicts == {}, "an out-of-enum tier was coerced instead of rejected"
    assert set(res.untiered) == {"m#0", "m#1"}
    assert coerce_tier("ticket+draft") is None
    assert coerce_tier("ticket+plan+draft") is Tier.TICKET_PLAN_DRAFT


@pytest.mark.negative
async def test_ac_pme_06_neg_unreadable_condition_report_drops_the_tier():
    """A non-boolean draft_conditions_met is treated as NOT met — drops, never raises."""
    caller = caller_returning([{"item_ref": "m#0", "tier": "ticket+plan+draft",
                               "draft_conditions_met": "yes"}])
    res = await run_triage(
        [ITEMS[0]], caller=caller, call_external=passthrough_call_external, config=CFG
    )
    assert res.verdicts["m#0"].tier is Tier.TICKET_PLAN


@pytest.mark.negative
async def test_ac_pme_06_neg_no_sandbox_is_started_during_triage():
    """Static, not behavioural.

    Handing run_triage a ForbiddenSandbox it never receives would assert nothing — the
    counter would read 0 whether or not the product misbehaved. The real property is that
    triage has no sandbox to reach: the module neither imports nor names one.
    """
    from ._support import assert_no_code_reference

    assert_no_code_reference(
        "services/harness/src/harness/post_meeting/triage.py",
        ("sandbox", "e2b", "propose_change", "staged_drafts"),
    )

    caller = caller_returning([_v("m#0", "ticket+plan+draft", met=True)])
    res = await run_triage(
        [ITEMS[0]], caller=caller, call_external=passthrough_call_external, config=CFG
    )
    assert res.ok


@pytest.mark.negative
async def test_ac_pme_06_neg_failed_judge_never_grants_draft_tier():
    for exc in (StructuredOutputError("5xx"), StructuredOutputError("timeout")):
        res = await run_triage(
            ITEMS, caller=caller_raising(exc),
            call_external=passthrough_call_external, config=CFG,
        )
        assert all(v.tier is not Tier.TICKET_PLAN_DRAFT for v in res.verdicts.values())
        assert res.verdicts == {}
