"""Reusable fault-injection primitives (Layer 4).

We deliberately do NOT depend on chaostoolkit: its last real code commit was
2024-05-01 (~2 years stale as of this writing), so per the framework's maturity
policy we inject faults directly. These primitives are small, dependency-free,
and target the same failure modes chaostoolkit would (process kill mid-write,
process hang via SIGSTOP, bounded-timeout supervision) against the REAL product
seams — a live Postgres postmaster, a live ``git`` subprocess, a stalled child.

Each experiment returns a :class:`ChaosResult` so the orchestrator can render a
uniform report. An experiment that cannot run for a benign infra reason (no
Postgres binary, etc.) returns ``skipped=True`` with a reason — it never fabricates
a pass.
"""
from __future__ import annotations

import dataclasses
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path


@dataclasses.dataclass
class ChaosResult:
    name: str
    doc: str
    steady_state: str          # the hypothesis asserted true BEFORE the fault
    fault: str                 # the fault injected
    survived: bool             # did the post-fault invariant hold?
    detail: str                # human-readable evidence
    skipped: bool = False
    skip_reason: str = ""

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    def line(self) -> str:
        if self.skipped:
            return f"[SKIP] {self.doc}/{self.name}: {self.skip_reason}"
        verdict = "SURVIVED" if self.survived else "FAILED"
        return f"[{verdict}] {self.doc}/{self.name} — {self.fault}"


# ---------------------------------------------------------------------------
# Generic process faults
# ---------------------------------------------------------------------------
def kill_process(pid: int, sig: int = signal.SIGKILL) -> None:
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass


def wait_gone(pid: int, timeout: float = 5.0) -> bool:
    """Poll until pid is gone (SIGKILL'd) or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


def sigstop(pid: int) -> None:
    """Freeze a process (simulate an unresponsive hung vendor call)."""
    kill_process(pid, signal.SIGSTOP)


def sigcont(pid: int) -> None:
    kill_process(pid, signal.SIGCONT)


# ---------------------------------------------------------------------------
# Ephemeral Postgres — for the durable-substrate kill-mid-write experiment
# ---------------------------------------------------------------------------
def _find_pg_bin() -> Path | None:
    """Locate a Postgres bin dir with initdb+pg_ctl+postgres (Homebrew/apt)."""
    candidates = [
        os.environ.get("PROXY_PG_BIN"),
        *[str(p) for p in Path("/opt/homebrew/opt").glob("postgresql@*/bin")],
        *[str(p) for p in Path("/usr/local/opt").glob("postgresql@*/bin")],
        "/usr/lib/postgresql/16/bin",
        "/usr/lib/postgresql/15/bin",
    ]
    for c in candidates:
        if not c:
            continue
        b = Path(c)
        if (b / "postgres").exists() and (b / "initdb").exists() and (b / "pg_ctl").exists():
            return b
    return None


class EphemeralPostgres:
    """A throwaway Postgres cluster on a private Unix socket.

    Mirrors the prod shape (Cloud SQL Auth Proxy Unix socket, no app-side SSL)
    so the product's asyncpg pool connects unchanged. Exposes the live postmaster
    pid so an experiment can ``kill -9`` it mid-write.
    """

    def __init__(self) -> None:
        self.bin = _find_pg_bin()
        self.tmp = Path(tempfile.mkdtemp(prefix="verif-chaos-pg-"))
        self.datadir = self.tmp / "data"
        # Postgres rejects a Unix-socket path longer than 103 bytes, so the
        # socket dir MUST be short — put it directly under /tmp, not under the
        # (very long) sandbox temp path where the data dir lives.
        self.sockdir = Path(tempfile.mkdtemp(prefix="vpg", dir="/tmp"))
        self.user = "proxy_chaos"
        self.dbname = "postgres"
        self._started = False

    @property
    def available(self) -> bool:
        return self.bin is not None

    def dsn(self) -> str:
        # asyncpg: unix socket via ?host=<dir>
        return f"postgresql://{self.user}@/{self.dbname}?host={self.sockdir}"

    def postmaster_pid(self) -> int | None:
        pidfile = self.datadir / "postmaster.pid"
        if not pidfile.exists():
            return None
        try:
            return int(pidfile.read_text().splitlines()[0])
        except (ValueError, IndexError):
            return None

    def start(self) -> None:
        """Start the cluster, initialising the data dir only on first start.

        Idempotent w.r.t. initdb so it can be called again after a crash (SIGKILL)
        to exercise WAL crash recovery on the same data directory.
        """
        assert self.bin is not None
        self.sockdir.mkdir(parents=True, exist_ok=True)
        if not (self.datadir / "PG_VERSION").exists():
            subprocess.run(
                [str(self.bin / "initdb"), "-D", str(self.datadir), "-U", self.user,
                 "--auth=trust", "-A", "trust", "--no-sync"],
                check=True, capture_output=True, text=True, timeout=60,
            )
        # Do NOT use pg_ctl -w: its readiness probe connects to the *default*
        # socket path, not our custom -k dir, so it would hang. Launch without
        # waiting, then poll for our socket + a live postmaster ourselves.
        logfile = self.tmp / "pg.log"
        proc = subprocess.run(
            [str(self.bin / "pg_ctl"), "-D", str(self.datadir), "-l", str(logfile),
             "-o", f"-k {self.sockdir} -h '' -c listen_addresses=''", "start"],
            capture_output=True, text=True, timeout=20,
        )
        sock = self.sockdir / ".s.PGSQL.5432"
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if sock.exists() and self.postmaster_pid() is not None:
                self._started = True
                return
            time.sleep(0.1)
        log = logfile.read_text() if logfile.exists() else ""
        raise RuntimeError(f"postgres did not become ready rc={proc.returncode}: "
                           f"{proc.stdout}\n{proc.stderr}\n{log}")

    def stop(self) -> None:
        if self._started and self.bin is not None:
            subprocess.run([str(self.bin / "pg_ctl"), "-D", str(self.datadir),
                            "-m", "immediate", "stop"], capture_output=True, text=True)
        self._started = False

    def cleanup(self) -> None:
        try:
            self.stop()
        finally:
            shutil.rmtree(self.tmp, ignore_errors=True)
            shutil.rmtree(self.sockdir, ignore_errors=True)


def free_tcp_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port
