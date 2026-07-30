"""Doc 08 · §4.8 / CANONICAL §11.11 — the per-field produce/consume contract diff (un-trimmed).

Beyond the §4.1 set-equality (type-registered ↔ model-exists), this is the CHEAP
Pydantic **field-level** produce/consume diff that runs in CI now. It walks every
contract model's real fields (via the producer/consumer field registries over the
LIVE ``libs.contracts`` models — never a hand-maintained list, which would itself
drift) and fails the build, NAMING each orphan, when a field is produced by one
side and consumed by neither (or consumed under a name no model produces).

Every test runs the REAL path: it imports the live ``libs.contracts`` package
(firing import-time model registration + the live consumers' field registrations)
and drives the shipped gate against the real objects. No mocks, no hand-listed
field sets on the produced side.

The three drift cases this node exists FOR — and MUST catch by construction:

* ``AgentChunk.kind`` → ``.type``  — a consumer reading ``.kind`` while the model
  produces ``type`` is an orphan on BOTH sides (``kind`` consumed-but-not-produced;
  ``type`` produced-but-not-consumed). The diff names it.
* the envelope ``verified|draft`` → ``EnvelopeStatus``  — a consumer reading a bare
  ``verified``/``draft`` field the model never carries (the real field is
  ``verification`` + ``status``) is a consumed-but-not-produced orphan.
* ``dm?`` → ``dm_available``  — a consumer reading ``dm`` while ``ChannelReport``
  carries ``dm_available`` is an orphan (``dm`` consumed-but-not-produced).
"""
from __future__ import annotations

import pytest

# ── the REAL contract models the diff walks (imported, never re-specified) ────
from libs.contracts import (  # noqa: E402
    AgentChunk,
    ChannelReport,
    Envelope,
)
from contracts.registry import (  # noqa: E402
    MESSAGE_FIELD_CONSUMERS,
    MESSAGE_FIELD_PRODUCERS,
    assert_contract_fields_consumed,
    assert_fields_consumed,
    collect_produced_fields,
    register_field_consumer,
)


# ── the gate populates produced from the REAL models, not a hand list ─────────
def test_produced_fields_come_from_the_real_models():
    """``collect_produced_fields`` reads each model's ``model_fields`` — the live shape."""
    produced = collect_produced_fields()

    # AgentChunk's real fields (chunks.py): type / text / metadata.
    assert "AgentChunk" in produced, "AgentChunk must contribute its produced fields"
    assert {"type", "text", "metadata"} <= produced["AgentChunk"], (
        f"AgentChunk produced fields must come from the live model: {produced['AgentChunk']}"
    )
    # The legacy name is NOT produced — the model was migrated .kind → .type.
    assert "kind" not in produced["AgentChunk"], "the .kind→.type migration must be reflected"

    # ChannelReport's real field is dm_available (channels.py), NOT dm.
    assert produced.get("ChannelReport") == {"dm_available"}, (
        f"ChannelReport produces exactly dm_available: {produced.get('ChannelReport')}"
    )
    assert "dm" not in produced.get("ChannelReport", set())

    # Envelope carries status + verification (envelopes.py), not a bare verified/draft.
    assert {"status", "verification"} <= produced.get("Envelope", set())
    assert "verified" not in produced.get("Envelope", set())
    assert "draft" not in produced.get("Envelope", set())


def test_produced_reflects_model_field_names_exactly():
    """A produced field-name set equals the live ``model_fields`` keys (no drift-by-hand)."""
    produced = collect_produced_fields()
    assert produced["AgentChunk"] == set(AgentChunk.model_fields)
    assert produced["ChannelReport"] == set(ChannelReport.model_fields)
    assert produced["Envelope"] == set(Envelope.model_fields)


# ── the SHIPPED gate is green on the live product (every produced field consumed) ─
def test_shipped_field_diff_is_green_on_the_live_product():
    """The live producers/consumers agree at the field level — the gate returns [] and does not raise."""
    violations = assert_contract_fields_consumed()
    assert violations == [], (
        "the shipped contract field-diff must be GREEN on the live product; "
        f"orphans: {violations}"
    )


def test_live_consumers_actually_registered_the_fields_they_read():
    """The consumer registry is populated from the LIVE services, not empty (else the diff is vacuous)."""
    consumed = MESSAGE_FIELD_CONSUMERS
    assert consumed.get("AgentChunk"), "the AgentChunk consumer must register the fields it reads"
    assert {"type", "text", "metadata"} <= consumed["AgentChunk"], (
        f"the live AgentChunk consumer reads type/text/metadata: {consumed.get('AgentChunk')}"
    )
    assert "dm_available" in consumed.get("ChannelReport", set()), (
        "the live channel-report consumer reads dm_available"
    )


def test_registered_fields_are_grounded_in_the_real_consumer_source():
    """The registered consumer fields are the ones the REAL service code reads (not an untethered list).

    Grounds the field registry against the live source so the diff can never go vacuous
    or drift from what the services actually consume (Law 1 — grounded, cite file:line).
    """
    import inspect

    import control_plane.provider as provider  # provider reads chunk.type/.text/.metadata
    import transport.chat as chat  # chat gates on report.dm_available

    provider_src = inspect.getsource(provider)
    # the live provider discriminates on .type, reads .text, inspects .metadata.
    assert "chunk.type" in provider_src or ".type" in provider_src
    assert "block.text" in provider_src or ".text" in provider_src
    assert ".metadata" in provider_src

    chat_src = inspect.getsource(chat)
    assert "dm_available" in chat_src, "the live chat consumer must read report.dm_available"


