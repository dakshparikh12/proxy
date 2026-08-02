"""The MEETING CONNECTION — the host-side driver that carries whatever Proxy sends to the room.

Proxy (native Claude in the workroom sandbox) reaches the live meeting through ONE dynamic
interface: it decides *what* to convey and *how* (say it out loud, drop it in chat, DM someone,
show a screen, offer a world-touching change for approval, or go quiet) and calls a single
``to_meeting`` tool over MCP. This object is what that tool lands on: it holds the meeting
credentials (host-side, never in the sandbox) and routes the agent's chosen medium to the real
Recall/Cartesia operation.

This is a **driver, not a decision** (Law 4). There is no situation→action logic here and no
capability catalog the agent is boxed into — the agent freely chooses the medium; this only maps
that choice to the physical pipe (like a person's voice, hands, and screen). World-touching stays a
human click by the credential boundary: the sandbox has no push/send creds, so an ``offer`` becomes
a staged draft + an approve link, applied only when a human clicks (Law 3).

The vendor sinks are injected as Protocols so the whole connection is provable with fakes (a
simulated meeting) before any live vendor round-trip.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)

#: How many recent spoken lines to remember for self-echo suppression (see ``spoken`` below).
#: Bounded so a long meeting never grows this without limit; the echo window is seconds, so a
#: handful is plenty — 64 is generous headroom.
_SPOKEN_LOG_MAX = 64


class SpeakSink(Protocol):
    """Proxy's voice: synth + play into the room, and cut on a human barge-in."""

    async def say(self, text: str) -> None: ...
    async def cut(self) -> None: ...


class RoomSink(Protocol):
    """The Recall room verbs the host executes for the agent (creds stay here)."""

    async def post_chat(self, bot_id: str, message: str, *, pinned: bool = False) -> None: ...
    async def send_dm(self, bot_id: str, message: str, participant_id: str) -> None: ...
    async def mute(self, bot_id: str) -> None: ...
    async def unmute(self, bot_id: str) -> None: ...


