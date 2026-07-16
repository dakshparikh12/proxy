# Acceptance-Criteria Generator — the system
### Input: one build spec (e.g. `Doc 01`). Output: an exhaustive, arbiter-tagged, validity-checked criteria set that IS the build loop's stop condition. The loop stops only when every criterion is green at pass^k on every real estate.
### This is the Phase-4 engine. It runs in FRESH CONTEXT (never the builder), so criteria are authored by a different mind than the one that will satisfy them.

## The principle (why this isn't "an LLM writes tests")
The LLM **proposes** the criteria surface; deterministic machinery **disposes**:
1. **EARS expansion** turns each capability claim into its full failure-sibling set (the exhaustiveness *structure*).
2. **Property-based testing** (Hypothesis / Schemathesis) turns enumerated cases into adversarial searches (the exhaustiveness *engine*).
3. **Mutation testing** (mutmut) proves each criterion can go red (the *validity* rule — a criterion that survives all mutants is invalid).
4. **Real-repo eval** (DeepEval scoring + a SWE-bench-style clone→build→grade harness + Langfuse latency) proves it works *in the world*, not just on fixtures.
A criterion is **admitted only if** it (a) passes on the reference-correct build AND (b) is killed by ≥1 targeted mutant. This is the "Assured" rule (Meta TestGen-LLM): never trust an unexecuted, unfalsifiable criterion.

## Step 1 — Extract capability claims from the spec
For each §3 component, pull every **exact-behavior / produces / consumes** statement, plus every promise in §2 and every rule in §4. Each becomes a *capability claim* traced to its clause (`§3.7`). Ground every generated criterion to its source clause — a criterion with no clause is hallucinated; a clause with no criterion is a coverage gap.

## Step 2 — Expand each claim across the EARS dimensions
For every capability claim, force-generate its siblings (skip a dimension only if the spec makes it structurally impossible, and say so):

| Dimension | The question it answers | EARS form |
|---|---|---|
| **Capability** | Does the happy path produce the correct, cited result? | WHEN <trigger> THE SYSTEM SHALL <response> |
| **Contract** | Does every output validate the declared schema/envelope? | THE SYSTEM SHALL always <emit schema> |
| **Concurrency/order** | Worst interleaving — race, atomic swap, duplicate/out-of-order event? | WHILE <in-flight> WHEN <event> SHALL <consistency> |
| **Adversarial** (per invariant touched) | Injection, cross-tenant, secret leak, token in log, malformed/huge/binary input, force-push? | IF <hostile> THEN SHALL <contain> |
| **Degradation/fallback** | Dependency hangs/crashes → labeled fallback, never hang or lie? | IF <dep fails> THEN SHALL <fallback + tag> |
| **Measurement** | Latency percentile / cost under a pinned protocol? | THE SYSTEM SHALL <pXX ≤ N under protocol> |
| **Negative-result honesty** | Not-found / lower-bound / external-unresolved shapes correct? | WHEN <nothing found> SHALL <bounded, never "doesn't exist"> |

## Step 3 — Arbiter-tag and assign a grader
Every criterion gets exactly one arbiter and one grader:

- `[unit-fixture]` — fast, on a tiny committed fixture repo, **every pass** (rung 1). Grader: deterministic pytest.
- `[unit-property]` — Hypothesis property (races via `RuleBasedStateMachine`, partitions, round-trips). Rung 1.
- `[contract]` — output validates a declared schema (Schemathesis / JSON-Schema-driven Hypothesis). Rung 1.
- `[eval-realrepo]` — clone a **real pinned repo**, build the component, grade against a golden key at a threshold, **section gate** (rung 2). Grader: deterministic where possible, else calibrated DeepEval G-Eval/DAG judge.
- `[adversarial]` — the attack must be contained; zero invariant violations. Rung 1 or 2 per cost.
- `[latency]` — measured under the §M protocol (N samples, warm, pXX, Langfuse). Rung 2.

## Step 4 — Attach the two-stage validity mutant
Every criterion names the **deliberate breakage that must turn it red** (its mutmut target or a hand-authored broken build). If you cannot name one, the criterion is untestable → rewrite it. This is enforced: a per-section negative build must be caught by its graders, else the gate fails regardless of the green.

## Step 5 — Bind to the real-repo matrix and the reliability gates
- **Estate matrix** (author once per spec, pin SHAs): `single` (small clean) · `monorepo` (≥1M LOC scale) · `polyglot` (multi-language) · `deep-history` · `many-tiny-files` · `no-codeowners` · `unsupported-language` · plus controlled forks: `secrets` (planted), `live` (mutate post-clone), `perms`/`tenant` (isolation). Every `[eval-realrepo]` criterion names its estate.
- **pass^k** — each eval case runs k≈3–5×; **all** must be green (flakiness fails the gate).
- **Judge calibration** — any LLM-judge is gated on Cohen's κ ≥ 0.6 (≥0.8 strong) vs a human gold set; prefer deterministic/DAG graders.
- **Grader-validity** — the negative build (Step 4) must be caught 100%.

## Output format (one block per criterion)
```
### <ID> · <name>
- Traces: §<clause>          - Arbiter: [tag]        - Estate: <fixture|estate-X>
- GIVEN <state> WHEN <trigger> THEN <observable outcome>
- Grader: <deterministic | Hypothesis | schema | DeepEval G-Eval/DAG (κ-gated) | Langfuse pXX>
- Threshold: <number / pass^k / recall floor>
- Validity mutant (must go red): <the deliberate breakage>
```

## Stop condition (what "fully working" means)
The build loop stops **only** when: every `[unit-*]`/`[contract]`/`[adversarial]` criterion is green in `verify.sh`, AND every `[eval-realrepo]`/`[latency]` criterion clears its threshold at pass^k on **every** estate (including the messy/monorepo ones), AND every criterion's validity mutant is caught, AND every LLM-judge clears its κ floor. Green fixtures alone are never done.
