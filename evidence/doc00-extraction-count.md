# Extraction-count audit — doc00  (Task 8, real fresh-context opus recount)

- bundle requirement count: 144
- independent spec-only recount: 211
- relative disagreement: 46.5%  (threshold 10%)
- verdict: MATERIAL_DISAGREEMENT (HALT for founder review)

## Analysis
auditor counted FINER than the bundle (+47%). Grain-dominated: schema/config/invariant specifics (§5 substrate, §7 config manifest, §13 hardening, §15 invariants) decomposed into individual testable atoms the bundle folds into acceptance-criteria of a parent requirement. Auditor SPOT-CHECK POINTERS for possible genuine under-extraction: §5 durable-ops substrate (~49 obligations) and §15 consolidated invariants (~21). Cross-section restatements were merged to one home (no double-count).

## Disposition (per GENERATOR.md Task-5 design)
HALT for founder review — the gate does NOT auto-resolve by regenerating. A human must adjudicate whether the disagreement is grain (most likely here) or a genuine under/over-extraction, using the spot-check pointers above.
RATIFIED 2026-07-20: doc00 extraction-count MATERIAL_DISAGREEMENT (144 vs 211) reviewed by founder. §5 substrate + §15 invariants spot-checked against requirements.yaml — all flagged obligations present as sub-clauses under fewer parent IDs. Confirmed grain difference, not coverage gap. No regeneration needed.
