# Verification-Ladder Redesign — Final Report

Date: 2026-07-20 · Scope: acceptance bundle + verification pipeline structural redesign (Tasks 1–10).
Motivation: tonight's discovery that a vendor boundary could sit unverified behind green tests, and that
no check verified its own extraction denominator or vendor failure paths.

---

## 0. What actually happened to the motivating example (grounded correction)

`libs/voice/backend.py` does not exist. doc02 IS "Voice & Transport"; its built package is
`services/transport/` (3,727 lines, 17 modules) — a fresh reality-check found it is **genuinely real,
not hollow-behind-mocks** (zero `Mock`/`MagicMock` in the 185 doc02 tests). BUT the founder's instinct
pointed at a real gap: by deliberate design the transport package holds **no vendor SDK** — vendor
calls are request-description dicts driven through an injected `call_external` seam that every test
stubs. So the transport *logic* is real and tested, but **nothing proved the request shapes it emits
are accepted by real Recall/AssemblyAI/Cartesia, or that real responses parse**. That unverified-seam
gap is exactly what the redesign now catches structurally. (Founder decision: the reality tier verifies
the seam via a recorded cassette, not an in-package SDK — matching the real architecture.)

---

## 1. Is the verification ladder fully implemented and wired for future docs? — YES.

Every criterion in a bundle now carries an explicit `verification_ladder` derived from a
`dependency_class` + `mock_boundary` (GENERATOR.md §8.4). Rungs:

| tier | present when | agent? |
|---|---|---|
| lint | always (real import/seam/class names) | no — subprocess |
| unit | always | no — subprocess |
| integration | local real dep (db:/fs:/gcs:) | no — subprocess |
| reality | `vendor:*` — seam driven vs a recorded cassette | fresh-context critic |
| negative | any non-null class — the paired `AC-…-NEG` criterion | fresh-context critic |
| e2e | golden-path only (a handful/doc) | cassette replay |

Wired into `orchestrate.py`:
- **P4 seal**: `ladder_schema_gate.py` (mechanical, no agent) fails the seal unless every criterion has
  a well-formed ladder, `mock_boundary` iff non-null, rungs match the §8.4 table, every non-null class
  has its `-NEG` pair, `e2e` is golden-only, and `dependency_manifest.yaml` is consistent.
- **P4.5**: `extraction_count_gate.py` (opus) — independent spec recount vs bundle requirement count;
  HALTS on >10% disagreement (does NOT auto-regenerate).
- **P7**: `ladder_runner.py` replaces the ad-hoc whole-doc refute — mechanical tiers fan out in
  PARALLEL (zero agent), reality/negative run per-criterion via `ladder_critics.py` (reason-first,
  two-consecutive-clean from two fresh agents, 4-round cap → founder halt), e2e replays cassettes.
  On failure it re-runs ONLY the failed tier, never re-climbs. One verification system, not two —
  the old auto-closing completeness sweep is retired (its job is now the P4.5 count gate + the P7 critics).
- Model tiering is CONFIG: `model-routing.json` (task-type → tier) read by `model_routing.py`. Cassette
  machinery: vcrpy + pytest-recording, secret-filtered, with a one-time recording script + honest docs.

For doc03–09: generation emits the ladder from the start (gen_criteria.md + FORMAT-TEMPLATE.yaml updated;
the schema gate blocks generation-cycle break AND seal until the ladder is well-formed).

---

## 2. Did retroactive application to doc00/01/02 find anything new? — YES, and it is surfaced (not auto-fixed by design).

Ran every ladder gate that is provable without cassettes against all three sealed docs:

- **Mechanical tiers: real-green.** lint (ruff + mypy --strict clean, 164 source files) + unit
  (doc00 85 passed, doc01 12, doc02 174).
- **Schema gate: all three lack ladders** (doc00 157 criteria / doc01 91 / doc02 164, 0 laddered) — the
  expected migration gap, not a defect. The `ladder_augment.py` applier closes it: proven end-to-end on
  the full doc02 bundle → schema-gate PASS (171 criteria, all laddered, 7 NEG pairs, manifest consistent).
