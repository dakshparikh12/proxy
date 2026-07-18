My systematic sentence-by-sentence walk of `00-FOUNDATION.md` against `acceptance/doc00/` is complete. I cross-checked every asserted behavior against the 100 criteria, the requirements, the 22 `covered_downstream`/`not_applicable` dispositions, and the fault model. The infra/structural surface (§3–§13) and the durable substrate (§5) are densely covered, and all five laws + §15 agentic-efficiency/safety-floor invariants are explicitly dispositioned to their owning service docs.

Two asserted **doc00-scope** behaviors survive the walk with **no covering criterion, no requirement, no disposition, and no fault** anywhere in the suite (verified by full-suite grep):

---

**1. §2 — the material-change events contract (03→04) is uncovered.**

Spec line (§2, contracts list):
> "**Notes deltas + events** (03→04): `add/patch/close` ops (`note` dropped, folded into `add`); **material-change events (claim-landed-checkable, decision-forming/final, contradiction, action-item, question-open/closed).**"

The `add/patch/close` half of this exact bullet is tested (AC-CMP-006 / R-DOC00-2-06). The **material-change events** half — a named `libs/contracts` seam that doc00 builds (`§16` step 1: "`libs/contracts` … the §2 models") — has no criterion enumerating its member set, no requirement, and no `covered_downstream` disposition. The signal-surface seam in the very next line *was* dispositioned (R-DOC00-2-11 frames it as Doc-02-internal); the material-change events were silently dropped rather than tested-here or dispositioned-downstream.

**2. §5.1 — the heartbeat's activity-bump (sandbox keep-alive during silent agent work) is uncovered.**

Spec line (§5.1, `with_operation_run` reference):
> "`await db.bump_activity(scope_id)    # keeps the sandbox alive during silent agent work`"

The §5.1 heartbeat is decomposed into several sub-criteria — `last_heartbeat_at` update (AC-SUB-004), the fencing self-check SQL (AC-SUB-007) — and sandbox TTL/destroy is covered separately (AC-SUB-016). But the distinct observable behavior that the heartbeat loop *also* bumps activity to prevent the E2B sandbox from timing out during long silent (no-token-emitting) agent work has no criterion, requirement, or fault. A build that heartbeats correctly but never calls `bump_activity` would pass every existing AC-SUB criterion while the promised behavior (sandbox survives silent work) silently fails.

---

Note (not a coverage gap, flagged in passing): §5.3 line 164 still reads "the `meeting_runtime` harness itself is a Cloud Run process, not a provisioned resource," which is stale against **Amendment A1** (meeting_runtime = GCE MIG, *not* Cloud Run) that the criteria correctly encode in AC-HOST-005. That is a spec self-contradiction, not a missing criterion, so it is outside this sweep's output contract — surfacing it only so it isn't lost.
