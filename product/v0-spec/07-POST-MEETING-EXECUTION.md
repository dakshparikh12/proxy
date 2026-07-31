# Doc 07 — Post-Meeting Execution (the work that starts itself)

*Build order: V1, alongside 06. Consumes the output of Doc 03's close pass and Doc 06's WORK intake; triages, plans, and — only after a named human approves — dispatches into Doc 05's Workroom through Doc 04's existing bundle. It adds no sandbox, no agent framework, no second execution engine, no task queue. Acceptance criteria are generated into `acceptance/doc07/` per AGENTS.md — Appendix C is generator input, not spec body.*

> **Slot note (founder call required).** Doc 07 was cut as a doc on 2026-07-16, with V0 close folded into Doc 03 §3.7 and Doc 04 §3.16. **That cut stands and is not reopened here.** This doc reuses the vacant number for a different V1 capability the same register lists as deferred — *staged-drafts approval bundle* and *post-meeting pings*. The content is slot-agnostic; if reusing the number is undesirable, renumber it. Either way the reuse should be an explicit register amendment, never silent.


> **Before building:** this doc depends on upstream amendments that must land first (Appendix B). The exact patches, anchors, and apply order are in `AMENDMENTS-06-07.md`. Do not begin implementation until the founder calls in that pack are made.
---

## 1 · What we're building

The meeting ends and the work starts. Doc 03's close pass already produces decisions, action items and open questions in publishable form. Today a human reads that record and goes and does the work. **This doc makes Proxy do the first pass** — triage each action item, ask about the ones it cannot safely start, plan the ones it can, get a named human's approval on that plan, run it in the Workroom, and report to whoever owns it.

Every existing floor holds without exception. The Workroom does the work. `propose_change` stages the artifact. A named human accepts. Nothing lands in the world unapproved — the only difference is that nobody had to file the ticket.

**When it's done:** the notes link posts at close exactly as it does today, and by the time the team is back at their desks there are drafts waiting — each traceable to a line in the meeting, each a staged artifact with a receipt, each owned by a name that came from the room rather than a guess.

**Not built here.** The close pass and the permanent record (Doc 03 §3.7) · the ordered close sequence (Doc 04 §3.16) · sandboxes, plan artifacts, critics, verifiers, `propose_change`, staged drafts (Doc 05) · the accept handler (Doc 04) · in-meeting judgment (Doc 06) · draft cards and approval UI (Doc 08).

**Excluded by design:** starting work nobody asked for · guessing an owner · executing anything before a named human approves · pushing to a customer's repo (§3.7).

---

## 2 · The boundary with the close — stated first, because it is the easiest thing to get wrong

The close is Doc 03's and Doc 04's, and **this doc does not participate in it, extend it, delay it, or replace any part of it.**

Doc 04 §3.16 runs the ordered close. Doc 03 §3.7 reduces the folded ledger into the final object, renders the markdown, writes it to GCS create-only, posts the link in chat, and then the bot leaves. That sequence is untouched. Nothing here may hold the bot in the meeting, insert a step, or write to the notes object.

**This doc begins after the record is written, and only reads it.** The close output is intake — decisions, action items, open questions — alongside anything Doc 06's gate recorded as WORK during the meeting. Both are read; neither is modified. If this component fails entirely, the close is unaffected and the meeting record is identical.

The one adjacency is Doc 06's, not this doc's: Doc 06 asks its banked clarifying questions during the ordered close, and their answers land in the notes before the close pass reduces. This doc then reads those answers like any other note content. That amendment belongs to Doc 06 (its Appendix B) and is requested there.

---

## 3 · How it should behave

### 3.1 Intake and triage

The close output is read as intake, once, after the record is written. Each action item receives exactly one tier:

| Tier | Meaning | Produces |
|---|---|---|
| informational | worth recording, nothing follows | nothing |
| question | cannot proceed without an answer | a clarifying question |
| ticket | a human should do this | a staged task record |
| ticket + plan | thinking helps; building should not start | a plan for a human |
| ticket + plan + draft | narrow, concrete, safely startable | a plan, then a Workroom draft |

Tiering is model judgment over the close output with the notes as context — one structured call on the `sonnet` seat. **The conditions for the draft tier are policy, not mechanism:** a concrete scope, a clear area of the codebase, small enough to review in one sitting, no material ambiguity, and a stated way to tell it worked. They live in the prompt as the standard a task must meet, and whether the tier is available at all is one config value. Code owns the state machine, the caps, and the approval gate — not the judgment of which tier an item deserves (Law 4). **When in doubt, drop a tier:** an unhelpful ticket costs a glance, an unwanted draft costs trust.

