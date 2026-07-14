# PROXY · DEEP DIVE 02

# Meeting Understanding — the Scribe, the Meeting Page, and the Read

How Proxy turns live speech into one always-current, cited, machine-readable understanding of the meeting — not a transcript, not a summary, but a grounded page that *reasons* about what is happening. Part A is the product: what the page is and every capability it unlocks. Parts B–C are the build: the whole thing in one picture, then piece by piece — what each part is, how we wire it, how we optimize it. Part D is how it stays cheap, fast, and length-proof at any meeting size. Deep Dive 01 built a map of the company's software; this one builds the map of the meeting — live, as people talk — and forms a running read of what each new fact changes about the room.

---

## THE ONE IDEA TO HOLD

**One accountable agent — the Scribe — maintains one living object, the Meeting Page, and reasons over it.** The page has three strata that build on each other:

1. **The Meeting Context Graph** — the grounded facts: every claim, decision, referent, commitment, and open question, each pointing at the real thing it is about in the Estate Graph, each stamped in time. This is built continuously and automatically by an adopted engine (Graphiti), not hand-written.
2. **The Derived Relations** — the structure that falls out of the facts: a contradiction across time, a concern nobody answered, two people using one word for two different things. Most of these are free — they are what the graph *is*, not something a model has to notice.
3. **The Read** — the understanding on top: where the meeting is, where it is heading, whether the alignment in the room is real or polite, who is driving, who owns an affected system but hasn't spoken, and the health of the decision forming. This is the one place a strong model reasons, and it runs rarely.

The page is not a transcript and not a summary. It is the meeting's live understanding, and it is the one surface every other part of Proxy reads from and reacts to. Everything below is how speech becomes that understanding — cheaply, quickly, and without ever guessing silently.

> **THE DESIGN RULE UNDERNEATH THIS DOC**
> We do not build a knowledge-graph engine. Turning messy conversation into a temporal, grounded, contradiction-aware graph is a solved, commodity problem with a state-of-the-art open-source engine (Graphiti, Apache-2.0). We adopt it and spend all of our effort on the one thing that is ours: **the Read** — the reasoning and interpretation that makes the meeting *understood* rather than merely recorded. Build only the composing machine; rent everything else.

---

# PART A — THE PRODUCT

## What the Meeting Page is, and everything it makes possible

The Meeting Page is a single, always-current object that is the meeting's understanding. Where a transcript is a wall of words and a summary is a stale afterthought, the page is live, structured, cited, and — the part no meeting tool has — *reasoned*. It holds what is true right now, who said it, when, where it can be checked, **and what it means for where the room is going.** It is the working memory of the whole system.

### What is on the page

The page carries the meeting's whole state, in machine-exact structure where it must be exact and in a short reasoned prose read where it must convey meaning.

- **The goal, and how it's going.** The goal of the meeting, the distance still to cover, the momentum — converging, circling, or stalled — and the single current blocker: what is most in the way of the goal right now.
- **Claims.** Every checkable thing anyone asserts — the exact value, who said it, the timestamp — plus *how firmly it was said* (a claim floated as "maybe" is not a claim stated as hard fact). For example: "checkout's error rate is 2% (Priya, 10:12, hedged)."
- **Decisions.** The decision currently forming, its status, how reversible it is, where each person stands, and the blast-set — which parts of the software it would touch.
- **Referents.** What the pronouns and shorthand actually mean — "that migration" resolved to the real pull request, "the dashboard" to the real service — each linked to its page in the Estate Graph.
- **Open questions and commitments.** The questions nobody has answered yet, and who owes what follow-up.
- **Unresolved debate, first-class.** A disagreement with no consensus is not dropped or flattened into a fake conclusion. It is held as proposals, arguments, stances, and an OPEN status, so a live argument is captured honestly.
- **The Read.** The running interpretation: where the meeting is heading, whether alignment is real or polite, whose concern went unaddressed, who owns an affected system but hasn't spoken, and the health of the forming decision.

It records **observable dynamics only** — who is driving, who owns a topic but hasn't spoken, that a disagreement is unresolved, that a concern was raised and never answered — and **never inferred emotion.** It reports what happened in the room, grounded to specific utterances, not what it imagines anyone feels. This line is absolute: every humanistic signal is a function of countable, pointer-grounded facts, or it does not ship.

### The capabilities this unlocks

Because the page is structured, cited, and reasoned rather than a blob of text, Proxy can do things a meeting summarizer never could:

