# Autonomous Multi-Doc Build — Orchestration Spec
### One continuous run that builds the whole product, doc by doc (00→09), each to 100% of an independently-sealed, product-level acceptance bundle, verified by a fresh-context agent that did NOT build it. Composes the pieces already in this repo; adds the conductor + the independent-verification gate.

## The non-negotiable idea
The intelligence that **builds** a doc is never the authority that decides it is **done**. Three separate authorities per doc, each in fresh context, each unable to edit the others' artifacts:
1. **Criteria author** — reads the spec, emits product-level acceptance criteria + evidence, sealed & hashed.
2. **Builder** — read-only against the sealed bundle; writes only `services/*` + `libs/*`; cannot touch tests/criteria/goldens/harness.
3. **Independent verifier** — fresh context, never saw the build; adversarially tries to REFUTE "done" against the sealed criteria + the real code + the git log. Its refutation, not the builder's claim, gates advancement.

A green doc = *the independent verifier could not refute it against an independently-sealed bundle, and `verify.sh` exit 0 + the git log confirm it.* Never the builder's word.

## The per-doc pipeline (runs for hours, unattended, until the verifier can't refute)
```
for doc in [00, 01, 02, 03, 04, 05, 08, 09]:            # SPINE-REGISTER order; sequential by dependency
  PHASE 1  GENERATE      fresh session runs GENERATOR.md Phases A–E on product/v0-spec/<doc> →
                         product-level acceptance criteria (qualitative: how the product must BEHAVE,
                         not implementation). Emits acceptance/<doc>/ bundle (requirements, criteria,
                         faults, estates, protocols).
  PHASE 2  ADVERSARIAL   fresh session (spec-compliance-review skill, separate authority) attacks the
           CRITERIA      bundle: omitted product behaviors? weak/circular oracles? criteria that a wrong
           REVIEW        build could satisfy? vision-not-captured? → gaps fed back to PHASE 1, re-run
                         until the reviewer finds no gap.
  PHASE 3  EVIDENCE      author the tests + fixtures + simulation workflows that make each criterion
                         checkable; DERIVE goldens mechanically (derive_goldens.py — independent
                         toolchain, no shared-bug blindness). Simulations replace real-data cost.
  PHASE 4  SEAL          RTM COVERAGE GATE (criteria_coverage_gate.py) must exit 0 — every requirement
                         covered by >=1 criterion, every criterion traces to a real requirement, no
                         authorityless criteria — else the bundle CANNOT seal. Then hash bundle + evidence
                         (manifest.yaml). From here the builder is read-only; changing any of it = new
                         version, prior evidence void. [The one checkpoint that stays human-or-independent
                         -agent — see "The seal".]
  PHASE 5  PLAN          fresh session drafts the implementation plan from spec+criteria; planner-reviewer
                         (separate authority) critiques it as a skeptical staff engineer; lock the plan.
  PHASE 6  BUILD LOOP    runner.py <doc>: fresh session per pass, subagent-driven execution
                         (skills/subagent-driven-build.md), TDD toward the next sealed criterion;
                         verify.sh (ruff/mypy/bandit + pytest) is the ONLY green signal per pass;
                         integrity hash re-checked after every pass (protected-tree change → hard exit);
                         commit per increment. Loops until rung-1 green.
  PHASE 6.5 MUTATION     mutmut on the changed services/*+libs/* modules: NO surviving mutants on code
           GATE          backing any blocking criterion (deterministic "do the tests have teeth?").
                         A survivor → REFUTED → back to Phase 6. Automates half of Phase 7's teeth-check.
  PHASE 7  INDEPENDENT   ⭐ fresh session that has NOT seen the build. Reads the sealed criteria + the
           VERIFICATION  full diff + git log + the test suite, RE-RUNS the tests itself, and for EVERY
           GATE          blocking criterion tries to REFUTE it (see below). Any refutation → back to
                         PHASE 6 with the refutation report. Zero refutations → doc is done.
  PHASE 8  ADVANCE       record evidence to evidence/<doc>/; git tag; next doc.
```
Between docs there is no shared context — each phase is a fresh session reading only committed artifacts, so no agent can "remember" a shortcut it took.

## Phase 1 — product-level criteria (what "very precise, qualitative" means)
The criteria describe **observable product behavior**, traced to the spec, at the altitude of *how it must work for a user* — e.g. for Doc 01: "GIVEN a real repo, WHEN asked 'who writes the orders table', THEN every writer site is returned cited to file:line, and an unsupported-ORM stack is labeled `lower-bound`, never a silent exact." Not "the function returns a list." GENERATOR.md Phases A–E already enforce: atomic requirements, authority for every criterion, deterministic-oracle-preferred, risk-weighted depth, negative-result honesty. Phase 2's adversarial review is what forces them to actually capture the *vision*, not just the letter.

