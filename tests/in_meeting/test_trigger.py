"""Acceptance battery for Task M2 — the engagement trigger (``in_meeting.trigger``).

The trigger is the cheap always-on detector that decides WHEN Proxy wakes —
never WHAT it does (SPEC §2 "The trigger", §3 "The continuous loop"). Four
sources: addressed-voice (word-boundary name scan + ONE injected disambiguation
call), addressed-chat (the ``@proxy`` token, no model), reply-to-question (the
pending-ask window), and worker-done (a pure tap).

This is a LABELED battery: the disambiguation hook is stubbed by a labeled
oracle (a deterministic dict of line -> addressed?), so what is measured is the
mechanical scan + the state machine — not a model. The oracle COUNTS its calls
and refuses (KeyError) any line it has no label for, which proves the paid hook
never fires off a mechanical name-hit. Per-class precision/recall are computed
over the battery (floor: precision >= 0.90, recall >= 0.85 per class; with
deterministic oracles a well-built trigger scores 1.0).
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
from collections.abc import Callable

import pytest

from in_meeting.notes import TranscriptLine
from in_meeting.trigger import (
    PENDING_ASK_LINE_BUDGET,
    PROXY_SPEAKER,
    ChatLine,
    Engagement,
    EngagementTrigger,
)

# ---------------------------------------------------------------------------
# The labeled disambiguation oracle
# ---------------------------------------------------------------------------

#: Ground-truth labels for every line that IS a mechanical name-hit: True iff a
#: human addressed Proxy (vs. the common-noun "proxy server"). The oracle knows
#: ONLY these lines — a disambiguation call on any other line raises KeyError,
#: which fails the test loudly: the hook must never fire on a non-hit.
_ORACLE_LABELS: dict[str, bool] = {
    # Addressed to Proxy (voice wakes).
    "Proxy, what's the retry logic in billing-worker?": True,
    "hey proxy can you pull up Friday's deploy diff": True,
    "PROXY — summarize the last five minutes for Dana.": True,
    "Could you check the failing test for us, Proxy?": True,
    "Proxy, hold on — before you answer, check the deploy first.": True,
    # Common-noun / cross-talk (mechanical hits the oracle rejects).
    "The proxy server timed out again last night.": False,
    "We proxy the request through the edge gateway.": False,
    "Is the reverse proxy config in the infra repo?": False,
}

#: Cross-talk lines (mechanical hits, labeled False) used as the "intervening"
#: lines that spend the pending-ask budget without consuming the arm.
_CROSS_TALK: list[str] = [
    "The proxy server timed out again last night.",
    "We proxy the request through the edge gateway.",
    "Is the reverse proxy config in the infra repo?",
]


class CountingOracle:
    """The injected disambiguator: deterministic labels + a call counter."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, text: str) -> bool:
        self.calls.append(text)
        return _ORACLE_LABELS[text]  # KeyError = called on a non-hit line = a bug.


def _fresh() -> tuple[CountingOracle, EngagementTrigger]:
    oracle = CountingOracle()
    return oracle, EngagementTrigger(disambiguate=oracle)


def _line(text: str, speaker: str = "Marcus", t: float = 0.0) -> TranscriptLine:
    return TranscriptLine(text=text, speaker=speaker, timestamp=t, end_of_turn=True)


# ---------------------------------------------------------------------------
# The battery: every class from the AC, several diverse examples each.
# Each case runs against a FRESH trigger (deterministic isolation) and states
# the exact wake source it expects ("none" = stay asleep = free).
# ---------------------------------------------------------------------------

_Run = Callable[[EngagementTrigger], "Engagement | None"]


def _voice(text: str, speaker: str) -> _Run:
    return lambda trig: trig.on_transcript(_line(text, speaker))


def _chat(sender: str, message: str) -> _Run:
    return lambda trig: trig.on_chat(ChatLine(sender=sender, message=message))


def _armed_reply(reply_text: str, speaker: str) -> _Run:
    def run(trig: EngagementTrigger) -> Engagement | None:
        trig.arm_pending_ask()
        return trig.on_transcript(_line(reply_text, speaker))

    return run


def _armed_reply_after_own_question(reply_text: str, speaker: str) -> _Run:
    def run(trig: EngagementTrigger) -> Engagement | None:
        trig.arm_pending_ask()
        # Proxy's own spoken question echoes back through the transcript stream:
        # inert (self-guard), and it must not spend or consume its own window.
        own = trig.on_transcript(_line("Which environment is this behind the proxy?", PROXY_SPEAKER))
        assert own is None, "Proxy's own line must never wake"
        return trig.on_transcript(_line(reply_text, speaker))

    return run


