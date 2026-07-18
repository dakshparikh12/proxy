"""The emit frontier — the SOLE outward delivery / side-effect authority.

Every member of the frontier {speak, send_chat, show_screen, apply, dispatch} is
gated on ``is_owner``. A process whose operation_runs row was reclaimed (lost the
fence, is_owner False) is a zombie: every verb refuses and NOTHING reaches the
wire. The set is enumerated in ``EMIT_FRONTIER`` so a new outward verb added
outside the gate fails static completeness. ``dispatch`` stays single-def in
libs/http (AC-CMP-010); the emitter exposes it as a gated bound attribute, not a
redefinition.
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

    def __init__(self, is_owner: bool, sink: Callable[..., Any]) -> None:
        self._is_owner = is_owner
        self._sink = sink
        # 'dispatch' is the libs/http funnel verb — exposed as a gated bound
        # attribute so it is never a second definition of dispatch().
        self.dispatch: Callable[[Any], bool] = self._gated("dispatch")

    def _gated(self, verb: str) -> Callable[[Any], bool]:
        def _emit(payload: Any) -> bool:
            if not self._is_owner:
                return False
            self._sink(verb, payload)
            return True

        return _emit

    def speak(self, payload: Any) -> bool:
        if not self._is_owner:
            return False
        self._sink("speak", payload)
        return True

    def send_chat(self, payload: Any) -> bool:
        if not self._is_owner:
            return False
        self._sink("send_chat", payload)
        return True

    def show_screen(self, payload: Any) -> bool:
        if not self._is_owner:
            return False
        self._sink("show_screen", payload)
        return True

    def apply(self, payload: Any) -> bool:
        if not self._is_owner:
            return False
        self._sink("apply", payload)
        return True


def build_emitter(*, is_owner: bool, sink: Callable[..., Any]) -> Emitter:
    """Build an emitter bound to a process's ownership state."""
    return Emitter(is_owner=is_owner, sink=sink)