- **Catch a contradiction across time.** A number stated at minute three and quietly contradicted at minute twenty is catchable from the page alone — because both land as claims resolving to the same thing, and the mismatch is *structure*, not something a model has to happen to remember. (In the engine, the later fact invalidates the earlier edge and keeps both with timestamps — a pre-contradiction, visible before either speaker notices.)
- **Answer "is that true?" instantly.** Every checkable claim carries a check-pointer — the exact estate node and live source where it would be verified — so the answer is one lookup away, with no scramble to figure out what the question is even about.
- **Know what's fresh.** Each claim about a live system carries a freshness stamp — how long ago Proxy last verified that system — so it knows when a cached truth still stands and when a re-check is worth it.
- **See where the meeting is heading.** From decision velocity and agenda-remaining-versus-clock, the page projects what is likely to be decided and when, and what won't be reached — so downstream can act *before* the moment, not in the retro.
- **Name the room's dynamics, grounded.** Who is driving (utterance share on the forming decision), who owns an affected system but hasn't spoken (from Doc 1's ownership edges), whether a stated agreement is real or a concern was quietly decided around — each surfaced only with a *named* grounding, never a vibe.
- **Produce the post-meeting output for free.** The decisions, action items, and per-person follow-ups are already on the page, already cited. There is no separate "generate the summary" step; the final page state is the output.
- **Let anyone correct it live.** Because it's a readable document, the companion panel shows it forming; any participant can fix a mis-captured decision or rename a referent, and the correction re-enters the same writing path and takes effect immediately.

> **WHY THE PAGE IS WHAT WAKES THE WHOLE SYSTEM**
> The Meeting Page is not just a record for humans — it is the nervous system of the entire product. Every write is an event other agents react to. Resolving "checkout" to its estate node is exactly what summons checkout's expert. A claim appearing wakes the claim-checker; a decision moving toward final wakes the decision-watcher. The rest of Proxy doesn't poll or re-read the transcript — it watches this one page change. Get the page right and everything downstream has what it needs; that is why this layer is built first and held to the highest bar.

### It works for any meeting, with nothing branching on type

A standup, a production incident, a design review, a sales call, and a board meeting all run the identical mechanism. Nothing on the page or in its writer asks "what kind of meeting is this?" What differs is only which content shows up, absorbed by a type-superset schema:

- **Event / timeline nodes** appear when the meeting is an incident ("at 10:04 the alert fired; at 10:09 we rolled back") — a timeline builds itself from the same writer.
- **Metric nodes** appear when numbers are on the table — a quarterly review fills with revenue, churn, and error-rate values pinned to their sources.
- **Topic / Section regions** appear in long meetings — the graph is shaped like the meeting itself, and a re-opened region is the signal that the room is circling back.

One writer, one code path, instantiated by whatever the room actually talks about. There is no per-vertical logic anywhere — a sales call and a board review differ only in their content, never in the machine.

### It is size- and vertical-agnostic — the same system for two people or a hundred

Nothing in this layer knows or cares how big the company is or what industry it's in. The page tracks a two-person standup and a forty-person all-hands with the same fields, because it holds the meeting's *state*, which is small regardless of company size. And because cost is tied to the page (which stays small) rather than the transcript (which grows), a three-hour meeting costs almost the same to understand as a thirty-minute one. The industry never enters: the page holds claims and decisions whether they're about payment services, clinical pipelines, or game-engine modules. This is a design property, not a convenience — it is why one system serves every customer without a per-customer branch.

---

# PART B — HOW IT WORKS

## From speech to a reasoned page, end to end

The whole thing is three moving parts over one page, and one agent that owns it.

```
 Recall.ai (per-participant audio) ──▶ AssemblyAI Universal-3.5  ──▶  UTTERANCE LOG
   one clean stream per speaker         keyterms primed from the        append-only · the clock
   (no diarization to fail)             Estate Graph's own vocabulary    (audit + version stamps)
                                                     │
                                                     ▼
                                            THE COALESCER  (turn / semantic boundary)
                                            groups utterances into episodes · drops chit-chat
                                                     │
                        ┌────────────────────────────┴───────────────────────────┐
                        ▼  (background · never blocks a read)                      │
        THE MEETING CONTEXT GRAPH  ── Graphiti ──────────────────                  │
          add_episode → extract claims/decisions/referents                         │
          resolve against the Estate Graph · bi-temporal edges                     │
          contradiction = edge invalidation (tension edge, free)                   │
          ↑ passes through the ACCEPTANCE GATE (deterministic, single-writer)      │
                        │                                                          │
                        ▼                                                          ▼
                 THE READ  ── one Scribe pass (Sonnet), on material change ──▶  every downstream
                 interpretation · prediction · decision-health · human signals    consumer reads the
                 emits typed INTERPRETATION-SHIFT EVENTS                           small page @ ~300ms,
                        ▲                                                          never the transcript
                        │  escalate on ambiguity
                 THE SCRIBE  ── one accountable agent (Agent SDK) ──
                 sub-floor referent · real contradiction · garbled input · unknown entity
```

*The graph is always being brought up to date in the background; nothing that reads it ever waits for that to finish.* That single sentence is the entire latency design. There are no "lanes" to manage and no scheduler to tune — ingestion is a background task, reads are instant against whatever the graph currently holds, and the strong-model reasoning (the Read) runs only when something material changes. A read that lands a second before an episode finishes ingesting simply sees the state as of a second ago, which is exactly what a human participant sees too.

### The flow in words

Recall.ai gives Proxy a **separate clean audio stream per participant**, so overlapping speech arrives as parallel clean streams rather than one garbled mix — which means there is no diarization model to get wrong, and speaker labels are free. Each stream goes to AssemblyAI Universal-3.5 (`u3-rt-pro`, ~300 ms word emission), **primed with keyterms pulled straight from the Estate Graph's own vocabulary** — your service names, metrics, and acronyms. This is the jargon fix no competitor has: Doc 1 *is* the domain lexicon, so the recognizer knows "idempotency-key" and "checkout-v2" before anyone says them.

Utterances interleave by timestamp into the **utterance log** — append-only, the meeting's single clock, read by nothing live. The **coalescer** groups them into episodes at natural turn or semantic boundaries and drops chit-chat, so the graph engine thinks on settled thoughts, not on every partial word.

Each episode is handed to the **Meeting Context Graph** — Graphiti — which extracts the claims, decisions, and referents, resolves them against the existing graph *and against the Estate Graph*, writes bi-temporal edges, and invalidates any fact a new one contradicts. Everything it proposes passes a deterministic **acceptance gate** before it lands — the single-writer safety seam. This all happens in the background.

On material change — a decision moving, a topic shift, a tension edge opening, goal drift — the **Scribe** runs one strong-model pass, **the Read**, reasoning over the graph and the Estate Graph (never the transcript) to write the interpretation, the prediction, the decision-health, and the grounded human signals, and to emit typed interpretation-shift events. When either the engine or the Read hits something it can't safely resolve — a referent below the confidence floor, a genuine contradiction, garbled input, an unknown entity — it escalates to the Scribe, which investigates with tools and resolves it into a grounded fact or an honest open question, never a low-confidence guess.

> **THE PAGE IS A GRAPH — THE SCHEMA, IN PLAIN TERMS**
> The Meeting Page is stored as a temporal graph, the same shape as the Estate Graph from Deep Dive 01: nodes and typed edges, each edge carrying validity intervals (when a fact became true, when it stopped, when Proxy learned it). A **claim** node holds a value, a speaker, a timestamp, an assertiveness stamp, a check-pointer (the estate node and live source where it verifies), and a freshness stamp. A **decision** node holds a status, a reversibility, each person's stance, and a blast-set. A **referent** resolves to an Estate Graph node. There are **commitments**, **open questions**, and the **goal** with its distance, momentum, and current blocker. Node types are a superset — Event, Metric, Topic/Section. A **tension edge** exists whenever two claims resolve to the same node with incompatible values — and in this engine it is not something we detect and write, it is what edge-invalidation already produces. **Unresolved debate** is a first-class subgraph. Layered on top, **the Read** is a small set of grounded interpretation nodes and a short prose gloss.

---

# PART C — THE BUILD

For each part: the overview in plain terms, then how we wire it and how we optimize it. Dense, but built to implement from.

> **WHAT'S CORE (v0) VS. WHAT'S ADDITIVE (v1) — read this before building**
> The v0 that proves the whole thesis is a short build: **Recall + AssemblyAI (keyterm-primed) → coalesce into turns → `Graphiti.add_episode` with our schema + estate loaded as entities → one Sonnet Read pass on material change → the Scribe for escalations.** Around that core, three things are cheap and worth doing in v0 because they are load-bearing safety or the entire downstream payoff: the **utterance log** (one clock, near-zero work), the **acceptance gate** (deterministic validation, the safety floor), and the **shift-event contract** (the reason Doc 3/4/5 get faster). Everything else is *additive and gated on measurement*: Maverick coref (only if referent recall misses its floor), model2vec embeddings (only when volume hurts cost/latency), two of the four human signals, the few-shot flywheel (log in v0, activate later), and merge-back (add when you support recurring meetings). Nothing below is deleted — it is **sequenced.** Build the core, run it on a real transcript, and let what breaks pull the next piece in. Do not build for failures you haven't observed.

## Component 1 — Speech and transport: the raw input

**Overview.** Everything starts with turning audio into text, fast and accurately, with each utterance tagged by who said it. Bad transcription poisons everything downstream, so this is the one place we default to a strong paid model — but we build a cheap flag so a cost-sensitive customer can dial it down.

**How we wire and optimize it.** Recall.ai is the transport bot that joins the meeting and, critically, exposes **per-participant audio streams** — so we drop speaker diarization entirely (the thing that degrades past four speakers in every competing tool) and get perfect speaker labels for free, plus clean separation of overlapping speech. Each stream goes to **AssemblyAI Universal-3.5** (`u3-rt-pro`, ~300 ms word emission), behind a one-line swap adapter so a cheaper flag (Deepgram Nova-3) or a self-hosted sovereignty option (faster-whisper / Parakeet) is a config change, not a code change. The one piece of real value here beyond commodity transcription: we prime the recognizer with **keyterms from the Estate Graph** so company jargon transcribes correctly on the first try — the differentiator no notetaker has, and nearly free because Doc 1 already holds the vocabulary. Every utterance carries an input-quality/confidence stamp that rides downstream so the gate and Scribe know how much to trust each word.

## Component 2 — The utterance log: the single clock

**Overview.** Every utterance is appended to a log that is never edited and never deleted. This is *not* what the system reads to understand the meeting — that is the page. The log's only jobs are replay/audit and, more importantly, being the meeting's single source of time.

**How we wire and optimize it.** An append-only table in Postgres, one row per utterance, monotonically numbered. This is a consistency invariant, not just a record: **every version of the Meeting Page is stamped to the utterance that produced it**, and every graph episode references the utterance span it came from. That gives the whole system one unambiguous timeline — so any agent reasoning over the page knows exactly which state it saw, and a draft written against minute-3's page is detectably stale at minute-20 and discarded rather than silently acted on. We optimize by keeping the log entirely off the read path: nothing reads it live, so it can be written lazily and never sits between speech and the page. It is also the verbatim ground truth — the one place the exact words survive — which is what lets the Scribe read a raw span on demand when it needs to disambiguate.

## Component 3 — The Coalescer: episodes, not keystrokes

**Overview.** The graph engine should reason on settled thoughts, not on every partial word. The coalescer groups a burst of utterances into one episode at a natural boundary — a completed turn, a topic beat — and drops chit-chat before it costs anything.

**How we wire and optimize it.** Deterministic edit-magnitude and turn-boundary logic, no model: several short back-to-backs from one speaker, or a tight exchange that resolves one point, become one episode. A tiny self-hosted relevance classifier drops greetings, "can you hear me?", and tangents so only substance reaches the engine. This is the cheapest quality lever in the layer — it keeps every downstream cost proportional to substance rather than to talk, and it is what makes ingestion cost flat regardless of how fast people talk. The one exception: anything that looks like a decision moving or a hard contradiction bypasses coalescing and ingests immediately, so a decision going final is never delayed by a coalescing window.

## Component 4 — The Meeting Context Graph: Graphiti, adopted

**Overview.** This is the heart of the capture layer, and we do not build it. Graphiti is the open-source (Apache-2.0), state-of-the-art temporal knowledge-graph engine for agents — it takes messages and turns them into a live, grounded, bi-temporal graph, resolving entities as it goes and invalidating facts that newer facts contradict. It is exactly the "text-to-knowledge-system" machine this layer needs, it is free, and it is maintained by a funded team (Zep). We adopt it and shape it to Proxy.

**How we wire it.**
- **Our schema is its entity types.** Claim, Decision, Referent, Commitment, OpenQuestion, plus the Event/Metric/Topic supersets, are declared as Graphiti custom entity types via Pydantic models — so the graph it builds is *our* page, not a generic one.
- **The Estate Graph is prescribed knowledge.** Doc 1's graph is loaded as prescribed entities, so a meeting claim about "checkout" resolves against the *real* checkout node — which is what gives every claim its check-pointer and what benches the right expert downstream.
- **Contradiction is free.** Graphiti's bi-temporal model gives every edge validity intervals and invalidates a fact when a conflicting one arrives, preserving both with timestamps. That *is* our tension edge and our freshness stamp — we don't detect contradictions, the engine's data model produces them.
- **Extraction runs on a capable-but-cheap model.** Graphiti's extraction needs reliable structured output (small models emit malformed JSON and fail ingestion), so episodes are extracted with **Haiku 4.5** (validated) or a fast Groq-hosted model, on coalesced turns — a few calls per minute, not one per word — which keeps both cost and reliability in range. Anthropic models are natively supported.
- **Storage is FalkorDB, ephemeral per meeting.** Graphiti runs on FalkorDB (open-source, Redis-based, sub-10 ms queries, far lighter than Neo4j) as one small container. The meeting graph lives for the session and **merges into the durable Postgres Estate Graph at close** — so our long-term memory stays in one Postgres, and we add only a cheap, disposable live-state store. (Graphiti's PostHog telemetry is disabled for enterprise privacy.)

**How we optimize it.** Ingestion is a background task and never blocks a read — reads hit Graphiti's hybrid search (semantic + BM25 + graph traversal, **P95 ~300 ms, no LLM in the query path**), which is already fast enough for live downstream use. Concurrency is raised via `SEMAPHORE_LIMIT` since we own our LLM throughput. Because the whole engine sits behind our page interface, it obeys the same swap-adapter discipline as everything else in Proxy — if a better engine appears, the page contract doesn't move. *V1 cost/latency lever, only when volume justifies it:* swap Graphiti's default embedder for a self-hosted static model (**model2vec / potion**, ~8–30 MB, up to 500× faster on CPU, numpy-only) to take embedding calls to ~zero. Ship v0 on the default embedder; measure before optimizing.

## Component 5 — The acceptance gate: deterministic, no AI, single-writer

**Overview.** Nothing the extraction step proposes touches the page until it passes a gate made of plain code — no model, so it's free, instant, and perfectly predictable. The gate is what lets us trust a cheap extraction model to maintain the page: the model can be sloppy because the gate is strict. This is preserved unchanged from the original design because it is the load-bearing safety property, and adopting an engine does not get to weaken it.

**How we wire and optimize it — the exact checks.** Every proposed change must be (1) **schema-valid** — the right fields, right types; (2) **free of invented IDs** — a referent may only resolve to a real Estate Graph node; (3) **pointer-sound** — anything referenced must already exist on the page; and (4) **status-monotonic** — a decision's status cannot silently revert. A change that fails is rejected and the ambiguity that caused it escalates to the Scribe rather than dropping. This gate is also the **single-writer seam**: participant corrections and Proxy's own contributions never write the page directly — they enter through this same gate — so there is never a write–write conflict on the meeting's understanding. One hand holds the pen; everyone else reads. It sits between Graphiti's output and the committed page, so the safety floor stays ours even though the extraction is rented.

## Component 6 — Referent binding: because "it" is the poison

**Overview.** Coreference is figuring out what "it," "that," "the migration," "the old one" point to. Getting this wrong is the single most dangerous failure in the whole layer: a confidently mis-resolved "it" produces a claim that looks grounded and cited and sails through every downstream check. So we treat it as its own discipline, not a side effect of extraction.

**How we wire and optimize it.** Graphiti resolves entities as it ingests, against both the meeting graph and the estate — and for v0 that is the whole mechanism. What makes it *safe* is one rule, preserved absolutely: **anything below the confidence floor is written as an open question, never as an asserted fact** — the page says "'it' here is unclear — the migration, or the rollback?" instead of silently picking one — and below-floor cases escalate to the Scribe, which reads more context and resolves them. We never let a low-confidence referent land as a bare fact. That is the entire referent discipline, and it rides on capability the engine already has.

*V1 fallback, only if measured referent recall misses its floor:* add a self-hosted specialist coreference model (**Maverick-coref** — a specialist encoder that beats general LLMs at this task, ~170× faster than autoregressive systems, ~free per call) to cross-check hard pronouns in fast, overlapping speech. Do not build this until the numbers say you need it — Graphiti's resolution plus the below-floor rule is expected to clear the bar on its own.

## Component 7 — The Read: the one place we reason

**Overview.** The graph captures *what is true, claimed, decided, and referenced.* The Read captures *what it means and where it is going* — the interpretation a sharp chief of staff would keep. This needs a strong model, so it runs rarely: only when something material changes. This is the enhancement over the original DD02's "prose refresh" — it is no longer a paragraph of vibes, it is grounded structure plus a short gloss, and it does real forward-looking reasoning.

**How we wire it — what it writes, all grounded.** A Sonnet-class pass, invoked on material change (a decision moving, a topic shift, a tension edge opening, goal drift) — a handful of times per meeting segment, cents not dollars, never per utterance. It reasons over the **graph and the Estate Graph, never the transcript,** and writes four things, each a function of countable, pointer-grounded facts:

- **Where it is** — goal, current blocker, momentum (converging / circling / stalled).
- **Where it's heading** — prediction by grounded extrapolation: decision velocity × agenda-remaining-versus-clock → "likely to decide X by ~T; item Y won't be reached." Arithmetic on structure the pass already touches, not a forecasting model.
- **What people are contributing** — observable roles: who is driving (utterance share on the forming decision), who is dissenting, and who **owns an affected system but hasn't spoken** (from Doc 1's ownership edges).
- **The human read** — named, grounded signals, never an inferred feeling. **Ship two in v0** (the most mechanical, lowest false-positive risk):
  - **Silent-owner flag**: an estate node in the decision's blast-set has an owner (from Doc 1) with zero utterances this session. Pure structure — near-impossible to false-positive.
  - **Unaddressed-concern edge**: a concern-claim raised by P, no responding claim resolving to it, and the decision it bears on advancing anyway.
  - *V1, once you have real meeting data to calibrate against:* **false-consensus candidate** (a decision advancing while stance coverage is below threshold or a concern is still OPEN) and **referent-divergence** (the same word resolving to two estate nodes across speakers). These carry a base-rate risk — most quiet agreement is genuine — so they need a labeled false-positive target before they surface. The closed enum already reserves their slots; turning them on is a config change, not new architecture.

**How we optimize it — the interpretation-shift event.** When the Read materially moves, it emits a **typed shift-event** from a small closed enum, and this enum is the explicit contract to the downstream engines. The **hard types map one-to-one onto Doc 4's three unconditional forced-development floors** — so Doc 4 fires them deterministically without re-deriving anything:

| Shift-event | Fires |
|---|---|
| `decision→final` / `became-irreversible` | Doc 4 hard floor 1 (heard before commit) |
| `tension-edge-opened` (contradiction) | Doc 4 hard floor 2 (right to correct the record) |
| `commitment-on-checkable-claim` | Doc 4 hard floor 3 (can we honor the promise) |
| `silent-owner` / `unaddressed-concern` (v0) · `false-consensus` / `referent-divergence` (v1) | Doc 4 discretionary (the dial) |

The closed enum is what keeps this cheap and testable: the Read never runs an open-ended "did the meaning change?" judgment (expensive, drift-prone) — it checks for a fixed set of grounded conditions. And the human-read events are **born private-by-default**: the Read emits them; Doc 4's dial and Doc 6's whisper-first decide whether the room ever hears them. A wrong process read never asserts itself.

```
SYSTEM PROMPT — THE READ (ILLUSTRATIVE)

You reason over THE MEETING GRAPH + the ESTATE GRAPH. You never read the transcript.
You are invoked only on material change. Output grounded structure + a <=2-line gloss.

Write, each grounded to specific nodes (cite claim/decision/utterance ids):
  where_it_is    : goal · current_blocker · momentum{converging|circling|stalled}
  heading        : predicted next decision + rough ETA, from decision velocity vs.
                   agenda-remaining vs. clock; and what will NOT be reached
  contributing   : who is driving · who dissents · who OWNS an affected system in the
                   blast-set but has ZERO utterances this session
  human_read     : emit ONLY when a named, grounded condition holds. v0 signals:
                   silent_owner(owner, node_id)
                   unaddressed_concern(concern_id, decision_id)
                   # v1 (needs calibration): false_consensus, referent_divergence

Then emit interpretation-shift events (closed enum only). Hard types fire Doc 4 floors:
  decision_final · became_irreversible · tension_edge_opened · commitment_on_checkable
Soft types feed the dial:
  silent_owner · unaddressed_concern  (v0)   |   false_consensus · referent_divergence  (v1)

Rules:
- Observable behavior grounded to utterances ONLY. Never infer emotion, engagement,
  or enthusiasm — you cannot see them, so you do not report them.
- Every signal names its grounding. No grounding, no signal.
- If nothing material moved, emit an empty update. Silence is correct most of the time.
```

## Component 8 — The Scribe: one accountable agent over it all

**Overview.** The Scribe is the one agent that owns the page. The engine (Graphiti) and the Read do the routine work; the Scribe is the intelligence that steps in exactly where they hit something they can't safely handle. It turns a would-be silent error into either a resolved fact or an honest open question — never a low-confidence guess.

**How we wire and optimize it.** The Scribe is a Claude Agent SDK session — think → call-a-tool → read-result → repeat. It is invoked on escalation: a sub-floor referent, a claim contradiction the engine flagged, garbled input, or an unknown entity. Its tools let it actually investigate: read the raw transcript span (the one place the verbatim log is read, and only on demand), query the Estate Graph, re-run coreference with more context, edit the page through the gate, write an open question, or fire a quiet clarifying DM to a participant. The Read is this same agent's periodic deliberate pass. Escalations are rare — a handful per meeting, cents — which is exactly why we can afford real intelligence there. We keep the Scribe off the routine path: it costs money only when ambiguity genuinely needs it, so capture stays cheap and the intelligence sits precisely where it earns its keep.

```
SYSTEM PROMPT — THE SCRIBE (ILLUSTRATIVE)

You are the Scribe: the one agent accountable for THE MEETING PAGE. The graph engine
and the Read maintain it under you. You are invoked when they ESCALATE — a referent
below the floor, a contradiction, garbled input, or an entity no one has mapped.

Your job: resolve the ambiguity into a grounded fact, or an honest open question.
Never let a low-confidence guess reach the page.

Tools:
  read_transcript_span(from_ts, to_ts)     query_estate_graph(node|query)
  rerun_coref(referent, extra_context)     edit_page(patch)  # through the gate
  write_open_question(text)                clarifying_dm(person, question)

Procedure:
- Investigate before you write. Read the span, check the graph, re-run coref.
- If you can ground it: edit_page with the resolved fact + its check-pointer.
- If two claims genuinely conflict: leave the tension edge; do not pick a winner.
- If you still cannot resolve it: write_open_question, or DM the one person who
  would know. Abstain rather than assert. Silence is a valid, honest output.

Every write is stamped to the current utterance (the one clock).
```

## Component 9 — The self-improvement flywheel: corrections are free data

**Overview.** Every time a human fixes the page, or the Scribe resolves an escalation, or a write is accepted or rejected, that is a labeled example of the right answer. We log all of it — cheaply, from day one — and use it to get better.

**How we wire and optimize it.** **All v0 does here is log** — every correction, escalation resolution, and accept/reject verdict is written to a shadow ledger as labeled data. That's one logging decision, near-zero work, and it's the only part that must be day-one, because you can't recover data you didn't capture. *V1:* feed that corpus as **dynamic few-shot examples** into the prompt-cached extraction and Read prompts so the system adapts to an org's own vocabulary within a meeting or two — **no fine-tuning pipeline until later.** Don't build the adaptation loop in v0; just make sure you're logging so it's there when you want it.

## Component 10 — How downstream consumes the page: the load-bearing seams

**Overview.** The page isn't built for humans to read (though they can) — it's built for the rest of Proxy to react to. Four seams are load-bearing, and each is an optimization that makes the rest of the system cheap and fast.

- **Constant-time reads.** Every consumer reads the small structured page (Graphiti hybrid search, ~300 ms, no LLM), never the growing transcript — so a two-hour meeting is no more expensive to reason over than a five-minute one. This is why cost and latency stay flat with meeting length.
- **Each resolved referent benches an expert.** When "checkout" resolves to its estate node, that resolution summons checkout's expert (Deep Dive 01) to the live bench and stands it down when the topic leaves. The page's links are the routing — no separate router, no classifier, no LLM call to decide who handles what.
- **Each check-pointer's freshness stamp drives the tier decision.** A claim stamped static-and-valid routes a later "is that true?" to a cache read; a versioned-or-expired claim routes it to a live lookup. This is exactly what lets the action layer's ~10 ms router decide how much work a question needs, with no model call.
- **Shift-events fire the proactive floors.** The Read's typed hard shift-events map one-to-one onto Doc 4's three forced-development floors, so the most important proactive guarantees fire deterministically off Doc 2's output, with nothing re-derived downstream.

## Component 11 — Merge-back at close: the one deliberate reconciliation

**Overview.** *(Additive — add when you support recurring meetings; a single meeting works without it.)* When the meeting ends, everything it learned that belongs in the company's long-term memory folds back into the Estate Graph — so next week's standup starts already knowing today's decisions.

**How we wire and optimize it.** The meeting graph's final state is merged onto the affected estate pages, with the meeting as the source. Graphiti's bi-temporal model makes this natural: on conflict with an existing estate claim we **supersede-with-timestamp** and route to re-verify — never a silent overwrite; both truths are kept, the new one stamped, and Deep Dive 01's re-verification runs. Recurring meetings pre-seed from their predecessor's open tail via a **`continues` edge**, so a meeting series becomes a queryable thread and open questions carry forward. Then the ephemeral FalkorDB meeting graph is torn down. This is the one place the two memories deliberately reconcile; everywhere else the single-writer page, the one clock, and the per-row ledger keep the society coherent in real time.

---

# PART D — SCALING AND OPTIMIZATION

**How the layer stays cheap, fast, and length-proof — one system at any size.**

- **Reason rarely; capture cheaply.** The strong model runs only on the sparse Read (cents per segment). Capture runs on a cheap extraction model over coalesced episodes. The judgment stays frontier-grade exactly where it's rare enough to afford.
- **Ingestion is background; reads are instant.** Nothing that reads the page waits for ingestion. Reads hit Graphiti's hybrid search at ~300 ms with no LLM in the query path. This is the entire latency design — no lanes, no scheduler.
- **v1 cost levers, held until measured.** Self-hosted static embeddings (model2vec/potion, up to 500× faster on CPU) take embedding calls to ~zero, and a self-hosted coref model (Maverick) is near-free per call. Both are swap-in optimizations added when volume or referent recall demands them — not v0 spend.
- **Cost is flat in meeting length.** Every consumer reads the small page, never the transcript, and the utterance log stays off the read path — so a three-hour meeting costs essentially what a thirty-minute one does. Cost tracks the size of the understanding, which is bounded.
- **One system, every size.** Nothing branches on company size, meeting type, or vertical. Two people or a hundred, a startup standup or a board review, a fintech or a hospital — same schema, same engine, same Read. Size and type differ only in content, never in the machine.
- **The engine is free and open.** Graphiti (Apache-2.0), FalkorDB (open-source), model2vec (open-source) — the software cost is zero. The only spend is the extraction and Read LLM calls, which we were paying anyway; adopting the engine removes the engineering cost of building and maintaining a temporal-KG store.
- **Vendors sit behind swap adapters.** Speech-to-text, the extraction model, the embedder, and the graph engine itself are all behind the same adapter discipline — a cheaper or better one is a one-line swap, and the page contract never moves.
- **The number.** This layer runs for cents per meeting-hour of model spend on top of the STT plumbing, inside Proxy's full ~$1.05–1.75/hr v0 envelope, and gets cheaper and more accurate the longer an org uses it as the shadow-ledger few-shot adapts.

---

# DONE WHEN

These are the acceptance criteria — the build target. Meet the v0 set and the layer works; the v1 set is added on measurement.

**v0 — the core is done when:**

- speech becomes a grounded graph: on a real multi-party recording, each substantive utterance lands as a claim/decision/referent resolved against the Estate Graph, within a couple of seconds of the turn completing;
- ingestion runs in the background and provably never blocks a read — a read that races an in-flight episode returns the last committed state rather than waiting — and any downstream read returns in constant time (~300 ms) regardless of meeting length;
- extraction (Haiku or Sonnet) produces schema-valid output that passes the deterministic gate; **no invented estate IDs, no silent status reverts**, and anything that fails escalates to the Scribe rather than dropping;
- referent resolution beats its accuracy floor, and every sub-floor referent is written as an open question and never asserted;
- **coverage is measured**: on a labeled replay set, the fraction of ground-truth claims, decisions, and raised concerns the page captured meets its floor — a missed salient fact is a first-class failure, not just a mis-write;
- a claim at minute three contradicted at minute twenty is catchable from the page alone, as an invalidated edge, with no model re-reading anything;
- the Read runs only on material change and writes grounded interpretation, prediction, and its two v0 human signals (silent-owner, unaddressed-concern) — each naming its grounding, none reporting inferred emotion — and demonstrably improves downstream answer quality versus graph-facts-alone on a replay set;
- the typed shift-event enum fires Doc 4's three hard floors deterministically on replay, and human-read events default to private surfacing;
- a participant can correct the page mid-meeting and it takes effect immediately through the same gate;
- the final page state produces the post-meeting outputs with no additional pass;
- the shadow ledger logs every correction and verdict from day one;
- and **nothing branches on meeting type or company size** — a two-person standup and a hundred-person board review run the identical mechanism, differing only in content.

**v1 — added on measurement, no architecture change:**

- Maverick coref, *if* v0 referent recall misses its floor on messy/overlapping speech;
- model2vec self-hosted embeddings, *when* embedding cost or latency at volume justifies it;
- the two calibration-sensitive human signals (false-consensus, referent-divergence), each with a labeled false-positive target before it surfaces;
- the few-shot adaptation loop over the shadow ledger, with measurable per-org improvement;
- merge-back into the Postgres Estate Graph at close (supersede-with-timestamp, `continues` edge for series), when recurring meetings are supported.
