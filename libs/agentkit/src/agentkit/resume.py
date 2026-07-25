"""Single definition home for ``resume_with_fallback`` (AC-CMP-010) — the
two-tier session-durability fallback (§3.5).

Tier 1 resumes the SDK ``session_id``; the stale-session replay tier (this
function's ``except`` arm) rebuilds context from the app-level meeting history
(the Postgres transcript plane, Doc 03) when the resumed session is *gone*,
prepends a delimited preamble, emits a user-visible "session restored" notice,
and retries WITHOUT resume.

Two distinct failure classes are recovered here, and they must not be confused:

  * a **gone session** (:data:`STALE_MARKERS`) → rebuild from ``history_fn`` and
    retry *without* resume (a new session; the old context is lost, so the
    transcript-plane replay is how the room stays coherent);
  * a **truncated stdio frame** (:data:`JSON_TRUNCATION_MARKERS` — the SDK pipe
    can cut a large tool-result frame → ``SyntaxError: unterminated string in
    json``) → the session is still alive, so retry on the SAME session (resume
    unchanged), capped at :data:`JSON_TRUNCATION_RETRY_CAP` attempts. No notice,
    no history rebuild — nothing was lost, just re-read the frame.

A caller-initiated abort is FINAL: it short-circuits before EITHER recovery, so a
build the user killed can never be resurrected by resume or by the JSON retry.

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
# The SDK's stdio pipe can truncate a large tool-result frame mid-JSON; the CLI
# reports it as an unterminated-string decode error. It means the FRAME was cut,
# NOT that the session is gone — recover by re-reading on the same session.
JSON_TRUNCATION_MARKERS = ("unterminated string in json",)
# Cap the same-session JSON-truncation retry so a persistently-truncating frame
# can never loop forever (§3.5: "retry on the same session, cap 2").
JSON_TRUNCATION_RETRY_CAP = 2
RESTORED_NOTICE = (
    "_My session was restored from the meeting so far; "
    "some working context may be missing._"
)


def is_stale_session_error(err: BaseException) -> bool:
    """True when the error is a gone-session error the replay tier should recover."""
    msg = (str(err) or "").lower()
    return any(m in msg for m in STALE_MARKERS)


def is_json_truncation_error(err: BaseException) -> bool:
    """True when the error is a truncated stdio frame (session still alive).

    Distinct from :func:`is_stale_session_error`: a gone session is rebuilt from
    history and retried WITHOUT resume; a truncated frame is retried on the SAME
    session (nothing was lost, the pipe just cut the frame).
    """
    msg = (str(err) or "").lower()
    return any(m in msg for m in JSON_TRUNCATION_MARKERS)


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
    """Run ``behavior`` through ``runner``, resuming ``resume_id``; recover the two
    §3.5 failure classes and re-raise everything else.

    * **Truncated stdio frame** (:func:`is_json_truncation_error`) — the session is
      still alive, so retry on the SAME session (``resume`` unchanged), capped at
      :data:`JSON_TRUNCATION_RETRY_CAP` attempts; no notice, no history rebuild.
    * **Gone session** (:func:`is_stale_session_error`) — rebuild from
      ``history_fn()`` (Doc 03's transcript plane), prepend a delimited preamble,
      emit the user-visible RESTORED_NOTICE, and retry WITHOUT resume.

    A caller-initiated abort is FINAL — it short-circuits before EITHER recovery,
    so a killed build is never resurrected by resume or by the JSON retry.
    """
    # Same-session pass: retry a truncated frame on the live session, capped so a
    # persistently-truncating frame can never loop forever. A gone-session error
    # breaks out of this loop into the transcript-plane replay below.
    attempts = 0
    while True:
        try:
            async for chunk in runner.run(behavior, {**inputs, "resume": resume_id}, abort):
                yield chunk
            return
        except ProviderError as exc:
            if getattr(abort, "aborted", False):   # a caller-abort is FINAL — never resurrect it
                raise
            if is_json_truncation_error(exc) and attempts < JSON_TRUNCATION_RETRY_CAP:
                attempts += 1                      # re-read the cut frame on the SAME session
                continue
            if resume_id and is_stale_session_error(exc):
                break                              # session gone → transcript-plane replay
            raise                                  # unknown fault (or retry cap hit): surface it

    # Gone-session replay: rebuild from app history, prepend a delimited preamble,
    # notify the room, and retry WITHOUT resume (the new session overwrites the
    # stale pointer). Not looped — one honest replay from the durable transcript.
    history = await history_fn()                    # reads the Postgres transcript plane (Doc 03)
    yield AgentChunk(type="TEXT", text=RESTORED_NOTICE, metadata={"msg_id": "restored"})
    preamble = build_history_preamble(history)
    async for chunk in runner.run(
        behavior, {**inputs, "resume": None, "preamble": preamble}, abort
    ):
        yield chunk
