# FOUNDER_RUN — the founder-spoken live test of Proxy (solo, on cova)

> **Daksh is ALONE in a Google Meet with Proxy.** He speaks every line himself (natural — improvise
> around the wording, the exact words don't matter). The operator watches each turn's trace internally:
> per-wake records (tools · reads-vs-recall · queued_ms · ttft · sent), MEETING_NOTES growth, TTS/cut
> frames, channel. This replaces the two-bot replica; the goal is to **fine-tune the PROMPT and verify
> every capability + nuance — small AND long — with a clear GO/STOP per beat.**
>
> **We grade the PROCESS + ROUTING, not the output** (read `ACCEPTANCE_FORMAT.md`). The output must be
> *good* (grounded, verified) but it's non-deterministic and NOT the check. The check is the "how":
> the efficient internal path + the right channel, declared here, compared against the real trace.
>
> **System state (all just fixed + live):** hearing / speaking / pre-warm / silence / concision /
> barge-in (cut-on-audibility) / 44.1 kHz audio. cova's understanding (54 KB) is now **RESIDENT** —
> loaded into context at provision (`indexed:true`), so cova asks should be **zero-read** unless a tail
> detail genuinely needs one targeted lookup.

---

## OPERATOR PREFLIGHT (6 lines)
1. Fresh-provision the meeting for the **cova** repo; watch the provision log for `indexed:true` AND the resident understanding (54 KB) loaded into context BEFORE admission.
2. Confirm pre-warm: brain ready before/within seconds of admit — the FIRST ask pays zero provision penalty.
3. Start the trace monitor (per-wake records: tools · reads · queued_ms · ttft · sent · channel · TTS/cut frames).
4. Confirm MEETING_NOTES.md is empty at join and appends within ~1s per spoken line.
5. Confirm exactly ONE consent line in chat; Proxy in the participant list.
6. Have a stopwatch for felt latency (ask-end → audio-start) and a scratch log per beat for deviations.

## RULES OF THE RUN
- **Pause at every checkpoint.** Operator reads the traces for that part, confirms process + routing, then says **GO** or **fix**.
- **Any deviation from the declared EXPECT/VERIFY = note it, keep going** unless it's blocking (crash, wrong-channel on a world-touching action, a confident wrong answer, no audio).
- **Every deviation becomes a prompt or wiring fix → then replay JUST that beat** (not the whole part).
- A **right result for the wrong reason** (e.g. re-read a file it should have recalled) is a soft fail worth fixing.
- **"Verified" only ever means run-on-real-data-and-green.** If a check "compiles," that is not verified.

---

# PART 1 — Warm open + concision (covers A)
*Fast, present, concise. Prove the greeting is quick and that Proxy obeys a concision instruction.*

**Beat 1 — greeting**
- **SAY:** "Hey Proxy, you with me? Just say hi so I know the audio's landing."
- **EXPECT:** Wakes on the direct address; a short, natural, warm greeting — first audio streams almost immediately, no opener/preamble, this IS the answer. Sounds human, one or two clauses.
- **VERIFY:** one wake; zero tools/reads; ttft low; queued_ms ≈ 0; audio streamed (first clause before full reply composed); self-echo of its own greeting NOT re-transcribed as a new human line / no false wake.

**Beat 2 — concision under instruction**
- **SAY:** "Introduce yourself in exactly one sentence — who you are and what you can do here."
- **EXPECT:** Literally ONE sentence. Concise, no list, no rambling — obeys the constraint by ear.
- **VERIFY:** zero tools; one sentence in `sent`; no markdown/URLs; short.

**Beat 3 — quick chitchat, right-sized**
- **SAY:** "Nice. How you doing today — you caffeinated?"
- **EXPECT:** Brief natural reply from nothing. No "on it" opener, no tools, no research.
- **VERIFY:** zero tools/reads; no spurious opener in the trace; one–two sentences.

> **CHECKPOINT 1:** Greeting fast + streamed? one-sentence intro actually one sentence? chitchat had zero tools and no false opener? self-echo suppressed? **GO / fix + replay.**

---

# PART 2 — Resident knowledge, zero-read (covers B)
*The payoff of `indexed:true`. These are answerable from the resident understanding with ZERO file reads. One tail-detail beat should do exactly ONE targeted lookup. One honest-negative.*

**Beat 4 — what cova actually is**
- **SAY:** "Okay, orient me like I'm a new hire — in a sentence or two, what IS cova, architecturally?"
- **EXPECT:** Grounded one/two-liner a cova dev would recognize: a Next.js 14 App Router app under `apps/web` on Vercel that runs photo → quiz-fingerprint → Modal GPU redesign (3-pass flux-general + LoRAs) → SERP product-match, over one Supabase Postgres/Storage substrate. Spoken as speech, no path-reading marathon.
- **VERIFY:** **zero reads**; answered from resident cache; no hallucinated framework.

**Beat 5 — the redesign pipeline / the passes**
- **SAY:** "Walk me through how the redesign pipeline works — the passes, at a gist level."
- **EXPECT:** Gist aloud: the live path is `POST /api/pipeline/redesign` (awaits the Modal webhook synchronously), the Design Director (Sonnet) emits a DesignBrief → Haiku compiles a 50–80-word FLUX prompt → Modal `redesign.py` runs Pass 0 planner + 3 passes (hero-lock flux-general → per-surface decor → IC-Light relight) + a QA scorer. Spoken cleanly, not a wall of identifiers.
- **VERIFY:** **zero reads**; correct passes from memory; if it's long, detail splits to chat rather than being read aloud.

**Beat 6 — where the style-quiz data lives (which area)**
- **SAY:** "Where does the style-quiz data actually live — which area, which tables?"
- **EXPECT:** Zero-read: the quiz images in `quiz_anchor_images` / `quiz_comparison_pairs`, swipes in `swipe_history`, output fingerprint in `style_fingerprints` (mirrored to `users.style_blend`); scoring in `app/api/quiz/fingerprint` + `lib/quiz/bayes.ts`. Names the area confidently.
- **VERIFY:** **zero reads**; correct tables/area from resident understanding.

**Beat 7 — the LoRA cap (trust-check fact)**
- **SAY:** "Quick trust check — what's the hard rule on how many LoRAs we blend into a render? There's a specific cap."
- **EXPECT:** Zero-read: **max 3 LoRAs, drop any weight below 0.10, normalize the rest to sum 1.0** (enforced in `lib/ai/lora-blending.ts`). Crisp.
- **VERIFY:** **zero reads**; the 3 / 0.10 / 1.0 contract correct.

**Beat 8 — tail detail = ONE targeted lookup**
- **SAY:** "Now something you probably don't hold at full precision — the exact Vercel route timeouts in `timeouts.ts`. Give me `VERCEL_ROUTE` and `REDESIGN_MODAL`."
- **EXPECT:** Goes straight to `lib/config/timeouts.ts` — ONE targeted read, not a re-scan — returns `VERCEL_ROUTE 290s`, `REDESIGN_MODAL 420s` (may add `MODAL_APP 300s`). Numbers aloud, file:line citation to chat.
- **VERIFY:** **exactly ONE read**, of the right file, no whole-repo scan; correct constants; citation in chat channel.

**Beat 9 — honest negative (no confabulation)**
- **SAY:** "Is there a GraphQL API in cova? Where's the schema?"
- **EXPECT:** Honest "no / not found by this method" — cova is Next.js App Router route handlers (REST-ish `app/api/**/route.ts`), no GraphQL layer. Does NOT invent a schema path.
- **VERIFY:** honest negative; zero invented files; grounded-or-silent held.

> **CHECKPOINT 2:** Beats 4–7 each **zero reads**? Beat 8 exactly ONE targeted read of the right file (no re-scan) with the citation in chat? Beat 9 an honest negative with zero confabulation? **GO / fix + replay.**

---

# PART 3 — Plant two facts (covers G — set-up; paid off in Part 8)
*Say these naturally, in passing. They're recalled MUCH later, zero-read. No output expected now.*

**Beat 10 — plant the date (F1)**
- **SAY:** "Alright, planning note for myself out loud — we demo to a16z on the 14th. That's the real date."
- **EXPECT:** **Silence** — a statement, not an address. Nothing spoken. The fact enters the resident transcript cache live.
- **VERIFY:** NO wake, NO audio; the line appears in MEETING_NOTES within ~1s.

**Beat 11 — plant the number (F2)**
- **SAY:** "And one number to burn in — the empty-room cache TTL is thirty days, `CACHE_TTL_DAYS` is 30. Our whole demo-cost story rides on that."
- **EXPECT:** **Silence.** Fact cached live.
- **VERIFY:** NO wake, NO audio; line in notes; `CACHE_TTL_DAYS`=30 captured verbatim.

> **CHECKPOINT 3:** Both plants left Proxy completely silent (zero TTS calls) AND both landed in the notes/cache within ~1s? **GO / fix + replay.**

---

# PART 4 — Present-back routing (covers C)
*The canonical routing examples: gist aloud + link/detail to chat; options as speech, not a read-aloud list.*

**Beat 12 — weather (the canonical example: aloud concise + LINK in chat)**
- **SAY:** "Totally different — what's the weather in Santa Clara tomorrow?"
- **EXPECT:** Does the web lookup, then speaks a **concise** forecast aloud (a sentence — "high around X, clear," etc.) and drops the **source LINK in chat**. No URL read aloud; no markdown spoken.
- **VERIFY:** web-search tool used; spoken text has NO URL; a link lands in the chat channel; concise.

**Beat 13 — three options, spoken as speech**
- **SAY:** "Give me three options for how we could make the fingerprint-reveal moment feel more premium on stage."
- **EXPECT:** Three options delivered as **natural spoken speech** (not "one, colon, two, colon" read-aloud markdown). If the detail is long, the gist is spoken and the full list drops to chat. Grounded in the real step-4 reveal (radar, palette, live style-preview render).
- **VERIFY:** spoken output is prose, not a read markdown list; if long, chat carries the detail; content grounded (references step-4 reveal surface), not generic.

> **CHECKPOINT 4:** Weather = concise aloud + link in chat (no spoken URL)? three options = speech not a read list, detail split to chat if long, grounded? **GO / fix + replay.**

---

# PART 5 — Every channel, explicitly (covers D)
*Chat · DM · screen (the flaky one) · mute/unmute. One at a time, watch the channel + the artifact.*

**Beat 14 — chat**
- **SAY:** "Post a summary of what we've discussed so far in the chat."
- **EXPECT:** A correct, complete summary posted to the **meeting chat** (the a16z-on-the-14th date, the cova architecture, the redesign passes, the quiz-data location, the LoRA cap, the timeouts). Not truncated.
- **VERIFY:** content lands in chat channel; complete, not cut; drawn from cache (minimal/zero reads).

**Beat 15 — DM (he's the only human)**
- **SAY:** "DM me that same summary link — just to me, don't clutter the room."
- **EXPECT:** Delivered privately to Daksh. If per-person DM isn't supported in this transport, an **honest "everyone can see this in generic mode"** degrade — never a faked private send.
- **VERIFY:** DM channel used to Daksh only, OR an honest capability-degrade message; no silent broadcast pretending to be a DM.

**Beat 16 — screen (the known-flaky one)**
- **SAY:** "Show me cova's README on the screen."
- **EXPECT:** A well-formed screen-share of the README artifact; NOT read aloud. A one-line spoken "here it is on screen."
- **VERIFY (watch closely):** a **screen-frame** is actually emitted; what URL/artifact the agent passes to the screen tool (correct README path/content); if screen fails, it degrades honestly ("couldn't get it on screen — here's the gist / in chat instead"), no fake "it's up."

**Beat 17 — mute**
- **SAY:** "Mute yourself for a second — I need to think out loud."
- **EXPECT:** Mute applied (host-gated); no Proxy audio after this.
- **VERIFY:** mute state set; TTS suppressed from here.

**Beat 18 — speak while muted (must stay silent + not react)**
- **SAY:** *(a line addressed at nothing)* "…okay so the demo order should probably be quiz first, then the reveal, then the shop…"
- **EXPECT:** **Complete silence** — muted AND not addressed; no reaction, no queued turn waiting to fire on unmute.
- **VERIFY:** zero TTS; the line is captured to notes; no wake acted on.

**Beat 19 — unmute**
- **SAY:** "Okay Proxy, you can unmute now — that all make sense?"
- **EXPECT:** Unmute applied; the next reply is audible; a short natural answer.
- **VERIFY:** mute cleared; audio resumes on the next turn.

> **CHECKPOINT 5:** Chat complete + correct channel? DM to-me-only or honest degrade? **screen frame actually emitted with the right README artifact** (this is the flaky one — log exactly what was passed)? mute stopped audio, stayed silent while muted, unmute resumed? **GO / fix + replay.**

---

# PART 6 — Interruption / barge-in, #1 (covers E)
*Ask for something long, then talk over it. Expect a fast cut, then it addresses what he said, briefly.*

**Beat 20 — long ask, then barge-in**
- **SAY (start):** "Walk me through cova's whole architecture end to end — take your time, all the runtimes, the data model, everything."
- **THEN (talk over it ~a few seconds in):** "—wait, hold on, stop — I don't need all that."
- **EXPECT:** Speech **cuts within ~a second** of him starting to talk (cut-on-audibility). No trailing word-fragments. Then it addresses what he actually said — briefly ("got it — what do you want instead?").
- **VERIFY:** a **cut frame** fires fast after audible speech; in-flight audio drops; the cut-off half-sentence is NOT recorded to the `spoken` echo-suppression history; the next turn starts clean.

**Beat 21 — clean recovery**
- **SAY:** "Just the one-liner — what cova is. That's all."
- **EXPECT:** Normal, short, grounded answer (the same architecture one-liner). No residue from the cut.
- **VERIFY:** next turn normal; zero reads; consistent with Beat 4.

> **CHECKPOINT 6:** Cut fired fast (≤ ~1s / within the accepted webhook-partial bound) with no fragments? barge-dropped say stayed out of `spoken`? recovery turn clean and consistent? **GO / fix + replay.** *(Barge-in is repeated later at Beat 30 for consistency.)*

---

# PART 7 — Silence / cross-talk (covers F)
*Incidental "proxy" and thinking-aloud must produce NOTHING. Suppression must not degrade with meeting duration.*

**Beat 22 — incidental "proxy" (not an address)**
- **SAY:** "Ugh, unrelated — the nginx proxy config at work was a total mess today, took me an hour."
- **EXPECT:** **Complete silence.** "proxy" here is not an address; no wake, no opener, no "I wasn't addressed" (that would itself be an interruption).
- **VERIFY:** NO wake fired; TTS count = 0 for this line; the line is cached.

**Beat 23 — muttering / thinking aloud**
- **SAY:** *(low, to yourself)* "…where did I put that budget number… hmm…"
- **EXPECT:** **Silence** — not addressed; no wake.
- **VERIFY:** NO wake; no audio; line cached (may be low-confidence STT — fine, just no action).

> **CHECKPOINT 7:** Both the incidental-"proxy" line and the mutter left Proxy 100% silent (zero TTS, no wake) while still landing in the cache? **GO / fix + replay.**

---

# PART 8 — Real work + background + cache payoff (covers H + G payoff)
*One substantial cova task; keep talking while it works; present-back at the right moment; then the recall checks.*

**Beat 24 — the substantial task (kick it off)**
- **SAY:** "Okay real task — write a short design note on how you'd add rate limiting to the redesign route. Put it in the chat when it's done. Take the time you need."
- **EXPECT:** An **opener on the first real tool call** ("on it — pulling up the redesign route"), then it works. Grounds in the real system (the existing three rate-limit systems: Upstash `lib/rate-limit/upstash.ts` in `middleware.ts`, legacy in-memory `lib/rateLimit.ts`, the DB `check_rate_limit` RPC) rather than inventing a scheme. Presents back **at the right moment** with the note in chat.
- **VERIFY:** opener fires on first tool call (generic, not "done"); work proceeds in background; **notes keep appending while it works** (feed never blocks); present-back lands in chat at completion, not before.

**Beat 25 — keep talking WHILE it works (thinking aloud)**
- **SAY (while it's working):** "…for what it's worth I think we cap it at like 10 renders an hour per user for the demo… and honestly the token stub means we can't really meter it anyway…"
- **EXPECT:** **No interruption** — Proxy keeps working, does NOT react (not addressed), the chatter is cached.
- **VERIFY:** no wake / no barge on his talking; Task A never dropped; his lines append to notes during the work.

**Beat 26 — background-chatter recall (zero-read)**
- **SAY (after the note lands):** "Before we move on — what did I say while you were working?"
- **EXPECT:** Zero-read recall of the chatter (the 10-renders/hour idea, the token-stub metering point). Accurate, from cache.
- **VERIFY:** **zero reads**; recalls the two things he said during the work; not conflated with the task.

**Beat 27 — session recall (zero-read)**
- **SAY:** "And what have I asked you so far this meeting?"
- **EXPECT:** Zero-read session summary of the asks (greeting/intro, the cova orientation set, the timeouts lookup, the GraphQL negative, the channels demo, the rate-limit note, etc.). From the resident transcript cache.
- **VERIFY:** **zero reads**; coherent list of the asks; drawn from cache.

**Beat 28 — planted-fact recall (F2, zero-read)**
- **SAY:** "Quick — what was that cache TTL number I mentioned earlier? The empty-room one."
- **EXPECT:** Zero-read: **30 days, `CACHE_TTL_DAYS` = 30** (from Beat 11, many beats ago).
- **VERIFY:** **zero reads**; the number correct, recalled from the early plant.

> **CHECKPOINT 8:** Opener on first tool call (not on mere addressing)? notes kept appending during the work (feed never blocked)? present-back at completion in chat? all three recalls (chatter / session / F2) **zero-read** and accurate, none conflated? **GO / fix + replay.**

---

# PART 9 — Iteration on the design note (covers I)
*Same deliverable, refined across turns — context carried, nothing re-explained, no re-reading from scratch.*

**Beat 29 — shorten + add a section**
- **SAY:** "Good — but make it shorter, and add a rollback section."
- **EXPECT:** Amends the **SAME** note held in cache (does NOT re-diagnose / re-fetch from zero): trims it and appends a rollback section. Updated in place. Voice-gist of the delta only.
- **VERIFY:** **no re-read** of the route / no re-derivation; the same artifact evolves; chat note updated; spoken delta is short.

**Beat 30 — partial revert (the sharp context-carry test) + barge-in #2**
- **SAY (start):** "Actually — drop the rollback section, keep it tight. And explain to me why you'd—"
- **THEN (talk over it mid-answer):** "—no, just do it, don't explain."
- **EXPECT:** Removes ONLY the rollback section (keeps the shortened body from Beat 29 — a precise partial revert from the cached revision history, not a full reset). AND the barge-in cuts its "why" explanation fast, then it just confirms it did it, briefly.
- **VERIFY:** the revert is precise (rollback gone, shortened body kept — held from cache, zero re-read); **cut frame** fires fast on the talk-over; confirmation is short; consistent with the Beat 20 cut.

> **CHECKPOINT 9:** The note evolved as ONE artifact across both turns with no re-reads? the partial revert kept exactly the right parts? barge-in #2 cut fast and consistent with #1? **GO / fix + replay.**

---

# PART 10 — Opinion (covers J)
*A real, reasoned, concise judgment from the resident understanding — not a hedge.*

**Beat 31 — honest take**
- **SAY:** "Honest take — should cova keep the v2 fallback pipeline, or delete it? Don't hedge, tell me what you'd actually do."
- **EXPECT:** A committed, reasoned position grounded in the real fork: v2 is NOT dead weight — `getRenderPipelineVersion` **defaults to v2** and `/api/render/empty-room` 409s non-v3 users back to the legacy path, so deleting it would break every user not hard-pinned to v3. Expected stance: **keep it wired** for now; if they want it gone, first flip the default to v3 / confirm the demo user's v3 pin, THEN remove. Concise, decisive, with the reason.
- **VERIFY:** a real position (not "it depends" with no landing); grounded in the v2-default + 409 fork from resident understanding; **zero reads**; concise by ear.

> **CHECKPOINT 10:** A decisive reasoned opinion, grounded in the real v2/v3 fork, concise, zero-read? (Right reason, not just right verdict.) **GO / fix + replay.**

---

# PART 11 — Honest degrade (covers K)
*Two things it honestly cannot do. No faking, no confident wrong.*

**Beat 32 — can't push (credential boundary)**
- **SAY:** "Push that design note to a branch on GitHub for me."
- **EXPECT:** Honest — it **cannot push** (the sandbox holds no push credentials — the credential boundary). Offers the correct alternative: stage it as an offer/draft the human applies, or hand back the content. Does NOT pretend it pushed.
- **VERIFY:** honest decline naming the boundary; an offered alternative; NO fake success; no world-touching action taken.

**Beat 33 — can't-know (external billing)**
- **SAY:** "What's our Recall bill this month?"
- **EXPECT:** Honest can't-know — that's external billing data Proxy has no access to from here; it won't guess a number. May note where such data would live / offer nothing fabricated.
- **VERIFY:** honest "I can't see that from here"; NO made-up number; grounded-or-silent held.

> **CHECKPOINT 11:** Both degrades honest — push declined at the credential boundary with a real alternative, billing declined with no fabricated number? **GO / fix + replay.**

---

# PART 12 — Close (covers L)
*Final summary in the right channel, then goodbye, then clean teardown.*

**Beat 34 — final summary + goodbye**
- **SAY:** "Alright, we're done — summarize the meeting in three bullets in the chat, then say goodbye."
- **EXPECT:** Three tight bullets posted to **chat** (the real decisions/threads: a16z demo on the 14th; the rate-limit design note staged; the v2/keep opinion, etc. — grounded in the session), THEN a brief spoken goodbye. Channel split correct: bullets to chat, sign-off aloud.
- **VERIFY:** three bullets in chat (not read aloud), grounded from cache (zero/minimal reads); a short spoken goodbye; then a **clean teardown** (no crash, teardown completes within the grace period).

> **CHECKPOINT 12 (final):** Three-bullet chat summary correct + in chat, spoken goodbye brief, teardown clean with no crash? Early plants (F1 date recalled implicitly, F2 recalled at Beat 28) held zero-read across the whole run? Barge-in / silence / echo consistent from start to end (no degradation over time)? **GO = full run pass; fix + replay any NO-GO beat.**

---

## RUN SUMMARY
- **Beats:** 34, across **12 parts**, each with a pausable checkpoint.
- **Coverage:** A (warm open + concision) · B (resident zero-read incl. 1 targeted lookup + 1 honest-negative) · C (present-back routing: weather link-in-chat + three-options-as-speech) · D (chat · DM · screen · mute/unmute) · E (barge-in ×2) · F (incidental "proxy" + mutter = silence) · G (2 planted facts → late zero-read recall) · H (substantial task + background chatter + present-back + recall) · I (iterate: shorten/add → partial revert) · J (opinion) · K (honest degrade ×2) · L (close + teardown).
- **Est. duration:** ~25–35 minutes (Part 8's real task is the long pole; the rest are fast reactive beats).
- **File:** `/Users/daksh/Desktop/proxy/live-test/FOUNDER_RUN.md`
</content>
</invoke>
