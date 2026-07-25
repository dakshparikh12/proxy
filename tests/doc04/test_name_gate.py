"""Doc 04 · §3.1 Address detection — the mechanical name-gate + tiny disambiguator.

Node: ``orchestrator.name-gate`` (build-new). Spec refs: 04-ORCHESTRATOR §3.1
(the front gate, mostly free) and §2 (the reactive flow / how an ask arrives).

§3.1, verbatim: "The transcript feed is scanned mechanically for the name
('Proxy') and chat for ``@proxy``. A name-hit triggers one tiny disambiguation
call ('addressed to me, or "proxy server"?' — pennies, only on hits). Confirmed
→ the ack reflex fires and Proxy wakes. Chat ``@proxy`` needs no disambiguation.
Proxy's own transcribed speech is never a hit (Doc 02 marks it)."

Node definition-of-done: "a mechanical scanner detects 'Proxy'/@proxy, fires the
disambiguator only on spoken hits, and 'the proxy server config' does NOT wake
Proxy while 'Proxy, can you...' does; Proxy's own marked speech never
self-triggers. NOT done: disambiguating chat @proxy, or firing the model on
every transcript line."

Node acceptance: "WHEN 'Proxy, would renaming chargeCard break anything?' is
spoken THE SYSTEM SHALL confirm the address via one disambiguation call and wake
Proxy; WHEN 'the proxy server config' is spoken THE SYSTEM SHALL NOT wake."

Invariants asserted here:
  * Proxy's own speech never self-triggers (speaker-scoped, keyed on the Doc 02
    ``PROXY_SPEAKER`` label).
  * disambiguation only on spoken name-hits (pennies) — the model is NEVER
    called on a line with no mechanical name-hit, and NEVER on chat.
  * the mechanical scan itself calls no model (self-contained, no SDK/provider).

Product imports live INSIDE the test bodies so this module COLLECTS clean and
FAILS red before ``services/harness/src/harness/name_gate.py`` exists.
"""
from __future__ import annotations

import pathlib

import pytest

from transport.hearing import PROXY_SPEAKER
from transport.signals import ChatMessage, Transcript

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_NAME_GATE_SRC = _ROOT / "services" / "harness" / "src" / "harness" / "name_gate.py"


class _Disambiguator:
    """A recording stand-in for the bounded ('pennies') disambiguation model call.

    It records every call and returns a pre-programmed verdict, so a test can
    assert BOTH the verdict-driven wake AND the call-count discipline (the model
    fires only on spoken name-hits, never on chat, never on a no-hit line).
    """

    def __init__(self, verdict: bool = True) -> None:
        self.verdict = verdict
        self.calls: list[str] = []

    def __call__(self, text: str) -> bool:
        self.calls.append(text)
        return self.verdict


# ── the mechanical scan (no LLM) ──────────────────────────────────────────────


def test_mechanical_scan_detects_spoken_name_hit() -> None:
    """A spoken line containing the name 'Proxy' as a word is a mechanical hit."""
    from harness.name_gate import scan_transcript

    hit = scan_transcript(Transcript(words="Proxy, can you check the retry logic?", speaker="Sam", t=1.0))
    assert hit is True


def test_mechanical_scan_is_case_insensitive() -> None:
    """The name match is case-insensitive ('proxy' / 'PROXY' still hit)."""
    from harness.name_gate import scan_transcript

    assert scan_transcript(Transcript(words="hey proxy are you there", speaker="Sam", t=1.0)) is True
    assert scan_transcript(Transcript(words="PROXY what's the status", speaker="Sam", t=1.0)) is True


def test_mechanical_scan_matches_name_as_a_word_not_a_substring() -> None:
    """'proxying' / 'proxyserver' is NOT a name-hit — the name is a whole word.

    A substring match would fire the (paid) disambiguator on unrelated tech talk;
    the mechanical gate keys on the name as a token so most lines cost nothing.
    """
    from harness.name_gate import scan_transcript

    assert scan_transcript(Transcript(words="we are proxying the request upstream", speaker="Sam", t=1.0)) is False
    assert scan_transcript(Transcript(words="the proxyserver crashed again", speaker="Sam", t=1.0)) is False


