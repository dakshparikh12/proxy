# Proxy In-Meeting — SOURCE OF TRUTH (dense)

This is the single source of truth. Every build node is checked against it with fresh context. If code
disagrees with this doc, the code is wrong. Dense on purpose.

## 0. The system, one line
Proxy is ONE Claude Agent SDK session living in a meeting. Notes accumulate always; when addressed it
wakes with full context + pre-arranged access, reasons, plans, acts, speaks, returns. Every capability
is composed by the agent at runtime. We build **access + the loop + safeguards** — never capabilities.

## 1. The law of this build (guardrail for every node)
- **Nothing hard-coded as a command/capability.** No `speak`/`mute`/`show_screen`/`catch_me_up`/router/
  catalog. The agent composes actions from access. CODE never maps a situation → an action.
- **Engine is dynamic; everything around it is configured infrastructure.** Recall config, hosting, DB,
  server config are set up and pinned (production). The *brain* is the only thing that's free-form.
- **Simplest thing that works.** No overengineering. If a node adds a mechanism the plan didn't call
  for, it fails the Faithful gate.
- **Real or it doesn't count.** No fakes at the seams; validate on real configs + simulated messy runs.

## 2. Pre-arranged access (set up at join; infra, not capability)
Given a meeting URL, join with everything warm so the agent can flow down any path instantly:
- **Warm computer** — E2B sandbox running at join, holding the clone + `index.md` + network. The agent's
  place to run code / build. Idempotent per meeting. Goes there only when it decides to.
- **Recall API access** — bot in the call; the agent holds an authenticated way to call the Recall API
  for this bot (audio out, video out, chat, mute). It composes these calls itself.
- **Speak channel** — a ready pipe: the agent's words → Cartesia → Recall audio. Output of the loop, not
  a command. Highest-frequency path, so pre-arranged (not composed per utterance).
- **Transcript, stored** — AssemblyAI (BYOK inside Recall) streams the cleaned transcript to us; we
  **store it raw as the notes. No model on the transcript.** It's the agent's memory, fed as context.
- **The trigger** — cheap always-on detector: someone says/types "Proxy". Decides WHEN to wake, never WHAT.
Also: the agent has the Recall API reference in its environment so its composed calls are correct/fast.

## 3. The continuous loop (always on, cheap)
Two things never stop: transcript → stored notes; the trigger watches for engagement (addressed by
voice/chat, a reply to a question it asked, or a worker finishing). Idle = the agent is asleep = free.

## 4. The orchestrator turn (the deviation)
1. **Wake with everything** — stored transcript + notes + `index.md` (cached prefix) + the ask + access.
2. **Acknowledge instantly** — first streamed words "on it / let me look" audible <0.5s (just first tokens).
3. **Reason → plan → act, one pass** — Claude Code's loop; depth scaled to the ask; plans and acts as it
   streams. It decides exact steps and how to do each (speak / read code / call Recall for screen / run
   in sandbox / research on the internet / spawn a worker). We coded none of it.
4. **Confirm world-touching actions** — anything irreversible/external is **staged behind a human click**
   (a chat card with an approve link). Reversible in-meeting actions it just does. This gate never bends.
5. **Communicate back — dynamically** — quick work: speak; heavy work: say "on it", spawn a worker, and
   **keep monitoring the meeting** while it runs; speak the result when it lands. Voice vs. chat vs. raise-
   a-hand vs. stay-silent is its judgment.
6. **Return to the loop.**
**Monitoring while working is mandatory** — heavy work is async; the agent is never blocked, never deaf.

## 5. Agent (dynamic) vs. us (access + safeguards)
- **The agent does, at runtime, no code:** understand, plan, choose steps, speak, chat, mute, screen-
  share, look up code, research, build, ask clarifying questions, hear replies, raise a hand, stay silent,
  confirm, decide when to use the sandbox, say honestly "I can't do that / I need access."
- **We build:** the warm sandbox, real Recall API access + join config, the Cartesia speak channel, the
  stored transcript + trigger, the Claude loop, per-ask isolation, the barge-in reflex, reconnect+catch-up,
  the one-circle tile, the consent line, prompt caching. Plus **delete the old brain**.

## 6. The connections (exact wiring — access we set up)
- **Recall** (`api.recall.ai/api/v1`, `Authorization: Token <key>`): `POST /bot` to join **with** config
  (run AssemblyAI streaming + enable audio output + webhooks to us). `output_audio` (real PCM), `output_video`
  (the circle + any render), `send_chat_message`, and a **mute** toggle. *(Today `_api` returns a fake dict —
  make it real HTTP + real join config + real PCM out + add mute.)* Given to the agent as API access.
- **AssemblyAI** — BYOK inside Recall (key registered in Recall). Removes fillers, punctuates, cases,
  labels speakers by default. Transcript webhooks → `POST /webhooks/recall` (HMAC-verified) → drain →
  stored notes + trigger. **Inbound drain already real;** gap is sending the transcription config at join.