### 3.2 Ownership

An owner comes from the room or the item is **UNRESOLVED** — a real value, distinct from empty, that holds the item at the question tier. Proxy never infers an owner from seniority, who talked most, or who last touched the file. *"Who owns this?"* is cheap at close and expensive to get wrong.

### 3.3 Ambiguity stops the line

An item without an owner, a scope, or a way to know it is done does not get a plan. It becomes a clarifying question written to the same `clarify_items` record Doc 06 uses. Most were already asked in the room at close and already have answers. For the rest, routing is mechanical, not judged: the follow-up goes to the person the notes attribute the item to — its speaker or its named owner — over the configured out-of-meeting channel, if the Doc 06 Appendix B channel amendment has landed. With no attributed person or no such channel, the question remains pending on the task record and surfaces on the draft cards. The item waits either way.

### 3.4 The plan and the approval gate — the safety boundary of this doc

For every surviving item, Proxy writes a plan: the task in one line · why it exists, with the meeting reference · the owner · assumptions · risks · what "done" looks like · the files it expects to touch · the steps · a confidence signal.

The owner may **approve · edit · split · downgrade to a ticket · reject.** Approval is recorded with a name and a timestamp.

*(**Build note, 2026-07-29.** Four of the five are built: **approve** (the gate, §3.4 / D07.2), **reject** (task → `DISCARDED`, nothing runs), **downgrade to a ticket** (tier → `ticket`, back to `TRIAGED`, plan and expiry clock cleared — the item stays alive and visible, with nothing to approve and nothing to dispatch), and **edit** (plan rewritten, stays `PLANNED`, approval **not** granted or carried over, and the `plan_expiry` clock restarts because an edited plan is a new plan awaiting a new decision).*

***`split` is DEFERRED*** *— a founder call, recorded here rather than left as an omission. It is the only one of the five that is not a state write. It needs: a parent/child relation `post_meeting_tasks` has no column for (a migration, plus a ruling on whether the parent stays non-terminal while children run — if it does it holds a dispatch slot, if it does not the children have no owner record above them); `max_tasks_per_meeting` accounting across the split (one task becoming four either consumes four slots or the cap stops meaning what it says); and a rule for children that disagree (three `ACCEPTED` and one `CHANGES_REQUESTED` is not obviously any single parent outcome). Estimated 1–1.5 days including the migration. Nobody has requested it and there are no users yet. An owner wanting a split can reject the plan, or edit it down to the part that is actually startable.)*

**Until that approval exists, no sandbox starts, no model does work beyond triage and the plan itself, and no durable write occurs outside the task's own record — with exactly one carve-out: `clarify_items`, which §3.3 writes while the task is in CLARIFYING** (founder ruling on contradiction C-D, 2026-07-27; the carve-out is closed — `clarify_items` is the only table exempt, and asking a question is not a world-change) — the plan text lives on that record. This is Law 3 and Invariant 6 (every world-change is a staged draft behind a named human) applied to the *run*: Invariant 6 already covers the artifact at the end; this covers the start. A plan nobody answers expires quietly after `plan_expiry`. Proxy does not nag and never proceeds by default.

### 3.5 Execution

An approved task is dispatched through **Doc 04's existing bundle** — the item verbatim, the owner, the notes reference, the transcript tail, a task id — into **Doc 05's Workroom, unchanged**. Everything about how the work happens is Doc 05's: the plan artifact, the critic pass, checkpoints and git read-back, the separate verifier requiring running evidence, no-progress detection, bounded replan, and the honest partial when it cannot finish. It returns the standard envelope.

Where it runs: post-meeting work outlives the meeting, and the meeting harness tears down at close. **A dispatched task is `(db, meeting_id)` handed to Doc 05's `SessionDriver`** — no transport, no Scribe, no tile; just the Workroom and the notes reader. No new deployable.

