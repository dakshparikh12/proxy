fixture-thin.**
§3.4 / §3.11: named things → real code areas. AC-FAIL-07 / REFM test that a fixture `"checkout"` binds and a nonsense term stays unbound — but nothing measures whether the Scribe correctly *marks* referent candidates from real speech, nor `lookup_referent` precision/recall at any threshold. "starts oriented" (§1) is asserted by a single happy-path fixture.

---

## G3 · BOUNDARY / ORDERING GAPS ON SERIOUS BEHAVIORS

**G3.1 — Correction → prefix-refresh timing is unsatisfiable-as-written OR encodes an unstated behavior.**
§3.6: *"The next Scribe call sees the corrected notes in its prefix — the correction sticks."* AC-CORR-03 makes this concrete: *"The Scribe's notes prefix (Segment B / rolling context) contains E with value V1"* on *"the next Scribe micro-call."* But Segment B is the **cached** rolling summary, byte-stable **between cadence regenerations** (§3.2: every ~20 deltas / ~90s) — a correction cannot appear in the *next* call's prefix unless a correction force-triggers an immediate rolling-summary regeneration. **No requirement specifies that**, and doing so would bust the cache (tension with SCRIBE-07 byte-identical-prefix discipline). So AC-CORR-03 is either impossible as written or silently assumes an un-required behavior. Unrouted.

**G3.2 — Two criteria assert different freshness-flag thresholds.**
§4 pins *"notes freshness flag threshold `[>90s lag]`"*; AC-FAIL-06 correctly uses **90s**. But AC-PERF-07-NEG is titled *"Comprehension lag exceeding 4s triggers freshness flag"* — conflating the §4 *"2–4s typical"* / *"within T+4s at p95"* latency figure with the flag trigger. Since typical lag **is** 2–4s, a builder following PERF-07-NEG would fire the freshness flag almost continuously. Same flag, two contradictory thresholds in one bundle.

---

## G4 · MECHANISM ABSENT: no ambiguities/spec-bug channel exists

The reviewer mandate expects spec bugs routed to `ambiguities.yaml`, but **no such file exists** in this bundle (or in doc00–doc02). So the Topic schema inconsistency (G1.1) and the correction-prefix contradiction (G3.1) have nowhere to be recorded and are simply absent. The bundle silently resolved at least one ambiguity inline correctly (the `"checkable"` qualifier on `claim-landed` — resolved via R-doc03-EVENT-19 as "all Claims checkable, all ContextLines not," documented at criteria.yaml:4132 — this one is *handled well*), which shows the others should have been too.

---

### Priority for the author
1. **G0.1 + G0.2** — fix the YAML so the artifacts load at all (blocking; nothing else can be verified until then).
2. **G2.1–G2.4** — add real-data `[eval]` golden-key criteria for the *live* Scribe's firmness/provenance accuracy, contradiction detection, semantic dedup, and referent binding; today the product's central quality promise can pass while feeling wrong.
3. **G1.1 (Topics)** and **G3.1/G3.2** — route to an ambiguities record and resolve.
4. **G1.2, G1.3, G1.4** — close the omitted-behavior coverage.
RATIFIED 2026-07-21: doc03 extraction-count MATERIAL_DISAGREEMENT (91 vs 127) reviewed by founder. Spot-checked §6 bi-temporal contradiction cases — SCRIBE-08 (symmetric contradiction ordering), SCRIBE-05 (decision-supersedes-decision), plus event_corr/schema requirements cover the named sub-cases as consolidated parent requirements. Confirmed grain difference, not coverage gap. No regeneration needed.
