"""Layer 4 chaos — doc00 (durable substrate): kill Postgres mid-write.

Steady state : the product asyncpg pool (``db.open_pool``) commits and reads rows
               over a prod-shaped Unix-socket DSN.
Fault        : SIGKILL the live postmaster while an UNCOMMITTED write is in flight.
Invariant    : the client sees a controlled connection error (not a silent
               success), and after crash recovery the committed row survived while
               the uncommitted 'phantom' row was rolled back — atomicity + durability
               hold across a hard crash. This is the doc00 §5 substrate promise.

Run directly:  python verification/chaos/doc00.py   (prints JSON)
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import pathsetup  # noqa: E402

pathsetup.bootstrap()
from config.chaos_lib import ChaosResult, EphemeralPostgres, kill_process, wait_gone  # noqa: E402


async def _experiment(pg: EphemeralPostgres) -> ChaosResult:
    from db.database import open_pool  # product durable-substrate seam

    pool = await open_pool(pg.dsn())
    async with pool.acquire() as con:
        await con.execute("CREATE TABLE chaos_rows (id int primary key, v text)")
        await con.execute("INSERT INTO chaos_rows VALUES (1, 'committed')")
    steady = "pool commits + reads a row over the prod-shaped unix-socket DSN"

    # Open an UNCOMMITTED write, then crash the server mid-transaction.
    con2 = await pool.acquire()
    tr = con2.transaction()
    await tr.start()
    await con2.execute("INSERT INTO chaos_rows VALUES (2, 'phantom-uncommitted')")

    pid = pg.postmaster_pid()
    assert pid is not None
    kill_process(pid)          # SIGKILL the postmaster mid-write
    wait_gone(pid)

    # The client's in-flight connection must fail loudly, not pretend success.
    # A raised connection error OR a hang-then-timeout both count as "did not
    # silently succeed"; only a clean SELECT return would falsify the invariant.
    client_errored = False
    try:
        await asyncio.wait_for(con2.execute("SELECT 1"), timeout=5.0)
    except Exception:          # asyncpg connection-lost family, or TimeoutError
        client_errored = True
    pool.terminate()           # non-graceful (server is gone)

    # Crash recovery: restart the SAME data dir; WAL replays committed work only.
    pg.start()
    pool2 = await asyncio.wait_for(open_pool(pg.dsn()), timeout=15.0)
    rows = await asyncio.wait_for(
        pool2.fetch("SELECT id, v FROM chaos_rows ORDER BY id"), timeout=10.0)
    pool2.terminate()
    ids = [r["id"] for r in rows]

    survived = client_errored and ids == [1]
    detail = (f"client_errored={client_errored}; rows_after_recovery={ids} "
              f"(expected [1]: committed survived, uncommitted rolled back)")
    return ChaosResult(name="kill_postgres_mid_write", doc="doc00", steady_state=steady,
                       fault="SIGKILL postmaster during an uncommitted INSERT",
                       survived=survived, detail=detail)


def run() -> ChaosResult:
    pg = EphemeralPostgres()
    if not pg.available:
        return ChaosResult(name="kill_postgres_mid_write", doc="doc00",
                           steady_state="", fault="SIGKILL postmaster mid-write",
                           survived=False, detail="", skipped=True,
                           skip_reason="no Postgres binary (initdb/pg_ctl/postgres) found on host")
    try:
        pg.start()
        return asyncio.run(_experiment(pg))
    finally:
        pg.cleanup()


if __name__ == "__main__":
    print(json.dumps(run().to_dict(), indent=2))
