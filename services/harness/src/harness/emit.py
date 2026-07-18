"""The emit frontier — the SOLE outward delivery / side-effect authority.

Every member of the frontier {speak, send_chat, show_screen, apply, dispatch} is
gated on ``is_owner``. A process whose operation_runs row was reclaimed (lost the
fence, is_owner False) is a zombie: every verb refuses and NOTHING reaches the
wire. The set is enumerated in ``EMIT_FRONTIER`` so a new outward verb added
outside the gate fails static completeness. ``dispatch`` stays single-def in
libs/http (AC-CMP-010); the emitter exposes it as a gated bound attribute, not a
redefinition.

The emitter binds either a live ``OperationHandle`` (``is_owner`` is read fresh on
every verb, so a mid-turn fence loss immediately silences the wire) or a static
``is_owner`` flag with a ``sink`` (the spec-derived surface ``build_emitter``
exposes).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

# The canonical, explicitly-enumerated gated-emit frontier (§5.1 + §12.3).
EMIT_FRONTIER: frozenset[str] = frozenset(
    {"speak", "send_chat", "show_screen", "apply", "dispatch"}
)


class Emitter:
    """Outward-verb surface; each verb is gated on ownership of the meeting."""

    def __init__(
        self,
        handle: Any = None,
        sink: Callable[..., Any] | None = None,
        *,
        is_owner: bool | None = None,
    ) -> None:
        # Handle mode: an object exposing a live ``is_owner`` (OperationHandle).
        self._handle = handle if (handle is not None and hasattr(handle, "is_owner")) else None
        # Static mode: a fixed ownership flag + a sink (the build_emitter surface).
        self._static_owner = bool(is_owner) if self._handle is None else False
        self._sink = sink
        self._wire: list[tuple[str, Any]] = []
        # 'dispatch' is the libs/http funnel verb — a gated bound attribute so it
        # is never a second definition of dispatch().
        self.dispatch: Callable[[Any], bool] = self._gated("dispatch")

    @property
    def is_owner(self) -> bool:
        """Live ownership of the meeting row — the emit-frontier fence."""
        if self._handle is not None:
            return bool(self._handle.is_owner)
        return self._static_owner

    def _deliver(self, verb: str, payload: Any) -> bool:
        """Deliver one verb iff is_owner; a zombie (not owner) emits nothing."""
        if not self.is_owner:
            return False
        self._wire.append((verb, payload))
        if self._sink is not None:
            self._sink(verb, payload)
        return True

    def _gated(self, verb: str) -> Callable[[Any], bool]:
        def _emit(payload: Any) -> bool:
            if not self.is_owner:
                return False
            return self._deliver(verb, payload)

        return _emit

    def attempt(self, verb: str, payload: Any = None) -> bool:
        """Attempt one frontier verb; returns True iff it reached the wire."""
        if verb not in EMIT_FRONTIER:
            return False
        if not self.is_owner:
            return False
        return self._deliver(verb, payload)

    def drain_wire(self) -> list[tuple[str, Any]]:
        """Return and clear everything actually put on the wire this turn."""
        out = list(self._wire)
        self._wire.clear()
        return out

    def speak(self, payload: Any) -> bool:
        if not self.is_owner:
            return False
        return self._deliver("speak", payload)

    def send_chat(self, payload: Any) -> bool:
        if not self.is_owner:
            return False
        return self._deliver("send_chat", payload)

    def show_screen(self, payload: Any) -> bool:
        if not self.is_owner:
            return False
        return self._deliver("show_screen", payload)

    def apply(self, payload: Any) -> bool:
        if not self.is_owner:
            return False
        return self._deliver("apply", payload)


def build_emitter(*, is_owner: bool, sink: Callable[..., Any]) -> Emitter:
    """Build an emitter bound to a process's ownership state."""
    return Emitter(is_owner=is_owner, sink=sink)
