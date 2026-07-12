# Proxy · RUNBOOK — how to actually run the build system
### One document. Follow it top to bottom to take a component from a doc to a merged, real-data-proven build. Written so either founder can run it.

---

## The mental model (read once)
You never "run the whole product." You run **one component at a time** through **one agentic loop**: plan → build → verify → iterate until done. The verify step has two rungs (cheap first):
- **Rung 1 — code check** (`verify.sh`, pytest): unit/practical tests, every [pytest] acceptance criterion. Fast; runs every pass.
- **Rung 2 — real-data eval** (`eval_runner.py`): runs the built component on **real repos** vs your **golden answer keys** + thresholds. Runs when rung 1 is green.

It is ONE loop. `runner.py` drives build+rung-1 every pass; when rung 1 is green it triggers rung 2 (the eval); if either rung fails, it reads the failure and keeps looping. **Done = both rungs green.** Rung 2 firing only after rung 1 is a cost choice (the eval is expensive), not a second loop. And the checker is never the builder — the test suite and eval graders decide "done," not the agent that wrote the code.

---

## STEP 0 — Prerequisites (once, per machine)
- A Unix machine (Mac/Linux) or cloud VM. Python 3.12, git, Docker (for the real Postgres in tests).
- Claude Code installed, logged in on a **Max** plan (continuous autonomous sessions need the higher tier).
- A GitHub repo for this build environment (private).
- API access for the model gateway (Anthropic key via the metered `llm.py`; keep it out of the repo).

## STEP 1 — Bootstrap the repo (once)
```bash
git clone <your-repo> proxy-build && cd proxy-build
bash harness/scripts/bootstrap.sh        # pinned venv + deps (pytest, ruff, mypy, mutmut, deepeval, etc.)
python3 harness/verify.sh --selftest     # confirm the arbiter runs and exits non-zero on an empty repo
```
`.claude/` (skills, agents, hooks) is already in the repo — Claude Code auto-loads it. Optionally:
```bash
specify init --integration claude-code                      # Spec Kit (optional; skip for build #1 to stay lean)
/plugin install superpowers@claude-plugins-official         # Superpowers (optional; skip for build #1)
```

## STEP 2 — Before you build a component: lock the doc + seed the golden dataset
A component is **runnable** only when three things are real:
1. **The doc is locked** — `components/<id>.md` (done for 1.1, 1.2).
2. **The golden dataset is seeded** — `fixtures/<id>/` has real repos + a human-authored answer key. **This is the step people skip and it's the whole product bar.** See STEP 5 for how to author it. Do this BEFORE building.
3. **The harness scripts exist** — `harness/runner.py`, `verify.sh`, `guard.py` (in this repo as scaffolds; replace with your hardened versions where you have them).

## STEP 3 — Run the build loop (the code)
```bash
python3 runner.py --component 1.1-ingest
```
What happens: `runner.py` spawns a **fresh Claude Code session each pass**, hands it `harness/prompts/pass_prompt.md`, lets it build one increment (TDD), then runs `verify.sh`. It loops — continue / stop / stall — until pytest is green or it hits a stop/stall/cost ceiling. Unattended. You wake up to either a green component or a stall report in `runner.log`.

Watch for: `verify.sh` exit 0 = a green pass. Stall (same failure 3–5×) = it's stuck; read the log, fix the doc or a fixture, re-run. Cost ceiling hit = it stopped safely.

## STEP 4 — Run the eval gate (the real test — this is the one that matters)
```bash
python3 eval_runner.py --component 1.1-ingest
```
What happens: it pulls the real repos from `fixtures/1.1/`, runs the built component on each, and scores every **Tier-2 [eval]** criterion against your golden answer keys with the graders + thresholds named in the doc. Deterministic graders where a check confirms it; a **calibrated** LLM-judge for fuzzy outcomes. It prints a per-criterion table: measured score vs threshold, per estate.

**Pass = every [eval] criterion clears its threshold on EVERY real estate (including the messy one).** That's "it actually works on real, complex data."

## STEP 5 — Author the golden dataset (do this in STEP 2, explained here)
This is the differentiator and the part only a human can do well. Per component:
1. **Pick 3–5 real, public repos** spanning the range: messy/multi-language, a single small repo, a big monorepo, a docs-heavy repo. Put them (or clone scripts) under `fixtures/<id>/estates/`.
2. **Author the expected answers** — the "senior-engineer" ground truth — in `fixtures/<id>/golden/`:
   - For 1.2 graph, e.g. `blast-radius.json`: 20 real "what breaks if X changes?" questions on those repos + the dependency set a senior engineer says is correct.
   - `referents.json`, `edges-sample.json` (validity labels), `unmapped.json`, `live-truth.json`.
3. **Keep it ~20–30 cases to start** — small and high-quality beats thousands of auto-generated. It's a **living dataset**: every real failure you later find, add back as a new golden case.
4. **Calibrate the LLM-judge** against these human answers before it's allowed to gate (run the judge on the golden set; confirm it agrees with the humans).

## STEP 6 — Iterate until accurate (the recursive loop)
```
build loop green → eval gate → any [eval] criterion below threshold on any estate?
   → write the failure report → re-run the build loop with FRESH context + that report
   → fix root cause → re-verify → re-run eval gate
   → repeat until every criterion clears threshold on every estate
```
Never weaken a criterion to pass. "Perfect results" = clears threshold on the messy/complex estates too, not just the clean one.

## STEP 7 — Merge (evidence + signoff)
```bash
# evidence auto-collected to evidence/<id>/ : verify tail, mutmut, review, eval-results
```
Both founders add a signoff line → open the PR → CI runs `verify.yml` (Tier-1) + the eval job (Tier-2) → branch protection (incl. admins) requires both green → merge. Next component.

---

## The acceptance-criteria ↔ testing relationship (your question, answered)
- **Tier-1 [pytest]** = code correctness. Deterministic, written once, run every pass by the build loop. "The code is right."
- **Tier-2 [eval]** = product accuracy on real data. A **stable property + threshold** in the doc, scored against a **living golden dataset** of real repos. "The graph is *actually correct* on real, messy systems." Run per section by the eval gate.
- **They are the same acceptance criteria, split by arbiter.** The doc's §8 lists both, tagged. The doc's §9 defines the eval. There is no gap between "the AC we wrote" and "how it's tested" — the AC names its own grader and threshold.
- **The criterion never changes day to day; the golden dataset grows.** You add real repos and real failures over time; the same criterion re-runs against more evidence. That's why it's not a moving target — you're proving the same "good," harder, over time.
- **Authored before building** (eval-driven): the golden dataset is written when the doc is locked, so "accurate on real data" is concrete from day one, and the build iterates toward a fixed, real target.

## Troubleshooting
- Loop stalls repeatedly on one milestone → the doc/AC for that milestone is ambiguous or a fixture is wrong. Fix the *spec*, not the code, then re-run.
- Eval passes on clean repos, fails on messy → that's the system working; iterate on the messy case. Don't ship until the messy estate clears.
- LLM-judge disagrees with your golden answers → recalibrate or replace the fuzzy criterion with a deterministic grader; a mis-calibrated judge causes false passes.
- Cost spiking → move per-pass subagent review to per-milestone; eval runs are expensive, keep them at the section cadence, not the inner loop.
