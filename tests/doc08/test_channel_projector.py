"""Doc 08 · §4.5 — the pure-rendering ChannelProjector + in-process carry_turn.

The projector (``services/transport/projector.py``) maps a **delta-stream**
``AgentChunk`` — the OUTPUT of ``stream_deltas`` applied exactly once in
``BehaviorRunner.run`` (CANONICAL §11.3) — to **registered** ``ProxyMessage``
render frames. Per AMENDMENT C2 it is narrowed to two event types:

* a ``TOOL_USE`` whose ``metadata["name"]`` is a delivery tool (``speak`` /
  ``send_chat`` / ``show_screen``) → the model's own streaming text delta →
  ``VoiceSpeak`` / ``ResponseChunk`` / ``CanvasPatch``;
* a ``TOOL_USE`` for any other (work) tool → a ``ToolStart`` "working…" tile line;
* a structured ``TOOL_RESULT`` (``metadata["structured"]`` not ``None``) →
  ``CanvasPatch``.

Everything else — raw ``TEXT`` (the model's reasoning), ``INIT``, ``RESULT``,
``ERROR``, an *unstructured* ``TOOL_RESULT`` — is **NOT projected to any surface**.

Field access is ``chunk.type`` (never ``.kind``), ``chunk.metadata["name"]`` for a
TOOL_USE, ``chunk.metadata["structured"]`` for a TOOL_RESULT — never ``.tool`` /
``.structured`` as top-level attrs. ``chunk.text`` is forwarded as a delta, never
re-accumulated; the projector NEVER re-runs ``stream_deltas``.

Every test drives the REAL projector over REAL ``AgentChunk`` instances (the live
``libs.contracts`` model) and asserts on REAL registered ``ProxyMessage`` frames.
Product imports live inside test bodies so the module COLLECTS clean and fails RED
before the projector exists.
"""
from __future__ import annotations

import inspect
import json
from uuid import uuid4

import pytest

from libs.contracts import (
    CHANNEL_REGISTRY,
    MESSAGE_PROJECTORS,
    OUTBOUND,
    AgentChunk,
    CanvasPatch,
    ProxyMessage,
    ResponseChunk,
    ToolStart,
    VoiceSpeak,
    assert_registry_closed,
)


# ── the projector maps delivery-tool TOOL_USE → the right channel frame ───────────
def test_speak_tool_use_projects_voicespeak() -> None:
    """A ``speak`` delivery TOOL_USE → a registered ``VoiceSpeak`` carrying the delta."""
    from transport.projector import ChannelProjector

    chunk = AgentChunk(
        type="TOOL_USE",
        text="",
        metadata={"id": "t1", "name": "speak", "input": {"text": "on it"}},
    )
    frames = list(ChannelProjector().project(chunk))

    assert len(frames) == 1
    frame = frames[0]
    assert isinstance(frame, VoiceSpeak)
    assert isinstance(frame, ProxyMessage)  # a REGISTERED instance, not a bare dict
    assert frame.text == "on it"


def test_send_chat_tool_use_projects_responsechunk() -> None:
    """A ``send_chat`` delivery TOOL_USE → a registered ``ResponseChunk`` (the chat record)."""
    from transport.projector import ChannelProjector

    chunk = AgentChunk(
        type="TOOL_USE",
        text="",
        metadata={"id": "t2", "name": "send_chat", "input": {"text": "here's the fix", "dm": False}},
    )
    frames = list(ChannelProjector().project(chunk))

    assert len(frames) == 1
    assert isinstance(frames[0], ResponseChunk)
    assert frames[0].chunk == "here's the fix"


