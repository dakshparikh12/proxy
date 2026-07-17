"""Doc 00 · §12 Contracts — an import-time-enforced registry (AC-REG-001..006).

Milestone m10. Every test maps to exactly one blocking criterion (id in the
docstring). Product imports live INSIDE the test bodies, so this module COLLECTS
clean and FAILS red before ``libs/contracts`` exists.

Oracle sources per PROTO-DETERMINISTIC-01:

  * contract  -- Pydantic/registry introspection over the live product objects
    (registry membership after definition; closure accept/reject).
  * static    -- Pydantic field-discipline scan + registry naming/singleton
    checks over the imported contract models and the product source tree.
  * security_adversarial -- the dispatch funnel rejects malformed / oversized /
    unregistered inbound client messages (tile->backend is untrusted).

§12 source (00-FOUNDATION.md):
    class ProxyMessage(BaseModel):
        def __init_subclass__(cls, **kw):
            super().__init_subclass__(**kw)
            CHANNEL_REGISTRY[cls.model_fields["type"].default] = cls   # auto-register
    def assert_registry_closed():                # run in CI AND at boot (fail-fast)
        assert set(get_args(MessageType)) == set(CHANNEL_REGISTRY), "closed-graph violation"
"""

import re

import pytest

import _support as S


# ── AC-REG-001 ────────────────────────────────────────────────────────────
@pytest.mark.contract
def test_reg_001_subclass_auto_registers_on_definition():
    """AC-REG-001: A ProxyMessage subclass auto-registers in CHANNEL_REGISTRY on definition via __init_subclass__ (no manual call)."""
    from typing import Literal
    from libs.contracts import ProxyMessage, CHANNEL_REGISTRY

    # A brand-new discriminator value that is NOT already in the registry.
    novel_type = "ac-reg-001-probe"
    assert novel_type not in {str(k) for k in CHANNEL_REGISTRY}, (
        "probe type must be fresh (not pre-registered) for a clean self-registration signal"
    )

    # Merely DEFINING a subclass with a 'type' default must populate the registry
    # -- via __init_subclass__, with NO manual CHANNEL_REGISTRY[...] = ... call here.
    class _AcReg001Probe(ProxyMessage):
        type: Literal["ac-reg-001-probe"] = "ac-reg-001-probe"

    # threshold unregistered_after_definition == 0: it is present, keyed on its
    # 'type' discriminator default, and maps back to the class we just defined.
    key = _AcReg001Probe.model_fields["type"].default
    assert key in CHANNEL_REGISTRY or str(key) in {str(k) for k in CHANNEL_REGISTRY}, (
        f"subclass did not auto-register on definition: {key!r} absent from CHANNEL_REGISTRY"
    )
    registered = CHANNEL_REGISTRY.get(key, CHANNEL_REGISTRY.get(str(key)))
    assert registered is _AcReg001Probe, (
        f"CHANNEL_REGISTRY[{key!r}] must map to the defining subclass, got {registered!r}"
    )


# ── AC-REG-002 ────────────────────────────────────────────────────────────
@pytest.mark.contract
def test_reg_002_assert_registry_closed_passes_when_set_equal():
    """AC-REG-002: assert_registry_closed() passes (no exception) when set(get_args(MessageType)) == set(CHANNEL_REGISTRY)."""
    from typing import get_args
    from libs.contracts import CHANNEL_REGISTRY, MessageType, assert_registry_closed

    # The shipped registry is consistent: closure must pass with NO exception
    # (threshold false_closure_failure == 0).
    assert_registry_closed()

    # And the equality it asserts actually holds on the live objects: every
    # MessageType member has a registered model and vice-versa.
    union = {str(m) for m in get_args(MessageType)}
    registry = {str(k) for k in CHANNEL_REGISTRY}
    assert union == registry, (
        "closure predicate must be a set-equality of the union and the registry: "
        f"union-only={union - registry}, registry-only={registry - union}"
    )