- **Cartesia** — real streaming synth (Sonic-3, `wss://api.cartesia.ai/tts/websocket`), PCM matching
  Recall, verbatim text, one fixed voice. *(Today `_synth` stub; swap `_NullTTS`/`_NullSink` for real —
  they're injectable.)*
- **E2B** — install it; bake the sandbox template (Node sidecar + ast-grep + clone, `/health` on 8081);
  create the warm sandbox **at join** (backend written, never called). The agent's computer + internet.

## 7. UX (minimal, honest reflections — never scripted behavior)
- **Orb = one circle.** No state machine. Just presence, rendered as the video tile (a subtle pulse while
  speaking is fine). We do NOT build the 8-state system.
- **Chat = dynamic, NOT auto-parity.** We do not copy every spoken line. The agent decides when chat is
  right (drop an answer while someone's talking, raise a hand, answer an "@proxy" chat message in chat,
  post when asked). Access set up; when/whether is its judgment.
- **Screen-share = Recall video-out, agent-composed.** It renders a *view* (a log, a browser page from
  research, a diff, a result) and pushes it as a frame. The sandbox is headless — no desktop to mirror —
  so "show my work" = render a view. No screen-share command.
- **Consent line** — one line, first action on join, pinned: *"I'm Proxy, an AI participant — I observe and
  record this meeting, and anyone here can address me."*
- **Barge-in** — reflex (too fast for the model): a human speaking cuts Proxy's audio mid-word.

## 8. Cost & latency
Idle = free (cheap trigger) · map = cached prefix (~90% cheaper, ~85% lower latency) · instant ack +
streaming (first word <1s) · biggest model only in async workers · warm sandbox = no cold start · no model
on the transcript. Targets: ack <0.5s, first word <1s, simple answer 1.5–3s, heavy = ack + async.

## 9. Messiest-meeting safeguards (the floor the agent's judgment stands on — MUST be built)
Monitor-while-working · barge-in reflex · per-ask isolation (concurrent asks don't corrupt) · reconnect +
catch-up from stored notes (never amnesiac) · graceful failure everywhere (a failed call is reported
honestly, never faked, never crashes the loop) · robust trigger in cross-talk · **no time cap** (current
code hard-kills at 60 min — remove) · voice/chat/hand all available so the agent can choose how to interject.

## 10. Repo end structure (converge to this; keep config/infra, delete the old brain)
```
services/  premeeting/ (repo→index.md, DONE) · in-meeting/ (the engine) · control-plane/ (connect API,
           GitHub webhook, Recall webhook, provision+launch a meeting)
libs/      llm/ (Claude SDK provider seam + routing) · db/ (Postgres: map/notes/meetings) · http/
           (call_external — one vendor-HTTP seam) · contracts/ (minimal shared types)
apps/      connect/ (onboarding + readiness)
build/ migrations/ config/ tests/ infra/ (KEPT — production hosting/DB/server config, double-checked)
```
- **KEEP (production, verify — don't rewrite):** infra/hosting/cloud, DB + migrations, config, the vendor
  *connection setup* (Recall/Cartesia/E2B account wiring), the `call_external` seam, the provider seam.
- **DELETE (dead — remove the files):** the old orchestrator brain — keyword router, the 8 behaviors, the
  capabilities catalog, `channel_action`, `code_intel` graph, `agentkit` behavior/execution machinery, the
  dead Nango provider, `gen_ui_manifest`, the 8-state tile, `apps/tile` if folded. Nothing from the old
  brain is carried over — the files are deleted, not left dangling.
- **REWRITTEN fresh (not from scratch — the base stands):** the in-meeting engine. Config/setup/connection
  stay; the logic on top is new.

## 11. The build method (self-validating — no external QA phase)
Driven by `build/`. **Every node passes three gates before the next starts:**
1. **Faithful** — fresh-context review vs. THIS doc: nothing hard-coded, no overengineering, no old-brain
   code carried over, simple as described.
2. **Complete** — no gaps, no stubbed seam pretending to work.
3. **Real** — runs on real configs + **simulated messy-meeting scenarios** (not just unit tests): the real
   Claude agent handling messy transcripts, cross-talk, interruptions, additive asks, ambiguity, failures.

**Nodes (whole product, in order):** (1) foundations + infra double-check · (2) connections real · (3) the
loop/brain · (4) trigger + notes · (5) control-plane (provision+join) · (6) safeguards · (7) the worker ·
(8) delete the old brain (files gone, tree green) · (9) whole-product run — full arc + a deliberately messy
simulated meeting. The clean structure (§10) emerges from nodes 2–8.

## 12. Validation (internal now; live later)
- **Internal (now):** fresh-context review each node + the real Claude agent run on a battery of **simulated
  messy meeting scenarios** (diverse, adversarial) scored for correct behavior. Vendor wiring validated for
  *correctness* (config/API shapes right) without paid live calls.
- **Live (later):** the real bot in a real Zoom with real vendors — deferred, low cost when we do it.
- Bar: gap-free, faithful, and it *does the thing* on messy simulations — not toy unit tests.

## 13. The guardrail, restated
If any node introduces a coded command, a capability catalog, a situation→action branch, an unrequested
mechanism, or carries over old-brain code — it fails Faithful and does not ship. The agent does the work;
we do the access, the loop, and the floor.
