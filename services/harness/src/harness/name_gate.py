"""Address detection — the mechanical name-gate + tiny disambiguator (§3.1).

The front gate, and mostly free. Two mechanical scanners key on the physics of
the two ask sockets, and NOTHING in this module calls a model on its own:

* **spoken** — the transcript feed is scanned mechanically for the name
  (``"Proxy"``) as a *word*. A name-hit triggers exactly ONE tiny disambiguation
  call ("addressed to me, or 'proxy server'?" — pennies, only on hits). Confirmed
  → the caller fires the ack reflex and wakes Proxy. The disambiguation itself is
  a bounded model call, but it is **injected** (``disambiguate``) — this module
  constructs no SDK client and imports no provider, so the mechanical scan is
  provider-free and self-contained.
* **typed** — chat is scanned for the ``@proxy`` token. Chat ``@proxy`` needs NO
  disambiguation — it wakes directly, with no model call (§3.1; NOT-done clause:
  "disambiguating chat @proxy").

**Proxy's own transcribed speech is never a hit.** Doc 02 marks Proxy's line with
the reserved ``PROXY_SPEAKER`` label; the guard is *speaker-scoped* (keyed on the
label, not the content), so even when Proxy literally says its own name it never
self-triggers, and it never reaches the (paid) disambiguator. Every human line is
still scanned — the suppression is one speaker, never a content filter.

This is a **reflex-layer physics component, not a judgment turn** — the wake turn
(§3.2) is what runs *after* a confirmed address. There is no ``if event_type →
action`` here; the gate only answers "was Proxy addressed?" and hands the ask
text + speaker forward.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from transport.chat import has_proxy_token
from transport.hearing import PROXY_SPEAKER
from transport.signals import ChatMessage, Transcript

#: The name Proxy answers to, matched mechanically as a whole word (case-insensitive).
#: A word boundary (``\b``) means ``proxying`` / ``proxyserver`` are NOT hits, so the
#: paid disambiguator never fires on unrelated tech talk — but ``the proxy server``
#: IS a token hit (the mechanical scan cannot know intent; that is the disambiguator's
#: job, §3.1).
_NAME = "proxy"
_NAME_WORD_RE = re.compile(rf"\b{_NAME}\b", re.IGNORECASE)

#: The address socket a verdict came in on — recorded for parity with the two ask
#: sockets (voice vs chat). Never a judgment; just provenance.
Source = Literal["voice", "chat"]

#: A disambiguator is any callable taking the spoken line and returning True iff the
#: name-hit was an address to Proxy (vs. the common-noun "proxy server"). Injected so
#: this module stays provider-free; the real one is a bounded ('pennies') model call.
Disambiguate = Callable[[str], bool]


def scan_transcript(line: Transcript) -> bool:
    """Mechanically scan one spoken line for the name — no model, no side effects.

    Returns True iff the line is a name-hit that WARRANTS disambiguation:
      * the speaker is NOT Proxy (speaker-scoped self-guard — Proxy's own marked
        speech is never a hit, Doc 02); and
      * the name ``"proxy"`` appears as a whole word (case-insensitive).

    ``"the proxy server config"`` returns True here (it IS a token hit); intent is
    resolved by the tiny disambiguation call, not by this scan. ``"proxying"`` /
    ``"proxyserver"`` return False (substring, not a word).
    """
    # Speaker-scoped self-guard FIRST: Proxy's own transcribed speech is inert data
    # (CANONICAL §10.3), never a hit and never disambiguated — even if it says the name.
    if line.speaker == PROXY_SPEAKER:
        return False
    return _NAME_WORD_RE.search(line.words) is not None


def scan_chat(msg: ChatMessage) -> bool:
    """Mechanically scan one chat line for the ``@proxy`` address token — no model.

    Chat addressing is the token only (§3.1 — "chat for ``@proxy``"): a bare
    mention of the word "proxy" in prose is NOT a chat address. Reuses Doc 02's
    canonical ``@proxy`` token recognizer (case-insensitive) so the two layers
    never drift.
    """
    return bool(has_proxy_token(msg.message))


@dataclass(frozen=True)
class AddressingVerdict:
    """The gate's answer: was Proxy addressed, and (if so) the ask to carry forward.

    ``wake`` is the single decision the caller acts on (fire the ack reflex + the
    wake turn). ``source`` records the socket. ``text`` / ``speaker`` carry the ask
    verbatim forward so the ack + wake turn consume it without re-parsing — dropped
    to empty strings on a no-wake verdict.
    """

    wake: bool
    source: Source
    text: str = ""
    speaker: str = ""


class NameGate:
    """The front gate: mechanical scan → (spoken hits only) one disambiguation call.

    ``disambiguate`` is the injected bounded model call, invoked ONLY on a spoken
    mechanical name-hit — never on chat, never on a line with no hit, never on
    Proxy's own speech. The gate holds no situational judgment beyond "addressed
    or not"; the wake turn (§3.2) does the thinking.
    """

    def __init__(self, *, disambiguate: Disambiguate) -> None:
        self._disambiguate = disambiguate

    def on_transcript(self, line: Transcript) -> AddressingVerdict:
        """Decide whether a spoken line addresses Proxy.

        Mechanical scan → if (and only if) it is a name-hit, ONE disambiguation
        call resolves "addressed to me, or 'proxy server'?". Confirmed → wake.
        """
        if not scan_transcript(line):
            # No name-hit (or Proxy's own speech): free, and the model never fires.
            return AddressingVerdict(wake=False, source="voice")
        # Exactly one tiny disambiguation call, only on a spoken hit (pennies).
        addressed = bool(self._disambiguate(line.words))
        if not addressed:
            return AddressingVerdict(wake=False, source="voice")
        return AddressingVerdict(wake=True, source="voice", text=line.words, speaker=line.speaker)

    def on_chat(self, msg: ChatMessage) -> AddressingVerdict:
        """Decide whether a chat line addresses Proxy — ``@proxy`` wakes directly.

        Chat ``@proxy`` needs NO disambiguation (§3.1): a token hit wakes with no
        model call at all.
        """
        if not scan_chat(msg):
            return AddressingVerdict(wake=False, source="chat")
        return AddressingVerdict(wake=True, source="chat", text=msg.message, speaker=msg.sender)
