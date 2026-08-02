"""``meeting_session`` — the reactive loop that ties the workroom to the meeting.

This is the entire in-meeting spine, in words (SPEC §0/§3): a transcript line arrives →
it is appended to the workroom's ``MEETING_NOTES.md`` (continuous, so a woken turn reads
the up-to-date room) → a cheap word-bounded wake gate decides whether Proxy is addressed
→ on a wake, the reactive turn runs in the workroom and the agent responds through the
one meeting connection. No wake ⇒ nothing happens (idle costs nothing).

This is a **driver, not a decision** (Law 4): the only judgement in *our* code is the
cheap "is Proxy addressed?" gate (word-bounded ``proxy`` / ``@proxy`` — physics, not a
situation→action mapping). WHAT Proxy does and HOW it responds is entirely the agent's,
made live inside the workroom and carried out over the connection. A wake calls
:meth:`Workroom.run_ask`; the response is ALWAYS the agent's own ``to_meeting`` choices —
either relayed live during the turn, or replayed afterward from the agent's recorded intents
(honoring its chosen mediums). We never speak our own prose: a clean turn with zero intents is
the agent choosing silence (cross-talk), and an errored turn that delivered nothing gets ONE
honest degrade line so a needed response is never met with total silence.

A wake runs as a BACKGROUND task so the room keeps flowing while Proxy works (monitor-
while-working, §3/§6). Nothing here ever raises into the drain: a failed wake is an honest
no-op the meeting survives.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: The speaker label Proxy's own audio carries — never treated as a human (no self-wake,
#: no self-barge-in). Proxy's own lines must never wake it.
PROXY_SPEAKER = "Proxy"

#: Rolling window of recent transcript lines materialized into the workroom each line (FW-2).
#: Bounds the per-line write to O(1) bytes (the full rewrite was O(N) → O(N^2) over a long
#: meeting); the recent room is what a woken turn needs, the map+repo cover older context.
_TRANSCRIPT_WINDOW = 400

#: The cheap always-on wake gate: a word-bounded "proxy" (voice) / "@proxy" (chat). This
#: is physics (a name being called), not a situation→action rule — WHAT to do on a wake is
#: the agent's live judgement. Word-bounded so "proxy server" said in passing is filtered
#: at least by the boundary; a bounded model confirm is the follow-up refinement.
_VOICE_ADDR = re.compile(r"\bproxy\b", re.IGNORECASE)
_CHAT_ADDR = re.compile(r"@proxy\b", re.IGNORECASE)

#: Self-echo suppression — the headphones-optional guard. Without headphones, Proxy's own voice
#: bleeds from the room speakers into a human's mic and returns on the transcript MISLABELED as that
#: human, so the speaker-name self-wake filter can't catch it. We instead match the incoming line
#: against what Proxy actually SAID (the connection's ``spoken`` log): a line that reproduces Proxy's
#: recent speech is Proxy's own echo — relabeled to Proxy so it is attributed correctly and never
#: re-wakes Proxy. The bar is deliberately strict (a min length + a high share of the incoming words
#: present in the recent spoken line) so a brief human reply that happens to reuse a word or two of
#: Proxy's is NOT swallowed; the window spans playback + STT latency.
_ECHO_WINDOW_S = 45.0
_ECHO_MIN_TOKENS = 4
_ECHO_CONTAINMENT = 0.7


def _echo_tokens(text: str) -> list[str]:
    """Lowercase alphanumeric word tokens — the normalized form for order-insensitive echo matching
    (an echo's STT drops punctuation/casing and often captures only a partial span, so we compare
    token sets, not the exact string)."""
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _is_self_echo(text: str, spoken: Sequence[tuple[float, str]], now: float) -> bool:
    """True if ``text`` is Proxy's own voice echoing back — it reproduces a line Proxy SAID within
    the echo window. Label-independent (works when the echo returns mislabeled as a human on a
    no-headphones mic). Requires a minimum length and that a high share of the incoming tokens appear
    in the recent spoken line, so a short human interjection is never mistaken for an echo."""
    tokens = _echo_tokens(text)
    if len(tokens) < _ECHO_MIN_TOKENS:
        return False
    incoming = set(tokens)
    for said_ts, said in spoken:
        if now - said_ts > _ECHO_WINDOW_S:
            continue
        said_set = set(_echo_tokens(said))
        if said_set and len(incoming & said_set) / len(incoming) >= _ECHO_CONTAINMENT:
            return True
    return False


def is_addressed(speaker: str, text: str, *, is_chat: bool = False) -> str | None:
    """Return the ask text to wake on (the line verbatim), or ``None`` if not addressed.

    Proxy's own lines never wake it. A voice line naming ``proxy`` or a chat ``@proxy`` is
    an address; anything else is idle. The ask is the line as spoken — the workroom reads
    the full ``MEETING_NOTES.md`` for the surrounding context.
    """
    if speaker == PROXY_SPEAKER:
        return None
    addr = _CHAT_ADDR if is_chat else _VOICE_ADDR
    return text if text and addr.search(text) else None


@dataclass(slots=True)
class MeetingSession:
    """The reactive loop for one live meeting: transcript-in → wake gate → run → respond.

    Drive it by calling :meth:`on_line` for every final transcript line (the webhook
    drain does exactly this). ``workroom`` is the per-meeting E2B sandbox (native Claude
    with the repo + the transcript file); ``connection`` is the host-side meeting driver
    the agent's result is carried out over. ``drain`` awaits in-flight wakes (meeting end).
    """

    workroom: Any                          # in_meeting.workroom.Workroom
    connection: Any                        # in_meeting.meeting_connection.MeetingConnection
    proxy_speaker: str = PROXY_SPEAKER

    _lines: list[tuple[float, str, str]] = field(default_factory=list)  # (ts, speaker, text)
    _inflight: set[asyncio.Task[None]] = field(default_factory=set)
    #: The results of each wake, in order (host-observed record for tests + audit).
    results: list[Any] = field(default_factory=list)

    async def on_line(
        self, speaker: str, text: str, *, ts: float = 0.0, is_chat: bool = False
    ) -> None:
        """Ingest ONE final transcript/chat line: append to the notes, then run the wake gate.

        ``is_chat`` selects the chat wake rule (``@proxy``) vs the voice rule (a spoken
        ``proxy``). Never blocks: the transcript sync + a wake both run so the room keeps
        flowing while Proxy works. A non-addressed line only updates the notes (the up-to-date
        transcript a later wake — or a mid-task follow-up — reads). Never raises."""
        speaker, text = str(speaker or ""), str(text or "")
        # Self-echo suppression (headphones-optional): a voice line that reproduces something Proxy
        # just SAID is Proxy's own voice echoing back — mislabeled as a human when the room has no
        # headphones. Relabel it to Proxy so it is recorded as Proxy's contribution and NEVER
        # re-wakes Proxy (the speaker==proxy_speaker gate below then filters it). Chat is text and
        # cannot echo acoustically, so only voice lines are checked (a human may legitimately quote
        # Proxy in chat). Label-independent, so it works with or without headphones.
        if (
            not is_chat
            and speaker != self.proxy_speaker
            and _is_self_echo(text, getattr(self.connection, "spoken", ()), time.time())
        ):
            speaker = self.proxy_speaker
        self._lines.append((ts, speaker, text))
        # Continuous feed (§3): materialize the latest transcript into the workroom so a
        # woken turn — and any mid-task follow-up — sees the up-to-date room. Never-raise.
        try:
            await self.workroom.feed_transcript(self._render_transcript())
        except Exception:  # noqa: BLE001 — transcript sync never crashes the meeting
            logger.exception("meeting transcript sync failed (meeting continues)")
        ask = is_addressed(speaker, text, is_chat=is_chat)
        if ask is None:
            return
        task = asyncio.create_task(self._handle(ask))
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)

    async def _handle(self, ask: str) -> None:
        """One wake: run the reactive turn in the workroom, respond through the connection.

        The agent chooses WHETHER and HOW to reach the room — always its own ``to_meeting`` calls,
        never our prose. There are two delivery paths and we honor whichever one carried the turn,
        in priority order (Law 4 — the mediums are the agent's, not ours):

        * ``acted_live`` — the in-sandbox MCP server relayed ≥1 ``to_meeting`` call to THIS
          connection during the turn (the connection's ``sent`` grew). The agent already reached the
          room live → stay quiet (never double-send).
        * else ``result.sent`` (the no-relay/file path) — the agent recorded ≥1 intent locally.
          REPLAY each over the connection with the agent's OWN chosen medium.
        * else ``result.error`` — the turn crashed/incompleted with nothing delivered. Speak ONE
          honest degrade so a task that needed a response is never met with silence.
        * else (a clean turn, zero intents) — the agent chose not to respond (e.g. it was not really
          addressed / cross-talk). STAY SILENT. We never invent a response from ``result.text``.

        A failed wake is an honest no-op the meeting survives (§3.8), never a raise."""
        try:
            # The connection's ordered record of what actually reached the room; a live to_meeting
            # relay appends to it. Snapshot the count so we can tell if the agent acted this turn.
            sent_before = len(getattr(self.connection, "sent", ()))
            result = await self.workroom.run_ask(ask)
            self.results.append(result)
            if len(getattr(self.connection, "sent", ())) > sent_before:
                return  # the agent reached the room itself (via the relay) — stay quiet.
            recorded = list(getattr(result, "sent", None) or [])
            if recorded:
                # The no-relay/file path: replay the agent's OWN channel choices verbatim.
                for intent in recorded:
                    await self.connection.to_meeting(
                        str(intent.get("content", "") or ""),
                        medium=str(intent.get("medium", "say") or "say"),
                        to=str(intent.get("to", "") or "") or None,
                    )
                return
            if getattr(result, "error", None):
                # The turn errored with NO recorded ``to_meeting`` intent (the ``recorded`` branch
                # returned above when there was one). We speak ONE bare, honest apology — NEVER the
                # agent's last assistant prose (``result.text``): that is internal scratchpad the
                # agent did NOT choose to say to the room (e.g. "on it…"), so surfacing it would
                # put words in Proxy's mouth it never picked (soft Law 2). A recorded intent is the
                # only thing the agent chose for the room, and that path is already handled above.
                await self.connection.to_meeting(
                    "Sorry — I hit a problem finishing that one and couldn't wrap it up cleanly.",
                    medium="say",
                )
            # else: a clean turn with zero intents — the agent chose silence (cross-talk). Stay quiet.
        except Exception:  # noqa: BLE001 — a wake failure never crashes the meeting (§3.8)
            logger.exception("meeting wake failed (meeting continues)")

    def _render_transcript(self) -> str:
        # Window to the most recent lines (FW-2): the full rewrite is O(N) bytes per line over the
        # E2B seam → O(N^2) over a long meeting. A rolling window bounds each write to O(1) and is
        # what a person reads anyway — the woken turn always sees the recent room (the addressing
        # line is fresh), and the map + repo cover anything older. Header notes the elision.
        recent = self._lines[-_TRANSCRIPT_WINDOW:]
        elided = len(self._lines) - len(recent)
        header = "# Meeting transcript"
        if elided > 0:
            header += f"\n(… {elided} earlier line(s) elided …)"
        body = "\n".join(
            f"[{ts:.0f}] {speaker}: {text}" for (ts, speaker, text) in recent
        )
        return f"{header}\n{body}\n"

    async def drain(self) -> None:
        """Await every in-flight wake (meeting end / tests). Best-effort, never raises."""
        while self._inflight:
            await asyncio.gather(*tuple(self._inflight), return_exceptions=True)


__all__ = ["MeetingSession", "PROXY_SPEAKER", "is_addressed"]
