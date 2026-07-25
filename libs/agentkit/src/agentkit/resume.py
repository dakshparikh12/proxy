"""Single definition home for ``resume_with_fallback`` (AC-CMP-010) — the
two-tier session-durability fallback (§3.5).

Tier 1 resumes the SDK ``session_id``; Tier 2 (this function's ``except`` arm)
rebuilds context from the app-level meeting history (the Postgres transcript
plane, Doc 03) when the resumed session is gone, prepends a delimited preamble,
emits a user-visible "session restored" notice, and retries WITHOUT resume.

Pinned full 6-arg signature (Doc 04/05, A-010), parameterized by the history
*source* (``history_fn``): Doc 04 passes Doc 03's transcript-plane reader, Doc 05
passes its own. Defined exactly once, here in ``libs/agentkit``.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any

from libs.contracts import AgentChunk

from .provider import ProviderError

# Match strings the SDK reports a gone session as, across versions — match BOTH
# (re-audit on every SDK upgrade, §3.5).
STALE_MARKERS = ("no conversation found with session id", "process exited")
RESTORED_NOTICE = (
    "_My session was restored from the meeting so far; "
    "some working context may be missing._"
)


def is_stale_session_error(err: BaseException) -> bool:
    """True when the error is a gone-session error the replay tier should recover."""
    msg = (str(err) or "").lower()
    return any(m in msg for m in STALE_MARKERS)


def build_history_preamble(history: Any) -> str:
    """Wrap rebuilt meeting history in a delimited preamble the model reads as DATA."""
    body = history if isinstance(history, str) else "\n".join(str(h) for h in (history or []))
    return f"--- BEGIN PRIOR MEETING ---\n{body}\n--- END PRIOR MEETING ---"


async def resume_with_fallback(
    runner: Any,
    behavior: Any,
    inputs: Mapping[str, Any],
    resume_id: str | None,
    abort: Any,
    history_fn: Callable[[], Awaitable[Any]],
) -> AsyncIterator[AgentChunk]:
    """Run ``behavior`` through ``runner``, resuming ``resume_id``; on a stale-session
    ``ProviderError`` rebuild from ``history_fn()`` and retry without resume.

    A caller-initiated abort is FINAL — it short-circuits before any recovery, so
    a killed build is never resurrected by resume.
    """
    try:
        async for chunk in runner.run(behavior, {**inputs, "resume": resume_id}, abort):
            yield chunk
    except ProviderError as exc:
        if getattr(abort, "aborted", False):   # a caller-abort is FINAL — never resurrect it
            raise
        if not (resume_id and is_stale_session_error(exc)):
            raise
        # Rebuild from app history, prepend a delimited preamble, notify the room.
        history = await history_fn()            # reads the Postgres transcript plane (Doc 03)
        yield AgentChunk(type="TEXT", text=RESTORED_NOTICE, metadata={"msg_id": "restored"})
        preamble = build_history_preamble(history)
        async for chunk in runner.run(
            behavior, {**inputs, "resume": None, "preamble": preamble}, abort
        ):
            yield chunk
