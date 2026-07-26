# PROXY VERIFICATION & SIMULATION PROGRAM

The bar: **100% works on real and messy data, exhaustively simulated, fully measured, ready
for customer use.** This supersedes the shallow Phase-3 sketch. It runs as ordered stages: an
integrity + spec-compliance audit FIRST (nothing broken, nothing over-engineered, exactly
spec-compliant), then an industrial-scale real-data meeting-simulation program with best-in-
class tooling for every dimension, full observability, and a measured production certificate.

Tooling is chosen best-in-class per dimension (not "every tool" — overlap is waste). Where we
already have a tool it is named; new adoptions are marked **[ADOPT]**.

---

## STAGE 0 — INTEGRITY & SPEC-COMPLIANCE AUDIT (do this first)
*"Nothing broken, nothing over-engineered, exactly spec-compliant."*

- **0.1 Dead-code / over-engineering sweep** — **[ADOPT] Vulture** (AST, confidence 60–100%) +
  `deadcode` across `services/`+`libs/`: every unused function/class/var/import + unreachable
  path named. Cross-check with the chain's **A4 reverse-map** (files claimed by no node).
  Anything built the spec doesn't require → remove or justify. *This is the anti-over-engineering gate.*
- **0.2 Bidirectional spec↔code compliance** — fresh-context audit mapping EVERY requirement
  across all 8 docs + `CANONICAL-DECISIONS.md` → its implementing code → its test. Flag: (a)
  spec requirement with no implementation, (b) code with no spec basis (over-engineering), (c)
  implementation that DEVIATES from spec. Bidirectional closure (Tier A did structural; this is
  behavioral/semantic). Uses per-doc reader agents + the acceptance bundles.
- **0.3 Test-suite integrity (are the tests real?)** — **mutmut** (already a dep) mutation
  testing on the load-bearing modules (isolation/abort/cost/applier/dispatch/claim): a test
  suite that survives mutants is theater. Kill-rate threshold per module.
- **0.4 Invariant property tests** — **[ADOPT] Hypothesis** on the physics invariants (cost
  accrual = elapsed×rate, coalescer windowing/cap, cache-key tenant-isolation, exactly-once
  apply, ordered-close) to find edge cases deterministic tests miss.
- **0.5 Static/security floor** — ruff · mypy --strict · bandit · the 6 guards · **CodeRabbit**
  on the full diff. Full offline + integration tiers green.
- **Exit 0:** zero unjustified dead code · spec↔code closes both ways · mutation kill-rate ≥ bar
  · invariants hold under property fuzzing · static clean.

---

## STAGE 1 — THE SIMULATION ENGINE (immense real-data e2e)
*Source real repos; LLM-generate the meeting content; simulate insane numbers of end-to-end meetings.*

- **1.1 Real-repo corpus** — source **N diverse real GitHub repos** (small / large / multi-
  language / messy-monorepo / framework code) → clone + index them for REAL. These are the
  "actual repos" Proxy grounds on. Held-out set for eval-gaming resistance.
- **1.2 LLM-generated meeting content** — an **Evaluator-LLM** generates realistic meeting
  material per scenario: the transcript/dialogue, the asks to Proxy ("who calls X", "build Y",
  "catch me up", corrections, "Proxy, quiet"), across **personas** (senior eng / PM / confused
  / demanding / adversarial / non-technical). The talking + reactive parts are generated; the
  repo is real. *(This is the "source repos + LLM-generate the talking/reactive" you described.)*
