# Exhaustive Coverage — the generator amendment (Phase A + D)
### Every behavior in the spec becomes a criterion. No matter how small — the orb's pulse, one routing branch, one copy-voice rule. Directly from the spec text, in Pranav's bundle format. This tunes GENERATOR.md Phases A (atomize) and D (expand); it does not replace the sealed-bundle model or the coverage gate.

## The rule: total breadth, risk-weighted depth
- **Breadth is total.** *Every* spec sentence that asserts an observable behavior — including table rows, parentheticals, "must/never/always" rules, and cosmetic details — becomes **≥1 criterion.** A behavior with no criterion is a coverage hole, no matter how small. The RTM gate (criteria_coverage_gate.py) enforces this: every atomized behavior → a requirement → ≥1 criterion, or the bundle cannot seal.
- **Depth is risk-weighted.** *How many* criteria a behavior spawns scales with risk (a P0 security/concurrency behavior gets its full dimension set; a cosmetic P2 gets one). Breadth ≠ padding: every criterion is a *distinct* observable behavior with its own oracle; the adversarial review culls duplicates.

## Phase A — atomize EVERYTHING (how "no matter how small" is enforced)
For each doc, walk the spec top to bottom and emit one atomic requirement per assertion, capturing:
- **A table row is a behavior.** The orb state table's "Speaking | gentle pulse synced to its own audio | Doc 02's speaking signal" → one requirement.
- **A parenthetical / an inline rule is a behavior.** "(never on a timer)", "no exclamation marks", "the swap itself is announced" → each its own requirement.
- **A "never/always/must" is a behavior** (often two — the positive and the negative-guard). "the renderer must contain no code path for facial features" → a structural requirement.
- Each requirement carries `source_location` + `source_quote` (verbatim), so it provably comes from the spec — not invented.

## Phase D — expand each behavior into its APPLICABLE dimensions (situation + primary outcome)
Every criterion states a **situation** (given/when) and the **primary outcome** (then) — the observable result. For each atomized behavior, generate the dimensions that genuinely apply (skip the inapplicable, and say so):
| Dimension | The situation → outcome it captures |
|---|---|
| **Capability** | the behavior happens on its trigger (orb pulses WHEN the speaking signal fires) |
| **Negative / off-state** | the behavior does NOT happen without its trigger (orb does NOT pulse when silent) |
| **Exclusivity** | mutually-exclusive states don't co-occur (not Speaking + Muted at once) |
| **Source-honesty** | the behavior is driven by the REAL system event named, never decorative/faked |
| **Accessibility / parity** | the cross-cutting law holds for this behavior (every tile state also in speech/chat) |
| **Invariant / structural** | the hard-ceiling holds (no unrenderable path exists — static check) |
| **Degradation** | on the driver's failure, the honest fallback (dim/label), never a lie |

## Oracles are cost-free even for UI (how we test "the blob pulses" with no spend)
- **State-machine assertion** — the tile is a rendered webpage driven by system events; the oracle is `render_state(event) == expected_state`, asserted on the renderer's emitted state, not a pixel diff. Deterministic, no models, no cost.
- **Static analysis** — the hard-ceiling ("no facial-feature code path") is a source scan for forbidden render primitives. Deterministic.
- **Channel-parity check** — accessibility ("every state also in speech/chat") asserts a corresponding chat/speech output exists for each tile state. Deterministic.
- **Simulation trace** — the behavior appears in an end-to-end run (a scripted meeting emits a speaking event → the trace shows the pulse state). No live meeting, no API.
Nothing is graded by an LLM judge that a deterministic or simulated oracle can settle — GENERATOR.md §1.4.

## Density tiers — serious behaviors get their exact boundary nailed
Breadth is total; *density per behavior scales with consequence.* A cosmetic P2 (orb color) gets one criterion. A **serious behavior** (security boundary, concurrency, money, irreversible write, a routing decision, a staged-draft gate, a latency SLO) gets its **exact accept/reject boundary decomposed into sub-criteria** — because "roughly right" is a failure there:
- **Boundary values** — the exact threshold and both sides of it (at the limit, one below, one above). E.g. blobless clone at 99,999 vs 100,000 vs 100,001 files; latency at the SLO and just over it.
- **Exact error/output shape** — not "it errors" but *which* typed error/tag, verbatim (`resolution: lower-bound` with the named missed-reason, not just "not exact").
- **Ordering & atomicity** — the exact sequence that must hold (DROP-before-INSERT; reconcile-before-pin; secret-redacted-before-persist) and that no interleaving violates it.
- **Every branch of a decision** — one criterion per routing outcome / per state transition / per task class, not one lumped "routes correctly." (Orchestrator: a criterion per wake-decision branch; Workroom: a criterion per task class that it plans-then-builds correctly.)
- **The negative frontier** — the precise set of inputs that must be REJECTED, stated as explicitly as the accepted set. "Only this behavior is accepted" means the rejected behaviors are enumerated too.
The rule of thumb: for a serious behavior, a reader should be able to reconstruct the *exact* accept/reject line from the criteria alone, with no ambiguity about a single input. Take whatever space that needs.

## The "nothing loose" bar
Every criterion has a named oracle + a threshold (e.g. `pulse_without_speaking_allowed: 0`). No criterion says "works well" or "looks good." If a behavior can't be pinned to an observable outcome + oracle, it's a spec ambiguity → SPEC_BLOCKED, not a vague criterion. See the worked demonstration in `demo-doc08-orb-criteria.yaml` — every orb behavior in Doc 08 §2.1–2.2, exhaustively, directly from the spec.
