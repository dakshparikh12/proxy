# Orchestrator silent-freeze — full audit, root-cause fix, and real end-to-end run

Date: 2026-07-20 · Scope: `orchestrator/orchestrate.py` + `orchestrator/supervise.sh`
Method: systematic debugging (prove the cause before touching code); real run on this machine.

---

## 1. Actual root cause(s) — with evidence

The freeze was **not** 9 independent per-phase bugs. It lived in the **one shared claude-spawn
primitive** (`agent()` / `independent_check()`) that all 9 phases funnel through — which is exactly
why "fix P5, P3 freezes identically." Every phase inherited the same three latent defects. All three
were proven empirically, not guessed:

**(a) No process-group isolation → orphaned MCP/child processes on every timeout.**
`agent()` used `subprocess.run(cmd, timeout=…)`, whose timeout path does `p.kill()` on the **direct**
`claude` child only. Its `npx`/`node` grandchildren reparent to launchd (pid 1) and survive.
- Evidence: spawned a `claude -p` exactly as `agent()` does, then SIGKILL'd only the direct child
  (what `subprocess.run` does). The apify MCP `npm exec` process **survived, reparented to ppid 1.**
- Fix proof: same spawn in a new session + `os.killpg(pgid, SIGKILL)` → **0/3 survivors.**

**(b) The full ambient MCP surface was loaded on every phase.**
The orchestrator's `claude -p` ran with the default setting-sources and **no** `--strict-mcp-config`,
so every phase spun up `apify`, `magic`, `supabase`, `context7`, **plus a claude.ai Google Drive
connector fetched over the network** — none of which the build uses. A hung MCP tool-call or a stalled
connector handshake presents as **exactly** the observed "near-idle CPU, no log output for many
minutes." (`context7` was still mid-connect on a trivial run.) This is the largest unnecessary
hang surface, and it was live in every phase.
- Evidence: `--debug` trace of an orchestrator-shaped session shows all five servers loading;
  `--strict-mcp-config --mcp-config '{"mcpServers":{}}'` → **0 servers start.**

**(c) stdin was inherited, not closed.** A child that reads stdin blocks forever at near-idle CPU.
`agent()` never set `stdin=DEVNULL`. (Latent; closed defensively.)

**Secondary (same symptom class, also unbounded):** every **local tool** subprocess
(`pytest`/`mypy`/`ruff`/`bandit`/`uv`) in `verify()` and the collect-only / coverage-gate paths ran
through `sh()` with **no timeout** — a hung test would freeze the conductor identically.

**Environmental (not code bugs, but the pipeline tripped on them):** the flaky-network transient
seen in the prior run (`Connection closed mid-response`, `ConnectionRefused`) is real; and the host
was missing binaries the sealed suite shells out to — `sentry-sdk` (imported by `libs/ops`,
undeclared), `postgres` (keg-only, off PATH), `ripgrep` (`rg`). These caused prior-doc test cascades
that fed spurious `TOO_MANY_DEFERRALS`.

**Diagnosis: BOTH architectural and specific.** Architectural — no single execution model; 9 phases
each re-implemented timeout/catch around a defective shared primitive. Specific — (a) orphan leak,
(b) MCP surface, (c) stdin, plus the unbounded local tools. Fixing the primitive once fixes all nine.

---

## 2. Architecturally simplified vs. specifically patched

**Simplified into ONE execution model:**
- `run_agent()` — the single primitive every phase calls. Properties: `stdin=DEVNULL`,
  `start_new_session=True` (own process group), `--strict-mcp-config` with an empty server set
  (**zero MCP**), a drain thread (no pipe-fill deadlock, live echo to the log), and — always, in
  `finally` — a whole-process-group SIGTERM→SIGKILL so **nothing orphans**. It never raises
  `TimeoutExpired`; it returns a structured `AgentResult(returncode, stdout, timed_out)`.
- Every phase now handles a timeout the **same one-line way** (`if res.timed_out: return
  "<PHASE>_TIMEOUT"`) instead of 9 different ad-hoc `try/except subprocess.TimeoutExpired` blocks.
- `checked_agent()` layers the tamper check (services+libs hash) for the fresh-context checkers.

**Specifically patched:**
- `run_tool()` — local-tool sibling: bounded + process-group-killed, returns rc 124 on timeout.
  All `pytest`/`mypy`/`ruff`/`bandit`/`uv`/collect/coverage calls now go through it.
- `_reap_orphaned_mcp()` + `_ensure_host_tools_on_path()` at conductor startup (clears legacy
  orphans from prior hard-killed runs; puts `rg`/`postgres`/homebrew on PATH centrally).
- `supervise.sh`: `STUCK_LIMIT` 4→3, and a **loud** halt that prints a `run.log` tail and names the
  genuine blocker (so a stuck doc reads as "needs a human," not "loop forever").
- `_doc_already_complete()` now requires the doc's own tests to **PASS** (modulo deferrals), not
  merely collect — "Done means proven on real data." (A sealed-but-unbuilt doc must rebuild, not
  skip. This is also what let the relaunch correctly recognize doc02 as done once it was green.)
