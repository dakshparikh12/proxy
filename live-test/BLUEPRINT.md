# Proxy — definitive whole-stack blueprint

The single source of truth for the restructure. Supersedes PLAN.md / RESTRUCTURE.md /
EXECUTION_SPEC.md (kept only as history). Built from the full-stack audit: reactive flow,
dead code, DB/migrations, infra/deploy/GCS, API/Recall/`to_meeting`. Bar: **one path, no dead
code or wiring, every file has a purpose, Proxy always speaks, and the infra/API/DB/GCS are
all correct for our use case.** Simplify + optimize *in place* (keep the uv members; no
repackage). After this: only interaction layer / prompt / capability / latency remain.

## The system
One agent per meeting. Pre-meeting: connect→clone→understanding→store (Postgres map + GCS
artifacts). Prep: seed the sandbox (resident CLAUDE.md = prime + **interaction layer** +
understanding; MCP; roster) — boot-verified — and warm the session before join. In-meeting:
transcript flows into context → wake gate → per-wake prompt+ask → warm `session_host` turn
(agent decides everything from the resident interaction layer) → **speaking = streamed prose**;
`to_meeting` = the non-spoken channels (chat/dm/screen/offer/mute) through the host relay →
transport. Background heavy work → returns as a wake (later phase). One ingress (Recall), one
egress (prose-stream + relay), one human-approval loop.

---

## THE #1 NEW FINDING — the `to_meeting` two-contract bug (a speaking root cause)
There are **two contradictory contracts for how Proxy talks, both in the agent's context:**
- **LIVE (Design B, correct):** speaking = the agent's prose, streamed sentence-by-sentence to
  Cartesia; `to_meeting` is ONLY for non-spoken channels. The real MCP tool says so
  (`sandbox_meeting_mcp.py:65-77`, "To SPEAK ALOUD do NOT use this tool").
- **STALE (Design A, dead but still in context):** `TO_MEETING_TOOL` (`meeting_connection.py:207-232`)
  declares `say` as the default and describes `to_meeting` as *the way to speak*; the prime text
  (`workroom.py:35,378`) lists `speak` as a medium; defaults disagree (`chat` vs `say`).
The model gets **mixed instructions about the one thing it does most.** → **Commit to Design B
everywhere:** delete `TO_MEETING_TOOL`; mediums = `chat|dm|screen|offer|mute|unmute`; one
canonical medium vocabulary; default `chat`; keep `_route` unknown→say only as a documented
safety net; fix the prime drift. (One dynamic tool + sandbox→relay indirection = correct, keep
— it enforces the Law-3 credential boundary; the latency-critical voice path is the prose
stream.)

---

## Target state, area by area (KEEP · UPDATE · DELETE · ADD)

### A. In-meeting reactive spine (the product) — mostly KEEP + the real fixes
KEEP: wake gate (`meeting_session.on_line`), `run_ask`/warm `session_host` turn,
`meeting_connection` egress, `speak.py`/`tts.py`/`output_media.py` audio path (solid),
barge-in cut, follow-up window, the relay (honest, auth-gated).
- **UPDATE/FIX:** the `to_meeting` two-contract bug (above). Surface delivery failures on the
  speak path — a swallowed relay POST currently reports "delivered" (`session_host._deliver_say`
  → `relay_error` skipped by `_parse_intents:201`); make failure an honest degrade. Normalize
  the `to_meeting` default medium. `[SILENT]` latch = **already correct — do not touch** (no repro).
- **ADD (later phase, not this restructure):** background-work→wake; latency (transcript-write
  + shell-exec off the critical path; effort tuning).

