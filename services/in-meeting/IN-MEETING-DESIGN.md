# In-Meeting System — Design (converged)

**The whole system in one sentence:** a running log describes everything happening in the meeting;
when Proxy is addressed, we hand Claude the log + the ask + full access and say "get it done" — and
Claude's own agent loop plans (as much or as little as the ask needs) and does it.

That's it. It's Claude Code, pointed at a meeting. Everything below is just *how* we give it the log,
the access, and the speed.

---

## Part 1 — The running log = the AssemblyAI transcript (no model)

The log is just the accumulating **AssemblyAI transcript** — and AssemblyAI already does the cleanup we
want, *by default, for free, with zero added latency*:
- **Filler words removed by default** (um, uh, hmm, mhm, uh-huh) → decluttered.
- **Auto-punctuation + casing + number formatting** on by default → readable.
- **Speaker-attributed** (diarization) + timestamped.

So there is **no continuous Haiku loop** — we delete the whole per-utterance model. AssemblyAI's clean,
speaker-labeled transcript is already agent-readable and good enough for reactive context. A strong
orchestrator reasons over it directly; the one thing the raw transcript can't do — bind "the auth
service" to a real file — the orchestrator resolves **on demand** in the reactive turn (it has the map +
code tools), only when an ask actually needs it. That's cheaper than enriching every utterance forever.

- **Free + instant + clean** — no model on the continuous path, nothing to block, nothing to pay per hour.
- **Fits for hours** — speech is ~150 wpm, so even a 3-hour meeting is tens of thousands of tokens; cache it.
- **Optional:** a single end-of-meeting summary pass for the shareable deliverable (one call, at close —
  not continuous). Validate against real transcripts whether even that's wanted.
- This *deletes* most of the current Scribe (the coalescer + per-window `NoteDelta` extraction + rolling
  summary). Keep only: accumulate the transcript into a durable log + the optional close summary.

## Part 2 — The orchestrator = Claude with access

On any reactive ask, we run **one Claude Agent SDK session** (same engine as Claude Code), primed with
the running log, handed the ask, and given full access. Its native loop does the rest:
- **Planning is native and proportional.** Simple ask ("share your screen", "what's the retry logic")
  → it just acts. Multi-step ask ("build and publish the PR") → it writes a plan and works through it.
  **We never branch on "does this need a plan" — the model decides in its loop.**
- **Heavy sub-problems spawn subagents** (native SDK `Task`) in isolated contexts that report back —
  this is the "workroom," and it's a native agent capability, not something we architect.
- **The nuances are just steps in the loop** — "on it, pulling that up", muting itself, backgrounding
  a task, asking a clarifying question. All decided at runtime by the model, none coded as behaviors.

**Delete** the keyword router, the 8 behaviors, and the dead capabilities catalog. They sit *on top of*
this loop and fight it. Underneath, the wake turn already *is* a Claude agent — we're removing the
scaffolding, not building a new engine.

## Part 2.5 — The engine, the orchestrator, and the workroom (who does what)

These are not three peer systems. There is **one engine and three loops.**

- **The Claude Agent SDK is the engine** — the software that runs an agentic loop (listen → reason →
  call a tool → feed the result back → repeat), with native subagents, streaming, and thinking. It is
  not a thing *in* the meeting; it is what every agent here is *built from*.
- **The orchestrator is the SDK session that lives in the meeting** — always-on, conversational, the
  "front." It **owns every reactive ask**: it receives it, plans (natively), and delivers the result.
- **The workroom is the sandbox (a computer) where the orchestrator sends heavy/code work to run** —
  as a spawned SDK session. It is a *place*, not a second brain.

**So who does the work when a reactive ask comes in?** The orchestrator, always — it owns the ask end
to end. For light work (answer, look up code, talk, show, **mute**) it acts inline in its own loop. For
heavy work (build a PR, run a sim, long research) it **spawns a session in the workroom and coordinates**,
then brings the result back and says it. "The orchestrator does the work" is right; the workroom is just
*where the heavy part physically runs.*

**Why the workroom has to exist at all** (the answer to "what's the point"):
1. **Responsiveness** — the orchestrator must stay live to keep hearing the meeting; a 3-minute build
   can't run *inside* the conversational loop or Proxy goes deaf. The workroom is a *separate* session,
   so heavy work runs in parallel while the orchestrator keeps listening.
2. **Environment** — executing code needs a real, isolated computer (E2B); the orchestrator runs on the
   trusted host inside the meeting and can't run arbitrary builds there.

**The three loops (this is the whole runtime):**
1. **The describe loop (Scribe)** — always-on, cheap (Haiku), one pass per utterance → enhance → append
   to the log. Runs the entire meeting, for hours.
2. **The orchestrator loop** — always listening (a free regex name-gate, zero model cost while idle); on
   address, it spins its Claude loop to handle the ask. Lives the whole meeting.
3. **The worker loops** — spawned per heavy task, each its own Claude loop *in the workroom*, run async,
   return, and end. Many can run at once.

All three are Claude loops. "Loops everywhere," one engine.

## Part 3 — What's hardwired (four enablers) vs. dynamic (everything else)

The rule: **hardwire ONLY what enables dynamism — never anything that decides behavior.** There are
exactly four enablers, and they are all "core mechanisms" or "efficiency," never a scripted action:

