# Doc 02 · Voice & Transport — Build Spec

*Build order: 3rd. Reads from Doc 00 (runtime, hosting). Consumed by Doc 03 (transcript in), Doc 04 (speak/deliver + turn/floor decisions), Doc 08 (renders the canvas this doc streams). This document is the complete description of what to build and exactly how it must work; acceptance criteria and tests are generated from it separately.*

---

# 1 · The end goal

Proxy must be a **physical participant in the meeting**: it joins like a person, hears everyone (knowing who said what), and can communicate back through **every channel a human participant has** — voice, chat (public and private), its camera tile, and a shared screen — with human turn-taking manners (talks into silence, never over people, stops instantly when interrupted).

Stated precisely: **this layer guarantees a complete, general set of input and output channels such that any behavior designed later (Docs 04–06) just composes them — no scenario ever needs new plumbing.** It must work identically on Google Meet, Zoom, and Teams, hold the cost floor (~$0.75–0.85/meeting-hour managed, with a designed migration path toward ~$0.10), and never lose or fake a stretch of audio.

Concretely, when this layer works:
1. Proxy is invited by meeting link like a person, joins within seconds, and announces itself ("I'm an AI participant, observing/recording") — the consent gate.
2. A live, speaker-attributed transcript streams to the rest of the system with ~300ms word latency.
3. When any upstream doc says "say this / show this / send this," it happens: spoken in one calm voice, posted in chat (broadcast or DM), drawn on the tile, or presented on a shared screen — including **live views of the agent working** (browsing, running tests, building an artifact).
4. If a human starts talking while Proxy speaks, Proxy stops mid-word. If someone says "Proxy, quiet," it goes silent. If the bot drops, it rejoins and says honestly what it missed.

**What this layer is NOT.** It decides nothing: *what* to say and *when* is Docs 04/06; what the tile/screen *look like* (the orb, layouts, cursor) is Doc 08; understanding the words is Doc 03. This is pipes and manners only. Screen-*ingestion* (reading what others present) is deferred — not in this doc.

---

# 2 · The design

**The one adopted transport: Recall.ai.** One API gives us bot join + per-speaker audio capture + speak/see surfaces on Meet, Zoom, and Teams — we write zero per-platform code and own zero per-platform breakage (meeting platforms change constantly; that maintenance is what the fee buys). Everything else in this layer is a thin glue loop around it.

**The big simplification: no voice framework.** We deliberately skip Pipecat/LiveKit-class voice-agent frameworks — they exist to own WebRTC transport, and Recall already owns transport. Our entire loop is: *transcript websocket in → the system; text out → TTS → into the call.* Fewer moving parts, less latency, nothing to maintain.

**Where it runs.** This transport layer is an **in-process package inside `meeting_runtime`** (CANONICAL §12.1) — the one asyncio harness process per meeting — not a separate network service. The carrier from this layer to the projector/canvas and to the Orchestrator is an **in-process asyncio call** (no bus, no broker, no wire).

**The five channels (the complete communicate-back surface):**
1. **Voice** — one calm TTS voice. Headlines only by design (detail goes to chat) — both the UX rule and the cost lever. Everything spoken is also posted as text (we have the text before synthesis — free).
2. **Chat, broadcast** — detail, links, receipts, notices; and **@proxy in chat** as a first-class way to address it (input).
3. **Chat, DM** — private delivery to one participant, where the platform supports it.
4. **The camera tile — our controlled signal surface.** The tile is a webpage we render and stream as the bot's camera. Because we fully control it, **all social signals are things we draw** — raise-hand, reactions, "has something," "checking…", listening state — platform-independent, no reliance on native meeting buttons (whose bot APIs are unverified). One canvas, our rules, identical on every platform.
5. **The shared screen — the same canvas, promoted.** Screenshare uses the identical render-a-webpage mechanism at higher resolution. Its signature use: **showing work live** — when the agent browses, runs a test, or builds an artifact in its sandbox, we render that live view onto the presented screen, so the room watches the work form instead of waiting on a spinner. ("Can you search this up?" → Proxy shares its screen and visibly does it.)