### B. Prep / seeding — the #1 correctness ADD
- **ADD:** one explicit, **boot-verified** `SEED_FILES` list writing CLAUDE.md · REPO_MAP ·
  MEETING_NOTES · MCP+`.mcp.json` · session_host · **`INTERACTION_LAYER.md`** (never seeded
  today — the dangling `@import` at `workroom.py:168-173,199` vs `906-928`) · **`MEETING_INFO.md`**
  roster · **skills pack** (or delete `SKILL_NAMES`/`SKILLS_DIR` if the layer won't use skills).
  Any file named in CLAUDE.md that's missing/unwritten **halts prep loudly.**

### C. API routes / Recall — UPDATE + DELETE dead
- KEEP: `/webhooks/recall` (ingress), `/meetings`, `/m/{id}` + accept/reject, `/connect/*`,
  `/output-media/*`, `/relay`, `/auth/*`, `/health`, `/readiness`. Recall bot-create + STT +
  webhook HMAC + transcript unwrap + consent gate = correct.
- **DELETE (dead):** `gateway_route.py` + the `/ws` mount + the `channel_action`/`dispatch`
  funnel (vestigial chat protocol — no client connects); `_stamp_internal_scoped` (no
  `/internal/*` routes exist); the `LivePipelineRegistry`/pipeline-handler branch in
  `github_webhook.py` (registry never populated → dead).
- **UPDATE:** collapse `/webhooks/github` to verify→`_maybe_refresh_map`; fold the 3 duplicated
  internal-bearer checks (`relay`/`admin`/`dev_smoke`) into one `libs/http` helper; mount
  `/admin/.../offboard` explicitly in `app.py` (not as a side effect of connect routes); merge
  the 7 `install_*` route files into one mount table.
- **FIX (roster/DM):** `MEETING_INFO.md` participants is almost always empty on live paths →
  `dm` by id has no id source. Capture the roster (participant-events subscription or
  `GET /bot/{id}`) and refresh on join/leave, or accept address-by-name and drop `dm`.
  (Optional: latecomer consent re-post — wired handler `join.on_participant_join` is never called.)

### D. DB / migrations / schema — DELETE dead + one tightening
- KEEP (10 live tables): `tenants, users, repos, meetings, sessions, operation_runs,
  staged_drafts, webhook_events, connect_readiness, repo_maps`; repos
  `connect/drafts/meetings/identity/sessions/webhooks`; `map_store`; `objectstore` (GCS).
- **DELETE (scribe/chat pivot dead):** `repos/{notes,cost,transcript}.py` **+ `repositories.py`
  entirely** (the `Repos` facade + 5 `*Repository` wrappers, zero callers) + the `Database.repos`
  property; each with its `repos/__init__.py` + `repositories.py` edit in the same commit.
  Dead tables via a **new forward migration `0011`** (never edit shipped ones): drop
  `transcript_segments`, `meeting_cost`, `meeting_cost_telemetry`; drop `note_deltas` + remove
  its write-only writer `accept._apply_notes_edit` (no reader remains). Stale "scribe" docstrings.
- **FIX (defense-in-depth):** add `tenant_id` to the id-only durable reads (`accept.py` drafts,
  `drafts.list_drafts_for_meeting`, `meetings.get_by_id/get_by_bot_id`) — safe today only via
  route-authz; enforce the P0 isolation at the substrate too.

### E. Infra / deploy / config / GCS — UPDATE in place (estate is the right shape)
KEEP: the Terraform estate (one Cloud Run + one Cloud SQL PG15 + one versioned GCS bucket +
E2B + Secret Manager, least-privilege IAM), the Dockerfile (migrate-then-serve),
`config/defaults.toml` (exemplary, no dead entries), `settings.py` boot gate.
- **🔴 FIX (blocking): Cloud SQL network path** — `cloudsql.tf:25 ipv4_enabled=false` with no
  private-IP/VPC connector → DB unreachable at boot. Set `ipv4_enabled=true` (or add a VPC
  connector); verify on a real apply.
- **🟠 FIX: wire the E2B `DEFAULT_TEMPLATE`** (`workroom.py:147` = None) → today every meeting
  builds a base sandbox + installs the toolchain at join (slow / OOM). Feed the baked template id.
- **🟠 FIX: GCS offboard is a silent no-op** — `app.state.gcs` never assigned, no `delete_prefix`;
  and `objectstore` keys are flat `objects/<sha>` (no tenant prefix) → not deletable/namespaced.
  Add the small GCS adapter (via the `gcs_bucket` seam), assign it at startup, namespace keys
  under `tenants/<tenant>/`. Confirm draft durability is written host-side (sandbox holds no GCS creds).
- **UPDATE (housekeeping, "no dead config"):** delete `verify.yml` (runs a missing script) or
  repoint it; fix `CLAUDE.md:55` signoff path (`scripts/signoff.sh`, not `build/gates/`); delete
  the ~11 stale `.env.example` keys (Nango, 8 `PROXY_MODEL_*`, GitHub-App client pair); add
  E2B/Cartesia/AssemblyAI to the fail-fast boot gate; optionally a GCS Terraform backend.
- **DECIDE:** GitHub-App clone-token secrets are read by code but not provisioned — keep
  public-only (delete the surface) or wire the secret+binding for private repos.
- **CLARIFY the substrate wording:** the map is Postgres-only *by design* (it's text); GCS is
  for artifact/draft bodies. Update CLAUDE.md/SPEC to say "Postgres for the map, GCS
  object-versioned for artifacts" (or, if versioned-understanding history is wanted, mirror the
  map body to GCS too — optional).

---

## Ordered execution (in place, gate green after each; checkpoint-commit per group)
- **T0** Checkpoint commit; confirm `scripts/signoff.sh` green baseline; confirm branch stable.
- **T1 — Delete dead code (+ required `__init__`/aggregator edits, same commit):** Group-A files;
  `repos/{notes,cost,transcript}.py` + `repositories.py` + `Database.repos`; `gateway_route.py`
  + `/ws` mount + `dispatch`/`gateway`/`registry`/`handlers/channel_action`; `_stamp_internal_scoped`;
  `TO_MEETING_TOOL`; the `github_webhook` pipeline-registry branch; `ops/{sandbox_provider,sandbox}`;
  `.pyc`; `proof/` excludes. Verify: `import db.repos, agentkit`, boot, gate green.
- **T2 — Delete the `channel_action` contracts (surgical):** `contracts/{channel,registry,
  contract_reads}.py` + the closure gate `ops/check_field_contract.py`; **keep `contracts/{chunks,
  readiness}.py`** (live). Update `CLAUDE.md` (Contracts hard rule) + `AGENTS.md` — founder-ratified.
- **T3 — New migration `0011`** dropping the 4 dead tables; remove the vestigial notes writer.
- **T4 — ADD the boot-verified `SEED_FILES`** (interaction layer + roster + skills-or-delete). *The unlock.*
- **T5 — FIX the `to_meeting` contract** → Design B everywhere (delete the dead twin, one medium
  vocabulary, consistent default, prime-drift fix) + surface speak-path delivery failures as honest degrade.
- **T6 — Infra fixes:** Cloud SQL network path (blocking) · E2B `DEFAULT_TEMPLATE` · GCS offboard
  adapter + tenant namespacing · `.env.example`/CI/CLAUDE.md housekeeping.
- **T7 — Merge in place:** the 7 route installers → one mount table; the 3 bearer checks → one
  helper; explicit offboard mount; collapse genuine loop duplication (no cross-member moves).
- **T8 — DB defense-in-depth:** `tenant_id` on the id-only durable reads.
- **T9 — Park to later phase:** background→wake, latency tuning, optional GCS map-mirror.
- **T10 — Founder gates:** `github_webhook`/`refresh` keep-mount-and-stub; GitHub-App private-repo
  scope (in or out); the substrate-wording doc update.

## End-state guarantees
Proxy **always speaks or we know it didn't** (one prose-stream voice path + honest delivery
failures + the `to_meeting` contract unified so the model isn't given two ways to speak). **One
path** — one ingress, one egress, one approval loop, no `/ws`/dispatch/pipeline-registry.
**Interaction layer actually drives the agent** — seeded + boot-verified. **No dead code/config**
across code, routes, DB, and infra. **Deploys and connects** — Cloud SQL reachable, E2B template
warm, GCS offboard real, config matching settings 1:1. Only nuance / latency / output quality
remain.
