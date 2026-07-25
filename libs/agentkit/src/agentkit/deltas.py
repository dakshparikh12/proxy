"""The per-msg_id suffix delta-izer (AC-CMP-015).

Defined once here and exposed under the public name as an alias, so the only
call token in the whole tree is its single call site inside
``BehaviorRunner.run`` — AC-CMP-005. Non-idempotent by construction: applying
it to its own output corrupts the deltas.

``stream_deltas`` is polymorphic on the *shape* of its one upstream argument:

  * a **sync** ``Iterable[AgentChunk]`` (a scripted list, a recorded cassette)
    → a **sync** ``Iterator[AgentChunk]`` (so applying it over ``iter(x)`` returns
    a materializable iterator — the canonical contract oracle, AC-CMP-015); and
  * an **async** ``AsyncIterator[AgentChunk]`` (a live provider stream)
    → an **async** ``AsyncIterator[AgentChunk]``.

Both share ONE delta state machine (:class:`_DeltaState`) so the semantics —
per-``msg_id`` suffix on ``TEXT``, everything else forwarded unchanged — cannot
drift between the two shapes. This is what lets the async ``BehaviorRunner.run``
(§3.4) apply the delta-izer exactly once over the provider's *async* raw stream
while the sync contract test keeps exercising the identical logic.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Iterator
from typing import Any

from libs.contracts import AgentChunk


class _DeltaState:
    """The per-``msg_id`` accumulator shared by the sync and async delta-izers.

    ``feed`` maps one raw upstream chunk to the one chunk that should be yielded
    downstream: a ``TEXT`` chunk becomes the *new suffix* accumulated for its
    ``msg_id`` (resetting when a new ``msg_id`` appears); every other chunk type
    (``INIT``/``TOOL_USE``/``TOOL_RESULT``/``RESULT``/``ERROR``) passes through
    untouched so tool events and the terminal frame survive to every consumer.
    """

    __slots__ = ("_seen",)

    def __init__(self) -> None:
        self._seen: dict[str, str] = {}

    def feed(self, chunk: AgentChunk) -> AgentChunk:
        if chunk.type != "TEXT":
            return chunk
        msg_id = str(chunk.metadata.get("msg_id", ""))
        accumulated = chunk.text or ""
        previous = self._seen.get(msg_id, "")
        self._seen[msg_id] = accumulated
        return AgentChunk(
            type="TEXT",
            text=accumulated[len(previous):],
            metadata=chunk.metadata,
        )


def _is_async_iterable(obj: Any) -> bool:
    return hasattr(obj, "__aiter__")


def _deltaize_sync(chunks: Iterable[AgentChunk]) -> Iterator[AgentChunk]:
    state = _DeltaState()
    for chunk in chunks:
        yield state.feed(chunk)


async def _deltaize_async(chunks: AsyncIterator[AgentChunk]) -> AsyncIterator[AgentChunk]:
    state = _DeltaState()
    async for chunk in chunks:
        yield state.feed(chunk)


def _deltaize(chunks: Any) -> Any:
    """Delta-ize a raw ``AgentChunk`` stream — sync in → sync out, async in → async out.

    Returns a sync ``Iterator`` for a sync ``Iterable`` and an async iterator for
    an ``AsyncIterator``; the shape of the return mirrors the shape of the input
    so callers of either flavour receive an object they can iterate the same way.
    """
    if _is_async_iterable(chunks):
        return _deltaize_async(chunks)
    return _deltaize_sync(chunks)


# Public name — an alias, never re-invoked under this identifier except once.
stream_deltas = _deltaize
