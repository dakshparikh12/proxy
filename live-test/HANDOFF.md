# Proxy v0 optimization build — handoff (2026-08-08, branch `proxy-build`)

**For the co-founder continuing the live testing + latency/cost proof.** This is everything changed/added
in the optimization + interaction-layer + core-features build, the verification state, and exactly how to
run the live e2e that remains.

## TL;DR
The full **latency/cost + interaction-layer + screenshare/raise-hand** build is **DONE, gate-green, and
fresh-context-audited (zero functional gaps, all chains wired end-to-end, honest)**. What remains is the
**live e2e** — a faithful Cova transcript replay on real infra to prove latency + cost and settle two
live-only checks. Nothing is fake or half-baked; where a vendor didn't support something, it's documented,
not stubbed.

Two source-of-truth docs alongside this one:
- `live-test/V0_BUILD_PLAN.md` — the numbered build ledger (what/why/validate per item).
- `live-test/OPTIMIZATION_ADDONS_SPEC.md` — the rationale (what we adopted vs. **cut**, and why).

---

## What changed (by area, with pointers)

### Caching / config (the latency + cost core) — `session_host.py`, `workroom.py`
- **Model → Sonnet 5** (`session_host.py:49`). Effort=high + adaptive thinking already correct and FIXED
  (never mutated per-turn → cache stays warm).
- **1-hour prompt-cache TTL** via `ENABLE_PROMPT_CACHING_1H=1` in the sandbox env (`workroom._wake_envs`).
  Retires the keep-warm ping (a meeting fits one TTL).
- **`strict_mcp_config=True`** + **`max_budget_usd`** ($20 per-meeting runaway backstop, env
  `PROXY_MAX_BUDGET_USD`) in `ClaudeAgentOptions`.
- **prime-during-prep** (`_prime_cache`, `session_host.py:723`): one silent `[SILENT]` warm-up before the
  host marks ready, so the first real wake is a fast `cache_read`. Relay stripped for the duration (can't
  touch the room). Safe within the 90s readiness budget; disable with `PROXY_PRIME_CACHE=0`.

### Interaction layer + prime — `interaction_layer.md`, `prime.py`
- Swapped in the finalized layer. **Raise-hand policy baked exactly as specified**: raise hand on ANY
  commotion, speak only when the room is genuinely open, chat is supplementary-only (§3, §6).
