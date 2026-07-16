# PROXY · DD00 — The Product Spine (PROPOSED)

*The connective tissue that makes DD01–DD06 one coherent product. Proposed to supersede and absorb the old DD00-contracts stub. Where this file and a component DD disagree, THIS FILE is intended to win.*

**Status: PROPOSED — staged for the three founder calls in Part 7. Not final until those are made and the Part 6 amendments applied.**

**Why this doc exists.** We simulated ~100 end-to-end scenarios across every vertical, meeting type, scale, and failure mode, and traced each through the six specs. The components (estate, understanding, reactive, proactive, workroom, experience) are ~85% right on their own. What was missing is the *product as a running system* — the flows and contracts that cross doc boundaries. That is what this doc owns. Nothing here is a new engine; every fix is a wiring, a consolidation, or a single shared contract. **Design rule for this whole doc: the simplest thing that makes sense in Proxy's own terms. No invention.**

---

## PART 1 — THE ONE FLOW (any prompt, top to bottom)

Everything Proxy does is **one loop**, entered through one of a few doors. Reactive (a human asks), proactive (the Page changes), an expert consult, and a training probe are the *same loop* — they differ only in the entrance and the defaults. Hold this above all: **there is not a reactive engine and a proactive engine; there is one loop with different provocations.**

```
PROVOCATION → COMPREHEND → ROUTE → BUNDLE → WORK+VERIFY → RETURN → DELIVERY-GATE → PROJECT → WRITE-BACK → (loop)
```

Walk it once, for *any* prompt. Each step notes who owns it and — per the simplicity check — whether it's genuinely needed or over-engineering.

**0 · Provocation (with an identity).** Either a human speaks/types (reactive, DD03) or the Page materially changes (proactive, DD04). Both reduce to *"a provocation + who it's from."* Reactive enters at top priority with the speak-gate OFF; proactive enters silent-by-default and must earn the mouth. *Needed: yes — this is the "two doors, one workroom" consolidation; do not build two pipelines.*

**1 · Comprehend — "what do I need to do?"** The persistent Orchestrator (thin, Haiku-class) reads the ask against the live Page.
- Reactive: mode (do/help/explain/advise) + referent + `clarify-or-declare` (one question only when the alternatives lead to *different actions*).
- Proactive: the draft call decides "is there a strong contribution?" — **and, in parallel, the three hard shift-events (`decision→final`, `contradiction`, `commitment-on-checkable`) fire a *deterministic* forced-development entrance that bypasses the draft call** (fixes C1 — the flagship "heard before commit" guarantee must not depend on a call that can return NOTHING).
- A ~10ms exact-answer cache sits in front (DD03 C3): a valid repeat is served with no model call. *Needed: yes; simplest possible.*

**2 · Route — retrieval, not a decision.** Resolve WHO owns the ask.
- **If the Page already resolved the referent to an estate node, that node→unit binding IS the route** (deterministic, free). Only when there is no resolved referent do we call semantic `find_experts` (fixes A5 — deterministic binding must sit *above* fuzzy retrieval).
- `find_experts` returns pack id(s) + each pack's `readiness_state` (Part 2). Zero → generalist floor; one → default; several → cross-system. *Needed: yes. Reject the proposed global SCIP/Zoekt index for the generalist — the generalist consults experts via `find_experts`; a global index is over-engineering.*

**3 · Bundle — the thin "car keys."** The Orchestrator seals the one typed contract that opens the door (full schema in Part 5). It now *must* carry: `request_id`, `asker_identity`, `return_surface`, the WHO (pack ids + `readiness_state`), one starting pointer, and flags (`effects`, `stakes/freshness`). **In-flight dedup:** a second identical ask (same resolved question + anchor, short window) *attaches its surface* to the existing job rather than spawning a second workroom (fixes B4). *Needed: yes — the correlation fields are what make the return loop work; the cheapest fix for the biggest gap.*

