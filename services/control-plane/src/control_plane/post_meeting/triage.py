"""B2 — triage. One structured call on the sonnet seat assigns exactly one tier per item.

Criteria: **AC-PME-01, AC-PME-01-NEG** (every item gets exactly one tier; the close record
is unmodified) and **AC-PME-06, AC-PME-06-NEG** (failing any draft-tier condition drops at
least one tier).

**Law 4 is the shape of this module.** Doc 07 §3.1: *"The conditions for the draft tier are
policy, not mechanism … They live in the prompt as the standard a task must meet."* So the
five conditions are prompt text, and the model returns both the tier it chose and whether
each condition held. Code owns only the floor: if the model claims the draft tier while
reporting an unmet condition, code drops a tier. That is not a situation→action mapping —
it is a consistency check on the model's own self-report, and it is what makes AC-PME-06
enforceable without a rule table keyed on item text.

The other direction is never allowed: nothing here ever raises a tier the model did not
ask for. Every failure path in this module moves DOWN the tier order or assigns nothing —
"when in doubt, drop a tier" (§3.1), because an unhelpful ticket costs a glance and an
unwanted draft costs trust.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from libs.llm.src.llm.structured import (
    CallExternal,
    StructuredCaller,
    StructuredOutputError,
    generate_structured,
)

from .config import PostMeetingConfig, load_post_meeting_config
from .models import Tier, drop_one_tier

log = logging.getLogger(__name__)

#: Doc 07 §4 names the MODEL ("claude-sonnet-4-6 through libs/llm") but not a seat, and a
#: new seat is not available: sealed doc00 criterion AC-CFG-002
#: (tests/doc00/test_m05_cfg.py:150) pins ``llm.routing.SEATS`` to exactly eight members.
#: Of the three sonnet seats — ANSWER, ORCHESTRATOR, WORKROOM — this is ORCHESTRATOR:
#: ANSWER is answering a human's live question, WORKROOM is the sandboxed builder, and
#: post-meeting triage is Proxy's own judgment about the meeting. Resolved to a model id
#: by libs.llm.routing, so this names a role and never a model id.
TRIAGE_SEAT = "ORCHESTRATOR"
_TOOL_NAME = "emit_triage"

#: Doc 07 §3.1's draft-tier conditions, verbatim in intent. PROMPT text, not a rule table.
DRAFT_TIER_CONDITIONS: tuple[str, ...] = (
    "a concrete scope",
    "a clear area of the codebase",
    "small enough to review in one sitting",
    "no material ambiguity",
    "a stated way to tell it worked",
)

TRIAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item_ref": {"type": "string"},
                    "tier": {
                        "type": "string",
                        "enum": [t.value for t in Tier],
                    },
                    "draft_conditions_met": {"type": "boolean"},
                    "failed_conditions": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "rationale": {"type": "string"},
                },
                "required": ["item_ref", "tier", "draft_conditions_met"],
            },
        }
    },
    "required": ["verdicts"],
}


@dataclass(frozen=True)
class TriageVerdict:
    item_ref: str
    tier: Tier
    rationale: str = ""
    #: True when the model's proposed tier was lowered by the §3.1 floor.
    dropped: bool = False


@dataclass
class TriageResult:
    verdicts: dict[str, TriageVerdict] = field(default_factory=dict)
    error: Optional[BaseException] = None
    #: item_refs the model returned no usable verdict for. These get NO tier.
    untiered: list[str] = field(default_factory=list)
    total_cost_usd: Optional[float] = None

    @property
    def ok(self) -> bool:
        return self.error is None


def build_prompt(items: list[tuple[str, str]], *, draft_tier_enabled: bool) -> str:
    """Render the triage prompt. The tier standard lives HERE, not in code.

    When the draft tier is disabled the prompt does not mention it AT ALL — not in the
    tier list and not in the explanation. Describing a tier the model is not offered
    invites it to ask for one anyway, which the §3.1 floor would then have to drop; better
    that the option never exists in the prompt.
    """
    tiers = "\n".join(
        f"  - {t.value}"
        for t in Tier
        if draft_tier_enabled or t is not Tier.TICKET_PLAN_DRAFT
    )
    lines = [
        "You are triaging the action items from a meeting that has just ended.",
        "Assign EXACTLY ONE tier to each item. The available tiers, least to most:",
        tiers,
        "",
    ]
    if draft_tier_enabled:
        lines += [
            f"The most consequential tier, {Tier.TICKET_PLAN_DRAFT.value}, means real "
            "code will be drafted.",
            "An item qualifies for it ONLY if ALL of these hold:",
            "\n".join(f"  - {c}" for c in DRAFT_TIER_CONDITIONS),
            "",
            "For each item report the tier you chose, whether ALL of the above held",
            "(draft_conditions_met), and which failed (failed_conditions).",
            "",
        ]
    else:
        lines += [
            "For each item report the tier you chose. Set draft_conditions_met to false;",
            "no drafting tier is available in this room.",
            "",
        ]
    lines += [
        "When in doubt, choose the lower tier: an unhelpful ticket costs a glance,",
        "an unwanted draft costs trust.",
        "",
        "Items:",
    ]
    lines += [f"  [{ref}] {text}" for ref, text in items]
    return "\n".join(lines)


def coerce_tier(raw: Any) -> Optional[Tier]:
    """Map a model-supplied tier string onto the enum, or reject it.

    An out-of-enum value returns ``None`` — it is REJECTED, never coerced to the nearest
    member. Coercion is how a hallucinated "ticket+draft" would silently become
    ``ticket+plan+draft`` (AC-PME-06-NEG asserts it does not).
    """
    if not isinstance(raw, str):
        return None
    try:
        return Tier(raw.strip())
    except ValueError:
        return None


def apply_tier_floor(
    tier: Tier, *, draft_conditions_met: bool, draft_tier_enabled: bool
) -> tuple[Tier, bool]:
    """The §3.1 floor. Only ever lowers. Returns ``(tier, was_dropped)``."""
    dropped = False
    if tier is Tier.TICKET_PLAN_DRAFT and not draft_tier_enabled:
        tier = drop_one_tier(tier)
        dropped = True
    if tier is Tier.TICKET_PLAN_DRAFT and not draft_conditions_met:
        tier = drop_one_tier(tier)
        dropped = True
    return tier, dropped


async def run_triage(
    items: list[tuple[str, str]],
    *,
    caller: StructuredCaller,
    call_external: CallExternal,
    config: Optional[PostMeetingConfig] = None,
    seat: str = TRIAGE_SEAT,
) -> TriageResult:
    """Assign one tier per item. Never raises; never assigns a tier it did not receive.

    ``items`` is ``[(item_ref, text), …]``. The close object is NOT passed in and is never
    touched — B1 already read it, and AC-PME-01 asserts the record stays byte-identical.
    """
    cfg = config if config is not None else load_post_meeting_config()
    result = TriageResult()
    if not items:
        return result

    try:
        structured = await generate_structured(
            seat=seat,
            prompt=build_prompt(items, draft_tier_enabled=cfg.draft_tier_enabled),
            output_schema=TRIAGE_SCHEMA,
            caller=caller,
            call_external=call_external,
            tool_name=_TOOL_NAME,
        )
    except StructuredOutputError as exc:
        # No tier is assigned on a failed call. Not "informational", not a default —
        # nothing (AC-PME-01-NEG). The items stay EXTRACTED and are never planned.
        log.warning("triage call failed; no tiers assigned: %s", exc)
        result.error = exc
        result.untiered = [ref for ref, _ in items]
        return result

    result.total_cost_usd = structured.total_cost_usd
    raw_verdicts = structured.data.get("verdicts")
    if not isinstance(raw_verdicts, list):
        result.error = StructuredOutputError("triage payload carried no verdicts array")
        result.untiered = [ref for ref, _ in items]
        return result

    seen: dict[str, TriageVerdict] = {}
    known = {ref for ref, _ in items}
    for raw in raw_verdicts:
        if not isinstance(raw, dict):
            continue
        ref = raw.get("item_ref")
        if not isinstance(ref, str) or ref not in known:
            # A verdict for an item we did not ask about is discarded, never applied to
            # a neighbouring item.
            continue
        tier = coerce_tier(raw.get("tier"))
        if tier is None:
            continue  # out-of-enum: rejected, leaves the item untiered
        met = raw.get("draft_conditions_met")
        if not isinstance(met, bool):
            met = False  # unreadable self-report is treated as NOT met — drops, never raises
        tier, dropped = apply_tier_floor(
            tier, draft_conditions_met=met, draft_tier_enabled=cfg.draft_tier_enabled
        )
        rationale = raw.get("rationale")
        # First verdict per item wins; a duplicate cannot upgrade an item.
        seen.setdefault(
            ref,
            TriageVerdict(
                item_ref=ref,
                tier=tier,
                rationale=rationale if isinstance(rationale, str) else "",
                dropped=dropped,
            ),
        )

    result.verdicts = seen
    result.untiered = [ref for ref, _ in items if ref not in seen]
    return result
