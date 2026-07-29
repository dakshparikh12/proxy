"""The engagement trigger — the cheap always-on WHEN-to-wake detector (Task M2).

SPEC §2/§3: two things never stop — the transcript accumulates as notes, and
this trigger watches for engagement. Idle is free: a line that engages nothing
returns ``None`` with no model touch anywhere. The trigger decides *when Proxy
wakes, never what it does* — it emits an :class:`Engagement` tagged with the
source that woke it and hands the ask forward verbatim; the loop does all the
thinking. No situation→action mapping lives here.

Four sources, all mechanical physics plus exactly one injected judgment seam:

* **voice** — a word-boundary scan of each spoken line for the name ``"proxy"``
  (case-insensitive; ``proxying`` / ``proxyserver`` are not hits). A name-hit
  triggers ONE awaited call to the *injected async* ``disambiguate`` hook
  ("addressed to me, or 'proxy server'?") — a bounded model call fits behind the
  seam now. Confirmed → wake. The hook is a plain async callable, so this
  module constructs no SDK client and imports nothing beyond the stdlib and the
  engine's own transcript type.
* **chat** — the ``@proxy`` token wakes directly, no model call. A bare word
  "proxy" in chat prose is not an address; the token only.
* **reply** — the pending-ask follow-up window: after Proxy asks a question the
  loop arms the window (:meth:`EngagementTrigger.arm_pending_ask`), and the next
  human line *without* the wake word counts as the reply — wake, and the arm is
  consumed. Name-hit lines are *intervening* (handled by the voice path); the
  arm survives at most :data:`PENDING_ASK_LINE_BUDGET` of them, then goes stale.
* **worker** — a pure tap: a finished background worker wakes the loop to
  deliver its result. No scan; an alarm clock.

**Proxy's own transcribed speech is never a hit.** The guard is speaker-scoped
(keyed on the reserved :data:`PROXY_SPEAKER` label, not on content), so even
when Proxy literally says its own name it never self-triggers, never reaches
the disambiguation hook, and never spends or consumes its own pending-ask
window.
"""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from in_meeting.notes import TranscriptLine

#: The reserved speaker label the transcript stream puts on Proxy's own speech.
#: The self-guard keys on this label — one speaker suppressed, never a content
#: filter (every human line is still scanned).
PROXY_SPEAKER = "Proxy"

#: How many *intervening* non-Proxy lines the pending-ask arm survives before it
#: goes stale. Intervening = a line the arm is NOT consumed by: a name-hit line
#: (it engaged the wake word, so the voice path owns it — whether the hook
#: confirms it or rejects it as a common noun). Any non-Proxy line WITHOUT the
#: wake word consumes the arm as the reply, and Proxy's own lines neither spend
#: nor consume. 3 keeps a short cross-talk burst from stranding the window open
#: forever while staying deterministic (a line count, not a clock).
PENDING_ASK_LINE_BUDGET = 3

#: The name Proxy answers to, matched mechanically as a whole word
#: (case-insensitive). The ``\b`` boundaries mean ``proxying`` / ``proxyserver``
#: are NOT hits — but ``the proxy server`` IS a token hit; intent is the
#: injected hook's job, never this scan's.
_NAME_WORD_RE = re.compile(r"\bproxy\b", re.IGNORECASE)

#: The chat address token: ``@proxy`` standing alone (case-insensitive). The
#: look-behind rejects mid-word forms (``oncall@proxyserver.dev``); the trailing
#: boundary rejects ``@proxying``.
_CHAT_TOKEN_RE = re.compile(r"(?<!\w)@proxy\b", re.IGNORECASE)

#: Which source woke Proxy — provenance for the loop, never a judgment.
Source = Literal["voice", "chat", "reply", "worker"]

