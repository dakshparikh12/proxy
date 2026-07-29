# Build Plan — the new in-meeting system (consolidated, gap-closing)

Reads with `IN-MEETING-DESIGN.md` (the target). This plan turns the design into a **sliced, live-validated
build** that closes every gap the adversarial hunt found, deletes the dead scaffolding safely, and never
accepts "passes against fakes" as done.

## 0. Setup & safety
- **Branch** off `v2-build-loop`. Every slice = one green commit; git is the revert net.
- **Dead-code removal is strangler-style, per-slice — NOT a big-bang delete up front.** The hunt proved
  "looks dead but load-bearing" is real (the emit frontier, the Scribe). Delete a thing only when: (a) grep
  proves it orphaned, (b) the new path is proven **live**, (c) the tree stays green.
- **Hard dependency (BLOCKER):** real Claude turns need a **funded Anthropic key** (currently out of
  credits, D-032). Slice 1 cannot be live-validated without it. Also need live vendor creds (Recall,
  Cartesia, AssemblyAI, E2B) + a real test meeting. List these before starting.

## 1. Target system (corrected design)
One engine (Claude SDK) · three loops (describe / orchestrate / work) · four enablers (I/O, access,
wake-events, warm+cache). **With the three corrections the hunt forced:**
1. **Access split by trust** — the *orchestrator* (trusted host) composes meeting-control + web/research +
   code-read; the *sandbox* stays locked for isolated code execution only. Web = host-side tool, never
   sandbox egress. (Security-strict egress deferred per founder, but flagged for re-enable before any real
   customer repo.)
2. **Keep the Scribe** — it's the durable memory + the only correct prompt-cache + referent + close
   deliverable, off the hot path at modest cost. Simplify the *orchestrator scaffolding*, not the notes.
3. **Wire prompt caching for real** — reuse the Scribe's Segment-A/B `cache_control` discipline; make
   `ProviderQuery.system_prompt` accept content blocks so map+prime carry a real breakpoint.

## 2. Dead-code inventory (delete vs keep — precise)
**KEEP (load-bearing, reused):** `transport/*` (Recall join, AssemblyAI STT, Cartesia TTS, turn-taking/
barge-in, carrier) · the Scribe · `workroom` + sandbox + isolation triad · the pre-meeting map · DB +
migrations + `operation_runs` fencing · the emit frontier (EXTEND, don't delete) · infra (Terraform, GCE
MIG, KMS) · the ~2000 tests (regression net).
**DELETE (scaffolding — each gated per §0):** `_BEHAVIOR_CUES` + `select_behavior` (keyword router) · the
8 wake behaviors · `contracts/capabilities.py` (`CAPABILITIES`/`Capability`) + `channel_action` +
`dispatch_capability` (dead) + `gen_ui_manifest` + the closed `ChannelAction.action` Literal ·
`code_intel/repo_provider.py` (dead Nango dup) · the graph pipeline (after referent→map).

## 3. The build slices (walking skeleton → widen). Each is proven LIVE before the next.

**Slice 1 — Walking skeleton (the core; closes the P0 media gap).**
Real Recall bot joins a real meeting → real AssemblyAI STT → "Proxy, <simple question>" → name-gate →
ONE Claude orchestrator turn → real answer → real Cartesia TTS → **real audio out to the meeting.**
- Closes: the media pass (real Recall HTTP in `recall.py`, real TTS/Output-Media sink replacing
  `_NullTTS`/`_NullSink`, the emitter sink on the live path) + the basic orchestrator loop.
- Also needs: a **manual invite path** to get the bot into a test meeting (the `invite_proxy` front door
  has no live caller — wire a minimal route).
- Acceptance (LIVE): the bot speaks a correct answer in a real Zoom/Meet. No fakes at any vendor seam.

**Slice 2 — Grounded code lookup, live.**
Mount code-read (grep/read/the map) on the orchestrator turn; wire prompt caching (map+prime prefix).
- Closes: caching gap (G1/G2); grounded answers with `file:line`.
- Acceptance (LIVE): "Proxy, where's the retry logic?" → cited answer in a real meeting; TTFT within target.

