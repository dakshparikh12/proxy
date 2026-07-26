# PHASE 3 — EXHAUSTIVE E2E + OPTIMIZATION → PRODUCTION-VERIFIED

**Entry condition:** every Phase-2 gate green (`build/PHASE2-VERIFICATION.md`) — the code is
already *very good and does exactly what's intended*. Phase 3 does NOT hunt for correctness
bugs (Phase 2 did that). It proves the WHOLE product works exhaustively on live infra, then
**optimizes it to the best achievable**, and issues a production-readiness certificate with
**measured** evidence.

Everything here is **measured**: each item carries a baseline → target → achieved number, so
"works as well as possible" is a number we hit, not a claim.

---

## Part A — Exhaustive whole-product E2E (composed, LIVE infra)
Not per-region — the *whole product composed*, webhook → meeting → answer → build → draft →
accept → close, on real Recall / Anthropic / E2B / Postgres / GCS.

- **A1 · The 10 Doc-09 journeys end-to-end, live.** s1 happy-arc, s2 dep-graph blast-radius,
  s3 staged-draft-accept, s4 barge-in human-control, s5 recycle-survives, s6 concurrency,
  s7 cost-breaker, s8 honest-failure, s9 tenant-isolation, demo-arc — each run as ONE composed
  flow on live infra, deepeval-scored end-to-end.
- **A2 · The full demo-arc.** A realistic complete meeting: Proxy joins, listens, answers
  grounded questions, does a real code task, surfaces a risk, is barged-in ("Proxy, quiet"),
  produces notes + a staged draft, a human accepts. Scored on the whole arc.
- **A3 · Chaos / resilience.** Recycle mid-meeting, injected vendor faults (STT/TTS/SDK 5xx,
  socket drops, timeouts), a killed sandbox → **honest degradation**, never a crash or a
  fabricated result. Every failure path speaks plainly (Law 2).
- **A4 · Scale / load.** N concurrent meetings + large/messy repos + long meetings — isolation
  holds (no cross-tenant bleed under load), performance holds, cost stays bounded.
- **A5 · Full scenario corpus** (`build/scenarios/`) replayed on real infra.

## Part B — Optimization ("make everything work as well as possible")
Each is a measured tuning loop: profile → change → re-measure → keep if better.

- **B1 · Latency** (the founder's priority). Profile + tune the hot paths and hit the SLOs
  with margin: STT→transcript, transcript→wake→first-audio, TTS + barge-in cut (<200ms),
  ack (<500ms p95), Workroom task-completion ("finish ASAP"), prompt-cache hit-rate, first
  token. Table: metric · baseline · SLO · achieved.
- **B2 · Cost.** Right model-seat per task (D-014), maximize the 1-hr prompt cache, token
  budgets/clamps, kill redundant calls. Minimize **$/meeting**; report before → after.
- **B3 · Quality.** Feed the Phase-2 deepeval scores back into the prompts/behaviors and
  raise answer / notes / edit quality on the diverse batteries. Report score deltas.
- **B4 · Robustness.** Close every red-team edge case surfaced in Phase-2 Tier-3.

## Part C — Production-readiness certificate
- **C1** `build/gates/signoff.sh` green (ruff + mypy --strict + bandit + full offline suite).
- **C2** Every capability battery + all 10 journeys ≥ baseline on **live** infra.
- **C3** Live-E2E tiers green (`DOC03_LIVE_E2E` etc.); the **E2B production template baked +
  published** (the one standing deploy residual, Node sidecar + ast-grep); Langfuse capturing
  real traces.
- **C4** Security/isolation red-test clean (cross-tenant fails closed under load).
- **C5** **The production-readiness matrix** — the single artifact that says "ship it":
  every one of the **5 laws** + **6 invariants** + each **SLO** (latency/cost) + each
  **capability**, one row, each with a link to the real evidence + the achieved metric.

**Exit:** return in exactly one state — **PRODUCTION-VERIFIED** (with the matrix + the
latency/cost/quality numbers) or a specific **BLOCKED / SPEC_BLOCKED** list. Then it is a
decision to deploy (founder-gated), not a question of whether it works.

---

## Method / harness
- Live composed runs: a `tests/e2e/` driver that boots the real control_plane + a real meeting
  (or a recorded-real Recall session), gated `PROXY_LIVE_E2E=1`.
- Load: a concurrency harness spinning N meetings against the real substrate.
- Optimization: a `bench/` profile+measure loop (latency + cost meters → Langfuse) with the
  before/after tables checked in.
- Everything reuses the Phase-2 `tests/eval` (deepeval), `tests/latency`, `tests/redteam`
  harnesses — Phase 3 composes + scales them, it doesn't reinvent them.

## What Phase 3 is NOT
Not a place to discover the product doesn't work (that's a Phase-2 gate failure to fix first),
and not a place to build new features. It is: prove-it-all-composed-live, then make-it-fast-
cheap-and-excellent, then certify.
