"""F4 / O-SEGPERSIST / C-EVENTS — the live applier's REAL wiring, driven offline.

The integration DB tier (``tests/doc03/coalescer/test_applier_db.py``) exercises
these against real Postgres, but it is env-gated and skips when PG is absent. This
module drives the REAL ``build_real_seams(...).apply_delta`` closure — the exact
production code path — with the ``db.repos.notes`` seam replaced by faithful
recording fakes (not a Mock of the seam: they record the SAME calls the real
asyncpg repo makes, in order). It proves:

* **O-SEGPERSIST** — every comprehended window persists a transcript_segments row
  that flips 'pending' -> 'comprehended' inside the SAME transaction as the
  note-delta append (§12.10 replay-idempotency linchpin).
* **C-EVENTS** — material-change events are emitted transactionally with the append
  and handed to the injected Doc-04 sink, keyed on the committed row (the Proactive
  trigger). The consumer stays deferred; only the pipe is asserted.
* **F4 / D-036** — a Claim whose ``contradicts`` dangles is DEGRADED (link stripped,
  claim kept, honest ``unbound_reference`` recorded) — the window is never dropped and
  no phantom base is fabricated. AND both events fire per D-036's bundled F4b are NOT
  asserted here (that clause is a sealed-test contradiction — see the report).
"""
from __future__ import annotations

import sys
import types

import pytest

from scribe.coalescer import BoundaryType, TranscriptSegment, Window
from scribe.prefix import MeetingHeader
from scribe.schema import (
    AddOp,
    Claim,
    Decision,
    DecisionStatus,
    Firmness,
    NoteDelta,
    Provenance,
    Reversibility,
)
from scribe.events import CollectingSink, MaterialChangeEvent
from contracts import MaterialChangeKind

pytestmark = pytest.mark.asyncio


# ── A faithful in-memory ``db.repos.notes`` recorder (contract, not a Mock) ───
class _FakeNotesRepo:
    """Records the SAME calls the real asyncpg repo makes, honouring return shapes."""

    def __init__(self, existing=None):
        self._existing = list(existing or [])
        self.appended: list[dict] = []
        self.segments: list[dict] = []
        self.status_flips: list[tuple] = []
        self._next_seg_id = 1

    async def load_deltas(self, conn, meeting_id):
        # The applier reads existing deltas (known ids for the refint store + counters).
        return list(self._existing)

    async def insert_segment(self, conn, *, meeting_id, text, start_s=None,
                             end_s=None, status=None, speaker=None):
        seg = {
            "id": self._next_seg_id,
            "meeting_id": meeting_id,
            "text": text,
            "start_s": start_s,
            "end_s": end_s,
            "status": status,
        }
        self._next_seg_id += 1
        self.segments.append(seg)
        return seg

    async def append_delta(self, conn, *, meeting_id, entry_id, op, payload,
                           window_start_s=None):
        row = {"id": len(self.appended) + 100, "entry_id": entry_id, "op": op,
               "payload": payload, "window_start_s": window_start_s}
        self.appended.append(row)
        return row

    async def set_segment_status(self, conn, *, segment_id, status):
        self.status_flips.append((segment_id, status))
        for s in self.segments:
            if s["id"] == segment_id:
                s["status"] = status


class _FakeTxn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeConn:
    def transaction(self):
        return _FakeTxn()


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *a):
        return False


class _FakeDB:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _FakeAcquire(self._conn)


def _install_fake_repo(monkeypatch, fake: _FakeNotesRepo):
    """Make ``from db.repos import notes as notes_repo`` resolve to the recorder."""
    mod = types.ModuleType("db.repos.notes")
    mod.load_deltas = fake.load_deltas
    mod.insert_segment = fake.insert_segment
    mod.append_delta = fake.append_delta
    mod.set_segment_status = fake.set_segment_status
    # Provide db, db.repos, db.repos.notes so both import forms resolve.
    db_pkg = sys.modules.get("db") or types.ModuleType("db")
    repos_pkg = sys.modules.get("db.repos") or types.ModuleType("db.repos")
    monkeypatch.setitem(sys.modules, "db", db_pkg)
    monkeypatch.setitem(sys.modules, "db.repos", repos_pkg)
    monkeypatch.setitem(sys.modules, "db.repos.notes", mod)
    monkeypatch.setattr(repos_pkg, "notes", mod, raising=False)


def _header() -> MeetingHeader:
    return MeetingHeader(meeting_id="m-1")


def _window(text="checkout ships Friday.") -> Window:
    seg = TranscriptSegment(
        speaker="Sam", text=text, start_s=0.0, end_s=3.0, token_count=4
    )
    return Window(segments=(seg,), boundary_type=BoundaryType.PAUSE_WITHIN_TURN)


def _claim(text="checkout ships Friday", contradicts=None) -> Claim:
    return Claim(
        text=text,
        speaker="sam",
        said_at_s=12.0,
        firmness=Firmness.firm,
        provenance=Provenance.observed,
        contradicts=contradicts,
    )