**Slice 3 — The workroom (one heavy task, live).**
Finish E2B backend (install + `Sandbox.create`), **pre-provision the warm sandbox at join**, dispatch →
work → return-and-speak, live.
- Closes: warm-sandbox stub (G3), the async dispatch/return on the real path.
- Acceptance (LIVE): "Proxy, build X" → "on it" → real result spoken back; meeting never froze.

**Slice 4 — Meeting controls (mute / share-screen), live.**
Extend the access so the orchestrator composes Recall controls (mute) + wire the canvas/screen-share into
the live runtime (the built-but-not-connected surface).
- Closes: mute-not-exposed (GAP-1), screen-share-not-connected (GAP-5).
- Acceptance (LIVE): "Proxy, mute" and "Proxy, share your screen" both work in a real meeting.

**Slice 5 — The conversational loop, live (dynamic, not hard-coded).**
Wire the *taps*: follow-up window (next reply after Proxy asks counts as addressed), `needs_clarification`
producer in the workroom, per-ask "Proxy, quiet"/barge-in model-loop kill, modify-in-flight.
- Closes: follow-up window (total gap), needs_clarification producer, per-ask quiet, additive-in-flight.
- Acceptance (LIVE): Proxy asks a clarifying question, hears the un-prefixed answer, continues; "Proxy,
  stop" halts the model loop; "also add tests" reaches the running task.

**Slice 6 — Concurrency & durability, live.**
Per-ask session (fix the shared `WakeTurn`/session corruption), non-blocking routing pipe, raise/remove
the 1-hour cap, thread rejoin history (no amnesia), bound session growth over hours.
- Closes: shared-session (GAP-1 critical), serial pipe (GAP-2), 1-hour cap (GAP-4), rejoin amnesia
  (RISK-6), unbounded session (RISK-5), slot exhaustion (RISK-7).
- Acceptance (LIVE): two asks 1s apart both handled correctly; a 90-min meeting doesn't cut off.

**After each slice:** delete the corresponding old scaffolding (strangler), tree stays green.

## 4. Validation discipline (this is how we get NO gaps)
- **Done = ran LIVE in a real meeting.** Never "passes against fakes" for any vendor-touching path. This
  is the exact discipline Phase 1 lacked.
- **Adversarial gap-hunt per slice** — fresh agents try to break each slice before it's called done.
- **Real-data acceptance per capability** (the founder's three-tier / region gate).
- **Regression net** — the reused assets keep their existing tests green.
- **Human-control conserved** even with security deferred: barge-in, "quiet", staged-draft-behind-a-click.

## 5. Gap → slice traceability (every known gap has a home)
media-pass→S1 · invite front door→S1 · caching→S2 · code grounding→S2 · warm sandbox/E2B→S3 · dispatch
return→S3 · mute→S4 · screen-share→S4 · follow-up window→S5 · needs_clarification→S5 · per-ask quiet→S5 ·
modify-in-flight→S5 · shared session→S6 · serial pipe→S6 · 1-hr cap→S6 · rejoin history→S6 · session
growth→S6 · access-split→S1/S4 · keep-Scribe→(no deletion) · sandbox security→DEFERRED (flagged).

## 6. Product-level arc (the full journey must hold end-to-end)
connect + pre-meeting map (DONE) → **join** (invite → real bot in-call, S1) → **in-meeting** (describe
loop always-on; orchestrator on address; workroom for heavy; controls; conversational; concurrency) →
**close** (the Scribe's notes deliverable). Latency targets: ack <500ms, first word <1s, simple action a
few seconds, heavy = ack + async. Idle = free (regex name-gate, confirmed).

## 7. Definition of done (whole plan)
Every slice proven live · all §5 gaps closed · old scaffolding deleted · tree green (ruff/mypy/bandit/
naming/contracts) · one real end-to-end meeting demonstrated · security-defer item logged for re-enable.
