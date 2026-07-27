# AMENDMENT PACK — landing Docs 06 & 07

*Everything upstream that must change for Doc 06 (Proactive) and Doc 07 (Post-Meeting Execution) to be buildable, with exact anchors. Nothing here is architecture: 06 and 07 are frozen as written. This is the landing checklist.*

**Read this first.** Four decisions need a founder call (Part 1). Eight patches then apply mechanically (Part 2). Two things earlier reviews asked for turned out to be **already satisfied or wrong**, and are retracted with evidence (Part 4) — do not spend time on them. Nothing in Part 3 may be touched at all.

---

## Part 1 · The four founder calls

| # | Decision | Recommendation | Why | Blocks |
|---|---|---|---|---|
| **F1** | Reuse the vacated Doc 07 slot, or renumber | **Reuse 07, with an explicit register amendment** | The 2026-07-16 cut of *Close & Trace* stands and is untouched; 07 is a free number and the deferred items being revived (*staged-drafts approval bundle*, *post-meeting pings*) are listed on that very row. Silent reuse is the only bad option. | P2 |
| **F2** | Slack as an optional reported channel | **Yes** | Google Meet chat is broadcast-only, so without it whisper-first has no private route on the platform most of our ICP uses, and Doc 07 has no channel that outlives the bot. One amendment, two needs. It is a channel in `channel-report`, never a second mouth. | P6 |
| **F3** | Permit an observability-only proactive env var against doc00's sealed oracle | **Yes** | The decision record is the calibration corpus, and the corpus is the sole gate on ever enabling voice (Doc 06 §3.5). Every unlogged week is calibration permanently lost. It must be an explicit permission, not a naming technicality. | P7 |
| **F4** | Pursue PR push in V1 | **No** | Draft-plus-branch-bundle ships real value with zero new permissions. Push needs `contents:write` plus tenant re-consent, and that conversation is far easier after a team has seen good drafts than before they have seen any. Doc 04 §3.16.1 and Doc 05 §3.8 both already encode never-push; V1 changes nothing. | nothing — this is a *decline* |

F4 is a decision to change nothing. It is listed so the answer is on the record rather than rediscovered by a builder who assumes a PR is obviously the point.

---

## Part 2 · The eight patches

### P1 — SPINE-REGISTER, Doc 06 row (line 18)

**Now:** `| 06 | Proactive | ⏸ CUT FROM V0 (founder call 2026-07-16) — design COMPLETE & captured below; V1 = pure addition, no re-pathing | — | V1 |`

**To:** status → `V1 — SPEC'D in 06-PROACTIVE.md (pure consumer of Docs 01–05; no re-pathing)`. Keep the 2026-07-16 cut date; it is still the reason this is V1.

*Why:* the row currently points at prose living inside the register. It must point at the doc.

### P2 — SPINE-REGISTER, Doc 07 row (line 19) · **needs F1**

**Now:** `| 07 | Close & Trace | ✂️ CUT AS A DOC (2026-07-16) … DEFERRED to V1: formatted show-your-work trace, staged-drafts approval bundle, decisions→index write-back (cross-meeting memory), post-meeting pings | — | — |`

**To:** keep the *Close & Trace* cut sentence verbatim — it is unchanged and Doc 07 explicitly does not reopen it. Then: move *staged-drafts approval bundle* and *post-meeting pings* out of the deferred list into `07-POST-MEETING-EXECUTION.md`; leave *formatted show-your-work trace* and *decisions→index write-back* deferred. Build order → `V1`.

*Why:* the slot is reused for a different capability. The register must say so in one place, once.

### P3 — SPINE-REGISTER, §"DEFERRED DESIGN — Proactive" (line 43)

Replace the block body with a pointer: *"Superseded by `06-PROACTIVE.md` (2026-07-24). The four V0 hooks named below are confirmed present and unchanged."* Keep the hooks list; delete the parallel design prose.

*Why:* two live descriptions of the same design is the exact drift that produced four review cycles. One wins; the other points.

**Note on the dial:** line 74 of the register specifies `off/semi/lead`. Doc 06 §3.4 now uses those exact words — earlier drafts said "normal," which was my drift, not the register's. No register change needed.

### P4 — CANONICAL-DECISIONS, seven new lines

In the existing scope-decision format:

