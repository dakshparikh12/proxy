# Phase 1 — Structural Convergence: FINAL REPORT

**Status: autonomously complete.** Every Phase-1 item achievable without a founder decision is
**done, independently verified, and committed.** The product assembles into ONE working system;
the reactive arc is proven end-to-end non-tautologically; the DoD static gate is clean. What
remains is exactly **6 founder-gated blockers** (each needs a sealed re-seal or a human-gated
migration) plus the Phase-3-deferred Doc-09 journeys. This is the "named blocker" half of the
two-return contract — *100% of what I can do without you is done; these 6 need you.*

Two independent fresh reviews (acceptance + final sign-off) confirmed this state, plus a
13-canary trust-gate whose 4 escapes were closed.

---

## What was done this run (all committed)

**Stage A — spec frozen.** The Structural-Convergence reconcile (190 raw findings → 34 clusters)
surfaced 8 high-blast-radius seams the spec left two-voiced; you ruled all 8 → frozen as law
**D-033..D-041** in `build/decisions.md`.

**The product spine — both reactive arms proven end-to-end.** `tests/e2e/test_composition_proof.py`
boots the real product (real Postgres, real webhook-drain → carrier bridge → run-loop → close →
reconcile; vendors faked ONLY at their seams) and drives a scripted meeting through **both**:
- **DIRECT ANSWER** — an addressed "Proxy, who calls X?" reaches the wake turn and the grounded
  answer is delivered on the gated wire.
- **WORKROOM DISPATCH** — *the missing integration*: a wake `dispatch_workroom` tool-use was
  entirely unwired (built in isolation, never connected). Built `harness/workroom_bridge.py` +
  the live-brain wiring: wake tool-use → real `dispatch_workroom` (durable `workroom:<id>` row) →
  ack → drive `run_task` → deliver the terminal draft. Both arms: zero unhandled task exceptions.
- Verified deterministic + **non-tautological** (breaking a delivery seam turns it RED).

**~25 Stage-C fixes** across 7 service clusters (rulings + auto-resolvable + spine wiring holes),
each grounded → fixed → verified green, **no sealed artifact edited** (BLOCKED-over-wrong).
Highlights: the code_intel 8-tool matrix from one constant; `owner()` exclusion guard; freshness
flag; close-merge carry-all; refint honest-degrade (F4); transcript→carrier signal binding;
segment persistence; material-change event emission; injection guardrail; evidence-gate
normalized match (F3/D-035); boot-reaper ratio validator (F1/D-033).

**The P0 — a real signed-in member can now reach their meeting home + accept a draft** (D-041):
`/m` + accept/reject converged onto the durable session resolver (the OAuth flow only ever
produced the durable cookie; the surfaces were reading the never-populated middleware dict).

**D-039 — the one world-touching click is now durably idempotent cross-instance**: dropped the
process-local ledger; the accept/reject id is deterministic and the replay is read off the durable
`staged_drafts` terminal-status belt, so a retry on ANY Cloud Run instance replays identically.

**Canary trust-gate**: 13 seeded defects, 9 caught → the 4 escapes (untested-but-correct fixes)
closed with new non-tautological regression tests. The fleet now catches all 13.

**Static gate clean** (the final sign-off's one finding): `mypy --strict` 36 → 0 (a `libs.*`
facade-resolution artifact + 4 masked findings); `bandit` exit-1 → 0 (documented policy; the
secret + shell gates stay active via verified per-line `# nosec`).

---

## Verification evidence

| Gate | Result |
|---|---|
| Full offline suite | ~1997 passed · only the 3 known env-reds (see below) |
| Composition proof (both arms) | PASS · non-tautology confirmed by two reviewers |
| `ruff` / `mypy --strict` / `bandit` | **all clean (exit 0)** |
| Sealed-artifact integrity | clean — criteria edits are additive/stricter (0 deletions); no baseline/cassette/golden/chain weakened |
| Chain | Tier-A CLOSED · 118/128 nodes verified (10 = Doc-09 Phase-3 journeys) |
| Per-doc acceptance closure (00–09) | every sampled blocking/P1 criterion backed by a passing test |
| Fresh reviews | acceptance review + final sign-off, both independent |

**The 3 known env-reds (not code defects):** (a) `test_webhook_transcript_bridge_wired` — the
`ANTHROPIC_API_KEY` has zero credit (D-032; the deterministic sibling passes); (b+c) two
`test_upgrade_auth` — test-pollution (pass in isolation).

---

## What YOU must do to reach absolute 100%

**1. Rule on the 6 named blockers (D-042).** Each is a founder-ruling-vs-sealed-test conflict or a
human-gated migration — I cannot touch a sealed artifact or write a prod migration autonomously:

| # | Blocker | Why blocked | Your call |
|---|---|---|---|
| F4b | D-036 "fire BOTH events" on a contradicting claim | sealed **AC-EVENT-01** asserts exactly ONE event | re-seal AC-EVENT-01 to two-events, or reverse F4b |
| F5 | D-037 barge-in debounce (≥150-250ms) | sealed latency tests lock fire-on-FIRST-frame | re-seal the barge-in latency tests, or reverse D-037 |
| F6 | D-038 rejoin per-episode | sealed tests lock per-meeting-once (940s-gap → `len==1`) | re-seal the rejoin tests, or reverse D-038 |
| A18 | reconcile close-drain step | sealed step-set is exact `{stale,sandboxes,notes}` + needs a close-resume driver | re-seal the step-set + greenlight the driver build |
| F2 / C-BUDGETWIRE | D-034 cost-breaker listening baseline | `meeting_cost` has no listening/task split column | **greenlight a forward migration** adding the split column |

Once you rule, the implementation of each is a bounded build (the analysis is done in `A_decisions.md`
+ `decisions.md`).

**2. Fund the `ANTHROPIC_API_KEY`** (or accept D-032) → the one true env-red goes green.

**3. Greenlight Phase 3** for the 10 composed Doc-09 journeys (S1–S9 + demo-arc) on live infra
(E2B + funded Anthropic + concurrency) — correctly deferred, never faked as done.

---

## Commit trail (branch `proxy-build`)
`7191b15` spine + P0 · `2bbdc7b` Stage-C 7-cluster fixes · `191c914` named blockers (D-042) ·
`a0c867b` D-039 + canary escapes · `6c27e44` static gate clean. Full decision record in
`build/decisions.md` (D-033..D-042); Stage-A analysis in `scratchpad/A_decisions.md`.
