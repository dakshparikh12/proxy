"""``meeting_session`` — the reactive loop that ties the workroom to the meeting.

This is the entire in-meeting spine, in words (SPEC §0/§3): a transcript line arrives →
it is appended to the workroom's ``MEETING_NOTES.md`` (continuous, so a woken turn reads
the up-to-date room) → a cheap word-bounded wake gate decides whether Proxy is addressed
→ on a wake, the reactive turn runs in the workroom and the agent responds through the
one meeting connection. No wake ⇒ nothing happens (idle costs nothing).

This is a **driver, not a decision** (Law 4): the only judgement in *our* code is the
cheap "is Proxy addressed?" gate (word-bounded ``proxy`` / ``@proxy`` — physics, not a
situation→action mapping). WHAT Proxy does and HOW it responds is entirely the agent's,
made live inside the workroom and carried out over the connection. Keep it simple: a wake
calls :meth:`Workroom.run_ask`, and the result text is handed to the connection's
``to_meeting`` (medium ``say`` by default; the agent's own richer channel choice rides the
in-sandbox MCP relay when live).

A wake runs as a BACKGROUND task so the room keeps flowing while Proxy works (monitor-
while-working, §3/§6). Nothing here ever raises into the drain: a failed wake is an honest
no-op the meeting survives.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: The speaker label Proxy's own audio carries — never treated as a human (no self-wake,
#: no self-barge-in). Proxy's own lines must never wake it.
PROXY_SPEAKER = "Proxy"

#: The cheap always-on wake gate: a word-bounded "proxy" (voice) / "@proxy" (chat). This
#: is physics (a name being called), not a situation→action rule — WHAT to do on a wake is
#: the agent's live judgement. Word-bounded so "proxy server" said in passing is filtered
#: at least by the boundary; a bounded model confirm is the follow-up refinement.
_VOICE_ADDR = re.compile(r"\bproxy\b", re.IGNORECASE)
_CHAT_ADDR = re.compile(r"@proxy\b", re.IGNORECASE)


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
        self._lines.append((ts, speaker, str(text or "")))
        # Continuous feed (§3): materialize the latest transcript into the workroom so a
        # woken turn — and any mid-task follow-up — sees the up-to-date room. Never-raise.
        try:
            await self.workroom.feed_transcript(self._render_transcript())
        except Exception:  # noqa: BLE001 — transcript sync never crashes the meeting
            logger.exception("meeting transcript sync failed (meeting continues)")
        ask = is_addressed(speaker, str(text or ""), is_chat=is_chat)
        if ask is None:
            return
        task = asyncio.create_task(self._handle(ask))
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)

    async def _handle(self, ask: str) -> None:
        """One wake: run the reactive turn in the workroom, respond through the connection.

        Keep it simple (the task's mandate): the wake runs :meth:`Workroom.run_ask` and the
        result text is carried to the room via the connection's ``to_meeting`` (medium
        ``say`` by default — the agent's own richer channel choice rides the in-sandbox MCP
        relay when live). A failed wake is an honest no-op the meeting survives."""
        try:
            result = await self.workroom.run_ask(ask)
            self.results.append(result)
            text = (getattr(result, "text", "") or "").strip()
            if not text and getattr(result, "error", None):
                text = "Sorry — I ran into a problem on that and couldn't finish it cleanly."
            if text:
                await self.connection.to_meeting(text, medium="say")
        except Exception:  # noqa: BLE001 — a wake failure never crashes the meeting (§3.8)
            logger.exception("meeting wake failed (meeting continues)")

    def _render_transcript(self) -> str:
        body = "\n".join(
            f"[{ts:.0f}] {speaker}: {text}" for (ts, speaker, text) in self._lines
        )
        return f"# Meeting transcript\n{body}\n"

    async def drain(self) -> None:
        """Await every in-flight wake (meeting end / tests). Best-effort, never raises."""
        while self._inflight:
            await asyncio.gather(*tuple(self._inflight), return_exceptions=True)


__all__ = ["MeetingSession", "PROXY_SPEAKER", "is_addressed"]
