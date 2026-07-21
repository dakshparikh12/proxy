bundle pins the readability floor. Confirm it's deferred, not dropped.)*

## Category 4 — Boundary gaps on serious behaviors

**5. `claim-landed (checkable)`: no boundary case, and the qualifier is unbound to any schema field.**
Spec §3.5 emits "`claim-landed (checkable)`"; §1 names the proactive trigger as "a **checkable** claim landed." `R-doc03-EVENT-02` fires on "a claim-landed (checkable) entry," and `AC-EVENT-01` fires on "the claim-landed delta" — but (a) the `Claim` schema has **no `checkable` field**, so how checkability is determined is unspecified, and (b) **no criterion tests that a non-checkable claim (an opinion) does *not* fire `claim-landed`.** If every claim fires, the load-bearing "(checkable)" filter that gates downstream fact-checking is inert. → needs a negative-frontier criterion + an ambiguity note on how "checkable" is derived.

**6. `Decision.leans` stance enum is unvalidated.**
Spec §3.2.1: `leans: dict[str, Literal["for","against","silent"]]`. `Decision.referents` max-8 is tested (`AC-SCHEMA-23`), but no criterion rejects an invalid stance value (e.g. `{"Priya": "maybe"}`). Data-integrity boundary with no oracle. (Minor.)

## Category 5 — Impossible/contradictory criteria (spec bugs → should route to ambiguities.yaml)

**2. `AC-PERF-05` / `R-doc03-PERF-05` require fields the §3.2.1 schema does not define — a schema-conformant build fails this P0-blocking criterion.**
`R-doc03-PERF-05` (from §4 Accuracy): "attach observed-vs-inferred **and** firmness labels to **every entry** emitted by the Scribe; these labels SHALL NEVER be omitted." `AC-PERF-05` asserts "Every entry has a non-null firmness field" / "non-null observed_vs_inferred field" across "claims, **decisions, actions, questions, context**." But per §3.2.1:
- `Decision` → **no** `firmness`, **no** `provenance` (has status/reversibility/leans/referents)
- `ActionItem` → `provenance` only, **no** `firmness`
- `OpenQuestion`, `ContextLine` → neither

So the criterion is unsatisfiable for four of five entry kinds. The source sentence (§4 "Observed-vs-inferred + firmness labels keep uncertainty visible") is scoped by §2 to **Claims** ("Claims — … with **firmness** … and an **observed vs inferred** label") — the requirement over-generalized "every claim" into "every entry." Same defect in `AC-XCUT-02`'s bullet: *"No Claim or **ActionItem** entry lacks a **firmness** or provenance field"* — `ActionItem` has no `firmness` field. → route to ambiguities; re-scope both to the entry kinds that actually carry each label.

---

Net: the mechanical/schema/storage/cost/latency spine is well-covered and honestly-oracled. The gaps cluster at the **product-comprehension surface** (Topics, checkable-claim filtering, the goal signal) and the **honesty frontier** (the sentiment/emotion "never," the secret-redaction seam), plus one **unsatisfiable P0 criterion** (#2) that blocks any correct build. Fix #2 and #1 before merge; route #1/#2/#5 (checkable-derivation) to an ambiguities file.
