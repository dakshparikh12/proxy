"""The MEETING BRIDGE — the thin trusted-host layer between the live meeting and the workroom.

The workroom (native Claude in the E2B sandbox) does all the *work*; the bridge does all the
*talking*. It owns the six seams that connect the two worlds:

1. **transcript-in**  — every line is materialized into the workroom's transcript file.
2. **trigger**        — a cheap always-on gate deciding WHEN to wake the (expensive) workroom.
3. **run + present**  — on a wake, run the ask in the workroom, then present the result to the
                        room the best way (speak / chat), Proxy's own choice.
4. **barge-in**       — a human speaking while Proxy is talking cuts Proxy's audio (Law 3).
5. **meeting-action-out** — reversible in-room verbs (mute/chat/DM) the sandboxed agent
                        requests but the bridge (holding the meeting credentials) executes.
6. **draft-gate**     — world-touching actions come back as a staged draft behind a human click.

The meeting-I/O sinks (``speaker``, ``actions``) are injected: production wires them to
Cartesia→Recall / the Recall bot; a simulation wires recorders. So the whole loop is provable
without a live meeting.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from in_meeting.workroom import Workroom, WorkroomResult

logger = logging.getLogger(__name__)

#: The speaker label Proxy's own audio carries — never treated as a human (no self-barge-in).
PROXY_SPEAKER = "Proxy"

#: Word-bounded "proxy" address detection (voice) + "@proxy" (chat). Common-noun "proxy server"
#: is filtered by requiring it to be an address, not an incidental mention (the disambiguate hook
#: — a bounded model confirm — refines this; here it's the cheap first gate).
_VOICE_ADDR = re.compile(r"\bproxy\b", re.IGNORECASE)
_CHAT_ADDR = re.compile(r"@proxy\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Line:
    """One transcript line the bridge ingests."""

    speaker: str
    text: str
    ts: float = 0.0
    is_chat: bool = False


class Speaker(Protocol):
    """The speak-out sink: Proxy's voice in the room, with barge-in cut."""

    async def speak(self, text: str) -> None: ...
    async def cut(self) -> None: ...


class Actions(Protocol):
    """The meeting-action-out sink: reversible in-room verbs the bridge executes for the agent."""

    async def mute(self) -> None: ...
    async def unmute(self) -> None: ...
    async def post_chat(self, message: str) -> None: ...
    async def send_dm(self, message: str, to: str) -> None: ...


#: The trigger: given a line + whether Proxy is mid-turn, return the ASK text to wake on, or None.
#: Injected so the real disambiguate (a bounded model confirm) can replace the cheap regex.
Trigger = Callable[[Line], "str | None"]


def default_trigger(line: Line) -> str | None:
    """Cheap always-on wake gate: a voice line naming 'proxy', or a chat '@proxy'. Proxy's own
    lines never wake it. The ask text is the line verbatim (the workroom reads the full
    transcript for context)."""
    if line.speaker == PROXY_SPEAKER:
        return None
    addr = _CHAT_ADDR if line.is_chat else _VOICE_ADDR
    return line.text if addr.search(line.text) else None


