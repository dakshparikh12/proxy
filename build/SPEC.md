# proxy-build — system spec

The build system that takes the Proxy specs to a **production-verified, whole-product**
state. One ordered chain of tasks; when every task is built, integrated, and verified on
real data, the chain **provably reconstitutes the entire working Proxy product**.

This is the converged industry pattern (Anthropic's long-running-agent harness + Spec Kit /
Kiro's dependency-ordered task list), reimplemented natively as a plain folder — plus the
verification spine those tools lack. It replaces the `forge` plugin runtime.

---

## 1. Principles (non-negotiable)
1. **The specs are the only source of truth.** `product/v0-spec/*`; `CANONICAL-DECISIONS.md`
   overrides. No derived artifact (this folder included) ever overrides them.
2. **One chain, whole product.** The chain covers all 8 docs (00–05, 08, 09). A chain that
   omits a doc cannot prove completeness — so partial scope is not a mode.
3. **The integrated codebase is the memory.** Each node is built into the live product and
   the code that exists *is* the accumulated state. No giant context window; fresh context
   per node, fed a precise packet by the lead.
4. **Trust but verify — done means proven on real data.** A node flips `verified` only after
   its real acceptance path RAN green on real/held-out data with evidence shown. The `passes`
   field is never trusted on its own; the gate re-runs the real command.
5. **Fresh context where bias matters.** The plan critic and the per-node verifier are
   separate fresh-context agents. The builder never grades its own work.
6. **Drift prevention is the control flow.** The plan cannot be approved unless the closures
   are green; a node cannot advance unless its fresh verify passes. Guards are structural, not
   reminders (§6).
7. **Environment hardened, config frozen.** Every environment killer we have hit is asserted by
   `preflight.sh` (Phase 0) so it cannot recur; config (`pyproject.toml`, `uv.lock`, `.venv`,
   `.claude/settings.json`) is frozen for the run — a needed config change is a HALT, never an
   auto-edit. Phase time goes to phases, not to fighting the environment. Observability
   (`observability.md`) watches every agent call, tool call, config touch, and route transition.

## 2. The one artifact — `chain.json`
An ordered list of nodes. Order is a valid linearization of the dependency graph: every
`depends_on` references an **earlier** node (no forward references). Each node conforms to
`chain.schema.json` and carries five blocks — meaning, wiring, acceptance, build guidance,
status. See `chain.schema.json` for the authoritative field list; the short version:

- **Meaning:** `spec_refs`, `requirement_ids`, `criterion_ids`, `intent`, `definition_of_done`.
  `intent`/`definition_of_done` cite and interpret the spec; they never re-copy it.
- **Wiring:** `consumes`, `exposes`, `depends_on`, `integration_point`.
- **Acceptance:** `acceptance` (EARS), `acceptance_cmd` (pytest selector args, run by
  `build/gates/verify-node.sh`), `evidence_class`, `journeys_now_live` (the cumulative product
  state true after this node — each id ∈ `build/journeys.json`).
- **Build guidance:** `codebase_anchors`, `invariants`, `risks`, `human_gated`.
- **Status:** `not-built` | `built:needs-verification` | `verified`.

Node granularity: a node is right-sized when it has **exactly one testable acceptance
criterion**, fits **one build session** (`/clear` boundary), and touches a **file set disjoint
from its siblings**. Too big to verify in one pass → split; no independent criterion → merge.
**Every node cites ≥1 sealed criterion** — there are no criterion-less nodes; a wiring/journey
node cites the relevant cross-cutting or Doc-09 journey criterion (Doc-09 is sealed like any
other doc, its criteria being the integration journeys). This keeps Tier-A A1 uniform.

## 3. Completeness — proving the chain IS the whole product
Four tiers, decreasing mechanical certainty, increasing subtlety. This is the guarantee that
"every node done ⇒ fully working Proxy," down to the wiring.

- **Tier A — mechanical closure** (`check_completeness.py`, deterministic, un-driftable):
  1. **Requirement closure (both ways):** every sealed criterion (`acceptance/doc*/…`) is
     cited by ≥1 node, and every node cites ≥1 real criterion. No gaps, no scope creep.
  2. **Wiring closure:** every `consumes` is produced by an earlier `exposes` (or a declared
     external input); every `exposes` is consumed by someone (or a declared product endpoint).
     A dangling consumer = the "routes-together" gap; a dangling producer = dead code.
  3. **Journey closure:** every Doc-09 integration journey (enumerated into `build/journeys.json`)
     maps step-by-step onto nodes.
- **Tier B — semantic critic** (fresh agent): hunts *implicit* obligations Tier A can't see
  (implied but never stated) and weak/untestable nodes. Loops to zero.
- **Tier C — human HALT:** you are the backstop for the truly-unstated. The one gap no
  algorithm closes.
- **Tier D — scenario coverage:** generate a diverse, spec-grounded scenario corpus (normal
  journeys, edges, failure/negative paths, cross-feature interactions). At plan time, trace
  each scenario against the chain — any scenario the chain can't serve end-to-end is a gap.
  At build time, the same corpus becomes the real-data test suite. Generated once, used twice:
  completeness at plan time, verification at build time. Diversity over raw count. **Plan-time
  tracing proves *logical* coverage (no missing nodes), not runtime correctness** — runtime is
  proven per-node (Phase 2) and whole-product (Phase 3); a logically-covered scenario that fails
  at runtime is a product bug to fix, not a completeness gap.

## 4. The phases
**Phase 1 — Plan.** Fresh readers (one per region; the Doc-09 reader also writes
`build/journeys.json`) read spec + code → unsealed docs **04/05/08/09** get their acceptance
bundle sealed (**HALT 1b**, human-gated, committed before synthesis) → lead authors + schema-
validates `chain.json` → Tier A must exit 0 → Tier B loops to zero → Tier D corpus traced →
**HALT 1** (you approve the chain + closure report + `decisions.md` + scenario coverage).

**Phase 2 — Build.** Walk the chain in order. Per node: **PLAN** (builder reads node → cited
spec → codebase → plan) → **BUILD** (TDD on the real path) → **INTEGRATE** (wire into the live
product at `integration_point`) → **VERIFY (fresh context)** (independent reviewer given only
the node's DoD + cumulative `journeys_now_live` confirms: node green on real data, new journeys
green, no prior regression, invariants intact — evidence shown) → **ADVANCE** (flip `verified`,
commit, `/clear`, next). `built:needs-verification` nodes skip PLAN/BUILD and go straight to a
fast VERIFY. Optional HALT at each region boundary.

**Phase 3 — Sign-off.** Because every node was integrated + verified as it landed, this is one
whole-product pass: `build/gates/signoff.sh` (ruff + mypy --strict + bandit + full offline suite,
self-contained) + the full scenario corpus + Doc-09 journeys on real infra + deepeval ≥ baseline.
→ **HALT** (final).

## 5. Absorbing existing work (00–03) — no lost progress, no slow re-audit
The chain covers the whole product; existing code pre-populates the already-built nodes. In
Phase 1 the readers map real files onto nodes and seed `status`:
- **00, 01** are production-verified — start `built:needs-verification`; Phase 2 gives them a
  fast *confirmation* run (still green, still integrated), **not** a rebuild.
- **02, 03** are substantially built — start `built:needs-verification`; Phase 2 verifies each
  node against the existing code; anything red or missing (e.g. the inbound-chat gap) gets the
  full 5-step loop, node-scoped.
Verification is **per node + scenario-scoped**, not doc-level re-auditing — that is why this is
fast, unlike the prior hours-long verify pass.

**No code migration.** Existing code stays exactly where it is. It is not owned by "forge" vs
"proxy-build" — it is one repo, and proxy-build reads that repo, maps existing code onto built
nodes, verifies them, and builds the gaps. Moving code into a folder would break the uv monorepo
(imports, tests, alembic) for zero benefit. The safety net for "worse comes to worst" is **git**
(a dedicated branch + revert), not a copied folder.

## 6. Drift guards (each a single-purpose mechanism; collectively exhaustive)
| Drift mode | Guard |
|---|---|
| Plan misses a spec obligation | Tier A requirement closure + Tier B critic + Tier D scenarios |
| Plan invents work with no spec basis | Tier A reverse closure (every node cites a criterion) |
| Wrong build order (forward dep) | Tier A order-validity (`depends_on` backward-only) |
| Builder builds the wrong thing / a duplicate | Lead hands a tight packet bounded to one node + DoD |
| Builder fakes done | `verify-node.sh` re-runs the real cmd (exit-5/no-op = FAIL); the fresh read-only verifier flags any edit to a sealed test/golden/`_baseline` in the diff |
| Builder breaks something earlier | Per-node VERIFY includes prior-journey regression |
| Builder grades its own work | VERIFY is a separate fresh-context reviewer |
| Verifier lacks an oracle | Node declares `journeys_now_live`; oracle is explicit |
| Spec genuinely ambiguous | Bounded question to human; real `SPEC_BLOCKED` escalates |
| Context fills over a long build | Fresh context per node; codebase is the memory |
| Test passes for the wrong reason | Anchor-first reviewer + deepeval ≥ baseline |
| A gate crashes and reads as pass | Fail-closed gates (crash → FAIL) |
| Environment killers (testmon/iCloud venv) | `preflight.sh` asserts the known-good state (Phase 0) |
| Config changed mid-run | Config-freeze fingerprint (preflight) + observability config-touch alert |
| Silent stall / hour-long hang | OTel per-span latency + soft phase budgets → "look, don't kill" (no silent hour) |
| Route deviates from LOOP.md | Anti-drift assertions on the trace stream (`observability.md` §3) |

## 7. Human-gated (escalate, never auto-approve)
Acceptance-bundle seal · `_baseline.json` · `EXTRACTION_COUNT_HALT` · destructive/prod
migration · prod deploy · a genuine `SPEC_BLOCKED`.

## 8. Definition of Done
Every node `verified` · Tier A green · Tier B zero-gap · Tier D corpus green on real infra ·
Doc-09 journeys green · deepeval ≥ baseline · ruff + mypy --strict + bandit clean · no
law/invariant-violating path · evidence committed. **Done means the product is proven on real
data — not that the code compiles.**

## 9. Self-contained — what it owns vs reuses
- **Owns** (in `build/`): `chain.json`, `chain.schema.json`, `check_completeness.py`,
  `gates/verify-node.sh` (per-node), `gates/signoff.sh` (Phase 3), `preflight.sh`, the docs.
  Decoupled from `done-check.sh`/`slices/` entirely (no signature mismatch, no two-impl ambiguity).
- **Reuses**: source of truth `product/v0-spec/*` + `acceptance/doc*/`; constitution `CLAUDE.md`;
  fresh-context agents in `.claude/agents/{analyst,criteria-auditor,coverage-auditor,planner-reviewer,reviewer}.md`.
  (The read-only PreToolUse guard in `.claude/hooks/` is kept as a reference but NOT wired globally
  — the forge version bluntly blocks all writes under `tests/`/`acceptance/`, which would break
  legitimate TDD test-creation and Phase-1 bundle sealing; sealed-artifact immutability is enforced
  by the fresh read-only verifier inspecting the diff.)
- **Deleted**: the entire `forge/` plugin (runtime `forge-run.js`, gates, its plugin manifests,
  budgets, the hand-rolled monitor) — salvaged pieces were relocated into `.claude/` + `build/`.