- **D06.1** Proactive is V1 and a pure consumer of Docs 01–05: no transport, no ASR, no model seat, no sandbox, no delivery path, no re-pathing of any sealed bundle.
- **D06.2** The proactive gate *clears* contributions; Doc 04's wake-turn tools (`speak` / `send_chat` / `show_screen`) remain the sole delivery authority. The gate delivers nothing itself.
- **D06.3** The judge is the only situation→action mapping in the proactive path and it is model judgment; the gate is physics and floors (a clock, a similarity lookup, a boundary signal, an enum comparison). This is the Law 4 basis.
- **D06.4** Voice enablement per verdict class is a config value reviewed against the decision record, not a code branch. `voice_enabled_classes` ships empty.
- **D07.1** Post-meeting execution runs Doc 05's Workroom in a `meeting_runtime` worker with no media session, and its run durability is the ordinary `operation_runs` row Doc 05 §3.1 already defines — no new deployable, no new operation shape, no second run table.
- **D07.2** No post-meeting work executes before a named human approves the plan. Invariant 6 covers the artifact; this covers the run.
- **D07.3** V1 stages code-change drafts and never pushes. PR creation requires `contents:write` and tenant re-consent — a separate decision (F4: declined for V1).

### P5 — Doc 04 §3.16, the ordered close · **the only behavioural change to a V0 doc**

**Now (line 98):** `Proxy runs the ordered close (§3.16): freeze notes → trigger close pass (Doc 03) → destroy sandbox → complete the operation_runs row → teardown last`

**To:** `… ordered close (§3.16): freeze notes → surface banked proactive items + the clarify queue (Doc 06, V1; no-op when 06 is absent) → trigger close pass (Doc 03) → destroy sandbox → complete the operation_runs row → teardown last`

*Why:* banked contributions and clarifying questions must reach the room *before* Doc 03 reduces the ledger, so the answers land in the notes Doc 07 then reads. **Blast radius:** one inserted step, strictly additive, and a no-op while 06 is unbuilt — V0 behaviour is bit-identical. This is where V1's value shows even with voice disabled, and it is the single highest-value line in this pack.

### P6 — Doc 02 §3.10, the emitted signal surface · **needs F2**

**Now:** `… · meeting-end · channel-report(dm_available)`

**To:** `… · meeting-end · channel-report(dm_available, slack_dm_available)`

Plus, in §3 item 5 (*Chat*), one clause: outbound Slack DM where the tenant has connected it, delivered through Doc 04's `send_chat` like any other channel.

*Why:* Doc 02 §3.10 is explicitly the list Docs 03/04 are built against — "if a behavior upstream needs a signal not on it, the gap belongs here." Whisper-first needs a private route on Meet; Doc 07 needs one that outlives the bot. **Blast radius:** one optional field. No new sender: 06 and 07 both name-check that Slack is reachable *only* via `channel-report` + `send_chat`.

### P7 — Doc 00 §7, feature flags (line 214) · **needs F3**

**Now:** *"Feature flags — env vars, no table (V0). V0 has zero active runtime flags (`proactive_enabled` is cut with proactive; …)"*

**To:** keep every word, and append: *"V1 adds exactly one observability-only env var, `PROACTIVE_SHADOW`, which permits the proactive decision record to be written. It gates no behaviour, delivers nothing, and is not a feature flag; the doc00 oracle's assertion that no `proactive_enabled` exists in `libs/` and `services/` stands unchanged and is not weakened by it."*

*Why:* the sealed doc00 criteria grep for `proactive_enabled`. `PROACTIVE_SHADOW` would pass that grep on a naming technicality, which is the wrong reason to pass. Make the permission explicit so the oracle's intent is preserved rather than dodged. **Blast radius:** doc00 is sealed — this is a spec amendment plus a criteria regeneration, so it must be done deliberately and signed (F3), not slipped in.

### P8 — Doc 05, the `needs_review` collision (lines 56, 309, 333) · **sharper than earlier stated**

Doc 05 uses `needs_review` for **two different fields, and only one is wrong.**

**Leave alone — correct as written:** the *envelope* `status="needs_review"` at §3.7 ① and ③. CANONICAL §1.2 governs it and it is a valid envelope value.

**Fix — three draft-side sites:** line 56 (`returns a draft_id with status=needs_review`), line 309 (same phrasing in §3.8), and line 333 (the literal `return ok({"draft_id": …, "status": "needs_review"})`). The `staged_drafts` row enum is `proposed | accepted | rejected | applied` (CANONICAL §4), and **Doc 04 §3.16.1 already reads that row as `status='proposed'`** — so Doc 05's prose contradicts both CANONICAL and Doc 04 today, independent of anything in 06/07.

