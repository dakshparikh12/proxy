# Doc 06 — Proactive (the noticed door)

*Build order: V1, after the V0 spine completes. Consumes Doc 03's material-change events and enriched notes; verifies through Doc 01's `code_intel` tools or Doc 05's read-only disposition; delivers **only** by handing a cleared contribution to Doc 04, whose wake-turn tools remain the sole mouth. It adds no transport, no ASR, no model seat, no sandbox, no delivery path. This doc supersedes the deferred-proactive prose in SPINE-REGISTER §"DEFERRED DESIGN — Proactive"; the register entry should point here once that amendment lands. Acceptance criteria are generated into `acceptance/doc06/` per AGENTS.md — Appendix C is generator input, not spec body.*


> **Before building:** this doc depends on upstream amendments that must land first (Appendix B). The exact patches, anchors, and apply order are in `AMENDMENTS-06-07.md`. Do not begin implementation until the founder calls in that pack are made.
---

## 1 · What we're building

Proxy already answers when asked. This is the second door: Proxy **noticing** — a claim that contradicts the code, a decision going final on a wrong number, a gap between the stated plan and what the repo actually does (a gap established only through §3.2 verification, never by this doc analyzing anything itself) — and choosing whether that is worth a room's attention right now.

The hooks for it are already built and dormant, and the register names all four. Doc 03 emits material-change events on a standing pipe. Doc 04 wrote its delivery priorities generically (`human answer > hard-floor proactive > gated proactive`) precisely so this could land. Doc 05 describes, in its own text, "a proactive read-tier task" as a disposition of the existing Workroom. Doc 02 draws the tile "has-something" signal and reports which chat channels exist. **What is missing is the consumer: a judge, a gate, and the rules for what may be said. That is this doc, and that is all of it.**

**When it's done:** a wrong number stated on the way to a decision gets caught, verified against the current clone, and surfaced at a natural boundary with a `file:line` receipt — or, if the room moved on or someone already made the point, silently banked and mentioned at close. Most material events produce nothing; quiet is the default the register designed for.

**Not built here.** Transport, chat channels, boundary and barge-in signals (Doc 02) · comprehension, the notes object, the event emitter, the close pass (Doc 03) · the mouth, the wake turn, delivery ordering, the cost breaker, the ordered close (Doc 04) · code search, the graph, `file:line` retrieval (Doc 01) · sandboxed work, plans, verifiers, `propose_change` (Doc 05) · rendering (Doc 08) · post-meeting execution (Doc 07).

**Excluded by the register's locked deferred design, upheld here:** process-policing (who's talking, timekeeping, tangents) · meeting-type modes · speaking unverified · interrupting mid-sentence.

---

## 2 · How it works

A claim lands in the notes. Doc 03's emitter fires `claim-landed (checkable)` with the entry and its focused context slice, on the standing pipe that already runs — no agent, no cost, no Proxy involvement.

**The judge reads it and decides whether anything matters.** One structured Haiku call on the seat Doc 04 §3.12 already reserves: *would a sharp, respected participant speak here — and if so, what, and in what general direction?* It reads the situation from the enriched notes themselves — firmness, reversibility, observed-vs-inferred, contradiction — never from a meeting-type label. The lenses are inspiration, not a menu: validate · correct · quantify · gap-or-risk · ideate · unblock · encourage.

**If it wants to speak, it must first be right.** Cheap structural checks go to the `code_intel` tools over the host-side internal API — the same hop Proxy's direct answers already make. Anything heavier becomes a read-only Workroom task and comes back in the standard envelope. Verification failure or timeout kills the contribution. Nothing unverified reaches the room; for something nobody asked for, Law 1 is absolute.

**Then the gate decides whether it can be said now.** That is the whole division of labour, in one line: **the judge decides whether something matters; the gate decides whether it can be said — and the gate never decides that something matters.** Its four questions are all mechanical: is the entry still current, has the point already been made, does the verdict clear the room's dial, and has Doc 02 emitted a boundary. If all four hold, the gate hands **one cleared contribution to Doc 04**. If the moment passed but the point stands, it banks it for the close. Otherwise it drops it and records why.