def test_show_screen_tool_use_projects_canvaspatch() -> None:
    """A ``show_screen`` delivery TOOL_USE → a registered ``CanvasPatch`` (screen render)."""
    from transport.projector import ChannelProjector

    artifact = {"kind": "diff", "path": "app.py"}
    chunk = AgentChunk(
        type="TOOL_USE",
        text="",
        metadata={"id": "t3", "name": "show_screen", "input": {"artifact": artifact}},
    )
    frames = list(ChannelProjector().project(chunk))

    assert len(frames) == 1
    assert isinstance(frames[0], CanvasPatch)
    # the artifact is carried as a JSON string on the capped ``patch`` field.
    assert json.loads(frames[0].patch) == artifact


# ── a WORK tool → the tile "working…" line only, never chat prose ────────────────
def test_work_tool_use_projects_toolstart_tile_line() -> None:
    """A non-delivery (work) TOOL_USE → a ``ToolStart`` tile line, never a chat/voice frame."""
    from transport.projector import ChannelProjector

    chunk = AgentChunk(
        type="TOOL_USE",
        text="",
        metadata={"id": "t4", "name": "grep_repo", "input": {"pattern": "def run"}},
    )
    frames = list(ChannelProjector().project(chunk))

    assert len(frames) == 1
    assert isinstance(frames[0], ToolStart)
    # humanized: no raw snake_case token leaks into the user-visible tile line.
    assert "grep_repo" not in frames[0].line
    assert frames[0].line  # non-empty humanized name


# ── a structured TOOL_RESULT → canvas; an UNSTRUCTURED one → nothing ─────────────
def test_structured_tool_result_projects_canvaspatch() -> None:
    """A structured TOOL_RESULT (``metadata['structured']`` set) → a ``CanvasPatch``."""
    from transport.projector import ChannelProjector

    structured = [{"type": "highlight", "line": 42}]
    chunk = AgentChunk(
        type="TOOL_RESULT",
        text="",
        metadata={"tool_use_id": "t3", "is_error": False, "structured": structured},
    )
    frames = list(ChannelProjector().project(chunk))

    assert len(frames) == 1
    assert isinstance(frames[0], CanvasPatch)
    assert json.loads(frames[0].patch) == structured


def test_unstructured_tool_result_projects_nothing() -> None:
    """A TOOL_RESULT with ``structured is None`` is internal — projected to NO surface."""
    from transport.projector import ChannelProjector

    chunk = AgentChunk(
        type="TOOL_RESULT",
        text="ran ok",
        metadata={"tool_use_id": "t4", "is_error": False, "structured": None},
    )
    assert list(ChannelProjector().project(chunk)) == []


# ── raw TEXT / reasoning / INIT / RESULT / ERROR → NOTHING (no headline, no speak) ─
@pytest.mark.parametrize(
    "chunk",
    [
        AgentChunk(type="TEXT", text="I think the bug is here", metadata={"msg_id": "m1"}),
        AgentChunk(type="INIT", text="", metadata={"session_id": "s1", "tools": [], "mcp_servers": []}),
        AgentChunk(type="RESULT", text="done", metadata={"session_id": "s1", "num_turns": 2, "total_cost_usd": 0.01}),
        AgentChunk(type="ERROR", text="", metadata={"message": "boom"}),
    ],
)
def test_no_text_projection_and_no_headline(chunk: AgentChunk) -> None:
    """Raw TEXT (the model's reasoning) and every non-tool chunk project NOTHING.

    The projector never auto-extracts a headline and never decides to speak — the
    wake-turn delivery tools are the sole delivery authority (CANONICAL §12.3).
    """
    from transport.projector import ChannelProjector

    assert list(ChannelProjector().project(chunk)) == []