# ── the diff is FIELD-LEVEL, not type set-equality ─────────────────────────────
def test_diff_is_field_level_not_set_equality():
    """A model present on both sides but with a produced field no consumer reads still fails."""
    produced = {"Sig": {"a", "b", "c"}}
    consumed = {"Sig": {"a", "b"}}  # 'c' produced, never consumed — set-equality of TYPES passes; field-diff must not.
    violations = assert_fields_consumed(produced=produced, consumed=consumed)
    assert violations, "a produced-but-unconsumed FIELD must fail even when the type is on both sides"
    assert any("Sig.c" in v for v in violations), f"the orphan field must be NAMED: {violations}"


# ── drift case 1: AgentChunk .kind → .type (caught by construction) ────────────
def test_catches_agentchunk_kind_to_type_drift():
    """A consumer stuck on the OLD ``.kind`` name (model produces ``.type``) is named an orphan."""
    produced = collect_produced_fields()  # AgentChunk.type is real
    # a stale consumer reads .kind (the pre-migration name) instead of .type
    stale_consumed = {"AgentChunk": {"kind", "text", "metadata"}}
    violations = assert_contract_fields_consumed(consumed=stale_consumed, produced=produced)
    # .type is produced but this stale consumer never reads it → orphan, named.
    assert any("AgentChunk.type" in v for v in violations), (
        f"the .kind→.type drift must name AgentChunk.type as produced-but-unconsumed: {violations}"
    )
    # and .kind is consumed but no model produces it → the reverse orphan, also named.
    assert any("AgentChunk.kind" in v for v in violations), (
        f"the .kind→.type drift must also name AgentChunk.kind as consumed-but-unproduced: {violations}"
    )


# ── drift case 2: envelope verified|draft → EnvelopeStatus (caught by construction) ─
def test_catches_envelope_verified_draft_drift():
    """A consumer reading a bare ``verified``/``draft`` field the Envelope never carries is named."""
    produced = collect_produced_fields()
    # the real Envelope carries status + verification, NOT a bare verified/draft field.
    stale_consumed = {
        "Envelope": {"headline", "detail", "artifact", "receipts", "verified", "draft", "task_id"},
    }
    violations = assert_contract_fields_consumed(consumed=stale_consumed, produced=produced)
    assert any("Envelope.verified" in v for v in violations), (
        f"the verified|draft→EnvelopeStatus drift must name Envelope.verified: {violations}"
    )
    assert any("Envelope.draft" in v for v in violations), (
        f"the verified|draft→EnvelopeStatus drift must name Envelope.draft: {violations}"
    )
    # and the real produced status/verification are then orphaned (nobody consumes them).
    assert any("Envelope.status" in v for v in violations)


# ── drift case 3: dm? → dm_available (caught by construction) ──────────────────
def test_catches_channel_report_dm_to_dm_available_drift():
    """A consumer reading ``dm`` while ChannelReport carries ``dm_available`` is named an orphan."""
    produced = collect_produced_fields()
    stale_consumed = {"ChannelReport": {"dm"}}  # the legacy name
    violations = assert_contract_fields_consumed(consumed=stale_consumed, produced=produced)
    assert any("ChannelReport.dm" in v for v in violations), (
        f"the dm?→dm_available drift must name ChannelReport.dm as consumed-but-unproduced: {violations}"
    )
    assert any("ChannelReport.dm_available" in v for v in violations), (
        f"the dm?→dm_available drift must name ChannelReport.dm_available as produced-but-unconsumed: {violations}"
    )


# ── the gate is a build-FAILING check, and it NAMES the field ──────────────────
def test_gate_fails_the_build_and_names_the_field():
    """When run in strict mode the gate RAISES, and the message names every orphan field."""
    produced = {"AgentChunk": {"type", "text", "metadata"}}
    consumed = {"AgentChunk": {"type", "text"}}  # metadata orphaned
    with pytest.raises(AssertionError) as exc:
        assert_contract_fields_consumed(consumed=consumed, produced=produced, strict=True)
    assert "AgentChunk.metadata" in str(exc.value), (
        f"the build-failing message must NAME the orphan field: {exc.value}"
    )


def test_gate_returns_empty_when_fields_align():
    """No orphan → empty violation list and no raise (the green path)."""
    produced = {"AgentChunk": {"type", "text"}}
    consumed = {"AgentChunk": {"type", "text"}}
    assert assert_contract_fields_consumed(consumed=consumed, produced=produced) == []
    # strict mode does not raise on a clean diff.
    assert assert_contract_fields_consumed(consumed=consumed, produced=produced, strict=True) == []


# ── the field-producer registry mirrors the collected produced set (single source) ─
def test_field_producer_registry_matches_collected():
    """MESSAGE_FIELD_PRODUCERS (populated at import) equals what collect_produced_fields walks."""
    collected = collect_produced_fields()
    for model_name, fields in collected.items():
        assert MESSAGE_FIELD_PRODUCERS.get(model_name) == fields, (
            f"the producer registry for {model_name} must match the live model fields: "
            f"registry={MESSAGE_FIELD_PRODUCERS.get(model_name)} model={fields}"
        )


def test_register_field_consumer_is_idempotent_and_additive():
    """Registering the same field twice is a no-op; a new field is additive."""
    before = set(MESSAGE_FIELD_CONSUMERS.get("AgentChunk", set()))
    register_field_consumer("AgentChunk", "type")  # already there
    register_field_consumer("AgentChunk", "type")  # idempotent
    assert set(MESSAGE_FIELD_CONSUMERS["AgentChunk"]) == before, "re-registering must be idempotent"