def _armed_window_lapses(probe_text: str, speaker: str) -> _Run:
    def run(trig: EngagementTrigger) -> Engagement | None:
        trig.arm_pending_ask()
        # Exactly the budget of intervening name-hit lines (all labeled False):
        # each spends the window without consuming it, then the arm goes stale.
        for i in range(PENDING_ASK_LINE_BUDGET):
            hit = trig.on_transcript(_line(_CROSS_TALK[i % len(_CROSS_TALK)], "Priya"))
            assert hit is None, "cross-talk rejected by the oracle must not wake"
        return trig.on_transcript(_line(probe_text, speaker))

    return run


def _armed_proxy_own_line(own_text: str) -> _Run:
    def run(trig: EngagementTrigger) -> Engagement | None:
        trig.arm_pending_ask()
        return trig.on_transcript(_line(own_text, PROXY_SPEAKER))

    return run


def _worker(worker_id: str, result: str) -> _Run:
    return lambda trig: trig.on_worker_done(worker_id, result)


#: (class-label, expected wake source or "none", scenario)
_BATTERY: list[tuple[str, str, _Run]] = [
    # addressed-voice → wake, source=voice.
    ("addressed-voice", "voice", _voice("Proxy, what's the retry logic in billing-worker?", "Priya")),
    ("addressed-voice", "voice", _voice("hey proxy can you pull up Friday's deploy diff", "Marcus")),
    ("addressed-voice", "voice", _voice("PROXY — summarize the last five minutes for Dana.", "Devon")),
    ("addressed-voice", "voice", _voice("Could you check the failing test for us, Proxy?", "Dana")),
    # not-addressed-idle → None (and, asserted below, zero disambiguator calls).
    ("not-addressed-idle", "none", _voice("Let's start with the incident review.", "Priya")),
    ("not-addressed-idle", "none", _voice("The retry queue backed up right after the deploy.", "Marcus")),
    ("not-addressed-idle", "none", _voice("I'll take the action item on the rollback.", "Devon")),
    ("not-addressed-idle", "none", _voice("Can we move standup to nine tomorrow?", "Dana")),
    # cross-talk / common-noun → mechanical hit, oracle says False → None.
    ("cross-talk", "none", _voice("The proxy server timed out again last night.", "Priya")),
    ("cross-talk", "none", _voice("We proxy the request through the edge gateway.", "Marcus")),
    ("cross-talk", "none", _voice("Is the reverse proxy config in the infra repo?", "Devon")),
    # substring forms → NOT even a mechanical hit (oracle must stay silent).
    ("cross-talk", "none", _voice("Proxying every call doubles the latency.", "Priya")),
    ("cross-talk", "none", _voice("The proxyserver box is out of rotation.", "Marcus")),
    # @proxy-chat → wake, source=chat, NO disambiguation.
    ("chat", "chat", _chat("Dana", "@proxy can you post the deploy checklist link")),
    ("chat", "chat", _chat("Marcus", "@Proxy what changed in billing-worker this week?")),
    # bare "proxy" in chat prose (and a mid-word @proxy) → None.
    ("chat", "none", _chat("Dana", "the proxy config is in infra/nginx.conf")),
    ("chat", "none", _chat("Priya", "mail the logs to oncall@proxyserver.dev please")),
    # reply-to-question within the window → wake, source=reply.
    ("reply", "reply", _armed_reply("Staging, not production.", "Priya")),
    ("reply", "reply", _armed_reply_after_own_question("The eu-west cluster.", "Devon")),
    # reply after the window closed → None.
    ("reply-lapsed", "none", _armed_window_lapses("So what do you all think?", "Marcus")),
    # worker-done → wake, source=worker, carrying the result.
    ("worker", "worker", _worker("w-42", "refactor branch pushed; tests green")),
    ("worker", "worker", _worker("w-7", "bisect finished: the regression enters at abc123")),
    # Proxy's own line — literally contains "proxy", even while armed → None.
    ("proxy-own-line", "none", _armed_proxy_own_line("I'm Proxy — should I revert the proxy config now?")),
    ("proxy-own-line", "none", _voice("The proxy service failed twice.", PROXY_SPEAKER)),
]


def _per_class_scores(rows: list[tuple[str, str]]) -> dict[str, tuple[float, float]]:
    """(expected, actual) rows → {class: (precision, recall)} over wake sources."""
    classes = {exp for exp, _ in rows} | {act for _, act in rows}
    scores: dict[str, tuple[float, float]] = {}
    for cls in classes:
        tp = sum(1 for exp, act in rows if exp == cls and act == cls)
        fp = sum(1 for exp, act in rows if exp != cls and act == cls)
        fn = sum(1 for exp, act in rows if exp == cls and act != cls)
        precision = tp / (tp + fp) if (tp + fp) else 1.0
        recall = tp / (tp + fn) if (tp + fn) else 1.0
        scores[cls] = (precision, recall)
    return scores


