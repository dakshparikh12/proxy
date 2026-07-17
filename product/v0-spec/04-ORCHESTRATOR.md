# Doc 04 · Proxy, the Orchestrator — Build Spec

*Build order: 5th. Wires together Docs 01 (index), 02 (voice/transport), 03 (notes) and dispatches to Doc 05 (workroom). The proactive door is V1-dormant (cut from V0); the ordered close lives in §3.16 (over Doc 03's close pass). This document is the complete description of what to build and exactly how it must work; acceptance criteria and tests are generated from it separately.*

*Enhancement note (this revision): the CORE is unchanged — Proxy is the agent on the call; standing pipes vs. judgment wakes; reflexes-in-code only for physics; zero situation-conditional branching. What this revision adds is the **durable engineering substrate** underneath that core, adopted line-for-line from our funded sibling repo `~/platform` (same Claude Agent SDK, same GCP/Cloud-Run/Postgres stack, same hard problems). See `PLATFORM-ADOPTION.md` Parts II, III, V, XIII. Two truths their production comments repeat — and that our design must be built around rather than rediscover in production — govern everything below: **(1) SDK tool calls execute where `query()` is invoked unless you force them remote; (2) SDK sessions live on one instance's local disk and die on autoscale.** The `meeting_runtime` runs on a **GCE MIG** (or small GKE pool — **AMENDMENT A1, 2026-07-17**) with real grace periods, so drain events are rare, not routine; the durability substrate below is **defense-in-depth**, not a crutch against 10s SIGTERMs — and it adds no broker.*

---

# 1 · The end goal

Proxy is **the agent on the call** — the one persistent identity that joins, runs the meeting session end to end, and conducts everything the other documents build. Docs 01–03 and 05–08 are its **instruments**: the index is its knowledge supply, the transport its ears and mouth, the Scribe its running memory, the workroom its hands, the judge its discretion. Each instrument owns its *own* internal wiring; **Proxy's place is the seams between them that need judgment** — and the session itself.

Stated precisely: **Proxy is a thin, dynamic orchestrator-agent that routes anything to anywhere it needs to go — asks to the workroom, events to the judge, results back to the room — deciding each case in the moment, with zero hard-coded situation handling, while doing none of the thinking itself.** It is the switchboard *and* the judgment about what the switchboard should do; it is never the brain doing the work.

Concretely, when this layer works:
1. Someone says "Proxy, can you check X?" — within half a second Proxy acks ("on it"). If it's a simple grounded lookup, Proxy answers it **directly in its own wake turn** (~1–2s, via the mounted `code_intel` tools that hit the **host-side `code_intel` internal API** — no E2B and no Workroom session on the direct path); if it's real work, Proxy bundles the ask with a `notes_ref` (the meeting context stays by reference, never the notes object inline) and dispatches it to the workroom, then presents the result when it finishes — the right channel, the right moment, still-relevant checked (the two-path decision is CANONICAL §11.6).
2. A claim lands in the notes → the event flows to the proactive judge on a standing pipe (no Proxy involvement); if the judge clears a contribution, Proxy surfaces it — at a boundary, never over a human, human answers always first.
3. Three things run in parallel — a long build, a quick question, the Scribe ticking — and nothing collides: results come back to the right places, the mouth says one thing at a time.
4. Something breaks — the Scribe stalls, a workroom dies, the bot drops, **the orchestrator's own Cloud Run instance is recycled mid-meeting** — and Proxy *handles* it: restart, degrade, re-join, or tell the room honestly. The product self-corrects because the agent of the call is responsible for the call.

**What this layer is NOT.** Proxy does no thinking-work: no code reasoning, no content generation, no meeting comprehension (Scribe), no worth-judging of proactive contributions (the V1 proactive judge), no rendering (Doc 08). It never manages the workroom's *internals* — it dispatches and receives. And it is never user-facing as "the Orchestrator" — to the room there is only **Proxy**.

---

# 2 · The design

**The core split — standing pipes vs. judgment moments.** Two kinds of flow run through the meeting, and they are built differently:

- **Standing pipes (plumbing — wired once at join, run continuously, cost nothing, no agent involved):** audio → STT → the transcript feed · transcript → the Scribe · Scribe → material-change events → the proactive judge · component heartbeats. These never need a decision — they are the session's nervous system, constant connections set up at session start. *Baking these in is not hard-coding a situation; it's wiring an organism.*
- **Judgment moments (Proxy wakes):** a confirmed ask · a result returning · a judge-cleared contribution · an anomaly · a control command · meeting end. Each of these needs a *call* — what to do, how, whether, when — and that call is made by **Proxy the agent**, dynamically, per case. Components **notify Proxy themselves** (the workroom finishing *is* the wake signal) — Proxy never polls or watches; it sleeps between wakes.

**Proxy the agent — one thin session, tools not wiring.** Proxy is a single persistent agent session (cheap model, prompt-cached identity + rules + a live session-state digest). When a judgment moment fires, it takes **one agent turn**: the event + current state go in; **tool calls** come out. Its tools *are* the routes: `dispatch_workroom(bundle)` · `speak(text)` · `send_chat(text, dm?)` · `show_screen(artifact)` · `ack()` · `cancel_task(id)` · `patch_notes(correction)` · `restart_component(name)` · `run_close()`. Nothing is branched per situation in code — a novel situation gets the same treatment as a common one: the agent reads it and picks tools. **That is the dynamism guarantee: the routing table is a prompt and a toolset, not a wiring diagram.**

**Reflexes (the only code-not-agent behaviors — physics, not decisions):** barge-in must kill speech in <200ms · voice may start only on a turn boundary · the canned **"on it" ack fires within 0.5s** of a confirmed address · task bookkeeping (semaphore, duplicate-attach) ticks mechanically. These are sub-second mechanics where a model in the loop would be malpractice. Everything with any judgment content belongs to the agent.

**Two doors, one machinery — V0 opens one.** *Asked* (reactive): confirmed address → ack reflex → **Proxy's wake judgment picks one of two paths (CANONICAL §11.6):** a simple grounded lookup is answered **in the wake turn itself** via the mounted `code_intel` tools (the ~1–2s path); real work is bundled (the ask verbatim + speaker + a `notes_ref` + the transcript tail + a task id) → workroom, where the workroom does **all** the thinking — including deciding whether a clarifying question is needed (the question returns through Proxy's mouth like any result). *Noticed* (proactive) is **cut from V0**: its design is complete (register) and it later drops in as a second door into this same machinery — notes events → judge → cleared contributions surfacing under the same delivery rules, with the standing law **a human ask always preempts an un-spoken proactive item** (the delivery priorities are already written generically for this reason).

**The substrate beneath (this revision).** All of the above is the *judgment* layer, and it stays exactly as written. Underneath it sits an **engineering substrate** that makes the single asyncio session survivable across a multi-hour meeting on autoscaling infra: one normalized agent-output stream (the provider seam + `AgentChunk`), wake-behaviors as declarative config not code, two-tier session durability, a heartbeated durable-operation row for the harness, atomic-claim meeting ownership, a simplified `ManagedResource` lifecycle for the per-meeting sandbox, and a live cost circuit-breaker. **None of it adds a broker or a bus** — Postgres rows + wall-clock + the asyncio Task remain the whole runtime. §3 builds it.

## THE REACTIVE FLOW — the core of V0, end to end
*(This is the product. Everything else in every document exists to make this one journey excellent. It is deliberately integrated — not a separate engine or doc — because it is exactly one thing: an ask, routed by Proxy from the live conversation to the workroom and back to the room. This section narrates the whole journey in one place; the mechanics live in the referenced docs.)*

**How an ask arrives (all first-class, anyone in the room):**
- **Spoken** — "Proxy, …" mid-conversation (the name-gate hears it, the disambiguator confirms it, ~300ms).
- **Typed** — `@proxy …` in the meeting chat (no disambiguation needed); a DM where the platform supports it.
- **A follow-up** — "run it again at 2×," "how does that compare?" — resolved against Proxy's own prior results on the notes.
- **A correction** — "that's wrong, we decided Friday" — routed as a notes patch; "actually make it 100/min" mid-build — routed *into the running task's live plan*.