**Doc 04's mouth does the rest.** A cleared contribution is a wake trigger like any other: Proxy takes one turn, sees the contribution plus current room state, and calls `speak` / `send_chat` / `show_screen`. Its existing priority rule holds unchanged. **The gate delivers nothing itself.** It clears; Proxy speaks. That is what keeps the wake-turn tools the sole delivery authority, and it is why proactive inherits barge-in, quiet mode, the spoken register, and late-result re-checking without restating any of them.

**Three situations skip only the model judgment.** A decision going final on a checkable claim Proxy can show is wrong; a contradiction — which Doc 03 already detects deterministically from the claims ledger and emits as `contradiction-detected`; and a commitment on a wrong number. These hard floors skip the judge and nothing else: verification, the boundary, quiet mode, and Doc 04's sole-mouth authority all hold. Their "jumping the queue" is not a new scheduler — it is Doc 04's existing priority order, `human answer > hard-floor proactive > gated proactive`, doing exactly what it was written to do.

**At meeting end,** banked items and unanswered questions ride out on Doc 04's ordered close, before Doc 03's close pass writes the record — *this is the Appendix B close amendment, requested and not assumed.* What is still unanswered becomes Doc 07's intake.

---

## 3 · How it should behave

### 3.1 The judge — deciding whether something matters

One Haiku turn per event, structured output, cached prefix. It sees only Doc 03's material-change classes — `claim-landed (checkable)`, `decision-forming`, `decision-final`, `contradiction-detected`, `action-item-created`, `question-opened/closed`. Chitchat emits no events, so the judge never sees it.

```
verdict    : NOTHING | CORRECTION | GAP | FLOOR | WORK | CLARIFY
severity   : LOW | MEDIUM | HIGH
confidence : float [0,1]
direction  : what to check and roughly what to say — never the final wording
event_ref  : the material-change event
```

WORK and CLARIFY are dispositions — they are routed onward (§3.3) and are never spoken as-is.

**NOTHING is the default outcome, not an exception path.** A NOTHING verdict writes one decision row, and nothing else happens — no delivery, no queue, no bank, no retry, no other state. Ambiguity resolves to NOTHING. A judge timeout, malformed output, or error resolves to NOTHING. A non-NOTHING verdict below `judge_confidence_floor` also resolves to a row and nothing else. **Every quiet path is the same quiet path.**

**Severity means one thing:** HIGH iff the wrong or missing information blocks a decision being made now, or blocks work from starting safely. Everything else is MEDIUM or LOW. Severity, not confidence, is what earns escalation.

### 3.2 Verification — grounded or silent

Cheap structural checks use `code_intel` (`get_dependents` · `who_writes` · `list_entry_points` · `grep` · `read`). Anything needing real reasoning becomes a Workroom task on the read-only disposition — no write tools, no `propose_change`. The result carries a `file:line` receipt or the contribution is dropped.

Two consequences worth stating plainly. **Uncertainty may leave only as a question, never as an assertion** — a CLARIFY contribution is allowed to be unsure, because asking is honest where asserting would not be. And **a floor without complete proof is not a floor** — it degrades to a gated correction and takes its place in the queue. Floor proof is four fields: the claim, the grounded source, the exact `file:line` or doc reference, and the delta between what the code says and what the room said.

### 3.3 The gate — deciding whether it can be said

Deterministic, no model call. It cannot originate content — only clear, hold, bank, or drop.

| Predicate | Rule |
|---|---|
| still-current | `now − event.ts < relevance_window`, and no later note delta supersedes the entry |
| not-already-said | a deterministic similarity over contribution text — never a model call: no contribution cleared this meeting matches within `dedupe_similarity`, and no human has made the point since the event |
| clears-dial | the verdict is in the dial's allowed set (§3.4) |
| at-a-boundary | Doc 02's `boundary` signal is open — never mid-utterance. (Doc 02 §7 sources it from AAI `end_of_turn`, with Smart Turn v3 as the confirm-at-build fallback; this doc consumes `boundary` and is indifferent to which.) |

