"""J-09-registry-closed · Doc 09 §2 (first bullet) — the cross-doc contract check closes E2E.

This is the whole-product *journey* proof for the closed-graph invariant, distinct from
the doc08 build test (``tests/doc08/test_m10_registry_canonical.py``, which proves the
*rebuild* to the canonical shape). Here we prove the discipline that stops registry
re-drift actually holds end-to-end against the **live** ``libs.contracts`` registry object
— the same call that runs at boot (fail-fast) and in CI:

    set(MessageType) == set(CHANNEL_REGISTRY)      (§4.1 set-equality)

with the §11.8 signal-surface exclusion honored. Every assertion runs the REAL path: it
imports the live ``libs.contracts`` package (firing import-time model self-registration +
the three declared produce/consume maps) and calls the shipped ``assert_registry_closed()``
against the real objects — no mocks, no hand-written type list.

Node: ``journey.registry-closed`` · spec_refs 09-VERIFICATION §2, 00-FOUNDATION §12,
08-EXPERIENCE §4.1 · CANONICAL §11.8 (signal-surface out of the client registry) /
§12.12 (produce/consume graph closure).

Definition of done proven here:
  1. ``assert_registry_closed()`` PASSES now against the live registry.
  2. It RAISES when a produced type has no consumer — proven two ways: (a) a fake
     unconsumed wire type injected directly into the LIVE ``CHANNEL_REGISTRY`` trips the
     shipped arg-less boot/CI call; (b) an injected enum-view carrying an orphan value.
  3. Adding a Doc-02 signal-surface type does NOT leak into the client registry — the
     exclusion is honored, and a leaked signal type still fails closure.

The test always restores the live registry in a ``finally`` so it never poisons the
process for any later test collected in the same session.
"""
from __future__ import annotations

import enum

import pytest

pytestmark = pytest.mark.e2e

# The exact canonical client wire set per 08-EXPERIENCE §4.1 (channel_action + 9 frames).
_CANONICAL_TYPES = frozenset(
    {
        "channel_action",
        "response.start",
        "response.chunk",
        "response.end",
        "voice.speak",
        "canvas.patch",
        "tool.start",
        "tile.state",
        "note.line",
        "draft.card",
    }
)


def test_live_registry_closes_now() -> None:
    """The shipped, arg-less ``assert_registry_closed()`` passes against the live registry.

    This is the exact call the boot path (fail-fast) and CI run. It imports the real
    ``libs.contracts`` package so import-time self-registration has populated
    ``CHANNEL_REGISTRY`` + the handler/projector maps, then proves set-equality holds.
    """
    from libs.contracts import CHANNEL_REGISTRY, MessageType, assert_registry_closed

    # Sanity: the live registry IS the canonical set — not a hand-written stand-in.
    assert set(CHANNEL_REGISTRY) == _CANONICAL_TYPES, (
        f"live CHANNEL_REGISTRY must equal the canonical §4.1 set; "
        f"extra={set(CHANNEL_REGISTRY) - _CANONICAL_TYPES}, "
        f"missing={_CANONICAL_TYPES - set(CHANNEL_REGISTRY)}"
    )
    assert {m.value for m in MessageType} == set(CHANNEL_REGISTRY), (
        "MessageType enum and CHANNEL_REGISTRY are not set-equal"
    )

    # The real boot/CI closure call — must not raise (a clean return IS the pass).
    assert_registry_closed()