#: The injected disambiguation hook: takes the spoken line, resolves True iff
#: the name-hit addresses Proxy (vs. the common-noun "proxy server"). ASYNC —
#: the one model touch on this path is a real awaited bounded call now (ONE
#: shape, no sync/async branching); injected, so the module stays vendor-free;
#: awaited ONLY on a mechanical voice name-hit.
Disambiguate = Callable[[str], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class ChatLine:
    """One chat message as the engine consumes it — engine-local, minimal."""

    sender: str
    message: str


@dataclass(frozen=True, slots=True)
class Engagement:
    """The wake signal: which source engaged Proxy, and the payload to carry.

    ``text``/``speaker`` carry the ask verbatim for voice/chat/reply wakes;
    ``worker_id``/``result`` carry a finished worker's delivery. Fields a source
    does not use stay empty — the loop consumes this without re-parsing.
    """

    source: Source
    text: str = ""
    speaker: str = ""
    worker_id: str = ""
    result: str = ""


class EngagementTrigger:
    """The always-on detector for ONE meeting: wake or stay asleep, per input.

    ``disambiguate`` is the injected async hook, awaited ONLY on a spoken
    mechanical name-hit — never on chat (``on_chat`` stays sync: the ``@proxy``
    token wakes directly, no disambiguation), never on a non-hit line, never on
    Proxy's own speech, never on a worker tap (``on_worker_done`` stays sync).
    The only state is the pending-ask window (a small countdown); there is no
    scheduler, no clock, no persistence.
    """

    def __init__(self, *, disambiguate: Disambiguate) -> None:
        self._disambiguate = disambiguate
        #: Lines the pending-ask arm has left to live; 0 = disarmed.
        self._pending_lines_left = 0

    def arm_pending_ask(self) -> None:
        """Open the follow-up window — the loop calls this after Proxy asks.

        The next human line without the wake word counts as the reply. Calling
        again re-arms (a fresh question restarts the budget).
        """
        self._pending_lines_left = PENDING_ASK_LINE_BUDGET

    async def on_transcript(self, line: TranscriptLine) -> Engagement | None:
        """One spoken line → wake (voice or reply) or ``None`` (free).

        Order of physics: the speaker-scoped self-guard first (Proxy's own line
        is inert — no scan, no hook, no window effect); then the name scan (a
        hit belongs to the voice path and spends one intervening line from the
        window); then the pending-ask window (an armed, un-prefixed human line
        is the reply and consumes the arm).

        ASYNC, but the hook is awaited ONLY on a mechanical voice name-hit —
        non-hit lines and Proxy's own (``PROXY_SPEAKER``) lines never await
        anything. An async function with no internal await runs synchronously
        under asyncio (no yield to the event loop), so the feed path's
        atomicity is PRESERVED for all non-hits: append + consult still happen
        with nothing interleaving. Only a confirmed-or-rejected name-hit pays
        the awaited bounded confirm call.
        """
        if line.speaker == PROXY_SPEAKER:
            return None
        if _NAME_WORD_RE.search(line.text) is not None:
            # A wake-word line is never the un-prefixed reply: it spends one
            # intervening line from the window, then the hook resolves intent.
            if self._pending_lines_left > 0:
                self._pending_lines_left -= 1
            if await self._disambiguate(line.text):
                return Engagement(source="voice", text=line.text, speaker=line.speaker)
            return None
        if self._pending_lines_left > 0:
            self._pending_lines_left = 0
            return Engagement(source="reply", text=line.text, speaker=line.speaker)
        return None

    def on_chat(self, msg: ChatLine) -> Engagement | None:
        """One chat message → wake on the ``@proxy`` token, else ``None``.

        Direct, model-free, and orthogonal to the transcript window: chat never
        spends or consumes the pending-ask arm.
        """
        if _CHAT_TOKEN_RE.search(msg.message) is None:
            return None
        return Engagement(source="chat", text=msg.message, speaker=msg.sender)

    def on_worker_done(self, worker_id: str, result: str) -> Engagement:
        """A finished background worker → always a wake carrying its result."""
        return Engagement(source="worker", worker_id=worker_id, result=result)
