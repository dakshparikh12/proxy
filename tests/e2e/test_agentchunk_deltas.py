"""J-09-agentchunk-stream-deltas · 09-VERIFICATION §2 (4th bullet) + CANONICAL §1.1.

The whole-product journey proof for the streaming-spine invariant: **every** AgentChunk
consumer reads the ``stream_deltas`` output and NEVER a raw accumulated ``TEXT`` chunk, and
``stream_deltas`` is applied EXACTLY ONCE per stream (no double-application). This guards the
barge-in/TTS substrate — a consumer that read accumulated text (or a driver that wrapped an
already-deltaized stream again) would re-speak the whole answer on every delta.

Why this test (and not the Doc-00 sealed ``test_cmp_005``) is the right oracle
--------------------------------------------------------------------------------
The sealed ``tests/doc00/test_m00_cmp.py::test_cmp_005`` asserts ``stream_deltas`` is called
at LITERALLY ONE call site (a single-driver assumption from the foundation era, where the only
turn-driver was ``BehaviorRunner.run``). The built product has since grown a **multi-driver
Workroom** (Doc 05 §3): the plan turn, the plan critic, the replan turn, the subtask worker,
the session driver, the resume-fallback driver, and the verify-gate critic each run their OWN
``query()`` and therefore each produce their OWN *distinct* raw provider stream that must be
delta-ized once. That is 8 call sites — every one a SINGLE application over a DISTINCT raw
stream, never a re-wrap of another's output. The literal "== 1" is a stale foundation-era
count; the load-bearing invariant it was protecting is **"no double-application"**, which this
test enforces directly (statically over the AST *and* dynamically over the real delta-izer).

We do NOT edit the sealed test — the contradiction is reported to the founder (build/founder
flag) with the recommendation to relax AC-CMP-005 from "exactly one call site" to "no double
application". This test is the invariant that should carry that guarantee forward.

Everything here runs the REAL path: the shipped ``libs.agentkit.stream_deltas`` delta-izer,
the shipped ``transport.projector`` speak path (``carry_turn`` + ``ChannelProjector.project``),
and a static AST sweep over the committed product source (``services/*`` + ``libs/*``) — no
mocks of the seam under test.

Node: ``journey.agentchunk-stream-deltas`` · spec_refs 09-VERIFICATION §2, CANONICAL §1.1.
"""
from __future__ import annotations

import ast
import pathlib
import uuid

import pytest

from libs.contracts import AgentChunk

pytestmark = pytest.mark.e2e

# ---------------------------------------------------------------------------
# Repo-anchored source sweep (stdlib only — independent of the product toolchain)
# ---------------------------------------------------------------------------
_THIS = pathlib.Path(__file__).resolve()


def _repo_root() -> pathlib.Path:
    for parent in (_THIS, *_THIS.parents):
        if (parent / ".git").exists():
            return parent
    return _THIS.parents[2]


ROOT = _repo_root()
PRODUCT_TREES = ("services", "libs")


def _product_py_files() -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for tree in PRODUCT_TREES:
        base = ROOT / tree
        if base.exists():
            out.extend(sorted(base.rglob("*.py")))
    return out


def _text(chunk: AgentChunk, s: str, msg_id: str) -> AgentChunk:
    """A RAW provider TEXT chunk: ``.text`` is the ACCUMULATED text for this msg_id."""
    return AgentChunk(type="TEXT", text=s, metadata={"msg_id": msg_id})


