# LOOP.md — the routing the lead session follows

**You are the lead session — the main Claude Code session, not a script.** You hold the chain,
route context to worker subagents (dispatched via the Agent tool, `subagent_type:` the agent
name), and never grade your own work. Read `SPEC.md` first. Source of truth is
`product/v0-spec/*` — never overridden. Show your work: keep a live TODO, announce every
phase/node transition, print every gate result, and at each **HALT** stop and wait for the human.

**Before anything, enable observability** (`source` the env from `observability.md` §1) and keep
it on for the whole run. Config is **frozen**: you never edit `pyproject.toml`, `uv.lock`,
`.venv`, or `.claude/settings.json` mid-run — a needed config change is a HALT, not an auto-edit.

**Worker agents** (all in `.claude/agents/`, dispatched via the Agent tool):
`analyst` (intent/hidden-obligations), `coverage-auditor` + `criteria-auditor` (Tier-B critic —
coverage vs the spec), `planner-reviewer` (plan review),
`reviewer` (fresh read-only per-node verify — tools: Read/Grep/Glob/Bash, **no Edit/Write**).

---

## PHASE 0 — PREFLIGHT (env gate; run before Phase 1 and Phase 2, and after any interruption)
Run `bash build/preflight.sh`; it must exit 0 (asserts every env killer: iCloud venv, `db`
import, testmon, python version, fail-closed gates, config-freeze, observability env). A red
here is a real env fault — fix the environment, do **not** start a phase.

---

## PHASE 1 — PLAN (produce + prove the chain)

**1.1 Read (fresh readers, one per region).** Dispatch parallel read-only agents — one per spec
doc (00, 01, 02, 03, 04, 05, 08, 09) plus one for `call_external`/contracts/invariants. Each
reads its spec region **and the existing codebase** and returns: the obligations in its region
(`requirement_ids` + `criterion_ids` + `spec_refs`), the interfaces produced/consumed, and which
obligations already have implementing code (cite `file:line` as evidence). Use the `analyst`
agent for the intent/hidden-obligation pass. Readers return data, not prose.
- **The Doc-09 reader has one extra job:** enumerate every integration journey in
  `09-VERIFICATION.md §2/§3` into **`build/journeys.json`** as `{"journeys": ["J-09-...", ...]}`.
  This file is the oracle for Tier-A A3 and for `journeys_now_live` on every node — it MUST exist
  before 1.4. Journey ids here are the same ids nodes put in `journeys_now_live` and scenarios put
  in `journey_id`.

**1.1b Cross-spec synthesis + gap classification.** The comprehension's core job is to **bring
all specs together and analyze how they compose into one working product**, surfacing the gaps
that only appear at the seams. Expect gaps; classify each:
- **Technical / implementation gap** (spec says WHAT not HOW; two docs under-specify their
  interface): **resolve it yourself** with the approach best aligned to product intent and record
  it in `build/decisions.md` (append-only, reviewed at HALT 1). Do not bother the human.
- **Product-level gap** (a genuine product choice that changes user-visible behavior): **HALT and
  ask the human** a bounded question; encode the answer on the node + in `build/decisions.md`.
When unsure, treat it as product-level and ask. Every seam becomes an explicit wiring node so
Tier-A A2 can prove it routes together.

**1.2 Code-vs-spec audit → stamp dispositions.** For every obligation across all 8 docs, decide
its node's `disposition` from the readers' evidence: `verify` (code exists + looks right),
`fix` (exists + known defect), `rebuild` (exists + fundamentally wrong), `build-new` (absent).
No criteria are generated or sealed — the spec is the source of truth; 00–03 nodes reuse their
existing tests (`criterion_ids`), 04–09 nodes are judged against `spec_refs`.

**1.3 Synthesize `chain.json` (you, the lead, author it).** From the readers' reports, author the
full node set for all 8 docs against `chain.schema.json` — the sample node in `SPEC.md §2` is the
template. Rules: dependency-order it (every `depends_on` backward-only); **every node cites its
`spec_refs`** (00–03 also cite existing `criterion_ids`); stamp `disposition` (1.2) and set
`status: pending`; give every node its `codebase_anchors` (the files it owns — this feeds the
reverse-map); fill every field, especially `intent` + `definition_of_done` (cite + interpret the
spec, never re-copy it). Then **validate structurally** before 1.4: the file must parse and
satisfy `chain.schema.json`.

**1.4 Tier A — mechanical closure.** Run `python3 build/check_completeness.py`. It must exit 0:
order-validity (acyclic), requirement closure (existing tests covered + every node traceable),
wiring producer/consumer balance, Doc-09 journey closure, and **A4 reverse-map** (every existing
source file claimed by a node; the orphan list = dead code to remove/cover at HALT 1). Fix
`chain.json` until green — this is arithmetic; a red is a real gap.

**1.5 Tier B — semantic critic (fresh context).** Dispatch `coverage-auditor` + `criteria-auditor`
(fresh) to re-read all specs vs the chain and report: implicit obligations with no node, nodes
with no spec basis, untestable/weak `definition_of_done`, missing wiring nodes, and **any journey
in a node's `journeys_now_live` that has no real test in the suite** (else the verifier has no
oracle). Loop 1.3→1.5 until it reports zero.

