# decisions.md — technical-gap resolutions (append-only)

Every gap the comprehension resolves *itself* (technical/implementation gaps — LOOP.md §1.1b)
is logged here with its rationale, so the human can review the judgment at HALT 1 and the
builder can see why an under-specified seam was implemented a given way. Product-level gaps are
NOT resolved here — they go to the human as a bounded question and the answer is recorded below
once given.

Format:
```
## D-NNN  <short title>   [technical | product-answered]
- Gap: <what the specs left open, cite spec_refs>
- Options considered: <briefly>
- Decision: <what we chose>
- Rationale: <why it best aligns with the product intent>
- Nodes affected: <node ids>
```

<!-- entries appended below during Phase 1 -->