- **Extraction-count gate: the marquee finding — all three HALT (>10%).** Real fresh-context opus
  recount, spec-only:

  | doc | bundle reqs | independent recount | disagreement |
  |---|---|---|---|
  | doc00 | 144 | 211 | +47% |
  | doc01 | 63 | 71 | +13% |
  | doc02 | 171 | 114 | −33% |

  Per Task-5 design this HALTS for **founder review** — it does NOT auto-resolve by regenerating,
  because a human must judge whether the gap is grain or genuine. The auditors' own analysis:
  disagreement is **granularity-dominated** (bundles decompose to a finer per-criterion grain than
  "distinct testable rules"). Spot-check pointers recorded in `evidence/<doc>-extraction-count.md`:
  doc00 §5 durable-ops substrate + §15 invariants (possible under-extraction); doc02 platform×channel
  (possible over-extraction). **This is the genuine "are the docs complete under the stricter standard?"
  question — it needs founder adjudication before the bundles are re-sealed.**

Root-cause discipline: the extraction disagreement is NOT a bug to silently patch — the correct action
(and the founder's own Task-5 design) is to HALT and surface it, which is done. The sealed bundles were
NOT mutated this session: full augmentation + re-seal is one founder-gated finish-line pass, best batched
with cassette recording (augmenting adds reality/negative rungs only cassettes turn green).

---

## 3. Real evidence it runs end-to-end without hanging (command output, this machine, 2026-07-20 17:03–17:06)

- collection: `459 tests collected in 0.08s`, 0 errors.
- `harness/verify.sh` (ruff + mypy --strict + bandit + full pytest across doc00/01/02 + reality):
  **`458 passed, 1 xfailed, 44 warnings in 37.71s` · `ALL GREEN`**.
- `ladder_runner.py doc02` (sealed): `NOT LADDER-READY (164 criteria missing ladders)` in 0.07s — honestly
  detects the migration gap.
- `ladder_runner.py doc02aug --tests-doc doc02` (augmented vs REAL doc02 suite): `359 rungs — green=342,
  red=0, pending-cassette=17 (reality×7, negative×7, e2e×3)` in 0.75s. Whole ladder climbs; mechanical
  green; cassette tiers honestly pending; no hang.
- `preflight.sh`: two EXPECTED-artifact FAILs — (1) "verify.sh must exit nonzero (arbiter red before
  build)" fails because doc02 is BUILT GREEN (preflight is a pre-build checklist); (2) git-dirty was the
  ladder-runner's derived run-state, now gitignored. All other checks PASS.

Root cause of the one earlier 3-min timeout (diagnosed, not band-aided): a diagnostic loop invoked pytest
3× and conftest's `_ensure_local_postgres()` provisions Postgres once **per invocation**. A single
verify.sh invocation provisions once and runs the whole suite green in 37.7s — the suite does not hang.

---

## 4. Token/cost estimate for the full ladder on a doc02-size doc

Measured this session: each extraction-count audit consumed ~30–54k tokens (opus) ≈ **$0.40/doc**.
Mechanical tiers = **$0** (pure subprocess — the Task-6 discipline, enforced by a static test).

Modeled full (cassetted) ladder for a **vendor-heavy** doc02-size doc:
- Extraction-count gate: ~$0.40 (1 opus, measured).
- **Reality + negative critics (dominant):** ~90 vendor-dependent criteria + ~90 `-NEG` pairs ≈ 180
  criteria × ~2.5 rounds (2 min consecutive-clean, 4 cap) ≈ **~450 sonnet invocations** × ~17k tokens
  (~13k in incl. code reads + ~4k out; the ~8k stable prefix caches after the first via Task 6) ≈
  ~7.6M tokens ≈ **~$40–50**.
- e2e golden-path (3–7): cassette replay = mechanical ≈ $0. One-time recording = human + vendor free tier.

**Bottom line:** ~**$40–50 per vendor-heavy doc** with cassettes present, dominated by the reality/negative
critics; **~$0.40/doc in this session's cassette-pending mode** (critics short-circuit to `pending-cassette`).
doc03–09 scale with vendor-criterion count: vendor-light docs (doc04 orchestrator, doc05 workroom) →
single-digit dollars; vendor-heavy (doc03, doc08) → mid-range. doc02 sits near the top (three vendors).
The ± is irreducible: the exact critic count depends on the per-doc judgment map.

---

## 5. The one honest blocker

Reality/negative/e2e cannot be turned green by an agent — they require real vendor traffic to record
cassettes (Recall.ai / AssemblyAI / Cartesia). None exist; no vendor SDK is installed; I have no
credentials and will not fabricate a cassette. All machinery is built (vcrpy config with secret filters,
`scripts/record_cassettes.sh`, honest free-vs-paid docs, hygiene backstop). AssemblyAI + Cartesia are
recordable within free tiers; Recall.ai generally consumes trial/billable bot-minutes. Recording must be
run by a founder; until then those rungs report `pending-cassette` (visible, not green, not skipped).