def test_proxy_server_common_noun_is_still_a_mechanical_hit_pending_disambiguation() -> None:
    """'the proxy server config' IS a mechanical name-hit ('proxy' is a word) —

    the mechanical scan cannot know intent; that is exactly what the tiny
    disambiguation call resolves (§3.1). The scan's job is only to find the token.
    """
    from harness.name_gate import scan_transcript

    assert scan_transcript(Transcript(words="the proxy server config is wrong", speaker="Sam", t=1.0)) is True


def test_mechanical_scan_no_name_no_hit() -> None:
    """A line with no 'proxy' token is not a hit — the disambiguator never fires."""
    from harness.name_gate import scan_transcript

    assert scan_transcript(Transcript(words="let's ship the checkout fix on Friday", speaker="Sam", t=1.0)) is False


def test_mechanical_scan_calls_no_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """The mechanical scan is self-contained — it imports/constructs no SDK/provider.

    Static floor: name_gate.py's scan path must not reference anthropic / the
    provider seam. We assert the source contains no SDK/provider symbol in the
    mechanical scan (the model is reached ONLY through the injected disambiguator
    seam, never constructed in-module).
    """
    src = _NAME_GATE_SRC.read_text(encoding="utf-8")
    lowered = src.lower()
    assert "anthropic" not in lowered, "mechanical name-gate must not construct an SDK client"
    assert "asyncanthropic" not in lowered
    # No provider/seam is imported or constructed in-module: the disambiguator is
    # an injected callable, so the mechanical scan is provider-free.
    assert "import anthropic" not in lowered
    assert "pick_provider" not in lowered


# ── Proxy's OWN speech is NEVER a hit (speaker-scoped self-guard) ──────────────


def test_proxy_own_speech_never_self_triggers() -> None:
    """A transcript line labelled with the Proxy speaker label is NEVER a hit.

    Even when Proxy literally says its own name, the speaker-scoped guard keys on
    ``speaker == PROXY_SPEAKER`` (Doc 02 marks it), so Proxy never wakes itself.
    """
    from harness.name_gate import scan_transcript

    said_by_proxy = Transcript(words="Proxy here — I found the retry logic", speaker=PROXY_SPEAKER, t=2.0)
    assert scan_transcript(said_by_proxy) is False


def test_proxy_own_speech_does_not_fire_disambiguator() -> None:
    """Proxy's own line short-circuits before the (paid) disambiguation call."""
    from harness.name_gate import NameGate

    disambig = _Disambiguator(verdict=True)
    gate = NameGate(disambiguate=disambig)

    verdict = gate.on_transcript(Transcript(words="Proxy, on it", speaker=PROXY_SPEAKER, t=3.0))

    assert verdict.wake is False
    assert disambig.calls == [], "Proxy's own speech must not reach the disambiguator"


# ── the gate: spoken name-hit → ONE disambiguation call ───────────────────────


def test_spoken_address_confirmed_wakes_via_one_disambiguation_call() -> None:
    """§3.1 acceptance (positive): 'Proxy, would renaming chargeCard break anything?'

    is a spoken name-hit → exactly ONE disambiguation call → confirmed → wake.
    """
    from harness.name_gate import NameGate

    disambig = _Disambiguator(verdict=True)
    gate = NameGate(disambiguate=disambig)

    line = Transcript(words="Proxy, would renaming chargeCard break anything?", speaker="Sam", t=4.0)
    verdict = gate.on_transcript(line)

    assert verdict.wake is True
    assert verdict.source == "voice"
    assert len(disambig.calls) == 1, "exactly one tiny disambiguation call on a spoken hit"