# ── forwards chunk.text as a delta; NEVER re-accumulates / re-runs stream_deltas ──
def test_delta_forwarded_not_reaccumulated() -> None:
    """Two send_chat deltas project two frames each carrying ONLY its own delta.

    If the projector re-accumulated (the deleted old Doc 08 re-wrap), the second
    frame would carry the concatenation. It must carry the delta as-is.
    """
    from transport.projector import ChannelProjector

    projector = ChannelProjector()
    first = AgentChunk(type="TOOL_USE", text="", metadata={"name": "send_chat", "input": {"text": "The "}})
    second = AgentChunk(type="TOOL_USE", text="", metadata={"name": "send_chat", "input": {"text": "fix"}})

    out1 = list(projector.project(first))
    out2 = list(projector.project(second))

    assert [f.chunk for f in out1] == ["The "]
    assert [f.chunk for f in out2] == ["fix"]  # NOT "The fix" — no re-accumulation


def _code_only(module: object) -> str:
    """The module source with comments AND string/docstring literals stripped.

    So a guard tests the actual CODE (calls, attribute access), never documentation
    prose that legitimately *names* the anti-pattern to say the code avoids it.
    """
    import io
    import textwrap
    import tokenize

    src = textwrap.dedent(inspect.getsource(module))  # type: ignore[arg-type]
    out: list[str] = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        out.append(tok.string)
    return " ".join(out)


def test_projector_does_not_reference_stream_deltas() -> None:
    """The projector CODE never calls/imports ``stream_deltas`` (applied once upstream)."""
    import transport.projector as projector_mod

    code = _code_only(projector_mod)
    assert "stream_deltas" not in code, "projector must NOT re-run stream_deltas (CANONICAL §11.3)"


def test_projector_never_reads_top_level_kind_tool_structured() -> None:
    """Field access is ``chunk.type`` / ``chunk.metadata[...]`` — never ``.kind``/``.tool``.

    Guards the exact anti-pattern the DoD forbids: reading ``.kind`` / ``.tool`` /
    ``.structured`` as top-level chunk attributes. Checks the CODE (docstrings that
    name the anti-pattern to explain the avoidance are stripped first).
    """
    import transport.projector as projector_mod

    code = _code_only(projector_mod)
    for forbidden in (".kind", "chunk.tool", "chunk.structured", "chunk.name"):
        assert forbidden not in code, f"projector must not read {forbidden!r} as a top-level attr"


# ── every OUTBOUND type has a MESSAGE_PROJECTORS entry; registry stays closed ─────
def test_message_projectors_has_entry_per_outbound_type() -> None:
    """Importing the projector wires a MESSAGE_PROJECTORS entry per outbound type."""
    import transport.projector  # noqa: F401 — import wires the projectors

    for outbound in OUTBOUND:
        assert MESSAGE_PROJECTORS.get(outbound), f"no projector registered for {outbound.value!r}"


def test_registry_stays_closed_after_projector_import() -> None:
    """assert_registry_closed stays GREEN after the projector wires its entries."""
    import transport.projector  # noqa: F401

    assert_registry_closed()  # raises AssertionError on any drift; must not raise


# ── carry_turn: ResponseStart → frames → ResponseEnd, over the SAME delta stream ──
class _RecordingConn:
    """A test connection: records every ``send_json`` payload; ``ready`` gates the send."""

    def __init__(self, ready: bool = True) -> None:
        self.ready = ready
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


class _Meeting:
    def __init__(self) -> None:
        self.id = uuid4()


async def _adeltas(chunks):
    for c in chunks:
        yield c


@pytest.mark.asyncio
async def test_carry_turn_wraps_start_frames_end() -> None:
    """carry_turn emits ResponseStart → projected frames → ResponseEnd, in order."""
    from transport.projector import carry_turn

    conn = _RecordingConn(ready=True)
    meeting = _Meeting()
    deltas = _adeltas(
        [
            AgentChunk(type="TEXT", text="reasoning", metadata={"msg_id": "m1"}),  # not projected
            AgentChunk(type="TOOL_USE", text="", metadata={"name": "grep_repo", "input": {}}),  # tile
            AgentChunk(type="TOOL_USE", text="", metadata={"name": "speak", "input": {"text": "found it"}}),
        ]
    )

    await carry_turn(conn, deltas, meeting)

    types = [p["type"] for p in conn.sent]
    assert types[0] == "response.start"
    assert types[-1] == "response.end"
    # the middle frames are exactly the projected ones (TEXT dropped): tile + voice.
    assert types[1:-1] == ["tool.start", "voice.speak"]
    # start/end carry the server-side meeting id (isolation: server owns the id).
    assert conn.sent[0]["meeting_id"] == str(meeting.id)


