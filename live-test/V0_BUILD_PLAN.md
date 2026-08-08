# Proxy v0 — the consolidated build order (single source)

Everything finalized in the optimization + interaction-layer work, as one numbered build order. **Rule:
simple path, fully effective, no overengineering. Claude does the work; these are add-ons/config/prompt/
tuning.** Each item: **what · why · files · validate**, tagged `[NEW]` (build it), `[AUDIT]` (may already
exist — verify/keep/finish), `[CHANGE]` (touches the working path — behind a seam with a fallback to
current behavior). Delta-aware: audit first, add only the genuine gap, never rebuild what works.

---

## GROUP A — Reasoning-turn config & caching (`session_host.py`, sandbox env)
1. `[CHANGE]` **Model → Sonnet 5.** `DEFAULT_MODEL = "claude-sonnet-5"` (currently `claude-sonnet-4-6`).
   *Why:* latency/cost. *Validate:* quality holds on the e2e replays; fall back to 4-6 if it regresses.
2. `[AUDIT]` **`effort:"high"` + adaptive thinking, locked for the session.** Already present — confirm it
   stays byte-stable (no per-turn change) so the cache never invalidates. *Validate:* cache_read high.
3. `[NEW]` **1-hour cache TTL** via `ENABLE_PROMPT_CACHING_1H=1` in the sandbox env. *Why:* a 60-min meeting
   fits one TTL → quiet gaps don't expire the cache (retires the keep-warm ping). *Validate:* usage shows
   `cache_read` after a >5-min gap.
4. `[AUDIT]` **`max_turns` soft gate** (currently 40; keep ~40–50). *Why:* nudges subagent delegation, not
   truncation.
5. `[NEW]` **`maxBudgetUsd` per-meeting hard cost ceiling.** *Why:* real spend cap. *Validate:* run halts at
   the cap.
6. `[AUDIT]` **`strictMcpConfig: true`** + keep `settingSources:["project"]` (we need the prime). *Why:*
   deterministic tool surface.
7. `[AUDIT]` **`abortController` → `query()`** — barge-in / stop-spending halts the loop. *Validate:* a
   "Proxy, stop" mid-turn actually cancels model work.
8. `[NEW]` **`getSdkEnv()` auth-key strip** — drop conflicting OAuth/API-key/Vertex before spawn. *Why:*
   wrong-auth insurance (we use the OAuth token).
9. `[AUDIT]` **stderr filter + secret redaction** — error-lines only, scrub `sk-ant-*`/Bearer/token. *Why:*
   secrets-never-logged guard.
10. `[AUDIT]` **Streaming (`includePartialMessages`)** — deltas as they form (feeds C & Group E).

## GROUP B — Caching hygiene & pre-warm
11. `[NEW]` **Prime the cache with a near-empty first request during prep** so the big prefix pays its
    one-time `cache_creation` before anyone speaks. *Validate:* first real wake is `cache_read`, no cold TTFT.
12. `[AUDIT]` **Transcript is a strict suffix** — appended to `MEETING_NOTES.md` tail, prefix never mutated.
13. `[NEW]` **Compaction-preserve block** in the resident CLAUDE.md (objective, files touched, decisions,
    results) so auto-compaction summaries keep the load-bearing context.

## GROUP C — Context management (prompt/behavior)
14. `[NEW]` **Teach subagent context-isolation** — delegate verbose work (test runs, log parsing, deep
    research) to subagents so their output never bloats the main context (each has its own prefix cache).
15. `[AUDIT]` **Read-only sub-agent fan-out** for independent research/lookups (safe parallelism).

## GROUP D — Wake path latency (STT / `transport`)
16. `[CHANGE]` **Wake on AssemblyAI immutable partials, not end-of-turn** (~300ms vs ~1280ms). *Validate:*
    measured wake latency drops ~1s; no false wakes on partials.
17. `[NEW]` **`keyterms_prompt: ["Proxy", …]`** — reliable wake-word capture.
18. `[NEW]` **`mode:"min_latency"` + turn-silence tuning** (tight for short commands, looser for long asks).

## GROUP E — Output path latency (TTS / `transport`)
19. `[AUDIT]` **Delta-diff streaming** (`text.slice(lastText.length)`, reset on `msg.id`) wired to TTS.
20. `[CHANGE]` **Cartesia continuations** — pipe tokens as generated → first audio on the first clause.
21. `[NEW]` **Lower `max_buffer_delay_ms` + clause-terminated punctuation + raw PCM out.** *Validate:*
    measured time-to-first-audio drops.

## GROUP F — Interaction layer & skills (`in_meeting/`, `workroom.py`)
22. `[CHANGE]` **Swap in the consolidated interaction layer** (the finalized 12-section resident layer) →
    `interaction_layer.md`. *Validate:* seeded into the sandbox CLAUDE.md; behavior shows in replays.
23. `[NEW]` **Seed the 3 skills** (`meeting-artifact`, `meeting-diagram`, `background-job`) into
    `SEED_FILES` — they exist but aren't seeded (the known gap). *Validate:* present + invocable in-sandbox.
