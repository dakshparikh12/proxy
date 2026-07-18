"""The client ``ProxyMessage`` registry + closure/field-contract checks.

AC-CMP-009 (produce/consume field contract), AC-CMP-011 (signal-surface events
are excluded from the client registry closure). The Doc 02 signal surface
(transcript/roster/speaking/boundary/barge-in/bot-status/meeting-end/
channel-report) is intentionally NOT part of the client ProxyMessage registry.
"""
from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel

# type-string -> ProxyMessage subclass. Populated by ``ProxyMessage.__init_subclass__``.
CHANNEL_REGISTRY: dict[str, type["ProxyMessage"]] = {}

# Doc 02 signal-surface events — outside the client registry closure by design.
SIGNAL_SURFACE_EVENTS: frozenset[str] = frozenset(
    {
        "transcript",
        "roster",
        "speaking",
        "boundary",
        "barge-in",
        "bot-status",
        "meeting-end",
        "channel-report",
    }
)


class ProxyMessage(BaseModel):
    """Base for client<->backend wire messages.

    A concrete message declares ``message_type`` (a stable string). Declaring one
    auto-registers the subclass in :data:`CHANNEL_REGISTRY`.
    """

    message_type: ClassVar[str | None] = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        mt = cls.__dict__.get("message_type")
        if mt is not None:
            if mt in CHANNEL_REGISTRY and CHANNEL_REGISTRY[mt] is not cls:
                raise ValueError(f"duplicate ProxyMessage type registered: {mt!r}")
            CHANNEL_REGISTRY[str(mt)] = cls


def assert_registry_closed() -> None:
    """Assert the client registry is closed: no signal-surface event leaked in.

    Raises ``AssertionError`` on a violation (used at boot and in CI).
    """
    leaked = SIGNAL_SURFACE_EVENTS & set(CHANNEL_REGISTRY)
    if leaked:
        raise AssertionError(
            f"signal-surface events leaked into the client registry: {sorted(leaked)}"
        )


def assert_fields_consumed(
    *, produced: dict[str, set[str]], consumed: dict[str, set[str]]
) -> list[str]:
    """Return the list of produced-but-unconsumed contract fields (AC-CMP-009).

    A non-empty return is a build-failing violation naming each orphan field.
    """
    violations: list[str] = []
    for signal, fields in produced.items():
        seen = set(consumed.get(signal, set()))
        for orphan in sorted(set(fields) - seen):
            violations.append(f"{signal}.{orphan} produced but never consumed")
    return violations
