# Proxy optimization & add-on layer — the locked v0 spec

**What this is.** The curated, final set of optimizations we apply across the whole pipeline — pre-meeting
→ warm-up → in-meeting loop → output — to get the best work at the lowest latency and cost. **Governing
rule: we keep the exact system that works today; everything here is an *add-on, a config flag, a prompt
block, or a tuning knob.* Claude still does all the work.** Anything that would add a subsystem, a router,
or a moving part to the live loop is in the CUT / v1 list at the bottom, not the build. Each item is
tagged **[add]** (pure addition/tuning, safe) or **[change]** (touches the working path — gated with a
fallback to the current behavior).

Two authorities fed this: Anthropic's official agent-SDK / context / caching guidance, and a live-doc
sweep of the SaaS we already pay for (E2B, AssemblyAI, Cartesia, Recall). The headline: **most of the win
is *tuning what we already have* — and the biggest single win is E2B pause/resume, which we're not using.**

---

## 0. The governing facts (settle these once)

- **Prompt caching is automatic on the Agent SDK** (confirmed in Gallop's identical usage + Anthropic
  docs). We already get it via the warm session + `resume`. `cache_read` = ~10% of input cost and skips
  re-encoding the prefix. Nothing to build — only things to *not break*.
- **`effort: "high"` (the default on Sonnet 5) already bakes in adaptive thinking** — deep on hard asks,
  near-zero on trivial ones. So there is **no separate thinking config**: lock `effort: high` at session
  start and hold it (`xhigh` for heavy coding, never `max`). Do not set `thinking:{adaptive}` — redundant.
- **1-hour cache TTL** is reachable via the **`ENABLE_PROMPT_CACHING_1H=1`** sandbox env var (and may
  already be the default on our OAuth/subscription auth). A 60-min meeting fits inside one hour → **quiet
  gaps no longer expire the cache, so the periodic keep-warm ping is NOT needed for v0.**
- **What breaks the cache** (never do mid-session): switch model, change effort, change thinking, edit the
  system prompt/CLAUDE.md, or change tool definitions **unless deferred**. On Sonnet 5+ **MCP tool schemas
  are deferred by default** → **adding Serena will NOT bloat or invalidate the cached prefix.**

---

## 1. Pre-meeting (once per repo) — cost, not live latency

- **[add] Bake a per-repo E2B template** (toolchain + `ast-grep` + Serena + deps baked as Docker/build
  layers) so the only per-meeting delta is the repo delta, not a full build. A template `start command`
  pre-warms processes. *(E2B templates + build-layer caching.)*
- **[change→cost, gated] Comprehension model:** the once-per-repo understanding pass currently runs on
  Opus. Try **Sonnet 5** for it — big cost cut if grounding quality holds; keep Opus as the fallback if an
  eval shows quality drop. *(v1 candidate — verify quality first.)*
- **[change→cost, gated] Incremental re-map on push** — rebuild only changed subsystems instead of the
  whole map. *(v1 candidate.)*

## 2. Warm-up (per meeting) — **the biggest latency + cost win**