24. `[NEW]` **Prompt-hygiene constants** — no internal monologue; only claim what tool evidence proves;
    guardrails appended last.
25. `[NEW]` **~12-line AI-slop block** into artifact generation (no fake examples, no bullet-bloat, no
    changelog narration, no unexplained magic numbers, UX visual tells).
26. `[NEW]` **Coding-turn lines** — "do only this step, then stop"; never mark done on a failed step; paste
    RED/GREEN evidence.
26a. `[CHANGE]` **Screenshare as a `to_meeting` medium (CORE).** Simplest path: enable Recall's
    Start-Screenshare surface carrying our live output-media HTML page. The agent decides when (interaction
    layer) and should reach for it whenever there's something worth *seeing*. *Validate:* a screenshare
    action emits + renders our live HTML up-to-post.
26b. `[CHANGE]` **Raise-hand overlay (CORE).** Simplest path: one raise-hand state on the tile we already
    control (green bar top-right "✋ Proxy raised its hand") + a chat drop, cleared when it speaks — same
    surface as the orb. Agent decides when (busy room / unprompted contribution). *Validate:* raise-hand
    action flips the tile overlay + drops chat, up-to-post.

## GROUP G — Tools (E2B template + prompt)
27. `[CHANGE]` **Serena** baked into the E2B template (deferred MCP schema → cache-safe) + taught use-when
    (symbol lookup/nav). *Validate:* invocable; token count on a lookup drops vs whole-file reads.
28. `[AUDIT]` **ast-grep** — confirm baked; taught use-when (structural search/rewrite).
29. `[AUDIT]` **Context7** — confirm available; taught use-when (external library docs).
30. `[NEW]` **Design skeleton** (Open Props tokens + house theme + Chart.js/ECharts, Mermaid/D2, reveal.js,
    inline/CDN) into `meeting-artifact`/`meeting-diagram`. *Validate:* a generated artifact looks designed.

## GROUP H — Pre-meeting / warm-up (biggest win — gated)
31. `[CHANGE]` **Per-repo E2B template bake** (toolchain + Serena + ast-grep + deps as build layers).
32. `[CHANGE, gated beta]` **E2B pause/resume of a pre-primed sandbox** — prime once (repo + files + warm
    session + built cache), pause at the calendar signal (zero compute, no TTL), resume ~1s at start.
    *Validate:* multi-cycle prime→pause→resume with session + cache intact; **fall back to cold-build if the
    beta fails** (keeps the working path).

## GROUP I — Dev-only (never in the meeting loop)
33. `[NEW]` **promptfoo + deepeval** battery for our own eval gate.
34. `[NEW]` **prompt-review pass** on our own prime (no output-count anchors, no fabricated examples,
    mandatory uncertainty-handling).

---

## VERIFY — codebase integrity (after the deltas land)
- Fresh-context audit: **zero gaps, one flow, no half-baked/simple-path stubs, no dead code**, spec fully
  wired. Static gate green: **ruff · mypy --strict · bandit · naming · contracts-closed**.
- Every `[AUDIT]` item confirmed present-and-correct or finished; every `[NEW]`/`[CHANGE]` wired end-to-end.

## TEST — live end-to-end replay (real money: Anthropic + E2B + AssemblyAI + Cartesia)
- **Harness:** replay an exact meeting transcript through the **real in-meeting path** (real E2B sandbox,
  real Anthropic OAuth, real AssemblyAI + Cartesia), **capture sinks only** — everything runs **up to the
  `to_meeting` post**; the only thing skipped is the final audio hear/say. Real-time pace.
- **Measure, per wake and per meeting:** wake latency (partial→wake), model TTFT, total wake→post latency,
  TTS time-to-first-audio, cost (`cache_read`/`cache_creation`/`total_cost_usd`), `num_turns`.
- **Verify (the work, not just that it ran):** grounded `file:line` correctness; wake-vs-silent decisions;
  instant-ack fit; the thought process; the delivery-surface choice; artifact content quality (anti-slop).
- **Monitor + iterate:** a reviewer pass grades each reaction; fixes are **general** (edit the prompt/
  system, not a per-case patch); re-run the same replay. Loop until reactions are correct and latency/cost
  are within target.

## RUN PROTOCOL (overnight autonomous)
- Build Groups A→I in order; `[AUDIT]` items first (fast wins + reveal what's already done), then `[NEW]`/
  `[CHANGE]`. Commit per coherent group on the working branch (no push without ask).
- Gate green before the live test. Then the live replay loop with cost/latency capture + iterate.
- **Stop only on:** completion, a true hard blocker (missing capability/credential, a beta that fails
  validation with no fallback), or the live-spend ceiling. Leave an evidence ledger (per-item status +
  measured latency/cost) in `live-test/`.
- **Locked run params:** live-spend ceiling **~$100** · canonical replay target **Cova** (matches the
  seeded understanding) · screenshare + raise-hand **in scope, fully, simplest path** (26a/26b).
