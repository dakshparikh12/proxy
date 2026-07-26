"""Doc 09 · node ``journey.cost-drafts-persist`` — the persistence guarantee (§2 fifth
bullet + CANONICAL §3/§4).

Proves the two durable rows Proxy leans on for recovery — ``meeting_cost`` (so budget
reloads on a harness recycle, S5) and ``staged_drafts`` + its GCS bundle (so a draft is
accepted after teardown, S3) — are truly on the durable substrate (Postgres + GCS), NOT
in-process state that dies with the orchestrator.

The harness writes both rows on the REAL substrate through the REAL product seams
(``db.repos.cost.add_model_spend`` for ``meeting_cost``; ``workroom.drafts.propose_change``
for the ``staged_drafts`` row + Object-Versioned bundle), then simulates a **process kill**
and reads them back cold:

  * ``test_rows_survive_connection_kill_and_fresh_import`` — kill = close the connection +
    evict EVERY product module from ``sys.modules`` (so no module-level cache can carry the
    answer) + drop every in-process handle; the re-read opens a **fresh** psycopg connection
    and **freshly imports** the product, then asserts ``meeting_cost`` sum > 0 and the draft
    bundle body is present in the object store. Cost non-zero and bundle present prove the
    rows are durable.
  * ``test_rows_survive_a_real_process_kill`` — the strongest kill: the writer runs in a
    **child Python process** which is then **killed** (``SIGKILL``); a fresh parent process
    (this test) re-reads both rows on a brand-new connection. Nothing of the writer's memory
    can survive ``SIGKILL``, so a successful re-read can only be the durable substrate.

These would go RED if either row lived only in memory/cache: evicting the modules or killing
the writing process would lose it, and the cold re-read would find nothing (cost == 0 or the
bundle absent) — the two NOT-done conditions the node names.

Integration bodies open ``S.pg_conn()`` (skip when no local Postgres) and import the product
FIRST (missing product → RED, missing DB → skip). Rows are written to the real ``meeting_cost``
/ ``staged_drafts`` tables migrated to head — no in-memory dict stands in.
"""
from __future__ import annotations

import json
import os
import pathlib
import signal
import subprocess
import sys
import time
import uuid

import pytest

pytestmark = [pytest.mark.integration]

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "doc00"))
import _support as S  # noqa: E402  reuse pg_conn / apply_migrations / _local_dsn


# ---------------------------------------------------------------------------
# Helpers — the REAL substrate seams (no in-memory stand-in).
# ---------------------------------------------------------------------------
def _require_schema(conn) -> None:
    """Ensure meeting_cost + staged_drafts exist at head; skip if no reachable DB."""
    for table in ("tenants", "meetings", "meeting_cost", "staged_drafts"):
        if conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0] is None:
            r = S.apply_migrations(S._local_dsn() or "")
            assert r.returncode == 0, f"alembic upgrade head failed: {r.stderr}"
            return


def _read_cost_sum(conn, meeting_id) -> float:
    """The persisted budget sum the harness reloads on recycle (model+transport+e2b)."""
    row = conn.execute(
        "SELECT model_usd + transport_usd + e2b_usd FROM meeting_cost WHERE meeting_id = %s",
        (meeting_id,),
    ).fetchone()
    return float(row[0]) if row is not None else 0.0


def _read_draft(conn, draft_id):
    """The persisted staged_drafts row (artifact_ref, status) or None."""
    return conn.execute(
        "SELECT artifact_ref, status FROM staged_drafts WHERE draft_id = %s",
        (draft_id,),
    ).fetchone()


def _write_both_rows(conn, *, cost_usd: float) -> tuple[str, str, str]:
    """Write a meeting_cost + a staged_drafts row (+ GCS bundle) via the REAL seams.

    Returns ``(meeting_id, draft_id, artifact_ref)`` — the durable handles the cold
    re-read will resolve against a fresh connection / fresh process.
    """
    from libs.db import Database  # product first → red if absent
    from workroom import drafts

    db = Database.from_connection(conn)
    meeting_id = str(db.repos.meetings.create_bare(pinned_sha="HEAD").id)

    # meeting_cost — the seam meter's real write-through (upsert-increment).
    db.repos.cost.add_model_spend(meeting_id=meeting_id, usd=cost_usd)

    # staged_drafts + Object-Versioned GCS bundle — propose_change's real persist path.
    proposed = drafts.propose_change(
        conn,
        meeting_id=meeting_id,
        kind="code-change",
        summary="cost+drafts persist across a process kill",
        files=[{"path": "a.py", "new_content": "x = 1\n"}],
    )
    return meeting_id, str(proposed.draft_id), proposed.artifact_ref