**The journey, on a clock:**
1. **T+0** — the ask lands. Doc 02 delivers it attributed (who, when, exact words).
2. **T+0.5s at most** — the reflex **"on it"** (canned, instant — dead air is the enemy). If Proxy is mid-answer on something else, the ack is FIFO-honest: *"on it — right after Sam's."*
3. **One wake turn — Proxy picks the path** (this is the latency-critical DECISION, CANONICAL §11.6; it is **Proxy's wake judgment, not a code branch**). There are exactly two paths, and *not* every ask dispatches to the workroom:
   - **DIRECT ANSWER — the ~1–2s path.** A **simple grounded lookup** ("where's the checkout retry logic?", "what writes the `refunds` table?") is answered **in Proxy's own wake turn**, using the mounted `code_intel` tools (`get_dependents` / `who_writes` / `list_entry_points` / `grep` / `batch_read` / `read`). Those reads call the **host-side `code_intel` internal API** (one ~50–100ms hop against the warm graph + pinned clone, CANONICAL §12.2) — **no E2B and no Workroom session on the direct path** (the sandbox is Workroom-only). This is the path the ~1–2s SLA is measured on; the map orients, one grep/LSP/`get_dependents` call lands it, Proxy speaks the cited answer.
   - **WORKROOM DISPATCH — minutes-scale, and honest about it.** **Real work** (trace deep impact, build a feature, run a simulation, write a report) is bundled — **the ask verbatim + the speaker + a `notes_ref` (= the meeting_id; the Workroom reads the live notes via Doc 03's `GET /internal/notes/{meeting_id}` reader) + the raw transcript tail + a task id** — and dispatched to the workroom. These are **not** claimed to be ~1–2s: Proxy says "on it" and delivers when done. Proxy adds no interpretation; on this path the workroom is the brain.
4. **On the workroom path, the workroom judges the size itself** (Doc 05 — no router; the direct-answer path above never reaches here):
   - **Quick** (a pointed check that still needs the sandbox): a few seconds; the tile shows *working: "checking payments/retry…"* so the wait is legible.
   - **Medium** (needs a few reads or a verification): several seconds, same legible working state.
   - **Large** (build/analyze/simulate): **detaches** — *"that'll take ~10 minutes — I'll have it"* — with progress lines as real state changes; the meeting never blocks, and other asks keep flowing in parallel.
   - **Ambiguous**: **clarify-or-declare** — if the alternatives would change what it *does*, ONE question comes back ("production or staging?"); otherwise it states the assumption aloud and proceeds ("assuming production — say if you meant staging").
5. **Delivery, at the right moment** (below): the **headline is spoken** at a turn boundary, the **detail + receipts land in chat** (cited `file:line`, honesty-tagged), an **artifact** (a draft PR, a document, a running test) links or shows on screen. A late-finishing answer gets a still-relevant check — the room moved on → chat, not voice.
6. **The result lands on the notes** — which is what makes the *next* follow-up compose ("it" resolves), and what the close file records.

**The breadth guarantee (never hard-coded — these are examples, not categories):** anything a sharp colleague could be asked — *answer* ("what's our retry policy?") · *explain*, adapted to the asker's depth · *advise* ("should we ship Friday?" — a grounded opinion with evidence attached) · *help/coach* ("walk me through changing this") · *do* ("build it," "write the report," "model churn at 9%," "search the web for X") · *meta* ("catch me up," "what can you do?", "what would you do?" (dry-run), "how did you get that?" (show-your-work), "keep answers shorter"). One pipeline serves all of it; the workroom's judgment — not a type system — decides the treatment.

**The manners, always on:** a human starting to speak stops Proxy mid-word (the rest finishes in chat) · "Proxy, quiet" silences the voice, visibly · a human ask preempts everything else Proxy wanted to do · it never claims more than it verified (✓ resolved vs ~ lower-bound, visible) · it declines by naming exactly what's missing, never bluffs · every failure is spoken plainly, never silent.

**Live modes — human-activated only; Proxy may offer (session preferences, honored immediately):** the **shared screen** and the **verbal walkthrough** (exact real-time narration of actions as it performs them) turn on only via a human's ask (*"show us what you're doing," "walk us through how you're doing it"*) or a human's yes to Proxy's single offer (*"want me to show you / talk through it?"*) — never self-initiated; *"stop sharing" / "quiet narration"* end them instantly and always win. Narration obeys all turn-taking manners (speaks only into silence; drops to tile/chat when people talk). Other spoken preferences (*"keep answers shorter," "stop the decision notes"*) are held in the session-state digest and applied from the next turn.

**Who contributes what (the orchestration map, one line each):** Doc 01 makes the code findable instantly · Doc 02 hears the ask and carries the answer · Doc 03 supplies the meeting context that makes answers *about this conversation* · Doc 04 (this doc) runs the journey — ack, bundle, dispatch, deliver, keep order · Doc 05 does the thinking and the work · Doc 08 makes every step visible and calm. If any step of this journey is unclear to a builder, this section wins over any other phrasing.

**Delivery (how Proxy presents — rules it applies with judgment, not a classifier):** one thing at a time · priority: human answer > hard-floor proactive > gated proactive · voice only into a boundary · channel from the result's own structure — `headline`→voice (+ text copy always), `detail`→chat (DM where supported), `artifact`→screenshare (sequenced with the camera) · late results re-checked for relevance (room moved on → chat instead of voice, or hold) · async etiquette ("that'll take ~20 minutes — I'll ping you") · barge-in → abort and re-decide, not blindly re-speak.

**Self-correction (the product working properly, agentically).** Health arrives as events like everything else: a missed heartbeat, a workroom exception, transcript quality collapsing, the bot dropping, **the harness heartbeat going stale (the orchestrator instance itself died)**. Proxy *handles* them with the same judgment turn — restart the component, degrade gracefully, tell the room plainly ("I hit an error on that — here's what I have"). For a repeated or unrecognized failure, it **dispatches a diagnostic task through the workroom it already commands** ("figure out what's wrong; propose the fix") and acts on the answer. Supervision is not a separate watchdog subsystem — it is Proxy being responsible for its own call, plus the durable substrate (§3) that lets a *replacement* instance detect the death and resume the call.

---

# 3 · The build

```
JOIN (Recall webhook / calendar) — arrives at-least-once, possibly at ≥2 instances
 └─ ATOMIC-CLAIM meeting ownership (§3.6) — the claim INSERTs the operation_runs row directly,
    one harness per meeting, no Redis
    └─ heartbeat the same durable operation_runs row (§3.7, withOperationRun) — heartbeat starts
       └─ ensureRunning the meeting sandbox (§3.9, ManagedResource) — idempotent, race-safe
          └─ session setup: bind tenant↔repo · index readiness check · pin SHA · seed sandbox
             · post consent line · WIRE THE STANDING PIPES (audio→STT→transcript; transcript→Scribe;
             Scribe events→judge; heartbeats) · start the reflex layer · start Proxy's agent session
RUN (event-driven, for hours — survives instance recycle)
 ├─ reflexes (code): barge-in abort · boundary gating · 0.5s ack on confirmed address · bookkeeping
 ├─ wake: confirmed ask        → agent turn → EITHER direct-answer via host-side code_intel (§12.2)
 │                                            OR bundle(notes_ref) + dispatch_workroom (FIFO ack if busy)
 ├─ wake: workroom notifies    → agent turn → present (channel/timing/still-relevant) or handle failure
 ├─ wake: judge-cleared item   → agent turn → surface under delivery rules  *(V1 — dormant in V0)*
 ├─ wake: control command      → agent turn → mute / cancel / status
 ├─ wake: anomaly/heartbeat    → agent turn → restart / degrade / disclose / diagnostic task
 └─ wake: cost threshold       → checkMeetingBudget (§3.13) → degrade + tell the room (never a silent cliff)
CLOSE (meeting-end signal from Doc 02)
 └─ Proxy runs the ordered close (§3.16): freeze notes → trigger close pass (Doc 03) →
    destroy sandbox → complete the operation_runs row → teardown last
RECONCILE (Cloud Scheduler, every 5min — §3.8)
 └─ reap stale-heartbeat harnesses (operation_runs) · destroy sandboxes for ended meetings ·
    notes-retention sweep
```

**The build has two layers, and this section keeps them cleanly separate.** The **judgment layer** (§3.1–§3.4) is the core — the name-gate, the wake turn, the harness, dispatch, bookkeeping — and is unchanged in intent from the original design. The **durable substrate** (§3.5–§3.13) is what this revision folds in from `~/platform` so the judgment layer survives real infra. Everything in the substrate obeys one discipline, stated once and never violated: **config configures the capability surface (which tools, which context, which model); model judgment makes the choices (which behavior fires, what to do).** There is no rules-table of "if speaker says X do Y" anywhere in this layer — not in the harness, not in the typed `BehaviorConfig`.

## 3.1 · Address detection (the front gate, mostly free)
The transcript feed is scanned mechanically for the name ("Proxy") and chat for `@proxy`. A name-hit triggers one **tiny disambiguation call** ("addressed to me, or 'proxy server'?" — pennies, only on hits). Confirmed → the ack reflex fires and Proxy wakes. Chat `@proxy` needs no disambiguation. Proxy's own transcribed speech is never a hit (Doc 02 marks it).

## 3.2 · The wake turn (the whole agent, precisely — and how it works under the hood)
The mechanics are two pieces:
- **The harness** — one plain asyncio program per meeting. It owns the real environment: the transcript/chat websockets, the standing pipes (pure forwarding), the task table, and the registered tool functions (speak/chat/screen/dispatch/… — thin wrappers over the other docs' APIs). "Knowing when things are done" is a **completion callback**: every dispatched workroom is an `asyncio.create_task(...)` with a done-callback — the runtime delivers the done-moment; nothing polls. Every signal (webhook, heartbeat, boundary) likewise lands as an event on a queue.
- **The agent** — one persistent **Claude Agent SDK session** (`[Haiku-class]`) the harness keeps open all meeting, reached **only through the provider seam** (§3.3) so its output is a normalized stream. A wake = the harness sends one message: *the event + a compact state digest* (tasks in flight, pending items, mouth free/busy, component health). The model replies with **tool calls**; the SDK routes each to its registered harness function, which does the real thing. Turn over; the session idles (idle = stored history, zero tokens).
- **Cached prefix (1-hour-TTL breakpoint — CANONICAL §10.1):** identity, delivery rules, roster, state digest — so each wake pays only the event (~1s, cents per meeting). The manual `cache_control` breakpoint sits at the **end of this stable prefix** and carries a **1-hour TTL** (not the 5-min default); the volatile tail — the wake event + the transcript tail — is placed *after* the breakpoint so it never invalidates the cached block. The long TTL is load-bearing: wakes are **sporadic** over an hour-plus meeting, so a 5-min cache would silently expire between wakes and re-pay the full cache-write every single wake. Same session all meeting → Proxy remembers its own prior judgments.
- **State-digest compaction (CANONICAL §10.2 — firms "long histories compact into the digest"):** the wake turn is primed by the **compact state digest**, never raw session history. That digest (tasks in flight, pending items, mouth free/busy, component health) is **regenerated every `[~15]` wakes or on a material-state change** — the same rolling-summary discipline Doc 03 specifies for the Scribe's Segment B. The **Notes object is the durable memory that survives compaction**: Proxy reads it on demand through `notes_ref` (= the meeting_id) rather than carrying it in the session history, so a compaction never drops meeting state. **The read path is the durable one (CANONICAL §11.4):** the live notes are folded from the `note_deltas` Postgres table via **`GET /internal/notes/{meeting_id}`** — **not** the Scribe's in-process `NOTES_CACHE` (which is a scribe-hot-path optimization only, and not readable from the orchestrator turn or a foreign host). The SDK's **context-editing** (auto-clear stale `tool_result` blocks) is available for the longest turns; the **1M-context model variant is a costlier fallback only**, taken solely if a meeting genuinely needs full raw history.
- **The dynamism, concretely:** there is no `if event_type → action` anywhere in the harness. The harness only says *"here's what happened, here's the state"*; the situation→action mapping lives entirely in the model's turn. An unanticipated situation is just another event description handled by the same judgment. Access to the environment is exactly the tool manifest — no more, no less.

**SDK isolation on every call (mandatory — `PLATFORM-ADOPTION.md` III.2).** Proxy's own wake session is cheap and tool-light, but the moment it — or any structured sub-call it makes — touches the SDK, it sets the isolation triad: `strict_mcp_config=True` (ignore ALL discovered `.mcp.json` / user settings / claude.ai connectors like Gmail/Slack/Drive), `setting_sources=[]` (load no filesystem permissions/hooks/CLAUDE.md — and note that `setting_sources=[]` *alone does NOT* suppress connectors; you need both), and a computed built-in `tools` list. This matters even more in Doc 05 (the workroom runs tools in E2B), but it is a property of *every* SDK call site, so it lives in the shared seam below.

## 3.3 · The provider seam + the normalized `AgentChunk` union (the ONE consumer)
*(`PLATFORM-ADOPTION.md` III.1 / V.4 / XIII.2. Their `chat/providers/*` + `AgentService.ts`.)*

The SDK is **never called from harness business logic.** Every *seam-routed* agent call — Proxy's wake turn and the Workroom build (Doc 05) — goes through one provider seam that yields a **normalized, provider-neutral stream of `AgentChunk`**. **The one exception is the Scribe micro-call (Doc 03): it is a bare `anthropic.AsyncAnthropic().messages.create` and does NOT go through the seam** — correcting the earlier claim that "every call incl. the Scribe goes through the seam." (The Scribe meters its own spend by writing the `meeting_cost` table directly, §3.13; the seam-based meter writes the same table.) Providers stay *dumb* (translate native SDK events → `AgentChunk`, re-throw nothing in-band); one central place owns every cross-cutting concern: delta computation, cost metering, abort, tracing, the SDK-isolation triad, stale-session recovery. This is the seam that lets a role run a cheap family later without rewriting anything, and — more importantly for Proxy — it is the source the **barge-in / TTS substrate** consumes (start speaking on the first `TEXT` delta, stop on abort).

**The consumer contract (canonical, CANONICAL-DECISIONS §1.1):** raw `TEXT.text` is *accumulated per `msg_id`*, so **no consumer reads raw chunks.** `stream_deltas()` (below, in `libs/agentkit`) turns the raw stream into true deltas; **every consumer — the per-channel projector (Doc 08), the cost meter, the transcript logger — reads the `stream_deltas` output, never a raw `AgentChunk`.** Field access is `chunk.type`, `chunk.metadata["name"]`, `chunk.metadata["structured"]` — never `.kind` / `.tool` / `.structured` as top-level attrs.

**`stream_deltas` is applied EXACTLY ONCE, and the single application site is `BehaviorRunner.run()` (§3.4, CANONICAL §12.3).** The runner wraps the provider's raw stream once; every downstream consumer receives the already-delta'd typed stream and **MUST NOT re-wrap it.** There is no second `stream_deltas` call anywhere — not in the projector (Doc 08 §4.5's re-wrap is deleted), not in the cost meter, not in the transcript logger.

`AgentChunk` and its `ChunkType` discriminator are the canonical type from **`libs/contracts`** (defined verbatim in CANONICAL-DECISIONS §1.1 — do not redeclare its shape; it is reproduced here only for reading convenience). The union has six variants, with the load-bearing contract that **`TEXT.text` is the accumulated text per `msg_id`, not a delta**:

```python
# libs/contracts/chunks.py — the ONE normalized agent-output type (canonical §1.1)
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

ChunkType = Literal["INIT", "TEXT", "TOOL_USE", "TOOL_RESULT", "RESULT", "ERROR"]

@dataclass(slots=True)
class AgentChunk:
    type: ChunkType                 # discriminator is `type` (NOT `.kind`)
    # TEXT: accumulated assistant text for metadata.msg_id (NOT a delta).
    # TOOL_RESULT: the tool result text. RESULT: final accumulated text (may be "").
    # ERROR: the error message. INIT/TOOL_USE: usually "" — payload is in metadata.
    text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    # metadata keys by variant:
    #   INIT:        session_id, tools[], mcp_servers[{name,status}], model
    #   TEXT:        msg_id, turn
    #   TOOL_USE:    name, input, id
    #   TOOL_RESULT: tool_use_id, is_error, structured
    #   RESULT:      num_turns, total_cost_usd, structured_output, subtype, session_id
```

The provider contract every implementation honors (Claude day-one; a cheap family for Scribe later without touching a single consumer):

```python
class AgentProvider(Protocol):
    name: str
    def matches(self, model: str) -> bool: ...
    def stream(self, prompt: str, q: "ProviderQuery") -> AsyncIterator[AgentChunk]: ...

def pick_provider(model: str) -> AgentProvider:   # route by model-id; unknown → Claude
    ...
```

**Pin the mapping at build, do not assume it (CANONICAL §11.10).** The Claude provider's `stream()` translates native SDK messages → `AgentChunk`, and *where* `session_id` / `total_cost_usd` / `msg_id` come from on each SDK message is a third-party wire shape a design doc cannot fix. **The build loop MUST fetch the installed Python `claude_agent_sdk`'s live message shapes and confirm the SDK-message → `AgentChunk` mapping (and the structured-output API) against them before coding this seam** — it is a confirm-at-build item, not a silent assumption.

**The streaming delta computer — the ~15 lines that make barge-in and TTS possible (canonical signature, CANONICAL-DECISIONS §11.3).** `stream_deltas` takes **ONE** argument — an upstream `AsyncIterator[AgentChunk]` (a provider's raw stream) — and yields **`AgentChunk`**, not bare `str`. It delta-izes only `TEXT` (emits the new suffix per `msg_id`, resets on a new `msg_id`) and **passes `INIT` / `TOOL_USE` / `TOOL_RESULT` / `RESULT` / `ERROR` through unchanged**, so tool events and the terminal frame survive to every consumer. This is the load-bearing correction: the earlier Doc 04 version yielded bare `str`, took 4 args, and dropped tool events — which is exactly what blocked the projector. It lives in **`libs/agentkit`**. This is also the sub-second-TTFB substrate: the first `TEXT` delta is the first thing Proxy can start speaking; abort stops it mid-word.

```python
# libs/agentkit/streaming.py — the provider seam + stream_deltas both live in libs/agentkit
async def stream_deltas(chunks: AsyncIterator[AgentChunk]) -> AsyncIterator[AgentChunk]:
    """Takes ONE upstream AgentChunk iterable. Delta-izes TEXT (yields only the new
    suffix per msg_id, resets on a new msg_id); passes INIT/TOOL_USE/TOOL_RESULT/RESULT/ERROR
    through UNCHANGED. Yields AgentChunk (not str), so tool events survive to every consumer.
    Every consumer — the per-channel projector (Doc 08), the cost meter, the transcript logger —
    reads THIS typed stream, never a raw AgentChunk."""
    last_text = ""                        # accumulated text seen for the current msg_id
    last_msg_id: Optional[str] = None
    async for chunk in chunks:
        if chunk.type == "TEXT":
            msg_id = chunk.metadata.get("msg_id", "")
            if msg_id != last_msg_id:     # new assistant turn → reset delta tracking
                last_text = ""
                last_msg_id = msg_id
            text = chunk.text
            if len(text) > len(last_text):                    # emit only the growth
                delta = text[len(last_text):]
                last_text = text
                # a TEXT chunk carrying just the new suffix ← barge-in/TTS start point
                yield AgentChunk(type="TEXT", text=delta, metadata=chunk.metadata)
            continue
        # INIT / TOOL_USE / TOOL_RESULT / RESULT / ERROR → passed through UNCHANGED
        yield chunk
```

The cross-cutting concerns that the old 4-arg version folded *into* `stream_deltas` are now **separate consumers of this one typed stream**, exactly as the canonical consumer contract requires: the **cost meter** reads `RESULT.metadata["total_cost_usd"]` off this stream and feeds the §3.13 gate (it no longer lives inside the delta computer); the **durability layer** captures `INIT.metadata["session_id"]` for resume (§3.5); and the pass-through `ERROR` chunk is surfaced as a `ProviderError` at the **`BehaviorRunner` boundary** (below), where the **stale-session/retry layer** (§3.5) catches it. `stream_deltas` itself never raises and never meters — it only delta-izes `TEXT` and forwards. One typed stream, many readers — no consumer touches a raw `AgentChunk`.

Also on the seam and worth stating explicitly for Proxy: a runtime **`[CRITICAL]` tripwire** in the chunk consumer — if a non-MCP built-in tool (`Read`/`Grep`/`Bash`) fires while the run is in remote/sandbox mode, log `[CRITICAL]` (it means the isolation triad leaked and a tool is executing on the *orchestrator host*, not in E2B). And `env` sanitization: hand the SDK subprocess a *curated* env with mutually-exclusive auth keys stripped (a leaked dev `.env` otherwise makes the SDK pick the wrong auth path), and route SDK stderr through a `sk-ant-*`/Bearer/`token=…` redactor before logging.

## 3.4 · Wake-behaviors as typed Python `BehaviorConfig` constants + one generic runner (config, not code)
*(`PLATFORM-ADOPTION.md` III.3. Their `server/src/nodes/` node registry + generic `NodeRunner`, adapted per CANONICAL §12.5 — **typed Python, not YAML.**)*

Proxy's wake-behaviors — `catchup`, `answer-question`, `surface-risk`, `propose-action`, plus the Workroom dispositions in Doc 05 (`quick`, `plan-artifact`, `critic`, `worktree-worker`, `verifier`) — are **not per-behavior code branches.** Each is a **typed Python `BehaviorConfig` module constant** describing the capability *envelope*: which role/rules prime the turn, which inputs it needs, which model tier and turn budget it runs on, which tools it may touch. One generic runner reads that constant, resolves the declared inputs, mounts exactly the declared tools, computes the SDK-isolation params, and hands one context object to the provider seam. **`REGISTRY` / `register()` / `BehaviorRunner` are unchanged** — the only change (CANONICAL §12.5) is that the ~9 behaviors are declared as typed constants rather than parsed from YAML: a `BehaviorConfig(tools=[...], model=..., role="...")` declares the identical envelope, removes a loader inherited from a 90-task-type repo, and kills the class of bug where the sample YAML silently diverged from the prose. **A small JSON/TS capability manifest is generated at build** (from these constants) for UI labels — the one place the surface crosses into TS. The **YAML-registry pattern is kept as the documented Expansion path** for when behavior count grows enough to want out-of-code authoring.

**The discipline, stated prominently because it is the whole design:** *config configures the capability surface; model judgment makes the choices.* The typed `BehaviorConfig` says **what tools/context/model a behavior may use** — never **what to do with a given utterance.** There is no `rules` entry of the form "if the speaker asks for a comparison, call `compare_tool`." The model, mid-turn, decides which behavior fires and what it does inside. Even the prompt text fights hardcoding: rule lists are framed as *"these are examples to prime your judgment, NOT a checklist that bounds it."* (`PLATFORM-ADOPTION.md` IX.5 — hold this line; it is where the boil-the-ocean trap and the don't-shrink-the-value memory both apply.)

**Curated tool subset per wake-behavior (CANONICAL §10.5).** Each behavior's `config.tools` advertises a **curated subset**, never the union of all of Proxy's tools — tool-selection accuracy degrades measurably with every extra advertised tool (the platform prunes even *available* tools per flow). `answer-question` mounts the orchestration verbs plus the `code_intel` server it actually needs; a `catchup` mounts far fewer. This sharpens the "advertised, not forced" rule rather than softening it: the model still *chooses* within the subset (no hard-coding of which tool for which utterance), but it chooses from the **smallest set that covers the behavior**, not from everything Proxy can do. `BehaviorRunner` already enforces this — `allowed_tools=b.config.tools` mounts exactly the declared subset and nothing else.

A sample wake-behavior, as a typed module constant:

```python
# behaviors/answer_question.py — a typed BehaviorConfig constant (no YAML loader; CANONICAL §12.5)
# AMENDMENT C3, 2026-07-17: normative sample regenerated with code_intel MCP tools + mcp_servers mount.
ANSWER_QUESTION = Behavior(
    name="answer-question",
    role=(
        "You are Proxy, the agent on this call. A question was addressed to you. "
        "Decide the path (CANONICAL §11.6): a simple grounded code lookup you ANSWER "
        "DIRECTLY in this turn using the mounted code_intel tools (cited file:line); "
        "real work you dispatch to the workroom and present when it returns — using "
        "the delivery rules already in your session prefix. "
        "Speak short sentences, use contractions, no enumeration, two sentences max."
    ),
    rules=[
        # Examples of judgment (NOT a decision table):
        "A simple grounded lookup ('where's the retry logic?', 'what writes refunds?') "
        "you answer directly via code_intel and speak the cited answer at the next boundary.",
        "A large build you dispatch detached with an async-etiquette line. Decide per case.",
        "Never invent an answer. If the workroom needs a clarification, relay its one "
        "question through your mouth like any result.",
    ],
    inputs=[
        "event",         # the wake payload: the ask verbatim + speaker + timestamp
        "state_digest",  # tasks in flight, mouth free/busy, component health
        "notes_ref",     # = meeting_id; live notes read via GET /internal/notes/{meeting_id} (Doc 03)
    ],
    config=BehaviorConfig(
        model=MODELS["orchestrator"],   # → Sonnet (grounded-answer tier), §3.12
        max_turns=4,
        # DIRECT-ANSWER + dispatch envelope: the code_intel read tools so it can answer
        # itself, PLUS the orchestration verbs. Curated subset (CANONICAL §10.5), not the union.
        tools=[
            "get_dependents", "who_writes", "list_entry_points", "grep", "read", "batch_read",
            "dispatch_workroom", "speak", "send_chat", "show_screen", "ack", "cancel_task",
        ],
        # The code_intel MCP server is mounted on this behavior (CANONICAL §7 / §12.5).
        mcp_servers=[{"name": "code_intel", "tools": [
            "get_dependents", "who_writes", "list_entry_points", "grep", "read", "batch_read",
        ]}],
    ),
)
register(ANSWER_QUESTION)
```

The `answer-question` behavior **mounts the `code_intel` read tools so it can direct-answer, not only dispatch** (CANONICAL §12.5) — those tools call the host-side `code_intel` API (§12.2), never E2B. The JSON/TS capability manifest generated at build reads the same `tools` list for its UI labels.

The generic runner shape — adopt their `AgentNode` / `NodeRunner` structure; ~one `register()` line per behavior:

```python
# libs/agentkit/behavior.py — the provider seam, stream_deltas, and BehaviorRunner all live in libs/agentkit
@dataclass
class Behavior:
    name: str
    role: str
    rules: list[str]
    inputs: list[str]
    config: "BehaviorConfig"      # model, max_turns, tools (the capability envelope)

class BehaviorRunner:
    """ONE runner for every wake-behavior and Workroom disposition. Reads the
    declarative Behavior, resolves inputs, mounts declared tools, applies the
    SDK-isolation triad, and streams through the provider seam. No per-behavior
    code path exists — selecting a behavior by name IS the branch, and the model
    makes that selection."""
    def __init__(self, registry: dict[str, Behavior], providers, cost_meter):
        self._registry, self._providers, self._cost = registry, providers, cost_meter

    async def run(self, behavior_name: str, resolved_inputs: dict[str, Any],
                  abort: "AbortController") -> AsyncIterator[AgentChunk]:
        b = self._registry[behavior_name]
        provider = pick_provider(b.config.model)
        q = ProviderQuery(
            model=b.config.model, max_turns=b.config.max_turns,
            system_prompt=with_proxy_guardrails(render_role(b, resolved_inputs)),
            allowed_tools=b.config.tools,
            strict_mcp_config=True, setting_sources=[],   # isolation triad
            tools=compute_builtin_tools(b.config.tools),  # [] in sandbox mode
            abort=abort,
        )
        # stream_deltas takes the provider's raw AgentChunk stream and yields AgentChunk;
        # the cost meter is a consumer of that same typed stream (reads RESULT.total_cost_usd).
        raw = provider.stream(render_prompt(b, resolved_inputs), q)
        async for chunk in stream_deltas(raw):
            self._cost.observe(chunk)     # RESULT.metadata["total_cost_usd"] → §3.13 gate
            if chunk.type == "ERROR":     # surface the pass-through ERROR as the exception
                raise ProviderError(chunk)  # §3.5 stale-session/retry recovery catches this
            yield chunk

REGISTRY: dict[str, Behavior] = {}
def register(b: Behavior) -> None: REGISTRY[b.name] = b   # one line per behavior
```

Model selection is exactly the three-tier config from §3.12 (`per-behavior config.model → per-role env default → global`). A new capability is a new `BehaviorConfig` constant + a new tool wrapper in the manifest — **never new wiring, never a new code branch.**

**The `code_intel` MCP server on wake turns (CANONICAL-DECISIONS §7).** Wake-behaviors that field grounded code questions mount the **core `code_intel` MCP server** (factory-per-query, advertised-not-forced) alongside the orchestration tools, so Proxy can answer a quick grounded code question directly — cited `file:line` — without always round-tripping the Workroom. The mounted tools are exactly the core set: `get_dependents(symbol|table)` (blast-radius from the dep graph), `who_writes(table)`, `list_entry_points()`, native `grep`/`read` on the clone, and **`batch_read` (CANONICAL §10.4) — reads up to `[N=10]` related files in one round-trip** (internally parallel, partial-failure-tolerant, one compact block), since a grounded answer usually needs 3–6 related files and one call beats N (serves the ~1–2s target). The deferred capability-map tools (`get_capability`, `search_capabilities`, `get_flow`) are **NOT** in the core — they return with the agentic map at Expansion. (The Workroom, Doc 05, mounts the same `code_intel` server for its own deeper work.)

**Within-session tool-result reuse (CANONICAL §10.8).** Proxy's grounded wake turns share a **per-meeting cache of identical `get_dependents`/`who_writes`/`grep`/`read`/`batch_read` results** — the same file or symbol is asked about repeatedly across a meeting, so an identical call (keyed by tool + args, scoped to the pinned SHA) returns the cached result instead of re-executing against the clone. This cuts both latency and spend on the repeat asks that a real meeting generates.

**Prompt discipline in the wake-turn system prompt (CANONICAL §10.9).** `with_proxy_guardrails(...)` appends one standing line to every grounded wake turn: *"prefer the compact artifact, cheapest tool first, one gather pass — pull the map/notes summaries once, prefer them over re-exploring raw code, don't re-fetch."* This biases the model toward the compact digest + the map/notes summaries + the reused tool cache (above) over re-exploring raw code, holding the ~1–2s grounded-answer target instead of fanning out redundant reads.

**Spoken register (AMENDMENT C2, 2026-07-17).** Every `speak()` utterance is composed as speech — short sentences, contractions, no enumeration, two sentences maximum. This constraint appears in **at least two prompt locations**: (1) the wake-behavior `role` string and (2) the `with_proxy_guardrails(...)` system suffix. The spoken register ensures voice output sounds like a colleague talking, not a document being read aloud.

## 3.5 · Two-tier session durability (the multi-hour meeting *will* meet a recycle)
*(`PLATFORM-ADOPTION.md` III.6. Their `AgentService` stale-session recovery @462-495. **The Tier-2 Postgres session-mirror is cut** per CANONICAL-DECISIONS §6 — the stale-session replay below rebuilds from Doc 03's transcript plane, so a second mirror was redundant.)*

**DRY (CANONICAL §11.9 / §12.12):** the stale-session replay mechanism is the pinned full **6-arg** `resume_with_fallback(runner, behavior, inputs, resume_id, abort, history_fn)`, **imported from `libs/agentkit/resume.py` — not redefined here.** It is parameterized by the history *source* (`history_fn`); Doc 04 passes Doc 03's transcript-plane reader, Doc 05 passes its own. The code below is shown for reading convenience only.

A Claude Agent SDK session lives on the **local disk of whichever instance created it.** With the `meeting_runtime` on a GCE MIG (**AMENDMENT A1, 2026-07-17**), drain events are rare (real grace periods, not 10s SIGTERMs), but a multi-hour meeting *can* still span a redeploy or a rare drain — and the next wake can land on a *different* instance. Without durability, Proxy silently forgets the whole meeting mid-call. Two tiers defend against this as **defense-in-depth**. **Single-writer-per-meeting is an explicit invariant** (§3.6 guarantees exactly one harness owns a meeting).

**Tier 1 — persist the SDK `session_id`, resume each wake.** On the `INIT` chunk of the first turn, capture `metadata.session_id` and write it to the meeting's Postgres row. Every subsequent wake passes `resume=<session_id>` into the provider query. Cheap, and covers the common case (same instance still alive).

**Tier 2 — stale-session replay (the floor that always works).** When `resume` fails because the session is gone, the SDK reports it two ways across versions — match **both**: `"no conversation found with session id"` and `"process exited"`. On a match, rebuild context from app-level meeting history (the Postgres transcript plane, Doc 03/VI.3 — the single source of durable meeting history; there is no separate SDK-session mirror), **prepend a delimited preamble**, emit a **user-visible "session restored" notice** so recovery is transparent rather than silent amnesia, and retry *without* resume (the new session's `session_id` overwrites the stale pointer). Re-audit the two match strings on every SDK upgrade.

```python
STALE_MARKERS = ("no conversation found with session id", "process exited")
RESTORED_NOTICE = ("_My session was restored from the meeting so far; "
                   "some working context may be missing._")

def is_stale_session_error(err: BaseException) -> bool:
    msg = (str(err) or "").lower()
    return any(m in msg for m in STALE_MARKERS)

# libs/agentkit/resume.py — imported, NOT redefined here (CANONICAL §11.9 / §12.12).
# Pinned full 6-arg signature; parameterized by the history SOURCE (history_fn) — Doc 04 passes
# Doc 03's transcript-plane reader, Doc 05 passes its own.
async def resume_with_fallback(runner, behavior, inputs, resume_id, abort, history_fn):
    try:
        async for chunk in runner.run(behavior, {**inputs, "resume": resume_id}, abort):
            yield chunk
    except ProviderError as e:
        if abort.aborted:              # a caller-abort is FINAL — never resurrect it
            raise
        if not (resume_id and is_stale_session_error(e)):
            raise
        # Rebuild from app history, prepend a delimited preamble, notify the room.
        history = await history_fn()   # reads the Postgres transcript plane (Doc 03)
        yield AgentChunk(type="TEXT", text=RESTORED_NOTICE, metadata={"msg_id": "restored"})
        preamble = build_history_preamble(history)   # "--- BEGIN PRIOR MEETING ---" ...
        async for chunk in runner.run(behavior,
                                      {**inputs, "resume": None, "preamble": preamble},
                                      abort):
            yield chunk
```

Two more resilience details from the same call site, ported: a **JSON-truncation retry** (the SDK's stdio pipe can truncate a large tool-result frame → `SyntaxError: unterminated string in json`; retry on the same session, cap 2) and the **abort-is-final rule** (a caller-initiated abort short-circuits *before* any recovery so a killed build can't be resurrected by resume/JSON retry).

## 3.6 · Atomic-claim meeting ownership (one harness per meeting, no Redis)
*(`PLATFORM-ADOPTION.md` II.2. Their `WorkspaceRepository.createWorkspaceIfNotExists` partial-unique-index claim.)*

Recall's join webhook is **at-least-once**, and with ≥2 orchestrator instances two of them can both receive it and both pass a naive "is there already a harness for this meeting?" check-then-act — spawning two harnesses, two SDK sessions, two mouths. Postgres arbitrates the race; read `RETURNING` / rowcount to know who won. **No broker, no Redis — the row is the lock.**

**There is exactly ONE durable-ops table — the canonical `operation_runs` (CANONICAL-DECISIONS §2 / Doc 00 §5.1); Doc 04's old separate `meeting_harness` table is deleted.** The atomic claim inserts a `meeting-harness` row *directly into `operation_runs`*, keyed on `scope_id` (which holds the `meeting_id`); the partial-unique index `operation_runs_active (scope_id, operation_type) WHERE status='running'` is the lock. **Type note (CANONICAL §11.2):** `meeting_id` is a **UUID** everywhere in the app tables (`meetings.id`, `meeting_cost`, `staged_drafts`, …); **only `operation_runs.scope_id` stays `text`** (it also holds workroom `task_id`s), so the claim below casts `meeting_id::text` at the call site — the one documented cast.

```python
async def claim_meeting(pool: asyncpg.Pool, meeting_id: str, instance_id: str) -> str | None:
    """Returns the operation_runs id if this instance won the meeting; None → someone
    else already owns it, so back off (optionally subscribe as a follower for handoff).
    The claim IS the operation_runs row — §3.7's withOperationRun heartbeats this same id."""
    return await pool.fetchval(
        """INSERT INTO operation_runs (scope_id, operation_type, status,
                                       created_by, last_heartbeat_at, started_at)
           VALUES ($1, 'meeting-harness', 'running', $2, NOW(), NOW())
           ON CONFLICT (scope_id, operation_type) WHERE status = 'running'
           DO NOTHING
           RETURNING id""",
        meeting_id, instance_id)
```

The winner opens the harness on the returned `run_id`; the loser backs off. **Instance affinity (CANONICAL §11.11):** the claim writes the **owner instance-id onto the `operation_runs` claim row** (`created_by`), so a reconnecting tile WS or a retried Recall webhook **routes to the owner instance** — the instance actually holding this meeting's harness — a non-owner instance proxies to it or hands off, rather than being scattered by random Cloud Run load-balancing. When a harness dies and its heartbeat goes stale (§3.8's reaper flips this row's `status` off `running`), the partial index frees, and a *replacement* instance can re-claim the same meeting to resume it (and becomes the new affinity owner). (This is the bug the audit caught: the old duplicate `meeting_harness` table was never reaped, so a crashed meeting was permanently un-reclaimable — collapsing to the single reaped `operation_runs` table fixes it.) For per-meeting critical sections that must be cluster-wide serial (notes finalization at close), use `pg_advisory_xact_lock(hashtext(meeting_id), 0)` — auto-released on COMMIT, and run *all* work on the locked connection (checking out a second pooled connection while holding it deadlocks). Keep the two layers platform keeps: an in-process mutex for single-process ordering **plus** the atomic claim for cross-instance.

## 3.7 · The harness as a heartbeated durable-operation row (`withOperationRun`)
*(`PLATFORM-ADOPTION.md` II.1. Their `utils/withOperationRun.ts`. The highest-value adoption in the study.)*

Keep the asyncio Task as the *executor*; make its **lifecycle a Postgres row**. This is what keeps "asyncio Task, no bus" honest while adding crash-detection, pause-of-a-running-build, and resume-state — *without* a broker. The heartbeat's activity-bump also solves "don't reap the sandbox while the agent is silently thinking" before it can bite (§3.7 heartbeat → §3.9 sandbox TTL).

**The row is the canonical `operation_runs` (CANONICAL-DECISIONS §2 / Doc 00 §5.1) — Doc 04 does NOT redefine it.** For the harness, `scope_id = meeting_id` and `operation_type = 'meeting-harness'`; the row was already created by §3.6's atomic claim, so `with_operation_run` **adopts that claimed `run_id`** (it does not insert a second row) and owns only the heartbeat + terminal-status lifecycle. `progress` carries crash-recovery/resume state; `pause_requested` is the first-class "a human interrupts the running build."

```python
HEARTBEAT_S = 10
STALE_AFTER_S = 40             # ~4 missed 10s beats → the owning instance is dead (CANONICAL §12.10)

class OperationRun:
    def __init__(self, pool, run_id, meeting_id):
        self._pool, self.id, self._meeting_id = pool, run_id, meeting_id
        self.is_owner = True   # fencing flag (§12.10): every side-effect is gated on this
    async def update_progress(self, progress: dict) -> None:
        await self._pool.execute(
            "UPDATE operation_runs SET progress=$2 WHERE id=$1",
            self.id, json.dumps(progress))
    async def check_pause(self) -> bool:
        return await self._pool.fetchval(
            "SELECT pause_requested FROM operation_runs WHERE id=$1", self.id) or False

@asynccontextmanager
async def with_operation_run(pool, meeting_id, run_id, activity):
    # run_id was returned by §3.6 claim_meeting (the atomic INSERT into operation_runs).
    # We adopt it here — one row per harness, created once at claim, heartbeated here.
    op = OperationRun(pool, run_id, meeting_id)

    async def _heartbeat() -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            try:
                # FENCING self-check (CANONICAL §12.10): scope the beat to status='running'. If a
                # reaper already flipped this row (a replacement instance re-claimed the meeting),
                # rowcount is 0 → we were fenced out. Drop ownership and self-terminate so two
                # harnesses can never both speak; every side-effect (speak/send_chat/apply/dispatch)
                # is gated on op.is_owner.
                status = await pool.execute(
                    "UPDATE operation_runs SET last_heartbeat_at=NOW() "
                    "WHERE id=$1 AND status='running'", run_id)
                if status.endswith(" 0"):          # rowcount 0 → reclaimed / fenced
                    op.is_owner = False
                    log.warning("fenced out of meeting %s — self-terminating harness", meeting_id)
                    return
                activity.bump(meeting_id)   # keeps the sandbox alive during silent work
            except Exception as e:
                log.error("heartbeat failed for %s: %s", run_id, e)

    hb = asyncio.create_task(_heartbeat())
    try:
        yield op
        await pool.execute(
            "UPDATE operation_runs SET status='completed', completed_at=NOW() WHERE id=$1",
            run_id)
    except Exception as e:
        await pool.execute(
            "UPDATE operation_runs SET status='failed', error=$2, completed_at=NOW() "
            "WHERE id=$1", run_id, str(e)[:500])
        raise
    finally:
        hb.cancel()
```

**Crash detection is a staleness read, not a broker ack.** A harness whose `last_heartbeat_at < NOW() - STALE_AFTER_S` (~40s) belongs to a killed instance — never report it running. Two reapers: a lazy per-read check, and a **boot-time bulk sweep** (`mark_stale_operations_interrupted()`) so a rebooting instance heals the DB before doing anything else. `check_pause()` / `pause_requested` **is** our first-class "a human interrupts the running build" requirement, for free; `progress` is the crash-recovery + resume state.

```python
async def mark_stale_operations_interrupted(pool) -> int:
    return await pool.fetchval(
        """UPDATE operation_runs SET status='interrupted', completed_at=NOW()
           WHERE status='running' AND last_heartbeat_at < NOW() - INTERVAL '40 seconds'
           RETURNING count(*)""") or 0
```

## 3.8 · The reconcile sweep + Cloud Scheduler (periodic work under scale-to-zero)
*(`PLATFORM-ADOPTION.md` II.4. Their `services/reconcile.ts` + Cloud Scheduler every 5min.)*

Between meetings our compute *should* scale to zero, so an in-process `setInterval`/loop is exactly as unreliable for us as for them — a scaled-to-zero instance runs no interval. One idempotent `runReconcileSweep()` behind `POST /internal/reconcile` (mounted outside the auth wall, `RECONCILE_TOKEN` bearer-gated); **prod: Cloud Scheduler hits it every 5min**; **dev: an in-process interval calls the same function** so the two paths can't drift. Each step is isolated in its own `try/except` — one bad step never aborts the rest.

```python
async def run_reconcile_sweep(deps) -> dict:
    errors: list[str] = []
    for name, step in (
        ("stale-harnesses", deps.mark_stale_operations_interrupted),   # §3.7 reaper (operation_runs)
        ("meeting-sandboxes", deps.sandbox_orchestrator.reconcile),    # §3.9 TTL/orphan reap
        ("notes-retention", deps.notes_retention.reconcile),
    ):
        try:
            await step()
        except Exception as e:
            errors.append(f"{name}: {e}")
    return {"errors": errors}
```

Split as they do: **cost-driven reaping** (stale harnesses, ended-meeting sandboxes) rides the Scheduler sweep; **availability-critical loops** — keeping a *live* meeting's STT/Recall credentials fresh — stay on an in-process interval where a warm instance provably exists (`min_instances≥1` for the meeting-serving service, `PLATFORM-ADOPTION.md` XIII.6).

## 3.9 · ManagedResource — the per-meeting compute lifecycle (simplified: E2B timeout + explicit destroy + TTL cron)
*(`PLATFORM-ADOPTION.md` II.3 + Part X TRIM. Simplified per CANONICAL-DECISIONS §6 — the `provisioning/running/stopped/failed` state machine, the sliding-TTL under-lock re-check, and the stuck-provision recovery are all cut. One meeting, one sandbox; we do not need a state machine to manage it.)*

The per-meeting sandbox (E2B) is a `ManagedResource` with **three defenses, no state machine**:
1. **E2B-native sandbox timeout = the crash backstop.** Set the sandbox's own `timeout`/`maxRunDuration` at provision (their `instanceTerminationAction:DELETE` analog). If the orchestrator dies and never calls `destroy`, E2B self-reaps the sandbox — no reconcile tick required for correctness.
2. **Explicit `destroy` on meeting-end** — the ordered close (§3.16) tears the sandbox down deterministically in the happy path.
3. **A simple reconcile-cron** (§3.8) that **lists all live sandboxes and kills any past its TTL** (labelled/tagged by `meeting_id`, cross-checked against ended meetings). No per-row status transitions, no under-lock idle re-check — just "list, find the orphans, destroy them."

**The provider verbs stay idempotent** — the load-bearing assumption that keeps the whole thing broker-free: a repeat `provision` returns the existing sandbox, `destroy` tolerates a 404 (`'gone'`), `health` is read-only. `ensure_running` is the one race-safe call the harness uses.

```python
class SandboxProvider(Protocol):
    async def provision(self, spec: "ProvisionSpec") -> "Meta": ...  # create E2B sandbox w/ native timeout; idempotent per meeting_id
    async def destroy(self, meta: "Meta") -> None: ...               # tolerates 404 → 'gone'
    async def health(self, meta: "Meta") -> "Health": ...            # ok | transient | gone (read-only)

class MeetingSandboxLifecycle:
    """One meeting → one E2B sandbox. No state machine — the E2B-native timeout is the
    crash backstop, meeting-end calls destroy, and a TTL cron kills orphans."""

    async def ensure_running(self, meeting_id: str, *, provision_if_missing) -> "Meta":
        """Idempotent 'get me a healthy sandbox for this meeting NOW'. provision is
        idempotent per meeting_id, so a redelivered join can't double-provision."""
        row = await self._repo.find(meeting_id)
        if row is not None:
            h = await self._provider.health(row.meta)
            if h.ok:
                return row.meta
            # gone/transient → the old sandbox is dead; provision a fresh one (idempotent)
        meta = await self._provider.provision(await provision_if_missing())
        await self._repo.upsert(meeting_id, meta)
        return meta

    async def reconcile(self) -> None:
        """The §3.8 cron: list live sandboxes, destroy any orphaned or past-TTL."""
        for meta in await self._provider.list_sandboxes():
            if await self._is_orphan_or_past_ttl(meta):   # meeting ended, or age > TTL
                await self._provider.destroy(meta)         # tolerates 404
```

The E2B-native timeout means a dropped reconcile tick never leaks a sandbox forever; the cron is a cost optimization (reap sooner), not a correctness requirement. Health that returns `gone` → provision a fresh sandbox, not a doomed restart.

## 3.10 · Restart-not-resume (the honest recovery policy for a mid-meeting crash)
*(`PLATFORM-ADOPTION.md` II.5. Write the policy explicitly — it was unspecified before.)*

Two different recovery stories, and we promise the honest one, not checkpoint-resume theater:

- **The workroom task (Doc 05): restart-the-task, guarded by a completion predicate.** A Workroom task **is itself an `operation_runs` row** (`operation_type='workroom:<id>'`, `progress` jsonb = the dispatched bundle `{ask, notes_ref, pinned_sha, sandbox_id, plan_session_id}`, `result_ref` = the terminal `Envelope` = the outbox) — **no `workroom_tasks` table** (CANONICAL §12.10). A crashed `workroom:<id>` row → `interrupted`, re-picked by the drain loop / boot-recovery (`WHERE operation_type LIKE 'workroom:%' AND status='interrupted'`), but **only re-run if its deliverable doesn't already exist** (a SQL completion predicate) — finished work isn't redone; a persistently-failing item cools down; completed sub-artifacts (worktrees) are preserved so restart is cheap. `finally → kick_drain` pulls the next queued task.
- **The meeting harness (Doc 04): restart-not-resume.** When the orchestrator instance dies, the *media session is gone* — you cannot resume an RTP stream. So the replacement instance (which re-claimed the meeting via §3.6 after the heartbeat went stale) **re-joins via Recall**, replays the recent transcript from the persisted `progress` + Doc 03's Postgres transcript plane, rebuilds Proxy's SDK context via §3.5 Tier-2 replay, and speaks a single honest line if anything was lost ("I dropped for about a minute — catching back up"). Proxy's *judgment* history survives in Doc 03's transcript plane; only the live audio couldn't be resumed.

We do **not** promise fine-grained process checkpointing. We promise restart-guarded-by-completion-predicate (tasks) + durable-artifact-resume (state) + honest disclosure (the room).

## 3.11 · AbortController discipline (kill runaway spend/speech mid-meeting)
*(`PLATFORM-ADOPTION.md` III.7. Their `Map<sessionId, AbortController>`; SDK default `maxTurns` is 1000.)*

**DRY (CANONICAL §11.9):** the `AbortController` + `AbortRegistry` are **imported from `libs/agentkit/abort.py`** — Docs 04 §3.11 and 05 §3.11 both import them, neither redefines them. The code below is shown for reading convenience only.

Every wake turn and every dispatched Workroom run is threaded with an abort handle, held in a `dict[meeting_id | task_id, AbortController]`. Aborting halts the *model loop* itself (not just ignoring the result — the SDK would otherwise run to `maxTurns=1000`, burning our budget). **Abort is final, never retried** — this is what makes §3.5's stale-session/JSON retry safe: a build the user killed can't be resurrected. Wire aborts to three triggers: **meeting-end** (cancel everything), **whisper-"stop" / "Proxy, quiet"** (cancel the addressed task), and a **hard per-task timeout** (rescaled to *seconds* for the interactive loop, not their 5-min — Scribe ~3–4s then skip-the-window, Orchestrator answer ~4–5s; a stalled meeting is worse than a dropped note).

```python
class AbortController:
    def __init__(self): self._evt = asyncio.Event()
    @property
    def aborted(self) -> bool: return self._evt.is_set()
    def abort(self) -> None: self._evt.set()

class AbortRegistry:
    def __init__(self): self._m: dict[str, AbortController] = {}
    def make(self, key: str) -> AbortController:
        self.cancel(key)                       # a new judgment-moment preempts a stale one
        c = self._m[key] = AbortController()
        return c
    def cancel(self, key: str) -> None:
        if c := self._m.pop(key, None): c.abort()
    def cancel_meeting(self, meeting_id: str) -> None:   # on meeting-end: kill all its tasks
        for k in [k for k in self._m if k.startswith(meeting_id)]:
            self.cancel(k)
```

## 3.12 · Per-role model routing (capability-router separate from model-router)
*(`PLATFORM-ADOPTION.md` XIII.4 + `config/models.ts`. Adopt their `env → tier-default → global` seam; INVERT their Opus-everywhere default to cheap-first.)*

Their production default is Opus-everywhere because their per-run cost is a business input the customer pays; ours is a promised SLA, so we invert to cheap-first and let the spend live where it earns its keep (the big build). The seam is theirs (`env.PROXY_MODEL_<ROLE> or tier_default`, provider chosen by model-id); the assignments are ours. **The model-router (which model runs a role) stays separate from the capability-router (which behavior fires)** — CLAUDE.md §4, two routers kept apart.

**DRY (CANONICAL §11.9):** the one per-role model table lives in **`libs/llm/routing.py`** and is **imported, not redefined per doc** — Docs 04 §3.12 and 05 §3.2 both import it. The table below is therefore just **the seats the orchestrator uses** (a filtered view of the shared table for reading convenience), not a second definition.

| Role | Model | Turns | Why |
|---|---|---|---|
| Scribe micro-call (Doc 03) | **Haiku** | `max_turns=1` | not agentic — one structured extraction on a cache-warm prefix |
| Proactive gate (V1 — dormant in V0) | **Haiku** | 1 | a small structured "should I speak now?" classifier + confidence |
| Orchestrator wake / grounded answer | **Sonnet** | 3–6 | fast + real reasoning for routing + presentation judgment |
| Workroom quick | Haiku / Sonnet | few | a lookup or pointed explanation |
| Workroom big build | **Opus** | many | the spend lives here — real code work, verified |
| Code-intel fan-out (Doc 01) | Haiku / cheap-open-weight | few | bounded parallel summarization |

```python
# The per-role model table is ONE definition in libs/llm/routing.py (CANONICAL §11.9 / §12.12) —
# 8 seats, REAL model ids (claude-haiku-4-5 / claude-sonnet-4-6 / claude-opus-4-8), the
# env → tier-default → global seam. Doc 04 IMPORTS it; it does NOT redefine an inline dict.
# (The old inline MODELS with fake ids `claude-haiku-4`/`claude-sonnet-4`/`claude-opus-4` is DELETED.)
from libs.llm.routing import MODELS   # {"scribe","scribe_close","gate","quality_gate",
                                      #  "answer"|"orchestrator","workroom","big_build", ...}
# A behavior's config.model (§3.4) resolves against MODELS[<role>]; the provider is picked by id.
```

Also adopt the `min(env_max_output, model_ceiling)` output-token clamp (one `MAX_OUTPUT_TOKENS`, each model self-clamps) and the manual **1-hour-TTL** `cache_control` breakpoint on the Scribe/Orchestrator stable prefix (§3.2 / CANONICAL §10.1 — placed *by us* on the raw Messages call with the volatile tail after the breakpoint, not delegated to the SDK; `PLATFORM-ADOPTION.md` XIII.1).

**Targeted extended/adaptive thinking (CANONICAL §10.6).** Extended/adaptive thinking is enabled **only** on the Opus-escalated grounded-answer path (real code reasoning that earns the latency); it is **explicitly disabled on the fast path** — the should-I-speak wake gate, quick lookups, and the Scribe micro-call — where a thinking preamble is latency-toxic. Where thinking *is* on, cap it: extended thinking shares the output-token budget, so an uncapped preamble can truncate a large structured emission mid-object.

## 3.13 · The live cost circuit-breaker — `check_meeting_budget()` (the thing they LACK)
*(`PLATFORM-ADOPTION.md` XIII BUILD. They have no spend enforcement — cost is observed, not gated — because the customer pays it. Our **$1/meeting-hour is a promised SLA**, so we must gate.)*

**Two honest meters, never the false all-in $1 (CANONICAL §12.7).** The **listening baseline** (~$0.95–1.25/hr: transport + Scribe + orch idle) is the promised SLA ceiling for *listening + notes + quick answers*; **substantive work** (Workroom builds, E2B runtime, Opus deep answers) is a **separate, metered, disclosed per-meeting task budget**, never folded into the baseline. `check_meeting_budget()` reads the composed spend, and the lean `meeting_cost` row (CANONICAL §3 / §12.7) carries all three components — `transport_usd` (accrued `elapsed_hours × rate`) + `e2b_usd` (sandbox-seconds) + the model spend — **not** a full category-ledger (no `meeting_cost_entries`, no reserve/settle).

**The accumulator is PERSISTED, not in-memory** — a crashed orchestrator must not reset the meter to 0 (that would silently defeat the $1/hr SLA at exactly the recovery moment). The `meeting_cost` table is canonical (CANONICAL-DECISIONS §3); **both meters write it**: the Scribe writes `meeting_cost` directly (it is a bare Messages call, not on the seam — §3.3), and the seam-based meter (Proxy wakes + Workroom, via `AgentChunk.RESULT.total_cost_usd`) increments the same row. `check_meeting_budget()` reads the **persisted sum (model + transport + e2b)** and gates **every non-Scribe call** — Scribe is exempt from *degradation* because notes are the floor deliverable we never degrade, though its spend still counts toward the total. **One pre-dispatch estimate gate on `dispatch_workroom`:** if the estimated task cost exceeds the remaining task budget, Proxy asks-approval or declines (disclosed) before the sandbox spins — the task budget can't be blown past silently. Two caps, both *disclosed* (never a silent cliff): a **soft cap** degrades (Orchestrator drops to Haiku, Scribe summary interval widens, proactive contributions stop) and a **hard cap** goes notes-only. This is the deterministic complement to our offline-and-live discipline and gives CLAUDE.md §1j bounded-converge a real ceiling.

```python
@dataclass
class MeetingBudget:
    meeting_id: str
    soft_usd: float          # e.g. 0.80 * projected_hours
    hard_usd: float          # e.g. 1.00 * projected_hours (the SLA line)
    spent_usd: float = 0.0   # in-process cache; write-through to the meeting_cost row

    async def reload_from_db(self, pool) -> None:
        """On harness re-claim (a replacement instance resuming the meeting), reload
        the persisted spend so the meter survives the recycle — never restart at 0.
        The lean meter (CANONICAL §12.7) sums model + transport + e2b on the one row."""
        self.spent_usd = await pool.fetchval(
            "SELECT COALESCE(model_usd,0) + COALESCE(transport_usd,0) + COALESCE(e2b_usd,0) "
            "FROM meeting_cost WHERE meeting_id=$1", self.meeting_id) or 0.0

class BudgetExceeded(Exception): ...

def check_meeting_budget(b: MeetingBudget, role: str) -> "Tier":
    if role == "scribe":
        return Tier.FULL                       # notes are never degraded (spend still counted)
    if b.spent_usd >= b.hard_usd:
        raise BudgetExceeded("notes-only")     # → Proxy discloses, stops non-Scribe work
    if b.spent_usd >= b.soft_usd:
        return Tier.DEGRADED                    # Orchestrator→Haiku, widen Scribe interval,
                                                # proactive off — and SAY so in the room
    return Tier.FULL

def estimate_gate(b: MeetingBudget, est_task_usd: float) -> bool:
    """Pre-dispatch estimate gate on dispatch_workroom (CANONICAL §12.7): True → the estimate
    fits the remaining task budget; False → Proxy asks-approval / declines (disclosed) before
    the sandbox spins, so the metered task budget can't be blown past silently."""
    return b.spent_usd + est_task_usd <= b.hard_usd
```

Keep an in-process cache for hot-path reads, write-through to Postgres. The harness calls `check_meeting_budget()` before every wake dispatch and calls `reload_from_db()` when it re-claims a meeting (§3.6); crossing a threshold is itself a wake event ("cost threshold" in the RUN loop) so the *agent* decides how to phrase the degradation to the room — the enforcement is deterministic, the disclosure is judgment.

## 3.14 · The dispatch funnel for inbound tile/connect traffic (specified once, elsewhere)

The inbound dispatch funnel — rate-limit → Pydantic-validate → meeting/tenant isolation keyed on `meeting_id` presence → entity→owner→tenant server-side resolution → handler, with auth at the WS upgrade — **is specified once in `libs/http` (Doc 08 §4.3; CANONICAL-DECISIONS §5).** Doc 04 does not redefine it. Proxy's own inbound handlers (the accept-handler §3.16.1, control commands) register into that one funnel; the standing rule it enforces is that **a new message type defaults to meeting-scoped unless explicitly marked global.**

## 3.15 · In-flight bookkeeping (mechanical state, agent-consulted)
A task list under a `[3–5]` semaphore · a duplicate of an in-flight ask **attaches** to it rather than spawning · a correction mid-task **injects into that task's session** · anything past ~2s is a detached task ("I'll have it in a moment") whose completion re-wakes Proxy · `cancel that` kills by task id (via §3.11 abort). The list is plain state; what to *do* about collisions is the agent's call on wake. **Every mouth/apply/dispatch side-effect is gated on `op.is_owner` (§3.7 fencing) — a fenced-out harness stays silent.**

## 3.16 · The ordered close
On meeting-end (the in-place close — there is no separate close doc): the close runs as **its own `operation_runs` row** (`operation_type='meeting-close'`, completion predicate = the notes.md object exists — **no `close_jobs` table**, CANONICAL §12.10), which makes the close itself crash-safe and idempotent. Ordered: freeze the notes → trigger Doc 03's close pass (writes the notes file `if_generation_match=0` + posts the link in chat — the whole V0 close deliverable) → **`destroy` the sandbox (§3.9) → mark the harness `operation_runs` row `completed` (§3.7)** → tear down pipes **last** (nothing reads a torn-down store). Staged drafts were already linked in chat when created; they persist in durable state (the `staged_drafts` table + GCS-versioned content, CANONICAL-DECISIONS §4) past teardown so approval still works after the call.

## 3.16.1 · The accept-handler (a human accepts a staged draft after the call)
`propose_change` (Doc 05) persists at creation — a `staged_drafts` row (`status='proposed'`) plus the full diff/content written to GCS (Object-Versioned) — because the sandbox's in-memory review session dies at teardown, so a human accepting *after* the call needs durable storage (CANONICAL-DECISIONS §4). The accept-handler is reached via **`POST /m/{meeting_id}/drafts/{draft_id}/accept`** (the authenticated `control_plane` surface, CANONICAL §12.9 — `protected()` + a server-side `meeting/draft→tenant` check + CSRF + audit, idempotent), which invokes **Proxy's accept-handler**:
1. reads the persisted `staged_drafts` row + its GCS content by `draft_id` (rejecting an already-`applied`/`rejected` draft — idempotent),
2. performs the actual apply — **for a core `kind`, a notes-edit apply** (write the edit into the notes object via Doc 03's write path),
3. flips the row to `status='applied'` and posts confirmation in chat.

**A `code-change` draft is not a PR (CANONICAL §12.9):** accept **records the approval and exposes the diff bundle for download** (the branch bundle already persisted in GCS) — it **never pushes**. Push is an Expansion seam behind the `contents:write` scope we deliberately do not hold in the core; the handler recognizes the `code-change` kind, records approval + surfaces the bundle link, and does **not** silently do nothing.

---

**3.17 · Build steps (in this order — each step ends in something provable).**
1. **The provider seam + `AgentChunk` + delta computer (§3.3).** The one normalized stream, Claude wired, the SDK-isolation triad on the call, the `[CRITICAL]` tripwire. *Provable: a synthetic Claude stream yields correct deltas; a non-MCP tool firing in sandbox mode logs `[CRITICAL]`; the RESULT frame never re-emits.*
2. **The harness skeleton as a durable row (§3.7).** The per-meeting asyncio program: event queue, task table, standing pipes as pure forwarding, wrapped in `with_operation_run` with the 30s heartbeat + boot-reaper. *Provable: a test meeting runs an hour with pipes flowing and zero agent calls; killing the process leaves a row that the boot-reaper flips to `interrupted`.*
3. **Atomic-claim ownership (§3.6).** The partial-unique-index claim. *Provable: two processes handed the same Recall webhook → exactly one opens a harness.*
4. **The reflex layer + name-gate.** Boundary gating, the 0.5s ack, bookkeeping ticks; mechanical "Proxy"/@proxy detection + the tiny disambiguator. *Provable: the ack fires inside the deadline; "the proxy server config" does not wake Proxy; two identical asks attach to one task.*
5. **Wake-behaviors as typed `BehaviorConfig` constants + the generic runner (§3.4), through the seam.** The behavior registry, `BehaviorRunner`, tool functions (dispatch/speak/send_chat/show_screen/ack/cancel/patch_notes/restart_component/run_close), and the build-time JSON/TS capability manifest. *Provable: a synthetic "result returned" event produces the right speak+chat tool calls with nothing hard-coded between; adding a behavior is one `BehaviorConfig` constant + one `register()` line; `answer-question` direct-answers a grounded lookup via `code_intel` without dispatching.*
6. **Dispatch + bookkeeping (§3.15).** Bundle assembly, completion callbacks waking the session, FIFO acks, correction-injection, detach-past-2s, cancel via abort. *Provable: two overlapping asks and one mid-task correction resolve to the right tasks and deliveries.*
7. **Session durability, two tiers (§3.5).** Persist `session_id` + resume; stale-session replay (rebuilt from Doc 03's transcript plane) with the "session restored" notice. *Provable: a wake after a simulated instance swap replays from history, emits the notice, and answers coherently.*
8. **Sandbox lifecycle (§3.9) + reconcile + Scheduler (§3.8).** `ensure_running` (idempotent provision), E2B-native timeout backstop, the TTL/orphan reconcile-cron, `/internal/reconcile` + Scheduler(prod)/interval(dev). *Provable: a cold meeting provisions a sandbox once (race-safe); an ended meeting's sandbox is destroyed by close and, failing that, by the cron/E2B timeout.*
9. **Model routing (§3.12) + cost circuit-breaker (§3.13) + AbortController (§3.11).** The per-role table, `check_meeting_budget()` gating every non-Scribe call, the abort registry wired to end/stop/timeout. *Provable: crossing the soft cap downgrades Orchestrator to Haiku with a spoken line; the hard cap goes notes-only; "Proxy, quiet" aborts the in-flight turn.*
10. **Inbound handlers + accept-handler (§3.16.1) + session lifecycle + ordered close (§3.16).** Proxy's control commands register into the one `libs/http` dispatch funnel (§3.14); the **accept-handler is invoked by the `control_plane` route `POST /m/{meeting_id}/drafts/{draft_id}/accept`** (§12.9), not the WS funnel. Join setup (claim → durable row → ensure_running → readiness → pin SHA → seed → consent) and the ordered close. *Provable: `POST …/drafts/{id}/accept` on a persisted core draft applies the notes-edit and flips the row to `applied`; a `code-change` draft records approval + exposes the bundle without pushing; a full join→close leaves the notes posted and nothing reading a torn-down store.*
11. **Supervision + restart-not-resume (§3.10).** Heartbeat-miss / component-failure wakes; restart/degrade/disclose; diagnostic-task escalation; a killed harness re-claimed and re-joined by a replacement. *Provable: killing the Scribe mid-meeting produces a restart (and an honest line if anything was lost); killing the whole harness produces a replacement that re-joins via Recall and catches up.*

**3.18 · What a wake turn actually looks like (illustrative).**
The harness sends one message into the session (via the §3.4 `answer-question` behavior, through the §3.3 seam):
> *Event: task #2 finished (Sam's ask: "would renaming chargeCard break anything?"). Result: headline "14 call sites, all in payments/" · detail (cited list) · receipts attached · status done. State: mouth free · room mid-discussion, same topic · 1 other task in flight (#3, building, ~4 min) · no pending items · budget: $0.31/$1.00, tier FULL.*

The model replies with tool calls — `speak("Renaming chargeCard touches 14 call sites, all inside payments — details in chat.")` · `send_chat(<cited detail>)` — and the turn ends. Nothing in the harness decided any of that; a different state (room moved on) would have produced `send_chat` only; a budget past the soft cap would have run the same behavior on Haiku with a one-line disclosure.

# 4 · Key variables

**Cost:** standing pipes $0 (plumbing) · reflexes $0 (code) · disambiguation pennies (name-hits only) · wake turns on a cached cheap model — **~$0.02–0.08/meeting-hour** for the orchestrator itself. The `check_meeting_budget()` circuit-breaker (§3.13) enforces the **$1/meeting-hour SLA** across all engines with disclosed soft/hard degradation; the budget lives in the engines (Scribe ~$0.10–0.20, workroom per-task, transport $0.75–0.85). Full `total_cost_usd` + cache-read/creation split captured per micro-call, aggregated per meeting.

**Latency:** ack ≤0.5s (reflex) · wake turn ~1s (the ask is already dispatched by then) · first `TEXT` delta sub-second (the §3.3 delta computer is the barge-in/TTS start point) · barge-in abort <200ms (reflex + §3.11 abort) · sub-100ms in-process preflight before a costly answer (index-loaded? sandbox-healthy?) · presentation waits only on turn boundaries (by design, not lag).

**Pinned SLO block (CANONICAL §12.8 — the DIRECT-ANSWER path only):** ack-audible p95 ≤ 500ms · first-grounded-text p50 ≤ 2s / p95 ≤ 4s · first-grounded-audio p50 ≤ 2.5s / p95 ≤ 5s. Targets apply to **SHALLOW** answers only (index/graph/grep, one gather pass via the host-side `code_intel` API, no live-LSP, no Workroom). **LSP-bound direct answers are exempt from the audio gate** — they fire the "checking…" **tile ACK** (still ≤500ms) and speak when the LSP returns. A "direct" answer needing >1 pass or an LSP call is reclassified and excluded from the shallow SLO; §11.12 step-0.5 measures the shallow path.

**Parallelism & durability:** asyncio session, **no bus, no broker** — every dispatched task is a parallel `asyncio.create_task` whose completion notifies Proxy (no polling). The durability is the *Postgres row + wall-clock* as **defense-in-depth against rare drain events** (the `meeting_runtime` runs on GCE MIG with real grace periods — AMENDMENT A1, 2026-07-17): the heartbeated `operation_runs` row (§3.7) detects a dead instance, the atomic claim (§3.6) — the same `operation_runs` row — guarantees one harness per meeting, two-tier session durability (§3.5) survives a recycle, the simplified sandbox lifecycle (§3.9: E2B-native timeout + destroy-on-close + TTL cron) scales sandboxes to zero between meetings. Proxy is the only serializer, and only at the mouth.

**Dynamism (the principle, restated as a build constraint):** there is **no situation-conditional branching** anywhere in this layer's code, and **no rules-table of "if X do Y"** anywhere in the typed `BehaviorConfig`. Code = pipes (constant connections) + reflexes (physics) + bookkeeping (state) + the durable substrate (rows, claims, lifecycle). Config = the capability envelope (which tools/context/model a behavior may use). Every "what should happen here?" — routine or novel — is answered by the same agent turn with the same tools. New capability = a new tool in the manifest + a new `BehaviorConfig` constant, never new wiring.

**Failure behavior:** notify → judge → act → disclose. Restart what restarts (components; workroom tasks guarded by a completion predicate); re-join what can't resume (the media session — restart-not-resume, §3.10); degrade under budget (§3.13); say what happened. A dead orchestrator instance is *detected* (stale heartbeat) and its meeting *re-claimed and resumed* by a replacement. The diagnostic-task escalation covers the unknowns. Proxy is the reason the product works properly for a whole meeting without a human minder — and now survives the infra it runs on.

**Tunable defaults (pin before build):** wake-turn model `[Sonnet]` · Scribe/gate `[Haiku]` · big-build `[Opus]` · semaphore `[3–5]` · detach threshold `[~2s]` · ack deadline `[0.5s]` · heartbeat `[~10s]` · stale-harness `[~40s]` · sandbox idle-reap TTL `[~15–30min]` · reconcile sweep `[5min]` · per-task hard timeout `[seconds]` · budget soft/hard `[0.8/1.0 × projected-hours]`.

---

**The stack:** one persistent **Claude Agent SDK** session reached only through a **provider seam** that yields a normalized `AgentChunk` stream (delta computer = the barge-in/TTS substrate) · **asyncio** task runtime (Task-handle correlation, completion callbacks — explicitly no message bus, no workflow engine) whose *lifecycle is a heartbeated Postgres `operation_runs` row* · **atomic-claim** meeting ownership on the single `operation_runs` partial-unique index (the claim IS the durable row; no separate `meeting_harness` table, no Redis) · **two-tier session durability** (SDK `session_id` resume → stale-session replay rebuilt from Doc 03's transcript plane) so a Cloud Run recycle can't erase the meeting · a **simplified sandbox lifecycle** (E2B-native timeout backstop + explicit destroy on close + a TTL/orphan reconcile-cron) · **wake-behaviors as typed `BehaviorConfig` constants + one generic runner** (config configures, judgment chooses; JSON/TS manifest generated at build; YAML kept as the Expansion path) with the core **`code_intel` MCP server** mounted on grounded wake turns so `answer-question` can direct-answer · **per-role model routing** (Haiku/Sonnet/Opus) with a live **`check_meeting_budget()`** circuit-breaker reading the persisted `meeting_cost` table to enforce the $1/hr SLA across a recycle · **AbortController** discipline (abort-is-final) · control commands registered into the **one `libs/http` dispatch funnel**, the **accept-handler reached via the `control_plane` route `POST /m/{meeting_id}/drafts/{id}/accept`** · the tool manifest over Docs 01–03/05's interfaces · a mechanical name-gate + tiny disambiguator · code reflexes for barge-in/boundary/ack. Nothing else — and, crucially, no broker.

*One correct interaction, end to end:* Sam: "Proxy, would renaming `chargeCard` break anything?" Name-gate hits → disambiguator confirms → **"on it"** within half a second → Proxy wakes once (Sonnet, through the seam), bundles ask + notes + tail, dispatches, sleeps. The workroom thinks (Doc 05); meanwhile Priya asks something else — second task, FIFO ack ("on it — right after Sam's"). The first result notifies Proxy: it wakes, sees the room mid-discussion on the same topic (still relevant), waits for the boundary, speaks the headline, drops the cited detail in chat, sleeps. Mid-meeting the Scribe misses two heartbeats — Proxy wakes, restarts it, nothing was lost, nobody needed to know. Then Cloud Run recycles the orchestrator instance itself: its harness heartbeat goes stale, the boot-reaper on a sibling instance flips the row to `interrupted`, that instance re-claims the meeting (atomic claim on the one `operation_runs` row), re-joins via Recall, replays the last minute of transcript, rebuilds Proxy's session from Doc 03's transcript plane, and reloads the persisted cost meter so the SLA still holds — Proxy says "I dropped for a moment — I'm back." At meeting end it runs the close in order (§3.16), destroys the sandbox, completes the operation row, and posts the notes link. Total orchestration spend for the hour: a few cents, and the meeting never noticed the infra underneath it move.