def test_spoken_proxy_server_not_confirmed_does_not_wake() -> None:
    """§3.1 acceptance (negative): 'the proxy server config' → hit → disambiguator

    says NOT addressed → Proxy does NOT wake. One call was made (it was a hit),
    but the verdict is no-wake.
    """
    from harness.name_gate import NameGate

    disambig = _Disambiguator(verdict=False)
    gate = NameGate(disambiguate=disambig)

    line = Transcript(words="the proxy server config is wrong", speaker="Sam", t=5.0)
    verdict = gate.on_transcript(line)

    assert verdict.wake is False
    assert len(disambig.calls) == 1, "a spoken name-hit is disambiguated exactly once"


def test_disambiguator_never_fires_on_a_no_name_line() -> None:
    """A spoken line with no name-hit never reaches the (paid) disambiguator."""
    from harness.name_gate import NameGate

    disambig = _Disambiguator(verdict=True)
    gate = NameGate(disambiguate=disambig)

    verdict = gate.on_transcript(Transcript(words="ship it Friday", speaker="Sam", t=6.0))

    assert verdict.wake is False
    assert disambig.calls == [], "no model call on a line with no mechanical name-hit"


# ── chat @proxy: a hit needs NO disambiguation ────────────────────────────────


def test_chat_at_proxy_wakes_without_disambiguation() -> None:
    """§3.1: chat '@proxy' needs no disambiguation — it wakes directly, no model call."""
    from harness.name_gate import NameGate

    disambig = _Disambiguator(verdict=True)
    gate = NameGate(disambiguate=disambig)

    verdict = gate.on_chat(ChatMessage(message="@proxy what's the retry policy?", sender="Sam"))

    assert verdict.wake is True
    assert verdict.source == "chat"
    assert disambig.calls == [], "chat @proxy must NOT fire the disambiguation model call"


def test_chat_at_proxy_is_case_insensitive() -> None:
    """'@Proxy' / '@PROXY' are the same address token (Doc 02 parity)."""
    from harness.name_gate import NameGate

    gate = NameGate(disambiguate=_Disambiguator(verdict=False))
    assert gate.on_chat(ChatMessage(message="@Proxy ping", sender="Sam")).wake is True
    assert gate.on_chat(ChatMessage(message="hey @PROXY status?", sender="Sam")).wake is True


def test_chat_without_token_does_not_wake_and_calls_no_model() -> None:
    """A chat line with no '@proxy' token is not an address — and no model fires.

    The mechanical name ('proxy' as prose, not the '@proxy' token) does NOT wake
    from chat: chat addressing is the token only (§3.1 — 'chat for @proxy'), and
    disambiguation is a spoken-only mechanism.
    """
    from harness.name_gate import NameGate

    disambig = _Disambiguator(verdict=True)
    gate = NameGate(disambiguate=disambig)

    verdict = gate.on_chat(ChatMessage(message="the proxy server timed out", sender="Sam"))

    assert verdict.wake is False
    assert disambig.calls == [], "chat is never disambiguated (§3.1)"


# ── the happy-arc end-to-end shape (J-09-S1) ──────────────────────────────────


def test_addressing_verdict_carries_the_confirmed_ask_text() -> None:
    """A confirmed address carries the spoken words + speaker forward to the ack/wake.

    (J-09-S1 happy arc: the confirmed address is what the ack reflex and the wake
    turn consume next; the gate must hand the ask text + speaker on, not drop it.)
    """
    from harness.name_gate import NameGate

    gate = NameGate(disambiguate=_Disambiguator(verdict=True))
    line = Transcript(words="Proxy, would renaming chargeCard break anything?", speaker="Sam", t=7.0)
    verdict = gate.on_transcript(line)

    assert verdict.wake is True
    assert verdict.text == "Proxy, would renaming chargeCard break anything?"
    assert verdict.speaker == "Sam"
