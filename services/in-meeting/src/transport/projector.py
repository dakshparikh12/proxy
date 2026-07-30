"""The pure-rendering channel projector (08-EXPERIENCE §4.5, CANONICAL §12.3/§11.3/§1.1).

The projector maps the **delta stream** — the OUTPUT of ``stream_deltas`` applied
**exactly once** inside ``BehaviorRunner.run`` (CANONICAL §11.3) — to **registered**
``ProxyMessage`` render frames. It is *pure rendering*: it never re-runs
``stream_deltas``, never re-accumulates ``chunk.text``, never auto-extracts a
headline, and never decides which channel to use. **The wake-turn delivery tools
(``speak`` / ``send_chat`` / ``show_screen``) are the sole delivery authority**
(CANONICAL §12.3) — the projector only renders what the model already chose to
deliver by calling one of them.

**Field access is on the delta-stream shape (CANONICAL §1.1):**
``chunk.type`` is the discriminator (never ``.kind``); ``chunk.metadata["name"]``
names a ``TOOL_USE``; ``chunk.metadata["structured"]`` carries a ``TOOL_RESULT``'s
structured payload — never ``.tool`` / ``.structured`` as top-level attributes.

**Narrowed to two event types (AMENDMENT C2, 2026-07-17):**

* a ``TOOL_USE`` for a **delivery tool** → the model's own streaming text delta →
  ``VoiceSpeak`` (speak) / ``ResponseChunk`` (send_chat) / ``CanvasPatch`` (show_screen);
* a ``TOOL_USE`` for any other (**work**) tool → a ``ToolStart`` "working…" tile line;
* a structured ``TOOL_RESULT`` → a ``CanvasPatch`` (pin-to-source highlight / final-artifact
  preview — §2.5).

**Everything else — raw ``TEXT`` (the model's reasoning), ``INIT`` / ``RESULT`` /
``ERROR``, an *unstructured* ``TOOL_RESULT`` — is NOT projected to any surface.**
Only an explicit delivery tool reaches a human.

The tool-input streaming shape (§11.10) is confirmed against the live provider
mapper (``services/harness/src/harness/provider.py``): a ``TOOL_USE`` chunk carries
``metadata = {"id", "name", "input"}`` where ``input`` is the tool's argument dict
(``{"text": ...}`` for speak/send_chat, ``{"artifact": ...}`` for show_screen). The
delivery text delta is therefore read off ``metadata["input"]``.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable, Iterator
from typing import Any, Protocol

from libs.contracts import (
    AgentChunk,
    CanvasPatch,
    DraftCard,
    MessageType,
    NoteLine,
    ProxyMessage,
    ResponseChunk,
    ResponseEnd,
    ResponseStart,
    TileState,
    ToolStart,
    VoiceSpeak,
    register_projector,
)

#: The three wake-turn delivery tools — the SOLE delivery authority (CANONICAL §12.3).
#: A ``TOOL_USE`` for one of these renders the model's own text delta to its channel;
#: any other tool is a WORK tool and renders only the tile "working…" line.
DELIVERY_TOOLS: frozenset[str] = frozenset({"speak", "send_chat", "show_screen"})


def humanize_tool(name: str) -> str:
    """Humanize a tool identifier for the tile line (no raw snake_case token leaks).

    ``"grep_repo"`` → ``"Grep Repo"``. Kept tiny and dynamic — code owns only the
    physics of the string transform, never a per-tool phrase table (Law 4).
    """
    words = str(name).replace("-", " ").replace("_", " ").split()
    return " ".join(word.capitalize() for word in words) if words else str(name)


def _as_patch(payload: Any) -> str:
    """Serialize a structured render payload to the capped ``CanvasPatch.patch`` JSON string.

    A str passes through (already a JSON/text payload); anything else is JSON-encoded.
    ``None`` becomes an empty JSON object so the frame stays a valid, bounded render.
    """
    if isinstance(payload, str):
        return payload
    if payload is None:
        return "{}"
    return json.dumps(payload, default=str)


def _delivery_frame(name: str, tool_input: dict[str, Any]) -> ProxyMessage:
    """Render one delivery-tool ``TOOL_USE`` to its channel frame (the model chose the channel)."""
    if name == "speak":
        # → TTS; the chat mirror ("spoken is also posted", §2.3) is a harness mirror,
        # not a second projector decision.
        return VoiceSpeak(text=str(tool_input.get("text", "")))
    if name == "send_chat":
        return ResponseChunk(chunk=str(tool_input.get("text", "")))  # → the permanent chat record
    # show_screen → the shared screen canvas.
    return CanvasPatch(patch=_as_patch(tool_input.get("artifact")))


class ChannelProjector:
    """Pure rendering: one delta-stream ``AgentChunk`` → registered render frames.

    No headline extraction, no delivery decision — the wake turn already decided by
    calling a delivery tool. Emits **registered** ``ProxyMessage`` instances only
    (``send()`` serializes them via ``model_dump()``).
    """

    def project(self, chunk: AgentChunk) -> Iterator[ProxyMessage]:
        """Map ONE delta-stream chunk to zero-or-more registered render frames."""
        if chunk.type == "TOOL_USE":
            name = str(chunk.metadata.get("name", ""))
            if name in DELIVERY_TOOLS:
                # the model chose the channel; render its own streaming text delta
                # (chunk.text is already a delta — forwarded via metadata['input'],
                # NEVER re-accumulated).
                tool_input = chunk.metadata.get("input") or {}
                if not isinstance(tool_input, dict):
                    tool_input = {}
                yield _delivery_frame(name, tool_input)
            else:
                # a WORK tool → the tile "working…" line only (§2.2), never chat prose.
                yield ToolStart(line=humanize_tool(name))
            return
        if chunk.type == "TOOL_RESULT" and chunk.metadata.get("structured") is not None:
            # a structured result → the screen canvas; SCREEN mode-change frames are
            # separate, so camera↔screenshare stays exclusive (§2.5).
            yield CanvasPatch(patch=_as_patch(chunk.metadata["structured"]))
            return
        # TEXT (the model's reasoning) / INIT / RESULT / ERROR / an unstructured
        # TOOL_RESULT: internal — NOT projected to any surface.
        return


# ── the in-process carrier drive (transport is a package in meeting_runtime) ──────
class _Connection(Protocol):
    """The render WS connection the carrier drives (readyState guard + JSON send)."""

    @property
    def ready(self) -> bool: ...

    async def send_json(self, payload: dict[str, Any]) -> None: ...


class _Meeting(Protocol):
    """The per-turn meeting context — the SERVER-owned meeting id (isolation)."""

    @property
    def id(self) -> Any: ...


async def send(conn: _Connection, frame: ProxyMessage) -> None:
    """Send one registered frame — the readyState guard drops on a closed connection.

    A tab closed mid-generation ⇒ ``conn.ready`` is false ⇒ we DROP the send rather
    than crash the turn. A registered instance is serialized via ``model_dump()``.
    """
    if conn.ready:
        await conn.send_json(frame.model_dump(mode="json"))


async def carry_turn(
    conn: _Connection,
    deltas: AsyncIterator[AgentChunk] | Iterable[AgentChunk],
    meeting: _Meeting,
) -> None:
    """Drive the projector over the SAME delta stream every consumer reads (§4.5).

    Emits ``ResponseStart`` → projected frames → ``ResponseEnd``, owns the ordering
    law + readyState guard, and NEVER re-wraps/re-runs ``stream_deltas`` — ``deltas``
    is already ``stream_deltas`` output (applied once in ``BehaviorRunner.run``,
    CANONICAL §11.3). ``meeting.id`` is the SERVER-owned id (a client meeting id is
    never trusted to authorize the turn — isolation).
    """
    await send(conn, ResponseStart(meeting_id=meeting.id))  # resolve FIRST (persist done upstream)
    projector = ChannelProjector()
    async for chunk in _aiter(deltas):  # already stream_deltas output — DO NOT re-wrap
        for frame in projector.project(chunk):
            await send(conn, frame)
    await send(conn, ResponseEnd(meeting_id=meeting.id))


async def _aiter(
    deltas: AsyncIterator[AgentChunk] | Iterable[AgentChunk],
) -> AsyncIterator[AgentChunk]:
    """Iterate the delta stream in its native shape — async in → async, sync in → async.

    This is pure adaptation (it forwards each chunk UNCHANGED); it is emphatically
    NOT ``stream_deltas`` and does not touch ``chunk.text``.
    """
    if isinstance(deltas, AsyncIterator):
        async for chunk in deltas:
            yield chunk
    else:
        for chunk in deltas:
            yield chunk


# ── the surface projectors + the per-outbound-type MESSAGE_PROJECTORS wiring ──────
# One projector per OUTBOUND render-frame type (§4.1) — the closure
# (``assert_registry_closed``) needs ≥1 projector for every outbound type. Each is a
# pure pass-through render seam: the concrete surface sink (TTS / chat / canvas / tile)
# is injected at the carrier ``send`` boundary above, so these stay thin and typed.
def project_voice(frame: VoiceSpeak) -> VoiceSpeak:
    """Render a ``voice.speak`` delta to the TTS surface (§4.5)."""
    return frame


def project_chat(frame: ResponseChunk) -> ResponseChunk:
    """Render a ``response.chunk`` delta to the permanent chat record (§4.5)."""
    return frame


def project_canvas(frame: CanvasPatch) -> CanvasPatch:
    """Render a ``canvas.patch`` to the screen canvas surface (§4.5)."""
    return frame


def project_tile(frame: ToolStart) -> ToolStart:
    """Render a work-tool ``tool.start`` "working…" line to the tile (§4.5, §2.2)."""
    return frame


def project_response_lifecycle(frame: ProxyMessage) -> ProxyMessage:
    """Render a response lifecycle frame (``response.start`` / ``response.end``) (§4.5)."""
    return frame


def project_tile_state(frame: TileState) -> TileState:
    """Render a ``tile.state`` lifecycle frame to the tile (§4.5)."""
    return frame


def project_note(frame: NoteLine) -> NoteLine:
    """Render a ``note.line`` decision/action/correction chat line (§2.4)."""
    return frame


def project_draft(frame: DraftCard) -> DraftCard:
    """Render a ``draft.card`` staged-draft card to chat (§2.4 #8)."""
    return frame


#: The concrete surface projector for each OUTBOUND render-frame type (§4.5). Wired
#: into ``MESSAGE_PROJECTORS`` at import so ``assert_registry_closed`` sees a projector
#: per outbound type — a produced frame no projector renders fails the build (§4.1).
_SURFACE_PROJECTORS: dict[MessageType, Any] = {
    MessageType.VOICE_SPEAK: project_voice,
    MessageType.RESPONSE_CHUNK: project_chat,
    MessageType.CANVAS_PATCH: project_canvas,
    MessageType.TOOL_START: project_tile,
    MessageType.RESPONSE_START: project_response_lifecycle,
    MessageType.RESPONSE_END: project_response_lifecycle,
    MessageType.TILE_STATE: project_tile_state,
    MessageType.NOTE_LINE: project_note,
    MessageType.DRAFT_CARD: project_draft,
}

for _message_type, _projector in _SURFACE_PROJECTORS.items():
    register_projector(_message_type, _projector)


__all__ = [
    "DELIVERY_TOOLS",
    "ChannelProjector",
    "carry_turn",
    "humanize_tool",
    "send",
]