## Phase 7 — the independent verification gate (the anti-falsification centerpiece)
This is the piece the whole run hinges on. `orchestrator/verify_doc.md` is its prompt. It runs in a **fresh Claude Code session with read-only tools** and is told: *you did NOT write this code; your job is to REFUTE that it is done.* For every blocking criterion it must answer, with evidence:

1. **Is it really implemented?** Point to the real `services/*`/`libs/*` code that satisfies it — not a stub, hardcoded return, or `pass`. A criterion "satisfied" by a function that returns the expected constant is REFUTED.
2. **Does the passing test actually exercise it?** Re-run the test. Reject tautologies (asserts nothing), over-mocking (the thing under test is mocked away), `skip`/`xfail`, and tests that would pass against an empty implementation. A green test that proves nothing = REFUTED.
3. **Does the behavior hold in simulation?** Run the doc's workflow simulations end-to-end; the criterion's behavior must appear in a real execution trace, not only in a unit assertion.
4. **Is the evidence real?** Cross-check the builder's commit messages against the git diff — claimed work absent from the diff = REFUTED. (The loop's rule: *the agent's word is never evidence; verify.sh exit 0 and the git log are.*)

Output: a per-criterion table `SATISFIED | REFUTED` each with file:line + test-run evidence. **One REFUTED = the doc is not done**, the report goes back to Phase 6, and the build loop continues. The verifier cannot edit code, tests, or criteria — it only judges. Because it is a separate session against the sealed bundle, the builder cannot pre-satisfy it or see its reasoning.

## How simulations give near-100% at zero API cost
The user's bar — "exactly as envisioned" — is met without paid real-data by three cost-free evidence layers, all deterministic:
- **Mechanical properties** (most criteria): synthetic fixtures + assertions (token-never-logged, zero-LLM-in-build, exclusions-absent, full-rebuild ordering, redaction, no-repo-exec). Rigorous, free.
- **Correctness properties**: goldens **derived from real *public* repos** by `derive_goldens.py` — real data, but $0 (public code + parsers, no models). This is real-data accuracy without real-data cost.
- **Workflow simulations** (≥10 per doc): full end-to-end pipeline runs through the fixtures asserting behavioral *chains* (connect→clone→exclude→graph→tool-answer→push→rebuild→pin→uninstall), verified in Phase 7 against a real execution trace. Behavior proven, not just units.
Model-calling docs (02–05) use recorded-transcript **replay** + FakeTransport for the same effect; the only genuinely-real-data step (live meetings) is deferred and is NOT on the critical path to "the code is correct."

## What's reused vs. new
- **Reused, unchanged:** `runner.py` (per-doc loop), `harness/{guard,verify.sh,preflight,stop_verify}`, `criteria/GENERATOR.md` (Phases A–E), `tools/derive_goldens.py`, `.claude/agents/{verifier,planner-reviewer}`, `.claude/skills/{spec-compliance-review,proxy-component-build}`.
- **New (this dir):** `orchestrate.py` (the conductor: the for-doc loop + phase sequencing), `verify_doc.md` (Phase-7 independent-verifier prompt), and a per-doc parameterized pass-prompt (today's `harness/prompts/pass_prompt.md` is hardcoded to doc01 — must be parameterized, a harness change for Pranav).
- **Still to author per doc (the evidence layer):** tests + fixtures + simulations + derived goldens. Doc 01's is the first and is partially built (tests exist; fixtures/goldens missing).

## The honest ceiling (stated, because hiding it would be the falsification we're preventing)
This system provably delivers: *every product-level criterion, independently reviewed and sealed, satisfied by real code and proven in simulation, confirmed by a fresh-context adversary that could not refute it.* That is the **90–95% of the full product** you named. The residual 5–10% is exactly what simulation cannot reach and is deferred by design: live third-party behavior (real Recall meetings, real STT/TTS timing, real Opus workroom builds under production load). Those need the paid real-data rung, later. No autonomous run can close that gap for free, and claiming otherwise would be the dishonesty this design exists to prevent.

## The seal (the one non-automatable-away checkpoint)
Between Phase 3 and Phase 6, the bundle is sealed so the builder can't weaken its own bar. The seal can be performed by the Phase-2 adversarial-review agent (mostly automated) — but a run that lets the *builder* generate, seal, and grade its own criteria has collapsed maker≠checker and its "green" means nothing. So Phase 2's independent review is mandatory; a founder glance at the sealed criteria per doc (minutes) is the recommended belt-and-suspenders. Everything else runs unattended for hours.

## Failure behavior (unattended-safe)
- **SPEC_BLOCKED** (a criterion contradicts the spec/a law, or is untestable): the loop STOPS that doc and logs it — it never guesses or weakens a criterion. Other docs do not start (sequential dependency), so the run halts with a clear report rather than building on a bad foundation.
- **Stall** (same failure 4×), **wall-clock** (9h), **max-passes** (40), **integrity violation** (protected tree changed): hard stop, per runner.py's existing caps.
- **Verifier REFUTED after N build cycles on the same criterion**: escalate as a likely spec ambiguity → SPEC_BLOCKED, not an infinite loop.
