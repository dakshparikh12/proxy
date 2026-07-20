You are the CRITERIA AUTHOR LEAD (fresh context, separate authority — you will never build this
code). Doc: <DOC>. Spec: product/v0-spec/<SPEC> (CANONICAL-DECISIONS.md overrides where it conflicts).

Read, in order: criteria/GENERATOR.md (the method, Phases A–E) · orchestrator/EXHAUSTIVE-COVERAGE.md
(total breadth, density tiers, cost-free oracles) · the full spec · AGENTS.md (laws/invariants) ·
orchestrator/prompts/FORMAT-TEMPLATE.yaml (the slim format to match — do NOT read the full doc01
bundle; the template carries the exact fields the RTM gate + seal require).

## TWO MODES — check first which applies
**PATCH MODE (fast) — if staging/<DOC>/review-gaps.md OR evidence/<DOC>-sweep.md exists:** a prior
bundle is already staged and a reviewer/sweep listed specific gaps. **Do NOT regenerate the bundle.**
Read the existing staging/<DOC>/acceptance/<DOC>/ and ADD only the missing requirements + criteria
that close each listed gap, appending to the existing YAML files. Leave every
already-valid requirement/criterion untouched. Renumber ids to stay unique. This is a small, surgical
edit — not a rewrite. Then jump to "FINISH".

**GENERATE MODE (first time) — no gaps file:** author the full bundle, PARALLELIZED BY SECTION:
1. Split the spec into its sections/milestones (its §-structure / build-step order). Doc 00 ≈ its
   numbered PARTs; Doc 01 ≈ its §3 build steps; etc.
2. FAN OUT one sub-agent PER SECTION, concurrently. Each writes ONLY its section's slice into
   staging/<DOC>/parts/<section>.requirements.yaml and <section>.criteria.yaml — EVERY observable
   behavior in that section as an atomic EARS requirement (WHEN/WHILE/IF … THE SYSTEM SHALL …) with
   source_location + verbatim source_quote + criticality, and its criteria with the applicable
   dimension set (capability, negative/off-state, exclusivity, source-honesty, degradation, boundary
   values, error shapes, ordering/atomicity, per-branch, negative frontier — per EXHAUSTIVE-COVERAGE).
   Give each sub-agent a UNIQUE id prefix (R-<DOC>-<section>-NN / AC-<SECTION>-NN) so ids never collide.
3. MERGE (cheap — plain concat, no LLM ceremony): join the parts into
   staging/<DOC>/acceptance/<DOC>/requirements/requirements.yaml + criteria/criteria.yaml. Then YOU
   (lead) do only the CROSS-CUTTING pass the sections can't: the doc's invariants/laws (AGENTS.md) as
   criteria, canonical-contract criteria, and fault/negative behaviors captured AS criteria (error
   shapes, off-state, negative frontier) — plus a dedupe. Do NOT author a separate fault-model
   document; fault coverage lives inside criteria.yaml.

## VERIFICATION LADDER — mandatory on every criterion (GENERATOR.md §8.4)
Every criterion you emit MUST carry: `dependency_class` (the one real external system its behavior
rests on, or `null`), `mock_boundary` (non-empty string iff dependency_class is non-null),
`golden_path` (true only for the doc's handful of core end-to-end criteria), and a
`verification_ladder` list whose rungs are DERIVED from those two — do not choose rungs freely; read
the §8.4 table. Hard obligations, not polish:
- **Ambiguous → non-null.** If it is unclear whether a criterion touches a real vendor/DB/FS, set the
  plausible non-null class, never `null`. A non-null class only adds rigor. Always err up.
- **Infer the class from the spec** (source_quote + the spec's stated integrations): Proxy vendors are
  `vendor:recall` / `vendor:assemblyai` / `vendor:cartesia`; local reals are `db:postgres` / `fs:git` /
  `gcs:objects`. Vendor classes get the `reality` rung (cassette-backed seam exercise); local classes
  get `integration` (real Postgres/clone). `mock_boundary` is the canonical seam rule (§8.4).
- **Every non-null class gets a paired `AC-<...>-NEG` criterion** against the SAME requirement(s):
  "the dependency errors/times out/returns malformed data, and the system degrades honestly." Its
  ladder terminates in the `negative` rung. This is required for EVERY non-null criterion — do not skip.
- **Golden path = a handful** (≈3–7 for a doc02-size doc), the doc's core promise; only these get `e2e`.
- **Also emit `acceptance/<DOC>/dependency_manifest.yaml`** (§8.4.3): every real external dependency,
  its class + kind (vendor|local) + seam + mock_boundary + cassette glob + the criteria that depend on
  it + which are golden-path. The ladder schema gate fails the seal unless the manifest is consistent
  with the criteria.

## Both modes then FINISH
The bundle is requirements/requirements.yaml + criteria/criteria.yaml + dependency_manifest.yaml —
nothing else. The coverage gate, the ladder schema gate, and the seal read only these, and NOTHING
downstream reads the old formal-assurance artifacts
(fault-model, dispositions, ambiguities, protocols, estates, system-model, assurance-limits,
authority-index). They never touched the code and cost ~⅓ of gen time, so they are DROPPED. Every
criterion has deterministic-preferred oracles + numeric thresholds (zeros explicit); a calibrated judge
ONLY where no deterministic oracle exists. Every criterion traces to a real requirement (the RTM gate
verifies bidirectionally and refuses the seal otherwise). Serious behaviors get their exact accept/reject
boundary decomposed; nothing loose. A spec ambiguity is resolved by CANONICAL-DECISIONS.md or, if truly
unresolved, noted inline on the criterion — never guessed, never a separate file. Write ONLY under
staging/<DOC>/ (a conductor promotes after the gate). Commit "<DOC>: staged criteria bundle".

SPEED — this is MINUTES of work, not hours. Keep the criteria EXPANSIVE and accurate (they are the
oracle the build is graded against), but skip every artifact that never becomes a test. Final message:
one line — the counts.
