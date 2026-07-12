# Proxy — Build Environment (loop-engineering)
Point a Claude Code build at this repo. It reads `AGENTS.md` (constitution) → `docs/BUILD-SYSTEM.md` (how we build) → one `components/<id>.md` (what to build) → builds it via the `proxy-component-build` skill → verifies with two arbiters (pytest + real-data eval) → iterates until the product is proven on real data.

```
AGENTS.md                 constitution (read first)
CLAUDE.md                 Claude Code context + style
docs/BUILD-SYSTEM.md      the full four-layer loop
docs/VERIFICATION-PROTOCOL.md   the 7 verification layers (your doc, + subagents + eval gate)
components/_TEMPLATE.md    the optimal component-doc structure
components/1.1-ingest.md   components/1.2-graph.md
.claude/skills/           proxy-component-build · eval-gate · spec-compliance-review
.claude/agents/           planner-reviewer · verifier · eval-runner
.claude/settings.json     hooks wiring (guard · post-edit-test · stop-verify)
harness/                  the hardened substrate (guard/runner/verify — from your build doc)
fixtures/<id>/            real seeded estates + golden keys (the eval dataset)
eval/<id>/                DeepEval/Braintrust scenarios + thresholds
evidence/<id>/            per-section signoff + eval results
```
Adopt: Spec Kit (spec/plan/tasks + constitution) + Superpowers (brainstorm→TDD→review) + DeepEval/Braintrust (eval). Keep: your harness. Skip: Ruflo, BMAD. See `docs/BUILD-SYSTEM.md §5`.

## To run (see RUNBOOK.md for the full step-by-step)
```
bash harness/scripts/bootstrap.sh
python3 runner.py --component 1.1-ingest        # build loop (code arbiter: verify.sh)
python3 eval_runner.py --component 1.1-ingest   # eval gate (product arbiter: real data vs golden keys)
```
Runnable scaffolds: runner.py · eval_runner.py · harness/{verify.sh,guard.py,stop_verify.py,post_edit_test.py,prompts/pass_prompt.md,scripts/bootstrap.sh}. Replace with your hardened versions where you have them. Author fixtures/<id>/golden/ + eval/<id>/scenarios.json before the eval gate (RUNBOOK STEP 5). See docs/TESTING-STRATEGY.md for how acceptance criteria relate to real-data testing.