**Routing.** FLOOR with complete proof clears immediately, bypassing dial and confidence floor but never boundary, quiet, or the rate budget · CORRECTION and GAP clear when above the confidence floor with all four predicates true · CLARIFY at HIGH severity on a decision or action item clears now, whisper-first, because the blocked decision is happening in this minute · CLARIFY otherwise joins the close queue · WORK is **recorded for Doc 07 intake** — the gate dispatches nothing and never interrupts · a valid contribution whose moment passed banks for the close, re-checking not-already-said before it surfaces · everything else drops with a reason code.

**Rate:** at most one non-floor clearance per `rate_window`. Floors are exempt but still counted.

**Self-loop suppression, mechanical:** before the judge runs, an event is suppressed when its content matches a delivered contribution already on the decision record, using the same `dedupe_similarity`. Proxy does not react to being quoted back to itself.

### 3.4 The dial

Per-room, static, three positions: **off** (floors only) · **semi** (floors, corrections, gaps) · **lead** (all non-floor verdicts). One key in `config/defaults.toml`; room- or tenant-level overrides ride whatever override path Doc 00 provides — none is invented here. The dial never suppresses Doc 07 intake, because post-meeting work is not an interruption. Learned per-room dials stay deferred; they need a corpus that does not exist.

*The register's captured design specifies this dial (off / normal / lead). An earlier draft of this doc parked it; here it costs one enum and one comparison.*

### 3.5 Which channels a cleared contribution may use

Doc 04 chooses the channel from the contribution's own structure, exactly as it does for answers: headline to voice, detail to chat, artifact to screen. Two rules are specific to proactive.

**Whisper-first, where a private channel exists.** A contribution aimed at one person goes to them privately first, giving them the first move. The channel comes from Doc 02's `channel-report` for this meeting: platform DM where the platform supports it, otherwise the tile's "has-something" signal, otherwise hold. **A whisper never degrades silently to broadcast.** (Slack, if the tenant has connected it, appears in `channel-report` like any other channel — Appendix B; it is not a special-case messaging path.)

**Voice is enabled per verdict class by config, starting empty.** `voice_enabled_classes = []` means cleared contributions use private and text channels only. A class is added when the decision record shows enough labelled contributions at high enough precision for that class, and a false positive in the room removes it again. One config key, reviewed by humans; no code path changes.

### 3.6 What this doc is permitted to do

Stated once, plainly, because this is where misunderstanding is most expensive.

| | Permitted | Not permitted |
|---|---|---|
| **Speak / send / show** | hand one cleared contribution to Doc 04 | call any transport or channel directly; deliver anything itself |
| **Read code** | `code_intel` tools; Workroom read-only disposition | any write tool; `propose_change`; the sandbox |
| **Write durable state** | `proactive_decisions` (owned outright — labels and `human_response` are columns on it, not a third store) and `clarify_items` (co-owned: written here, completed by Doc 07). No others. | any table this doc does not own — notes, `staged_drafts`, transcript, `meeting_cost`, `operation_runs`, and the rest |
| **Stage or accept** | nothing | `propose_change`, the accept handler, any world-touching act |
| **Spend** | Haiku judge turns and verification, under `check_meeting_budget()` | anything outside the existing meter |

At the soft cap, gated contributions stop and Proxy says so in the room; at the hard cap, notes-only — the coupling Doc 04 §3.13 already specifies, unchanged. Floors survive the soft cap because they are the safety function.

### 3.7 Explaining itself

Every gate decision writes a reason code, including the decisions to stay quiet, and each renders to one plain line: *flagged — contradicts `<ref>`* · *couldn't verify, so I stayed out of it* · *someone already made the point* · *waited for a gap that didn't come* · *gone quiet — hit the budget cap*. Anyone may ask why Proxy spoke or why it didn't, and Proxy answers from the last relevant code through its normal mouth. A dismissed contribution is never raised again in that meeting; the dismissal is recorded as `human_response = 'dismissed'` on the decision row — it is the highest-value calibration signal the system gets.

### 3.8 The laws, concretely

**Law 1** — no contribution without a `file:line` receipt; unverified dies silently. **Law 2** — proactive claims carry the same resolved / lower-bound tags as answers. **Law 3** — barge-in stops speech under 200ms; "quiet" silences everything including floors; a human ask preempts any un-spoken contribution. **Law 4** — the judge is the *only* situation→action mapping and it is model judgment; the gate is a clock, a similarity lookup, a boundary signal, and an enum comparison, with no rule of the form "if the speaker says X." A predicate that ever needs judgment belongs in the judge. **Law 5** — every proactive surface is speakable or glanceable.