**To:** the row persists at `'proposed'`; `propose_change` returns `{"draft_id": …, "status": "proposed"}`. If a builder wants the tool return to also carry review state, name that field something other than `status`.

*Why:* three spellings for one concept is how a fifth status gets invented. **This is a pre-existing repo bug that 06/07 surfaced, not one they caused** — worth fixing regardless of whether 06 and 07 ever ship.

### P9 — PLATFORM-ADOPTION, stale flag assumption (line 389)

**Now:** *"DB-backed feature flags 4-tier (VI.4) — adopt a simple flags table + env floor for the 2–3 flags we actually need (`durable_meeting_sessions`, `proactive_enabled`)."*

**To:** annotate — *"SUPERSEDED: Doc 00 §7 cut the flags table for V0; both named flags are gone (`durable_meeting_sessions` with the Tier-2 mirror, `proactive_enabled` with proactive). V1 proactive uses one observability-only env var, not a table."*

*Why:* it is a catalog, not a spec, so the stakes are low — but it will mislead the next reader into rebuilding a table doc00's sealed criteria test for the absence of.

---

## Part 3 · Do not touch

Each of these was decided across several review rounds. They are listed so nobody relitigates them mid-build.

- **No new transport, ASR, TTS, sandbox, task queue, execution engine, or messaging path.** 06 and 07 are consumers of Docs 01–05, full stop.
- **No pgvector or embeddings fallback.** AGENTS.md rejects them by name; the dependency graph, LSP, and `code_intel` are the floor.
- **No direct Slack sender inside 06 or 07.** Slack exists only as a `channel-report` entry delivered by `send_chat`. Sole delivery authority is not negotiable.
- **No PR push in V1** (F4). Staged drafts only.
- **Do not reopen close as a subsystem.** Doc 07 is strictly post-close; the only close change in this pack is P5, and it belongs to Doc 06.
- **`post_meeting_tasks` is lifecycle state, never a second run table.** `operation_runs` remains the only execution durability row — this is the `workroom_tasks` prohibition, and it applies here too.
- **`UNRESOLVED` stays a real owner value.** It is what stops fake ownership inference.
- **`voice_enabled_classes` ships empty.** The single best anti-footgun control in the design.

---

## Part 4 · Retractions — two things not to do

**R1 — The Doc 04 accept-handler needs no amendment.** Earlier drafts of Doc 07 requested extending it to an out-of-meeting context. **Already satisfied.** Doc 04 §3.16.1 exists precisely because "the sandbox's in-memory review session dies at teardown, so a human accepting *after* the call needs durable storage," and it is reached by the authenticated `control_plane` route `POST /m/{meeting_id}/drafts/{draft_id}/accept` — not the meeting WS funnel (line 661 says so explicitly). It already records approval and exposes the bundle for a `code-change` draft without pushing. **Residue:** one *confirm-at-build* — the handler must execute in `control_plane`, not inside a torn-down harness process. No spec change.

**R2 — Doc 03's event enum needs no `task_request` class.** An earlier draft called this a doc03 seal blocker. `action-item-created` already covers it. **There is no doc03 seal blocker.** Retained here for auditability because it was escalated once already.

---

## Part 5 · Order of operations

1. **Take F1–F4.** Nothing else starts until the four calls are made; five of the eight patches depend on them.
2. **Land P8 and P9 immediately** — neither depends on any decision, and P8 fixes a live contradiction between Doc 05, Doc 04, and CANONICAL that exists today.
3. **Land P1–P4** (register + CANONICAL). This is what makes 06 and 07 authoritative rather than proposals.
4. **Land P5, P6, P7** (the three doc amendments). P7 requires regenerating doc00's criteria bundle — treat it as a sealed-bundle change with the ceremony that implies.
5. **Then generate `acceptance/doc06/` and `acceptance/doc07/`** from the Appendix C seeds, per AGENTS.md. **The next artifact for proactive is a criteria bundle, not another revision of these docs.**

**Verification that the pack landed correctly:** no live proactive prose remains in SPINE-REGISTER (P3) · `grep -rn "needs_review" 05-WORKROOM.md` returns only envelope sites (P8) · `grep -rn "proactive_enabled" libs/ services/` still returns zero, and doc00's oracle still asserts it (P7 preserved the intent) · Doc 04 §3.16's inserted step is a no-op with 06 absent (P5) · `channel-report` gained one optional field and no new sender exists anywhere (P6).