# ===========================================================================
# 1. Kill via connection-close + full module eviction; re-read cold.
# ===========================================================================
@pytest.mark.integration
def test_rows_survive_connection_kill_and_fresh_import():
    """Write both rows → evict every product module + close the connection → re-read cold.

    The 'kill' evicts EVERY ``libs.*``/``services.*``/``workroom.*`` module from
    ``sys.modules`` and closes the connection, so nothing in this interpreter's product
    memory can answer the re-read. A fresh connection + fresh import then reads both rows
    back: cost sum > 0 and the draft bundle present. If either row lived only in memory,
    the eviction would lose it and this would go RED.
    """
    # Import the product FIRST so absence-of-product is a red failure, not a skip.
    from libs.db import Database  # noqa: F401
    from workroom import drafts, objectstore  # noqa: F401

    with S.pg_conn() as conn:
        _require_schema(conn)
        meeting_id, draft_id, artifact_ref = _write_both_rows(conn, cost_usd=0.37)

        # Sanity BEFORE the kill: both rows are readable on the writing connection.
        assert _read_cost_sum(conn, meeting_id) == pytest.approx(0.37)
        row = _read_draft(conn, draft_id)
        assert row is not None and row[1] == "proposed"

    # --- simulate the process kill --------------------------------------------------
    # (a) the writing connection is closed by the pg_conn context exit above;
    # (b) evict every product module so NO module-level cache can carry the answer.
    for name in list(sys.modules):
        if name.split(".")[0] in {"libs", "services", "workroom", "control_plane", "db"} or ".workroom" in name:
            del sys.modules[name]

    # --- cold re-read: a BRAND-NEW connection + a FRESH product import ---------------
    from workroom import objectstore as objectstore2  # a fresh import post-eviction

    with S.pg_conn() as conn2:  # a new connection == a new session to the substrate
        cost_after = _read_cost_sum(conn2, meeting_id)
        assert cost_after > 0, "meeting_cost sum must survive the kill (budget reloads, not reset to 0)"
        assert cost_after == pytest.approx(0.37), "the exact accrued spend must be intact"

        drow = _read_draft(conn2, draft_id)
        assert drow is not None, "the staged_drafts row must survive the kill"
        artifact_ref2, status = drow
        assert status == "proposed", f"the durable draft status is intact, got {status!r}"

        # the GCS bundle body must be present in the object store (durable, not in-memory).
        body = objectstore2.get(artifact_ref2)
        assert body is not None, "the draft bundle must be present in GCS after the kill"
        assert json.loads(body)["files"][0]["path"] == "a.py", "the bundle body is intact"


# ===========================================================================
# 2. Kill via a real child-process SIGKILL; re-read from a fresh parent process.
# ===========================================================================
_CHILD_WRITER = r"""
import json, os, sys
# The child is a fresh Python process; it writes both rows then prints their handles.
from libs.db import Database
from workroom import drafts

dsn = os.environ["TEST_DATABASE_URL"]
import psycopg
conn = psycopg.connect(dsn, autocommit=True)
db = Database.from_connection(conn)
meeting_id = str(db.repos.meetings.create_bare(pinned_sha="HEAD").id)
db.repos.cost.add_model_spend(meeting_id=meeting_id, usd=0.51)
proposed = drafts.propose_change(
    conn, meeting_id=meeting_id, kind="code-change",
    summary="written by a process that is about to be SIGKILLed",
    files=[{"path": "b.py", "new_content": "y = 2\n"}],
)
sys.stdout.write(json.dumps({
    "meeting_id": meeting_id,
    "draft_id": str(proposed.draft_id),
    "artifact_ref": proposed.artifact_ref,
}) + "\n")
sys.stdout.flush()
# Hang forever so the parent can SIGKILL us — nothing of our memory may survive.
while True:
    import time; time.sleep(3600)
"""