**4 · Work + Verify — the one workroom.** The workroom (per-task, Sonnet/Opus, ephemeral) loads the pack(s), takes the **cheapest correct path** (KNOWN → LOOKUP → WORK; these *emerge*, they are not routed), and verifies **independently** (deterministic → HHEM grounding → cross-family judge for high stakes only). Side effects are **staged drafts.** *Needed: yes, exactly as DD05 specifies. Reject the compound-ask orchestrator — the loop's just-in-time planning already handles "do X and Y."*

**5 · Return — the envelope comes back (the loop that was missing).** The result envelope echoes `{request_id, return_surface}` immutably, plus artifact + receipt + `verify_state ∈ {verified|partial|unverified|judgment|offer}` + status + cost. It becomes a first-class **`result` node** on the Page so follow-ups ("edit that chart") can resolve to it (fixes B2, D1). *This is the core missing seam — see Part 4 for the full return contract.*

**6 · Delivery gate — two cheap checks, reused not rebuilt.** Before anything surfaces:
- **Still-current?** Re-read the *live* Page (not the ≤2s-lagged committed page) via the pointer: is the anchor still the topic, and did a human already say/resolve it? This is the SAME predicate for reactive and proactive — extract DD04's relevance-check-2 into one shared gate (fixes B7, D4; "derive, don't build a second").
- **Permission (per-recipient).** Gate delivery against who may *hear* it (Part 3). *Needed: yes; both are cheap graph reads, no model.*

**7 · Project — onto the right surface.** Route strictly by `request_id → return_surface`. Serialize the shared physical channels through their arbiters: **Mouth Arbiter** (voice) and **Screen Arbiter** (screen-share — new; Part 4). Voice + tile delivered under one `delivery_id` so banking/superseding one half atomically moves the other (fixes B3). Texture reflects `verify_state` — a `partial` result renders as the honest "couldn't fully verify" texture, never a solid verified edge (fixes D3). *Needed: yes.*

**8 · Write-back — the loop continues.** The `result`/`contribution` node is on the Page; the dedup ledger records what surfaced + *why it was suppressed* (the reveal reads this — fixes D2). A follow-up composes against it. Every proactive silence is priced here for the close reveal. *Needed: yes; keep the ledger a **derived cache** over Page nodes + gate records, not a fourth durable store (crash-safe by construction).*

