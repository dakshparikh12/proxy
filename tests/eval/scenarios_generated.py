"""The generated PLAN-QUALITY scenario pool — schema, validation, loader.

Scenario generation is OFFLINE-AHEAD: ``tests/eval/generate_scenarios.py`` mints
scenarios on the Claude Max subscription into the committed
``tests/eval/plan_scenarios.json`` fixture, and every run afterward is a
DETERMINISTIC replay of that fixture — the pool is versioned evidence, never
re-rolled at test time. This module owns the schema those scenarios satisfy,
the validator the generator and the loader both enforce, and the loader the
runner consumes.

Ask classes span easiest→hardest, per the founder's bar ("test its ability on
ANY task"):

* ``quick-answer`` — conversational/trivial; the plan should be near-empty.
* ``grounded-lookup`` — one code fact; grep + bounded read; ``file:line`` cited.
* ``meeting-control`` — mute / post-to-chat; the transport verb is recorded
  mechanically (``require_transport``).
* ``sandbox-exec`` — "run it and tell me"; judged against
  ``no_sandbox_behavior`` when no E2B sandbox is mounted (the battery's honest
  no-exec mode).
* ``research-style`` — a multi-file walk-through of the request path.
* ``clarify`` — genuinely ambiguous; the first turn opens with the fork, then
  the un-prefixed ``follow_up`` reply resolves it.
* ``concurrent`` — ``second_ask`` lands back-to-back with no drain between.
* ``reconnect`` — the ask references discussion Proxy verifiably missed (its
  notes contain the gap, not the content); honesty about the gap is the bar.
* ``cant-do`` — prod restarts / deploys / prod metrics; honest decline.
* ``multi-step-build`` — sketch a concrete change across files (no write access
  is mounted; claiming to have applied anything is a failure).
* ``pr-draft`` — SCAFFOLDED ONLY (``SCAFFOLDED_CLASSES``): ``in_meeting``
  exposes no DRAFT_TOOLS yet — see the TODO in ``plan_trace.LATENCY_BOUNDS``.

The pool targets HUNDREDS of asks (the generator mints on demand); the committed
fixture carries the initial mint. Ask texts are UNIQUE pool-wide — the runner's
turn→ask attribution keys on the volatile "You were addressed:" suffix.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "ASK_CLASSES",
    "GENERATABLE_CLASSES",
    "POOL_PATH",
    "SCAFFOLDED_CLASSES",
    "TRANSPORT_VERBS",
    "PlanScenario",
    "load_pool",
    "scenario_from_dict",
    "validate_scenario_dict",
]

#: Every ask class the machinery knows (easiest → hardest, then the scaffold).
ASK_CLASSES: tuple[str, ...] = (
    "quick-answer",
    "grounded-lookup",
    "meeting-control",
    "sandbox-exec",
    "research-style",
    "clarify",
    "concurrent",
    "reconnect",
    "cant-do",
    "multi-step-build",
    "pr-draft",
)

#: Classes the product cannot exercise yet — kept in the taxonomy, excluded from
#: generation and the runner. TODO(DRAFT-TOOL): un-scaffold ``pr-draft`` when
#: ``in_meeting`` ships DRAFT_TOOLS (none exist as of 2026-07-29).
SCAFFOLDED_CLASSES: tuple[str, ...] = ("pr-draft",)

GENERATABLE_CLASSES: tuple[str, ...] = tuple(
    c for c in ASK_CLASSES if c not in SCAFFOLDED_CLASSES
)

#: The fake meeting transport's verb vocabulary (meeting_battery.FakeMeetingTransport).
TRANSPORT_VERBS: tuple[str, ...] = ("mute", "unmute", "post_chat", "send_dm")

#: The committed fixture the generator writes and the loader reads.
POOL_PATH: Path = Path(__file__).resolve().parent / "plan_scenarios.json"

_PROXY_WORD = re.compile(r"\bproxy\b", re.IGNORECASE)

#: Judge criteria shorter than this cannot describe a real behavioral bar.
_MIN_CRITERIA_CHARS = 40


@dataclass(frozen=True, slots=True)
class PlanScenario:
    """One minted scenario: a short run-up, one ask, and its judge criteria.

    ``context`` is (speaker, text) chatter fed before the ask — it must never
    wake the engine (the battery's deterministic-disambiguation convention: an
    addressed line STARTS with the wake name; chatter never does, though it may
    contain 'proxy' as a common noun). Class-specific fields: ``follow_up``
    (clarify — the un-prefixed reply), ``second_ask``/``second_expected_behavior``
    (concurrent), ``require_transport`` (meeting-control — verbs the fake
    transport must record), ``no_sandbox_behavior`` (sandbox-exec — the honest
    bar when no sandbox is mounted).
    """

    id: str
    ask_class: str
    ask: str
    expected_behavior: str
    context: tuple[tuple[str, str], ...] = ()
    follow_up: str | None = None
    second_ask: str | None = None
    second_expected_behavior: str | None = None
    require_transport: tuple[str, ...] = ()
    no_sandbox_behavior: str | None = None
    #: Free-form generator note (difficulty, topic) — never judged.
    tags: tuple[str, ...] = field(default=())


def _as_context(raw: Any) -> tuple[tuple[str, str], ...]:
    """Normalize context lines: ``[speaker, text]`` pairs or ``{speaker,text}`` dicts."""
    lines: list[tuple[str, str]] = []
    if not isinstance(raw, list):
        return ()
    for item in raw:
        if isinstance(item, dict):
            lines.append((str(item.get("speaker", "")), str(item.get("text", ""))))
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            lines.append((str(item[0]), str(item[1])))
    return tuple(lines)


def _as_transport(raw: Any) -> tuple[str, ...]:
    """Normalize ``require_transport``: a JSON array of verbs, or one bare verb
    string (a common generator shape — never split char-by-char)."""
    if isinstance(raw, str):
        return (raw.strip(),) if raw.strip() else ()
    if isinstance(raw, list):
        return tuple(str(v) for v in raw)
    return ()


def validate_scenario_dict(raw: dict[str, Any]) -> list[str]:
    """Every schema violation in ``raw`` (empty = valid).

    The rules encode the runner's physics: ask lines must wake (start with the
    wake name), context must not, clarify replies must be un-prefixed, control
    asks must name recordable verbs, exec asks must carry the no-sandbox bar.
    """
    errors: list[str] = []
    ask_class = str(raw.get("ask_class", ""))
    if ask_class not in ASK_CLASSES:
        errors.append(f"ask_class {ask_class!r} not in ASK_CLASSES")
        return errors  # class-specific rules below would be noise

    ask = str(raw.get("ask", ""))
    if not ask.strip():
        errors.append("empty ask")
    elif not ask.lower().lstrip().startswith("proxy"):
        errors.append(f"ask must START with the wake name: {ask[:80]!r}")
    if len(ask) > 500:
        errors.append(f"ask too long ({len(ask)} chars > 500)")

    expected = str(raw.get("expected_behavior", ""))
    if len(expected.strip()) < _MIN_CRITERIA_CHARS:
        errors.append(f"expected_behavior too short ({len(expected.strip())} chars)")

    context = _as_context(raw.get("context", []))
    if len(context) > 8:
        errors.append(f"context too long ({len(context)} lines > 8)")
    for speaker, text in context:
        if not speaker.strip() or not text.strip():
            errors.append(f"context line missing speaker/text: {(speaker, text)!r}")
        if text.lower().lstrip().startswith("proxy"):
            errors.append(f"context line starts with the wake name (would wake): {text[:80]!r}")

    follow_up = raw.get("follow_up")
    second_ask = raw.get("second_ask")
    if ask_class == "clarify":
        if not isinstance(follow_up, str) or not follow_up.strip():
            errors.append("clarify needs a follow_up reply")
        elif _PROXY_WORD.search(follow_up):
            errors.append(f"the clarify follow_up must be un-prefixed (no wake word): {follow_up[:80]!r}")
    elif follow_up:
        errors.append(f"follow_up is the clarify flow only (class {ask_class!r})")

    if ask_class == "concurrent":
        if not isinstance(second_ask, str) or not second_ask.strip():
            errors.append("concurrent needs a second_ask")
        elif not second_ask.lower().lstrip().startswith("proxy"):
            errors.append(f"second_ask must START with the wake name: {second_ask[:80]!r}")
        elif second_ask.strip() == ask.strip():
            errors.append("second_ask must differ from ask (attribution keys on the text)")
        second_expected = str(raw.get("second_expected_behavior", "") or "")
        if len(second_expected.strip()) < _MIN_CRITERIA_CHARS:
            errors.append("concurrent needs second_expected_behavior (real criteria)")
    elif second_ask:
        errors.append(f"second_ask is the concurrent flow only (class {ask_class!r})")

    transport = _as_transport(raw.get("require_transport"))
    if ask_class == "meeting-control":
        if not transport:
            errors.append("meeting-control needs require_transport verbs")
        for verb in transport:
            if verb not in TRANSPORT_VERBS:
                errors.append(f"unknown transport verb {verb!r} (know {TRANSPORT_VERBS})")
    elif transport:
        errors.append(f"require_transport is meeting-control only (class {ask_class!r})")

    if ask_class == "sandbox-exec":
        no_sandbox = str(raw.get("no_sandbox_behavior", "") or "")
        if len(no_sandbox.strip()) < _MIN_CRITERIA_CHARS:
            errors.append("sandbox-exec needs no_sandbox_behavior (the honest can't-run bar)")

    return errors


def scenario_from_dict(raw: dict[str, Any]) -> PlanScenario:
    """Build a :class:`PlanScenario` from a VALIDATED dict (see the validator)."""
    return PlanScenario(
        id=str(raw["id"]),
        ask_class=str(raw["ask_class"]),
        ask=str(raw["ask"]).strip(),
        expected_behavior=str(raw["expected_behavior"]).strip(),
        context=_as_context(raw.get("context", [])),
        follow_up=(str(raw["follow_up"]).strip() if raw.get("follow_up") else None),
        second_ask=(str(raw["second_ask"]).strip() if raw.get("second_ask") else None),
        second_expected_behavior=(
            str(raw["second_expected_behavior"]).strip()
            if raw.get("second_expected_behavior")
            else None
        ),
        require_transport=_as_transport(raw.get("require_transport")),
        no_sandbox_behavior=(
            str(raw["no_sandbox_behavior"]).strip() if raw.get("no_sandbox_behavior") else None
        ),
        tags=tuple(str(t) for t in (raw.get("tags") or [])),
    )


def load_pool(path: Path = POOL_PATH) -> tuple[PlanScenario, ...]:
    """Load + re-validate the committed pool (deterministic replay).

    Every scenario is re-validated on load — a hand-edited fixture that breaks
    the schema fails HERE, loudly, not as a silent misattribution mid-run. Ids
    and ask texts must be unique pool-wide (attribution physics).
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    raw_scenarios = data["scenarios"]
    if not isinstance(raw_scenarios, list):
        raise ValueError(f"{path}: 'scenarios' is not a list")
    pool: list[PlanScenario] = []
    seen_ids: set[str] = set()
    seen_asks: set[str] = set()
    for raw in raw_scenarios:
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: non-object scenario entry: {raw!r}")
        errors = validate_scenario_dict(raw)
        sid = str(raw.get("id", "<missing id>"))
        if not str(raw.get("id", "")).strip():
            errors.append("missing id")
        if errors:
            raise ValueError(f"{path}: scenario {sid}: " + "; ".join(errors))
        scenario = scenario_from_dict(raw)
        if scenario.id in seen_ids:
            raise ValueError(f"{path}: duplicate scenario id {scenario.id!r}")
        seen_ids.add(scenario.id)
        for text in (scenario.ask, scenario.second_ask or ""):
            key = " ".join(text.lower().split())
            if not key:
                continue
            if key in seen_asks:
                raise ValueError(
                    f"{path}: duplicate ask text (attribution needs uniqueness): {text[:80]!r}"
                )
            seen_asks.add(key)
        pool.append(scenario)
    return tuple(pool)
