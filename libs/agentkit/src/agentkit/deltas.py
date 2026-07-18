"""The per-msg_id suffix delta-izer (AC-CMP-015).

Defined once here and exposed under the public name as an alias, so the only
call token in the whole tree is its single call site inside
``BehaviorRunner.run`` — AC-CMP-005. Non-idempotent by construction: applying
it to its own output corrupts the deltas.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator

from libs.contracts import AgentChunk


def _deltaize(chunks: Iterable[AgentChunk]) -> Iterator[AgentChunk]:
    seen: dict[str, str] = {}
    for chunk in chunks:
        if chunk.type == "TEXT":
            msg_id = str(chunk.metadata.get("msg_id", ""))
            accumulated = chunk.text or ""
            previous = seen.get(msg_id, "")
            seen[msg_id] = accumulated
            yield AgentChunk(
                type="TEXT",
                text=accumulated[len(previous):],
                metadata=chunk.metadata,
            )
        else:
            yield chunk


# Public name — an alias, never re-invoked under this identifier except once.
stream_deltas = _deltaize