- Show-the-work delivery menu (§5), subagent context-isolation (§8), Cova hardcoding removed.
- `prime.py`: added `raise_hand` to the medium list + a compaction-preserve block (keeps the live thread
  when a long meeting's context is condensed).

### Skills (seeded + real content) — `workroom.py`, `skills/*/SKILL.md`
- **The seeding gap is fixed** (`_packaged_skill_source` + `_seed_files`) — the 3 skills now seed to
  `.claude/skills/<name>/SKILL.md`; the interaction layer's references resolve.
- `meeting-artifact`: a locked house design skeleton (tokens + theme) + an **anti-slop checklist** (kills
  the "AI-generated" look). `meeting-diagram`: Mermaid + house theme. `background-job`: RED/GREEN coding
  discipline (do-only-this-step, never-done-on-failure).

### Raise-hand (CORE) — fully wired end-to-end
`output_media.py` green-bar overlay + `set_raised_hand` → `meeting_connection.py` `ADVERTISED_MEDIA(+raise_hand)`
/ `RaiseHandSink` / routing (shows bar + chat nudge) / **auto-clear on speak** → `provisioner.py` `_raise_hand`
sink (now a 4-tuple) → passed to `MeetingConnection`. The agent decides WHEN (interaction layer), no
hard-coded situation→action.

### Screenshare (CORE) — real, wired — `recall.py`
- **Recall's OpenAPI documents `output_media.screenshare` (webpage)** — the twin of `camera`. Selected via
  **`RECALL_OUTPUT_MEDIA_SURFACE`** env: `camera` (default) or `screenshare` (prominent shared-screen of our
  live HTML). One env flip. See the schema citation at `recall.py:95-112`.
- **Live-check pending:** confirm audio still rides the screenshare surface (the voice channel shares that
  page) before defaulting to it.
- Out of scope: screensharing external **authed** content (e.g. a live company Google Doc) — needs a real
  browser holding the company's login (credential boundary). Separate, larger feature.

### Tools — Serena, ast-grep, Context7 — `session_host.py`, `e2b.Dockerfile`
- **Serena** (symbol-level code intel) wired as a **deferred** (cache-safe) MCP, **gated on real
  availability** (`PROXY_SERENA_CMD` / `PROXY_SERENA=1` / `shutil.which`). Skipped silently if absent.
- **`e2b.Dockerfile` + `e2b.toml`**: per-repo template baking node + Claude Code + pinned mcp/SDK + Serena
  (`uv tool install serena-agent`) + ast-grep + the repo. **Not yet built** (`e2b template build` is a
  deploy step). `DEFAULT_TEMPLATE=None` keeps the working path.
- Context7 already pre-wired (key-gated).

### STT / TTS latency — `recall.py`, `tts.py`, `libs/http/ws.py`
- **AssemblyAI**: `keyterms_prompt:["Proxy"]` + `min_latency` + immutable partials in the bot transcription
  config (faster finals). (Waking on incomplete partials was intentionally NOT done — it would answer half
  a question; the tightened end-of-turn is the safe win.)
- **Cartesia continuations**: new **WebSocket seam** in `libs/http/ws.py` (`call_external_ws`, the single
  external-call seam) → `tts._stream_ws` streams clauses (`continue:true/false`, raw pcm_s16le) with an
  **honest REST fallback**. `websockets>=13,<18` added to `libs/http/pyproject.toml`.
- **v1 follow-up (flagged):** `speak.py` still buffers to sentence boundaries, so cross-sentence clause
  streaming isn't wired yet — first-audio is per-sentence today, per-clause once `speak.py` pushes clauses.

### E2B pause/resume (biggest warm-up win, gated) — `workroom.py`
- Prime-once → pause at calendar signal → resume ~1s. Behind `PROXY_ENABLE_PAUSE_RESUME` (default **OFF**,
  cold-build fallback). Uses `beta_pause()` + `AsyncSandbox.connect`. **Needs live multi-cycle validation**
  (E2B beta, issue #884) before enabling.

---

## Verification state
- **Full gate GREEN**: ruff · mypy --strict (107 files) · bandit · naming lint · contracts-registry-closed
  · unit+offline suite — all pass (the 2 sealed-contract tests were updated for the `raise_hand` medium).
- **Fresh-context integrity audit**: all 8 chains traced end-to-end = zero functional gaps, one flow, honest.
- **NOT yet done**: real-inference / live validation (the e2e below). Everything above is code-complete +
  statically proven; the e2e is the "prove on real data" step.

---

## The live e2e — how to run it (the remaining work)

### Infra prerequisites — ACCOUNT NOTE (important)
- Use the **proxy-meeting** GCP account, NOT gallopintelligence. Verify BOTH:
  - `gcloud config get-value account` → `proxy.meeting@gmail.com`
  - **ADC too**: `gcloud auth application-default login` as the proxy-meeting account (the cloud-sql-proxy
    uses ADC; the earlier failure was ADC resolving to the wrong account / a stopped instance).
- **The Cloud SQL instance is currently STOPPED.** Start it:
  `gcloud sql instances patch proxy-dev-pg --activation-policy=ALWAYS --project proxy-meeting-dev`
  (and **STOP it after** — `--activation-policy=NEVER` — to save cost).
- Start the proxy: `cloud-sql-proxy --unix-socket /tmp/csql proxy-meeting-dev:us-central1:proxy-dev-pg`
  then point `DATABASE_URL`'s `host=` at `/tmp/csql/proxy-meeting-dev:us-central1:proxy-dev-pg` (or create
  `/cloudsql` and use the socket path the current `DATABASE_URL` already expects).

### Boot + replay
- All API keys are present in `.env` (Anthropic OAuth, E2B, Cartesia, AssemblyAI, Recall, GCS, AES×3). Add:
  `PROXY_INTERNAL_TOKEN=<random>` and `PUBLIC_BASE_URL` (placeholder `http://localhost:8099` for capture mode).
- Boot: `PROXY_ENV=local .venv/bin/python -m control_plane.server` (fail-fast config gate names any missing key).
- **Capture / FILE-mode replay (recommended — no tunnel, no real meeting):** provision the **Cova** repo,
  feed `live-test/MEETING_TRANSCRIPT.md` at real-time pace, capture every `to_meeting` intent up to the post.
  Watch with `live-test/watch_live.py <meeting_id>` + the `PROXY_WAKE_OUT` mirror. (Confirm the DB behind
  the proxy has the **seeded Cova understanding** so provisioning returns `indexed:true`.)
- **Real-meeting path (adds the audio leg):** run a tunnel (ngrok), set `RECALL_WEBHOOK_SECRET` + the 3
  tunnel URLs, `POST /admin/test-provision {meeting_url, repo}` → a real Recall bot; play/speak the
  transcript. See `live-test/LIVE-RUNBOOK.md` for the full bring-up.

### What to measure / verify (the latency + cost proof)
- Per wake: **wake latency, model TTFT, first-audio (Cartesia TTFA), cost** (`cache_read` / `cache_creation`
  / `total_cost_usd`), `num_turns`. (The `[usage]` line in the sandbox host log prints the cache split.)
- Behavior (stop-and-inspect each wake): wake-vs-silent decisions, instant acks, **raise-hand usage** in a
  busy room, tool use (Serena/sub-agents), delivery-surface choice, artifact quality (anti-slop).
- **The two live-only checks:** does **audio ride the screenshare surface** (before defaulting
  `RECALL_OUTPUT_MEDIA_SURFACE=screenshare`); real **Cartesia TTFA** (and tune `CARTESIA_WS_BUFFER_MS`).

### Toggles worth trying
- `RECALL_OUTPUT_MEDIA_SURFACE=screenshare` (prominent screenshare) · `PROXY_SERENA=1` (if the baked
  template has Serena) · `PROXY_ENABLE_PAUSE_RESUME=1` (only after multi-cycle validation) ·
  `PROXY_MAX_BUDGET_USD` (per-meeting cost cap).

---

## Known-safe deferrals (documented, not gaps)
- Real screenshare of external **authed** content (browser + company login) — separate feature.
- Cartesia per-**clause** streaming across sentences — needs a `speak.py` clause-boundary push (v1).
- E2B pause/resume default-on — after live multi-cycle validation.
- The `e2b template build` itself (a deploy step).
