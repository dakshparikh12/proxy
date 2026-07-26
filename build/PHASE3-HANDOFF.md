# HANDOFF — Design Proxy's Phase 3 Testing & Optimization Strategy

> Paste this into a fresh Claude chat along with the 8 spec docs (`product/v0-spec/00..09`)
> and `CANONICAL-DECISIONS.md`. It gives full context on the product, what's built, what we've
> learned, and the infra/tooling available. Your job: design the exhaustive **Phase 3** —
> prove the product 100% works on real & messy data at scale, then optimize it, then certify it
> for customer use.

---

## YOUR TASK
Design Proxy's Phase 3 testing + optimization strategy in depth. Phase 3 is NOT for finding
correctness bugs (Phase 2 does that) — it's: **(a)** prove the WHOLE product works exhaustively
end-to-end on real/messy data at scale (thousands of simulated real meetings on real repos),
**(b)** optimize it to the best achievable (latency, cost, quality, robustness), **(c)** issue a
measured production-readiness certificate. Be concrete, tool-specific, measurable, and
exhaustive. Challenge our assumptions. Tell us what we're missing.

## WHAT PROXY IS
Proxy is an AI participant that joins a company's meetings **already knowing their codebase**.
In a meeting it: listens (real-time STT), maintains live notes, answers grounded codebase
questions ("who calls X", "where's Y", "what's the blast radius"), does real code work in an
isolated sandbox (edits/refactors → a staged draft a human approves), surfaces risks, catches
people up, and can be told "Proxy, quiet." Product + agent are both named "Proxy." The premise:
**Proxy is grounded in the real codebase and its outputs are trustworthy or explicitly silent.**

## ARCHITECTURE / STACK
- Python 3.12 · uv workspace monorepo (`services/*` + `libs/*`, src-layout) · ONE Cloud SQL
  **Postgres** + **GCS** (object-versioned) as the durable substrate · Alembic migrations.
- **8 spec docs / regions**: 00 foundation (substrate/contracts/hosting/CI) · 01 code-intelligence
  (connect→clone→index→graph→MCP query tools) · 02 voice-transport (Recall bot · AssemblyAI STT ·
  Cartesia TTS · turn-taking/barge-in) · 03 scribe (live meeting understanding → notes) ·
  04 orchestrator (the per-meeting brain: wake turns, dispatch, close) · 05 workroom (the
  sandboxed code agent in **E2B**, staged drafts) · 08 experience (connect page, meeting home,
  draft accept/reject, tile+chat surfaces) · 09 verification (integration journeys).
- Agent runtime: the **Claude Agent SDK** (`claude_agent_sdk`), tools exposed as **MCP servers**.
- The build was driven by a dependency-ordered **chain of 128 nodes** (`build/chain.json`), each
  built + adversarially verified, then real-data-gated.

## THE PRODUCT CONSTITUTION
**5 laws:** (1) Grounded or silent — cite file:line or say "not found by this method". (2) Never
overstate — exact results tagged resolved, search-derived tagged lower-bound, failures spoken
plainly. (3) Human control is absolute — every world-touching action is a staged draft behind a
human click; barge-in stops speech. (4) Dynamic, never hard-coded — situation→action lives in
model judgment; code owns only physics/pipes/substrate. (5) Talk-and-glance — operable by
speaking + glancing.
**6 invariants (each guard-enforced):** naming (no internal names in user-visible strings) ·
secrets only from Secret Manager · contracts-registry-closed · isolation triad (per-tenant
volume+process+index, never cross-tenant) · every external call via the single `call_external`
seam · tool handlers return errors never throw.

## WHAT'S CURRENTLY BUILT (state)
- **~108/128 nodes verified.** Regions COMPLETE: foundation, transport, scribe, orchestrator,
  workroom; experience ~16/17; code-intel 7/11 (4 close via the connect→index trigger); the
  15 Doc-09 integration journeys are Phase-3 material.
- Foundation certified on real DB (≈998 tests). Workroom proven on real infra: a real
  `claude-opus` agent edits real code in a **live E2B sandbox** → real staged draft (deepeval 1.0).

## THE CRITICAL LESSON (this must shape Phase 3)
Node-by-node unit + adversarial verification **passed on nodes that were product-broken.** Only
**real-data scenario testing** caught them. Real examples we found + fixed:
- **The provider seam**: `ProviderQuery` never carried `mcp_servers`; `permission_mode=default`
  auto-denied tools; `tools=[]` serialized to `--tools ""` which nuked the toolset. Net: *no
  agent, in any path, could use a single tool* — the product was non-functional behind green
  unit tests.
- **The code_intel mount**: the wake turn advertises `mcp__code_intel__*` tools but the server
  is mounted nowhere → **Proxy cannot answer a grounded codebase question in a real meeting**
  (it hallucinates/fabricates). The core premise, broken. (Being fixed now.)