**Close of meeting (ordered, atomic).** freeze Page → persist reveal + draft-bundle + gate-decisions to Postgres → merge-back estate facts → **tear down the ephemeral FalkorDB meeting graph LAST** (fixes B9's torn-down-store risk). Human-confirmed drafts resume post-close: durable resume (Inngest) → re-mint expired scoped token → execute → receipt → flip envelope `staged→executed` → ping the confirmer. A follow-up refuses to compose on a staged-but-unexecuted action.

**In-flight the whole time:** a control bus keyed by `task_id` carries CANCEL / REDIRECT / CLARIFY-ANSWER, polled at each tool-call boundary (the same boundary crash-resume already uses). A hard mute cancels in-flight TTS *and* flushes the queue (fixes B6, G3). *Needed: yes — DD03 already promises "say stop if you meant staging"; this is its missing receiver, built the cheapest way.*

---

## PART 2 — THE AGENT TOPOLOGY (stated once, canonically)

Five things, and no more. Every DD must agree with this.

- **The Orchestrator** — one persistent, thin agent per meeting (the identity on the call). Comprehends, routes, delivers, owns the mouth. Does no heavy work.
- **The Meeting Page** — shared state + event bus, maintained by the Scribe (DD02). Every write is an event others react to. This *is* the router — a resolved referent benches an expert; a `decision→final` fires a floor. No separate router subsystem.
- **The Workroom** — spawned per task (DD05). Any provocation enters it with a bundle; it does the doing; it returns an envelope. Ephemeral — costs nothing between tasks.
- **Experts = packs, not standing agents.** "The checkout expert" is checkout's cached pack (repo-map + graph slice + tools). **The workroom loads the pack and *becomes* the expert.** There is no separate expert process and no "expert→workroom" hop. *(This resolves the DD01↔DD05 contradiction that made the whole thing feel disconnected.)*
- **The Registry** — the phonebook (`find_experts`). The Postgres row is the single source of truth; the MCP subregistry is a regenerated projection (fixes A5).

**Expert lifecycle — the ownership table ("how Proxy knows and maintains its agents"):**

| Concern | Rule (simplest sensible) | Fixes |
|---|---|---|
| **Register** | Registry entry (unit meta + example-queries + embedding) written **eagerly at carve time**. Only the heavy pack (repo-map/PageRank) is built **lazily** on first reference. "Lazy = the pack only." | A1 |
| **Discover** | Resolved page-referent → node → unit is authoritative WHO; semantic `find_experts` only when no referent. Permission pre-filter before top-K. | A5 |
| **Trust** | Registry entry carries `readiness_state {carving, shadow, live-provisional, live}` + health. `find_experts` returns it; the bundle carries it; verify and the proactive gate read it. An unbuilt/shadow edge reads as **low-confidence, never absent** (so blast-radius never silently under-reports). A tracer-free promotion tier (differential-agreement + property-invariants) so greenfield/COBOL units aren't stuck in shadow. | A2 |
| **Grow** | A first-class `estate-expansion` event (a new repo) → cross-repo edge-union pass → **bounded** re-carve reusing the existing stable-ID overlap match. Per-file freshness untouched. | A3 |
| **Identity** | `node.id` minted once (not path/content-derived); renames resolved via git `--find-renames` + SCIP symbol continuity, so merged-back human facts survive a refactor. A `pack_id` referenced by an open job is pinned (drain-don't-delete) so live re-carve can't strand a running workroom. | A4 |
| **Own everything** | The excised super-hub/platform layer is sub-clustered into `platform/*` units, each with a named expert + owner. Acceptance: no highest-blast-radius file is unowned. | A6 |
| **Stay fresh live** | Freshness intersects `invalidated unit_ids` with units benched on live Pages (DD02 already tracks bench membership) → pushes `unit-invalidated` → the in-flight workroom expires its cached map. Honest "answered against pre-`<sha>`" receipt as backstop. | A7 |
| **Retire** | Retire by unit-id diff on re-carve; drain open jobs on the old `pack_id` alias first. | — |

---

## PART 3 — THE ESTATE-READ PERMISSION MODEL (the one blocker, rewritten)

*This replaces the old §2/§3. The intersection model must not ship.*

Separate two things the stub conflated:

- **What Proxy MAY READ** to ground a contribution = the **union** of the mapped participants' principals (a chosen superset). Grounding on the intersection made the moat vanish the moment one non-engineer joined — the opposite of the intent.
- **Who MAY HEAR it** = gated **per-recipient at delivery.** A catch grounded in content a person can't see is *whispered only to those entitled*, with evidence scoped to what each recipient may open. Reactive reads run under the **asker's** principals; an answer grounded outside the room's shared scope returns on a private channel with a room-visible "answered privately."

Everything else stays: permission stamps copied from the source at repo/directory granularity; every query filtered `current_user_principals && allowed_principals`; **revocation enforced at read time, never cached.** Expert packs are permission-partitioned per viewer (a shared `platform-auth` unit shows each viewer only the files they may open).

*Founder call (Part 7): default read-scope to the **union** of the room, or a configurable superset (e.g. "team scope")? Recommendation: union-of-mapped-participants as the simple default.*

---

## PART 4 — CROSS-CUTTING RESOURCE OWNERS

The physical, single-instance resources no component can own alone. Each is deliberately simple and mostly reuses an existing pattern.

- **Mouth Arbiter (voice) — exists, tightened.** One serializer owns TTS. Priority: human-ask response > hard-floor surface > dial-gated surface. **Class-aware banking:** a *reactive* answer is never routed to the close-reveal ("being asked is always answered"); only proactive contributions bank. Re-validate a banked item's still-current check on resume. Barge-in and supersede rules as before.
- **Screen Arbiter (screen-share) — new, the structural twin of the Mouth Arbiter.** Tile→screen-share promotion is currently unilateral and would *steal the single presenter slot* from a human on Meet/Teams. Same shape as the Mouth Arbiter: detect an active human share from transport telemetry, never pre-empt, gated bid, priority ladder, explicit yield-back. Near-zero build — the share-state is existing telemetry and the bid reuses the orb state machine (fixes B8).
- **Delivery router.** Routes every returned envelope strictly on `request_id → return_surface`. Under the >2s detach, this is what makes out-of-order returns deterministic and whisper routing correct (fixes B2).
- **In-flight control bus.** `task_id`-keyed CANCEL / REDIRECT / CLARIFY-ANSWER, polled at tool-call boundaries; hard-mute = STOP + flush (fixes B6).
- **Close-sequence orderer.** Owns the ordered close (Part 1) so the reveal never reads a torn-down store (fixes B9).
- **Provider-concurrency queue.** Extend the swap-adapter discipline (already used for Recall/TTS) to the core reasoning models: a 429 degrades to a cross-family fallback (OpenRouter) instead of dead air; reactive/hard-floor outrank proactive in the queue (fixes F1).

---

## PART 5 — THE LINTABLE HANDOFF TABLE

Every inter-doc contract, as a typed row. **Rule: a field present on one side of a handoff and missing on the other is a build-time lint failure.** This is the mechanism that would have caught B1/B2/A1/A2/D1 automatically.

| Contract | Producer → Consumer | Mandatory fields |
|---|---|---|
| **Thin bundle** | Orchestrator/DD03·DD04 → Workroom/DD05 | `request_id`, `asker_identity`, `return_surface`, `who[]`(pack_id + `readiness_state`), `pointer`, `effects∈{read-only,draft-staging,execute-terminal}`, `stakes/freshness` |
| **Result envelope** | Workroom/DD05 → Delivery router → Page | echo `request_id`, `return_surface`; `artifact`, `receipt`, `verify_state∈{verified,partial,unverified,judgment,offer}`, `status∈{answered,staged,executed,failed}`, `cost` |
| **Registry entry** | Carve/DD01 → Registry | `unit_id`(stable), `unit_meta`, `example_queries`, `embedding`, `readiness_state`, `health`, `owner`, `allowed_principals[]`, `pack_id?`(lazy) |
| **`fact` node** | Merge-back/DD02 → Estate/DD01 | `id`, `attached_node_id`, `text`, `source∈{meeting_id,human,doc}`, `stated_by`, `at`, `supersedes_id?`, `verify_state` |
| **`result`/`contribution` node** | Workroom/DD04 → Page/DD02 | `id`, `request_id`, `anchor`(claim/decision), `verify_state`, `type∈{answer,ideate,validate,gap,recall,quantify,unblock}`, `receipt_ref`, `suppressed_reason?` |
| **Proactive trigger** | DD02 shift-events → DD04 | enum incl. `decision_final`, `became_irreversible`, `tension_edge_opened`, `commitment_on_checkable` (→ forced-development entrance) and `claim_landed`, `silent_owner`, `unaddressed_concern` (→ draft call) — fixes C1, C2 |
| **Control-bus signal** | Orchestrator → Workroom | `task_id`, `op∈{cancel,redirect,clarify_answer}`, `payload` |
| **unit-invalidated** | Freshness/DD01 → benched Workrooms | `unit_id`, `new_sha` (only for live-benched units) |

---

## PART 6 — THE AMENDMENTS TO THE SIX DDs (the whole checklist)

Small, targeted edits — the DDs' internal designs stand. Each line = one confirmed gap's simplest fix.

**DD01 (Estate):** eager registry entry / lazy pack (A1) · add `readiness_state`+health to entry + tracer-free promotion tier (A2) · `estate-expansion` event + cross-repo union pass + bounded re-carve (A3) · `node.id` minted once + rename tracking + live-job pack pin (A4) · registry = Postgres source-of-truth, MCP projection, content-hash re-embed (A5) · super-hub `platform/*` units each owned (A6) · push `unit-invalidated` to live-benched packs (A7) · base meeting-decision write-back is **v0** (only `continues`-edge series threading is v1 — fixes C4, so v0 Recall has data to ground on).

**DD02 (Understanding):** add `result`/`contribution` node types so the gate accepts Proxy's own output + Ideate has a home (D1) · status-monotonic check made **source-aware** (human/Scribe revert = authorized supersede; only extraction reverts rejected — D5) · expose the shared `still-current?` predicate (B7) · dedup reads live page + keyed by estate-node (D4) · buffer raw audio at the transport for the crash blind-window, or announce it (G1).

**DD03 (Reactive):** bundle carries the correlation fields (B2) · in-flight intake dedup attaches surfaces (B4) · reactive delivery runs the still-current gate before speaking a now-redundant answer (B7) · a spoken reactive ask honors a hard mute — two-state mute (G3).

**DD04 (Proactive):** deterministic forced-development entrance off the three hard shift-events, bypassing the draft call (C1) · `claim_landed` trigger for a lone wrong number (C2) · feed `fact`-node priors attached to page referents into the draft call for Recall/Quantify (C3) · dedup ledger gains a `suppressed_reason` field the reveal reads (D2).

**DD05 (Workroom):** envelope echoes `request_id`/`return_surface` + `verify_state` (B2, D3) · third effects class `execute-terminal` → explicit-confirm, never silence=go; scale approval friction off reversibility (E1) · control-bus polling at tool-call boundaries (B6) · post-close execute-after-confirm loop wired (B9) · provider-concurrency queue + cross-family fallback (F1).

**DD06 (Experience):** Screen Arbiter render + gated share-bid (B8) · atomic voice+tile under one `delivery_id` (B3) · partial-receipt texture (D3) · non-visual carry for the silent bid (minor) · per-viewer consent re-notice on late join (G2).

**Cross-cutting / [RATIFY] before GA:** GDPR erasure must fan out to merged-back estate facts + workroom envelopes, preserving superseded priors (G2 — gate behind legal) · two-bot coexistence at multi-deployment scale · tenant-scoping of all derived stores.

---

## PART 7 — VALIDATION, AND THE FEW GENUINE FOUNDER CALLS

**Every step in Part 1 was checked against "could this be cut or is it over-engineered?"** and each is the simplest sensible form. The things that *were* over-engineered are explicitly rejected — keep them out: a global SCIP/Zoekt index for the generalist; incremental Leiden (full re-run + stable-ID overlap is cheaper and already specified); a compound-ask orchestrator/decomposer; sub-file symbol partitioning for god-files; continuous per-decision write-through (fights the merge-at-close design); a fourth durable store for the dedup/reveal (derive it).

**The genuine decisions only you should make (everything else is set to the obvious default):**
1. **Estate read-scope default** — union-of-mapped-participants (recommended) vs a configurable team-scope superset. Affects the moat's reach in mixed-seniority rooms.
2. **`execute-terminal` actions in v0** — allow them at all (with explicit confirm), or force *everything* world-touching to a staged draft in v0 and defer live execution? Simplest-safest = stage everything in v0 (leaning this way).
3. **Crash audio blind-window** — pay for durable audio buffering at the transport, or ship the honest "I was disconnected 14:03–14:04" announcement for v0? Leaning announce-for-v0.

**Where this leaves us:** with this spine applied plus the Part 6 amendments and the three calls above resolved, the six specs agree with each other and every routing path from prompt→done→next is owned end-to-end — no redesign, no over-engineering. Until those calls are made, this spine stays a proposal; I'm flagging the three back to you rather than deciding them for you.