#: Stage a world-touching artifact for a human click; returns an approve URL (or "" if unavailable).
OfferSink = Callable[[str, str], Awaitable[str]]
#: Point the bot's Output-Media surface at a URL (a rendered diff/mock/page). Returns the shown URL.
ScreenSink = Callable[[str], Awaitable[str]]
#: Mute/unmute the meeting's conversational audio at the Output-Media webpage channel (where the
#: spoken PCM actually rides). ``True`` mutes, ``False`` unmutes. Law 3 — human control is absolute.
AudioMuteSink = Callable[[bool], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class MeetingSend:
    """The outcome of one send to the room — what actually went out (for logging + tests)."""

    medium: str
    ok: bool
    detail: str = ""


@dataclass(slots=True)
class MeetingConnection:
    """One live meeting, reached through one dynamic interface. Construct per meeting; the MCP
    ``to_meeting`` tool calls :meth:`to_meeting`. The agent owns *whether/what/how*; this owns only
    the physics."""

    speak: SpeakSink
    room: RoomSink
    bot_id: str
    offer: OfferSink | None = None
    screen: ScreenSink | None = None
    #: Mute/unmute the Output-Media webpage channel where the spoken PCM actually rides. When set,
    #: a 'mute'/'unmute' medium silences/restores the real conversational audio (Law 3); the Recall
    #: ``room.mute/unmute`` still fires alongside it for the (unused) clip path. ``None`` ⇒ only the
    #: room verb (the pre-wiring behavior).
    audio_mute: AudioMuteSink | None = None
    #: every send, in order — the host-observed record (never the model's prose), for tests + audit.
    sent: list[MeetingSend] = field(default_factory=list)
    #: what Proxy actually SAID out loud, as ``(wall_ts, text)`` — the ground-truth reference for
    #: self-echo suppression. When the room has no headphones, Proxy's own voice bleeds from the
    #: speakers back into a human's mic and returns on the transcript MISLABELED as that human (so
    #: the speaker-name self-wake filter can't catch it). The reactive loop matches incoming lines
    #: against THIS log to recognize Proxy's own echo regardless of how it's labeled, so Proxy never
    #: re-wakes on itself and the line is attributed back to Proxy. Only the spoken ('say') channel
    #: is recorded — chat/dm are text and never echo acoustically. Bounded to ``_SPOKEN_LOG_MAX``.
    spoken: list[tuple[float, str]] = field(default_factory=list)

    async def to_meeting(
        self, content: str = "", medium: str = "say", to: str | None = None
    ) -> MeetingSend:
        """Carry ONE thing to the room the way the agent chose. Never raises — a failed send is an
        honest ``MeetingSend(ok=False, ...)`` so one bad send never crashes the meeting (§3.8)."""
        m = (medium or "say").strip().lower()
        try:
            result = await self._route(m, content, to)
        except Exception as exc:  # noqa: BLE001 — never crash the loop on a vendor fault
            logger.exception("meeting send failed (medium=%s)", m)
            result = MeetingSend(medium=m, ok=False, detail=str(exc) or exc.__class__.__name__)
        self.sent.append(result)
        return result

    def _record_spoken(self, content: str) -> None:
        """Remember what Proxy just SAID (wall-clock stamped) so the reactive loop can recognize the
        acoustic echo of it — Proxy's own voice returning on a human's mic when the room has no
        headphones — and never re-wake on itself. Bounded to the most recent ``_SPOKEN_LOG_MAX``."""
        text = (content or "").strip()
        if not text:
            return
        self.spoken.append((time.time(), text))
        if len(self.spoken) > _SPOKEN_LOG_MAX:
            del self.spoken[: len(self.spoken) - _SPOKEN_LOG_MAX]

    async def _route(self, m: str, content: str, to: str | None) -> MeetingSend:
        # The physical pipe: the agent's chosen medium → the real vendor op. A driver, not a rule.
        if m in ("say", "speak", "voice"):
            await self.speak.say(content)
            self._record_spoken(content)
            return MeetingSend("say", True)
        if m in ("chat", "message", "post"):
            await self.room.post_chat(self.bot_id, content)
            return MeetingSend("chat", True)
        if m in ("dm", "direct", "whisper"):
            if not to:
                return MeetingSend("dm", False, "no recipient given")
            await self.room.send_dm(self.bot_id, content, to)
            return MeetingSend("dm", True, f"to={to}")
        if m in ("mute", "silence"):
            # Silence the real conversational audio at the webpage channel first (that is where the
            # spoken PCM rides); the Recall room verb fires alongside for the clip path (Law 3).
            if self.audio_mute is not None:
                await self.audio_mute(True)
            await self.room.mute(self.bot_id)
            return MeetingSend("mute", True)
        if m in ("unmute", "resume"):
            if self.audio_mute is not None:
                await self.audio_mute(False)
            await self.room.unmute(self.bot_id)
            return MeetingSend("unmute", True)
        if m in ("screen", "show", "share"):
            if self.screen is None:
                return MeetingSend("screen", False, "screen surface not available")
            url = await self.screen(content)
            return MeetingSend("screen", True, url)
        if m in ("offer", "propose", "draft", "approve"):
            if self.offer is None:
                return MeetingSend("offer", False, "offer path not available")
            approve_url = await self.offer(content, to or "")
            # Surface the approve link into the room so a human can click it (Law 3).
            if approve_url:
                await self.room.post_chat(self.bot_id, f"Ready to apply — approve: {approve_url}")
            return MeetingSend("offer", True, approve_url)
        # Unknown medium → default to voice rather than dropping the agent's words silently.
        await self.speak.say(content)
        return MeetingSend("say", True, f"unknown medium {m!r} → said")


#: The MCP tool schema the sandbox agent sees — ONE tool, the agent chooses content + medium.
#: (Kept here so the MCP transport wrapper and the tests share one definition.)
TO_MEETING_TOOL: dict[str, Any] = {
    "name": "to_meeting",
    "description": (
        "Send something to the live meeting. You decide what to convey and how. "
        "medium: 'say' (out loud, the default) | 'chat' | 'dm' (needs `to`) | 'screen' (show a "
        "URL/view) | 'offer' (stage a world-touching change/message for a human's one-click "
        "approval) | 'mute' | 'unmute'. Use your judgment like a great teammate; stay silent by "
        "simply not calling this."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "What to convey (or a URL for 'screen')."},
            "medium": {"type": "string", "description": "How to convey it; defaults to 'say'."},
            "to": {"type": "string", "description": "Recipient participant id for 'dm'."},
        },
        "required": ["content"],
    },
}
