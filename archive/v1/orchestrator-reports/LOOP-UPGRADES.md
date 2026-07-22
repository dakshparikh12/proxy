# Loop Upgrades — making the acceptance criteria + verification bulletproof
### Four additions that take the autonomous loop from "very good" to "as close to trusted-perfect as free/simulated evidence allows." Built on what Pranav already made; each says what's DONE, what needs promotion into a protected tree (supervised — the guard blocks the builder from doing it), and why.

## 1. RTM coverage gate — DONE + PASSING (deterministic proof of full spec coverage)
**File:** `orchestrator/criteria_coverage_gate.py` (dependency-free, runnable now).
**What it guarantees:** bidirectional requirement↔criterion completeness — every requirement covered by ≥1 criterion, every criterion traces to a real requirement, no authorityless (scope-creep) criteria. A gap exits nonzero → the bundle may not seal. This turns "did the criteria cover the whole spec?" into a decidable check, not an agent's hope.
**Proven:** `python3 orchestrator/criteria_coverage_gate.py doc01` → 50/50 covered (10 P0, 36 P1, 4 P2), 0 dangling, 0 authorityless. **GATE PASS.**
**Wire-in:** run it in ORCHESTRATION **Phase 4 (seal)** — the bundle cannot seal unless this exits 0. (Add to `harness/preflight.sh` alongside the existing coverage check — a supervised harness edit.)
**Honest limit:** this proves requirement↔criterion completeness. It does NOT prove *requirement↔spec* completeness (that every spec behavior became a requirement) — that requires prose judgment and is Phase 2's adversarial reviewer's job. The two together (deterministic RTM + adversarial spec-gap hunt) are the completeness argument.

## 2. EARS atomization — ALREADY PRESENT; enforce it
Pranav's requirements already use EARS (`WHEN <trigger> THE SYSTEM SHALL <response>`, with `source_location` + `source_quote` traceability). This is exactly the "this should happen, this should happen" product-level shape. **The one addition:** an EARS-lint in the generator's Phase A — reject any `normalized_statement` that isn't one of the five EARS templates (ubiquitous / event-driven / state-driven / unwanted-behavior / optional-feature) and isn't atomic (one trigger, one response). This kills vague compound criteria at authoring time. (A ~30-line linter, run in Phase 1; promote into `criteria/GENERATOR.md` Phase A — supervised.)

## 3. Mutation-testing gate — mechanize "do the tests have teeth?"
**Why:** the fresh-context verifier judges whether a passing test actually proves anything; mutation testing proves it *deterministically*. Inject deliberate bugs into the built `services/*`/`libs/*`; a test that stays green on a mutant has no teeth → that criterion's evidence is worthless.
**Wire-in:** after **Phase 6 rung-1 green**, run `mutmut run` on the changed modules; require **no surviving mutants** on the code backing any blocking criterion. A survivor = REFUTED, back to Phase 6. This is the automated half of Phase 7's teeth-check; the agent verifier catches the semantic gaps mutation can't (tautologies, wrong behavior that still passes mutants).
**Dependency:** `mutmut` (BSD, dev-only) — add to the bootstrap deps (supervised; note the guard blocks `pip install`, so it goes in `harness/scripts/bootstrap.sh` and runs there, not ad-hoc).

## 4. Planning + execution discipline — Superpowers *patterns* as Claude Skills (not the plugin)
**Why not install the plugin:** it ships session hooks + opt-out telemetry that fight this default-deny, sealed-arbiter loop. Borrow the discipline, not the dependency.
**Files (ready to promote to `.claude/skills/` — supervised):** `orchestrator/skills/writing-plans.md` (Phase 5 plan quality) and `orchestrator/skills/subagent-driven-build.md` (Phase 6 execution: decompose → parallel independent subagents → integrate, each verified). These harden the two phases where the build actually happens, and compose with the existing `planner-reviewer` agent + `proxy-component-build` skill.

## The verification stack after these upgrades (why we can trust "done")
A doc is DONE only when ALL of these independent signals agree — no single one is trusted, and none is the builder's word:
1. **RTM gate** (deterministic) — criteria fully cover the spec's requirements.
2. **EARS + adversarial criteria review** — criteria are atomic, product-level, and capture the vision (no missed behavior).
3. **`verify.sh` exit 0** (deterministic) — ruff/mypy/bandit + the pre-authored tests pass.
4. **Mutation gate** (deterministic) — the tests have teeth; no surviving mutants on blocking-criterion code.
5. **Workflow simulations** — the behavior appears in real end-to-end execution traces, not just units.
6. **Fresh-context verifier** (independent agent) — re-runs everything and tries to REFUTE each criterion; cross-checks commit claims against the git diff; one refutation = not done.
7. **Integrity hash** (deterministic) — the sealed criteria/tests/goldens were never weakened mid-build.

Falsification would require fooling a deterministic coverage proof, a deterministic mutation proof, the pre-authored tests, real execution traces, AND a fresh adversary — simultaneously, without changing the sealed bundle (hash-checked). That's the trust floor.

## Promotion checklist (the supervised step — with Pranav, since it's his arbiter)
- [ ] `criteria_coverage_gate.py` → called in `harness/preflight.sh` + ORCHESTRATION Phase 4 seal.
- [ ] EARS-lint → `criteria/GENERATOR.md` Phase A.
- [ ] `mutmut` → `harness/scripts/bootstrap.sh` + a mutation step in `harness/verify.sh` (or a Phase-6.5 gate).
- [ ] `orchestrator/skills/*` → `.claude/skills/`.
- [ ] `orchestrator/verify_doc.md` verifier → wired as ORCHESTRATION Phase 7 (already specced).
None of these can be done by the build loop itself (guard-protected) — that's the point: the arbiter is strengthened by humans/independent agents, never by the maker.