**Turn-taking manners, deterministic and tiny.** No separate end-of-turn model. We consume **AssemblyAI Universal-Streaming's `end_of_turn`** — already on the STT stream Proxy pays for (§3.2) — as the natural-boundary signal, and keep **Silero VAD** (<1ms/chunk speech-or-silence, CPU) for **barge-in**. These are not redundant: AAI's transcript latency (~300ms) is far too slow to cut TTS mid-word, so VAD stays as the sub-200ms barge-in trigger even though we no longer run a dedicated boundary model. So the Orchestrator gets two clean signals: *someone is speaking* (Silero VAD) and *a natural boundary just opened* (AAI `end_of_turn`). Proxy speaks only into boundaries; **barge-in** (a human starts talking) stops TTS instantly and flushes the queue. One fewer model to run, no LLM in the loop. *(Confirm-at-build: verify Recall passthrough forwards AAI's `end_of_turn` field; if it does not, re-add Smart Turn v3 as the boundary model — see §3.9.)*

**Cost, honestly, with the path down.** Managed V0: bot $0.50 + STT $0.15 + TTS $0.10–0.20 ≈ **$0.75–0.85/meeting-hour, zero ops** — the verified cheapest managed stack (every premium alternative breaks the $1/hr budget). The design keeps each piece behind a swap seam with a named OSS migration: self-hosted STT (Whisper/Parakeet-class, ~$0.02), self-hosted OSS TTS (Kokoro/Piper-class, ~$0), and at real volume a self-hosted OSS meeting-bot (Attendee/Vexa-class — same browser-automation technique as Recall) → **~$0.10/hr infra-only**. Each migration is gated on a zero-trust vet + reliability test; V0 stays managed because a broken bot kills a customer demo and the ops burden is the opposite of lazy.

---

# 3 · The build

Data flow:
```
meeting link/calendar → Recall bot joins → consent notice posted
  IN:  per-speaker audio → AssemblyAI (via Recall passthrough) → transcript websocket → Doc 03/04
       meeting chat (@proxy, messages) → Doc 04
       Silero VAD on the audio → speaking + barge-in signals; AAI `end_of_turn` (from the STT stream) → boundary signal → Doc 04
  OUT: Doc 04 says speak → TTS (Cartesia) → Output Media audio (+ text copy to chat)
       Doc 04 says send → chat broadcast or DM
       Doc 04/08 render → the canvas webpage → Output Media camera (tile) or screenshare (promoted)
  CONTROL: barge-in → stop TTS + flush · "Proxy, quiet" → silent mode · bot-status webhook → rejoin + honest gap
```

### 3.1 · Join, consent & the roster
Proxy joins from a meeting link (or calendar invite) as a **Recall.ai bot** — no host-side install; the link is enough. Immediately on join it posts the **consent notice** in chat, pinned where the platform allows: one line — it's an AI participant, it observes/records, and anyone in the room can address it. This notice is a hard gate (our upfront-consent decision), not a courtesy. The bot belongs to the room, not the inviter.

**Roster & metadata.** The transport streams **participant events** — who is present, joins, leaves, with names — to the Orchestrator, live. This powers name-aware responses ("Sam, the p95 is…"), speaker attribution in the notes, and **late-join consent**: a participant joining mid-meeting never saw the notice, so it re-posts on their join. Meeting metadata (title, participant list) passes through to the Orchestrator as context. **Meeting end** (meeting closes / bot removed) emits an explicit signal that triggers the close sequence (ordered by Doc 04 §3.16; close pass written in place by Doc 03 §3.7) — never inferred from silence.

### 3.2 · Hearing (audio in → transcript out)
Recall delivers **per-speaker audio streams**. STT is **AssemblyAI Universal-Streaming via Recall's passthrough** (BYOK: our AssemblyAI key configured in Recall — zero integration code, **$0.15/hr**, ~300ms word latency; the verified best accuracy-per-dollar, and the only class of STT price that holds the $1/hr total). The live transcript — words + speaker + timestamps — streams over one websocket to the Orchestrator (Doc 04) and Notes (Doc 03). Note the audio path under BYOK passthrough is **Recall→AssemblyAI direct**, so buffer-through-STT-hiccup is **not ours to guarantee** — see the honest fallback in §3.7. **Proxy's own speech appears in the transcript marked as Proxy** — part of the record, but never processed as a human ask (it must not respond to itself).

*Pinned test before locking the engine:* a side-by-side transcript-accuracy check on **code-heavy engineering audio** (identifiers, file names, acronyms — where generic STT stumbles) between the Recall-passthrough engine and one alternative. Quality cannot drop for the sake of the floor; if the passthrough fails the test, revisit with eyes open about the budget.

### 3.3 · Speaking (text in → voice out)
Upstream hands this layer text → **Cartesia Sonic 3** synthesizes (~40ms time-to-first-audio — voice starts effectively instantly) → **Recall Output Media** streams it into the call as Proxy's voice. One voice, one calm register (brand: the teal "deep feel"). Rules enforced here: **headlines only** (~2–4k chars/hr ≈ $0.10–0.20 — the speak-short/detail-in-chat design); **every spoken line also posts as text** (accessibility + record — free since we synthesize from text); speaking only begins on a **boundary signal** (3.6) and **aborts instantly on barge-in**. **Small-chunk Output-Media buffer:** TTS is streamed to Output Media in **small chunks**, deliberately, so barge-in isn't defeated by a large already-buffered stretch of audio — a flush drops at most one small in-flight chunk, keeping the mid-word cut-off honest.

**Ack-audible reflex.** A direct answer can fire a ≤500ms canned "on it" spoken line the instant it's picked up, while the real answer resolves — the audible counterpart to the tile "checking…" ACK (§3.5). The two are separate signals: this one is voice, the tile ACK is a drawn state.

### 3.4 · Chat (both directions)
- **In:** platform chat messages stream via Recall; `@proxy …` (or any addressed message) forwards to the Orchestrator as a first-class ask — identical to a spoken ask, different socket.
- **Out, broadcast:** detail, links, receipts, quiet notices ("repo advanced 2 commits"), the text copy of everything spoken.
- **Out, DM:** private delivery to a single participant where the platform's chat supports it (asker-private answers, whisper-style delivery). Where a platform's chat is broadcast-only, DM-dependent behaviors degrade to broadcast-or-hold (the *judgment* of which lives in Docs 04/06; this layer just reports which channels exist in this meeting).

### 3.5 · The canvas: tile + screenshare (our surface, both directions of size)
One **canvas webpage** per meeting, rendered by us, streamed by Recall Output Media as either the **camera tile** (small, ambient) or the **shared screen** (big, presented). Rules owned here:
- **Tile = the signal surface.** All social signals are drawn: listening / checking / has-something / **raise-hand** / reactions. No dependence on native platform buttons. (What the drawings look like — the orb, glyphs — is Doc 08; this doc guarantees the surface and the stream.)
  - **The "checking…" state is the tile ACK.** When an LSP-bound direct answer — one exempt from the audio SLO (CANONICAL §12.8) — is resolving its slow answer, it surfaces the "checking…" tile state (a drawn state, ≤500ms) so the room sees Proxy is on it. This is distinct from the **ack-audible reflex** (§3.3, a ≤500ms canned "on it" spoken line): tile ACK is visual, the audible ack is voice; a behavior may use either or both.
- **Screenshare = show the work, on request.** The sandbox's live view (a browser being driven, a test running, an artifact forming) renders onto the canvas when presenting — the room watches Proxy work. Activation is **human-only** (an ask, or a yes to Proxy's offer — the rule lives in Docs 04/08); this layer just executes the promote/demote.
- **Camera and screenshare are mutually exclusive** on the transport → sequence them: speak the headline, then swap to the screen; swap back when done.
- *Pinned measurement:* Output Media **frame-rate/smoothness** for live-work views (Recall publishes no number). Worst case = choppier motion, never lost capability; calm ambient tile motion is safe regardless.

