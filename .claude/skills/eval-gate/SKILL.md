---
name: eval-gate
description: Use to verify a built Proxy component actually works on real data (the product arbiter). Pulls real public repos, builds the component on them, scores outputs against human golden keys with thresholds, and iterates until accurate. This is the section merge gate for Tier-2 criteria — not code tests.
---
# Real-data eval gate (Tier-2 product verification)

Code tests prove the machinery; this proves the PRODUCT is correct on real systems. Run this once a component passes its Tier-1 pytest arbiter, before merge.

## Procedure
1. **Seed** from `/fixtures/<id>/`: real, diverse, public/licensed estates spanning the doc's §8 range (messy / single / monorepo / multi-language / docs-heavy). Confirm each has a human-reviewed golden key in `/fixtures/<id>/golden/`.
2. **Build** the component on each estate (e.g. for 1.2: actually construct the estate graph from the real repo + its docs + issues).
3. **Score** each [eval] criterion as a scenario:
   - **deterministic graders** wherever a code check confirms the outcome (counts, latency, locator-resolution, permission matrix, stale-vs-live).
   - **LLM-as-judge** ONLY after calibrating it against the human gold set, for fuzzy outcomes (is the blast-radius answer the one a senior engineer gave? is it bluffing? is the extraction faithful?).
   - metrics: correctness, groundedness, completeness, honesty/refusal, latency, cost.
4. **Gate** on the thresholds in doc §8. A criterion passes only at/above its number, on EVERY estate.
5. **Adversarial** (doc §10): run the attack suite; any invariant violation fails the gate regardless.
6. **Iterate**: any miss → write the failure report → hand back to the build loop with FRESH context → fix root cause → rebuild → re-run this gate. Repeat until all clear.
7. **Record** results to `/evidence/<id>/eval-results.md` for signoff.

## Gotchas
- **A bad embedding/judge causes false passes.** Calibrate the LLM-judge against humans before it may gate; prefer deterministic graders wherever possible.
- **Small, high-quality golden sets beat large auto-generated ones** (~100 curated, not thousands of slop). Add real failures back over time — it's a living dataset.
- **Test on the messy estate, not just the clean one.** A graph that's perfect on a tidy single repo but wrong on a config-routed monorepo has not passed.
- **Done is a threshold on real data, never "the eval ran."**