- Placeholder/tautological oracles (the applier's exactly-once was never actually run); a P0
  cross-tenant cache-key bug; a hollow orchestrator loop (Proxy would never wake).
**Implication for Phase 3:** unit/adversarial/static analysis (CodeRabbit) are necessary but
NOT sufficient. Phase 3 must be **real-data-first, at scale, adversarial, and measured** — hollow
assemblies hide at the seams and only surface when the whole product runs for real.

## THE VERIFICATION MODEL WE'VE ADOPTED (build on this)
Three tiers: (1) per-node unit+adversarial (correct in isolation); (2) **per-capability
real-data battery** — deepeval-scored diverse scenarios on the REAL path (this is where the real
bugs surface); (3) Phase 3 = whole-product composed. See `build/PHASE2-VERIFICATION.md`
(checkpoint gates: code-intel red-test · transport STT/TTS latency · scribe messy-transcript
eval · orchestrator battery · workroom task+latency · experience Law-3) and
`build/VERIFICATION-PROGRAM.md` (the deeper program below).

## PHASE 2 PROGRAM (runs before Phase 3 — for your awareness)
Stage 0 integrity+spec-compliance audit (Vulture dead-code/over-engineering · bidirectional
spec↔code closure · mutmut mutation testing · Hypothesis property-fuzzing) → Stage 1 simulation
engine → Stage 2 multi-dim scoring → Stage 3 red-team → Stage 4 load/chaos → Stage 5 latency →
Stage 6 observability → Stage 7 certificate.

## INFRA + TOOLING AVAILABLE (all live + confirmed working)
- **E2B** live (real cloud sandboxes) · **Anthropic** live (real agent calls) · **deepeval**
  4.1.3 (GEval judge via Anthropic/OpenAI) · **Langfuse** (tracing, wired) · real Postgres + GCS.
- Funded accounts: GCS / AssemblyAI (STT) / Cartesia (TTS) / E2B on free credits; Anthropic
  modest; **Recall** (the meeting bot) limited $ — use sparingly. Real labeled meeting audio for
  WER is the one data gap (we generate a proxy corpus otherwise).
- 2026 best-in-class tooling landscape we researched (pick/justify for Phase 3): **deepeval**
  (+ its **ConversationSimulator** for multi-turn synthetic users, + **DeepTeam** red-team) ·
  **RAGAS** (grounded-answer metrics) · **promptfoo** · NVIDIA **garak** · Microsoft **PyRIT**
  (multi-turn crescendo) · **Giskard** (agent tool-call attacks) · **Locust** (Python load) ·
  **Vulture** (dead code) · **Hypothesis** (property) · **mutmut** (mutation) · OpenTelemetry.
  Research bar: **500–1,000+ simulated multi-turn meetings per release** for statistical
  confidence; agent **trajectory evaluation** (score every tool call/turn, not just the final
  answer); voice norms: barge-in <200ms, end-to-end turn <1,200ms p50.

## WHAT PHASE 3 NEEDS TO DESIGN (the questions for you)
1. **The meeting-simulation engine**: how do we source diverse real repos + LLM-generate
   realistic meeting transcripts/dialogue/personas + run thousands of composed end-to-end
   meetings on the real product path? What's the scenario matrix (repo × persona × intent × edge
   × fault)? How many for statistical confidence, and how do we avoid eval-gaming?
2. **Scoring**: trajectory eval + deepeval + RAGAS + per-scenario law/invariant compliance —
   what metrics, what thresholds, what's the aggregate pass bar?
3. **Adversarial**: which red-team tools for our threat model (transcript injection, tool-abuse,
   cross-tenant coercion, cost-exhaustion, jailbreak), multi-turn over a whole meeting?
4. **Load/chaos**: concurrency + fault-injection design; how do we prove tenant isolation holds
   under load + honest degradation under every vendor failure?
5. **Latency/cost optimization**: the measure→tune→re-measure loop; the SLO targets + method.
6. **The production-readiness certificate**: the exact matrix (laws/invariants/SLOs/capabilities
   × evidence × achieved metric) and the statistical bar to declare PRODUCTION-VERIFIED.
7. **What are we missing?** Testing types/tools/risks we haven't considered for THIS product.

## CONSTRAINTS
Never fake done — a node/capability is proven only when its real path RAN on real data with
evidence. The spec (`product/v0-spec/*` + `CANONICAL-DECISIONS.md`) is the source of truth.
Human-gated (never auto): prod deploy, prod/destructive migration, sealed-test edits, the
extraction-count halt. Don't over-engineer the test infra (best-in-class per dimension, not
every tool). Everything measured (baseline → target → achieved).
