---
name: proxy-component-build
description: Use when building any Proxy component from its /components/<id>.md doc. Enforces the full loop-engineering process — brainstorm, plan (reviewed), TDD build, dual-arbiter verification — and refuses to declare done until the real-data eval passes. Invoke explicitly at the start of a component build.
---
# Building a Proxy component (the loop)

You are building ONE component defined by `/components/<id>.md`. Read `AGENTS.md` first (constitution: invariants, stack, shared types). Your scope is this one component; you may READ other component docs for contracts you consume, never build them.

## Process (do not skip a step)
1. **Read** the component doc + AGENTS.md. The doc's §8 acceptance criteria are the contract; §9 real-data eval is the product bar; §11 is done.
2. **Plan** the implementation to a file. Then request the `planner-reviewer` subagent to review it as a staff engineer against the doc. Lock the plan before coding.
3. **Tasks**: build in milestone order (doc §5 stages / tasks). One small increment per pass.
4. **TDD**: write the failing test first (Tier-1 [pytest]) → make it green → refactor. `verify.sh` exit 0 is the ONLY signal of a green pass — never your own claim.
5. **Per pass**: after a green increment, request the `verifier` subagent (fresh context) to review the diff vs the doc. Fix reported gaps before continuing.
6. **Section gate**: when all [pytest] criteria pass, run the `eval-gate` skill — pull real repos, build, score the [eval] criteria against golden keys and thresholds. **You are NOT done until every [eval] criterion clears its threshold on real data.**
7. **Iterate**: any failing criterion or eval miss → analyse root cause → fix → rebuild → re-verify. Recursively, with fresh context on repeated failure. Never weaken a criterion to pass.

## Gotchas (read before building — these are the ways this goes wrong)
- **"Green tests" is not "done."** Tier-1 pytest proves the code; only the real-data eval (§9) proves the product. Do not stop at pytest.
- **The author cannot grade itself.** Always use a fresh-context subagent (verifier / spec-compliance-review) to review — a model that wrote the code over-reports its correctness.
- **Never assert from the map or a cache.** Facts come from a live read at verify time (invariant #5). Never cache verify/operate results (invariant #7).
- **Never edit tests, fixtures, verify.sh, hooks, spec, or the component doc.** Tests define done; if a test seems wrong, record the conflict in PROGRESS.md — do not argue with the arbiter.
- **Don't over-build.** No abstraction until a second concrete use exists; no function over ~40 lines; no config flag nothing asked for (see CLAUDE.md style constitution).
- **Ambiguity is a spec bug, not a code decision.** If a criterion isn't checkable against a fixture, stop and flag the spec — don't guess.
- **Every model call goes through the metered gateway** (`src/proxy/llm.py`), never a direct client.
- **Learn:** when a fresh-context reviewer or the eval catches a recurring mistake, add the correction to CLAUDE.md or this component's Gotchas — so the loop never makes it twice.