*(**Amendment P11, 2026-07-28.** This paragraph previously specified a `meeting_runtime` worker "with no media session", and a 2026-07-27 clarification made that a `media_session=False` mode on `MeetingRuntime`. Both are withdrawn: they described a hosting model the Workroom does not use. `SessionDriver` (Doc 05, `services/workroom/src/workroom/session.py`) takes `provider, sandbox_fs, store, db, abort_registry, model, disposition, …` and resolves the rest **itself** from `(db, meeting_id)`:*

*• the **notes reader** takes a `db`-shaped acquirer plus `meeting_id`; • the **`code_intel` server** is built fresh per task from `db` — `_resolve_code_intel_server` explicitly refuses a shared or process-global server, so a runtime-held `code_intel_ctx` would be rejected rather than reused; • the **`operation_runs` claim** is `dispatch_workroom(db, bundle)`; • the **warm sandbox** is `sandbox_provider.provision(meeting_id=…)`, idempotent per meeting.*

*`services/workroom/` contains no reference to `MeetingRuntime`. **No `MeetingRuntime` object is required to run a dispatched task**, and the no-media mode has been reverted. This amendment is deliberately silent on where the **live, in-meeting** dispatch path is hosted — Doc 04 §112 owns that (the harness's registered tool functions and the completion callback) and is unchanged by this.*

*The contradiction recorded in `acceptance/doc07/manifest.yaml`'s `assurance_limits` is **not** resolved by this amendment. AC-PME-09/AC-PME-10 and their NEG pairs remain blocked, correctly: the blocker is one layer below the worker — see `docs/gaps/DOC04-WORKROOM-DISPATCH-UNWIRED.md`.)*

**The run's durability is exactly Doc 05 §3.1's:** the task *is* an `operation_runs` row — `scope_id` = the **meeting id** (cast to text at the one call site), `operation_type = 'workroom:{task_id}'`, `progress` jsonb = the bundle, `result_ref` = the terminal envelope — with the same atomic claim, heartbeat, and reconcile. *(**Amendment P10, 2026-07-27, founder ruling on contradiction C-A:** an earlier draft of this line put the task id in `scope_id` as well as in `operation_type`, which duplicated identity across both columns and disagreed with the built dispatch path. The code is right and the spec was wrong: `services/harness/src/harness/dispatch.py:129-145` and `libs/ops/src/ops/cost.py:323` both claim with `scope_id` = meeting id and `operation_type` = `workroom:{task_id}`. **No code changes; this doc conformed to it.** The partial unique index `operation_runs_one_running_per_scope` is on `(scope_id, operation_type)`, so this split is what makes "one running row per meeting per task" the actual guarantee.)* **This doc's `post_meeting_tasks` table (§4) is not the forbidden `workroom_tasks` table by another name:** the run's record stays the `operation_runs` row, unduplicated; `post_meeting_tasks` holds the product lifecycle that exists *before* any run (tier, owner, plan, approval) and *after* it (outcome), and most rows — informational, question, ticket — never spawn a run at all. A crashed worker is reclaimed exactly as a crashed meeting is.

Limits, all config: `max_concurrent_tasks` per tenant, `max_tasks_per_meeting`, and a per-task cost ceiling checked before the sandbox spins — the same pre-dispatch estimate gate Doc 04 already applies to live asks. Exceeding it asks the owner rather than spending.

### 3.6 Reporting

Reports go to the owner and to anyone the room explicitly named. **Channel selection reuses Doc 02's `channel-report` exactly as in-meeting delivery does** — the bot has left, so platform chat is gone, and what remains is an out-of-meeting channel only if one exists: Slack, *if* the Doc 06 Appendix B amendment has landed and the tenant connected it; otherwise the draft cards surface (Doc 08). There is no messaging path here that does not already exist, and this doc defines no channel of its own.

**Cadence is deliberately quiet:** on completion, on a question, on failure, and on the pre-dispatch cost ask. Not on every step. Silence means it is running.

Each report carries the headline, the receipts, the draft link, and a confidence signal read directly off the envelope status — `done` reads as confident, `partial` as needs-attention, `needs_clarification` as blocked-on-you (reported as a question, never a failure), `failed` as plainly failed with the reason. The signal never rounds up.

### 3.7 What lands in the world

The artifact is a **staged code-change draft**: the multi-file bundle in GCS with a `staged_drafts` row, row `status = 'proposed'` (CANONICAL §4's enum: `proposed | accepted | rejected | applied` — distinct from the *envelope* `status`, whose `needs_review` value is valid and unrelated), a `draft_id`, and the diff downloadable as a branch bundle. Doc 04's accept handler records approval and exposes it. **It does not push.**

That is not a limitation invented here — Doc 05 §3.8 states it, and the GitHub App is installed read-only. **Opening a pull request in a customer's repo requires `contents:write`, a permission escalation and tenant re-consent.** That is a product and trust decision, and it belongs in front of both founders rather than arriving behind an assumed "obviously it opens a PR." Until then the final push remains the human's, and it is thirty seconds.

### 3.8 What this doc is permitted to do

| | Permitted | Not permitted |
|---|---|---|
| **Read** | close-pass output, notes via the internal reader, Doc 06 WORK intake | modifying the notes object or the close record |
| **Judge** | tier and plan an item | executing on its own judgment |
| **Execute** | dispatch an approved task into the Workroom via Doc 04's bundle | any second execution engine, sandbox, or agent framework |
| **Write durable state** | its own task record; `clarify_items`, co-owned with Doc 06 | any table this doc does not own: notes, transcript, `meeting_cost`, and the rest |
| **Stage** | `propose_change` through the Workroom, as Doc 05 defines it | writing `staged_drafts` directly |
| **Accept / push** | nothing — accept is Doc 04's handler and a human's click | applying a draft; pushing; opening a PR |
| **Send** | reports over channels present in `channel-report` | any new messaging path |

### 3.9 State

```
EXTRACTED → TRIAGED → CLARIFYING → PLANNED → APPROVED → RUNNING → DRAFTED
                                                     → { ACCEPTED | CHANGES_REQUESTED | DISCARDED }
```

Invariants worth testing as invariants: **CLARIFYING** is entered only when unresolved questions exist · **APPROVED** is written only by a named human's action · **RUNNING** is entered only from APPROVED · a task in any state before APPROVED has written nothing durable outside its own record. (`CHANGES_REQUESTED` is the reviewer's "request changes" outcome — spelled in full so no builder mistakes it for a diff-content state.)

### 3.10 The laws, concretely

**Law 1** — every draft carries receipts; a task that cannot ground its claims returns partial, not confident. **Law 2** — `partial` and `failed` are reported as themselves. **Law 3** — the approval gate is Law 3 for this doc; every world-touching act remains a staged draft behind a human click. **Law 4** — triage and planning are model judgment; code owns the state machine, the caps, and the gate, and there is no rule table mapping item text to a tier. **Law 5** — approval and results are reachable by glance.

---

## 4 · Stack

**No new stack.** Triage and planning = `claude-sonnet-4-6` through `libs/llm`. Execution = Doc 05's Workroom, unchanged, in a `meeting_runtime` worker without a media session. Run durability = the `operation_runs` row exactly as Doc 05 §3.1 defines it. Artifacts = `propose_change` → GCS + `staged_drafts`. Approval = Doc 04's accept handler with Doc 08's draft cards. Reporting = channels present in `channel-report`.

**Never to be rebuilt here:** a second coding agent or agent framework · a separate sandbox provider (E2B is Workroom-only, and this *is* the Workroom) · a task queue, scheduler, or broker (`operation_runs` holds one run, not a plan of runs) · an issue-tracker integration in V1 · direct pushes or PR creation.

**One new durable store**, following existing conventions (snake_case, `tenant_id` per Invariant 9, `meeting_id` as `uuid` per CANONICAL §11.2). A plain record with no hidden semantics — nothing reads it to make a runtime decision:

```sql
post_meeting_tasks  -- meeting_id, source ('close-item' | 'doc06-work'), item_ref, tier,
                    -- owner (or UNRESOLVED), state, plan, approved_by, approved_at,
                    -- operation_ref, draft_id, cost, outcome
```

`clarify_items` is co-owned with Doc 06 and defined there. No labels table — the `outcome` column carries the review signal, per CANONICAL §12.10 ("reject the table zoo").

**New config keys** under a `[post_meeting]` section in `config/defaults.toml`, each one value + unit + range in the established style: `max_concurrent_tasks` · `max_tasks_per_meeting` · `task_cost_ceiling` · `plan_expiry` · `draft_tier_enabled`.

**Deliberately deferred:** issue-tracker export (Linear, Jira) — the staged task record is the V1 surface, and export is a connector, not a capability · cross-meeting memory and decisions folding back into the index, which the register already holds as Expansion · scheduled or recurring work · parallelism beyond the concurrency cap.

---

## 5 · One correct interaction, end to end

The meeting ends with three action items. The close pass writes the record, the link posts in chat, the bot leaves — untouched by anything here.

The first — *"Sam to bump the retry ceiling on checkout to 5"* — clears the draft tier: one owner, one concrete change, a clear area, an obvious test. Proxy plans it (touching `payments/checkout/retry.py`; assumption: the existing retry test covers the path; done when it passes at the new ceiling) and sends it to Sam, who approves from his phone on the walk back. The task is claimed, the Workroom edits and runs the test, and `propose_change` stages a two-file draft with the failing-then-passing test as its receipt. Sam gets one message: what changed, the receipt, the draft link, green.

The second — *"someone should look at the checkout error spike"* — has no owner and no definition of done. It never becomes a plan. It becomes one question, routed to the person the notes attribute it to: *"Who's taking the error-spike investigation, and what would make it done?"* The item waits.

The third — *"we should redesign the retry architecture"* — is real work and far too large. Triage stops it at ticket-plus-plan: Proxy writes what it understands of the problem, the files involved, and the open questions, and records it as a ticket-plus-plan on the task record — no issue tracker exists in V1, and none is implied. No sandbox starts. Nobody wakes up to an unrequested architectural rewrite.

---

## Appendix A — Register and CANONICAL updates this doc requests

*Requests, not authorizations. Each needs the normal amendment path.*

- **SPINE-REGISTER:** Doc 07 returns as a V1 doc with the scope above; the 2026-07-16 cut of *Close & Trace* stands unchanged and is explicitly not reopened. Move *staged-drafts approval bundle* and *post-meeting pings* from that entry's deferred list into this doc.
- **CANONICAL-DECISIONS**, three lines: **D07.1** post-meeting execution runs the Workroom in a `meeting_runtime` worker without a media session, recorded as the ordinary `operation_runs` row Doc 05 §3.1 defines — no new deployable, no new operation shape · **D07.2** no work executes before a named human approves the plan · **D07.3** V1 stages code-change drafts and never pushes; PR creation requires `contents:write` and a separate founder decision.
- **Stale-prose fix requested (sharper than first stated):** Doc 05 uses `needs_review` for **two different fields**, and only one is wrong. The *envelope* `status="needs_review"` (§3.7 ①, ③) is correct and must stay. But Doc 05 §2 line 56, §3.8 line 309, and the code at line 333 have `propose_change` return the **draft** with `status=needs_review`, which collides with CANONICAL §4's `staged_drafts` enum and with Doc 04 §3.16.1, which already reads that row as `status='proposed'`. Fix the three draft-side sites; leave every envelope site alone. See the amendment pack, P8.

## Appendix B — Amendments and decisions required (each a separate founder call)

1. **Register slot reuse** — reviving 07 for post-meeting execution, per the note at the top.
2. ~~**Doc 04 accept handler** — extend to accept from an out-of-meeting context.~~ **Retracted: no amendment needed.** Doc 04 §3.16.1 already specifies acceptance *after* the call, reached by the authenticated `control_plane` route `POST /m/{meeting_id}/drafts/{draft_id}/accept`, against durably persisted rows and bundles — and §3.16.1 already states a `code-change` draft records approval and exposes the bundle without pushing. This doc needs nothing added. The only residue is a **confirm-at-build**: the handler must execute in `control_plane`, not inside a torn-down meeting harness process.
3. **Slack channel** — shared with Doc 06 Appendix B and requested there, not here. This doc needs *a* channel that outlives the bot; it does not care which, and defines none.
4. **PR-push decision** (product) — whether to pursue `contents:write` and per-tenant re-consent. Recommendation: not in V1. The draft-plus-bundle is genuinely useful, ships with zero new permissions, and the re-consent conversation is far easier once a team has seen good drafts than before they have seen any.

## Appendix C — Criteria seeds (generator input, not spec body)

WHEN the close pass completes THEN every action item SHALL receive exactly one tier AND the close record SHALL be unmodified · WHEN this component fails entirely THEN the close sequence and the meeting record SHALL be unaffected · WHEN an item lacks owner, scope, or a done-condition THEN it SHALL NOT be planned and SHALL become a clarifying question · WHEN a clarifying question has no attributed person or no out-of-meeting channel THEN it SHALL remain pending on the task record · WHEN ownership is ambiguous THEN owner SHALL be UNRESOLVED and never inferred · WHEN an item fails any draft-tier condition THEN it SHALL drop at least one tier · WHEN a plan has not been approved by a named human THEN no sandbox SHALL start, no model work beyond triage and the plan SHALL run, and no durable write SHALL occur outside the task's own record · WHEN a plan expires unanswered THEN the task SHALL close quietly without proceeding · WHEN a task is dispatched THEN it SHALL use Doc 04's existing bundle and Doc 05's Workroom, and no second execution path SHALL exist · WHEN a task runs THEN its durability SHALL be one `operation_runs` row per Doc 05 §3.1 and it SHALL survive worker recycle · WHEN concurrency or per-meeting caps are reached THEN further dispatch SHALL wait · WHEN estimated cost exceeds the ceiling THEN the owner SHALL be asked before the sandbox spins · WHEN the Workroom returns `needs_clarification` THEN it SHALL be reported as a question, not a failure · WHEN the Workroom returns `partial` or `failed` THEN the report SHALL say so plainly and the confidence signal SHALL NOT round up · WHEN a task produces an artifact THEN it SHALL be a `staged_drafts` row at `status='proposed'` with receipts and a `draft_id`, and it SHALL NOT be pushed · WHEN a report is sent THEN it SHALL use a channel present in `channel-report` and SHALL go to the owner and any explicitly named recipients, on completion, question, failure, or cost ask only.