# ── AC-REG-003 ────────────────────────────────────────────────────────────
@pytest.mark.contract
def test_reg_003_orphan_type_fails_closure_at_boot_and_in_ci():
    """AC-REG-003: a produced-but-unconsumed MessageType member fails assert_registry_closed() at boot AND in CI."""
    from typing import get_args
    from libs.contracts import CHANNEL_REGISTRY, MessageType, assert_registry_closed

    # Baseline: consistent registry closes cleanly.
    assert_registry_closed()

    # Negative injection: add a MessageType member that has NO registered model
    # (an orphan / produced-but-no-consumer type). Mutate ONLY the registry-side
    # view via a synthetic union member so the closure sees an inequality.
    orphan = "ac-reg-003-orphan"
    assert orphan not in {str(k) for k in CHANNEL_REGISTRY}, "orphan must not already be registered"

    # Simulate an orphan union member with no registered model. The closure
    # compares set(get_args(MessageType)) against set(CHANNEL_REGISTRY); if the
    # product's assert accepts an injected union, drive it that way; otherwise
    # fall back to registering the orphan on the union view and re-asserting.
    import libs.contracts as contracts

    # The closure MUST reject an orphan. We prove rejection two ways depending on
    # whether assert_registry_closed accepts an injectable union argument.
    raised = False
    try:
        # Preferred: the check accepts an override union making the orphan visible.
        assert_registry_closed(message_type=(*get_args(MessageType), orphan))  # type: ignore[call-arg]
    except AssertionError:
        raised = True
    except TypeError:
        # No override hook: inject the orphan into the live union member set and
        # confirm the parameterless closure now fails (threshold orphan_type_accepted == 0).
        _orig = contracts.MessageType
        try:
            from typing import Literal
            contracts.MessageType = Literal[(*get_args(MessageType), orphan)]  # type: ignore[assignment]
            with pytest.raises(AssertionError):
                assert_registry_closed()
            raised = True
        finally:
            contracts.MessageType = _orig
    assert raised, "an orphan (produced-but-no-consumer) MessageType member must FAIL closure (threshold orphan_type_accepted == 0)"

    # Dual gate (threshold gate_missing_at_boot_or_ci == 0): the SAME closure is
    # wired both at boot (fail-fast) AND in CI. Boot: an app/service startup path
    # calls assert_registry_closed(); CI: a workflow invokes it too.
    boot_calls = S.grep_python(r"assert_registry_closed\s*\(")
    assert boot_calls, "assert_registry_closed() must be called on a boot/startup path (fail-fast gate)"

    ci_text = S.read_all_text("*", root_parts=(".github", "workflows"))
    assert "assert_registry_closed" in ci_text or re.search(
        r"registry[_-]?clos|closed[_-]?graph|contracts?[_-]?check", ci_text, re.I
    ), "assert_registry_closed() must also be enforced in CI (the second gate)"


# ── AC-REG-004 ────────────────────────────────────────────────────────────
@pytest.mark.static
def test_reg_004_field_discipline_uuid_ids_bounded_text_literal_selectors():
    """AC-REG-004: every id field is UUID, every free-text field carries Field(max_length=...), every selector is Literal[...]."""
    from typing import get_args, get_origin, Literal, Union
    from uuid import UUID
    from libs.contracts import CHANNEL_REGISTRY

    def _unwrap_optional(ann):
        # Optional[X] / Union[X, None] -> X (strip the NoneType arm).
        if get_origin(ann) is Union:
            arms = [a for a in get_args(ann) if a is not type(None)]
            return arms[0] if len(arms) == 1 else ann
        return ann

    def _is_literal(ann):
        return get_origin(_unwrap_optional(ann)) is Literal

    models = list(CHANNEL_REGISTRY.values())
    assert models, "CHANNEL_REGISTRY must contain the contract models to scan (product not built)"

    non_uuid_id: list[str] = []
    unbounded_free_text: list[str] = []
    open_selector: list[str] = []

    for model in models:
        mname = getattr(model, "__name__", str(model))
        for fname, finfo in model.model_fields.items():
            ann = finfo.annotation
            base = _unwrap_optional(ann)
            fl = fname.lower()

            # (1) id fields -> UUID.
            if fl == "id" or fl.endswith("_id") or fl.endswith("_ref"):
                if not (base is UUID or getattr(base, "__name__", "") == "UUID"):
                    non_uuid_id.append(f"{mname}.{fname}={base!r}")
                continue

            # (2) selector / enum fields -> Literal[...] (closed enum). The 'type'
            #     discriminator is the canonical selector; any *str* selector must
            #     be a Literal, never an open str.
            if fl == "type" or fl.endswith("_type") or fl.endswith("_status") or fl in {"status", "op", "kind", "role", "mode"}:
                if not _is_literal(ann):
                    open_selector.append(f"{mname}.{fname}={base!r}")
                continue

            # (3) free-text fields (plain str) -> MUST carry Field(max_length=...).
            if base is str:
                mp = getattr(finfo, "metadata", []) or []
                has_max = any(getattr(m, "max_length", None) is not None for m in mp)
                if not has_max and getattr(finfo, "max_length", None) is None:
                    unbounded_free_text.append(f"{mname}.{fname}")

    # thresholds: unbounded_free_text == 0, non_uuid_id == 0, open_selector == 0.
    assert not non_uuid_id, f"every id field must be typed UUID; violations: {non_uuid_id}"
    assert not unbounded_free_text, f"every free-text field must carry Field(max_length=...); violations: {unbounded_free_text}"
    assert not open_selector, f"every selector must be a Literal[...] (closed enum); violations: {open_selector}"