### 3.9 How the judge gets good

Every judge verdict and gate decision is recorded whether or not anything was delivered, including everything below the confidence floor. That record is the calibration corpus, the audit trail, and the input to §3.5's config change. It delivers nothing and cannot deliver anything; one env var controls whether it is written (Appendix B), observability-only.

Labels come from a small post-meeting review card — three to five sampled moments, thumbs up or down, about thirty seconds — written back onto the decision row (`label_value`, `labeled_by`). Without labels the thresholds stay at their defaults forever, so the card is the mechanism, not a nicety.

---

## 4 · Stack

**No new stack.** Judge = `claude-haiku-4-5` through `libs/llm`, one turn, structured output, cached prefix — the seat Doc 04 §3.12 lists as "proactive gate (V1 — dormant in V0)." Verification = Doc 01's `code_intel` internal API, or Doc 05's read-only disposition. Delivery = Doc 04's wake-turn tools over Doc 02's reported channels. Cost = the existing `meeting_cost` row and `check_meeting_budget()`. Durability = the meeting harness's existing `operation_runs` row; proactive adds no operation record of its own. Tracing = Langfuse where already wired.

**Never to be rebuilt here:** embeddings or vector search for verification (AGENTS.md rejects them; the dependency graph and LSP are the fallback) · a second ASR or turn-detection model (Doc 02 owns both signals) · any delivery path or notification service · a proactive sandbox (E2B is Workroom-only) · a feature-flag table (env vars, per Doc 00).

**Two new durable stores — exactly the two §3.6 permits** — following existing table conventions (snake_case, `tenant_id` per Invariant 9, `meeting_id` as `uuid` per CANONICAL §11.2). Both are plain records with no hidden semantics: nothing reads them to make a runtime decision.

```sql
proactive_decisions  -- one row per judge+gate cycle, including every NOTHING.
                     -- event_ref, verdict, severity, confidence, predicate results,
                     -- gate_action, reason_code, delivered, shadow, trace_ref,
                     -- human_response ('accepted'|'dismissed'|null),
                     -- label_value, labeled_by, labeled_at   (labels live here, not in a
                     --                                        separate table — CANONICAL §12.10)
clarify_items        -- question, kind, blocking_ref, urgency, answer, answered_by
                     -- written by Doc 06, read and completed by Doc 07
```

**New config keys** in `config/defaults.toml` under a `[proactive]` section, each one value + unit + range in the established style: `judge_confidence_floor` · `relevance_window` · `dedupe_similarity` · `rate_window` · `dial` · `voice_enabled_classes`.

---

## 5 · One correct interaction, end to end

Priya: *"Checkout's error rate is only 2% — let's ship Friday."* The Scribe lands the claim (firm, observed, unverified), binds *checkout* to `payments/checkout`, and fires `claim-landed (checkable)` and `decision-forming`. The judge wakes on the first: CORRECTION, HIGH — a decision is riding on a number. `code_intel` finds the metric emitter and returns the threshold constant with its line. The gate checks: still current at twelve seconds, not already said, clears the room's `semi` dial, and Doc 02 has just opened a boundary. It clears one contribution. Proxy wakes, sees the room still on topic, and speaks: *"Quick flag — the alert threshold in `payments/checkout/metrics.py:88` is 4%, not 2%. Detail's in chat."* The cited detail follows in chat; the decision row records the receipt.

Twenty minutes later Sam mentions the rate spiked yesterday. The ledger detects the contradiction structurally and fires the hard floor — but Priya has already restated the corrected number, so *not-already-said* fails and the gate drops it, recording `SILENT_ALREADY_SAID`. Nobody is told twice.

A third moment — Proxy is unsure who owns the Friday cutover — never becomes speech. It banks as a clarify item and rides out on the close (per the Appendix B amendment): *"Before I write this up: who's cutting over on Friday?"* The answer lands in the notes, and Doc 07 starts with an owner instead of a guess.

---

## Appendix A — Register and CANONICAL updates this doc requests

*Requests, not authorizations. Each needs the normal amendment path; this doc cannot enact its own.*

