"""Layer 4 chaos — doc02 (voice/transport): a hung vendor call (SIGSTOP).

Steady state : a bounded supervisor invokes a "vendor" child (stand-in for the
               Cartesia/Recall seam, which in prod is an async call wrapped by the
               ``libs/http`` call_external timeout) and gets a prompt reply.
Fault        : SIGSTOP the vendor child so it is frozen and will NEVER respond —
               the classic unresponsive-third-party hang.
Invariant    : the supervisor REGAINS control within its timeout budget and
               terminates the frozen child, rather than blocking the meeting
               forever. "Human control is absolute" / barge-in cannot be held
               hostage by a stalled vendor. Bound here: 2.0s (± slack).

Run directly:  python verification/chaos/doc02.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import pathsetup  # noqa: E402

pathsetup.bootstrap()
from config.chaos_lib import ChaosResult, kill_process, sigstop  # noqa: E402

_BOUND_S = 2.0
_VENDOR = (
    "import sys, time\n"
    "sys.stdout.write('READY\\n'); sys.stdout.flush()\n"
    "time.sleep(600)\n"          # a slow vendor turn; we freeze it before it ends
)


def run() -> ChaosResult:
    proc = subprocess.Popen([sys.executable, "-c", _VENDOR],
                            stdout=subprocess.PIPE, text=True)
    # steady state: the child is alive and greeted us.
    ready = proc.stdout.readline().strip() if proc.stdout else ""
    steady = f"vendor child responsive (handshake={ready!r})"

    # Fault: freeze the vendor mid-call — it will never reply again.
    sigstop(proc.pid)

    # Bounded supervisor: wait up to _BOUND_S for a (never-coming) result, then
    # reclaim control by killing the frozen child.
    start = time.monotonic()
    reclaimed = False
    try:
        proc.wait(timeout=_BOUND_S)
    except subprocess.TimeoutExpired:
        kill_process(proc.pid)     # SIGKILL overrides SIGSTOP; the child dies
        try:
            proc.wait(timeout=3.0)  # reap the child; wait() returning == it's dead
            reclaimed = True
        except subprocess.TimeoutExpired:
            reclaimed = False
    elapsed = time.monotonic() - start

    within_budget = reclaimed and elapsed <= _BOUND_S + 1.0
    detail = (f"handshake={ready!r}; regained_control={reclaimed}; "
              f"elapsed={elapsed:.2f}s (budget {_BOUND_S}s + slack)")
    return ChaosResult(name="hung_vendor_call_sigstop", doc="doc02", steady_state=steady,
                       fault=f"SIGSTOP the vendor child; supervisor bound {_BOUND_S}s",
                       survived=within_budget, detail=detail)


if __name__ == "__main__":
    print(json.dumps(run().to_dict(), indent=2))