def test_raises_on_produced_but_unconsumed_wire_type_live_registry() -> None:
    """A produced-but-unconsumed CLIENT wire type fails the SHIPPED boot/CI call.

    We inject a fake wire type directly into the LIVE ``CHANNEL_REGISTRY`` (a produced
    ``ProxyMessage`` model that no ``MessageType`` enum member declares — i.e. produced
    on the wire but with no consumer/enum entry) and call the exact arg-less
    ``assert_registry_closed()`` the boot path uses. Set-equality must break and it must
    raise. This is the anti-drift discipline: a new render frame added without registering
    its enum member cannot slip through.
    """
    from libs.contracts import CHANNEL_REGISTRY, assert_registry_closed

    class _FakeUnconsumedFrame:  # a stand-in produced ProxyMessage model
        pass

    orphan_type = "fake.unconsumed"
    assert orphan_type not in CHANNEL_REGISTRY  # precondition: really new

    CHANNEL_REGISTRY[orphan_type] = _FakeUnconsumedFrame  # type: ignore[assignment]  # inject an orphan
    try:
        with pytest.raises(AssertionError) as exc:
            assert_registry_closed()  # the SHIPPED, arg-less boot/CI call
        # the failure NAMES the orphan (registry-only side), not a vague message.
        assert orphan_type in str(exc.value), (
            f"closure must name the orphan {orphan_type!r}; got: {exc.value}"
        )
    finally:
        del CHANNEL_REGISTRY[orphan_type]

    # the live registry closes again once the orphan is removed (no residue).
    assert_registry_closed()  # must not raise


def test_raises_on_produced_but_unconsumed_via_enum_view() -> None:
    """Second proof: an injected enum-view whose value has no registered model fails.

    Complements the direct-registry proof — here the *enum* side carries an extra value
    (a type produced but with no model/consumer registered), the mirror of the drift
    where an enum member is added but its model/consumer wiring is forgotten.
    """
    from libs.contracts import assert_registry_closed

    class _ExtraView(enum.Enum):
        CHANNEL_ACTION = "channel_action"
        RESPONSE_START = "response.start"
        ORPHAN = "orphan.produced"  # produced value with no registered model

    with pytest.raises(AssertionError) as exc:
        assert_registry_closed(_ExtraView)
    assert "orphan.produced" in str(exc.value), (
        f"closure must name the union-only orphan; got: {exc.value}"
    )


def test_signal_surface_exclusion_is_honored() -> None:
    """§11.8: Doc-02 signal-surface types are OUT of the client registry (no leak).

    None of the in-process signal-surface events (transcript/roster/speaking/boundary/
    barge-in/bot-status/meeting-end/channel-report) may be client ``ProxyMessage`` keys,
    and the exclusion set is disjoint from the live client registry.
    """
    from libs.contracts import CHANNEL_REGISTRY
    from contracts.registry import SIGNAL_SURFACE_EVENTS

    overlap = SIGNAL_SURFACE_EVENTS & set(CHANNEL_REGISTRY)
    assert not overlap, (
        f"signal-surface events leaked into the client registry (§11.8): {sorted(overlap)}"
    )
    # the exclusion set is exactly the eight Doc-02 in-process events.
    assert SIGNAL_SURFACE_EVENTS == frozenset(
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


def test_adding_a_signal_type_does_not_leak_into_client_registry() -> None:
    """Adding a Doc-02 signal type does NOT trip closure UNLESS it leaks into the client set.

    Two-part proof of the exclusion boundary:
      * a signal-surface event that stays in its own surface (NOT in CHANNEL_REGISTRY)
        leaves closure GREEN — it is legitimately out of scope, not a violation; and
      * the moment such a signal type is (wrongly) registered as a client ProxyMessage,
        closure FAILS and names it — the leak is caught.
    """
    from libs.contracts import CHANNEL_REGISTRY, assert_registry_closed
    from contracts.registry import SIGNAL_SURFACE_EVENTS

    signal_type = next(iter(sorted(SIGNAL_SURFACE_EVENTS)))

    # Part 1: the signal type living outside the client registry is a no-op for closure.
    assert signal_type not in CHANNEL_REGISTRY
    # a Doc-02 signal type absent from the client registry must NOT trip closure.
    assert_registry_closed()  # must not raise

    # Part 2: leaking it into the client registry must fail closure and name it.
    class _LeakedSignalModel:
        pass

    CHANNEL_REGISTRY[signal_type] = _LeakedSignalModel  # type: ignore[assignment]  # inject a leak
    try:
        with pytest.raises(AssertionError) as exc:
            assert_registry_closed()
        assert signal_type in str(exc.value), (
            f"a leaked signal type must be named in the failure; got: {exc.value}"
        )
    finally:
        del CHANNEL_REGISTRY[signal_type]

    # registry restored → closes again.
    assert_registry_closed()  # must not raise