**1.6 Tier D — scenario corpus.** Dispatch a generator to produce a diverse, spec-grounded
scenario set into `build/scenarios/` (normal journeys, edges, failure/negative, cross-feature;
deduped by behavior; each scenario's `journey_id` ∈ `build/journeys.json`). Trace each scenario
against the chain — a scenario is *served* iff every node it touches is present. **Plan-time
tracing proves *logical* coverage (no missing nodes), NOT runtime correctness** — runtime is
proven per-node in Phase 2 and whole-product in Phase 3. Any unserved scenario → gap → 1.3. Keep
the corpus; Phase 2/3 reuse it as the real-data suite.

**1.7 HALT 1 (human).** Present: `chain.json`, the Tier-A closure report, the Tier-B zero-gap
result, `decisions.md`, and scenario coverage. The human approves or corrects. Do not build first.

---

## PHASE 2 — BUILD (walk the chain, one node at a time)

Pick the next node whose `depends_on` are all `verified`. For each:

**2.1 PLAN.** Assemble the context packet from the node (`intent`, `definition_of_done`,
`spec_refs`, `acceptance`, `consumes`/`exposes`, `integration_point`, `codebase_anchors`,
`invariants`, `risks`). The builder reads the node → opens the cited spec sections → reads the
codebase → writes its plan. Have `planner-reviewer` (fresh) review it before any code.
*(If `disposition == verify`, skip 2.1–2.3; go straight to 2.4 — the fast verify.)*

**2.2 BUILD.** TDD: write the failing acceptance test on the **real path** → code to green. You may
CREATE a new test; you must never EDIT or delete a **sealed** test/cassette/golden/`_baseline.json`
— the fresh read-only verifier (2.4) flags any such edit in the diff.

**2.3 INTEGRATE.** Wire the node into the live product at `integration_point` — not a standalone
module; it must be reachable on the real product path.

**2.4 VERIFY (fresh context, read-only).** Dispatch `reviewer` (fresh; tools Read/Grep/Glob/Bash,
**no Edit/Write** — it grades from a context that never built the code). Give it ONLY the node's
`definition_of_done` + `acceptance`, and the cumulative `journeys_now_live` of all nodes ≤ this
one. It confirms, with evidence shown:
- (a) `bash build/gates/verify-node.sh <acceptance_cmd>` green on real/held-out data
  (verify-node.sh treats **exit 5 / no-tests-collected as FAIL**, closing the silent no-op hole),
- (b) the newly-live journeys green,
- (c) every previously-green journey still green (regression),
- (d) `invariants` intact (no law/invariant-violating path).

**2.5 ADVANCE.** Only if 2.4 fully passes: flip `status: verified`, commit (evidence — the
verify-node output — in the message), `/clear`, next node. Else: fix **product code, never the
test**; a sealed test that contradicts the spec is a human gate. After N identical failures, write
`BLOCKED:<node-id> <reason>`, continue with independent nodes, never deadlock.

Optional **HALT** at each region boundary (end of a doc's nodes).

---

## PHASE 3 — SIGN-OFF (whole product, real infra)

Every node `verified` ⇒ one whole-product pass: `bash build/gates/signoff.sh` (ruff + mypy
--strict + bandit + full offline suite, self-contained — no `slices/` or `done-check` coupling) +
the full `build/scenarios/` corpus + Doc-09 journeys on real infra + deepeval ≥ baseline. A
scenario that is logically covered but fails at runtime is a **product bug to fix**, not a
completeness gap — fix it and re-verify the affected node. Return in exactly one state:
**PRODUCTION-VERIFIED** (with the evidence table) or a specific **BLOCKED / SPEC_BLOCKED** list.
→ **HALT** (final).

---

## Standing rules
- Never fake DONE; flip `verified` only after the real path ran on real data with evidence shown.
- Human-gated (escalate, never auto-approve): bundle seal · `_baseline.json` ·
  `EXTRACTION_COUNT_HALT` · destructive/prod migration · prod deploy · genuine `SPEC_BLOCKED`.
- Env hygiene: never bare `uv sync` (use `uv sync --all-packages` + pinned tools); venv stays off
  iCloud; `-p no:testmon` is set globally; run pytest via `.venv/bin/python -m`, never `uv run`.

---
## PHASE 2 execution enhancements (efficiency · accuracy · no-gaps)
- **Wave-parallelism (safe only):** batch nodes whose `depends_on` are all `verified`. VERIFY-disposition nodes (read-only, no mutation) + build nodes with DISJOINT file sets run in parallel (container-isolated); nodes sharing an `integration_point` stay sequential. The 00–03 verify sweep is one wave.
- **Tiered verification:** routine node = 1 fresh read-only reviewer. HIGH-STAKES nodes (any `human_gated`; isolation-triad; accept-handler; tenant isolation; meeting-runtime-provisioner; cost-breaker; lethal-trifecta paths) get a 2nd ADVERSARIAL verifier that tries to REFUTE the pass — both must clear.
- **Real-data eval at build time:** `[eval]/[latency]` nodes run deepeval ≥ baseline on real/held-out data inside VERIFY — not deferred to Phase 3.
- **Verify the negatives + pinned contracts:** the reviewer checks each node's "NOT done if…" clauses AND conformance to `decisions.md` (D-013..D-029 shapes/conventions).
- **Region-boundary smoke:** after each doc's nodes verify, run the reachable journeys + scenarios on real infra; a regression blocks advancing to the next region.
- **Observability ON** for the whole run (`observability.md`); the anti-drift assertions fire live.
- **Prod-ready bar:** a node is done ONLY when its real path RAN on real data AND its output was INSPECTED (not just "tests green") AND the fresh reviewer(s) cleared it. Never advance on a flag alone.
- **No waste:** `BLOCKED` after N identical failures → diagnose → continue on independent nodes; no hard time budgets; no silent stalls.
