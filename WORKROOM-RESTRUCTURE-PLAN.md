# Workroom Restructure — full scope

**Target.** The in-meeting product becomes: a per-meeting **WORKROOM** = an E2B sandbox with the
repo cloned in + the live transcript written in as a file + **native Claude Code** running in it
(subscription auth via `CLAUDE_CODE_OAUTH_TOKEN`) doing ALL planning + execution with its full
native tools. A thin **MEETING BRIDGE** on the trusted host connects it to the live meeting. This
**replaces** the custom in-meeting MCP-tool engine. Proven: native Claude in an E2B cal.com clone
did coding (grounded, ran+verified, clarified), UI mockup (real design tokens), web research
(WebSearch/WebFetch), and sub-agents — all on subscription.

---

## A. BUILD

### A1. The workroom (native Claude in the sandbox)
1. **[NET-NEW] Bake the E2B template `proxy-workroom`** (`e2b.toml` + Dockerfile — named in `sandbox_provider` but never built). Base + Node + `@anthropic-ai/claude-code` (native `claude`) + `ast-grep` + common language deps + a **bigger machine size** (fixes the OOM). Latency win.
2. **[EXTEND] `libs/ops/sandbox_provider.provision_async`** — point at `proxy-workroom`, add `CLAUDE_CODE_OAUTH_TOKEN` to the curated-env allowlist (from Secret Manager), keep egress-deny + per-sandbox secret.
3. **[EXTEND] `premeeting/cloner.py`** — add a `--depth 1` shallow-clone variant (~6s on cal.com).
4. **[NET-NEW] Seed the sandbox** at provision: clone the repo in (or bake a base layer), write `index.md` (map, from `map_store`), `CLAUDE.md` (the prime), and the live transcript file.
5. **[NET-NEW] `_assemble_workroom`** — replaces `_assemble_engine` (`provisioner.py:380/491`): provision (baked template + OAuth env) → seed → ready native claude → build the bridge.
6. **[NET-NEW] Wake = launch native `claude`** inside the sandbox (cwd = repo) with the transcript file + the ask, streaming output back over the E2B command channel. Re-map resume/abort/progress to the CLI session (`--resume` / session id), persisted in `operation_runs.progress`.
7. **[REUSE] keep-warm heartbeat + ordered teardown** (`close.py`, `_teardown_engine`), pause/resume between asks (E2B pause = cost lever), the `operation_runs` atomic claim.
8. **[REUSE] No-creds world-touching gate** — egress-deny + read-only clone token + `gitio` push-refusal + no GCS/Postgres creds in-sandbox → world-touching = the agent emits a **draft** → host stages it (`workroom.drafts.propose_change`) behind a human click.

### A2. The meeting bridge (thin edge — 6 seams, adapted from `in_meeting`)
1. **[ADAPT] trigger** — port `in_meeting/trigger.py` + `disambiguator.py`: decide *when* to wake native claude (idle stays free).
2. **[NET-NEW/ADAPT] transcript-in** — append every final line to the sandbox transcript file (from the webhook feed + `notes.py` render).
3. **[ADAPT] speak-out** — `in_meeting/speak.py` SpeakPipe → Cartesia (`transport/tts.py`) → `output_media`; take the agent's spoken output → synth → room.
4. **[ADAPT] meeting-action-out** — mute/chat/DM (`meeting_control.py` verbs) exposed to the sandbox agent via a callback the bridge executes (Recall creds stay host-side).
5. **[ADAPT] barge-in** — `_cut_speech_on_human_voice` (`webhooks.py`) → `SpeakPipe.cut()`. Re-home the cut logic (currently in dead `transport/turn.py`).
6. **[ADAPT] present-back / draft-gate** — the agent's draft → `workroom.drafts.propose_change` → approve URL (`accept_route`).
7. **[NET-NEW] The bridge protocol** — how the sandboxed agent calls OUT (speak / action / draft): designated files the bridge polls, or a small tool endpoint. The key net-new integration piece.

### A3. Rewire the boot chain (control-plane)
- `webhooks` `launch` → provision the **workroom** (not the engine).
- transcript feed → `bridge.feed_transcript` (append to sandbox file + trigger) + barge-in cut first.
- `provisioner._assemble_engine` → `_assemble_workroom`; teardown → drain bridge + kill sandbox.
- **KEEP unchanged:** the atomic claim, `SignalCarrier` + Scribe notes-plane, consent, meeting-end, the `output_media` router, HMAC webhook intake.

---

## B. DELETE