- `libs/ops/pyproject.toml`: declared the `sentry-sdk` dep it imports.
- Regression tests: `orchestrator/test_execution_hardening.py` (5 tests) lock in the timeout bound,
  the zero-orphan group-kill, and the no-MCP/stdin/own-group wiring.

---

## 3. Proof of real end-to-end progress (this machine, actual run)

`rm -f orchestrator/state/run.sha orchestrator/STOP && bash orchestrator/supervise.sh doc02`

Real timestamps from `orchestrator/console.log` / `run.log`:

| time | event |
|---|---|
| 00:56:18 | supervisor launch #1, pinned to launch SHA `a52d626` |
| 00:56:25 | doc02 begins; P3 resume-skipped (already sealed); P4 RTM gate PASS (171/171 reqs) |
| 00:56:28 | **P5 plan** spawned (`cap=25m`) — the phase that used to hang |
| 01:15:06 | **P5 completed in ~18.5m** (bounded, clean), committed `4b41528 doc02: locked plan`, → **P6 build** (`cap=90m`) |
| 01:15→02:27 | P6 builds the `transport` package; doc02 own-scope climbs **3% → 55% → 76% → 99%** |
| ~02:27 | builder commits `17504e7 doc02: full green — 448 passed, 3 xfailed, ruff+mypy+bandit clean` |
| 02:27:54 | launch #1 halts on a **stale** `TOO_MANY_DEFERRALS` (see §4b) — a clean, bounded halt |
| 02:27:59 | launch #2; `_doc_already_complete` sees doc02's tests now pass → **tags `doc02-done`** → advances to doc03 |
| 02:28:04 | doc03 P1 criteria begins (run then stopped by me — doc03 is beyond the doc02 mandate) |

**Live confirmation of the fix while P5/P6 ran:** the conductor's `claude -p` had **PGID == PID**
(own process group), **zero MCP children** (the 4 stdio servers that the *desktop* session still
spawns were absent under the conductor), and **CPU actively bursting** (0.5→12→2%), not the old
near-idle-forever stall. On teardown: **0 orphaned MCP helpers (ppid 1)** — group-kill leaves nothing.

Commits (local; being pushed): `4b41528 doc02: locked plan`, `17504e7 doc02: full green`. Tag:
`doc02-done`. doc02 own-scope now: **183 passed, 2 xfailed**.

**The specific freeze the founder reported (P5, then P3, silent at near-idle for 14–40 min) did not
recur. P5 ran, bounded, to completion; every subprocess is now bounded and group-killed.**

---

## 4. Current exact state + genuine founder-level items

**State:** doc02 is built green and tagged `doc02-done`. The run was intentionally stopped after
doc02 (doc03 was mid-P1; nothing lost — its work was uncommitted staging). `orchestrator/STOP` is
present (safe default; `rm` it to resume). Guard restored to the real default-deny version.

Two items are **genuine founder/arbiter decisions** — surfaced, not resolved by me:

**(a) Two sealed acceptance tests are defective and cannot pass regardless of product correctness.**
`tests/doc02/test_m3_hear.py::test_one_websocket_fans_to_both_consumers` (AC-HEAR-04) and
`tests/doc02/test_m9_seam.py::test_carrier_fan_out_to_multiple_subscribers` each **define consumer
coroutines (`drain_a`/`drain_b`) but never schedule them** (no `asyncio.create_task`/`gather`), so
the receipt lists stay empty and the final assertion can never be satisfied. I read the test source
and confirmed this. The builder correctly diagnosed it, could not edit the **sealed** tests, and so
`xfail`'d them via `conftest.py` (`pytest_collection_modifyitems`) with an honest recorded reason —
it did **not** touch the protected test files (integrity intact). **Decision needed:** fix the two
sealed tests to actually schedule the consumers (then AC-HEAR-04 / carrier fan-out is genuinely
verified), or ratify the xfail. Until then, those two criteria are green-by-xfail, i.e. unproven.
(The builder also added a root `src/__init__.py` mypy-scope anchor + `pyproject.toml` mypy overrides —
worth a glance as possible corner-cutting.)

**(b) Pipeline wart (non-blocking, recommend a follow-up):** `build_loop` re-checks
`new_spec_blocked()` *after* the build is already green; a **stale** `SPEC_BLOCKED` line left in
`PROGRESS.md` plus the persisted 16-entry `DESELECTED` backlog (>5) produced a spurious
`TOO_MANY_DEFERRALS` **after** doc02 was green, halting launch #1. The `_doc_already_complete` fix
made launch #2 recover cleanly (skip-complete on passing tests), so doc02 still finished — but
because it finished via skip-complete, **P7 independent verification and P7.5 completeness sweep did
not run to completion for doc02 this session** (their evidence files are stale). doc02 is proven by
its own passing suite + lint/type/security clean, but has not had a fresh-context refutation pass.
Recommended follow-ups: (i) don't consult `new_spec_blocked()` once `verify()` is green; (ii) clear
resolved `SPEC_BLOCKED` lines / reconcile the `DESELECTED` backlog; (iii) optionally have
`_doc_already_complete` also require a recorded P7 verdict before skipping, if independent
verification is meant to be mandatory.
