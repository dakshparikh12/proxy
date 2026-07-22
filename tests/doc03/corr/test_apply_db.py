"""The db:postgres CORR criteria — REAL apply_correction on REAL Postgres.

Every test drives the production ``scribe.corrections.apply_correction`` against
the committed ``db.repos.notes`` append-only ledger on a real Postgres carrying
the sealed §3.3 note_deltas schema, then folds the ledger with the production
``scribe.notes_reader.Notes.fold_all`` to observe what the NEXT Scribe call sees.
No db seam is stubbed (mock_boundary). The write-failure negatives fail the write
for real via an unroutable asyncpg connection (``FailingAcquirer``), never a mock.

Payload note (§3.3.1): ``note_deltas.payload`` is jsonb; asyncpg returns each row's
payload as a JSON *string* — the raw-row helpers here ``json.loads`` it.

Criteria covered:
  AC-CORR-01 / -01-NEG   immediate + attributed + superseded ; missing attribution
  AC-CORR-02 / -02-NEG   prior values stay queryable ; supersede-not-erase (append-only)
  AC-CORR-03 / -03-NEG   next Scribe fold sees V1 ; failed commit -> fold still V0
  AC-CORR-04 / -04-NEG   firmness bump silent ; ordinary write-fail -> no false confirm
  AC-CORR-05 / -05-NEG   action tweak silent ; tweak+final -> high-stakes (spoken)
  AC-CORR-06 / -06-NEG   forming lean silent ; real db fault degrades honestly
  AC-CORR-07 / -07-NEG   open-question edit silent ; real db fault degrades honestly
  AC-CORR-08 / -08-NEG   sets final -> commit-then-speak-once ; write-fail -> no confirm
  AC-CORR-09 / -09-NEG   sets irreversible -> commit-then-speak-once ; write-fail -> no confirm
  AC-CORR-10 / -10-NEG   close already-final -> gate fires ; double-close still fires
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import asyncpg
import pytest

from scribe.corrections import (
    AttributionError,
    Correction,
    apply_correction,
    is_high_stakes,
)
from scribe.notes_reader import Notes
from scribe.schema import CloseOp, DecisionStatus, PatchOp, Reversibility
from db.repos import notes as notes_repo

from .conftest import FailingAcquirer, requires_pg

pytestmark = [pytest.mark.asyncio, requires_pg]

_T = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


# ── raw-ledger helpers (asyncpg jsonb payload is a JSON string -> json.loads) ──
async def _seed_add(pool: Any, mid: uuid.UUID, entry_id: str, entry: dict[str, Any]) -> None:
    async with pool.acquire() as conn:
        await notes_repo.append_delta(
            conn, meeting_id=mid, entry_id=entry_id, op="add",
            payload=entry, window_start_s=0.0,
        )


async def _raw_rows(pool: Any, mid: uuid.UUID) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows: list[dict[str, Any]] = await notes_repo.load_deltas(conn, mid)
    return rows


def _decode(payload: Any) -> dict[str, Any]:
    if isinstance(payload, str):
        decoded = json.loads(payload)
        assert isinstance(decoded, dict)
        return decoded
    return dict(payload)


async def _folded_entry(pool: Any, mid: uuid.UUID, entry_id: str) -> dict[str, Any]:
    """What the NEXT Scribe call sees for this entry: the production fold."""
    rows = await _raw_rows(pool, mid)
    notes = Notes.fold_all(rows)
    return dict(notes.entries.get(entry_id, {}))


def _mk(op: Any, corrector: str = "Sam", prior: Any = None) -> Correction:
    return Correction(
        target_id=op.target_id,
        op=op,
        corrector=corrector,
        corrected_at=_T,
        prior_entry=prior,
    )


# ── AC-CORR-01 : immediate, attributed, prior kept superseded ─────────────────
async def test_corr_01_applied_immediately_attributed_superseded(
    db: Any, pool: Any, meeting_id: uuid.UUID, speak: Any
) -> None:
    mid = meeting_id
    await _seed_add(pool, mid, "E1", {"kind": "decision", "text": "V0", "status": "forming"})

    corr = _mk(
        PatchOp(target_id="E1", changes={"text": "V1"}, supersede_reason="corrected to V1"),
        corrector="Sam",
    )
    result = await apply_correction(db, corr, meeting_id=mid, speak=speak, window_start_s=10.0)

    # applied immediately (within this call) -> a committed ledger row exists now
    assert result.committed is True
    assert result.delta_id is not None
    # current folded value is V1
    entry = await _folded_entry(pool, mid, "E1")
    assert entry["text"] == "V1"
    # prior value V0 is still on the append-only ledger (superseded-not-erased)
    rows = await _raw_rows(pool, mid)
    add_payloads = [_decode(r["payload"]) for r in rows if r["op"] == "add"]
    assert any(p.get("text") == "V0" for p in add_payloads)
    # the correction row carries the attribution: corrector Sam, timestamp T
    patch_payloads = [_decode(r["payload"]) for r in rows if r["op"] == "patch"]
    assert patch_payloads
    assert patch_payloads[-1]["corrector"] == "Sam"
    assert patch_payloads[-1]["corrected_at"] == _T.isoformat()


async def test_corr_01neg_missing_attribution_never_writes(
    db: Any, pool: Any, meeting_id: uuid.UUID, speak: Any
) -> None:
    mid = meeting_id
    await _seed_add(pool, mid, "E1", {"text": "V0"})
    before = len(await _raw_rows(pool, mid))

    # a correction with no corrector is rejected at construction -> never reaches apply
    with pytest.raises(AttributionError):
        _mk(PatchOp(target_id="E1", changes={"text": "V1"}, supersede_reason="x"), corrector="  ")
    # a correction with a non-datetime timestamp is likewise rejected
    bad_ts: Any = "2026-07-21"  # a str where a datetime is required
    with pytest.raises(AttributionError):
        Correction(
            target_id="E1",
            op=PatchOp(target_id="E1", changes={"text": "V1"}, supersede_reason="x"),
            corrector="Sam",
            corrected_at=bad_ts,
        )
    # no partial record was written for the rejected corrections
    after = len(await _raw_rows(pool, mid))
    assert after == before


# ── AC-CORR-02 : full supersede chain stays queryable (V0, V1 both) ───────────
async def test_corr_02_supersede_chain_intact(
    db: Any, pool: Any, meeting_id: uuid.UUID, speak: Any
) -> None:
    mid = meeting_id
    await _seed_add(pool, mid, "E1", {"kind": "decision", "text": "V0"})
    c1 = _mk(PatchOp(target_id="E1", changes={"text": "V1"}, supersede_reason="->V1"))
    await apply_correction(db, c1, meeting_id=mid, speak=speak, window_start_s=10.0)
    c2 = _mk(PatchOp(target_id="E1", changes={"text": "V2"}, supersede_reason="->V2"))
    await apply_correction(db, c2, meeting_id=mid, speak=speak, window_start_s=20.0)

    rows = await _raw_rows(pool, mid)
    # every intermediate value survives on the ledger, in write order
    texts = [_decode(r["payload"]).get("text") for r in rows]
    assert texts == ["V0", "V1", "V2"]
    # V2 is the current folded value
    entry = await _folded_entry(pool, mid, "E1")
    assert entry["text"] == "V2"


async def test_corr_02neg_no_in_place_overwrite_of_prior(
    db: Any, pool: Any, meeting_id: uuid.UUID, speak: Any
) -> None:
    """Supersede-not-erase: a correction is a NEW append row; the prior add row is
    never UPDATEd in place or deleted. After a correction, the prior value's row
    still carries V0 (detecting an in-place overwrite, which would show 0 priors).
    """
    mid = meeting_id
    await _seed_add(pool, mid, "E1", {"text": "V0"})
    before_add = [r for r in await _raw_rows(pool, mid) if r["op"] == "add"]
    assert len(before_add) == 1 and _decode(before_add[0]["payload"])["text"] == "V0"

    c = _mk(PatchOp(target_id="E1", changes={"text": "V1"}, supersede_reason="x"))
    await apply_correction(db, c, meeting_id=mid, speak=speak, window_start_s=10.0)

    after = await _raw_rows(pool, mid)
    after_add = [r for r in after if r["op"] == "add"]
    # the add row is untouched (same id, same V0) — no in-place overwrite
    assert len(after_add) == 1
    assert after_add[0]["id"] == before_add[0]["id"]
    assert _decode(after_add[0]["payload"])["text"] == "V0"
    # a distinct patch row carries the new value (INSERT supersede, not UPDATE)
    assert any(r["op"] == "patch" and _decode(r["payload"]).get("text") == "V1" for r in after)


# ── AC-CORR-03 : next Scribe call's notes prefix shows the corrected value ─────
async def test_corr_03_next_scribe_fold_sees_corrected_value(
    db: Any, pool: Any, meeting_id: uuid.UUID, speak: Any
) -> None:
    mid = meeting_id
    await _seed_add(pool, mid, "E1", {"kind": "decision", "text": "V0", "status": "forming"})
    c = _mk(PatchOp(target_id="E1", changes={"text": "V1"}, supersede_reason="->V1"))
    await apply_correction(db, c, meeting_id=mid, speak=speak, window_start_s=10.0)

    # the next Scribe call reads the fold of note_deltas (the real read path)
    entry = await _folded_entry(pool, mid, "E1")
    assert entry["text"] == "V1"  # corrected value in the notes prefix
    # V0 is NOT the current value in the prefix
    assert entry["text"] != "V0"

    # durability: folding again on the subsequent window still shows V1
    entry_again = await _folded_entry(pool, mid, "E1")
    assert entry_again["text"] == "V1"


async def test_corr_03neg_failed_commit_leaves_fold_at_v0(
    db: Any, pool: Any, meeting_id: uuid.UUID, speak: Any
) -> None:
    """A correction whose Postgres commit FAILS must not stick: the next fold
    still shows V0, V1 absent (F-CORR-UNCOMMITTED-STICKS)."""
    mid = meeting_id
    await _seed_add(pool, mid, "E1", {"text": "V0"})

    failing = FailingAcquirer()
    c = _mk(PatchOp(target_id="E1", changes={"text": "V1"}, supersede_reason="->V1"))
    with pytest.raises((asyncpg.PostgresError, OSError, ConnectionError)):
        await apply_correction(failing, c, meeting_id=mid, speak=speak, window_start_s=10.0)

    # the real DB never received the patch -> fold still V0, no false confirm
    entry = await _folded_entry(pool, mid, "E1")
    assert entry.get("text") == "V0"
    assert speak.calls == 0


# ── AC-CORR-04 : firmness bump applies silently, no gate speech ───────────────
async def test_corr_04_firmness_bump_silent_immediate(
    db: Any, pool: Any, meeting_id: uuid.UUID, speak: Any
) -> None:
    mid = meeting_id
    await _seed_add(pool, mid, "E1", {"kind": "claim", "text": "X", "firmness": "hedged"})
    c = _mk(PatchOp(target_id="E1", changes={"firmness": "firm"}, supersede_reason="firmer"))

    # gate does NOT classify this as high-stakes
    assert is_high_stakes(c) is False
    result = await apply_correction(db, c, meeting_id=mid, speak=speak, window_start_s=10.0)

    assert result.committed is True
    assert result.gate_fired is False
    assert result.spoken is None
    assert speak.calls == 0  # speech path never touched
    entry = await _folded_entry(pool, mid, "E1")
    assert entry["firmness"] == "firm"


async def test_corr_04neg_ordinary_write_fail_no_false_confirm(
    db: Any, pool: Any, meeting_id: uuid.UUID, speak: Any
) -> None:
    mid = meeting_id
    await _seed_add(pool, mid, "E1", {"firmness": "hedged"})
    failing = FailingAcquirer()
    c = _mk(PatchOp(target_id="E1", changes={"firmness": "firm"}, supersede_reason="x"))
    with pytest.raises((asyncpg.PostgresError, OSError, ConnectionError)):
        await apply_correction(failing, c, meeting_id=mid, speak=speak, window_start_s=10.0)
    # nothing to confirm, no success state propagated
    assert speak.calls == 0


# ── AC-CORR-05 : action-item tweak silent ; tweak+final NOT ordinary ──────────
async def test_corr_05_action_tweak_silent_immediate(
    db: Any, pool: Any, meeting_id: uuid.UUID, speak: Any
) -> None:
    mid = meeting_id
    await _seed_add(pool, mid, "A1", {"kind": "action", "text": "do X", "owner": "Sam"})
    c = _mk(PatchOp(target_id="A1", changes={"owner": "Ada"}, supersede_reason="reassign"))
    result = await apply_correction(db, c, meeting_id=mid, speak=speak, window_start_s=10.0)

    assert result.gate_fired is False
    assert speak.calls == 0
    entry = await _folded_entry(pool, mid, "A1")
    assert entry["owner"] == "Ada"


async def test_corr_05neg_tweak_plus_final_is_high_stakes_and_speaks(
    db: Any, pool: Any, meeting_id: uuid.UUID, speak: Any
) -> None:
    mid = meeting_id
    await _seed_add(pool, mid, "A1", {"kind": "decision", "text": "draft", "status": "forming"})
    c = _mk(
        PatchOp(
            target_id="A1",
            changes={"text": "Ship it", "status": DecisionStatus.final},
            supersede_reason="tweak+final",
        )
    )
    assert is_high_stakes(c) is True  # NOT ordinary
    result = await apply_correction(db, c, meeting_id=mid, speak=speak, window_start_s=10.0)
    assert result.gate_fired is True
    assert speak.calls == 1  # spoken-confirm gate fired


# ── AC-CORR-06 : forming-decision lean silent ; -NEG real db fault ────────────
async def test_corr_06_forming_lean_silent_status_stays_forming(
    db: Any, pool: Any, meeting_id: uuid.UUID, speak: Any
) -> None:
    mid = meeting_id
    await _seed_add(pool, mid, "D1", {"kind": "decision", "text": "?", "status": "forming"})
    c = _mk(
        PatchOp(target_id="D1", changes={"leans": {"Sam": "for"}}, supersede_reason="lean"),
        prior={"status": "forming"},
    )
    result = await apply_correction(db, c, meeting_id=mid, speak=speak, window_start_s=10.0)
    assert result.gate_fired is False
    assert speak.calls == 0
    entry = await _folded_entry(pool, mid, "D1")
    assert entry["status"] == "forming"  # still forming after the lean
    assert entry["leans"] == {"Sam": "for"}


async def test_corr_06neg_real_db_fault_degrades_honestly(
    db: Any, pool: Any, meeting_id: uuid.UUID, speak: Any
) -> None:
    """Inject a REAL db:postgres fault at the seam (unroutable connect); the apply
    surfaces the failure — no silent proceed, no corruption, no false confirm."""
    mid = meeting_id
    await _seed_add(pool, mid, "D1", {"status": "forming"})
    failing = FailingAcquirer()
    c = _mk(PatchOp(target_id="D1", changes={"leans": {"S": "for"}}, supersede_reason="x"))
    with pytest.raises((asyncpg.PostgresError, OSError, ConnectionError)):
        await apply_correction(failing, c, meeting_id=mid, speak=speak, window_start_s=10.0)
    # no corruption: the good DB still folds the untouched forming entry
    entry = await _folded_entry(pool, mid, "D1")
    assert entry["status"] == "forming"
    assert speak.calls == 0


# ── AC-CORR-07 : open-question edit silent ; -NEG real db fault ───────────────
async def test_corr_07_open_question_edit_silent_stays_open(
    db: Any, pool: Any, meeting_id: uuid.UUID, speak: Any
) -> None:
    mid = meeting_id
    await _seed_add(pool, mid, "Q1", {"kind": "open_question", "text": "why?", "resolved": False})
    c = _mk(PatchOp(target_id="Q1", changes={"text": "why exactly?"}, supersede_reason="clarify"))
    result = await apply_correction(db, c, meeting_id=mid, speak=speak, window_start_s=10.0)
    assert result.gate_fired is False
    assert speak.calls == 0
    entry = await _folded_entry(pool, mid, "Q1")
    assert entry["text"] == "why exactly?"
    assert entry.get("resolved") is False  # question stays open


async def test_corr_07neg_real_db_fault_degrades_honestly(
    db: Any, pool: Any, meeting_id: uuid.UUID, speak: Any
) -> None:
    mid = meeting_id
    await _seed_add(pool, mid, "Q1", {"resolved": False, "text": "why?"})
    failing = FailingAcquirer()
    c = _mk(PatchOp(target_id="Q1", changes={"text": "why!"}, supersede_reason="x"))
    with pytest.raises((asyncpg.PostgresError, OSError, ConnectionError)):
        await apply_correction(failing, c, meeting_id=mid, speak=speak, window_start_s=10.0)
    entry = await _folded_entry(pool, mid, "Q1")
    assert entry["text"] == "why?"  # untouched
    assert speak.calls == 0


# ── AC-CORR-08 : sets Decision.status=final -> commit-then-speak-once ──────────
async def test_corr_08_final_commits_then_speaks_once_patch_first(
    db: Any, pool: Any, meeting_id: uuid.UUID, speak: Any
) -> None:
    mid = meeting_id
    await _seed_add(pool, mid, "D1", {"kind": "decision", "text": "Ship Monday", "status": "forming"})
    c = _mk(
        PatchOp(
            target_id="D1",
            changes={"text": "Ship Friday", "status": DecisionStatus.final},
            supersede_reason="decided Friday not Monday",
        ),
        prior={"status": "forming"},
    )
    result = await apply_correction(db, c, meeting_id=mid, speak=speak, window_start_s=10.0)

    # patch committed to Postgres (status=final folds through)
    assert result.committed is True
    assert result.delta_id is not None
    entry = await _folded_entry(pool, mid, "D1")
    assert entry["status"] == "final"
    assert entry["text"] == "Ship Friday"
    # prior forming value kept as a superseded add row
    rows = await _raw_rows(pool, mid)
    assert any(_decode(r["payload"]).get("status") == "forming" for r in rows if r["op"] == "add")
    # exactly ONE spoken acknowledgement, AFTER the commit (patch-first): the row
    # id exists (committed) and the receipt line was recorded once.
    assert result.gate_fired is True
    assert speak.calls == 1
    assert result.spoken == speak.lines[0]
    assert speak.lines[0] and "\n" not in speak.lines[0]


async def test_corr_08neg_final_write_fail_no_false_confirm(
    db: Any, pool: Any, meeting_id: uuid.UUID, speak: Any
) -> None:
    mid = meeting_id
    await _seed_add(pool, mid, "D1", {"status": "forming"})
    failing = FailingAcquirer()
    c = _mk(
        PatchOp(target_id="D1", changes={"status": DecisionStatus.final}, supersede_reason="x"),
        prior={"status": "forming"},
    )
    with pytest.raises((asyncpg.PostgresError, OSError, ConnectionError)):
        await apply_correction(failing, c, meeting_id=mid, speak=speak, window_start_s=10.0)
    # patch did not commit -> NO spoken confirm, fold still forming
    assert speak.calls == 0
    entry = await _folded_entry(pool, mid, "D1")
    assert entry["status"] == "forming"


# ── AC-CORR-09 : sets Reversibility=irreversible -> commit-then-speak-once ─────
async def test_corr_09_irreversible_commits_then_speaks_once(
    db: Any, pool: Any, meeting_id: uuid.UUID, speak: Any
) -> None:
    mid = meeting_id
    await _seed_add(
        pool, mid, "D1",
        {"kind": "decision", "text": "delete prod", "reversibility": "easy-to-reverse"},
    )
    c = _mk(
        PatchOp(
            target_id="D1",
            changes={"reversibility": Reversibility.irreversible},
            supersede_reason="now irreversible",
        )
    )
    result = await apply_correction(db, c, meeting_id=mid, speak=speak, window_start_s=10.0)

    assert result.committed is True
    entry = await _folded_entry(pool, mid, "D1")
    assert entry["reversibility"] == "irreversible"
    # prior reversibility kept as a superseded add row
    rows = await _raw_rows(pool, mid)
    assert any(
        _decode(r["payload"]).get("reversibility") == "easy-to-reverse"
        for r in rows if r["op"] == "add"
    )
    assert result.gate_fired is True
    assert speak.calls == 1  # exactly one acknowledgement, after commit


async def test_corr_09neg_irreversible_write_fail_no_false_confirm(
    db: Any, pool: Any, meeting_id: uuid.UUID, speak: Any
) -> None:
    mid = meeting_id
    await _seed_add(pool, mid, "D1", {"reversibility": "easy-to-reverse"})
    failing = FailingAcquirer()
    c = _mk(
        PatchOp(target_id="D1", changes={"reversibility": Reversibility.irreversible}, supersede_reason="x")
    )
    with pytest.raises((asyncpg.PostgresError, OSError, ConnectionError)):
        await apply_correction(failing, c, meeting_id=mid, speak=speak, window_start_s=10.0)
    assert speak.calls == 0


# ── AC-CORR-10 : close/finalize an already-final entry -> gate fires ──────────
async def test_corr_10_close_already_final_entry_fires_gate(
    db: Any, pool: Any, meeting_id: uuid.UUID, speak: Any
) -> None:
    mid = meeting_id
    await _seed_add(pool, mid, "D1", {"kind": "decision", "text": "done", "status": "final"})
    c = _mk(
        CloseOp(target_id="D1", resolution="re-confirmed final"),
        prior={"status": "final"},
    )
    assert is_high_stakes(c) is True
    result = await apply_correction(db, c, meeting_id=mid, speak=speak, window_start_s=10.0)

    assert result.committed is True  # applied immediately
    assert result.gate_fired is True  # fires even though already high-stakes
    assert speak.calls == 1
    entry = await _folded_entry(pool, mid, "D1")
    assert entry.get("resolved") is True  # close folded through


async def test_corr_10neg_double_close_still_fires_gate(
    db: Any, pool: Any, meeting_id: uuid.UUID, speak: Any
) -> None:
    """Closing an already-closed-final entry AGAIN still fires the gate — not
    double-suppressed into the silent path (F-CORR-HIGH-STAKES-SILENT)."""
    mid = meeting_id
    await _seed_add(pool, mid, "D1", {"kind": "decision", "text": "done", "status": "final"})
    first = _mk(CloseOp(target_id="D1", resolution="closed once"), prior={"status": "final"})
    await apply_correction(db, first, meeting_id=mid, speak=speak, window_start_s=10.0)
    speak.calls = 0
    speak.lines.clear()

    # second close of the still-final entry
    second = _mk(CloseOp(target_id="D1", resolution="closed again"), prior={"status": "final"})
    assert is_high_stakes(second) is True
    result = await apply_correction(db, second, meeting_id=mid, speak=speak, window_start_s=20.0)
    assert result.gate_fired is True
    assert speak.calls == 1


# ── AC-CORR-11 (integration half): pipeline continues, no wait state ──────────
async def test_corr_11_speak_is_a_receipt_pipeline_continues(
    db: Any, pool: Any, meeting_id: uuid.UUID, speak: Any
) -> None:
    """After a high-stakes correction commits + emits its receipt, the apply call
    returns immediately (no wait/approval-pending) and a SUBSEQUENT delta is
    processed without any dependency on a human ack of the receipt."""
    mid = meeting_id
    await _seed_add(pool, mid, "D1", {"kind": "decision", "text": "x", "status": "forming"})
    hi = _mk(
        PatchOp(target_id="D1", changes={"status": DecisionStatus.final}, supersede_reason="final"),
        prior={"status": "forming"},
    )
    r1 = await apply_correction(db, hi, meeting_id=mid, speak=speak, window_start_s=10.0)
    assert r1.committed is True and speak.calls == 1

    # no human ack arrives; the pipeline processes the NEXT delta unblocked
    nxt = _mk(PatchOp(target_id="D1", changes={"text": "y"}, supersede_reason="tweak"), prior={"status": "final"})
    r2 = await apply_correction(db, nxt, meeting_id=mid, speak=speak, window_start_s=20.0)
    assert r2.committed is True  # subsequent delta processed
    entry = await _folded_entry(pool, mid, "D1")
    assert entry["text"] == "y"
