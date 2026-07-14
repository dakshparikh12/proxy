# PROXY · DEEP DIVE 04

# Proactive — When Proxy Speaks Unprompted

How Proxy contributes to a meeting without being asked — and why it stays quiet almost all the time. Part A is the product: what a proactive contribution is, the small set of things it will say, and the discipline that keeps it welcome. Parts B–C are the build: the chain from a page update to a spoken contribution, and the two guardrails that bracket it. Part D is cost and latency. Deep Dive 03 was Proxy answering when addressed; this is Proxy noticing when no one addressed it — the same shared workroom, entered through a different door with the opposite default.

---

## THE ONE IDEA TO HOLD

**Being asked is not required — but the bar to speak unprompted is much higher, and a deterministic gate, not the model, owns the mouth.** Proactive is the same develop-and-ground machinery as reactive (Deep Dive 05's workroom), entered through a second door whose provocation is a *material change to the Meeting Page* instead of a human question. The difference from reactive is entirely in the defaults: reactive **speaks by default** (you asked, you get an answer); proactive **stays silent by default** and must earn every contribution.

The whole engine is short because the intelligence lives where it belongs. The model is genuinely good at forming a sharp contribution given the live context — so we don't build scaffolding to help it think. We build only the two things it must **not** decide on its own: whether a fact is true (verified in the workroom, never asserted from confidence) and whether to actually interrupt the room (a deterministic gate tuned per room by a dial). Everything between is one cheap call and a shared workroom.

> **WHY THE MODEL DRAFTS BUT NEVER SPEAKS**
> A proactive AI that decides for itself when to interrupt is the failure mode everyone has felt — the copilot that won't stop talking. So Proxy's proactive side has no speaking tool. It can *draft* a contribution and *hand it to a gate*; only the gate — deterministic, defaulting to silence, tuned by a dial the room controls — can put it in front of people. The engineering effort goes into *when to speak*, which the whole industry has left unsolved, not into *what to say*, which the model already does well.

---

# PART A — THE PRODUCT

## The few things Proxy will say, and the discipline that makes them welcome

A proactive contribution is Proxy adding something no one asked for — and the entire product bet is that it does this **rarely, accurately, and only when a sharp participant genuinely would.** The value is real and unique to Proxy: it surfaces things nobody in the room raised — a wrong number, a forgotten past decision, a missed dependency. Reactive can't do that; it only answers what's asked. But that value evaporates the instant Proxy becomes noise, so the design is built around silence as the default and a high, room-controlled bar for breaking it.

### The six contributions (v1)

Every proactive contribution is one of six — a small, bounded menu that works in any meeting type. It is a *lens the model draws within*, not a rigid taxonomy: the model fills each dynamically against the live situation, and picks *none* far more often than one.

1. **Ideate** — a relevant idea, option, or alternative the room hasn't raised. *(The human-participant contribution; reasons over the conversation, needs no system grounding.)*
2. **Validate** — a stated claim, number, or assumption checked against what's actually true in the company's systems. *(The moat — grounded truth no one else in the room can produce.)*
3. **Gap / Risk** — a missing consequence, unexamined risk, or an avoidable-risk decision, including downstream / blast-radius impact of a proposed change or action.
4. **Recall** — "we decided the opposite three weeks ago" / "this was tried in Q1 and failed." *(Cross-meeting memory no human in the room holds.)*
5. **Quantify** — a real number or precedent attached to a vague claim: "that's ~3 days based on the last four times this shipped" / "at 9% churn that's ~$2.1M."
6. **Unblock** — the room is stuck on a factual question Proxy can answer fast from trusted sources.

Three are sharp-participant moves (Ideate, Gap/Risk, Unblock); three are only-Proxy moves grounded in the estate and its memory (Validate, Recall, Quantify). Deliberately **excluded from v1**: process-policing contributions (missing-question nudges, decision-hygiene prompts, "you're circling," "so-and-so hasn't spoken) — they read as a hall monitor and erode the welcome. Proxy contributes *substance*, not meeting etiquette.

### The discipline: default silent, dial-controlled, verified, never stale

Four properties make proactive welcome rather than intrusive, and they are the whole product discipline:

- **Default silent.** Proxy says nothing unless a contribution *strongly* clears the bar. Most material moments in a meeting produce no contribution, and that is correct behavior — a good colleague is quiet most of the time.
- **The dial controls how much it speaks.** A per-room setting (Observer → Facilitator) sets how high the bar is. Shipped conservative by default (near-silent, only near-unarguable contributions), and turned up per team as trust is earned. This is what lets one system be right for a quiet board review and an active design crit without code changes.
- **Verified before it speaks.** Any factual contribution (Validate, Quantify, Gap, Unblock) is checked against the real system before it can surface. Proxy never asserts a number from the model's confidence. This is structural, not prompted — an unverifiable claim simply can't become a contribution.
- **Never stale.** A contribution surfaces only if it's still relevant to what the room is discussing *right now.* If the conversation moved on while Proxy was developing it, the contribution is banked for the close-of-meeting reveal, not forced in late. A late proactive interruption is worse than silence.

### It works in any meeting — and degrades honestly where it can't ground

Nothing branches on meeting type. In a technical meeting all six fire; in a non-technical one (a strategy chat, a hiring discussion) the estate-grounded four (Validate, Recall, Quantify, and the grounded half of Gap) simply don't fire — there's nothing real to check against — and Proxy falls back to **Ideate only**, or stays silent. It never breaks; it contributes less where it knows less, which is exactly what an honest participant does. The bar stays high regardless.

---

# PART B — HOW IT WORKS

## From a page update to a spoken contribution

```
MATERIAL PAGE UPDATE  (Deep Dive 02 — trivial chatter / deliberation doesn't trigger this)
   │
   ▼  DRAFT  — one cheap prompt-cached call (this IS the first kill switch)
   │   "Given the live state + what's already been considered, is there a STRONG
   │    Ideate / Validate / Gap / Recall / Quantify / Unblock worth saying right now?
   │    Draft it + its strength + its anchor, or output NOTHING."
   │      → NOTHING  (the ~90% case) → stop. cheap.
   │      → a draft →
   ▼  BUNDLE  {relevant context slice (appended) · live-page pointer · the developed idea}
   │
   ▼  WORKROOM — the Proactive Engine entrance (Deep Dive 05)
   │   grounds the contribution: retrieve the owning expert → read the real value
   │   (usually prefetched → instant) → verify.  Reads only, cheapest tier, no build.
   │   Build as fast as possible; hand back "ready contribution + its anchor."
   │
   ▼  RELEVANCE GATE  — the second kill switch (deterministic, no model, owns the mouth)
   │   re-reads the LIVE page:  still on-topic?  ·  not already said/resolved by a human?
   │   ·  clears the dial's bar?  ·  worth interrupting now?
   │      → yes → SURFACE (whisper-first if sensitive)
   │      → moved on / already handled → BANK for the reveal, or drop
   │
   ▼  onto the Meeting Page (as a cited contribution) · one surface at a time · a human ask preempts
```

**Two model touches, one deterministic gate.** The draft call fuses four things that are really one judgment — *is this a moment, what would I say, which type, how strong* — into a single cheap inference: if it can't draft something strong, that "NOTHING" **is** the moment-check drop. The only other model work is the workroom's verify, and only for factual contributions. Everything else — the two kill switches — is deterministic.

> **REACTIVE AND PROACTIVE ARE ONE WORKROOM, TWO DOORS**
> The contribution work — grounding, retrieval, verify, and the six capabilities themselves — is shared with reactive and lives in the workroom (Deep Dive 05). What differs is only the door: reactive enters at top priority and exits straight to the asker; proactive enters on a page update and exits only through the dial + relevance gate. So when someone *asks* a proactive-flavored question ("anything we're missing here?"), it simply enters the reactive door and requests the same Gap capability — invited, top-priority, always answered. Nothing special to build; the consolidation is that the workroom is shared and the entrances are thin.

---

# PART C — THE BUILD

## Component 1 — The draft call (the first kill switch, fused)

**Overview.** One cheap, prompt-cached model call runs on each material page update. Its job is not "should I speak?" and not "generate a candidate to score later" — it is a single fused judgment: draft the strongest contribution the moment allows, typed by the six-menu, or output NOTHING. The NOTHING is the kill switch: if the model can't draft something strong, there's nothing here, and ~90% of updates end here for a few tokens.

**How we wire and optimize it.** A Haiku-class model, prompt-cached so the stable context (instructions, the six-menu, the meeting goal) is paid once and each update costs only the new delta. It reads: the live page state (goal, blocker, the latest claim/decision, tension edges), and the **dedup ledger** (Component 2). It outputs either NOTHING or `{contribution, type, strength, anchor}` — where `anchor` is the specific claim/decision the contribution is about (used later by the gate for the still-relevant check). Trusting the model here is correct: forming a sharp contribution from live context is exactly what LLMs are best at, so there is no router, no scoring pass, no candidate machinery — one prompt does draft + type + self-rated strength together.

```
SYSTEM PROMPT — THE DRAFT CALL (ILLUSTRATIVE)
You are a sharp expert participant who speaks ONLY when it strongly helps. Most of the time, say NOTHING.
On this page update, given the live state and what has ALREADY been considered/said (below), decide:
  Is there a strong contribution of one of these types worth saying RIGHT NOW?
    Ideate · Validate · Gap/Risk · Recall · Quantify · Unblock
Rules:
  • Default to NOTHING. Only draft if a sharp colleague would clearly speak here.
  • Do not repeat anything in the "already considered" list.
  • If the contribution asserts a fact/number, it MUST be one the systems can verify — else don't make it.
  • Never contribute meeting etiquette (who hasn't spoken, "you're circling"). Substance only.
Output: NOTHING, or {contribution, type, strength 0–1, anchor = the claim/decision this is about}.
```

## Component 2 — The dedup ledger (the one piece of state)

**Overview.** Without memory of what it has already surfaced or suppressed, the loop re-drafts the same contribution on every update (the claim is still on the page) and spams variations of one idea. A tiny ledger prevents this.

**How we wire it.** One short list per meeting: each contribution Proxy has surfaced or actively suppressed this session, keyed by its anchor. It's fed into the draft call so the model won't re-propose, and read by the gate so a contribution about an already-handled anchor is dropped. Cheap — it's a few lines in the prompt — but not optional: it's what makes the always-on loop stop looping.

## Component 3 — The bundle

**Overview.** What proactive hands the workroom. Because the contribution is *already drafted* (the draft call read the relevant slice to write it), we append that slice rather than pointing at it — it's in hand, and re-fetching would add a hot-path round-trip. The one thing that must stay a pointer is the live page, because the later relevance check must read *current* state, not the draft-time snapshot.

**How we wire it.** Three fields: the **relevant context slice** (appended — the specific claim/decision + the few surrounding turns the draft was based on), a **pointer to the live Meeting Page** (for the gate's freshness/relevance re-read), and the **developed idea** (the drafted contribution + its type + anchor). Minimal — car keys plus the one thing already in hand, not a fat context dump.

## Component 4 — The Proactive Engine entrance to the workroom

**Overview.** Proactive's development is not a separate engine — it's a dedicated entrance into the workroom (Deep Dive 05) that shares all of reactive's grounding but runs under proactive-specific constraints. This is where the contribution gets grounded and verified.

**How we wire it (and what the workroom must support — noted for Deep Dive 05).** The Proactive Engine entrance:
- **accepts a proactive bundle** (the three fields above);
- **reuses reactive's grounding wholesale** — retrieval of the owning expert, the expert pack as the map to the real value, the check-pointer read, the verify step, cross-meeting memory for Recall;
- **is reads-only, cheapest-tier** — it physically cannot open the sandbox/build tier. Nobody asked, so proactive never runs heavy compute or anything side-effecting. This keeps it fast and safe.
- **is fed by a prefetch hook** — it subscribes to entity mentions on Deep Dive 02's transcript stream and warms the key values ahead of need, so grounding a Validate/Quantify is usually a cache hit, not a live round-trip. This is the single biggest latency lever and it lives here.
- **builds as fast as possible and hands back** "ready contribution + its anchor." It does not decide to surface — it develops and returns; the gate decides. On timeout or if grounding can't complete, it returns "not ready in time," which the gate turns into a bank-for-reveal.

## Component 5 — The relevance gate (the second kill switch, owns the mouth)

**Overview.** The deterministic gate is the only thing that can put a contribution in front of the room. It defaults to silence and decides on *relevance now*, not on how fast the contribution was produced — which is the honest timing rule (a contribution that took 4s but is still dead-on gets said; one that took 1s but the room blew past gets banked).

**How we wire it — the ordered checks, no model.**
1. **Hard floors** — a decision going final, a detected contradiction, or a commitment on a checkable claim surface unconditionally (with proof). These are the unmissable moments.
2. **Still relevant?** — re-read the *live* page via the pointer: is the contribution's anchor still the live topic, and has a human not already said or resolved it since the draft? (This closes the "Proxy says what someone just said" and "corrects an already-fixed number" failures — dedup against live state, not just Proxy's ledger.)
3. **Clears the dial?** — does the strength beat the room's current bar (conservative by default)?
4. **Worth interrupting now?** — turn-taking: don't cut across an active speaker; prefer a natural break.

Passing all four → **surface** (whisper-first to the relevant person if it's sensitive, before the room hears it). Failing "still relevant" → **bank for the close-of-meeting reveal** (not lost — delivered where lateness is fine) or drop. The gate is **single-writer**: one contribution surfaces at a time, a newer contribution on the same anchor supersedes an older in-flight one, and a human ask always preempts. These checks are plain prompting + cheap graph reads — no library needed; a structured-output or guardrails repo wouldn't earn its place for a deterministic on-topic + dedup check.

## Component 6 — The dial (learned per room)

**Overview.** The single knob that tunes how much Proxy participates, so one system fits every room without code changes.

**How we wire it.** Five levels (Observer → Facilitator) that set the strength bar the gate enforces. Shipped conservative. Tuned per room from accept / dismiss / whisper-accept signals — if a room keeps waving contributions off, the bar quietly rises; if it engages, the dial can loosen. Start with a single learned threshold; the full multi-level bandit is added once there's enough accept/dismiss data to learn from (a bandit with no data is noise).

---

# PART D — COST AND LATENCY

- **Cheap by construction.** The always-on cost is one prompt-cached cheap call on *material* updates only (Deep Dive 02 already filters trivial chatter), and ~90% return NOTHING for a few tokens. Grounding + verify — the only real spend — runs on the handful of updates a meeting that produce a real contribution, and it's a read, never a build. Cents per meeting-hour.
- **Fast because facts are pre-read.** The prefetch hook warms estate values on entity mentions, so grounding a Validate/Quantify is usually an instant comparison. The slow structured Page update is never on the critical path for a catch — the live read happened ahead, in the background.
- **Relevance, not a timer, is the deadline.** Build as fast as possible; the gate decides on *still-relevant-now.* Anything that finishes after the room moved on banks for the reveal rather than surfacing late. This handles fast and slow contributions with one honest rule.
- **No wasted parallel work.** Material updates are coalesced before the draft call; concurrent drafts are fine (parallel thinking) but the gate serializes the mouth and supersedes stale contributions on the same anchor (serial speaking).
- **Degrades, never breaks.** No estate to ground against → the estate-grounded types don't fire, Ideate remains, bar stays high. Timeout → bank. Room moved on → bank. The failure modes all resolve to silence or the reveal, never a wrong or late interruption.

---

# DONE WHEN

**v0 — the core is done when:**

- a material page update triggers one cheap draft call that returns NOTHING for the large majority of updates, and a typed contribution + strength + anchor otherwise;
- the dedup ledger provably stops the loop from re-proposing a contribution it already surfaced or suppressed;
- a factual contribution (Validate / Quantify / Gap / Unblock) is verified against the real system before it can surface, and an unverifiable one never becomes a contribution;
- proactive enters the workroom through its own reads-only, cheapest-tier entrance, reusing reactive's grounding, and cannot open the build/sandbox tier;
- the prefetch hook warms estate values on entity mentions so grounding is usually a cache hit;
- the relevance gate re-reads the live page and drops/banks any contribution whose anchor is no longer the live topic or was already said/resolved by a human;
- the gate is single-writer (one surface at a time, supersede on same anchor, human ask preempts) and defaults to silence;
- hard floors (decision→final / contradiction / commitment) surface unconditionally with proof;
- the dial sets the strength bar, ships conservative, and is tunable per room;
- anything that can't be made ready while still relevant is banked for the close-of-meeting reveal, never surfaced late;
- in a meeting with no estate to ground against, only Ideate is available and the bar stays high — the system degrades rather than breaks;
- and the whole engine is one draft call + a shared-workroom grounding read + a deterministic gate — no separate proactive brain, no candidate-scoring machinery, no hard-coded situation rules.

**v1 — added on measurement:**

- the full multi-level dial as a per-room contextual bandit over accept / dismiss / whisper-accept signals (start with a single learned threshold);
- broadening the menu beyond the six only if real usage shows a recurring high-value contribution that none of the six cover.
