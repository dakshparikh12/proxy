---
name: specify
description: Turn a spec (+ the analyst's intent brief) into a COMPLETE, testable acceptance bundle — criteria + the atomic task list — with guaranteed both-ways coverage. Use in forge phase ② after Understand, before Plan.
---

# Specify — spec → sealed acceptance bundle (the coverage guarantee)

Input: the target spec + the `analyst` intent brief. Output: `acceptance/doc<NN>/{requirements.yaml,
criteria.yaml}` + `slices/<id>/tasks.json`, sealed. The guarantee: **every spec clause → a requirement
→ a criterion → a task → a passing test**, both ways.

## Steps
1. **Extract in parallel by section.** Dispatch one fresh-context extractor per top-level spec section
   (`superpowers:dispatching-parallel-agents`). Each reads *its* section + the intent brief and emits
   requirements (id, source_quote, normalized_statement) and criteria. Fresh context per ~5–10KB
   section reads deeply — this is the biggest lever on completeness. Merge by id (dedupe, validate
   authority_refs both ways).

2. **Author criteria in EARS.** Phrase each criterion's behavior in EARS —
   *"While `<precondition>`, when `<trigger>`, the `<system>` shall `<response>`"* — which maps 1:1 onto
   `behavior + oracle + threshold`: precondition/trigger = the oracle setup, "shall" = the behavior,
   and it forces a single testable predicate. Also set `evidence_class` (which ladder rungs verify it:
   static / unit / property / real-infra / real-data), `thresholds` (traced to spec/canon — never
   invented), and `test_ids`. Use EARS where behavior is event/state-driven; don't force it on pure
   data-transformation/extraction-quality criteria.

3. **Decompose into tasks.** Invoke the `forge:decompose` skill → `tasks.json`: one atomic,
   dependency-ordered task per criterion, each bound to its criterion + a **real** test via a
   `@pytest.mark.criterion("AC-XXX")` marker (never a `-k` name regex — that silently zero-matches).

4. **Prove coverage (deterministic).** Run `python3 "${CLAUDE_PLUGIN_ROOT}/gates/coverage.py" <id>` —
   must exit 0 (req↔crit↔task closure). A gap is a bundle bug, not an agent decision.

5. **Audit (maker≠checker).** Dispatch the `forge:criteria-auditor` agent (fresh context). It must
   return SEAL-READY (zero uncovered clauses, zero untestable/weak criteria). Each finding → FIX /
   `ambiguities.yaml` / SPEC_BLOCKED.

6. **[Clarify — only if needed].** If the auditor surfaces genuine ambiguity/contradiction, ask the
   founder ≤5 targeted questions and encode the answers. A clean spec skips this.

7. **Founder seals.** Present one review packet (intent brief · coverage summary · any parked
   ambiguities · SPEC_BLOCKED). On approval, the bundle is **immutable** — the guard makes
   `acceptance/` read-only, and the builder is now graded against a contract it cannot edit.

## Hard rules
Never invent a threshold. Never let a criterion pass without a real oracle. An untestable/contradictory
criterion is a founder-gated spec repair (`SPEC_BLOCKED`), never a guess. `_baseline.json` and the
extraction-count HALT are human-gated.