def test_battery_every_case_and_per_class_precision_recall() -> None:
    """Every battery case resolves to its labeled outcome; per-class floors hold."""
    rows: list[tuple[str, str]] = []
    failures: list[str] = []
    for label, expected, run in _BATTERY:
        _, trig = _fresh()
        engagement = run(trig)
        actual = engagement.source if engagement is not None else "none"
        rows.append((expected, actual))
        if actual != expected:
            failures.append(f"[{label}] expected {expected!r}, got {actual!r}")
    assert not failures, "battery mismatches:\n" + "\n".join(failures)

    for cls, (precision, recall) in _per_class_scores(rows).items():
        assert precision >= 0.90, f"class {cls!r} precision {precision:.2f} < 0.90"
        assert recall >= 0.85, f"class {cls!r} recall {recall:.2f} < 0.85"


# ---------------------------------------------------------------------------
# Exact-value contracts per source
# ---------------------------------------------------------------------------


def test_voice_wake_carries_the_ask_verbatim_after_one_disambiguation() -> None:
    """A confirmed name-hit wakes with the exact line + speaker; ONE oracle call."""
    oracle, trig = _fresh()
    engagement = trig.on_transcript(_line("Proxy, what's the retry logic in billing-worker?", "Priya", 12.4))

    assert engagement is not None
    assert engagement.source == "voice"
    assert engagement.text == "Proxy, what's the retry logic in billing-worker?"
    assert engagement.speaker == "Priya"
    assert oracle.calls == ["Proxy, what's the retry logic in billing-worker?"]


def test_common_noun_hit_is_disambiguated_away() -> None:
    """A mechanical hit the oracle rejects stays asleep — exactly one call, no wake."""
    oracle, trig = _fresh()
    assert trig.on_transcript(_line("The proxy server timed out again last night.", "Marcus")) is None
    assert oracle.calls == ["The proxy server timed out again last night."]


def test_substring_forms_never_reach_the_disambiguator() -> None:
    """``proxying`` / ``proxyserver`` are not word hits: no wake, zero oracle calls."""
    oracle, trig = _fresh()
    assert trig.on_transcript(_line("Proxying every call doubles the latency.", "Priya")) is None
    assert trig.on_transcript(_line("The proxyserver box is out of rotation.", "Devon")) is None
    assert oracle.calls == []


def test_chat_token_wakes_directly_with_no_model_call() -> None:
    """``@proxy`` in chat wakes source=chat carrying sender+message; oracle silent."""
    oracle, trig = _fresh()
    engagement = trig.on_chat(ChatLine(sender="Dana", message="@proxy can you post the deploy checklist link"))

    assert engagement is not None
    assert engagement.source == "chat"
    assert engagement.text == "@proxy can you post the deploy checklist link"
    assert engagement.speaker == "Dana"
    assert oracle.calls == []


def test_bare_proxy_in_chat_prose_is_not_an_address() -> None:
    """Chat addressing is the token only — prose mentions and mid-word forms stay asleep."""
    oracle, trig = _fresh()
    assert trig.on_chat(ChatLine(sender="Dana", message="the proxy config is in infra/nginx.conf")) is None
    assert trig.on_chat(ChatLine(sender="Priya", message="mail the logs to oncall@proxyserver.dev please")) is None
    assert oracle.calls == []


def test_reply_window_consumes_exactly_one_reply() -> None:
    """Armed → the next human line wakes as the reply; the one after stays asleep."""
    oracle, trig = _fresh()
    trig.arm_pending_ask()

    reply = trig.on_transcript(_line("Staging, not production.", "Priya", 41.0))
    assert reply is not None
    assert reply.source == "reply"
    assert reply.text == "Staging, not production."
    assert reply.speaker == "Priya"

    after = trig.on_transcript(_line("And the logs are in the usual bucket.", "Priya", 44.2))
    assert after is None, "the arm must be consumed by exactly one reply"
    assert oracle.calls == [], "an un-prefixed reply must not touch the disambiguator"


