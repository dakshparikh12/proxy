"""Doc 08 · §4.1 — the CANONICAL client ProxyMessage registry (render frames + channel_action).

This node REBUILDS ``libs/contracts/registry.py`` from the pre-canonical
``{connect-repo, approve-draft, invite-proxy}`` shape (CANONICAL §12.9/§12.12
explicitly delete it) to §4.1's canonical set: the single INBOUND type
``channel_action`` plus the OUTBOUND render-frame family. The closure check
(``assert_registry_closed``) is extended from set-equality to ALSO require every
inbound type has exactly one handler and every outbound type has ≥1 projector
(CANONICAL §12.12: "add MESSAGE_PRODUCERS/HANDLERS/PROJECTORS registries — every
inbound type 1 handler, every outbound ≥1 projector").

Every test runs the REAL path: it imports the live ``libs.contracts`` package
(triggering import-time self-registration + the three declared maps) and asserts
against the real objects. No mocks. Product imports live inside the test bodies so
the module COLLECTS clean and would fail RED before the rebuild.

Signal-surface events (§11.8 — transcript/roster/speaking/boundary/barge-in/
bot-status/meeting-end/channel-report) stay OUT of the client registry; a leak
still fails closure.
"""
from __future__ import annotations

import pytest

# The exact canonical set per 08-EXPERIENCE.md §4.1.
_CANONICAL_TYPES = {
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
_INBOUND = {"channel_action"}
_OUTBOUND = _CANONICAL_TYPES - _INBOUND


# ── MessageType is EXACTLY the 10 canonical types ─────────────────────────────
def test_message_type_is_exactly_the_canonical_ten():
    """MessageType == {channel_action + the 9 render frames}; no pre-canonical survivors."""
    from libs.contracts import MessageType

    values = {m.value for m in MessageType}
    assert values == _CANONICAL_TYPES, (
        f"MessageType must be EXACTLY the canonical 10; "
        f"extra={values - _CANONICAL_TYPES}, missing={_CANONICAL_TYPES - values}"
    )
    # The pre-canonical types must not survive ANYWHERE in the enum.
    for dead in ("connect-repo", "approve-draft", "invite-proxy"):
        assert dead not in values, f"pre-canonical type {dead!r} must be deleted from MessageType"


def test_pre_canonical_models_are_gone():
    """The deleted models must not be importable from contracts.registry."""
    import contracts.registry as reg

    for dead in ("ConnectRepoMessage", "ApproveDraftMessage", "InviteProxyMessage"):
        assert not hasattr(reg, dead), (
            f"pre-canonical model {dead!r} must be deleted (D-006 breaking migration)"
        )


# ── the registry is set-equal to the enum (canonical set) ─────────────────────
def test_channel_registry_set_equal_to_canonical():
    """Every MessageType value has a registered model and vice-versa (set-equality)."""
    from libs.contracts import CHANNEL_REGISTRY, MessageType

    values = {m.value for m in MessageType}
    registry = set(CHANNEL_REGISTRY)
    assert values == registry, (
        f"registry↔enum set-equality broken: enum-only={values - registry}, "
        f"registry-only={registry - values}"
    )
    assert registry == _CANONICAL_TYPES


# ── INBOUND / OUTBOUND partition ──────────────────────────────────────────────
def test_inbound_outbound_partition():
    """INBOUND == {channel_action}; OUTBOUND == the render frames; together == MessageType."""
    from contracts.registry import INBOUND, OUTBOUND, MessageType

    inbound = {m.value for m in INBOUND}
    outbound = {m.value for m in OUTBOUND}
    assert inbound == _INBOUND, f"INBOUND must be exactly {{channel_action}}, got {inbound}"
    assert outbound == _OUTBOUND, f"OUTBOUND must be the render frames, got {outbound}"
    # partition: disjoint and covering.
    assert not (inbound & outbound), "INBOUND and OUTBOUND must be disjoint"
    assert (inbound | outbound) == {m.value for m in MessageType}


# ── the three declared maps exist ─────────────────────────────────────────────
def test_three_declared_maps_exist():
    """MESSAGE_HANDLERS, MESSAGE_PROJECTORS, MESSAGE_PRODUCERS all exist as maps."""
    from contracts.registry import (
        MESSAGE_HANDLERS,
        MESSAGE_PRODUCERS,
        MESSAGE_PROJECTORS,
    )

    assert isinstance(MESSAGE_HANDLERS, dict)
    assert isinstance(MESSAGE_PROJECTORS, dict)
    assert isinstance(MESSAGE_PRODUCERS, dict)


# ── closure: set-equality AND handler/projector coverage ──────────────────────
def test_closure_passes_on_the_shipped_registry():
    """assert_registry_closed() passes (no raise) on the shipped, closed graph."""
    from libs.contracts import assert_registry_closed

    result = assert_registry_closed()
    assert result is None or result is True


def test_every_inbound_has_exactly_one_handler():
    """MESSAGE_HANDLERS: every inbound type maps to exactly one handler."""
    from contracts.registry import INBOUND, MESSAGE_HANDLERS

    for t in INBOUND:
        assert t in MESSAGE_HANDLERS, f"inbound {t.value!r} has no handler"
        # exactly-one: the value is a single handler, not a list of many.
        handler = MESSAGE_HANDLERS[t]
        assert handler is not None and not isinstance(handler, (list, tuple, set)), (
            f"inbound {t.value!r} must map to EXACTLY ONE handler, got {handler!r}"
        )


def test_every_outbound_has_at_least_one_projector():
    """MESSAGE_PROJECTORS: every outbound type maps to ≥1 projector."""
    from contracts.registry import MESSAGE_PROJECTORS, OUTBOUND

    for t in OUTBOUND:
        projectors = MESSAGE_PROJECTORS.get(t)
        assert projectors, f"outbound {t.value!r} has no projector (must be ≥1)"
        assert len(projectors) >= 1, f"outbound {t.value!r} must have ≥1 projector"


def test_closure_fails_when_an_inbound_handler_is_missing():
    """Closure is NOT just set-equality: a missing inbound handler must fail it."""
    from contracts.registry import INBOUND, MESSAGE_HANDLERS, assert_registry_closed

    victim = next(iter(INBOUND))
    saved = MESSAGE_HANDLERS.pop(victim)
    try:
        with pytest.raises((AssertionError, RuntimeError)):
            assert_registry_closed()
    finally:
        MESSAGE_HANDLERS[victim] = saved
    # restored graph closes again.
    assert assert_registry_closed() in (None, True)


def test_closure_fails_when_an_outbound_projector_is_missing():
    """Closure is NOT just set-equality: an outbound with no projector must fail it."""
    from contracts.registry import MESSAGE_PROJECTORS, OUTBOUND, assert_registry_closed

    victim = next(iter(OUTBOUND))
    saved = MESSAGE_PROJECTORS.pop(victim)
    try:
        with pytest.raises((AssertionError, RuntimeError)):
            assert_registry_closed()
    finally:
        MESSAGE_PROJECTORS[victim] = saved
    assert assert_registry_closed() in (None, True)


# ── signal-surface events still excluded (§11.8); a leak still fails ───────────
def test_signal_surface_events_excluded_and_leak_fails():
    """§11.8: signal-surface events are out of the client registry; a leaked one fails closure."""
    from libs.contracts import CHANNEL_REGISTRY, assert_registry_closed
    from contracts.registry import SIGNAL_SURFACE_EVENTS

    # none of the internal signal events are client registry keys.
    for name in SIGNAL_SURFACE_EVENTS:
        assert name not in CHANNEL_REGISTRY, (
            f"signal-surface event {name!r} leaked into the client registry (§11.8)"
        )
    # inject a leak and prove closure fails.
    leaked = next(iter(SIGNAL_SURFACE_EVENTS))

    class _Sentinel:
        pass

    CHANNEL_REGISTRY[leaked] = _Sentinel  # type: ignore[assignment]
    try:
        with pytest.raises((AssertionError, RuntimeError)):
            assert_registry_closed()
    finally:
        del CHANNEL_REGISTRY[leaked]
    assert assert_registry_closed() in (None, True)


# ── field discipline on the new models (matches sealed AC-REG-004) ────────────
def test_new_models_field_discipline():
    """Every id is UUID, every free-text carries Field(max_length=...), every selector is Literal."""
    from typing import Literal, Union, get_args, get_origin
    from uuid import UUID

    from libs.contracts import CHANNEL_REGISTRY

    def _unwrap(ann):
        if get_origin(ann) is Union:
            arms = [a for a in get_args(ann) if a is not type(None)]
            return arms[0] if len(arms) == 1 else ann
        return ann

    non_uuid_id: list[str] = []
    unbounded: list[str] = []
    open_selector: list[str] = []

    for model in CHANNEL_REGISTRY.values():
        mname = getattr(model, "__name__", str(model))
        for fname, finfo in model.model_fields.items():
            ann = finfo.annotation
            base = _unwrap(ann)
            fl = fname.lower()
            if fl == "id" or fl.endswith("_id") or fl.endswith("_ref"):
                if not (base is UUID or getattr(base, "__name__", "") == "UUID"):
                    non_uuid_id.append(f"{mname}.{fname}={base!r}")
                continue
            if fl == "type" or fl.endswith("_type") or fl.endswith("_status") or fl in {
                "status", "op", "kind", "role", "mode",
            }:
                if get_origin(_unwrap(ann)) is not Literal:
                    open_selector.append(f"{mname}.{fname}={base!r}")
                continue
            if base is str:
                mp = getattr(finfo, "metadata", []) or []
                has_max = any(getattr(m, "max_length", None) is not None for m in mp)
                if not has_max and getattr(finfo, "max_length", None) is None:
                    unbounded.append(f"{mname}.{fname}")

    assert not non_uuid_id, f"id fields must be UUID: {non_uuid_id}"
    assert not unbounded, f"free-text must carry Field(max_length=...): {unbounded}"
    assert not open_selector, f"selectors must be Literal[...]: {open_selector}"
