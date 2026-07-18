h node stale vs pinned commit → deference rule (2), re-read live"*).
> `built_at_sha` = 0 hits in criteria. This is dispositioned in `dispositions.yaml` D-INV-05 as *"No separate criterion needed,"* but no criterion enforces the stale-node → re-read behavior, and the `built_at_sha` node column exists solely to power it.

**6. Force-push (and parser/grammar upgrade) forces the full rebuild / safe history-rewrite handling.**
> §3.6: *"A **force-push, a parser/grammar upgrade, or a large change-set forces the full rebuild**"* (also §4: *"force-push → safe full rebuild"*).
> `force-push` = 0 hits in criteria. AC-M4-009 tests that an ordinary single-file push does a full rebuild, but nothing exercises the force-push / non-fast-forward (history-rewrite) delta-pull path the spec calls out as a distinct safe-handling case.

**7. `git blame` resolves on a blobless clone.**
> §3.2: *"so huge repos clone in minutes and **`git blame` still works** (we never use *shallow* clones; they break history)."*
> §3.8 step 2 provable: *"`git blame` resolves on a large repo."* AC-M2-002 verifies the pinned SHA and file tree only; no criterion verifies `git blame` works after a blobless clone (the specific reason shallow clones are rejected).

**8. Meeting about a PR pins to the PR head (not the default-branch tip).**
> §3.6: *"the session **pins to one commit** — the default branch's tip, or the **PR head** for a meeting about a PR."*
> AC-M7-004 tests pinning to a known SHA (default-tip case) only; the PR-head branch of the pin selection has no criterion.

---

## Dispositioned / boundary items (asserted behavior, no criterion — but explicitly accounted for, not silently missed)

Reporting these for completeness; each is a documented decision rather than an oversight:

- **`~10s` push-to-queryable freshness target** — §3.6 *"Target: `[~10s]` push-to-queryable on a pilot-scale repo"* / §4 *"substrate + graph push-to-fresh `[~10s]`."* No latency criterion (AC-LAT covers only direct-answer p50/p95 and 15-min ready). Explicitly dispositioned aspirational-not-a-gate in `ambiguities.yaml` A-001 and A-006.
- **Sandbox re-provision re-seeds at `meeting.pinned_sha`, not HEAD** — §3.6. No criterion; falls in the Doc 05 / Workroom sandbox boundary (AC-M3-005 only checks sandbox secret-exclusion).
- **Incremental parse — unchanged files keyed by hash are never re-touched** — §3.4. No criterion; an optimization mechanism (the *graph* rebuild is full and is tested by AC-M4-009).
- **`propose_change` and the deferred agentic-map tools (`get_capability`/`search_capabilities`/`get_flow`) absent from the core** — dispositioned as not-built-here (`D-BOUNDARY-01`) / Expansion (§5).

---

Note: I made no modifications (read-only sweep). Items 1–8 are the actionable coverage gaps; 1, 2, 6, and 7 are the strongest (each has an explicit "provable" clause in the §3.8 build steps yet no criterion), and 5 is notable because a graph column exists purely to serve a behavior that is never tested.
