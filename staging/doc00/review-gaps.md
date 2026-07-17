ly "status … NOT NULL text." A‑004 already documents live confusion (`in_meeting` in §6 prose). Nothing rejects an out-of-domain status value on the table that drives the partial-unique index, fencing, sweep, and reaper. Given all those P0 mechanisms key on `status='running'`, the accepted value set deserves the same pinning `meetings.status` gets.

**GAP‑6 (LOW-MODERATE). The tenant-scoping FK chain (AC‑TEN‑001) has no structural anchor for `meeting_cost`/`staged_drafts`.** AC‑TEN‑001 requires every tenant-scoped table to reach `tenant_id` "directly or via an FK chain to tenants." But the canonical DDL (CANONICAL §3/§4, mirrored in AC‑SUB‑025/027) declares bare `meeting_id uuid` with **no `REFERENCES meetings`**:
> §3: `CREATE TABLE meeting_cost ( meeting_id uuid PRIMARY KEY, … )` — no FK.

So the "FK chain to tenants" is not structurally present for those tables. Either the schema criteria (AC‑SUB‑025/027) must require `REFERENCES meetings`, or AC‑TEN‑001 (`tenant_unscoped_tables: 0`) is unsatisfiable on the canonical DDL as written. The intent-vs-DDL tension is unrouted.

### Category 5 — Contradiction (lower confidence, unrouted)

**GAP‑7 (LOW). `resume_with_fallback` signature self-contradicts within CANONICAL and is unrouted.**
> §11.9: `resume_with_fallback(session_id, history_fn)` — **2-arg.**
> §12.12: "Pin `resume_with_fallback` **full 6-arg signature** in §11.9."

2‑arg shown vs 6‑arg mandated, in the file Doc 00 encodes. Not in `ambiguities.yaml`. AC‑CMP‑010 checks only single-definition, not arity, so no Doc 00 criterion would catch either shape. (Arguably a Doc 04/05 detail, hence low — but it's a live spec contradiction that the "reconciled, 0 unresolved contradictions" claim missed.)

### Category 1 — Omitted behaviors (lower confidence)

**GAP‑8 (LOW). "keep all three modes" of Claude SDK auth is unverified.**
> §7: "Claude SDK auth (`ANTHROPIC_API_KEY` / OAuth token / Vertex — **keep all three modes**)."

AC‑BOOT‑001 lists only `ANTHROPIC_*` as a required key. A build supporting API-key mode alone satisfies every criterion yet violates the three-mode requirement. No criterion asserts the OAuth-token and Vertex paths exist.

**GAP‑9 (LOW). Envelope "progress events same shape minus finality" has no criterion.**
> §2: "…`status`, `verification…`, `task_id`}; **progress events same shape minus finality**."

AC‑CMP‑012 pins only the *terminal* Envelope's field set. The progress-event contract variant (Envelope shape without a finalized status) — a distinct wire shape consumers must handle — is uncovered at the layer that defines the type in `libs/contracts`.

---

**Priorities before seal:** GAP‑1 (a P1 pair that is jointly unimplementable on the canonical schema — route to `ambiguities.yaml` with a real resolution, not a wording patch), GAP‑2 (affinity/fencing seam with no populating path), GAP‑4 (streaming-spine correctness untested), GAP‑5 (linchpin status domain unpinned). GAP‑3/6 next; GAP‑7/8/9 are surfaced, lower-confidence.
