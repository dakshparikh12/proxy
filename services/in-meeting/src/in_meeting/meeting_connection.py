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
from typing import Protocol

logger = logging.getLogger(__name__)

#: How many recent spoken lines to remember for self-echo suppression (see ``spoken`` below).
#: Bounded so a long meeting never grows this without limit; the echo window is seconds, so a
#: handful is plenty — 64 is generous headroom.
_SPOKEN_LOG_MAX = 64

#: THE ONE canonical ``to_meeting`` medium vocabulary — the NON-SPOKEN channels the agent reaches
#: the room through. Speaking is deliberately NOT here: the live design (Design B) is that the agent
#: SPEAKS by writing its reply, which is streamed sentence-by-sentence to TTS (see ``prime.py`` and
#: ``session_host``); ``to_meeting`` carries only everything that is *not* the voice. This tuple is
#: the single source of truth the MCP tool advertises, ``_route`` handles, and the relay carries —
#: kept in ONE place so the lists can never diverge. (A stale second contract that also told the
#: model ``say`` was a ``to_meeting`` medium/default was the two-contract speaking bug this fixes.)
ADVERTISED_MEDIA: tuple[str, ...] = ("chat", "dm", "screen", "offer", "mute", "unmute")

#: The default ``to_meeting`` medium when the agent (or the relay/replay) names none: ``chat``, the
#: first non-spoken channel — NEVER ``say`` (speaking is the prose stream, not a ``to_meeting`` call).
#: Consistent across the MCP tool, the relay, and this connection so there is one default everywhere.
DEFAULT_MEDIUM = "chat"


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
#: Show something on the bot's Output-Media surface: a URL (iframe) OR agent-produced CONTENT
#: (html/text/markdown, ridden via srcdoc so it always renders). Returns an honest human-readable
#: outcome of what actually happened (showing <url|content>) — never a fabricated success (Law 2).
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
    #: The barge-in "cut" latch (Law 3). A human talking over Proxy calls :meth:`barge_in`, which
    #: stops the in-flight speech AND raises this latch. While it is up, the say-path DROPS further
    #: spoken deliveries for the INTERRUPTED turn: after a cut the sandbox may still be streaming
    #: later sentences of that turn to the relay, and playing them would talk over the human who just
    #: interrupted. :meth:`begin_turn` lowers it again when a new wake's delivery begins. Only the
    #: spoken channel is latched — a human's voice barge-in silences Proxy's VOICE, not its chat/dm.
    cut_latched: bool = False

    async def barge_in(self) -> None:
        """A human is talking over Proxy — STOP its speech now (Law 3) and latch out the rest of the
        interrupted turn's spoken deliveries. Detection (a human line during speech, not a sub-onset
        blip) lives in the reactive loop; this is the physical stop + latch. Never raises the loop:
        the underlying ``speak.cut()`` is the barge-in primitive and is itself never-throw."""
        self.cut_latched = True
        await self.speak.cut()

    def begin_turn(self) -> None:
        """Lower the barge-in cut latch — a NEW wake's delivery is beginning, so its spoken output
        must flow again. The latch only ever silences the ONE turn that was interrupted; the next
        turn starts clean (physics, not a decision — Law 4)."""
        self.cut_latched = False

    def audible_until(self) -> float:
        """The ``time.monotonic()`` horizon until which the ROOM is still audibly hearing Proxy's
        speech — 0.0 if not speaking / unknown. This is the SpeakPipe's own audible-end estimate
        (synth outruns playback, so the write-state goes idle while the page still has seconds of
        scheduled audio); the reactive loop anchors the follow-up window PAST this so the human's
        reply — which lands one beat AFTER the audio finishes — still falls inside the window
        (physics, not a decision — Law 4). Duck-typed: a speak sink without the horizon yields 0.0."""
        return float(getattr(self.speak, "_audible_until", 0.0) or 0.0)

    async def to_meeting(
        self, content: str = "", medium: str = DEFAULT_MEDIUM, to: str | None = None
    ) -> MeetingSend:
        """Carry ONE thing to the room the way the agent chose. Never raises — a failed send is an
        honest ``MeetingSend(ok=False, ...)`` so one bad send never crashes the meeting (§3.8).

        ``medium`` is one of :data:`ADVERTISED_MEDIA` (the non-spoken channels); an absent medium
        defaults to :data:`DEFAULT_MEDIUM` (``chat``). The streamed spoken prose rides the same
        interface with ``medium='say'`` (still handled by :meth:`_route`), but that is the voice
        channel — the agent never *chooses* ``say`` as a ``to_meeting`` medium (Design B)."""
        m = (medium or DEFAULT_MEDIUM).strip().lower()
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
        # The physical pipe: the medium → the real vendor op. A driver, not a rule. The non-spoken
        # mediums here are exactly :data:`ADVERTISED_MEDIA` (chat/dm/screen/offer/mute/unmute); the
        # ``say``/``speak``/``voice`` branch below is the VOICE channel the streamed prose rides over
        # the relay (``session_host`` POSTs each spoken sentence as ``medium='say'``) — not a medium
        # the agent picks. An unrecognized medium falls to the documented safety-net voice fallback.
        if m in ("say", "speak", "voice"):
            # Barge-in latch (Law 3): a human talked over Proxy, so the rest of THIS turn's streamed
            # sentences are dropped rather than played on top of the interrupter. The latch clears on
            # the next wake (``begin_turn``). Only the spoken channel is silenced — a chat/dm the
            # agent chose still lands, since a voice barge-in silences Proxy's VOICE, not its typing.
            if self.cut_latched:
                return MeetingSend("say", False, "dropped: barged-in")
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
            # The sink returns an honest human-readable outcome (showing <url|content>, or an
            # honest failure like oversize/fault). Surface it verbatim — never a fabricated success.
            outcome = await self.screen(content)
            return MeetingSend("screen", True, outcome)
        if m in ("offer", "propose", "draft", "approve"):
            if self.offer is None:
                return MeetingSend("offer", False, "offer path not available")
            approve_url = await self.offer(content, to or "")
            # Surface the approve link into the room so a human can click it (Law 3).
            if approve_url:
                await self.room.post_chat(self.bot_id, f"Ready to apply — approve: {approve_url}")
            return MeetingSend("offer", True, approve_url)
        # SAFETY-NET fallback (documented, NOT the default): an unrecognized medium string still
        # reaches the room as voice rather than being dropped silently — the honest last resort when
        # the agent names something outside :data:`ADVERTISED_MEDIA`. The DEFAULT for an absent
        # medium is ``chat`` (handled above via :data:`DEFAULT_MEDIUM`), never this branch.
        await self.speak.say(content)
        return MeetingSend("say", True, f"unknown medium {m!r} → said")
