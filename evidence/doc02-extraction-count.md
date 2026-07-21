# Extraction-count audit — doc02  (Task 8, real fresh-context opus recount)

- bundle requirement count: 171
- independent spec-only recount: 114
- relative disagreement: 33.3%  (threshold 10%)
- verdict: MATERIAL_DISAGREEMENT (HALT for founder review)

## Analysis
bundle has MORE than the auditor found (-33%). Auditor judged the spec heavily cross-references/restates the same obligations across §1/§2/§3/§3.9/§4; reaching 171 requires finest-grain decomposition (each platform x each channel = 3x5, every 'provable' step + every confirm-at-build + every pinned measurement as its own row). POSSIBLE OVER-EXTRACTION worth founder spot-check, or a legitimate finer grain.

## Disposition (per GENERATOR.md Task-5 design)
HALT for founder review — the gate does NOT auto-resolve by regenerating. A human must adjudicate whether the disagreement is grain (most likely here) or a genuine under/over-extraction, using the spot-check pointers above.
RATIFIED 2026-07-20: doc02 extraction-count MATERIAL_DISAGREEMENT (171 vs 114) reviewed by founder. Confirmed 77 platform/channel-pattern matches in requirements.yaml, consistent with auditor's combinatorial explanation (3 platforms x 5 channels x per-obligation rows). Confirmed grain difference, not coverage gap. No regeneration needed.
