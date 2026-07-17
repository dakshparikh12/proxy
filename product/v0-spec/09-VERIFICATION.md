# Doc 09 · End-to-End Verification & Run-Throughs — Build Spec (skeleton)

*Build order: **8th / last** — the cross-doc integration + acceptance stage. Design order: last. This doc does NOT restate per-doc acceptance criteria (those are generated later via Kiro/Spec Kit per the settled decision); it specifies the **assembled-product run-throughs** that prove the six services compose into one coherent product on the connect→meet→close arc, and the **cross-doc contract checks** that prove the seams close. Grounded in: `CANONICAL-DECISIONS.md` (the frozen seams), the three coherence audits, and every doc's own "Provable:" build steps. This is a skeleton — the scenarios are named and scoped; detailed step-tables come with the build.*

---

# 1 · The end goal

This stage exists to prove one thing in plain terms: **that the whole Proxy actually works as a product, not just as seven parts that each pass their own tests.** A real team should be able to connect their repository, invite Proxy to a real meeting, ask it questions and get answers grounded in their actual code within a second or two, hand it real work and get back a verified draft they approve with one click, and leave the meeting with clean, honest notes — and none of that should break when a server restarts mid-call, when several meetings run at once, or when the cost creeps toward the budget. In short: the connect → meet → close arc holds end-to-end for a real user, the pieces compose into one calm, trustworthy colleague, and no seam between the documents silently fails.

Stated precisely: **this doc specifies the assembled-product run-throughs that prove the six services compose into one coherent product on the connect→meet→close arc, plus the cross-doc contract checks that prove the seams close** — so that "it's done" is a demonstrated fact about the whole system, never an assumption about the parts. (Per-service acceptance criteria are generated separately via Kiro/Spec Kit; this stage is the *integration* proof.)

## 2 · Cross-doc contract checks (run in CI, before any scenario)

These prove the seams from CANONICAL-DECISIONS actually close — the discipline that stops re-drift:
- **`assert_registry_closed()`** — `set(MessageType) == set(CHANNEL_REGISTRY)`; a produced-but-unconsumed message fails the build (Doc 00 §12, Doc 08 §4.1). Runs in CI **and** at boot.
- **The contract types resolve to `libs/contracts`** — every doc's wire shape (the bundle, envelope, signal surface, notes deltas, readiness, `AgentChunk`) imports the literal type; a grep proves no doc re-declares a shared type's shape locally.
- **One `operation_runs`** — exactly one table definition across the repo (no `meeting_harness`); the reconcile sweep reaps it (CANONICAL §2).
- **`AgentChunk` consumers use `stream_deltas`** — no consumer reads a raw `TEXT` chunk (the accumulation-vs-delta bug); field access is `.type`/`.metadata[...]` (CANONICAL §1.1).
- **Cost + drafts persist** — `meeting_cost` and `staged_drafts` rows exist and survive a simulated process kill (CANONICAL §3, §4).

## 3 · The core run-throughs (each names the docs it exercises + the pass condition)

| # | Scenario | Exercises | Pass condition |
|---|---|---|---|
| **S1** | **The happy arc** — connect a repo → invite to a meeting → ask one grounded question out loud → get a cited voice-headline + chat-detail answer → meeting ends → notes file posted | 01·02·03·04·05·08 end-to-end | Answer cites `file:line` from the live clone in ≤ the latency target; notes file lands with decisions/actions/questions + receipts |
| **S2** | **"What breaks if we change this table?"** — the core dep-graph moment | 01 (`get_dependents`/`who_writes`) · 04 wake · 02 | Proxy answers from the dependency graph in ~1 tool call, grounded, honest about coverage |
| **S3** | **Real work → staged draft → accept after the call** | 05 (verify-loop, `propose_change` persist) · 04 (accept-handler) · 08 (accept action) | Draft persists to `staged_drafts`+GCS; "verified" only past the separate critic + evidence gate; a human accept **after teardown** applies it |
| **S4** | **Barge-in + human control** — interrupt mid-sentence; "Proxy, quiet"; "stop" a running build | 02 (barge-in <200ms) · 04 (AbortController, preempt) | Speech stops <200ms; the in-flight wake/build aborts (abort-is-final); a human ask preempts |
| **S5** | **Hours-long meeting survives an instance recycle** | 04 (Tier-1 resume + Tier-3 replay) · 03 (transcript plane) · 00 (§5 heartbeat/atomic-claim/reconcile) | Kill the orchestrator mid-meeting → a replacement **re-claims** the meeting (atomic claim frees after reap) → replays recent transcript → continues; **`meeting_cost` reloads** (budget not reset to 0) |
| **S6** | **Concurrency** — several meetings on one host at once | 04 (per-meeting harness, per-host in-flight bound) · 05 (per-meeting sandbox) | No cross-meeting starvation; each meeting's tenant isolation holds; sandboxes provisioned/reaped independently |
| **S7** | **Cost circuit-breaker** — a meeting with real builds crosses the $1/hr envelope | 03·04·05 (`check_meeting_budget`) | Soft cap degrades (Orchestrator→Haiku, widen Scribe interval) with a spoken disclosure; hard cap → notes-only; never a silent cliff |
| **S8** | **Honest failure** — index not-ready / a tool errors / STT gap | 01 (`not_ready`+gaps) · 05 (fail-closed verify) · 02 | Proxy says what it can't see; never a confident wrong answer (Law 1); a failed build returns `needs_review`/`failed`, never "done" |
| **S9** | **Tenant isolation** — a meeting guest opens the public connect page / a crafted tile message | 08 (dispatch funnel, `meeting_id`-presence isolation, entity→owner→tenant) | A message without a valid owned `meeting_id` is rejected; no cross-tenant read of customer code |

## 4 · The full demo arc (the one narrative that must hold end-to-end)

A single continuous run: connect a real repo → Proxy reaches `ready` with a coverage % → invited to an architecture meeting → answers two grounded questions (one via grep/read, one via `get_dependents`) → asked to draft an impact summary → does it in the Workroom, verifies it, leaves it as a `needs_review` draft card → the meeting recycles the orchestrator once (chaos test) and continues seamlessly → cost stays in-envelope → meeting ends → notes file + the staged draft persist → a human accepts the draft after the call and it applies. Every law observed; every claim cited; every world-touching act gated.

## 5 · What this stage does NOT cover (deferred)

Per-service unit/acceptance criteria (Kiro/Spec Kit generation). Expansion features (the agentic knowledge map, world-touching PR applies, cross-meeting memory, proactivity) — verified when they land, not here. Load/scale testing beyond the concurrency smoke (S6).

---

*This skeleton exists so build-order step 8 has a target and the coherence audits have something to verify convergence against. It fills in with concrete step-tables during the build, once the services it composes are real.*