- **1.3 Scenario matrix** — combinatorial `repo × persona × intent × edge-case × failure-mode`
  → thousands of distinct e2e meeting scenarios, both **broad** (random combos for coverage) and
  **doc-specific** (targeted batteries per doc's behaviors). Research bar: **500–1,000+
  simulated multi-turn meetings per release candidate** for statistical confidence + catching
  low-probability hallucinations.
- **1.4 Synthetic-user simulator** — **[ADOPT] deepeval `ConversationSimulator`** (+ LangWatch/
  Arklex-style patterns): an LLM plays the participants and reacts to Proxy's output MULTI-TURN
  (adaptive, not scripted), so Proxy is tested against realistic, messy, off-script interaction.
- **1.5 The e2e harness** — each scenario runs the FULL real product path: webhook → join →
  transcript → Scribe → wake → grounded answer / Workroom build → staged draft → accept →
  ordered close, on real infra (Anthropic · E2B · Postgres · GCS; Recall/AssemblyAI/Cartesia on
  the voice batteries). Gated `PROXY_SIM=1`.

---

## STAGE 2 — MULTI-DIMENSIONAL SCORING (measure everything)

- **2.1 Trajectory evaluation** (the 2026 agent-eval standard) — score the ENTIRE execution
  path: every tool call, reasoning step, turn — not just the final answer. **deepeval** agentic
  metrics + custom trajectory checks (right tool, grounded step, no wasted turns).
- **2.2 Answer/notes/edit quality** — **deepeval** GEval + Faithfulness + AnswerRelevancy +
  Hallucination per output.
- **2.3 Grounded-answer rigor** — **[ADOPT] RAGAS** on code-intel answers: faithfulness,
  context-precision, context-recall, answer-correctness (grounding is retrieval — score it like RAG).
- **2.4 Law/invariant compliance per scenario** — every scenario auto-checked against the 5
  laws + 6 invariants (grounded-or-silent, never-overstate, human-control, dynamic-not-hardcoded,
  talk-and-glance; isolation, secrets, contracts, isolation-triad, call_external, never-throw).

---

## STAGE 3 — ADVERSARIAL / RED-TEAM (the "red testing")
*Best-in-class LLM/agent red-team stack, multi-turn.*

- **[ADOPT] DeepTeam** (Confident AI — integrates with deepeval): 40+ vuln types mapped to the
  **OWASP Top-10 for LLM Apps**; linear/tree/**crescendo** jailbreaks.
- **[ADOPT] promptfoo red-team** (50+ vuln types, CI-native) + **[ADOPT] NVIDIA garak** (37+
  probes) + **[ADOPT] Microsoft PyRIT** (multi-turn crescendo / TAP) + **[ADOPT] Giskard**
  (agent tool-call attacks, GOAT multi-turn).
- Targets specific to us: transcript prompt-injection (already found once), tool-abuse (coerce
  the Workroom to push / exfiltrate), cross-tenant coercion, cost-exhaustion, guardrail
  jailbreak, data-leak — including **multi-turn crescendo across a whole meeting**.

---

## STAGE 4 — LOAD / CHAOS / RESILIENCE (scale + fault injection)

- **[ADOPT] Locust** (Python — fits the stack): N concurrent meetings against the real
  substrate; **noisy-neighbor** / multi-tenant isolation under contention.
- **Chaos / fault injection** during load: vendor 5xx/429/timeout, socket drop, killed sandbox,
  harness recycle, DB blip, quota-exceeded → **honest degradation**, isolation holds, no crash /
  no fabrication. Measure throughput · tail latency under load · isolation integrity · $/meeting at scale.

---

## STAGE 5 — LATENCY / PERFORMANCE OPTIMIZATION
*Research norms: 200ms turn-taking target; >1,200ms p50 end-to-end feels broken.*

- **Voice latency benchmarks** on real Recall/AssemblyAI/Cartesia (Coval-style, many runs):
  barge-in cut **<200ms** · ack-audible p95 **<500ms** · first-grounded-audio p95 **<5s** ·
  end-to-end turn **<1,200ms p50**. Cartesia TTFA + AssemblyAI WER on code-heavy audio.
- **Profile → tune → re-measure** the hot paths (STT→wake→answer, TTS/barge-in, task-completion,
  1-hr prompt-cache hit-rate, first token). Before→after tables; hit SLOs with margin.
- **Cost**: model-seat tuning + cache maximization → minimize **$/meeting** (cascaded stack
  norm $0.07–0.13/min — target within).

---

## STAGE 6 — FULL OBSERVABILITY (see + measure everything)

- **Langfuse** (already wired) + **OpenTelemetry**: every meeting / turn / tool-call / cost /
  latency / deepeval-score traced. A run **dashboard**: per-scenario pass/fail, score
  distributions, latency/cost histograms, the compliance matrix, red-team results. Production
  monitoring stays on Langfuse for the live tiers.

---

## STAGE 7 — PRODUCTION-READINESS CERTIFICATE
- The **matrix**: every one of the 5 laws + 6 invariants + each SLO (latency/cost) + each
  capability — one row, real evidence + achieved metric.
- **Statistical confidence**: ≥1,000 simulated meetings, pass-rate ≥ threshold, tail behavior
  bounded, red-team clean, isolation clean under load.
- Return **PRODUCTION-VERIFIED** (with the matrix + numbers) or a specific **BLOCKED** list.
  Then deploy is a founder decision, not a question of whether it works.

---

## TOOLING STACK (best-in-class per dimension)
| Dimension | Tool | Status |
|---|---|---|
| Unit / integration | pytest | have |
| Static / types / security | ruff · mypy --strict · bandit · CodeRabbit | have |
| Dead-code / over-engineering | **Vulture** + deadcode | ADOPT |
| Mutation (are tests real) | mutmut | have |
| Property-based fuzzing | **Hypothesis** | ADOPT |
| LLM output eval | deepeval | have |
| RAG/grounding eval | **RAGAS** | ADOPT |
| Multi-turn simulation | deepeval ConversationSimulator (+LangWatch patterns) | ADOPT |
| Red-team | **DeepTeam · promptfoo · garak · PyRIT · Giskard** | ADOPT |
| Load / concurrency | **Locust** | ADOPT |
| Chaos / fault-injection | in-process fault harness (Chaos-Mesh patterns) | ADOPT |
| Observability / tracing | Langfuse + OpenTelemetry | have |
| Voice latency | Coval-style benchmark harness | ADOPT |

Harness dirs: `tests/eval/` (deepeval+RAGAS) · `tests/sim/` (the engine + repo corpus + LLM
content gen) · `tests/redteam/` · `tests/load/` (Locust) · `tests/latency/` · `bench/` (profile).
All live tiers gated behind flags so the offline suite stays hermetic.