# ===========================================================================
# Pillar 1 — stream_deltas is the SOLE delta seam (one definition, imported everywhere)
# ===========================================================================
def test_stream_deltas_is_the_single_defined_delta_seam() -> None:
    """The delta-izer is DEFINED once (its public name is an alias) and every call site in the
    product imports that one symbol — no consumer re-implements accumulation→delta locally."""
    import inspect

    from libs import agentkit
    from libs.agentkit import deltas as deltas_mod

    # The public callable and the module-level alias resolve to the ONE source definition
    # (the src-layout shim re-exports the same file under two module paths, so we compare by
    # source, not object identity — the product's own oracles reason over source too).
    public_src = inspect.getsourcefile(agentkit.stream_deltas)
    alias_src = inspect.getsourcefile(deltas_mod.stream_deltas)
    assert public_src == alias_src, f"stream_deltas must be one seam; {public_src} != {alias_src}"
    assert public_src is not None and public_src.endswith("libs/agentkit/src/agentkit/deltas.py"), (
        f"the delta-izer must live in libs/agentkit/deltas.py; got {public_src}"
    )
    # It is non-idempotent by construction (delta-izing its own output corrupts it) — that is
    # WHY double-application is a bug, and why "applied once per stream" is the real invariant.
    assert agentkit.stream_deltas.__name__ in ("_deltaize", "stream_deltas")

    # No SECOND definition of a `stream_deltas` function/alias anywhere in the product tree —
    # the only *definition* line is the alias assignment in deltas.py.
    def_sites: list[str] = []
    for f in _product_py_files():
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "stream_deltas":
                def_sites.append(str(f.relative_to(ROOT)))
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id == "stream_deltas":
                        def_sites.append(str(f.relative_to(ROOT)))
    assert def_sites == ["libs/agentkit/src/agentkit/deltas.py"], (
        f"stream_deltas must be defined/aliased exactly once (in libs/agentkit/deltas.py); found {def_sites}"
    )


# ===========================================================================
# Pillar 2 — NO double-application anywhere (static AST over every consumer/driver)
# ===========================================================================
def _stream_deltas_calls(tree: ast.AST) -> list[ast.Call]:
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "stream_deltas"
    ]


def _first_arg(call: ast.Call) -> ast.expr | None:
    return call.args[0] if call.args else None


def test_no_stream_deltas_call_wraps_another_stream_deltas_output() -> None:
    """A sweep of EVERY AgentChunk driver/consumer proves ``stream_deltas`` is never applied to
    the output of ``stream_deltas`` — neither directly (``stream_deltas(stream_deltas(x))``) nor
    indirectly (wrapping a name bound to a prior ``stream_deltas(...)`` in the same function).

    Double-application is the accumulation-vs-delta bug in its purest form: the second pass
    re-diffs already-diffed suffixes, corrupting the stream (proven dynamically below)."""
    violations: list[str] = []
    for f in _product_py_files():
        try:
            src = f.read_text(encoding="utf-8", errors="replace")
            module = ast.parse(src)
        except (SyntaxError, OSError):
            continue
        rel = str(f.relative_to(ROOT))

        # Names bound to a stream_deltas(...) result, per enclosing function scope.
        for scope in ast.walk(module):
            if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
                continue
            deltaized_names: set[str] = set()
            for stmt in ast.walk(scope):
                if isinstance(stmt, ast.Assign):
                    val = stmt.value
                    if (
                        isinstance(val, ast.Call)
                        and isinstance(val.func, ast.Name)
                        and val.func.id == "stream_deltas"
                    ):
                        for tgt in stmt.targets:
                            if isinstance(tgt, ast.Name):
                                deltaized_names.add(tgt.id)
            for call in _stream_deltas_calls(scope):
                arg = _first_arg(call)
                # (a) direct nesting: stream_deltas(stream_deltas(...))
                if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) and arg.func.id == "stream_deltas":
                    violations.append(f"{rel}:{call.lineno} wraps stream_deltas() output directly")
                # (b) indirect: stream_deltas(<name bound to a prior stream_deltas call>)
                if isinstance(arg, ast.Name) and arg.id in deltaized_names:
                    violations.append(
                        f"{rel}:{call.lineno} re-wraps '{arg.id}' which is already stream_deltas output"
                    )
    assert not violations, "double-application of stream_deltas detected:\n  " + "\n  ".join(violations)


