# Proxy — System Spec (converged design, source of truth for the build)

> The whole system, distilled from the full design conversation. It is ONE simple, fully
> dynamic, agentic system. Nothing hard-coded, nothing over-engineered. Every capability is
> the agent composing behavior live — code owns only physics, pipes, caching, and the substrate.

## North star
**Proxy behaves like a super-intelligent human teammate:** it studied the codebase before the
meeting, has been in the room the whole time, and on ANY reactive task takes the *quickest path*
to the *best, above-and-beyond* work — verifying it — and communicates like a teammate, engaging
during or after the work. All dynamic. There is not much left to build; we are mostly verifying
and optimizing the one system.

## The one system — the flow

### 1. Pre-meeting (once per repo; refreshed on a signed push)
- Connect → clone → save the repo.
- **Part 1 — tree-sitter (deterministic):** a ranked index of the key symbols with REAL `file:line`.
  Accurate, no hallucination, re-parses only changed files on a push.
- **Part 2 — a bounded Claude comprehension pass:** reads the high-yield code (navigating via the
  index) and produces a **holistic understanding** (what the system is, how it works, key flows,
  domain, conventions, where things live), grounding every specific in a real `file:line`. Includes
  a **verification step** (the understanding's claims/paths are checked against the real repo).
- **Output = ONE dense, high-knowledge overview document** — as much genuinely useful knowledge as
  we can pack, made excellent. Saved (Postgres `repo_maps` + GCS).

### 2. Meeting start — load once, cached (resident all meeting)
- Provision the per-meeting E2B sandbox (repo inside); start the **warm Claude session**.
- Load into the agent's context ONE time, cached: the **lean behavioral prime** (`CLAUDE.md`) and
  the **understanding document as its OWN separate cached block** (NOT stuffed into the lean prime —
  keeps behavior undiluted). Warm + cached before anyone speaks. (Proven: cache carries it, ~3 fresh
  tokens/turn, zero-read grounded answers.)

### 3. During the meeting — it just knows
- The transcript **accumulates in the agent's cached conversation** as it happens → the agent just
  *knows* the whole meeting, cheap (only the delta per wake is fresh). Marathon safety net: quiet
  condensing of the oldest transcript ONLY if it grows huge.

### 4. A reactive task — the quickest path (the loop)
- Name-gate wakes the agent with **[the scene-setting prompt + the task]**. The understanding and
  the whole meeting are already resident + cached — nothing re-loaded, nothing re-read.
- The agent answers from what it knows (zero reads), or does ONE targeted just-in-time lookup, or
  does real work (understand → plan → build → verify-on-real-data → deliver) — its native loop,
  dynamically, no routing.
- Speaks by **streaming its reply**; uses the ONE `to_meeting` MCP tool for the non-spoken channels
  (chat/dm/screen/offer/mute); reaches for the best tool for the job (can pull in tools — gated on
  read-egress).

### 5. The prompt — sets the scene, never scripts
The prompt does NOT tell it what to do step-by-step. It sets the best starting point + direction:
*"You already have all the context and access. Here's the ask. Get it done the fastest way — AND
go above and beyond, actually verify it, deliver the best output. Throughout, handle any blocker,
question, or nuance exactly as a super-intelligent participant would — engage in the meeting
whenever it makes sense, during or after the work."* It trusts Claude to figure out the *how*.

### 6. The nuances (all dynamic, in the prompt)
Instant opener (≤~2.5s) before it digs in · meaningful mid-task beats (not every step) · ask a
clarifying question on a messy/unclear ask → continue when answered · barge-in (human over-talk
stops its speech) · self-echo suppression (its own voice on a no-headphones mic never interrupts or
re-wakes it) · right channel by judgment (gist aloud, detail in chat, artifact on screen, changes
as an offer) · verify on real data before "done"; say plainly when it couldn't run · ground in real
`file:line` or say "not found," never guess · cross-talk → silence · consent · human control
(world-touching = staged offer behind a click).

### 7. The latency design (part of the one system)
Caching (understanding + meeting resident) · warm session · resident understanding (no re-explore) ·
streaming + guaranteed opener · parallel tool calls · adaptive thinking (fixed effort) · fast crash
recovery. Goal: on any ask, the only fresh work is the tiny new ask + the actual work of the task.

## THE CORE BUILD + VERIFY PHILOSOPHY (do not deviate)
The tests are NOT to get a green light. They exist to **optimize the one system.** At every step:
**stop, look at the ACTUAL output and the agent's traces, and ask — given what just happened, how do
we make this better, GENERALIZABLY, for every future case and everything downstream?** Then apply the
fix as a **principle in the prompt / a latency addition / better tool access — never a hard-coded,
situation→action rule, never a deviation from the one dynamic system.** Aggressive optimization
mindset, never defensive rubber-stamping. Example: on a code-gen run, don't accept "it works" — read
the trace, notice independent steps ran serially, and teach the prompt the *principle* (parallelize
independent work) so ALL future tasks benefit. The system stays one, simple, dynamic; the tests make
IT better. Runtime latitude: choose the best optimizations (prompt / latency / tools / functionality)
live, for anything downstream.

## Workstreams (order + how + acceptance)
1. **Consolidate + make all the changes to match THIS spec.** Strip all dead code; get the codebase
   to exactly this simple design, defined to a T; fresh-context verify until the gate is green and it
   matches the spec. (This is WS1 = audit *and* execute the changes.)
2. **Pre-meeting understanding — build it right + prove it.** Part 2 comprehension pass + verification
   step → the dense document → load as the separate cached block. Actually compute the maps, shove
   into context, observe, optimize for max downstream impact. *Accept:* zero-read correct `file:line`
   answers from the loaded document.
3. **Latency to its absolute floor.** Measure real scenarios across MANY runs; monitor traces; make
   GENERALIZED fixes (industry patterns, agentic capabilities, caching, dynamic acknowledgements) to
   the floor for every reactive-ask type. *Accept:* each scenario at its real floor, documented.
4. **Quality + above-and-beyond (dynamic).** The agent dynamically senses and does more: rich,
   structured, aesthetic documents; self-validating/self-verifying code; parallelized work — all via
   the prompt, never hard-coded. Monitor traces; generalize every improvement. *Accept:* every task
   type is verified + above-and-beyond.
5. **Nuance pressure-test.** Hammer the predicted hard cases: messy/unclear prompt → clarifying
   question; how it presents back; how it handles being interrupted; blockers mid-work. *Accept:* it
   reacts as a super-intelligent participant would.
6. **Exhaustive internal verification.** Real transcripts played second-by-second like a live meeting
   (real Claude + E2B, NOT LM-fabricated), every reactive-task type, messy — actually doing the work.
   Apply the philosophy: stop, look at real output/traces, optimize generalizably. Loop until clean.
   *Accept (hard bar) — the five memory/human-emulation tests:* (a) answers from memory zero-read; (b)
   the private-repo TRUST test (correct zero-read on knowledge only the document could provide); (c)
   knows-where-to-look for the tail; (d) recalls what was said earlier (was in the room); (e) no
   forgetting in a long session.
7. **Reconfirm the simple system** — fresh context, no deviation, no gaps, gate green, matches spec.
8. **(Deferred) Customer-ready deploy package** — Cloud Run + Cloud SQL + GCS + E2B + Secret Manager.

## Definition of internally-done
All workstreams pass with the philosophy applied; gate green + comprehensive; the ONLY thing
remaining is the **live last-meeting test** (prove real audio: hear/speak/chat + barge-in/ask→continue
on real Cartesia/Recall). Founder-gated items to name honestly, never fake: read-egress toggle (full
tool-pulling), toolchain'd E2B template (non-Python verify-by-running), live vendor creds + deploy.