### 3.6 · Turn-taking, barge-in, mute (the manners)
- **Silero VAD** runs on the incoming audio: *is anyone speaking right now?* (<1ms/chunk, CPU) — this is the **barge-in** trigger, and it must stay: a human speech onset has to stop TTS in `[<200ms]`, and the AAI transcript (~300ms) is too slow to do that. Not redundant with the boundary signal.
- **End-of-turn boundary = AssemblyAI Universal-Streaming's `end_of_turn`**, already on the STT stream we pay for (§3.2): *is this a real end-of-turn or a mid-thought breath?* No Smart Turn v3 model — one fewer thing to run and maintain. (Confirm-at-build that Recall passthrough forwards this field; §3.9 step 7.)
- Both signals stream to the Orchestrator continuously. The contract this layer enforces: **voice output may only start on a clear boundary; any human speech onset during Proxy's speech = stop TTS mid-word + flush the queue** (upstream decides whether to re-queue, bank, or drop — Doc 04).
- **Hard mute:** "Proxy, quiet / mute" → this layer kills any in-flight TTS and enters silent mode (tile + chat remain; voice off) until re-invited. Recognition of the phrase is Doc 04's; the kill-switch is here.

### 3.7 · Failure honesty
- **Bot drop:** Recall's status webhook fires → auto-rejoin once → on rejoin, Proxy states the gap plainly ("I was disconnected 14:03–14:04 — notes have a gap there"). Never pretend continuity.
- **STT outage:** buffer-during-outage→resume is **NOT ours under BYOK passthrough** (the audio path is Recall→AssemblyAI direct, so we don't own the buffer) — **confirm-at-build** whether Recall buffers through an AAI hiccup; we don't over-promise buffering we don't own. **Honest fallback = the mark-lost path:** any stretch not transcribed is marked lost in the transcript (§3.3 / the comprehension-gap plane), never silently absent.
- **TTS/provider outage:** voice degrades to chat ("voice is down — I'll type") — a dead engine never makes Proxy mute *and* silent.
- **Rate limits:** Recall's workspace request limit is shared across output-media + chat → a per-bot outbound limiter/queue here so concurrent deliveries can't throttle mid-meeting.

### 3.8 · The seams (provider independence)
Every external piece sits behind a thin seam — `TransportProvider` (Recall), `STTProvider` (AssemblyAI-via-Recall), `TTSProvider` (Cartesia) — so any provider is swappable by migration, not redesign. **V0 runs the managed stack end to end.** (A self-hosted cost-reduction path exists but is explicitly not part of V0 scope; it is recorded outside this document.)

---

### 3.9 · Build steps (in this order — each step ends in something provable)
1. **Join.** Recall bot joins from a meeting link; consent line posts on join. *Provable: bot appears in a real Meet and the line is pinned/posted.*
2. **Events.** Roster (join/leave, names), meeting-end, and bot-status webhooks flow to the harness; late-join consent re-post wired. *Provable: a late joiner triggers the re-post; ending the meeting emits the close signal.*
3. **Hearing.** AssemblyAI-via-Recall passthrough configured (our key in the Recall dashboard); the transcript websocket delivers words + speaker + timestamps; Proxy's own speech marked as Proxy. *Provable: a two-speaker test call yields a correctly attributed live transcript; Proxy never reacts to its own lines.*
4. **Speaking.** Cartesia TTS → Output Media audio; the text copy posts to chat with every spoken line. *Provable: a test "say this" is audible in-call within the latency budget and appears in chat.*
5. **Chat.** Inbound (@proxy + messages) → the harness; outbound broadcast + DM-where-supported; per-meeting channel report. *Provable: @proxy in chat reaches the orchestrator; a DM lands privately on a supporting platform.*
6. **The canvas.** The tile webpage streams as the bot's camera; screenshare promotion + camera↔screen sequencing + announced swaps. *Provable: the tile renders in-call; a promote/demote cycle works without dropping either stream.*
7. **Turn-taking + control.** Silero VAD → speaking + barge-in (kills TTS mid-word + flushes in `[<200ms]`, small-chunk buffer so barge-in isn't defeated by buffered audio); AAI `end_of_turn` (from the STT stream) → boundary signal; the hard-mute kill-switch. **Confirm-at-build: verify Recall passthrough forwards AAI's `end_of_turn` field; if it does not, re-add Smart Turn v3 as the boundary model.** *Provable: speech onset during Proxy's speech stops it inside the budget; "quiet" silences voice while chat stays live.*
8. **Failure + limits.** Rejoin-once on drop + the honest gap line; the mark-lost path on any un-transcribed stretch (buffering through an STT hiccup is not ours under BYOK passthrough — confirm-at-build); the per-bot outbound rate limiter. *Provable: a forced disconnect produces a rejoin and the gap announcement; a burst of sends queues instead of throttling.*

### 3.10 · The signal surface this layer emits (what everyone upstream consumes)
One stream, qualitatively: `transcript(words, speaker, t)` · `chat(message, sender, dm?)` · `roster(join/leave, name)` · `speaking(on/off)` · `boundary(now)` · `barge-in(now)` · `bot-status(connected/dropped/rejoined)` · `meeting-end` · `channel-report(dm_available)`. Docs 03/04 are built entirely against this list — if a behavior upstream needs a signal not on it, the gap belongs here.

# 4 · Key variables

**Cost (per meeting-hour, this layer):** bot $0.50 + STT $0.15 + TTS $0.10–0.20 = **$0.75–0.85 managed** (fits the ≤$1/hr total with ~$0.20 left for model loops). V0 accepts this cost for zero ops and demo reliability; reduction paths live outside this document.

**Latency targets (defaults; pin during build):** STT word latency ~300ms · TTS time-to-first-audio ~40ms (voice ack <0.5s end-to-end is the goal this enables) · barge-in stop: effectively instant (`[<200ms]`) · join-to-listening `[<10s]` · speak-decision-to-audible `[<1s]` (the Output Media leg is the pinned measurement — Recall publishes no number).

**Platform matrix (stated, not discovered at demo time):** join/hear/speak/tile/screenshare — all three platforms via Recall. Chat DM — platform-dependent; this layer reports per-meeting which channels exist, upstream adapts. Native raise-hand/reaction buttons — **not used**; our tile draws all signals (platform-independent by construction).

**Failure behavior:** every failure mode has an honest, non-silent path (3.7): rejoin + admit the gap · mark-lost on un-transcribed audio (buffering not ours under BYOK passthrough) · degrade voice→chat · queue under rate limits. Proxy is never both broken *and* pretending.

**Pinned build-time measurements (not design forks):** ① STT accuracy on code-heavy audio (side-by-side before engine lock) · ② Output Media result-ready→audible p50/p95 + screenshare frame-rate · ③ the outbound rate-limiter under `[5+]` concurrent deliveries.

---

**The stack:** **Recall.ai** (transport: join, per-speaker audio, roster/meeting events, Output Media camera/screenshare, chat) · **AssemblyAI Universal-Streaming via Recall passthrough** (STT, BYOK — also supplies the `end_of_turn` boundary signal) · **Cartesia Sonic 3** (TTS) · **Silero VAD** (barge-in, OSS, CPU-tiny — the only turn-taking model we run; no separate Smart Turn v3) · one thin glue loop (websockets in, Output Media out) — **explicitly no Pipecat/LiveKit** (Recall owns transport). All of it an in-process package inside `meeting_runtime` (§2).

*One correct interaction, end to end:* Proxy joins from the meeting link, posts its consent line, and listens — tile breathing calmly. Sam asks "@proxy can you check what the p95 on checkout is and show us?" — the transcript (attributed to Sam) reaches the Orchestrator; the workroom does the work; Doc 04 says *speak + show*. Proxy waits for the boundary that AAI's `end_of_turn` signals, says the headline ("p95 is 340ms — showing the trace"), swaps its camera for a screenshare of the live view as it pulls the trace, posts the detail + link in chat, swaps back. Maya starts talking mid-sentence at one point — Proxy cuts off mid-word instantly, and finishes via chat instead. When the meeting's Wi-Fi hiccups and the bot drops for 40 seconds, it rejoins and says exactly that. Total transport cost for the hour: about eighty cents.