### B1. DELETE-NOW — dead islands off the live path (~9,900 LOC, ~55 files)
- **control-plane old-brain:** `orchestrator, wake, wake_turn, dispatch, direct_answer, heartbeat, screen_modes, accept_facade, provider, behaviors/, budget, emit, recovery` (+ edit `__init__` re-exports).
- **code_intel dead-answer island:** `direct, direct_answer, readiness_app, sandbox, verifier` + the top-level `__init__`/`crypto` shims.
- **transport M-suite:** `canvas, delivery, speak, turn, boundary, projector, cost, limiter, outbound, surface, resolution, fakes`.
- **scribe/libs stragglers:** `scribe/corrections, scribe/quality_gate`; `agentkit/{tools, tracing, wake_cache, execution}`; `llm/{client, prompts}`; `ops/{redaction, telemetry}`.
- **top-level:** `spike/`, `archive/` (untracked), `deploy/code_intel/Dockerfile` (dead entrypoint).
- **KEEP correction:** `ops/secrets.py` is the Secret-Manager guard — KEEP.
- **Sealed-test re-seal deps:** doc00 (many), doc01 (code_intel direct-answer), doc02 (turn/boundary), doc03 (corr/qgate). Non-sealed test dirs deleted outright: doc04, doc05, doc08 pins, `tests/code_intel/`, top-level `test_m*`.

### B2. DELETE-AFTER-WORKROOM — live today, superseded once the workroom serves a meeting e2e (~12,000 LOC)
- **custom in-meeting engine:** `in_meeting/{engine, provider, prompt, context, notes, trigger, disambiguator, meeting_control, map_loader, runtime, sandbox, drafts_access, settings, __init__}` — **but the logic of trigger/prompt/speak/meeting_control/drafts_access is first ADAPTED into the bridge, then the originals deleted.**
- **transport custom-engine plumbing:** `carrier, events, hearing, wire, seams, media, external, config, failure, signals` (split `signals` if the bridge reuses its dataclasses).
- **code_intel graph/LSP indexer:** `pipeline, mcp_server, sdk_server, warm_resolver, graph*, cloner, coverage, exclusions, gitio, langs, meeting, orm, paths, readiness, results, webhook_handler, crypto` (premeeting already duplicates clone/paths/etc.).
- **BRIDGE-KEEP (do NOT delete):** `in_meeting/output_media.py`, `in_meeting/speak.py`, `transport/{recall, tts, stt, join, consent}`.

---

## C. KEEP (the new-system foundation, ~28,000 LOC)
- **premeeting/** (clone/index/map + read-only tokens + push-refusal + secret-exclusion).
- **workroom/** reusable: `drafts, objectstore, recovery, envelope` (+ `agent_config`/`session`/`big_build` as reference for prompt text + durability patterns).
- **control-plane spine:** app/server/webhooks/webhook_routes/github_webhook/provisioner/meeting_runtime/close/accept/accept_route/authz/connect/meetings_route/settings/scribe_runtime/code_intel_mount (adapted).
- **vendor edges / bridge:** `transport/{recall, tts, stt, join, consent}`, `in_meeting/{output_media, speak}`.
- **scribe live notes** (all except corrections/quality_gate).
- **libs live:** `http/*`, `db/*`, `contracts/*`, `ops/*` (except redaction/telemetry), `llm/routing`, `agentkit/{provider, config, deltas, resume, guardrails, abort}`.
- **harness/eval/tests infra, apps/connect + apps/tile (orb), deploy/control_plane, infra, migrations.**

---

## D. EXECUTION ORDER (the process)

- **Phase 0 — Prep (founder gates):** approve lifting the PreToolUse guard for sealed-test edits; confirm prod auth = `claude setup-token` (long-lived) vs the current access-token; pick the template machine size (fix OOM); confirm re-seal authority for doc00/01/02/03.
- **Phase 1 — Workroom foundation:** bake `proxy-workroom`; extend `sandbox_provider` (OAuth env + template) + `cloner` (shallow); build `_assemble_workroom` + native-claude launch + keep-warm/teardown. Gate: a workroom provisions and native claude does a task wired through the provisioner.
- **Phase 2 — The bridge:** the 6 seams + the bridge protocol; rewire webhooks/provisioner to workroom+bridge.
- **Phase 3 — Prove end-to-end:** a real (or harness-driven) meeting served fully by workroom+bridge — trigger → wake → work in sandbox → speak back → barge-in → draft-gate. Verify against the acceptance criteria.
- **Phase 4 — DELETE-AFTER-WORKROOM (B2):** once e2e holds, delete the old engine + code_intel indexer + their tests.
- **Phase 5 — DELETE-NOW (B1) + re-seal:** delete the dead islands; re-seal doc00/01/02/03; delete the non-sealed test dirs.
- **Phase 6 — Verify + tune:** signoff (ruff/mypy --strict/bandit/naming/contracts); run the eval harness against the new system; tune the meeting-nuance judgment.

## Totals
| | files | ~LOC |
|---|---|---|
| DELETE-NOW | ~55 | ~9,900 |
| DELETE-AFTER-WORKROOM | ~44 | ~12,000 |
| KEEP | ~180 | ~28,000 |
| BUILD net-new | — | template + `_assemble_workroom` + launch + bridge protocol + transcript-file + 6 adapted seams |

## Founder gates
Re-seal doc00/01/02/03 · lift PreToolUse guard for test/acceptance edits · template machine size (OOM) · `claude setup-token` for durable prod auth · D-032 map-credit (optional, quality only).
