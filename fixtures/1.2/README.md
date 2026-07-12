# fixtures/1.2 — real seeded estates + golden keys (the eval dataset)
Build the graph over each; reuse 1.1's ingested estates. ~100 human-reviewed goldens. Living asset.

- `estate-service/` — multi-service / cross-language repo (calls, api_contract, cross-service edges).
- `estate-docs/` — docs describing code (doc↔code entity resolution).
- `estate-messy/` — dynamic dispatch + config routing (coverage ledger non-empty).
- `estate-live/` — a source that can be mutated after ingest (live-truth).
- `estate-single/`, `estate-monorepo/` — size range (P7).
- `golden/blast-radius.json` — 20 "what breaks if X?" + expert dependency sets (P2).
- `golden/referents.json` — 20 ambiguous mentions + target nodes (P3).
- `golden/edges-sample.json` — 100 edges + validity labels (T2/P-validity).
- `golden/unmapped.json`, `golden/live-truth.json`, `golden/novel-tasks.json`.
Record provenance + license. Public/licensed only.