1. **I/O — speak + hear.** Claude's words must reach the meeting and the meeting's audio must reach
   Claude. The core mechanism that enables everything; hardwired for speed. (This is the one thing that
   *can't* itself be dynamic — you can't talk your way into the ability to talk.)
2. **Access — one grant, not per-action functions.** Claude's environment holds Recall's controls
   (creds + bot id + the API/SDK), the codebase, and a sandbox with the network. **Claude composes every
   action from that access** — mute, share screen, look up code, research, build — by *using* it. We do
   **NOT** pre-code `mute()` or `share_screen()` as functions. Claude mutes by invoking Recall's control
   through its access, exactly the way Claude Code commits to git without a hand-coded `commit()`
   function. New actions we never imagined work because it's a real computer with real access.
3. **Wake-events — taps on the shoulder (efficiency, not a script).** Claude runs on-demand, not 24/7
   (running it continuously would burn money watching nothing). So something *taps* it: "you're
   addressed," "your worker finished," "your worker has a question," "you just asked something, so the
   next reply is for you." These events don't decide *what* Claude does — they're alarm clocks that
   *wake* it; when woken it reasons and acts freely. This is the same kind of enabler as talking.
4. **Warm + cache.** Keep the access warm (connection open, repo cloned, sandbox running) + cache the
   log/map. Pure latency/cost enabler.

**Everything else is Claude, dynamic — never coded:** what to do, how much to plan, which action to
take, mute / share / look-up / research / build, whether to ask a clarifying question, how to phrase
it, whether an ask is additive to a running task, when to speak a finished result back. All of it is
the model composing from access when a wake-event taps it. We push *everything reasonable* into the
model; we hardwire only the four enablers.

## Part 4 — Warm access + priming (the boring pre-work that buys latency)

This is the one thing we DO set up in advance — not capabilities, just keeping the doors open so
runtime is fast:
- **Recall connection open** — talk/show/mute are ready to invoke instantly (not booted per use).
- **Repo cloned + map ready** — codebase access is warm.
- **Sandbox already running** — no cold start; "run this" is immediate.
- **The log + map as a cached context prefix** (prompt caching, advancing breakpoint) — so pasting the
  whole meeting context into every ask stays fast (a cache read, not fresh processing).
- **A tight prime prompt** — who Proxy is + what access it has, kept small (markdown stripped for TTS).

Result: "share your screen" is ~a few seconds (Claude invokes an already-open connection), not a boot.

## Part 5 — Latency (the honest model, kept simple)

The only thing that must be instant is the **acknowledgment**, never the full plan. Claude's *first
streamed words* are the ack ("on it, looking at the auth flow") — under a second, no planning needed —
and the plan forms and executes *behind* that ack in the same streamed pass. You never need the plan
to be instant; you need the first words to be, and they buy all the planning time.

- **Simple ask** → 0-1 tool calls, streamed → seconds.
- **Multi-step ask** → instant ack, then native planning + tools; the agentic loop adds ~a round-trip
  per tool call, so genuinely heavy work runs as a **background subagent** and the conversational
  session stays live and responsive.
- **Clarifying questions are fast** — noticing ambiguity + asking is cheaper than doing, so they help
  latency; the agent asks, hears the answer (a brief follow-up window where the reply needs no
  wake-word), then plans the real work.
- Targets: first audible response <1s; simple action a few seconds; heavy work perceived as the ack,
  then delivered async. Clears the natural-conversation bar (silence > ~800ms feels delayed).

## Part 6 — The one gate that stays (safety, not a limit)

Full access lets Claude *do* anything, but **irreversible / world-touching actions** (publish a PR,
apply a change, send something external) surface as a **staged draft behind a human click** — Law 3,
human-control is absolute. Reversible in-meeting actions (talk/show/mute/look-up) are free and instant;
only the external/irreversible ones wait for approval. This is trust, not a capability limit, and it's
already built (the accept-route + is_owner fence + barge-in). Conserve it verbatim.

## Part 7 — Keep vs. simplify (mold, don't rebuild)

**KEEP untouched** — the plumbing works: Recall bot join, AssemblyAI STT, Cartesia TTS, the transport
seams, all configs/deps, the warm E2B sandbox + isolation triad, the Scribe's coalescer + ledger + close
pass, the emit connections + human-control gates, the pre-meeting map, streaming, the ack reflex.

**SIMPLIFY** — the two "brains":
- Scribe *output*: structured `NoteDelta` → enhanced-transcript segment.
- Orchestrator: delete the router + 8 behaviors + dead capabilities catalog → one Claude-with-access
  session driven by the native loop.

**FINISH WIRING** (small): screen-share connection (built, not live), web access + native-subagent
access, the follow-up window for clarification, the cost check before heavy async work. Migrate the
Scribe's reference-resolution onto the map (frees the old graph pipeline for deletion).

---

**The senior-lazy-dev summary:** don't build a planning system, don't build capabilities. Keep a great
running log, keep the connections warm, and hand Claude the log + the ask + the keys. Its own loop plans
proportionally, composes any capability from the connections, acks instantly, and backgrounds the heavy
stuff. We conserve all the plumbing and delete the scaffolding.