def test_reply_window_survives_intervening_hits_within_budget() -> None:
    """Name-hit lines are intervening: they spend the window but do not consume it."""
    assert PENDING_ASK_LINE_BUDGET >= 2, "budget must allow at least one intervening line"
    _, trig = _fresh()
    trig.arm_pending_ask()

    # One cross-talk hit (oracle: False) — no wake, the arm survives.
    assert trig.on_transcript(_line("The proxy server timed out again last night.", "Devon")) is None
    # One confirmed direct address — wakes as voice, the arm still survives.
    direct = trig.on_transcript(_line("Proxy, hold on — before you answer, check the deploy first.", "Priya"))
    assert direct is not None and direct.source == "voice"

    # The next un-prefixed human line is still the reply.
    reply = trig.on_transcript(_line("It's the staging cluster.", "Marcus"))
    assert reply is not None
    assert reply.source == "reply"
    assert reply.text == "It's the staging cluster."


def test_reply_window_lapses_after_line_budget() -> None:
    """After the budget of intervening lines the arm is stale: no reply wake."""
    _, trig = _fresh()
    trig.arm_pending_ask()
    for i in range(PENDING_ASK_LINE_BUDGET):
        assert trig.on_transcript(_line(_CROSS_TALK[i % len(_CROSS_TALK)], "Priya")) is None
    assert trig.on_transcript(_line("So what do you all think?", "Marcus")) is None


def test_proxy_own_lines_never_wake_and_never_spend_the_window() -> None:
    """Self-guard while armed: Proxy's lines are inert and leave the arm whole."""
    oracle, trig = _fresh()
    trig.arm_pending_ask()

    # More of Proxy's own lines than the whole budget — all inert, nothing spent.
    for i in range(PENDING_ASK_LINE_BUDGET + 2):
        own = trig.on_transcript(_line("I'm Proxy — should I revert the proxy config now?", PROXY_SPEAKER, float(i)))
        assert own is None, "Proxy's own line must never wake, even armed"
    assert oracle.calls == [], "Proxy's own lines must never reach the disambiguator"

    # The window is untouched: the first human line still wakes as the reply.
    reply = trig.on_transcript(_line("Yes — revert it.", "Priya"))
    assert reply is not None
    assert reply.source == "reply"


def test_worker_done_is_a_pure_tap_carrying_the_result() -> None:
    """on_worker_done always wakes, tagged worker, with the id + result verbatim."""
    oracle, trig = _fresh()
    engagement = trig.on_worker_done("w-42", "refactor branch pushed; tests green")

    assert engagement.source == "worker"
    assert engagement.worker_id == "w-42"
    assert engagement.result == "refactor branch pushed; tests green"
    assert oracle.calls == []
    # The tap is orthogonal to the transcript window: nothing became armed.
    assert trig.on_transcript(_line("Nice, thanks everyone.", "Dana")) is None


def test_engagement_is_frozen() -> None:
    """The signal is a value object — the loop can hold it without it shifting."""
    engagement = Engagement(source="worker", worker_id="w-1", result="done")
    with pytest.raises(dataclasses.FrozenInstanceError):
        engagement.result = "rewritten"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Idle = free, and the module stays self-contained
# ---------------------------------------------------------------------------


def test_idle_stream_is_zero_wakes_and_zero_model_calls() -> None:
    """N ordinary lines → no engagement and an untouched disambiguator (idle = free)."""
    chatter = [
        "Let's start with the incident review.",
        "The retry queue backed up right after the deploy.",
        "billing-worker owns that queue, I think.",
        "The consumer config changed in the same release.",
        "I can revert the config if we agree it's the cause.",
        "Agreed — revert it and watch the dashboards.",
    ]
    speakers = ["Priya", "Marcus", "Devon", "Dana"]
    oracle, trig = _fresh()

    wakes = 0
    for i in range(30):
        line = _line(chatter[i % len(chatter)], speakers[i % len(speakers)], float(i))
        if trig.on_transcript(line) is not None:
            wakes += 1
    assert wakes == 0, "an idle stream must never wake Proxy"
    assert oracle.calls == [], "an idle stream must never touch the disambiguator"


def test_trigger_module_is_self_contained_and_vendor_free() -> None:
    """Static proof: stdlib + the engine's own notes type only — no SDK, no vendor."""
    import in_meeting.trigger as trigger  # noqa: PLC0415

    source = inspect.getsource(trigger)

    allowed_import_roots = {"__future__", "re", "collections", "dataclasses", "typing", "in_meeting"}
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= allowed_import_roots, f"foreign import on the trigger path: {imported - allowed_import_roots}"

    lowered = source.lower()
    for pattern in ("claude_agent_sdk", "call_external", "http_client", "provider", "anthropic", "harness.", "transport."):
        assert pattern not in lowered, f"forbidden token {pattern!r} found in the trigger module"
