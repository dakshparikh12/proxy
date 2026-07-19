S, vs §3.2 "Quality cannot drop… **not worse** than the alternative."
- `AC-HEAR-06`: `speaker_attribution_recall_min: 0.95`, `speaker_misattribution_rate_max: 0.05` — vs §3.9 "a two-speaker test call yields a **correctly attributed** live transcript."
Both `-0.02` and `0.95` are author-chosen numbers with no spec/CANONICAL source and no decision record. Either tighten to the spec absolute (`>= 0` / full attribution) or route the tolerances to `ambiguities.yaml`. (Prior review item 4 — still open, and HEAR-06 is a fresh instance of the same class.)

**6. (BOUNDARY softening) Strict spec bounds written as `<` are made inclusive `≤` in the criteria.**
Spec: join-to-listening **[<10s]**, barge-in stop **[<200ms]**. Criteria invert the strict inequality: `AC-JOIN-02` `boundary_at_10s: pass`, `AC-TURN-09` `boundary_at_200ms: pass`. Exactly-10.0 s and exactly-200 ms should **fail** under `[<…]` but are graded PASS. Defensible on measurement-granularity grounds, but it is an undocumented softening of the literal spec on two latency P0s — record the decision or grade at `<`.

**7. (CONSISTENCY / ordering gap) Some spoken lines are bound to boundary-gating + verbatim text-copy parity, but two are not.**
`AC-FAIL-19`/`AC-FAIL-20` correctly bind the rejoin gap line and the voice-down notice to SPEAK-05/06 parity. But the **objection audible-defer** (`AC-JOIN-13`, "Proxy audibly defers…") and the **swap announcement** (`AC-CANVAS-10`, "every swap is announced") are also user-visible/spoken emissions, yet neither is tied to the §3.3 "every spoken line also posts as text" parity nor to the §3.6 boundary gate (`AC-SPEAK-06`/`AC-TURN-05`). If either is spoken, it can bypass the parity/boundary invariants the sibling lines were explicitly patched to honor.

**8. (SCOPE-CONFIRM, low confidence) Retried-webhook / reconnecting-tile-WS **instance affinity** has no criterion.**
CANONICAL live-WS instance affinity: "a reconnecting tile WS / **retried Recall webhook** must reach the instance holding the meeting's harness — route via the `operation_runs` claim owner… not random load-balancing." `AC-CANVAS-13` covers tile-WS *auth* but not *routing/affinity*; `AC-EVENTS-09/10` cover webhook durability + dedupe but not owner-routing. In a per-meeting single-harness design a webhook/WS landing on the wrong instance is a real correctness bug. Plausibly Doc 04/00-scoped — flag for the author to confirm ownership rather than assert a definite Doc-02 hole. (Prior review item 5 — still unresolved.)

---

**Highest-signal:** #1 (mutually-unsatisfiable P0 pair), #2 (unrouted P0 consent contradiction), #3 (headlines-only vision reducible to a byte ceiling), #4 (P0 no-name consent hole). #5–#6 are softenings to route/record; #7 a consistency patch; #8 a scope question. The recurring structural gap is that **no `ambiguities.yaml` exists**, so every "route this to ambiguities" recommendation from the prior sweep (and #1/#2/#5 here) has nowhere to land and remains silently unresolved.