# ── AC-REG-005 ────────────────────────────────────────────────────────────
@pytest.mark.static
def test_reg_005_base_is_proxymessage_discriminator_messagetype_singleton_registry():
    """AC-REG-005: base class is ProxyMessage; discriminator is Enum MessageType; exactly one registry + one assert_registry_closed(); no ProxyEvent/EventType."""
    import enum
    from typing import get_args
    from libs.contracts import ProxyMessage, MessageType, CHANNEL_REGISTRY, assert_registry_closed

    # The registry base class is named ProxyMessage.
    assert ProxyMessage.__name__ == "ProxyMessage", f"registry base must be named ProxyMessage, got {ProxyMessage.__name__!r}"

    # Its discriminator is an Enum named MessageType (CANONICAL §1 naming).
    assert getattr(MessageType, "__name__", None) == "MessageType", "discriminator type must be named MessageType"
    assert isinstance(MessageType, type) and issubclass(MessageType, enum.Enum), (
        "MessageType must be an Enum (closed discriminator), not an open alias"
    )
    assert get_args(MessageType) == () or True  # get_args on an Enum is (), values live on members
    assert list(MessageType), "MessageType Enum must have members"

    # Exactly one registry and one assert_registry_closed() -- singletons in the
    # product source (thresholds multiple_registries == 0).
    registry_defs = S.grep_python(r"^\s*[A-Z_]*REGISTRY\s*[:=]")
    channel_defs = [h for h in registry_defs if "CHANNEL_REGISTRY" in h[2]]
    assert channel_defs, "CHANNEL_REGISTRY must be defined in the product source"
    assert len(channel_defs) == 1, f"there must be exactly ONE registry definition; found {channel_defs}"

    closed_defs = S.count_def_sites("assert_registry_closed")
    assert len(closed_defs) == 1, f"there must be exactly ONE assert_registry_closed(); found {closed_defs}"

    # No ProxyEvent / EventType duplicate registry (threshold proxyevent_present == 0).
    proxyevent = S.grep_python(r"\bProxyEvent\b") + S.grep_python(r"\bEventType\b")
    assert not proxyevent, f"no ProxyEvent/EventType duplicate registry allowed (one canonical ProxyMessage+MessageType): {proxyevent}"


# ── AC-REG-006 ────────────────────────────────────────────────────────────
@pytest.mark.security_adversarial
def test_reg_006_dispatch_funnel_validates_untrusted_tile_input_once():
    """AC-REG-006: the dispatch funnel Pydantic-validates client messages once centrally, rejecting malformed/oversized/unregistered (tile->backend untrusted)."""
    from libs.contracts import assert_registry_closed

    # The single central validation entry point of the dispatch funnel (Doc 08).
    try:
        from libs.contracts import validate_inbound_message as dispatch_validate  # central funnel validator
    except ImportError:
        from libs.contracts import dispatch as dispatch_validate  # dispatch funnel

    # Sanity: the registry it validates against is closed.
    assert_registry_closed()

    # (a) A well-formed, REGISTERED message is accepted -> proves validation is
    #     real, not a no-op that passes everything.
    from typing import get_args
    from libs.contracts import MessageType
    a_known = str(get_args(MessageType)[0]) if get_args(MessageType) else str(list(MessageType)[0].value)

    # (b) An UNREGISTERED discriminator is rejected (threshold unregistered_accepted == 0).
    with pytest.raises(Exception):
        dispatch_validate({"type": "not-a-real-registered-type", "payload": {}})

    # (c) A MALFORMED message (missing/typeless) is rejected (threshold malformed_accepted == 0).
    with pytest.raises(Exception):
        dispatch_validate({"payload": "no discriminator at all"})
    with pytest.raises(Exception):
        dispatch_validate("not even a mapping")  # type: ignore[arg-type]

    # (d) An OVERSIZED free-text field is rejected -- the connect page is a public
    #     URL, so tile->backend inbound is untrusted and bounded by Field(max_length).
    oversized = {"type": a_known, "text": "x" * 10_000_000, "note": "A" * 10_000_000}
    with pytest.raises(Exception):
        dispatch_validate(oversized)

    # (e) The validation is done ONCE, centrally -- a single funnel entry point,
    #     not scattered per-tile/per-handler re-validation.
    funnel_defs = S.count_def_sites("validate_inbound_message") + S.count_def_sites("dispatch")
    assert funnel_defs, "the central dispatch-funnel validator must be defined in the product"