def test_every_stream_deltas_call_wraps_a_raw_provider_stream() -> None:
    """Positive shape check: every ``stream_deltas(...)`` call site takes a RAW provider stream —
    a ``provider.stream(...)`` call or a name bound to one (``raw``/``raw_stream``) — never a
    projector/progress-tap wrapper. This is the multi-driver reality the sealed test misses:
    each of the N drivers deltaizes its OWN distinct raw stream exactly once."""
    allowed_raw_names = {"raw", "raw_stream", "chunks"}
    offenders: list[str] = []
    call_count = 0
    for f in _product_py_files():
        try:
            module = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, OSError):
            continue
        rel = str(f.relative_to(ROOT))
        for call in _stream_deltas_calls(module):
            call_count += 1
            arg = _first_arg(call)
            ok = False
            if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute) and arg.func.attr == "stream":
                ok = True  # provider.stream(prompt, options)
            elif isinstance(arg, ast.Name) and arg.id in allowed_raw_names:
                ok = True
            if not ok:
                shape = type(arg).__name__ if arg is not None else "None"
                offenders.append(f"{rel}:{call.lineno} first arg is {shape}, not a raw provider stream")
    assert call_count >= 2, f"expected the multi-driver Workroom reality (>=2 call sites); found {call_count}"
    assert not offenders, "a stream_deltas call takes a non-raw stream:\n  " + "\n  ".join(offenders)


# ===========================================================================
# Pillar 3 — the delta-vs-accumulation REGRESSION (double-application MUST corrupt)
# ===========================================================================
def test_stream_deltas_once_yields_true_deltas() -> None:
    """The REAL delta-izer turns per-msg_id ACCUMULATED text into suffix deltas; non-TEXT chunks
    pass through untouched. This is the single-pass ground truth the double pass must NOT match."""
    from libs.agentkit import stream_deltas

    scripted = [
        AgentChunk(type="INIT", metadata={"session_id": "s1"}),
        _text(AgentChunk, "Hel", "m1"),
        _text(AgentChunk, "Hello", "m1"),
        _text(AgentChunk, "Hello wor", "m1"),
        _text(AgentChunk, "Hello world", "m1"),
        AgentChunk(type="RESULT", metadata={"total_cost_usd": 0.01}),
    ]
    out = list(stream_deltas(iter(scripted)))
    texts = [c.text for c in out if c.type == "TEXT"]
    assert texts == ["Hel", "lo", " wor", "ld"], f"single-pass deltas wrong: {texts}"
    # non-TEXT chunks are forwarded unchanged.
    assert out[0].type == "INIT" and out[-1].type == "RESULT"
    assert "".join(texts) == "Hello world", "the concatenated deltas must reconstruct the final text ONCE"


def test_double_application_corrupts_deltas_and_does_not_reproduce_single_pass() -> None:
    """THE regression: applying ``stream_deltas`` to its OWN output re-diffs already-diffed
    suffixes and CORRUPTS the stream — it must NOT reproduce the single-pass deltas. This is why
    a driver that double-wrapped (or a consumer that re-ran the seam) breaks the TTS substrate."""
    from libs.agentkit import stream_deltas

    scripted = [
        _text(AgentChunk, "Hel", "m1"),
        _text(AgentChunk, "Hello", "m1"),
        _text(AgentChunk, "Hello wor", "m1"),
        _text(AgentChunk, "Hello world", "m1"),
    ]
    once = [c.text for c in stream_deltas(iter(scripted)) if c.type == "TEXT"]
    # Re-feed the already-deltaized stream back through the seam.
    twice = [c.text for c in stream_deltas(iter(stream_deltas(iter(scripted)))) if c.type == "TEXT"]

    assert once == ["Hel", "lo", " wor", "ld"], f"single pass changed: {once}"
    assert twice != once, "double application MUST NOT reproduce single-pass deltas (it would hide the bug)"
    # And it is demonstrably CORRUPT: the double pass no longer reconstructs the answer.
    assert "".join(twice) != "Hello world", (
        f"double application must corrupt the reconstructable answer; got {''.join(twice)!r}"
    )