- **[change] E2B pause/resume of a pre-primed sandbox — the top change.** Prime a sandbox **once** (repo
  cloned, files seeded, deps installed, **warm Claude SDK session with the cache already built**), then
  **pause at the calendar signal** and **resume in ~1s** at meeting start. Pause preserves **filesystem
  AND memory** (the warm session survives), costs **zero compute while paused** (storage only, free within
  tier, **no TTL** — hold it for hours/days), and **resets the runtime clock on resume.** This **replaces
  the per-meeting cold clone+build** — warm-up drops from tens of seconds to ~1s, idle cost to ~zero, and
  it **collapses "pre-warm the sandbox" + "prime the cache before the meeting" into one mechanism.**
  *Gate:* persistence is E2B public beta (known multi-cycle-resume issue #884) — **validate a multi-cycle
  prime→pause→resume with the session + cache intact before depending on it; fall back to the current
  cold-build path if it fails.** This respects "keep what works." *(E2B pause/resume.)*
- **[add] Prime the cache with a near-empty first request** during priming (Anthropic's explicit
  recommendation) so the big prefix pays its one-time `cache_creation` before anyone speaks — the first
  real wake is a fast `cache_read`. If pause/resume lands, this happens *inside* the primed snapshot for
  free; if not, it's the standalone fix for the first-utterance cold-start we saw live.

## 3. In-meeting — the wake path (shave ~1s off every wake)

Wake latency is the most visible latency in the product. **Wake latency need not equal turn finalization.**
- **[add] Wake on AssemblyAI *immutable partials*, not end-of-turn.** Universal-Streaming emits immutable
  words at ~300ms; today we'd wait up to `max_turn_silence` (~1280ms) for a finalized turn. Wake the agent
  the moment the immutable partial contains "proxy." ~1s saved on every wake.
- **[add] `keyterms_prompt: ["Proxy", …]`** (+$0.04/hr) — biases STT toward the wake word; exactly the
  proper-noun case it's built for. Reliable wake-word capture.
- **[add] `mode: "min_latency"`** for wake responsiveness; tune turn-silence tighter for short commands,
  looser when capturing a long spoken request (so we don't cut the human off mid-ask).
- *(Verify exact thresholds in the authenticated AssemblyAI dashboard before hard-coding.)*

## 4. In-meeting — the reasoning turn (config + prompt, zero added latency)

**Locked SDK config (set at session start, hold steady):**
- **`model: claude-sonnet-5`**, **`effort: "high"`** (adaptive thinking baked in; `xhigh` for heavy
  coding). Lock both — any mid-session change is a full cache miss.
- **`ENABLE_PROMPT_CACHING_1H=1`** in the sandbox env (1-hour TTL).
- **`maxTurns`** ~50–100 as a *soft gate* that nudges subagent delegation, not a truncation.
- **[add] `maxBudgetUsd`** per-meeting hard cost ceiling — a real spend cap I'd been missing.
- **`settingSources: ["project"]`** (loads our prime + skills; we need this — do NOT use `[]`).
- **`strictMcpConfig: true`** — deterministic tool surface, no surprise loads.
- **Streaming (`includePartialMessages`)** — progress + feeds TTS (see §5).

**Context management for a 60-min transcript (Anthropic's recommended levers):**
- **[add] Subagents isolate context** — delegate verbose work (test runs, log parsing, deep research) to
  subagents so their output never bloats the main context; each gets its own prefix cache. This is *also*
  our safe read-only fan-out. The main agent stays lean → lower cost + latency every turn.
- **[add] Compaction-preserve block in the resident CLAUDE.md** — automatic compaction is cache-preserving;
  a short "what to keep on compaction" section (current objective, files touched, decisions, test results)
  makes its summaries better so less gets re-read.
- **Keep the transcript a strict suffix** — append to the tail of `MEETING_NOTES.md`, never edit earlier
  bytes (protects the cached prefix).

**The Gallop steals — trimmed to the genuinely simple + zero-latency (six flags, a few prompt lines):**
- **[add] `abortController` wired into `query()`** — the real barge-in / stop-spending primitive; halts
  the loop instead of running to `maxTurns`. Serves *human control is absolute*.
- **[add] `getSdkEnv()` auth-key strip** — drop conflicting `CLAUDE_CODE_OAUTH_TOKEN`/`ANTHROPIC_API_KEY`/
  Vertex before spawn; cheap wrong-auth insurance (we use the OAuth token).
- **[add] stderr filter + secret redaction** (~30 lines) — surface only error lines, scrub `sk-ant-*`/
  Bearer/token; enforces *secrets never logged*.
- **[add] Prompt-hygiene constants** in the prime: "no internal monologue" + "only claim what tool
  evidence proves" + guardrails appended **last** — maps 1:1 onto talk-and-glance + grounded-or-silent,
  and cuts wasted output (a latency win).
- **[add] Coding-turn prompt lines:** "do only this step, then stop"; never mark done when a step failed;
  paste RED/GREEN evidence in the summary. (All already our laws.)
- **Cut from the earlier draft** (added complexity/latency, not worth v0): the 1-turn adaptive replan
  (adds a turn), per-ask-class `maxTurns` routing (→ one global cap), the explicit built-in tools allowlist
  (we want the full toolset), tool-output truncation / retry-clean-JSON (only if we add a custom tool).

## 5. In-meeting — the output path (time-to-first-audio)

- **[add] Delta-diff streaming → Cartesia continuations.** Yield `text.slice(lastText.length)` on each
  assistant event (reset on `msg.id`) and pipe those tokens **straight into Cartesia as they generate**
  (`continue:true` per chunk) — first audio starts on the **first clause**, not the last token.
- **[add] Lower Cartesia `max_buffer_delay_ms`** (default 3000ms) and **always end chunks on
  clause/sentence punctuation** (missing punctuation forces extra buffering). Snappier first-audio.
- **[add] Request raw PCM** at the meeting's sample rate to skip a decode transform before Recall.
- Sonic-3 is already our fastest realistic voice; watch the tail (P50 ~188ms, but high variance).

## 6. The tools we give Claude — each taught "use it when" (the interaction layer wires the *when*)

| Tool | Status | Use it when |
|---|---|---|
| **Serena** (LSP symbol-level code intel, MCP) | **[change] bake into template** (deferred schema → cache-safe) | *Code lookup/navigation* — jump to a symbol's def/refs instead of reading whole files; fewer tokens, exact `file:line`. The one heavier add-on; earns it on a big repo (token savings *are* the latency/cost win). |
| **ast-grep** | **[add] already baked** | *Structural search/rewrite.* |
| **Context7** | **[add] already available** | *When an answer depends on an external library's current API* — pull real docs, don't rely on memory. |
| **Design skeleton + anti-slop** | **[add] fold into `meeting-artifact`/`meeting-diagram`** | *Every artifact.* Locked HTML skeleton (Open Props tokens + house theme) + Chart.js/ECharts, Mermaid/D2, reveal.js — inline/CDN, zero runtime process. |
| **superpowers** | **[add] already installed** | *Big coding tasks* — TDD + review. |
| **Read-only sub-agent fan-out** (native `Task`) | **[add] teach it** | *Independent research/lookups + any verbose work* — concurrency with zero merge fragility, and it keeps verbose output out of the main context. |
| **promptfoo + deepeval** | **[add] our dev only** | Eval battery for Proxy — never rides in the meeting loop. |

## 7. Output quality — the AI-slop block (fixes "looks AI-generated"), live & free

A **~12-line prose block injected into the artifact-generation prompt** (zero added latency, changes the
first draft): no fake/"for example, you might…" examples; no bullet-bloat (lists where every item restates
the same thing); no changelog narration ("previously did X"); no unexplained magic numbers; and the UX
visual tells (no hover-`scale-105`, no purposeless gradients/particles, no centered-hero-three-columns, no
same-shadow-24px-emoji cards). The heavy specialist-fanout review stays **post-meeting only**. Separately,
run the **prompt-review checklist against our own prime once** (no hardcoded output-count anchors, no
fabricated examples, mandatory uncertainty-handling) — a dev-time cleanup.

## 8. Build order (v0) — nothing wired yet

1. **Caching + config lock** — `effort:high`, `ENABLE_PROMPT_CACHING_1H=1`, `maxBudgetUsd`, `maxTurns`,
   `strictMcpConfig`, `abortController`, `getSdkEnv` strip, stderr redaction, streaming. *(Sonnet 5 switch
   lands here.)*
2. **Wake path** — AssemblyAI wake-on-partial + `keyterms_prompt` + `min_latency`.
3. **Output path** — delta-diff streaming → Cartesia continuations + buffer/PCM tuning.
4. **Interaction layer live** — swap `interaction_layer_final.md` → `interaction_layer.md`; **seed the 3
   skills** (exist but not in `SEED_FILES`); fold in prompt-hygiene + the AI-slop block + the
   compaction-preserve section.
5. **Tools** — Serena into the template (deferred schema); design skeleton into the artifact skills.
6. **E2B pause/resume** — prime→pause→resume, **validated multi-cycle** with the warm session + cache
   intact, fallback to cold-build. *(Biggest win; gated on the beta check.)*
7. **Interaction-layer enablers** (separate track): screenshare as a `to_meeting` medium; raise-hand
   (green bar + chat drop + "go ahead, Proxy").

## 9. CUT / v1 backlog (real, but each adds a subsystem, a hop, or a risk — not v0)

- **Remove the audio-relay hop via Recall** (BYO AssemblyAI as a managed provider, or consume Recall
  `transcript.partial_data` over websocket) — deletes a whole service + hop, **but only if the integration
  preserves our `keyterms`/`min_latency` knobs.** For v0 keep BYOK-direct (wake-word control) and just make
  sure we consume **partials over websocket, not webhooks.** Revisit the hop removal in v1 after verifying
  the knobs.
- **Periodic keep-warm ping** — retired by the 1-hour TTL; only revive if a meeting could idle >1h.
- **mcpproxy lazy tool-connector** — the baked set covers today's asks; revisit only when the agent needs
  an un-baked tool.
- **Playwright / chrome-devtools MCP** — only for driving an *external, live* web app ("fire it up and show
  the running product"). Screenshare of *our own* content needs none of it. Add when demos become a priority.
- **Model routing to Haiku** — base stays Sonnet 5 + effort; per-turn swaps fight the cache.
- **Semantic cache / prompt compression** — stale/lossy answers violate grounded-or-silent (Law 1). Never
  on the live path.
- **Batch API** — 50% off, async — post-meeting workloads only.
- **Comprehension on Sonnet / incremental re-map** — cost wins, gated on a quality eval (§1).
- **From Gallop, explicitly skipped:** parallel-codegen worktrees + merge-queue (batch/overnight
  economics; their own red-team deferred within-task parallelism at ~1.1×); provider abstraction; Postgres
  session-store mirror; the YAML node framework; Langfuse/OTel span pipeline; the workspace-MCP-over-HTTP
  shell (bridges a network gap we don't have — we're in-sandbox with native tools).

---

### The honest v0 verdict
The core in-meeting loop is already proven live. With **§1–§8** we've covered the real latency and cost
wins — a warm ~1s sandbox resume, a ~1s-faster wake, first-audio on the first clause, a locked cache-safe
config, bounded context and spend, better tools, and designed-not-slop artifacts — **without adding a
single new subsystem to the working path.** Everything genuinely heavier is in §9. This is the point of
diminishing returns for pre-optimization; the next signal should come from a **real customer meeting**,
not more planning.
