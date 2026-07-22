# Proxy verification system

A **permanent, reusable** pre-customer verification harness. It runs a doc's real
code through six layers — cheap/local first, expensive/networked last — and writes a
timestamped, never-overwritten report. It is not a one-off script: every layer reads
the shared doc registry, so the same command works on `doc00`, `doc01`, `doc02`, and
`doc03+` the moment they are registered.

```
./verification/run_full_verification.sh doc02            # layers 1–4 (+6 note)
./verification/run_full_verification.sh doc02 --redteam  # also Layer 5 (if eligible)
```

## Layout

```
verification/
  README.md                     ← you are here
  run_full_verification.sh      ← orchestrator: one doc arg, all layers, timestamped report
  pytest.ini                    ← self-contained rootdir for the property tests
  conftest.py                   ← bootstraps product imports + Hypothesis profile
  config/                       ← SHARED, reusable engine + definitions (not per-doc)
    docs.py                     ← the doc registry (add doc03 here, nothing else changes)
    pathsetup.py                ← product-import bootstrap (mirrors the sealed conftest)
    hypothesis_profiles.py      ← reusable adversarial strategies + settings profiles
    chaos_lib.py                ← reusable fault-injection primitives (kill/SIGSTOP/ephemeral PG)
    deepeval_support.py         ← Claude judge + the 4 reusable DeepEval metrics
    layer2_deepeval.py          ← spec→dataset generation + scoring runner
    pr_agent/                   ← Layer 3 self-host config (configuration.toml + run script)
  scenarios/<doc>/              ← per-doc, versioned-in-git test scenarios
    hypothesis_props.py         ← Layer 1 property tests (bound to real functions)
    deepeval_dataset.json       ← Layer 2 goldens generated FROM the spec (stable, committed)
    deepeval_results.json       ← Layer 2 last scoring result
  chaos/<doc>.py                ← Layer 4 fault-injection experiment, one per doc
  redteam/                      ← Layer 5 promptfoo config (flag-gated, not run by default)
  reports/                      ← permanent timestamped reports, one per run, never overwritten
  tools/                        ← ISOLATED uv project for heavy deps (DeepEval) — off the product lock
```

## The layers

| # | Layer | What it does | Cost | Default |
|---|-------|--------------|------|---------|
| 1 | **Hypothesis** | Property-based tests throwing weird strings, boundary numbers, malformed structures, empty/huge and path-traversal/injection inputs at each doc's **real** functions. | local | always |
| 2 | **DeepEval** | Generates grounded-QA goldens **from the actual spec file** (versioned per doc) + planted negative controls, then scores hallucination + citation-grounding G-Eval ("cited or silent"), judged by **Claude** (`claude-haiku-4-5`). | LLM | always* |
| 3 | **PR-Agent** | Self-hosted [`The-PR-Agent/pr-agent`](https://github.com/The-PR-Agent/pr-agent) code review, judged by **Claude via litellm**, reusing `ANTHROPIC_API_KEY` — no new vendor. | Docker + LLM | manual* |
| 4 | **Chaos** | Hand-written fault injection (chaostoolkit is stale — last real commit 2024-05): kill Postgres mid-write, SIGKILL a git clone mid-checkout, SIGSTOP a hung vendor call. | local | always |
| 5 | **Promptfoo** | Transcript prompt-injection red team — can a participant manipulate what Proxy says out loud. **Gated behind `--redteam`**, relevant once a doc handles live meeting input (doc03+). | LLM | **off** |
| 6 | **Confident AI** | Optional cloud dashboard for DeepEval. Needs an account/API key → documented as an optional follow-up, not wired. | cloud | note |

\* **External-limit history:** these layers were first built while the `.env`
`ANTHROPIC_API_KEY` had **exhausted credit** and Docker was **down**, so early runs were
recorded **BLOCKED** (never faked). After the key was funded and Docker started, all
three ran for real: **Layer 2** — citation-grounding (the specified "cited or silent"
G-Eval) **5/5 on all three docs**, negative controls **2/2 caught on all** (doc01's
combined score is 4/5 only because DeepEval's separate HallucinationMetric false-flags one
citation-1.0 case — both figures are reported, nothing dropped; a self-consistency guard
in generation ensures goldens are actually grounded); **Layer 3** — self-hosted PR-Agent
posted a real Claude review to PR #1 (no security concerns); **Layer 5** — 5/5
transcript-injection scenarios resisted (doc02). See the reports in `reports/`.

## What "cheap/local first" buys you

Layers 1 and 4 need nothing but the repo and run in seconds — they are where the real
defects were caught and fixed in this pass (see the curated reports in `reports/`).
Layers 2, 3, 5 cost money/tokens and only run when their preconditions are met.

## Reusing it for doc03 onward

1. Add one `Doc(...)` entry to `config/docs.py` (spec path, packages, test dir,
   `customer_facing`).
2. Write `scenarios/doc03/hypothesis_props.py` binding to doc03's real functions
   (reuse the strategies in `config/hypothesis_profiles.py`).
3. Write `chaos/doc03.py` (reuse `config/chaos_lib.py`).
4. `./verification/run_full_verification.sh doc03` — Layer 2 auto-generates the
   dataset from doc03's spec on first run; because doc03 is `customer_facing`, add
   `--redteam` to also run Layer 5.

Nothing else in the framework is per-doc hardcoded.

## Environment notes

- Property tests + chaos run under the product venv (`.venv`), which needs
  `hypothesis` (declared in the dev deps; `uv sync`).
- DeepEval runs under the isolated `verification/tools/.venv` (`cd verification/tools && uv sync`)
  so its large dependency tree never enters the product `uv.lock`.
- `ANTHROPIC_API_KEY` is read from the environment, else the repo/parent `.env`; it is
  never logged.
