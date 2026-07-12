---
name: spec-compliance-review
description: Use to review a diff against a component doc from a FRESH context, checking it satisfies the spec for the right reasons and violates no invariant. Report gaps, not style. This is the automated version of adversarial review (Layer 5a).
---
# Spec-compliance review (fresh context)

Review the current diff against `/components/<id>.md` and `AGENTS.md`. You did NOT write this code — do not assume it is correct.

## Check, and report GAPS not style
1. **Every acceptance criterion** (doc §8) — is each actually implemented and tested, or does a test pass for the wrong reason (tautology, over-mocking, asserting nothing real)?
2. **Every invariant** (AGENTS.md §4) — cited-or-abstain, zero-copy, permission-at-read, truth-is-live, freshness-gated caching, tenant isolation, etc. Name any violation.
3. **The seams** (doc §6) — do the adapter interfaces hold their contract shape?
4. **Scope** — did anything outside this component's task change? (non-goals in doc §3)
5. **Over-engineering** — abstraction/flags/defensive code the task didn't need (CLAUDE.md style constitution).

Return a concise list of concrete gaps with file:line. If none, say so plainly. Do not report style preferences.
