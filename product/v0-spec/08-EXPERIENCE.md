# Doc 08 · Experience, UI & Surfaces — Build Spec

*Build order: 9th (after the mechanism docs; it renders their exhaust). Reads from Docs 01–05. This document is the complete description of what Proxy looks, sounds, and feels like in V0 — every surface, every state, every string style, and the small features that make it feel finished. Acceptance criteria and tests are generated from it separately.*

---

# 1 · The end goal

Proxy should feel like **the calmest, best-prepared colleague in the room** — a participant you talk to and glance at, never software you operate. Everything in this document is governed by one test: **can a participant use this without stopping paying attention to the meeting?** If a feature requires opening another app, learning a command, configuring a setting mid-call, or looking away from the people — it fails the test and it is not in this product.

Why this matters commercially, not just aesthetically: the hardest thing about adopting an in-meeting AI is getting a whole team to change behavior. Proxy asks for zero behavior change — it is invited like a person, addressed like a person, and read like a person. That is the adoption story, and this document is where it is enforced.

The complete V0 surface footprint — six things, nothing else:
1. **Its voice** in the call.
2. **The meeting chat** (in and out).
3. **Its video tile** (a presence you can read at a glance).
4. **A shared screen**, only when work is worth watching.
5. **The connect app** (out-of-meeting): the minimal pre-meeting connect page (§2.7) **plus** the authenticated per-meeting home `GET /m/{meeting_id}` (§2.8) — the notes + staged drafts for one meeting, the flagship's missing home.
6. **One markdown notes file** (after the meeting — the artifact that gets forwarded), rendered at `/m/{meeting_id}` and readable by a forwarded-to recipient through a signed capability link.

In a category full of loud, purple-gradient, over-animated AI, Proxy is deliberately the quiet one. **Restraint is the brand.** Its personality shows in precision and calm, not decoration.

**Explicitly not in V0** (each was considered and cut — do not build): a companion web panel (a second place to look = too much), the close "reveal card" (V1 — the notes file carries the value for now), the full orb motion vocabulary and per-system tile morphing (V1 polish), the proactive dial and its preview (cut with proactive), post-meeting Slack/Teams pings (V1 — V0 ends at the notes file), the adoption graph-explorer/self-test spectacle (V1 sales tooling). The tile-canvas mechanism makes every one of these a pure addition later.

---

# 2 · The design

## 2.1 · Identity — the calm one

**The mark.** Proxy has no face, no avatar, no mascot. It is a single **presence orb** — a soft geometric bloom that breathes — in **deep teal-ink (seed `#35c2b8`)** on a near-black tile. The reasoning: a humanoid avatar is uncanny; a static logo is cold; a breathing abstract form reads as *present and alive* without pretending to be a person. Deliberately not electric purple, not gradient-heavy, not busy — the visual identity of restraint.

**The hard ceiling.** The tile renderer must contain **no code path for facial features, character animation, or theatrical effects.** This is structural, not stylistic: as features accrete over versions, the identity cannot drift into a costume, because the costume is unrenderable. The orb's entire expressive range is: breath rate, firmness, shimmer, dim, and pulse.

**One voice.** A single calm TTS voice (Cartesia), used for everything. No character voices, no per-mode voices. Response-style-per-question-type (a more detailed register for deep technical asks, a lighter one for quick facts) is a V1 refinement; V0 ships one consistent register.

