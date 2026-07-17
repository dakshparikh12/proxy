---
name: writing-plans
description: Phase-5 plan quality — turn a spec + sealed criteria into a milestone-ordered implementation plan a skeptical staff engineer would approve. Pattern borrowed from Superpowers (writing-plans); authored here, no plugin dependency.
---
# Writing the implementation plan (one doc)

You are planning the build of doc <DOC> against its SEALED acceptance bundle (read-only). Output a plan to PROGRESS.md that the `planner-reviewer` subagent will critique before any code is written.

## The plan must
1. **Order by testable milestone** — the sequence in which criteria go green, matching the pre-authored test-file order. Each milestone ends in something `verify.sh` can prove, in isolation.
2. **Map every milestone to the criteria it satisfies** — by `criterion_id`. A milestone that satisfies no sealed criterion is scope creep; cut it. A criterion no milestone reaches is a coverage gap; stop and flag it (the RTM gate should already have caught it — if not, it's a bundle bug).
3. **Name the seams first** — the `libs/contracts` types and adapter interfaces this doc consumes/produces, per AGENTS.md's contract homes. Build against the frozen contract, never redefine it.
4. **Adopt-vs-build per stage** — name the mature tool for each commodity stage (tree-sitter, ripgrep, Serena/solid-lsp, gitleaks…); build only the differentiated glue. No abstraction until a second concrete use exists.
5. **Call out the risky 20%** — the concurrency, the atomic-swap, the failure/degradation paths — and plan them first, not last. These are where correctness dies.

## The plan must NOT
- Propose editing anything under `acceptance/`, `tests/`, `fixtures/`, or the harness. Those are the sealed arbiter.
- Weaken, reinterpret, or defer a blocking criterion. If one is untestable or contradicts a law → `SPEC_BLOCKED`.
- Over-build: no config flags, base classes, or defensive branches the criteria don't demand.

## Gate
Do not start coding until `planner-reviewer` (separate authority) has critiqued the plan and its changes are folded in. Lock the plan to PROGRESS.md, then hand off to `subagent-driven-build`.
