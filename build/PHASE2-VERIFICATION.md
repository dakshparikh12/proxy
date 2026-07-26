# PHASE 2 VERIFICATION PATH — "the code is very good and does exactly what's intended"

The bar before Phase 3: not just *correct* (static analysis / CodeRabbit / guards), but
**proven to actually work on real and messy data, across all meaningful scenarios, with the
right method for each area** — and low-latency where it matters. Verify at **meaningful
checkpoints** (a capability/region completes, or a cross-region seam closes), NOT mechanically
per node. Each checkpoint uses the method that fits: **deepeval** for judgment quality,
**latency benchmarks** for timing, **red-testing** (adversarial vs ground-truth) for inference,
**deterministic assertions** for physics/security. Real + messy data, hard thresholds,
adversarial variants. Phase 2 is done only when every gate below is green.

## Tier 0 — correctness floor (continuous, already in place)
ruff + mypy --strict + bandit + CodeRabbit + the guards (naming · registry-closed ·
call_external · isolation triad · never-throw) + the full offline suite. Necessary, not
sufficient — it proves the code is *correct*, not that the product *works*.

## Tier 1 — region capability gates (run when a region is code-complete)
| # | Gate | Method | Hard bar |
|---|------|--------|----------|
| G1 | **Code-intel** (pre-meeting) — connect→clone→index→ready on DIVERSE real repos (small, large, multi-language, messy/monorepo) | **RED-TEST** every inference (who-calls / find-references / get-dependents / direct-answer) vs ground-truth incl. adversarial cases (decorators, dynamic dispatch, re-exports, generated code) + deepeval on direct-answer | precision/recall ≥ target; every "resolved" is exact, every approximation tagged lower-bound; 0 cross-file false-positives |
| G2 | **Transport** (in-meeting I/O) — STT/TTS/barge-in/ack on real vendor timing + messy multi-speaker audio | **LATENCY benchmarks** (real Recall/AssemblyAI/Cartesia, sparingly) + WER on code-heavy audio | barge-in cut <200ms · ack-audible p95 <500ms · first-grounded-audio p95 <5s · attribution correct on overlap |
| G3 | **Scribe** (in-meeting understanding) — DIVERSE messy real transcripts → notes | **deepeval** (decisions/actions/attribution correctness + faithfulness) + deterministic exactly-once/close | notes GEval ≥ 0.8 · corrections applied · 0 fabricated facts · close file complete |
| G4 | **Orchestrator** (in-meeting judgment) — wake/silent/grounded/catch-up/risk/propose/barge-in/recuse | **deepeval** battery on the real wake turn + code-intel tools | ≥7 diverse scenarios, mean ≥ 0.8, must-work ≥ 0.7, silent-when-unaddressed = 0 model calls, out-of-scope recuses (no fabrication) |
| G5 | **Workroom** (in-meeting action) — DIVERSE code tasks (add/fix/refactor/multi-file/impossible→honest-partial) | **deepeval** (edit correctness/groundedness) + **task-completion LATENCY** ("finish ASAP") + verify-gate catches a planted wrong claim | edit GEval ≥ 0.8 · task completes in-budget · impossible→honest partial (no fake success) |
| G6 | **Experience** (pre/post-meeting surfaces) — connect states / meeting-home / draft accept-reject / tile+chat | real routes + real data; **Law-3** human-control adversarial | every readiness state real · cross-tenant/token denied · accept idempotent+no-push · surfaces reflect real state |

## Tier 2 — integration-seam gates (run when a cross-region seam closes)
The seams are where hollow assemblies hide (the provider-seam defect lived here).
- **S1 connect→index→answer**: ask a grounded question about a *just-connected* real repo → correct, cited.
- **S2 transcript→scribe→wake→answer**: a spoken ask in a real transcript flows to a wake and a grounded answer.
- **S3 wake→dispatch→workroom→draft**: "build X" in a meeting → real staged draft persisted.
- **S4 draft→human-accept→applied**: human clicks accept → applied durably (never a push for code).
- **S5 barge-in→abort**: "Proxy, quiet" halts the MODEL loop + speech within budget.
- **S6 recycle→resume**: harness dies mid-task → next harness re-claims + resumes (no redo, no loss).

## Tier 3 — cross-cutting property red-tests (run once the regions are gated)
- **Isolation red-test**: adversarial cross-tenant attempts (cache/graph/notes/drafts/sandbox) ALL fail closed.
- **Cost/latency breaker**: cost caps + the abort watchdog actually fire under load.
- **Honest-failure sweep**: every degradation path speaks plainly (Law 2), never fabricates.

## Exit criteria (→ Phase 3)
All Tier-1 gates + Tier-2 seams + Tier-3 red-tests green on real/messy data with their hard
bars met. Then **Phase 3** = the full composed Doc-09 journeys end-to-end on live infra +
**optimization** (latency/cost tuning, making it work as well as possible) — an exhaustive pass
on a base we already know is very good.

## Harness
Reusable `tests/eval/` (deepeval batteries via `_battery.py`), `tests/latency/` (timing
benchmarks), `tests/redteam/` (adversarial ground-truth). Live tiers gated behind flags
(`CAPABILITY_LIVE_EVAL=1`, `LATENCY_LIVE=1`, etc.) so the offline suite stays hermetic.
Each gate names the nodes it covers → a coverage map ensures no meaningful behavior is unproven.