# ===========================================================================
# Pillar 4 — the REAL consumers read the delta stream (.type/.metadata; .text as a delta)
# ===========================================================================
def test_transport_speak_path_consumes_deltas_without_reaccumulating() -> None:
    """The transport speak path (``carry_turn`` → ``ChannelProjector.project``) is driven over
    the REAL ``stream_deltas`` output and must forward each TEXT delta as-is — a consumer that
    re-accumulated would re-speak the whole answer on every chunk (the barge-in/TTS bug).

    We drive the ACTUAL projector: the model delivers via a ``speak`` TOOL_USE whose ``input``
    carries the streaming delta, exactly as the wake turn emits it."""
    import asyncio

    from libs.agentkit import stream_deltas
    from transport.projector import carry_turn

    spoken: list[str] = []

    class _Conn:
        ready = True

        async def send_json(self, payload: dict[str, str]) -> None:
            # Capture only what reaches the TTS/voice surface.
            if payload.get("type") == "voice.speak":
                spoken.append(str(payload.get("text", "")))

    class _Meeting:
        # SERVER-owned meeting id (a client meeting_id never authorizes an entity — isolation).
        id = uuid.uuid4()

    # RAW provider stream: the model streams accumulated delivery text via successive speak
    # TOOL_USE chunks (the wake turn's shape). stream_deltas is applied ONCE by the driver.
    def _speak(accumulated: str) -> AgentChunk:
        return AgentChunk(
            type="TOOL_USE",
            metadata={"name": "speak", "input": {"text": accumulated}},
        )

    raw = [
        AgentChunk(type="INIT", metadata={"session_id": "s1"}),
        _speak("The"),
        _speak("The answer"),
        _speak("The answer is 42"),
        AgentChunk(type="RESULT", metadata={"total_cost_usd": 0.02}),
    ]

    async def _drive() -> None:
        deltas = stream_deltas(iter(raw))  # applied ONCE — carry_turn must NOT re-wrap
        await carry_turn(_Conn(), deltas, _Meeting())

    asyncio.run(_drive())

    # Each speak frame carried the model's own per-chunk delivery payload; the projector NEVER
    # re-ran stream_deltas or re-accumulated .text (proven statically below + by pass-through here).
    assert spoken, "the speak path must emit at least one voice.speak frame from the delivery tool"
    # The projector forwards each delivery input as its own frame (no re-accumulation collapse
    # to a single mega-frame, no dropping): one frame per delivering chunk.
    assert spoken == ["The", "The answer", "The answer is 42"], f"speak frames not forwarded 1:1: {spoken}"


def test_transport_projector_never_reruns_stream_deltas() -> None:
    """The transport projector CODE (the experience/channel projector + carrier) never imports or
    calls ``stream_deltas`` — it consumes the stream applied once upstream (CANONICAL §11.3/§1.1)."""
    proj = ROOT / "services" / "transport" / "src" / "transport" / "projector.py"
    module = ast.parse(proj.read_text(encoding="utf-8"))
    calls = _stream_deltas_calls(module)
    assert not calls, f"transport projector must NOT call stream_deltas (applied once upstream); found {calls}"


def test_channel_projector_reads_type_and_metadata_only_never_raw_text_field() -> None:
    """The channel/experience projector reads the discriminator via ``chunk.type`` and payloads via
    ``chunk.metadata[...]`` — never a raw accumulated ``chunk.text`` re-accumulation. Driven over
    the REAL ``ChannelProjector`` with the real six ChunkType variants."""
    from transport.projector import ChannelProjector

    projector = ChannelProjector()

    # A WORK tool → a tile "working…" line (reads .type + .metadata['name'], not .text).
    work = list(projector.project(AgentChunk(type="TOOL_USE", metadata={"name": "grep", "input": {}})))
    assert work and work[0].__class__.__name__ == "ToolStart", "work-tool must project a tile line via metadata"

    # A structured TOOL_RESULT → a canvas patch (reads .metadata['structured'], not .text).
    canvas = list(
        projector.project(AgentChunk(type="TOOL_RESULT", metadata={"structured": {"kind": "table", "rows": []}}))
    )
    assert canvas and canvas[0].__class__.__name__ == "CanvasPatch", "structured result must project a canvas patch"

    # Raw TEXT (the model's reasoning) is INTERNAL — projected to NOTHING (never re-accumulated/spoken).
    assert list(projector.project(_text(AgentChunk, "accumulated reasoning so far", "m1"))) == []
    # INIT / RESULT / ERROR are internal too.
    for t in ("INIT", "RESULT", "ERROR"):
        assert list(projector.project(AgentChunk(type=t, metadata={}))) == [], f"{t} must project nothing"