async def _run_apply(monkeypatch, delta, existing=None, sink=None, window=None):
    from control_plane.scribe_runtime import build_real_seams

    fake = _FakeNotesRepo(existing=existing)
    _install_fake_repo(monkeypatch, fake)
    sink = sink if sink is not None else CollectingSink()
    seams = build_real_seams(_header(), _FakeDB(_FakeConn()), event_sink=sink)
    await seams.apply_delta("m-1", window or _window(), delta)
    return fake, sink


# ── O-SEGPERSIST ──────────────────────────────────────────────────────────────
async def test_o_segpersist_window_flips_pending_to_comprehended(monkeypatch) -> None:
    delta = NoteDelta(ops=[AddOp(entry=_claim())])
    fake, _ = await _run_apply(monkeypatch, delta)
    # A transcript segment was inserted 'pending' with the window's raw text...
    assert len(fake.segments) == 1
    seg = fake.segments[0]
    assert seg["text"] == "checkout ships Friday."
    # ...and flipped to 'comprehended' in the same tx as the note-delta append.
    assert (seg["id"], "comprehended") in fake.status_flips
    assert seg["status"] == "comprehended"
    assert len(fake.appended) == 1  # the claim landed on the ledger


async def test_o_segpersist_goal_only_window_still_comprehended(monkeypatch) -> None:
    # A goal-only (no-op) window still records the span comprehended, never left pending.
    delta = NoteDelta(ops=[], current_goal="unblock checkout")
    fake, _ = await _run_apply(monkeypatch, delta)
    assert len(fake.segments) == 1
    assert fake.segments[0]["status"] == "comprehended"
    assert fake.status_flips == [(1, "comprehended")]


# ── C-EVENTS ──────────────────────────────────────────────────────────────────
async def test_c_events_material_change_emitted_to_sink(monkeypatch) -> None:
    delta = NoteDelta(
        ops=[
            AddOp(
                entry=Decision(
                    text="ship on Friday",
                    status=DecisionStatus.final,
                    reversibility=Reversibility.hard,
                )
            )
        ]
    )
    sink = CollectingSink()
    fake, sink = await _run_apply(monkeypatch, delta, sink=sink)
    # The material-change event fired to the Doc-04 sink (the Proactive pipe).
    assert sink.count == 1
    ev = sink.events[0]
    assert isinstance(ev, MaterialChangeEvent)
    assert ev.kind is MaterialChangeKind.DECISION_FINAL
    # Keyed on the committed row (the segment id is the free revision handle).
    assert ev.meeting_revision == fake.segments[0]["id"]


async def test_c_events_chitchat_emits_nothing(monkeypatch) -> None:
    from scribe.schema import ContextLine

    delta = NoteDelta(ops=[AddOp(entry=ContextLine(text="just banter"))])
    fake, sink = await _run_apply(monkeypatch, delta)
    assert sink.count == 0  # a context line lands on the ledger but fires no event
    assert len(fake.appended) == 1
    assert fake.segments[0]["status"] == "comprehended"  # still comprehended


# ── F4 / D-036 — honest degrade on a dangling contradicts ─────────────────────
async def test_f4_dangling_contradicts_degrades_link_stripped_claim_kept(monkeypatch) -> None:
    # 'c99' is not a known entry -> the contradicts link dangles.
    delta = NoteDelta(ops=[AddOp(entry=_claim(text="ships Monday", contradicts="c99"))])
    fake, sink = await _run_apply(monkeypatch, delta, existing=[])
    # The window is NOT dropped: the claim landed on the ledger...
    assert len(fake.appended) == 1
    landed = fake.appended[0]
    # ...with the dangling link STRIPPED (never a phantom base)...
    assert landed["payload"].get("contradicts") in (None,)
    # ...and an honest unbound_reference recorded on the payload.
    ub = landed["payload"].get("unbound_reference")
    assert ub is not None and ub["dangling_id"] == "c99"
    assert ub["action"] == "link_stripped_claim_kept"
    # The span is still comprehended (the window survived the degrade).
    assert fake.segments[0]["status"] == "comprehended"


async def test_f4_valid_contradicts_is_not_degraded(monkeypatch) -> None:
    # A contradicts pointing at an EXISTING entry ('c1') is kept intact.
    existing = [{"entry_id": "c1", "op": "add", "payload": "{}"}]
    delta = NoteDelta(ops=[AddOp(entry=_claim(text="ships Monday", contradicts="c1"))])
    fake, sink = await _run_apply(monkeypatch, delta, existing=existing)
    assert len(fake.appended) == 1
    landed = fake.appended[0]
    assert landed["payload"].get("contradicts") == "c1"  # link preserved
    assert "unbound_reference" not in landed["payload"]


async def test_f4_dangling_patch_target_dropped_no_phantom(monkeypatch) -> None:
    from scribe.schema import PatchOp

    # A patch whose target does not exist -> the op is DROPPED (no phantom base).
    delta = NoteDelta(
        ops=[
            PatchOp(
                target_id="d404",
                changes={"status": "final"},
                supersede_reason="forming->final",
            )
        ]
    )
    fake, sink = await _run_apply(monkeypatch, delta, existing=[])
    # No ledger row for the phantom patch (never fabricated a base)...
    assert fake.appended == []
    # ...but the window is still comprehended (never dropped).
    assert fake.segments[0]["status"] == "comprehended"