@dataclass(slots=True)
class MeetingBridge:
    """Connects one live meeting to one workroom. Drive it by calling :meth:`on_line` for every
    transcript/chat line; it triggers, runs, and presents autonomously. ``drain`` awaits
    in-flight work; ``teardown`` closes the workroom."""

    workroom: Workroom
    speaker: Speaker
    actions: Actions
    trigger: Trigger = default_trigger
    proxy_speaker: str = PROXY_SPEAKER

    _lines: list[Line] = field(default_factory=list)
    _speaking: bool = False
    _inflight: set[asyncio.Task[None]] = field(default_factory=set)
    results: list[WorkroomResult] = field(default_factory=list)

    async def on_line(self, line: Line) -> None:
        """Ingest ONE meeting line. Never blocks: a wake runs as a background task so the room
        keeps flowing while Proxy works (monitor-while-working)."""
        # barge-in FIRST (Law 3): a human speaking while Proxy talks cuts Proxy now.
        if self._speaking and not line.is_chat and line.speaker != self.proxy_speaker:
            await self._cut()
        self._lines.append(line)
        ask = self.trigger(line)
        if ask is not None:
            task = asyncio.create_task(self._handle(ask))
            self._inflight.add(task)
            task.add_done_callback(self._inflight.discard)

    async def _handle(self, ask: str) -> None:
        """One wake: sync the transcript into the workroom, run the ask, present the result."""
        try:
            await self.workroom.feed_transcript(self._render_transcript())
            result = await self.workroom.run_ask(ask)
            self.results.append(result)
            await self._present(result)
        except Exception:  # noqa: BLE001 — a wake failure never crashes the meeting
            logger.exception("bridge wake failed (meeting continues)")

    async def _present(self, result: WorkroomResult) -> None:
        """Present the workroom's result to the room. Speak it (the default channel); the
        agent's own richer channel choice (chat/hand/screen) rides the meeting-action-out
        protocol it emitted during the turn (applied in a follow-up refinement)."""
        text = result.text.strip() if result.text else ""
        if not text and result.error:
            text = "Sorry — I ran into a problem on that and couldn't finish it cleanly."
        if not text:
            return
        self._speaking = True
        try:
            await self.speaker.speak(text)
        finally:
            self._speaking = False

    async def _cut(self) -> None:
        self._speaking = False
        try:
            await self.speaker.cut()
        except Exception:  # noqa: BLE001
            logger.exception("bridge barge-in cut failed")

    def _render_transcript(self) -> str:
        body = "\n".join(
            f"[{ln.ts:.0f}] {'(chat) ' if ln.is_chat else ''}{ln.speaker}: {ln.text}"
            for ln in self._lines
        )
        return f"# Meeting transcript\n{body}\n"

    async def drain(self) -> None:
        """Await every in-flight wake (used at meeting end / for tests)."""
        while self._inflight:
            await asyncio.gather(*tuple(self._inflight), return_exceptions=True)

    async def teardown(self) -> None:
        await self.drain()
        await self.workroom.teardown()


# ── Real meeting-I/O sinks: the bridge protocols over the live vendor edges ───────


@dataclass(slots=True)
class CartesiaSpeaker:
    """The real speak-out: Proxy's voice via the live ``SpeakPipe`` (Cartesia synth → the
    Recall Output-Media channel), with barge-in cut. Satisfies :class:`Speaker`."""

    pipe: object  # in_meeting.speak.SpeakPipe

    async def speak(self, text: str) -> None:
        await self.pipe.say(text)   # type: ignore[attr-defined]
        await self.pipe.flush()     # type: ignore[attr-defined]

    async def cut(self) -> None:
        await self.pipe.cut()       # type: ignore[attr-defined]


@dataclass(slots=True)
class RecallActions:
    """The real meeting-action-out: reversible in-room verbs via the live ``RecallTransport``.
    Satisfies :class:`Actions` (the bridge holds the Recall creds; the sandboxed agent never
    does)."""

    transport: object  # transport.recall.RecallTransport
    bot_id: str

    async def mute(self) -> None:
        await self.transport.mute(self.bot_id)                       # type: ignore[attr-defined]

    async def unmute(self) -> None:
        await self.transport.unmute(self.bot_id)                     # type: ignore[attr-defined]

    async def post_chat(self, message: str) -> None:
        await self.transport.post_chat(self.bot_id, message)         # type: ignore[attr-defined]

    async def send_dm(self, message: str, to: str) -> None:
        await self.transport.send_dm(self.bot_id, message, to)       # type: ignore[attr-defined]


def build_meeting_bridge(
    *,
    workroom: Workroom,
    meeting_id: str,
    tts: object,
    transport: object,
    bot_id: str,
    trigger: Trigger = default_trigger,
) -> MeetingBridge:
    """Assemble the production bridge for one meeting: a live Cartesia→Recall speak pipe + the
    Recall action verbs, fronting the workroom. This is what the provisioner's
    ``_assemble_workroom`` calls once the sandbox is warm."""
    from in_meeting.speak import real_speak_sink

    pipe = real_speak_sink(meeting_id, tts)  # type: ignore[arg-type]
    return MeetingBridge(
        workroom=workroom,
        speaker=CartesiaSpeaker(pipe=pipe),
        actions=RecallActions(transport=transport, bot_id=bot_id),
        trigger=trigger,
    )
