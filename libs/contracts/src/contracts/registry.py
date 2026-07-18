"""The client ``ProxyMessage`` registry + closure/field-contract checks (§12).

Every client<->backend message is a Pydantic model that self-registers on
definition (``__pydantic_init_subclass__``) keyed on its ``type`` discriminator
default. ``assert_registry_closed()`` proves the ``MessageType`` enum and the
registry are set-equal — a produced-but-unconsumed message fails the build. The
connect page is a public URL, so tile->backend inbound is untrusted: the dispatch
funnel validates every inbound message once, centrally.
"""
from __future__ import annotations

import enum
from typing import Any, Literal, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# type-string -> ProxyMessage subclass. Populated by ``__pydantic_init_subclass__``.
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


class MessageType(enum.Enum):
    """The closed discriminator enum for client<->backend messages (§12)."""

    CONNECT_REPO = "connect-repo"
    APPROVE_DRAFT = "approve-draft"
    INVITE_PROXY = "invite-proxy"


class ProxyMessage(BaseModel):
    """Base for client<->backend wire messages; subclasses auto-register."""

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        field = cls.model_fields.get("type")
        if field is None or field.default is None:
            return
        default = field.default
        key = default.value if isinstance(default, enum.Enum) else str(default)
        if key in CHANNEL_REGISTRY and CHANNEL_REGISTRY[key] is not cls:
            raise ValueError(f"duplicate ProxyMessage type registered: {key!r}")
        CHANNEL_REGISTRY[key] = cls


# ── concrete client->backend messages (tile is untrusted; §12) ───────────────
class ConnectRepoMessage(ProxyMessage):
    """Connect page: bind a repository to the tenant."""

    type: Literal["connect-repo"] = "connect-repo"
    repo_full_name: str = Field(max_length=200)


class ApproveDraftMessage(ProxyMessage):
    """Tile: a named human approves a staged draft (world-touching)."""

    type: Literal["approve-draft"] = "approve-draft"
    draft_id: UUID


class InviteProxyMessage(ProxyMessage):
    """Tile: invite Proxy into a meeting."""

    type: Literal["invite-proxy"] = "invite-proxy"
    meeting_id: UUID


def _closure_values(message_type: Any) -> set[str]:
    """The discriminator value-set for the closure comparison."""
    if isinstance(message_type, type) and issubclass(message_type, enum.Enum):
        return {str(m.value) for m in message_type}
    # An injected union/tuple/Literal-args view (used by the orphan-rejection test).
    members = get_args(message_type) or tuple(message_type)
    out: set[str] = set()
    for m in members:
        out.add(str(m.value) if isinstance(m, enum.Enum) else str(m))
    return out


def assert_registry_closed(message_type: Any | None = None) -> None:
    """Assert the discriminator enum and the registry are set-equal (§12).

    Runs at boot (fail-fast) and in CI. Also guards that no Doc 02 signal-surface
    event leaked into the client registry (AC-CMP-011).
    """
    values = _closure_values(message_type if message_type is not None else MessageType)
    registry = {str(k) for k in CHANNEL_REGISTRY}
    if values != registry:
        raise AssertionError(
            "closed-graph violation: "
            f"union-only={values - registry}, registry-only={registry - values}"
        )
    leaked = SIGNAL_SURFACE_EVENTS & registry
    if leaked:
        raise AssertionError(
            f"signal-surface events leaked into the client registry: {sorted(leaked)}"
        )


def validate_inbound_message(payload: Any) -> ProxyMessage:
    """The ONE central dispatch-funnel validator for untrusted tile->backend input.

    Rejects a non-mapping, a missing/unknown discriminator, a malformed body, or an
    oversized free-text field (bounded by each model's ``Field(max_length=...)``).
    """
    if not isinstance(payload, dict):
        raise TypeError("inbound message must be a JSON object")
    discriminator = payload.get("type")
    model = CHANNEL_REGISTRY.get(str(discriminator)) if discriminator is not None else None
    if model is None:
        raise ValueError(f"unregistered message type: {discriminator!r}")
    try:
        return model.model_validate(payload)
    except ValidationError as exc:  # bounded/typed fields reject malformed+oversized
        raise ValueError(f"invalid {discriminator!r} message: {exc}") from exc


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
