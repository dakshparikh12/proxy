"""The Workroom's ONE output contract + tool-boundary progress events (05 §3.12).

This module is the node ``workroom.envelope`` — it realizes §3.12's single output
contract: it **assembles the sealed 8-field ``contracts.Envelope``** (AC-CMP-012 locks
its shape) from a completed ``query()`` result, and **mints a ``contracts.ProgressEvent``
at each REAL tool boundary** — read from the tool-use stream (``chunk.type == 'TOOL_USE'``),
NEVER from the model's TEXT prose. It is the value the Orchestrator receives back from a
bundle-dispatch: ``SessionDriver.run_task`` (the driver the harness dispatch invokes)
consumes the REAL ``contracts.Bundle`` and returns the REAL ``contracts.Envelope``
assembled here (the live-assembly seam — the doc04 lesson).

**Two responsibilities, each realized here and NOT in the model:**

  1. **Assemble the Envelope (§3.12).** ``{headline (speakable, ≤ a sentence or two) ·
     detail (chat-ready) · artifact? · receipts · status ∈ {done|partial|failed|
     needs_clarification|needs_review} · verification? ∈ {verified|unverified} (builds
     only) · draft_id? · task_id}`` — the sealed contract, round-trip-safe.
  2. **Emit tool-boundary progress (§3.12).** A ``ProgressEvent`` is the Envelope shape
     MINUS finality (no terminal status field). :func:`emit_tool_boundary_progress` passes
     every streamed chunk through untouched while minting ONE ProgressEvent per REAL
     TOOL_USE boundary to the harness sink — so the room sees live progress and the
     terminal fold still sees INIT/RESULT.

**The status/verification mapping is EXACTLY CANONICAL §1.2** (:func:`map_status_verification`):
a read-only answer or an applied+verified build → ``done``; a staged draft awaiting a
human click → ``needs_review``; a critic/evidence-gate-failed build → ``failed``.
``verified`` / ``draft`` are **NEVER** status values — the build's proof state rides the
optional ``verification`` field; smuggling it into ``status`` is the node's hard NOT-done.

**Never-throw (Rule 6 / §3.3).** A progress sink that raises must NOT abort the run:
:func:`emit_tool_boundary_progress` swallows a sink fault (a partial receipt beats a
crash mid-build) and keeps the stream flowing so the terminal Envelope still lands.

**e2b is NOT installed and this module never imports it** — it is pure host-side
assembly over ``contracts`` types and streamed ``AgentChunk``s; the E2B client is lazy
behind ``call_external`` (``libs/http``) and the template bake is the flagged Phase-3
residual, never faked here.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from uuid import UUID

from contracts import AgentChunk, Bundle, Envelope, EnvelopeStatus, ProgressEvent

# The write tools whose TOOL_USE boundary names a file that landed in the sandbox — used
# to phrase a progress headline. (The same set the session driver folds for wrote_paths.)
_WRITE_TOOLS = frozenset(
    {"mcp__code__write_file", "mcp__code__edit_file", "mcp__code__ast_grep"}
)

# A harness-side progress sink: receives one ProgressEvent per real tool boundary.
ProgressSink = Callable[[ProgressEvent], Awaitable[None]]


def map_status_verification(
    *,
    is_build: bool,
    verified: bool | None,
    has_draft: bool,
    failed: bool = False,
) -> tuple[EnvelopeStatus, str | None]:
    """Map a task outcome to ``(status, verification)`` EXACTLY per CANONICAL §1.2.

    The one place the §1.2 mapping lives — every visible ``status`` traces here:

      * a read-only answer (``is_build=False``) → ``("done", None)``: no proof state
        rides a lookup; ``verification`` is builds-only.
      * an applied + verified build (``verified=True``) → ``("done", "verified")``: the
        proof state rides the ``verification`` field, NEVER the status.
      * a staged draft awaiting a human click (``has_draft=True``, not verified) →
        ``("needs_review", "unverified")``: Law 3 — a world-touching act is staged.
      * a critic/evidence-gate-failed build (``failed=True``) → ``("failed",
        "unverified")``: failures are spoken plainly.
      * any other unverified build → ``("needs_review", "unverified")`` (staged for
        review — fail-closed: an unproven build is never silently ``done``).

    ``verified`` / ``draft`` are NOT ``EnvelopeStatus`` members and this function never
    returns them as a status — the hard NOT-done of the node.
    """
    if not is_build:
        # A read-only answer: `done`, no verification (proof state is builds-only, §1.2).
        return "done", None
    if failed:
        return "failed", "unverified"
    if verified:
        # Applied + verified: `done`, and the proof rides the verification field (§1.2).
        return "done", "verified"
    if has_draft:
        # A staged draft awaiting a human click → needs_review (§1.2 / Law 3).
        return "needs_review", "unverified"
    # An unverified build with nothing staged: fail-closed to needs_review (§3.7③) — an
    # unproven build is never silently `done`.
    return "needs_review", "unverified"


def build_envelope(
    *,
    bundle: Bundle,
    result_meta: dict[str, Any],
    wrote_paths: list[str],
    receipts: list[str] | None = None,
    detail: str | None = None,
    artifact_extra: dict[str, Any] | None = None,
    is_build: bool | None = None,
    verified: bool | None = None,
    has_draft: bool | None = None,
    failed: bool = False,
    draft_id: UUID | None = None,
) -> Envelope:
    """Assemble the terminal ``contracts.Envelope`` for a completed task (§3.12).

    The sealed 8-field contract (AC-CMP-012), assembled from the ``query()`` result:

      * ``headline`` — speakable (the answer is spoken in a meeting): ``Built: <ask>``
        when the task wrote a file, else ``Done: <ask>`` (trimmed to a sentence or two).
      * ``detail`` — chat-ready detail (citations inline), or ``None``.
      * ``artifact`` — the SDK cost + cache-split telemetry (how §3.9 proves the cached
        prefix hits) merged with any ``artifact_extra`` (e.g. the landed files list).
      * ``receipts`` — what ran / what landed (host-observed; the model's prose is never
        a receipt).
      * ``status`` / ``verification`` — mapped by :func:`map_status_verification` per
        §1.2 (``verified``/``draft`` are never status values).
      * ``draft_id`` — set for a staged code-change draft (Law 3).
      * ``task_id`` — the bundle's task_id (the 05→04 correlation).

    ``has_draft`` defaults to "a draft_id was produced" — so a staged code-change draft
    maps to ``needs_review`` without the caller restating the obvious. ``is_build``
    defaults to ``False``: a completed read/answer/write run returns an honest ``done``
    (writing a file is not, by itself, a verify-gated *build*). The verify-loop sibling
    node opts INTO build semantics explicitly — it calls this with ``is_build=True`` +
    the ``verified`` result + any ``draft_id`` — so the §1.2 needs_review/verified mapping
    fires only when a build actually ran the verify gate, never on a plain write.
    """
    if is_build is None:
        # A plain completed run (read/answer/write) → done. Build-verify semantics
        # (needs_review / verified) are opt-in by the verify node, never inferred here.
        is_build = draft_id is not None
    if has_draft is None:
        has_draft = draft_id is not None

    status, verification = map_status_verification(
        is_build=is_build,
        verified=verified,
        has_draft=bool(has_draft),
        failed=failed,
    )

    artifact = _build_artifact(result_meta, wrote_paths, artifact_extra)
    headline = _speakable_headline(bundle.ask, wrote=bool(wrote_paths))
    return Envelope(
        headline=headline,
        detail=detail,
        artifact=artifact,
        receipts=list(receipts or []),
        status=status,
        verification=verification,
        draft_id=draft_id,
        task_id=bundle.task_id,
    )


def failure_envelope(bundle: Bundle, exc: BaseException) -> Envelope:
    """An honest ``failed`` Envelope — the driver never throws (Rule 6 / §3.12).

    Speaks the failure plainly (a partial receipt beats a false claim); the receipt names
    the fault class without leaking secrets."""
    return Envelope(
        headline=_speakable_headline(bundle.ask, wrote=False, failed=True),
        detail=None,
        artifact=None,
        receipts=[f"task failed: {type(exc).__name__}"],
        status="failed",
        verification=None,
        draft_id=None,
        task_id=bundle.task_id,
    )


def progress_event_for_chunk(chunk: AgentChunk, task_id: UUID) -> ProgressEvent | None:
    """Mint a ``ProgressEvent`` from a REAL tool boundary, else ``None`` (§3.12).

    A boundary is a ``chunk.type == 'TOOL_USE'`` frame from the delta-ized stream — the
    tool the agent actually invoked. Every other chunk type (``INIT`` / ``TEXT`` /
    ``TOOL_RESULT`` / ``RESULT`` / ``ERROR``) returns ``None``: progress is derived from
    the tool-use stream, NEVER from the model's TEXT prose (the hard NOT-done — a model
    narrating "I ran the tests" is prose, not a boundary). The event is the Envelope
    shape MINUS finality — it carries NO terminal ``status``.
    """
    if chunk.type != "TOOL_USE":
        return None
    meta = chunk.metadata or {}
    name = str(meta.get("name", "")) or "a tool"
    tool = _short_tool_name(name)
    path = (meta.get("input") or {}).get("path") if isinstance(meta.get("input"), dict) else None
    if name in _WRITE_TOOLS and isinstance(path, str) and path:
        headline = f"{tool}: {path}"
        receipts = [f"{tool} {path}"]
    else:
        headline = f"running {tool}"
        receipts = [f"{tool}"]
    return ProgressEvent(
        headline=headline,
        detail=None,
        artifact=None,
        receipts=receipts,
        task_id=task_id,
    )


async def emit_tool_boundary_progress(
    chunks: AsyncIterator[AgentChunk],
    task_id: UUID,
    on_progress: ProgressSink | None,
) -> AsyncIterator[AgentChunk]:
    """Pass every streamed chunk through untouched, minting ONE ProgressEvent per REAL
    tool boundary to ``on_progress`` (§3.12 — the harness receives tool-boundary progress).

    ``chunks`` is the delta-ized provider stream (``stream_deltas`` output) — so field
    access is ``chunk.type`` / ``chunk.metadata`` only (CANONICAL §1.1, never raw
    ``AgentChunk`` from the SDK). The passthrough lets the terminal fold in the driver
    still observe INIT/RESULT/write-TOOL_USE while the room sees live progress.

    **Never-throw (Rule 6):** a sink that raises does NOT abort the run — the fault is
    swallowed and the stream keeps flowing (a partial receipt beats a crash mid-build).
    When ``on_progress`` is ``None`` this is a pure passthrough (no progress channel).
    """
    async for chunk in chunks:
        if on_progress is not None:
            event = progress_event_for_chunk(chunk, task_id)
            if event is not None:
                try:
                    await on_progress(event)
                except Exception:  # noqa: BLE001 - Rule 6: a sink fault never aborts the build
                    pass
        yield chunk


# -- internals ---------------------------------------------------------------


def _build_artifact(
    result_meta: dict[str, Any],
    wrote_paths: list[str],
    artifact_extra: dict[str, Any] | None,
) -> dict[str, Any]:
    """The Envelope ``artifact`` payload: SDK cost + cache-split telemetry (§3.9) merged
    with any caller-supplied extra (e.g. the landed files list)."""
    cost = {
        "total_cost_usd": float(result_meta.get("total_cost_usd", 0.0) or 0.0),
        "cache_creation_input_tokens": int(result_meta.get("cache_creation_input_tokens", 0) or 0),
        "cache_read_input_tokens": int(result_meta.get("cache_read_input_tokens", 0) or 0),
        "input_tokens": int(result_meta.get("input_tokens", 0) or 0),
    }
    artifact: dict[str, Any] = {"cost": cost, "session_id": result_meta.get("session_id")}
    if wrote_paths:
        artifact["files"] = list(wrote_paths)
    if artifact_extra:
        artifact.update(artifact_extra)
    return artifact


def _speakable_headline(ask: str, *, wrote: bool, failed: bool = False) -> str:
    """A speakable headline (≤ a sentence or two — it is spoken aloud, §3.12).

    ``Built: <ask>`` when the task wrote a file, ``Couldn't finish: <ask>`` on a fault,
    else ``Done: <ask>``; the ask is trimmed so the spoken line stays short."""
    ask = " ".join(ask.split())
    if len(ask) > 180:
        ask = ask[:177].rstrip() + "..."
    if failed:
        return f"Couldn't finish: {ask}"
    return f"Built: {ask}" if wrote else f"Done: {ask}"


def _short_tool_name(name: str) -> str:
    """The bare tool name for a speakable progress line — drop the ``mcp__<server>__``
    prefix so the room hears ``write_file``, not ``mcp__code__write_file``."""
    if name.startswith("mcp__"):
        return name.rsplit("__", 1)[-1]
    return name


__all__ = [
    "ProgressSink",
    "build_envelope",
    "emit_tool_boundary_progress",
    "failure_envelope",
    "map_status_verification",
    "progress_event_for_chunk",
]