@pytest.mark.asyncio
async def test_carry_turn_frames_are_registered_serializations() -> None:
    """Every payload carry_turn sends is a registered-frame ``model_dump()`` (real serialize)."""
    from transport.projector import carry_turn

    conn = _RecordingConn(ready=True)
    deltas = _adeltas([AgentChunk(type="TOOL_USE", text="", metadata={"name": "speak", "input": {"text": "hi"}})])

    await carry_turn(conn, deltas, _Meeting())

    for payload in conn.sent:
        # every sent payload's discriminator is a registered ProxyMessage type.
        assert payload["type"] in CHANNEL_REGISTRY
        # round-trips back through the registered model (a true model_dump()).
        CHANNEL_REGISTRY[payload["type"]].model_validate(payload)


@pytest.mark.asyncio
async def test_carry_turn_drops_on_closed_connection() -> None:
    """A tab closed mid-generation ⇒ readyState guard drops sends, never crashes."""
    from transport.projector import carry_turn

    conn = _RecordingConn(ready=False)  # connection not ready (tab closed)
    deltas = _adeltas([AgentChunk(type="TOOL_USE", text="", metadata={"name": "speak", "input": {"text": "hi"}})])

    # must NOT raise even though the connection is closed.
    await carry_turn(conn, deltas, _Meeting())
    assert conn.sent == []  # nothing sent on a closed connection


@pytest.mark.asyncio
async def test_carry_turn_does_not_rewrap_deltas() -> None:
    """carry_turn consumes the delta stream directly — it never re-runs stream_deltas.

    A raw-accumulated TEXT stream fed to carry_turn would, if re-deltaized, change
    the (dropped) TEXT handling; more directly: carry_turn's source must not mention
    stream_deltas, and it must iterate the given stream once.
    """
    import transport.projector as projector_mod

    assert "stream_deltas" not in _code_only(projector_mod.carry_turn)

    # one-pass consumption: a single-use async generator is fully driven exactly once.
    conn = _RecordingConn(ready=True)
    consumed: list[str] = []

    async def _tracking():
        for name in ("send_chat",):
            consumed.append(name)
            yield AgentChunk(type="TOOL_USE", text="", metadata={"name": name, "input": {"text": "x"}})

    await projector_mod.carry_turn(conn, _tracking(), _Meeting())
    assert consumed == ["send_chat"]  # consumed once, in order


@pytest.mark.asyncio
async def test_carrier_drives_projector_over_delta_stream() -> None:
    """The REAL carrier (``carrier.drive_projector``) drives the projector end-to-end.

    Proves the integration point: ``services/transport/carrier.py`` drives the
    projector over the delta stream — ResponseStart → frames → ResponseEnd — without
    re-wrapping ``stream_deltas``.
    """
    from transport.carrier import drive_projector

    conn = _RecordingConn(ready=True)
    meeting = _Meeting()
    deltas = _adeltas(
        [
            AgentChunk(type="TOOL_USE", text="", metadata={"name": "read_file", "input": {}}),  # tile
            AgentChunk(
                type="TOOL_RESULT",
                text="",
                metadata={"tool_use_id": "x", "is_error": False, "structured": [{"hl": 1}]},
            ),  # canvas
            AgentChunk(type="TEXT", text="reasoning", metadata={"msg_id": "m1"}),  # dropped
        ]
    )

    await drive_projector(conn, deltas, meeting)

    types = [p["type"] for p in conn.sent]
    assert types == ["response.start", "tool.start", "canvas.patch", "response.end"]