- **SPINE-REGISTER:** Doc 06 status → `V1 — designed, spec'd here`; the §"DEFERRED DESIGN — Proactive" prose is superseded and should point here. The four V0 hooks it names are confirmed present and unchanged.
- **CANONICAL-DECISIONS**, four lines in the existing scope-decision format: **D06.1** proactive is V1, a pure consumer of Docs 01–05, no re-pathing · **D06.2** the gate clears, Doc 04's wake-turn tools remain the sole delivery authority · **D06.3** the judge is the only situation→action mapping and the gate is physics and floors (the Law 4 basis) · **D06.4** voice enablement is a config value reviewed against the decision record, not a code path.
- **Retracted from earlier drafts of this doc:** the claim that Doc 03's event enum needs a new `task_request` class before sealing. `action-item-created` already covers it. **No doc03 seal blocker exists.**
- **Stale-prose fixes requested:** PLATFORM-ADOPTION.md:314/389 still assume a `proactive_enabled` feature-flags row that Doc 00 cut and doc00's sealed criteria test for absence — delete or annotate.

## Appendix B — Amendments required before build (each a separate founder call)

1. **Doc 04 §3.16, ordered close** — one added step before the notes are written: surface banked contributions and the clarify queue. Additive; it is where V1's value shows even with voice disabled.
2. **Doc 02 §3.4, channels** — Slack as an optional per-tenant channel *reported through the existing `channel-report`* and delivered by Doc 04's `send_chat` like any other channel. No new messaging system. It earns its place because Google Meet chat is broadcast-only — without it, whisper-first has no private channel on the platform most of our ICP uses — and because Doc 07 needs a channel that outlives the bot. One amendment, two needs.
3. **Doc 00 feature flags** — one env var permitting the decision record to be written, observability-only. This touches doc00's sealed oracle, which asserts no `proactive_enabled` in `libs/` and `services/`. The name differs and the semantics are observability-only, but **the oracle must explicitly permit it** rather than the build passing on a naming technicality. Recommended: yes — the corpus gates §3.5, and every unlogged week is calibration lost.

## Appendix C — Criteria seeds (generator input, not spec body)

WHEN a material-change event fires THEN the judge SHALL return one verdict within one turn, and low-signal events SHALL predominantly return NOTHING · WHEN the judge is ambiguous, errors, or times out THEN the outcome SHALL be NOTHING · WHEN the verdict is NOTHING THEN exactly one decision row SHALL be written and no other state SHALL change · WHEN confidence is below the floor THEN the item SHALL be recorded and never delivered · WHEN a contribution is cleared THEN it SHALL carry a `file:line` receipt · WHEN verification fails THEN nothing SHALL reach the room · WHEN the verdict is FLOOR with incomplete proof THEN it SHALL degrade to a gated correction · WHEN a floor clears THEN verification, boundary, quiet, and Doc 04's delivery authority SHALL all have held · WHEN the gate clears a contribution THEN delivery SHALL occur only through Doc 04's wake-turn tools, and this component SHALL call no transport directly · WHEN a human is speaking THEN nothing SHALL be delivered and in-flight speech SHALL stop within 200ms · WHEN quiet or mute is active THEN nothing SHALL be delivered, including floors · WHEN a human ask is pending THEN it SHALL preempt any un-spoken contribution · WHEN no boundary opens inside the relevance window THEN the item SHALL bank, never surface stale · WHEN a banked item's point was made by a human THEN it SHALL be dropped at close · WHEN the same point has been cleared once THEN it SHALL NOT be cleared again · WHEN Proxy's own contribution is echoed by a human THEN the self-loop filter SHALL suppress the event before the judge · WHEN the soft cap is reached THEN gated contributions SHALL stop and the stop SHALL be disclosed aloud · WHEN the hard cap is reached THEN mode SHALL be notes-only · WHEN a verdict class is absent from `voice_enabled_classes` THEN it SHALL NOT use `speak` · WHEN a participant asks why THEN the last reason code SHALL be rendered and delivered · WHEN a contribution is dismissed THEN it SHALL NOT be re-raised AND `human_response` SHALL record the dismissal · WHEN any judge or gate cycle completes THEN exactly one decision row SHALL exist.