**The copy voice — how Proxy writes and speaks.** Plain, specific, warm, and confident. It says what it did, shows the receipt, and states uncertainty as information rather than apology.
- Wrong: "I apologize, but as an AI language model I cannot be fully certain, however it appears that…"
- Right: "p95 is 340ms — trace in chat. One caveat: that's staging data; I can't see production."
- Rules: never "As an AI…" · no filler ("Certainly!", "Great question!") · no exclamation marks · no hedging theater — uncertainty is stated once, precisely · numbers and names over adjectives · one thought per spoken sentence (it's being *heard*, not read).

**The name is Proxy, everywhere.** "Orchestrator," "Scribe," "workroom," "sandbox" are internal engineering words. If any user-visible string contains them, it is a bug. Proxy refers to itself as "I."

## 2.2 · The tile — presence you can read at a glance

The tile is a webpage we fully control (Doc 02's canvas), streamed as Proxy's camera. Because we draw everything, the tile behaves identically on Meet, Zoom, and Teams, and **no signal depends on native platform buttons** (whose bot APIs are unverified) — raising a hand, reacting, showing state are all things we render.

The V0 state set — deliberately few, each **legible from across the room** and each **driven by a real system event, never decorative**:

| State | What it looks like | What drives it (the honest source) |
|---|---|---|
| **Listening** | the orb breathes slowly | default; the session is live |
| **Listening-to** | small "◉ listening to Maya" caption | an address was detected (Doc 04's name-gate + roster) |
| **Working** | a quiet shimmer + one line: "checking the checkout retry logic…" | a real progress event from the task (Doc 05's envelopes) |
| **Checking (ACK)** | a fast "checking…" line, fired ≤500ms | an LSP-bound *direct* answer that can't hit the audio-latency gate (CANONICAL §12.8): the tile ACKs immediately, Proxy speaks when the LSP returns — so the answer is never claimed under the shallow SLO but the room still sees it was heard |
| **Has-something** | the orb firms; caption "have the answer when there's a moment" | a finished result is waiting for a turn boundary (Doc 04) |
| **Speaking** | gentle pulse synced to its own audio | Doc 02's speaking signal |
| **Muted** | dimmed orb + "muted" label | the hard mute (so silence is legible as *chosen*, not broken) |
| **Reaction** | a brief drawn ✓ / small acknowledgment | e.g. a task completed and delivered; sparing by design |

The "has-something" state deserves emphasis: it is **V0's raise-a-hand**, and it solves a real social problem — Proxy never interrupts, but the room can *see* it has something and invite it ("go ahead, Proxy") whenever they want. Polite availability, drawn by us, working on every platform.

**The accessibility law: motion is an enhancement, never the only channel.** Every state the tile expresses is also carried in speech or chat — a dial-in participant or screen-reader user loses the ambience, never the substance. This is a hard rule reviewed per feature.

## 2.3 · Voice & chat — how it communicates

The channel rules, gathered from everything we've locked:

- **Speak the headline; detail lives in chat.** Voice carries one or two speakable sentences; anything with links, code, lists, numbers-tables, or receipts lands in chat. Example: *"That number's off — it's 3.4%, not 2. Trace in chat."* This is simultaneously the UX rule (don't lecture a meeting) and the cost lever (TTS is priced per character).
- **Worst news first.** In any multi-part answer, the blocker, caveat, or risk leads — before the happy summary. *"One thing blocks this: the migration isn't reversible. Everything else is ready."* Burying the caveat under good news is how it gets missed; a senior colleague front-loads the thing you most need to hear.
- **Everything spoken is also posted as text.** The permanent record; free, since we synthesize from text.
- **The <0.5s "on it."** Every ask gets an instant acknowledgment before the real answer. Dead air is the enemy; a half-second ack buys the seconds real work needs and feels like a colleague nodding.
- **If interrupted mid-answer, it finishes in chat.** Barge-in stops the voice instantly (Doc 02); rather than losing the answer or re-interrupting, the remainder posts as text: *"(finishing here —) the second issue is the shared lock in…"*. Nothing is lost, nobody was talked over.
- **Chat results carry visible receipts and honesty tags.** Citations render as `file:line`; verified results are marked (✓ resolved); search-derived results are marked as bounded (~ lower bound — dynamic calls may be missed). The trust texture is *visible*, not buried — a reader can tell at a glance whether a claim was proven or approximated.
- **Correcting the record, not the person.** The no-hedging-theater rule (§2.1) governs Proxy's *own* uncertainty — stated once, precisely. Contradicting something a *participant* said is a different act, and the highest social-risk thing Proxy does: it leads with the receipt, not the verdict, and ends with a genuine out. *"The trace shows 3.4% — am I missing something?"* rather than *"that's wrong."* The provenance does the correcting; the out keeps it two colleagues comparing notes, not a machine scoring a human. Same voice — uncertainty as information — pointed at the record instead of at the room.
- **Name-aware.** It addresses people by name ("Sam, the p95 is…") and attributes what it heard ("as Priya said earlier…") — the roster makes this free, and it is a large part of feeling like a participant.
- **Private where private is right.** Where the platform supports DM (Doc 02 reports per-meeting), personal deliveries — a catch-me-up, an answer someone asked for privately — go to that person alone. Where it doesn't, Proxy says so and offers the public version.
- **Honest failure lines, always spoken/posted, never silent:** *"I was disconnected 14:03–14:04 — notes have a gap there."* · *"I can't see the production dashboard — I have read-only code access. The staging number is 2.1%."* · *"That build hit an error — here's what I have and where it stopped."*
- **Three honesties, said differently.** Not-answering has three distinct shapes, and Proxy keeps them distinct so a listener knows which they got: *"that's outside what I can judge"* (recuse — a call that isn't Proxy's to make), *"I don't know"* (unknown — no grounding for it), and *"here's the part I can prove — the rest I can't"* (partial). When it recuses or hits an unknown, it names the likely owner rather than leaving a void: *"I'm not certain — Maya would know."* The name comes from the `owner(path)` code_intel tool (Doc 01) — codeowner / last-touched for the file in question — so the hand-off points at a real person, not a guess. Calm, not verbose: name the gap, name who'd close it, stop.
- **Belongs to the room.** Anyone can address it — voice or @proxy — not just whoever invited it. The intended habit: *"in doubt, ask Proxy."*

## 2.4 · The small features that make it feel finished (all riding existing machinery)

Each of these costs almost nothing because it renders exhaust the frozen docs already produce. They are the difference between "a working system" and "a finished product," and they are all in V0:

1. **Catch-me-up.** "Proxy, catch me up" → a ~20-second recap from the notes (what's been discussed, decided, and is open). A late joiner gets it privately where DM exists. *(Rides: the notes object. A signature demo moment.)*
2. **Where-are-we.** "Proxy, where did we land?" → the current decisions + open questions, spoken briefly. *(Same machinery as catch-me-up, different slice.)*
3. **Decision + action-item acknowledgments.** When the Scribe commits a decision or action-item note-delta, a quiet chat line marks it: *"— noted: decision — ship Friday (Priya, Sam agreed)"* · *"— action: Sam → fix the retry test (by Fri)"*. This line is a **deterministic harness formatter keyed on a committed `note_delta`** (CANONICAL §12.12) — **never a wake, never model-generated** — so it is free, race-free, and cannot hallucinate a decision; it **honors the session disable** (§2.4 #9 "stop posting decision notes"). Never spoken, never interrupting — just visible bookkeeping that kills the "wait, did we decide that?" a week later. *(Rides: Doc 03's committed note-deltas, rendered as chat lines.)*
4. **Live corrections, acknowledged.** "Proxy, it's Friday, not Monday" → the notes patch instantly (Doc 03 §3.6) and it confirms in one line: *"— corrected: ship Friday."* Trust behavior, made visible.
5. **Dry-run.** "What *would* you do?" → the plan, without executing: the workroom's plan artifact returned as the answer. *(Rides: Plan Mode — literally free.)*
6. **Show-your-work.** "How did you get that?" → the receipt expands: what it ran, what it read, cited lines. *(Rides: the task telemetry that already exists.)*
7. **Progress transparency.** Long tasks produce periodic one-line updates in chat (*"still building — tests running, ~3 min"*), and the tile's working line stays current. *(Rides: Doc 05's progress envelopes; the render policy is: update on real state change, never on a timer.)*
8. **Draft cards.** Every staged draft posts as a consistent chat card: **what it is → what it changes (one-line summary) → the link → "approve = your click."** The **link points at the authenticated `/m/{meeting_id}` home (§2.8), never a raw GCS URI** — the approve/reject buttons live there, tenant-checked and idempotent. One format, so approving Proxy's work becomes a recognizable, low-friction ritual. The card carries the persisted **`draft_id`** (the `staged_drafts` row Doc 05 wrote at `propose_change` time, CANONICAL §4); the accept click posts that `draft_id` to `POST /m/{meeting_id}/drafts/{draft_id}/accept`, which server-side re-checks `meeting/draft→tenant`, reads the persisted row, and calls Doc 04's accept handler to perform the apply — never an in-memory `review_session` object (which dies at sandbox teardown). The `draft_id` the card renders is the **`Envelope.draft_id`** field: the `Bundle` (04→05) and `Envelope` (05→04) Pydantic models both live in **`libs/contracts`** (CANONICAL §11.5), so the draft-card render and the accept route read the same typed field rather than a prose-re-described shape. *(Rides: Doc 05's staged-draft output + the `/m/` accept route + Doc 04's accept handler.)*
9. **Session preferences, spoken.** "Proxy, keep answers shorter" / "stop posting decision notes" → acknowledged and held for the session (it lands in Proxy's session-state digest — Doc 04 — and shapes behavior). No settings page; the meeting *is* the settings page. *(Session-scoped only in V0; persistent preferences are V1.)*
10. **Capability answer.** "Proxy, what can you do?" → a crisp spoken+chat summary of its actual abilities on this repo ("ask me anything about `acme/checkout`, have me build or analyze something, ask for a catch-up…"). Sets expectations correctly in the first minute of a team's first meeting. *(Rides: the system prompt knowing its own toolbelt.)*
11. **Readiness in the consent line.** The join line includes the grounding status: *"…I can see `acme/checkout` (indexed 20 minutes ago)."* Quietly answers "is it current?" before anyone asks. *(Rides: Doc 01's readiness/freshness signal.)*
12. **Verbal walkthrough — human-activated only (Proxy may offer).** Off by default, always. It turns on only when a human asks — *"walk us through how you're doing it," "talk us through it"* — or says yes to Proxy's **offer** (when starting a substantial task it may ask once: *"want me to show you / talk through it as I go?"*; silence or no = off, and it doesn't re-offer). When on, it is an **exact, real-time narration of the actions as it performs them**: *"opening `payments/retry.py` — changing max attempts to 5 — running the test suite — two passed, one to go."* "Quiet narration" (or "quiet") ends it instantly. The non-negotiable: narration never overrides turn-taking — it speaks only into silence, yields mid-word like all voice, and drops to the tile/chat the moment people talk. Three policies that make it feel human: **narrate the current action, never the backlog** (if the work outruns the voice, stale lines are dropped, not read like a log); when narration is on, the worker adds a one-line *why* to its action events ("adding the guard here — the retry loop re-enters"); and **every narration line routes through Proxy's normal delivery judgment** — so if the room's conversation has genuinely resumed elsewhere, narration drops to tile/chat and the result arrives at the end, by the same situational judgment that governs all speech, not a hardcoded rule. *(Rides: Doc 05's progress envelopes + the session-preference mechanism — zero new machinery.)*
13. **Screen sharing — human-activated only (Proxy may offer).** The screen turns on only in response to a human — an explicit command (*"share your screen," "show us"*), an ask that implies it (*"show us the trace"*), or a yes to Proxy's offer (*"want me to put it on screen?"*). Proxy never self-promotes to the shared screen. "Stop sharing" returns to the tile immediately and always wins. The two pair naturally: "show us what you're doing AND walk us through it" = the live guided tour. *(Rides: Doc 02's canvas promotion — this adds the spoken control path and the activation rule.)*

14. **Reconciliation card.** When Proxy detects the same figure asserted two different ways across time (Doc 03 already catches contradiction-across-time — the detection exists), it doesn't render a spoken *"you're wrong."* It posts a **reconciliation card**: the two values side by side, each with its as-of source and date — *"deck: $2.4M · Q3 filing: $2.19M, as of Q3 close."* It is a render variant of the pin-to-source / draft-card format (§2.4 #8, §2.5), a contested-figure layout on the same card renderer — so a disagreement surfaces as a sourced comparison the room resolves for itself, not a correction Proxy delivers at someone. *(Rides: Doc 03's contradiction-across-time detection + the existing card/pin-to-source renderer — no new machinery.)*

## 2.5 · The shared screen — show the work

The screen activates **only on a human's ask or a human's yes to Proxy's offer** (§2.4 #13) — never self-initiated. When it's on, it shows work worth watching; a good colleague takes the projector when invited, and gives it back the moment anyone says stop.

**V0 content modes** — three, each a render of something that already exists (CANONICAL §12.12 scoped the screen down; the live-mirror spectacle is deferred, not cut):
- **Structured progress view.** The task's own progress envelopes (Doc 05) rendered as a clean, legible step view — plan → subtasks → checkpoints → verifier result — not a raw terminal/browser mirror. It shows *what is happening* without streaming a live sandbox window.
- **Pin-to-source.** When the discussion is about specific code, the screen shows the actual file with the cited lines highlighted — "this function, these three lines" becomes literal instead of verbal.
- **Final artifact preview.** When a task lands, the screen shows the finished artifact (the diff/report/document) for the room to read before it's forwarded — the payoff, framed for the meeting.

**Deferred to Expansion** (do not build in V0): the live browser/terminal sandbox mirror ("the AI's hands, visible"), drawn boxes-and-arrows architecture diagrams, and the animated visible cursor. Each is a pure addition on the same canvas later; V0 earns trust with the three real renders above.

Rules: **detail belongs on the screen, ambience on the tile** (platforms downscale camera streams — small text on the tile is unreadable, so the tile never carries anything the room must read). **Camera ↔ screenshare are mutually exclusive** (transport constraint): speak the headline → swap to the screen → swap back when done. The swap itself is announced ("sharing my screen —") so it never feels abrupt.

## 2.6 · Join, consent, and close — the first and last impressions

**Join.** Proxy appears like any participant. The tile starts breathing, and one confident line posts to chat (pinned where the platform allows):
> *"Hi — I'm Proxy, an AI participant. I can see the `acme/checkout` codebase (indexed 20 minutes ago). I'm observing and taking notes, and anyone here can ask me anything — out loud or with @proxy."*

This is a hard consent gate (our upfront-consent decision) delivered as a confident brand moment rather than legal fine print. **Late joiners get it re-posted** (Doc 02's roster events) — consent isn't only for people who were on time.

**Close.** As the meeting ends and before the bot leaves:
> *"Notes + everything I did: `<link>`. One draft is waiting for approval (the rate-limiter change)."*

The `<link>` is the authenticated **`/m/{meeting_id}` home (§2.8)**, not a raw GCS URI. A tenant member opens it signed-in and sees the notes + drafts (with accept/reject). For the classic "forward it to the VP who wasn't there," the chat link carries a **signed, short-TTL, meeting-scoped, revocable capability token** that lets a *non-signed-in* recipient **read the notes only** — no accept, no other meeting. That token-read path is the one public entry; accept/reject stay `protected()`.

**The notes file is a designed artifact, not a dump.** It is the thing that gets forwarded to the VP who wasn't there — it must be excellent unedited. Structure: title/date/attendees → a five-line summary → **decisions** (each: what, who, when, and the moment it landed) → **action items** (who/what/when) → **open questions** → **what Proxy did** (each ask handled, work performed, drafts staged — each with its receipt) → a pointer to the raw transcript. Worst news first here too: any live blocker or risk leads the five-line summary rather than sitting buried below the decisions, so a forwarded reader sees what's at stake before what went well. Clean markdown, scannable in thirty seconds, complete in five minutes.

## 2.7 · The connect page — the one out-of-meeting surface

A single minimal web page, same teal identity, for setup only:
1. **Connect:** the "install the GitHub App" flow (launched from here — Doc 01's requirement).
2. **Watch it get ready:** the repo status, honestly. The page renders all **five** readiness states of Doc 01's `Readiness` enum (CANONICAL §1.5) — `connecting → cloning → indexing → ready`, plus an explicit **`not_ready`** terminal state that shows the gaps (`gaps: list[str]`) when the repo can't be fully grounded. The happy path carries the coverage number and any flagged files ("94% indexed · 12 files flagged: generated"); `not_ready` names what's missing rather than pretending. (Note: there is no `mapping` state — the agentic map is deferred, so `indexing` = clone + tree-sitter + LSP + dep-graph.) Readiness is the *first trust moment* — showing the honest number, including what's flagged or missing, is the brand working before any meeting happens.
3. **Invite instructions:** how to bring Proxy into a meeting (paste the meeting link / calendar invite address).

Nothing else lives here. It is a door, not a dashboard — no analytics, no settings, no meeting history in V0.

**One thing this door is that a dashboard isn't: a public URL.** Anyone with the link can open it, so its calls to the backend are untrusted and validated like any public API — HMAC-gated where a webhook, credentials-only and allowlisted where an authenticated read. The transport that enforces this is §4.6; it is why the connect page's honesty (showing the real coverage number, the flagged files) is safe to expose without exposing anything else.

## 2.8 · The authenticated `/m/{meeting_id}` home — where the notes and drafts live

The connect page is the *pre*-meeting door; **`/m/{meeting_id}` is the *per*-meeting home** — the one real surface this pass adds, and the answer to "where does the close-line link actually go, and where does approving a draft happen?" It closes F1 (the notes artifact has a home) + F9 (drafts have an accept surface). It lives **on the connect app** (the control-plane deployable, CANONICAL §12.1) so it reuses that app's auth wall and transport — net new is small (CANONICAL §12.9): **2 routes + 1 page + a token verifier; everything else is reused.**

What it is:
- **`GET /m/{meeting_id}`** — renders the **§2.6 notes markdown**, folded server-side from `note_deltas` (via the existing `GET /internal/notes/{meeting_id}` reader, CANONICAL §11.4), **plus that meeting's `staged_drafts` cards** (§2.4 #8). `protected()` + server-side `meeting→tenant` check: a tenant member sees their meeting; anyone else gets `Not found`.
- **`POST /m/{meeting_id}/drafts/{draft_id}/accept`** and **`.../reject`** — `protected()` + a server-side `meeting/draft→tenant` check + **CSRF** + **idempotent** + **audit**, calling **Doc 04's accept handler** (notes-edit apply for a core edit; a `code-change` draft records approval and exposes the diff bundle for download, never pushes — CANONICAL §12.9). The button on the draft card (§2.4 #8) posts the persisted **`Envelope.draft_id`** here.
- **The capability-token read path (the only public entry).** The chat/close link (§2.6) carries a **signed, short-TTL, meeting-scoped, revocable capability token**. Presenting it to `GET /m/{meeting_id}` grants a **read-only view of the notes** to a non-signed-in recipient (the forwarded-to VP) — **never** accept/reject, never another meeting. It earns its public exemption the way the webhook earns its (§4.6): it proves a scoped grant, it is not trusted by default. Accept/reject remain `protected()` and are never reachable by token.

This is the whole surface. It is deliberately not a dashboard — no cross-meeting list, no analytics, no history index (those are Expansion); it is one meeting's notes + drafts, addressable by its `meeting_id` (a UUID, CANONICAL §11.2).

---

# 3 · The build

- **The tile** = one HTML/canvas page: the orb (simple animated bloom; calm, modest frame rate — ambient motion, not a video game), a **state machine with exactly the §2.2 states**, each bound to its named system event (Doc 04 session state, Doc 05 progress envelopes, Doc 02 mute/speaking signals). Build rule: **no state without a real source** — if a state can't name its driving event, it doesn't exist. Rendered into the call via Doc 02's Output Media. The tile is **outbound-only** (CANONICAL §12.9): it authenticates its **render WS** via a **meeting-scoped bearer token** in the URL handed to Recall — it receives frames, it never originates a message.
- **The screen** = the same canvas at screenshare size with the **three V0 content modes** (CANONICAL §12.12): structured progress view (Doc 05's progress envelopes rendered as a step view), pinned source (file render + highlighted lines from a result's citations), final artifact preview. Mode changes are driven by Proxy's `show_screen` delivery decisions (Doc 04). The live sandbox mirror + drawn diagram + animated cursor are **Expansion**, not V0.
- **Chat formatting** = one template set, versioned with the copy guide: ask-ack · result (headline + receipt block + honesty tag) · decision/action note-lines · correction acknowledgment · draft card · progress line · failure lines · consent line · close line. Every string in the §2.1 copy voice.
- **The connect page** = a small static app: the GitHub App install flow (Doc 01), a **REST** status poll (`GET /connect/status`, §4.6 — not a WS message) against Doc 01's readiness signal rendering all five states `connecting|cloning|indexing|ready|not_ready(+gaps)` (coverage + flags honestly, gaps named on `not_ready`), and the invite instructions.
- **The `/m/{meeting_id}` home** (§2.8) = two protected routes + one page on the connect app (§4.6): `GET /m/{meeting_id}` folds `note_deltas` (via `GET /internal/notes`, CANONICAL §11.4) + renders that meeting's `staged_drafts` cards; `POST /m/{meeting_id}/drafts/{draft_id}/{accept,reject}` calls Doc 04's accept handler (idempotent, CSRF, audited). Plus a **capability-token verifier** for the read-only forwarded-link path.
- **The notes file** = the close pass's final object (Doc 03) rendered through one markdown template implementing §2.6's structure exactly, served at `/m/{meeting_id}`.
- **The copy guide** = a one-page appendix: the verbatim strings in this doc are the seed set; every new user-visible string is written to it and reviewed against the rules (no "As an AI," no filler, numbers over adjectives, speakable sentences).
- **The transport & contract spine** = the layer *underneath* every surface above — how a tile click, a connect-page poll, a Recall webhook, and a streamed answer physically move. It is not visible in the UI, but it is what makes the UI production-grade and provably tenant-isolated. It has its own section (§4) because we hold customer code and one of our surfaces is a public URL.

---

# 4 · The transport, contracts & structural-security spine (what carries agent traffic to these surfaces)

*Everything in §1–3 is what Proxy looks and sounds like. This section is the plumbing that carries it — adopted near-verbatim from our funded sibling repo `~/platform` (same Claude Agent SDK), where every pattern below exists because a named production incident taught it. It ports 1:1 to our stack (Python uv-workspace · FastAPI · Pydantic v2 · asyncpg Postgres · WebSockets · Vite static apps for the tile + connect page); the ceremony gets cheaper (Zod→Pydantic, TS `satisfies`→an import-time registry + a CI/boot set-equality assertion). The governing principle is theirs and ours, identical: **config configures the capability surface; model judgment makes the choices.***

**Why an experience doc carries a security spine.** Two of our surfaces changed threat model the moment we looked at them honestly, and neither was designed for it:

1. **The connect page is a PUBLIC URL a meeting guest opens.** We were treating tile→backend and connect→backend as trusted "because it's our own web app." That is wrong: the tile is a webpage we stream as a camera, and the connect page is reachable by anyone with the link. Inbound traffic from both is **untrusted** and must be validated like any public API.
2. **We hold customer code.** A tile message that references an `artifact_id` or `canvas_id`, or a connect poll that references an `install_id`, is a request to touch a tenant's indexed repository. Our isolation requirement is identical to theirs; "probably isolated" is not shippable when the asset is someone's source. This section is the difference between *provably* and *probably* isolated.

The spine has seven parts: an import-time message registry (§4.1), Pydantic-per-message (§4.2), one dispatch funnel (§4.3), the `channel_action` generic-surface family (§4.4), streaming render frames + the pure-rendering projector (§4.5), contract-registry HTTP for the connect API + the `/m/` home + webhook receiver (§4.6), and the typed `CAPABILITIES` catalog + its build-time UI manifest (§4.7).

## 4.1 · `libs/contracts` — an import-time message registry (the produce/consume graph, made structural)

Every message that crosses the WS wire — a human's voice/chat `channel_action` inbound, and backend→any-render-surface outbound — is a registered `ProxyMessage`. (The tile is outbound-only render, and the connect page is REST, not WS — §4.6 — so neither originates a WS message.) Registration is not a convention you remember; it is enforced at **import time** (a metaclass hook) and re-checked by a **CI test** and a **boot-time fail-fast**. Forgetting to wire a new message into the graph fails the build, exactly as their `satisfies Record<AllMessageTypes, …>` fails `tsc` in pre-commit. This is Doc 00 §2's "typed models, produce/consume graph closed" aspiration made into a mechanism. (This is the one registry named in CANONICAL §1 — `ProxyMessage` with discriminator `MessageType`, and the single `assert_registry_closed()`.)

```python
# libs/contracts/registry.py
from __future__ import annotations
from enum import StrEnum
from typing import ClassVar
from pydantic import BaseModel

class MessageType(StrEnum):
    # ── inbound: human → backend over the WS (voice/chat-originated commands) ──
    # The tile is OUTBOUND-ONLY (a video stream a human cannot click — CANONICAL
    # §12.9): there is NO tile.address inbound type. The connect page is REST
    # (§4.6), NOT a WS message: there are NO connect.* types here. So the sole
    # inbound client type is the generic surface-carrying family:
    CHANNEL_ACTION    = "channel_action"      # the generic surface family (§4.4); tile is never its origin
    # ── outbound: backend → surfaces (the streamed render frames, §4.5) ──
    RESPONSE_START    = "response.start"
    RESPONSE_CHUNK    = "response.chunk"       # a send_chat() delivery-tool delta → chat
    RESPONSE_END      = "response.end"
    VOICE_SPEAK       = "voice.speak"          # a speak() delivery-tool text delta → TTS (registered — never a bare "speak" dict)
    CANVAS_PATCH      = "canvas.patch"         # a structured TOOL_RESULT / show_screen() render → canvas/screen
    TOOL_START        = "tool.start"          # a work-tool TOOL_USE → tile "working…" line
    TILE_STATE        = "tile.state"          # listening / working / has-something / speaking / muted / checking
    NOTE_LINE         = "note.line"           # a decision/action/correction chat line (§2.4 #3,#4)
    DRAFT_CARD        = "draft.card"           # a staged-draft card (§2.4 #8)

# The registry every ProxyMessage self-registers into, keyed by its MessageType.
CHANNEL_REGISTRY: dict[MessageType, type["ProxyMessage"]] = {}

class ProxyMessage(BaseModel):
    """Base for every wire message. Subclassing registers it — at import time."""
    message_type: ClassVar[MessageType]
    # DELIBERATE DEFAULT (§4.3): an inbound message is meeting-scoped unless it
    # explicitly opts out. A new event author who forgets this gets isolation,
    # not a hole. (No V0 message opts out — the connect page moved to REST — but
    # the default-reject floor stays as a safety for any future global event.)
    requires_meeting_scope: ClassVar[bool] = True

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        et = cls.__dict__.get("message_type")
        if et is None:                        # a subclass that forgot its type
            raise TypeError(f"{cls.__name__} must set a class-level `message_type`")
        if et in CHANNEL_REGISTRY:
            raise TypeError(
                f"duplicate registration for {et!r}: "
                f"{cls.__name__} vs {CHANNEL_REGISTRY[et].__name__}"
            )
        CHANNEL_REGISTRY[et] = cls
```

The closure check — the part that makes "produce/consume graph closed" a build failure rather than a promise kept by inspection:

```python
# libs/contracts/registry.py (cont.)
# The produce/consume graph, made into three declared maps so CI can check the
# WHOLE graph — not just "a model exists" but "every inbound type is handled and
# every outbound type is projected somewhere" (CANONICAL §12.12):
INBOUND  = frozenset({MessageType.CHANNEL_ACTION})            # human → backend (WS)
OUTBOUND = frozenset(MessageType) - INBOUND                   # backend → surfaces (render frames)

MESSAGE_HANDLERS:   dict[MessageType, "Handler"]        = {}  # inbound  → EXACTLY 1 handler
MESSAGE_PROJECTORS: dict[MessageType, list["Projector"]] = {} # outbound → ≥1 projector (the channel render)
MESSAGE_PRODUCERS:  dict[MessageType, list[str]]        = {}  # who EMITS each type (for the field-diff, §4.8)

def assert_registry_closed() -> None:
    """The full graph is closed. Run in CI AND at boot (fail-fast)."""
    # (1) every declared type has a Pydantic model, and no model is undeclared.
    missing = set(MessageType) - set(CHANNEL_REGISTRY)
    unknown = set(CHANNEL_REGISTRY) - set(MessageType)
    # (2) every INBOUND type has EXACTLY ONE handler.
    unhandled = INBOUND - set(MESSAGE_HANDLERS)
    # (3) every OUTBOUND type has AT LEAST ONE projector (a render on some surface).
    unprojected = {t for t in OUTBOUND if not MESSAGE_PROJECTORS.get(t)}
    if missing or unknown or unhandled or unprojected:
        raise RuntimeError(
            "contract graph not closed — "
            f"unmodeled: {sorted(m.value for m in missing)}; "
            f"undeclared: {sorted(u.value for u in unknown)}; "
            f"inbound-without-handler: {sorted(t.value for t in unhandled)}; "
            f"outbound-without-projector: {sorted(t.value for t in unprojected)}"
        )
```

- **CI gate:** `test_contract_graph_closed()` imports `libs.contracts` (which imports every message module, triggering `__init_subclass__`) and calls `assert_registry_closed()`. A new `MessageType` with no model, an **inbound type nothing handles**, or an **outbound frame no projector renders** fails the merge — the prose here is exactly what the test asserts.
- **Boot gate:** the FastAPI `lifespan` calls `assert_registry_closed()` before accepting traffic — because our Python check is a *test* gate, not a *type* gate (honest flag: the `satisfies` guarantee is TS-specific), so we make it also fail-fast at startup, exactly as their `expected-public-routes` does.
- **Scope for V0 (CANONICAL §11.11 + §12.12 — un-trimmed):** keep the set-equality (type registered ↔ model exists) **and** the handler/projector coverage above, **and** add the cheap per-field produce/consume field-diff to CI **now** (over `MESSAGE_PRODUCERS`). It walks each contract model's fields and flags a field produced by one side and consumed by neither. This project already proved field-level drift happens (the `AgentChunk` `.kind`/`.type` seam, the envelope `verified|draft`, the `dm?`/`dm_available` field), so we pay the small cost up front rather than wait for it to bite. See §4.8.

Per CANONICAL §1, this message registry and its single `assert_registry_closed()` live in `libs/contracts` alongside all wire types and the `AgentChunk` union.

**Signal-surface types are OUT of this closure check's scope (CANONICAL §11.8).** Doc 02's signal surface — `transcript` / `roster` / `speaking` / `boundary` / `barge-in` / `bot-status` / `meeting-end` / `channel-report` — are **in-process / over-transport internal events** (02→03/04), **not** client-facing `ProxyMessage`s. They are therefore **outside** `assert_registry_closed()`, which governs only the WS **client** registry (`MessageType` above — the inbound `channel_action` + the outbound render frames). The closure check must not demand them, and a builder must not register them as `ProxyMessage`s — they travel the internal signal path between services, never the client wire, so a missing signal type is not a "graph not closed" failure.

## 4.2 · Pydantic-per-message — the inbound `channel_action` message is a validated model

The one inbound WS message (a human's voice/chat command) is a Pydantic model with three disciplines on every field, validated **once, centrally, at the funnel** (§4.3) — never re-parsed ad hoc in a handler. (The tile sends nothing — it is outbound-only render, CANONICAL §12.9; the connect page's reads are REST, validated by the §4.6 wrappers, not `ProxyMessage`s.)

- **`meeting_id: UUID`** on the meeting-scoped message. A non-UUID is rejected *before any DB lookup happens* — this is precisely what makes tenant isolation **sound**: the funnel never runs a query on attacker-shaped input. (`meeting_id` is a **UUID** everywhere in the app tables per CANONICAL §11.2 — `meetings.id`, `meeting_cost`, `staged_drafts`, `transcript_segments`, `note_deltas`; only `operation_runs.scope_id` stays `text`, cast at the claim site.)
- **`Field(max_length=N)`** on every free-text field, plus a socket-level payload cap. Together they bound DoS: an unbounded `arg` is a memory-exhaustion vector; a bounded cap is not.
- **`Literal[...]`** (a closed set) on every surface/action selector — an out-of-set `surface` is a validation error, not an `if/else` fall-through into an unintended code path.

```python
# libs/contracts/channel.py
from uuid import UUID
from typing import Annotated, ClassVar, Literal
from pydantic import Field
from .registry import ProxyMessage, MessageType

Surface = Literal["voice", "chat", "tile", "canvas", "screen"]   # the five render channels, §2
# The tile is OUTBOUND-ONLY (a human can't click a video stream, CANONICAL §12.9),
# so it is never an inbound ORIGIN. Humans act by voice or @proxy chat:
ActionSurface = Literal["voice", "chat", "canvas", "screen"]      # inbound origins/targets (no "tile")

class ChannelAction(ProxyMessage):                         # the generic-surface family, §4.4
    message_type: ClassVar[MessageType] = MessageType.CHANNEL_ACTION
    meeting_id: UUID                                       # → sound isolation (§4.3)
    surface: ActionSurface
    action: Literal[
        "share_screen", "stop_share", "walkthrough_on", "walkthrough_off",
        "catch_me_up", "where_are_we", "shorter", "capabilities", "show_your_work",
    ]
    # Present only for surfaces that reference a rendered artifact (pin-to-source,
    # the final-artifact preview). The funnel resolves its owning meeting SERVER-SIDE (§4.3).
    canvas_id: UUID | None = None
    arg: Annotated[str | None, Field(max_length=2_000)] = None
```

There is no `TileAddress` model and there are no `Connect*` models: the "◉ listening to Maya" caption is an **outbound** `tile.state` render driven server-side by Doc 04's name-gate + roster (not a tile→backend message), and the connect readiness poll / install-start are **REST** (§4.6). What remains inbound is exactly the "share your screen / catch me up / keep answers shorter" family — validated like any public API, at the single choke point.

## 4.3 · The dispatch funnel — meeting/tenant isolation you cannot opt out of

Every inbound WS message — the human's voice/chat `channel_action` — flows through **one** coroutine. (The connect page's public reads take the REST path in §4.6; they don't enter this funnel.) Isolation is keyed on the **presence of `meeting_id`**, applied by the funnel automatically; a handler cannot forget it because a handler never sees an un-isolated message. This is the single highest-value security steal in the whole adoption: it replaces N ad-hoc per-handler scope checks with one path that makes an unchecked route *structurally impossible*.

**Rate limiter — pinned (CANONICAL §11.11).** `ctx.rate_limiter` is the **`limits`** library (or **`slowapi`** for the FastAPI integration) with an **in-memory backend** for V0 — **not** a hand-rolled token bucket. Its `check()` is `limits`' fixed-/moving-window strategy, keyed per-connection (`conn.id`); a distributed (Redis) backend is an Expansion swap behind the same call.

```python
# libs/http/dispatch.py
from pydantic import ValidationError
from libs.contracts.registry import CHANNEL_REGISTRY

async def dispatch(conn: "Connection", raw: dict, ctx: "DispatchCtx") -> None:
    # 1 ── rate-limit (per-connection; a public connect page can hammer us) ──
    if not ctx.rate_limiter.check(conn.id):
        await conn.send_error("Slow down.")            # generic; no internal detail
        return

    # 2 ── registry lookup by declared type ──
    model = CHANNEL_REGISTRY.get(raw.get("type"))
    if model is None:
        await conn.send_error("Not found")             # never "unknown type X" — no info leak
        return

    # 3 ── Pydantic-validate ONCE, centrally (§4.2) ──
    try:
        msg = model.model_validate(raw)
    except ValidationError:
        await conn.send_error("Not found")             # generic on every failure
        return

    # 4 ── meeting/tenant isolation, keyed on meeting_id PRESENCE (automatic) ──
    meeting_id = getattr(msg, "meeting_id", None)
    if meeting_id is not None:
        # meeting_id is already a UUID — Pydantic rejected non-UUIDs in step 3,
        # which is what makes this lookup SOUND (no query on attacker input).
        await require_meeting_access(conn, meeting_id, ctx)
        ctx.meeting_activity.bump(meeting_id)          # sliding-TTL keep-alive (Doc 04 §3.6 / CANONICAL §11.11 WS instance-affinity)
    elif msg.requires_meeting_scope:
        # DEFAULT-REJECT: no meeting_id and not explicitly global ⇒ refuse.
        await conn.send_error("Not found")
        return
    # else: an explicitly-global message. No V0 message opts out (connect moved to
    # REST, §4.6) — the branch stays only as the safety floor for a future one.

    # 5 ── entity → owner → tenant, resolved from OUR store, never a client meeting_id ──
    entity_id = getattr(msg, "canvas_id", None) or getattr(msg, "artifact_id", None)
    if entity_id is not None:
        await require_entity_access(conn, entity_id, ctx)   # kills the smuggle-a-foreign-id bug

    # 6 ── handler (guaranteed: validated, isolated); MESSAGE_HANDLERS has exactly 1 per inbound type ──
    await MESSAGE_HANDLERS[msg.message_type](conn, msg, ctx)


async def require_meeting_access(conn, meeting_id, ctx) -> None:
    meeting = await ctx.repos.meetings.get(meeting_id)
    # Generic 'Not found' whether the meeting is absent OR owned by another tenant —
    # never distinguish the two, or the error itself leaks tenancy.
    if meeting is None or meeting.tenant_id != conn.tenant_id:
        await conn.send_error("Not found")
        raise NotFound()

async def require_entity_access(conn, entity_id, ctx) -> None:
    # A tile msg referencing a canvas/artifact resolves ITS owning meeting from
    # our DB, then tenant-checks that — the client's meeting_id is never trusted
    # to authorize the entity (their "smuggle {victimId, myMeetingId}" fix).
    owning_meeting_id = await ctx.repos.artifacts.owning_meeting(entity_id)
    if owning_meeting_id is None:
        await conn.send_error("Not found"); raise NotFound()
    await require_meeting_access(conn, owning_meeting_id, ctx)
```

**Auth is guaranteed at the connection upgrade, never per-message** — a WS that authenticates per-message instead of per-connection is the classic hole:

```python
# libs/http/gateway.py — auth BEFORE the 101 upgrade
async def authorize_upgrade(request) -> "Connection":
    session = await resolve_session(request.cookies)          # signed cookie → reads the `sessions` table (CANONICAL §11.1) → {user_id, tenant_id}
    if session is None:
        raise RejectUpgrade(401)                              # reject BEFORE the socket opens
    if not origin_allowed(request.headers.get("origin")):     # prod origin allowlist
        raise RejectUpgrade(403)
    if ctx.conn_limiter.count(session.user_id) >= MAX_CONN_PER_USER:
        raise RejectUpgrade(429)                              # per-user connection cap
    return Connection(user_id=session.user_id, tenant_id=session.tenant_id)
```

Relate it back to the product: when Sam says "Proxy, share your screen," when someone asks a "pin-to-source" canvas action that references a rendered file — each is a `channel_action` through this one funnel, validated and tenant-scoped before a single line of handler logic runs. (Maya's late-join readiness poll is a REST read, isolated by the §4.6 wrappers, not this funnel.) The teal orb never renders a state, and the notes file never gains a line, from traffic that skipped isolation.

## 4.4 · The `channel_action` generic-surface family — one message type, not five

Our five channels (voice / chat / tile / canvas / screen) are **surfaces**, not five separate message schemas. Inbound channel-specific actions are modelled as the **one** `ChannelAction` family (§4.2) carrying a `surface: Literal[...]`, with per-surface capability resolved from a single source of truth (§4.7) — mirroring their `artifact_agent_*` family that carries a `surface` enum and resolves capability per-payload. Adding a surface is one edit to the `Surface` literal and the capability catalog, not a new message type, a new registry entry, a new handler, and a new isolation check.

```python
# libs/http/handlers/channel_action.py
async def handle_channel_action(conn, msg: "ChannelAction", ctx) -> None:
    cap = resolve_capability(msg.surface, msg.action, ctx)   # per-surface, from the static CAPABILITIES
    if cap is None or msg.action not in cap.allowed_on(msg.surface):
        await conn.send_error("Not found"); return
    # e.g. surface="screen", action="share_screen" → Doc 02 canvas promotion;
    #      surface="voice",  action="catch_me_up"   → Doc 04 catchup wake-behavior.
    await ctx.orchestrator.dispatch_capability(cap, msg, conn)
```

This is why §2.4's "share your screen / walk us through it / catch me up / keep answers shorter" don't each spawn a bespoke wire path: they are `channel_action`s differing only in `surface` + `action`, capability-gated by the catalog. One type, five surfaces, one isolation guarantee.

## 4.5 · Streaming render frames + the per-channel projector (pure rendering, tools are the delivery authority)

An answer is produced **once**, as a single normalized chunk stream (Doc 04's `AgentChunk`). **Delivery is decided by the model's own wake-turn TOOL CALLS (CANONICAL §12.3): Proxy communicates only by `speak(text)` / `send_chat(text, dm?)` / `show_screen(artifact)` — the model chooses the channel.** The projector is therefore **pure rendering**: it maps the stream's tool events to render frames. It **never** auto-extracts a headline from raw text and decides to speak — that was a chat-product habit; here the wake turn is the sole delivery authority.

**The projector consumes the delta stream, not raw `AgentChunk` (CANONICAL §1.1 / §11.3).** `stream_deltas` is applied **exactly once**, inside `BehaviorRunner.run()` (Doc 04); every downstream consumer — the projector, the cost meter, the transcript logger — receives **deltas** and **MUST NOT re-wrap** (the old Doc 08 re-wrap is deleted). On that delta stream `chunk.text` is already a true delta, forwarded as-is and **never re-accumulated**. Field access is `chunk.type` (the discriminator — never `.kind`), `chunk.metadata["name"]` for a TOOL_USE, and `chunk.metadata["structured"]` for a TOOL_RESULT (never `.tool` / `.structured` as top-level attrs).

**The projector is NARROWED to two event types only (AMENDMENT C2, 2026-07-17):**
- **A `TOOL_USE`** → tile "working…" lines (the tool name, humanized).
- **A structured `TOOL_RESULT`** → screen canvas render (pin-to-source highlights, the final-artifact preview — §2.5).

**Everything else is handled by the delivery tools themselves.** `speak(text)` / `send_chat(text, dm?)` / `show_screen(artifact)` are the **sole delivery authority** (wake-turn tools — CANONICAL §12.3); the projector **never** auto-extracts a headline from raw text, never renders `TEXT` chunks, and never decides which channel to use. Raw `TEXT` (the model's own reasoning) is **not projected to any surface** — only an explicit delivery tool reaches a human.

Every projected frame is a **registered `ProxyMessage` instance** (never a hand-built dict, never an unregistered `"speak"` type); `send()` serializes it via `model_dump()`.

```python
# services/transport/projector.py — one DELTA stream → registered render frames.
# Consumes the OUTPUT of stream_deltas (applied once in BehaviorRunner.run, CANONICAL
# §11.3) — chunk.text is a TRUE delta, forwarded as-is, never re-wrapped/re-accumulated.
from typing import Iterable
from libs.contracts.channel import (            # all REGISTERED ProxyMessage subclasses
    VoiceSpeak, ResponseChunk, CanvasPatch, ToolStart,
)

DELIVERY_TOOLS = {"speak", "send_chat", "show_screen"}

class ChannelProjector:
    """Pure rendering. No headline extraction, no delivery decision — the wake turn
    already decided by calling a delivery tool. Emits registered ProxyMessage instances."""

    def project(self, chunk: "AgentChunk") -> Iterable["ProxyMessage"]:
        if chunk.type == "TOOL_USE":
            name = chunk.metadata["name"]
            if name in DELIVERY_TOOLS:
                # the model chose the channel; we render its own streaming text delta.
                # (tool-input streaming shape confirmed against the SDK at build, §11.10)
                text = chunk.metadata.get("text_delta", "")
                if name == "speak":
                    yield VoiceSpeak(text=text)          # → TTS; chat mirror ("spoken is also posted",
                                                         #   §2.3) is a harness mirror, not a 2nd decision
                elif name == "send_chat":
                    yield ResponseChunk(chunk=text)      # → the permanent chat record
                else:                                    # show_screen
                    yield CanvasPatch(patch=chunk.metadata.get("artifact"))
            else:
                # a WORK tool → the tile "working…" line only (§2.2), never chat prose
                yield ToolStart(line=humanize_tool(name))
            return
        if chunk.type == "TOOL_RESULT" and chunk.metadata.get("structured") is not None:
            # structured result → canvas render; SCREEN mode-change frames are separate,
            # so camera↔screenshare stays exclusive (§2.5).
            yield CanvasPatch(patch=chunk.metadata["structured"])
            return
        # TEXT / everything else: internal — NOT projected.
```

```python
# the IN-PROCESS carrier (transport is a package in meeting_runtime, CANONICAL §12.3 —
# a direct call, no undefined wire; handle_meeting_turn is DELETED as a chat-product artifact).
# It owns the ordering law + readyState guard and drives the projector over the SAME delta
# stream every consumer reads. Register it so assert_registry_closed sees a projector per type.
async def carry_turn(conn, deltas, meeting, ctx) -> None:
    await send(conn, ResponseStart(meeting_id=meeting.id))     # resolve FIRST, persist done upstream
    projector = ChannelProjector()
    async for chunk in deltas:                                 # already stream_deltas output — DO NOT re-wrap
        for frame in projector.project(chunk):
            await send(conn, frame)
    await send(conn, ResponseEnd(meeting_id=meeting.id))

async def send(conn, frame: "ProxyMessage") -> None:
    if conn.ready:                 # readyState guard — tab closed mid-generation ⇒ drop, don't crash
        await conn.send_json(frame.model_dump())   # registered instance → serialized via model_dump()

MESSAGE_PROJECTORS[MessageType.VOICE_SPEAK]    = [project_voice]
MESSAGE_PROJECTORS[MessageType.RESPONSE_CHUNK] = [project_chat]
MESSAGE_PROJECTORS[MessageType.CANVAS_PATCH]   = [project_canvas]
MESSAGE_PROJECTORS[MessageType.TOOL_START]     = [project_tile]   # …one per OUTBOUND type (§4.1)
```

The barge-in behaviour (§2.3, "if interrupted mid-answer, it finishes in chat") still falls out cleanly: barge-in stops the `speak` TTS stream (Doc 02) while the model, seeing the interrupt, finishes by calling `send_chat` — the two are independent tool-driven channels, so nothing is lost. Persisting the user turn before the stream (upstream, in the harness) is what lets the notes file (§2.6) and catch-me-up (§2.4 #1) read a turn that a mid-stream crash would otherwise have dropped.

## 4.6 · Contract-registry HTTP — the connect API + the webhook receiver

The connect page's REST calls, the **`/m/{meeting_id}` home (§2.8)**, and the Recall.ai webhook receiver do **not** register raw FastAPI routes. They register through typed wrappers where the handler receives a credentials-only context and **never raw `Request`/`Response`** — so "read the tenant from the request body" is *unrepresentable*, not merely discouraged (their whole "M1-shape bug class" closed by type). These wrappers cover our surface:

```python
# libs/http/registry.py
from dataclasses import dataclass
from fastapi import Depends, HTTPException, Request

@dataclass(frozen=True)
class AuthzCtx:                       # what an authenticated handler gets — NOT req/res
    user_id: str
    tenant_id: str                    # non-null by construction; safe as a DB filter

@dataclass(frozen=True)
class PublicAuthzCtx:                 # what a public handler gets — nullable ON PURPOSE
    user_id: str | None
    tenant_id: str | None            # nullable so it CANNOT be used as a DB filter by accident

def protected() -> AuthzCtx:
    async def _dep(request: Request) -> AuthzCtx:
        user = await resolve_session(request)
        if user is None:
            raise HTTPException(401, "Unauthorized")
        if user.tenant_id is None:
            raise HTTPException(403, "No tenant assigned")
        return AuthzCtx(user.id, user.tenant_id)
    return Depends(_dep)

# The public allowlist — the ONLY routes reachable unauthenticated. A CI test
# (below) asserts every route is either `protected()`-scoped or listed here.
PUBLIC_ROUTES: frozenset[str] = frozenset({
    "POST /webhooks/recall",         # Recall bot lifecycle (join/leave/transcript) — HMAC-gated
    "GET /connect/status",           # connect-page readiness poll (no meeting yet exists)
    "POST /connect/install/start",   # launch the GitHub-App install flow
    "GET /m/{meeting_id}",           # the notes home — public ONLY with a valid capability token
                                     # (read-only notes for the forwarded-to recipient); a signed-in
                                     # tenant member takes the same route protected(). No token +
                                     # no session ⇒ Not found. Accept/reject are NOT here (protected).
})
```

**The `/m/{meeting_id}` routes.** The read route is dual-mode; the mutations are strictly `protected()`:

```python
# apps/control_plane/routes/meeting_home.py
@get("/m/{meeting_id}")                      # dual-mode: session OR capability token
async def meeting_home(meeting_id: UUID, ctx: PublicAuthzCtx, token: str | None = None):
    grant = verify_capability_token(token, meeting_id) if token else None   # signed, short-TTL, meeting-scoped, revocable
    if grant is None and (ctx.tenant_id is None or not await meeting_in_tenant(meeting_id, ctx.tenant_id)):
        raise HTTPException(404, "Not found")
    notes  = await internal_notes(meeting_id)         # GET /internal/notes → folds note_deltas (CANONICAL §11.4)
    drafts = [] if grant else await staged_drafts_for(meeting_id)   # token = notes only, NO drafts
    return render_meeting_home(notes, drafts)         # §2.6 markdown + §2.4 #8 draft cards

@post("/m/{meeting_id}/drafts/{draft_id}/accept")    # protected() — a tenant member only
async def accept_draft(meeting_id: UUID, draft_id: UUID, ctx: AuthzCtx, csrf: CsrfToken):
    await require_meeting_and_draft_in_tenant(meeting_id, draft_id, ctx.tenant_id)   # server-side meeting/draft→tenant
    return await doc04_accept_handler(draft_id, actor=ctx.user_id)   # idempotent + audited; Envelope.draft_id (CANONICAL §11.5)

@post("/m/{meeting_id}/drafts/{draft_id}/reject")    # protected() — symmetric
async def reject_draft(meeting_id: UUID, draft_id: UUID, ctx: AuthzCtx, csrf: CsrfToken):
    await require_meeting_and_draft_in_tenant(meeting_id, draft_id, ctx.tenant_id)
    return await doc04_reject_handler(draft_id, actor=ctx.user_id)   # idempotent + audited
```

The **capability-token verifier** is the whole net-new security primitive (CANONICAL §12.9): a signed value (HMAC over `{meeting_id, scope:"notes", exp}`) that is **short-TTL**, **meeting-scoped** (a token for meeting A can't read B — the `meeting_id` is in the signed body and re-checked against the path), and **revocable** (a `revoked_tokens` check, or a per-meeting token epoch bumped on demand). It grants **read-only notes**, never a draft, never a mutation.

The webhook wrapper is **public but HMAC-signature-verified** — the signature is our equivalent of their "a `publicRoute` must supply a `reason`" gate: a public route earns its exemption by proving the caller is Recall, not by trust:

```python
# libs/http/webhook.py
import hmac, hashlib
from fastapi import HTTPException, Request

async def verify_recall_signature(request: Request) -> bytes:
    body = await request.body()
    sig  = request.headers.get("x-recall-signature", "")
    expected = hmac.new(settings.recall_webhook_secret,
                        body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(401, "bad signature")     # constant-time; the HMAC IS the gate
    return body
```

`safeError` — external callers (Recall, an anonymous connect-page visitor) **never** see an internal error. Validation errors *are* returned (they describe the caller's own bad input); everything else collapses to a per-status fallback:

```python
# libs/http/safe_error.py
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

_FALLBACK = {400: "Bad request", 401: "Unauthorized", 403: "Forbidden",
             404: "Not found", 500: "Service temporarily unavailable"}

async def safe_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, RequestValidationError):
        return JSONResponse(422, {"error": "invalid request", "issues": exc.errors()})
    status = getattr(exc, "status_code", 500)
    return JSONResponse(status, {"error": _FALLBACK.get(status, "Request failed")})
```

The structural guarantee is a **test that enumerates the routes**, plus the **live cross-tenant E2E we keep** (see §4.8):

```python
# tests/security/test_routes_are_scoped.py
def test_every_route_is_tenant_scoped_or_allowlisted():
    for route in app.routes:
        key = f"{next(iter(route.methods))} {route.path}"
        if key in PUBLIC_ROUTES:
            continue
        assert _declares_protected_dep(route), \
            f"{key} is neither tenant-scoped nor in the public allowlist"
```

**TRIM for V0:** adopt the wrappers + `safeError` + the HMAC webhook wrapper + the allowlist + the route-enumeration test. **Skip** the AST/ruff-custom-rule that *bans* raw route registration — a code-review convention suffices until the route count is large. **Keep** the *live* cross-tenant E2E — that one earns its cost because we hold customer code.

## 4.7 · `CAPABILITIES` — the typed catalog + a build-time UI manifest (never hardcodes, never fetches)

There is one source of truth for *what Proxy can do* — the **typed Python `BehaviorConfig` / `CAPABILITIES` constants** (CANONICAL §6 + §12.5), mirroring their `AGENTS` config. Each capability declares its **output kind**, its **renderer config**, its **allowed actions** (surface / propose / approve), and — for capabilities a wake-behavior or disposition fulfills — a **`service:` binding** to it (Doc 04/05). The backend imports this module directly. **The UI does NOT import the Python module** — a **build step generates a small JSON/TS manifest of UI labels** from these typed constants (CANONICAL §12.5), which the tile/connect/`/m/` apps import. This fixes the **service-string-in-TS problem**: the internal `service:` bindings (`wake:…`, `disposition:…`) and other backend-only fields **never ship to the browser** — the generated manifest carries only the UI-facing `{id, label, output, surfaces}`. There is **no runtime `GET /capabilities` endpoint, no dynamic HTTP-fetched catalog, and no boot-time validator apparatus** (all cut in CANONICAL §6). Because the manifest is generated from the same typed source the backend enforces, the "Proxy, what can you do?" answer (§2.4 #10) and the consent line's grounding status can never drift from it.

```python
# libs/contracts/capabilities.py — the typed source of truth (mirror of their AGENTS).
# Backend imports THIS; the UI imports a build-generated JSON/TS manifest of labels only
# (CANONICAL §12.5) — the `service:` strings never reach the browser.
from enum import StrEnum
from pydantic import BaseModel

class OutputKind(StrEnum):
    SPEECH = "speech"     # a spoken headline (voice)
    CHAT   = "chat"       # chat detail + receipt + honesty tag
    CANVAS = "canvas"     # tile/screen structured render
    NOTES  = "notes"      # a line in the markdown artifact
    DRAFT  = "draft"      # a staged-draft card (approve = your click)

class Action(StrEnum):
    SURFACE = "surface"   # may show/answer
    PROPOSE = "propose"   # may stage a draft (never apply)
    APPROVE = "approve"   # gated on a human click

Surface = str  # one of the five (voice/chat/tile/canvas/screen)

class Capability(BaseModel):
    id: str
    label: str                       # user-facing ("Catch me up", "Ask about the code")
    output: OutputKind
    renderer: dict = {}              # per-surface UI render config only (tile state, screen mode,
                                     # receipt on/off) — NOT a delivery flag (delivery is §12.3's job)
    actions: frozenset[Action]
    service: str | None = None       # binding to a wake-behavior / disposition (Doc 04/05);
                                     # None for a pure delivery mode over an existing stream
    surfaces: frozenset[Surface]

    def allowed_on(self, surface: str) -> frozenset[Action]:
        return self.actions if surface in self.surfaces else frozenset()

# A plain module constant — imported, not fetched. No runtime endpoint, no boot-validator.
CAPABILITIES: dict[str, Capability] = {
    "answer_grounded": Capability(
        id="answer_grounded", label="Ask about the code", output=OutputKind.CHAT,
        actions=frozenset({Action.SURFACE}), service="wake:answer-question",
        surfaces=frozenset({"voice", "chat", "screen"}),
        renderer={"tile_state": "working", "receipt": True}),
    "catch_me_up": Capability(
        id="catch_me_up", label="Catch me up", output=OutputKind.SPEECH,
        actions=frozenset({Action.SURFACE}), service="wake:catchup",
        surfaces=frozenset({"voice", "chat"})),
    "build": Capability(
        id="build", label="Build or change something", output=OutputKind.DRAFT,
        actions=frozenset({Action.PROPOSE, Action.APPROVE}),
        service="disposition:worktree-worker",
        surfaces=frozenset({"screen", "chat", "canvas"}),
        renderer={"tile_state": "working", "draft_card": True}),
    # Walkthrough is NOT a disposition (CANONICAL §8) and NOT a delivery flag on this
    # catalog: it is a delivery MODE (§2.4 #12) owned by §12.3's delivery layer — when a
    # human turns it on, the worker's progress events are narrated through Proxy's normal
    # speak() judgment. The catalog only names the capability + its UI label; the
    # `disposition:narrator` / `delivery:"narrated"` remnant is removed.
    "walkthrough": Capability(
        id="walkthrough", label="Walk us through it", output=OutputKind.SPEECH,
        actions=frozenset({Action.SURFACE}), service=None,
        surfaces=frozenset({"voice", "screen"})),
}
```

This is the single-source-of-truth discipline applied to the product's own promise. It is also the enforcement point for §2.4 #10's honesty rule: the spoken "what can you do?" answer is a render of `CAPABILITIES` filtered to this repo's mounted tools — it *cannot* over-claim, because the UI's generated label manifest and the funnel's authorization (§4.3/§4.4) derive from the one typed source.

## 4.8 · What we trim, and what we keep (the V0 discipline gate)

- **TRIM — skip the AST-ban ruff rule.** Their repo forbids raw `router.get/post` with an AST test; we skip it at V0. A code-review convention ("register through a wrapper") suffices until our route count is large enough to make a mechanical guard worth its weight. (§4.6)
- **KEEP — the per-field produce/consume graph-diff (un-trimmed, CANONICAL §11.11).** Beyond the §4.1 set-equality (type-registered ↔ model-exists), a **cheap Pydantic produce/consume field-diff runs in CI now** — it walks each contract model's fields and flags any produced by one side and consumed by neither. This project already proved field-level drift happens (`AgentChunk` `.kind`→`.type`, the envelope `verified|draft`→`EnvelopeStatus`, `dm?`→`dm_available`), so we pay the small cost up front rather than wait for it to bite. (§4.1)
- **KEEP — the live cross-tenant E2E test.** A running server enumerates every route and fires tenant-B credentials at tenant-A `meeting_id`s / `canvas_id`s, expecting `Not found`. We hold customer code; provable isolation is worth the one E2E even though most of the CI suite is fast and DB-free. (§4.3/§4.6)
- **KEEP — everything else in §4.** The import-time registry, Pydantic-per-message, the dispatch funnel, the generic-surface family, the projector, the contract-registry HTTP, and the capability catalog are all cheap in Python and directly load-bearing for a product whose surfaces include a public URL over customer code.

The overcomplication guardrail applies: we adopt the *behavior and the reason*, named to our concrete surfaces (the tile, the five channels, the connect page, the notes file) — not their generality. We have one product at V0, not dev+prod+N-customers; the spine is written concrete, and it stays that way until a real second instance of anything appears.

---

# 5 · Key variables

**The governing test, as the acceptance bar:** every feature usable by talking or glancing, mid-meeting, with zero installation or configuration by participants. Anything that fails is out — this is the line that keeps the product from bloating into software.

**Honesty in the UX (the differentiator, enforced visually):** every tile state driven by real system state · every result visibly tagged (resolved vs lower-bound) · every failure spoken plainly · readiness shown with its real coverage number. The experience never claims more than the system knows — *that is the brand,* and it is checkable.

**Cost/latency:** the tile is a lightweight webpage (negligible) · screen content re-renders existing views (free) · chat is text · the small features render existing events (near-zero marginal model cost — the decision/action lines and acks are string formatting, not new inference). The experience layer adds no meaningful cost and sits inside Doc 02's measured latency.

**Platform variance, stated:** all drawn signals identical on Meet/Zoom/Teams (our canvas) · chat DM availability varies — Doc 02 reports it per meeting and copy degrades gracefully ("I'll post it here since this platform doesn't support private messages") · screenshare available on all three via the transport.

**Tunables (pin before build):** final palette values (seed `#35c2b8`) · orb animation timing · the exact state-caption strings · the consent/close lines' final wording · the notes-file template · which of the §2.4 features are on by default vs. spoken-preference-off (default: all on, decision/action lines quiet-chat-only).

---

*One correct meeting, felt end to end:* Proxy joins — a calm teal bloom and one confident line naming the repo and its freshness. It breathes while people talk. Sam asks about the retry logic — "on it" in half a beat, the tile shimmers "checking payments/retry…", and four seconds later the spoken answer lands with a ✓-tagged receipt in chat. A decision forms and a quiet chat line notes it. Asked to build the rate-limiter, Proxy posts its plan, shares its screen while the tests run — the progress view stepping through, then the finished diff previewed — and drops a draft card linking its `/m/` home: *the change, what it changes, your click.* Maya joins late; the consent line re-posts; she types "@proxy catch me up" and gets twenty seconds of exactly what she missed, privately. Priya interrupts Proxy mid-answer — it stops mid-word and finishes in chat. Someone says "Proxy, keep answers shorter" — "noted." At the end: *"Notes + everything I did: link. One draft awaiting approval."* The file is clean enough to forward unedited. Nobody opened an app. Nobody read a manual. They talked, and they glanced.