@pytest.mark.integration
def test_rows_survive_a_real_process_kill():
    """A child process writes both rows, is SIGKILLed, and a fresh parent re-reads them.

    ``SIGKILL`` gives the child no chance to flush any in-process cache — if either row
    lived in the child's memory it dies with it. The parent (this process) then re-reads
    both rows on a brand-new connection: cost sum > 0 and the bundle present prove the
    substrate, not memory, holds the state.
    """
    from libs.db import Database  # noqa: F401  product first
    from workroom import objectstore  # noqa: F401

    dsn = S._local_dsn()
    if dsn is None:
        pytest.skip("no local Postgres (set TEST_DATABASE_URL) for the real-process-kill harness")

    with S.pg_conn() as conn:
        _require_schema(conn)

    env = dict(os.environ, TEST_DATABASE_URL=dsn, DATABASE_URL=dsn)
    proc = subprocess.Popen(
        [sys.executable, "-c", _CHILD_WRITER],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True,
    )
    try:
        # Read the child's one line of handles (it blocks forever after printing).
        assert proc.stdout is not None
        deadline = time.monotonic() + 60
        line = ""
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if line.strip():
                break
            if proc.poll() is not None:  # child died before printing → surface stderr
                err = proc.stderr.read() if proc.stderr else ""
                pytest.fail(f"child writer exited early: {err}")
        assert line.strip(), "child writer did not emit the row handles in time"
        handles = json.loads(line)
    finally:
        # THE KILL: SIGKILL leaves the child no chance to flush anything in memory.
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=30)

    assert proc.returncode is not None and proc.returncode != 0, "the writer must have been killed"

    # --- fresh parent process re-read: brand-new connection, no shared memory ---------
    from workroom import objectstore as objectstore_read

    with S.pg_conn() as conn2:
        cost_after = _read_cost_sum(conn2, handles["meeting_id"])
        assert cost_after > 0, "meeting_cost must survive a SIGKILL of the writer (durable substrate)"
        assert cost_after == pytest.approx(0.51), "the accrued spend written before the kill is intact"

        drow = _read_draft(conn2, handles["draft_id"])
        assert drow is not None, "the staged_drafts row must survive the SIGKILL"
        artifact_ref, status = drow
        assert status == "proposed"

        body = objectstore_read.get(artifact_ref)
        assert body is not None, "the draft bundle must be present in GCS after the SIGKILL"
        assert json.loads(body)["files"][0]["path"] == "b.py", "the bundle body written before the kill is intact"


# ===========================================================================
# 3. Guard the risk the node names explicitly: an in-process cache must NOT
#    be able to false-pass. The store's body lives on the durable filesystem
#    substrate (a path), not a module-level dict that dies with the process.
# ===========================================================================
@pytest.mark.integration
def test_object_store_body_is_on_the_durable_substrate_not_a_memory_dict():
    """The bundle body is read back from on-disk durable storage, not process memory.

    Regression guard for the node's stated risk ("a test that reads back from an
    un-cleared in-process cache would false-pass"). We prove the body is resolvable from
    a path on the durable object substrate — surviving a fresh import — so the re-reads
    above cannot be satisfied by leftover in-memory state.
    """
    from workroom import objectstore

    ref = f"gs://proxy-drafts/{uuid.uuid4().hex}/bundle.json"
    objectstore.put(ref, json.dumps({"files": [{"path": "c.py", "new_content": "z = 3\n"}]}))

    # Evict the module so any module-level cache is gone, then re-import and re-read.
    for name in [m for m in list(sys.modules) if m.endswith("workroom.objectstore") or m == "objectstore"]:
        del sys.modules[name]
    from workroom import objectstore as fresh_store

    body = fresh_store.get(ref)
    assert body is not None, "the body must be readable from the durable store after a fresh import"
    assert json.loads(body)["files"][0]["path"] == "c.py"
