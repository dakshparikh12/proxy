# scenarios/ — Tier-D corpus (generated in Phase 1)

A diverse, spec-grounded set of end-to-end scenarios: normal journeys, edge cases,
failure/negative paths, and cross-feature interactions. Deduped by behavior — diversity,
not raw count.

**Used twice** (SPEC.md §3, Tier D):
- **Phase 1 (plan-time):** each scenario is traced against `chain.json`. Any scenario the
  chain can't serve end-to-end is a completeness gap → back to planning.
- **Phase 2/3 (build-time):** the same scenarios become the real-data test suite —
  the ones that just became satisfiable run as each node lands; the full set is the
  whole-product sign-off.

Format (per scenario file, JSON):
```json
{
  "id": "S-<region>-<n>",
  "kind": "journey | edge | negative | cross-feature",
  "spec_refs": ["03-MEETING-UNDERSTANDING.md#3.2"],
  "given": "...", "when": "...", "then": "...",
  "touches_nodes": ["transport.recall-transcript", "scribe.fold-transcript"],
  "journey_id": "J-09-live-notes"
}
```
`journey_id` MUST be one of the ids in `build/journeys.json` (the same ids nodes carry in
`journeys_now_live`). A scenario is *served* at plan time iff every id in `touches_nodes` exists
in `chain.json` — that proves **logical** coverage; runtime correctness is proven in Phase 2/3.
This directory is populated by the Phase 1 generator; it starts effectively empty.
