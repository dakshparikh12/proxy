# Chain summary — the whole-product build plan (HALT 1 review)

**127 nodes** across 8 docs. Disposition: run each node's real test on real data → green+reviewer-OK = done, else make it green. `verify`=keep existing code if it re-proves; `fix`=targeted defect; `rebuild`=rip+rebuild the wrong code; `build-new`=greenfield.


## doc00 FOUNDATION — 18 nodes  (verify:16, fix:1, build-new:1)
- ✅ verify · `foundation.contracts-registry` — Typed contracts + import-time closed registry
- ✅ verify · `foundation.agentchunk-behavior` — AgentChunk union + stream_deltas + BehaviorRunner + never-throw tools
- ✅ verify · `foundation.repo-structure` — uv-workspace monorepo skeleton (src-layout, three deployables shape)
- ✅ verify · `foundation.substrate-schema` — The broker-free durable substrate — schema (operation_runs, cost, drafts, identity, transcript)
- ✅ verify · `foundation.substrate-runtime` — The durable substrate — runtime (with_operation_run, fencing, atomic claim, reconcile, sandbox lifecycle)
- ✅ verify · `foundation.hosting-deployables` — The three deployables + Cloud SQL / GCS substrate topology
- ✅ verify · `foundation.config-secrets` — Config contract, model-seat routing, Secret Manager wiring, credential homes
- ✅ verify · `foundation.db-layer` — asyncpg pool + Database facade/repos (no ORM) + Alembic revision DAG
- ✅ verify · `foundation.tenant-crypto-isolation` — Per-tenant envelope-key crypto isolation + crypto-shred offboarding
- ✅ verify · `foundation.boot-lifecycle` — Ordered fail-fast boot lifespan + provisioner_ready + EPIPE tolerance + parallel shutdown
- ✅ verify · `foundation.iac` — Terraform IaC — modules/envs, promote discipline, prevent_destroy, least-privilege
- ✅ verify · `foundation.docker-migrate` — Multi-stage uv Dockerfiles, non-root+HOME, self-migrate with advisory-lock retry
- ✅ verify · `foundation.seam-external-dispatch` — The call_external seam + the dispatch funnel + internal API surface
- ✅ verify · `foundation.observability-floor` — Observability + operational floor — logs, Sentry, cost telemetry, Langfuse scaffold, hardening, affinity
- ✅ verify · `foundation.invariants-guards` — Consolidated invariants + constitution — cost meters, lethal-trifecta, safety floor, naming lint
- ✅ verify · `foundation.ci-guards` — CI/CD guards — fast-blocking split, migration/secret/triad/banned/field guards, pre-commit parity
- 🔧 fix · `foundation.webhook-events-schema-fix` — FIX: webhook_events schema drift — add provider/sha/received_at + status CHECK · ⚠️HUMAN-GATED
- 🏗️ build-new · `foundation.call-external-guard` — BUILD-NEW: AST guard forbidding raw vendor clients outside libs/http

## doc01 CODE-INTELLIGENCE — 11 nodes  (verify:10, fix:1)
- ✅ verify · `codeintel.connect` — Connect — GitHub App + Nango + RepoProvider seam
- ✅ verify · `codeintel.clone` — Clone — per-tenant encrypted volume, blobless, no-exec
- ✅ verify · `codeintel.exclusions` — Exclusions — gitleaks + policy globs + read-path redaction
- ✅ verify · `codeintel.substrate_graph` — Structural substrate + dependency/call graph (mechanical, no LLM)
- ✅ verify · `codeintel.mcp_tools` — The code_intel MCP server — 8 host-side tools (factory-per-query)
- ✅ verify · `codeintel.direct_answer` — Direct-answer wake turn — host-side answer API (no E2B)
- ✅ verify · `codeintel.coverage_readiness` — Coverage read + Readiness gate — emits contracts.Readiness
- ✅ verify · `codeintel.freshness` — Freshness — push webhook, full rebuild, reconcile, SHA-pin + GC · ⚠️HUMAN-GATED
- ✅ verify · `codeintel.precise_nav` — Precise navigation — warm host-side LSP (Serena/solid-lsp) + fallback
- ✅ verify · `codeintel.pipeline_orchestration` — Pipeline orchestration + placement/static contracts + E2E estates
- 🔧 fix · `codeintel.tenant_isolation` — Tenant isolation — graph + volume + tenant-scoped cache key (FIX)

## doc02 VOICE-TRANSPORT — 10 nodes  (fix:1, verify:9)
- 🔧 fix · `transport.join-consent` — Join + consent hard-gate + fail-closed bot resolution
- ✅ verify · `transport.events-roster` — Recall webhooks → roster / meeting-end / bot-status signals (durable-first)
- ✅ verify · `transport.hear-transcript` — Hearing: per-speaker audio → speaker-attributed transcript fan-out (BYOK passthrough)
- ✅ verify · `transport.turn-barge-in` — Turn-taking: Silero-VAD barge-in + AAI end_of_turn boundary + hard-mute
- ✅ verify · `transport.speak` — Speaking: text → one calm voice + verbatim chat copy, headlines-only envelope
- ✅ verify · `transport.chat` — Chat: inbound @proxy asks + outbound broadcast/DM + per-meeting channel report
- ✅ verify · `transport.canvas` — Canvas: one tile webpage + screenshare promote/demote, drawn social signals
- ✅ verify · `transport.failure` — Failure honesty: rejoin-once + honest gap + mark-lost + voice→chat + rate limiter
- ✅ verify · `transport.seam-carrier` — Provider seams + in-process carrier + signal-surface completeness + platform matrix
- ✅ verify · `transport.cost-xcut` — Delivery verbs + cross-cutting guards (never-throw, naming, secrets, seam, SLO homing) + cost accrual

## doc03 MEETING-UNDERSTANDING — 15 nodes  (verify:13, fix:2)
- ✅ verify · `scribe.coalescer` — Coalescer — cut natural Scribe windows (turn/pause/cap, VAD-gated, chat-merged, serial-ordered)
- 🔧 fix · `scribe.chat-timestamp` — Inbound-chat temporal synthesis — ChatMessage has no `t`; the pump synthesizes ts_s (verify ordering)
- ✅ verify · `scribe.schema` — NoteDelta schema — Pydantic source of truth (firmness, provenance, reversibility, said_at_s guard)
- ✅ verify · `scribe.prefix-tool` — Scribe cached prefix + forced tool — hand-placed ×2 cache_control breakpoints, byte-stable head
- ✅ verify · `scribe.call-parse` — Scribe micro-call + parse — bare messages.create (no agent loop), tool-forced, Pydantic re-validate, typed errors
- ✅ verify · `scribe.pipeline-apply` — Serial pipeline + applier — one consumer per meeting, ~3.5s skip-never-retry, transactional apply+comprehend flip
- ✅ verify · `scribe.rolling-summary` — Rolling-summary generator — cadence-refreshed Segment B, off the hot path, byte-stable between refreshes
- ✅ verify · `scribe.quality-gate` — Sampled online quality gate — grounding/entailment on the cheap cascade, escalate-not-block, cascade-health telemetry
- ✅ verify · `scribe.referent-matcher` — Referent matcher — deterministic no-LLM lookup_referent over overview areas + graph_nodes, honest unbound
- ✅ verify · `scribe.event-emitter` — Event emitter — material-change events to Doc 04, chitchat emits nothing, closed registry, deterministic chat-record line
- ✅ verify · `scribe.corrections` — Live corrections — immediate attributed superseded patch + notes-injection gate (final/irreversible = spoken receipt)
- ✅ verify · `scribe.store` — Dual storage plane — Postgres append-only ledger (uuid, ON CONFLICT) + boot-reaping + GCS versioned artifact
- 🔧 fix · `scribe.cross-service-reader` — Cross-service notes read — GET /internal/notes/{meeting_id} folds Postgres, token-gated, host-independent, notes.finalized
- ✅ verify · `scribe.cost-telemetry` — Per-call cost telemetry — Haiku rate card, cache-split write-through to meeting_cost, cache-hit ratio proves the breakpoint
- ✅ verify · `scribe.close-pass` — Close pass — Sonnet generateStructured over folded ledger + gap/pending backfill → markdown → GCS create-only → chat link → teardown

## doc04 ORCHESTRATOR — 23 nodes  (build-new:14, verify:7, fix:2)
- 🏗️ build-new · `orchestrator.provider-seam` — Provider seam + AgentChunk normalization + [CRITICAL] tripwire
- ✅ verify · `orchestrator.stream-deltas-verify` — stream_deltas one-arg delta computer (verify)
- ✅ verify · `orchestrator.behavior-runner` — BehaviorRunner wired to the provider seam + cost/error boundary (verify+extend)
- 🏗️ build-new · `orchestrator.behaviors-dir` — Wake-behaviors as typed BehaviorConfig constants + build-time capability manifest
- ✅ verify · `orchestrator.wake-cache-verify` — 1-hour-TTL cached wake prefix + state-digest compaction (verify)
- 🏗️ build-new · `orchestrator.wake-turn` — The wake turn — one persistent SDK session, digest in, tool calls out
- 🏗️ build-new · `orchestrator.name-gate` — Address detection — mechanical name-gate + tiny disambiguator
- 🏗️ build-new · `orchestrator.ack-reflex` — The 0.5s 'on it' ack reflex + boundary/barge-in gating (code, not agent)
- 🏗️ build-new · `orchestrator.direct-answer-path` — Direct-answer path — grounded lookup answered in the wake turn via host-side code_intel
- 🏗️ build-new · `orchestrator.bundle-dispatch` — Workroom dispatch — bundle assembly (ask + notes_ref + tail) + completion-callback wake
- 🏗️ build-new · `orchestrator.run-loop` — The run loop — the asyncio event queue that is the missing spine
- 🏗️ build-new · `orchestrator.standing-pipes` — Standing pipes wiring + STT credential refresh loop (join-time plumbing)
- ✅ verify · `orchestrator.claim-fence-affinity-verify` — Atomic-claim ownership + affinity primitives (verify)
- 🔧 fix · `orchestrator.heartbeat-fence` — Heartbeat canonicalization — operation_runs fence, not Healthchecks (fix)
- 🏗️ build-new · `orchestrator.session-durability` — Two-tier session durability — session_id resume + stale-session replay
- 🔧 fix · `orchestrator.cost-circuit-breaker` — Live cost circuit-breaker — check_meeting_budget with 0.8/1.0 caps (fix) · ⚠️HUMAN-GATED
- ✅ verify · `orchestrator.abort-discipline-verify` — AbortController discipline — abort-is-final (verify)
- ✅ verify · `orchestrator.reconcile-sandbox-verify` — Reconcile sweep + ManagedResource sandbox lifecycle + Scheduler (verify)
- 🏗️ build-new · `orchestrator.accept-handler` — Accept-handler — human accepts a staged draft after the call (control_plane route)
- 🏗️ build-new · `orchestrator.ordered-close` — The ordered close — freeze → close pass → destroy sandbox → complete row → teardown last
- ✅ verify · `orchestrator.boot-lifecycle-verify` — Server boot ordering + settings gate + emit frontier + join/webhook seams (verify)
- 🏗️ build-new · `orchestrator.session-preferences` — Session-scoped preferences (shorter answers / stop posting decision notes) via the session-state digest
- 🏗️ build-new · `orchestrator.conversational-behaviors` — Conversational wake-behaviors (catch-me-up / where-are-we / dry-run / show-your-work / capability-answer)

## doc05 WORKROOM — 18 nodes  (rebuild:1, verify:3, build-new:13, fix:1)
- ♻️ rebuild · `workroom.sdk-isolation-triad` — SDK-isolation triad on every query()
- ✅ verify · `workroom.sandbox-provision-verify` — Verify per-meeting E2B sandbox provision + per-sandbox JWT secret
- 🏗️ build-new · `workroom.sandbox-sidecar` — JWT-gated MCP-over-HTTP sidecar inside E2B (:8081)
- 🏗️ build-new · `workroom.sandbox-tools` — 8 sandbox tools (7 core + ast_grep) with validate_path + atomic writes
- 🏗️ build-new · `workroom.sandbox-receipts` — Host-observed structured receipts from tool transport
- 🏗️ build-new · `workroom.disposition-prompt` — Quick-vs-deep disposition + curated per-disposition toolbelt
- 🏗️ build-new · `workroom.session-per-task` — One SDK session per task on a shared warm sandbox
- 🏗️ build-new · `workroom.envelope` — The Envelope out + tool-boundary progress events
- 🏗️ build-new · `workroom.plan-step` — Plan artifact (first turn) + plan-verify critic pass
- 🏗️ build-new · `workroom.sequential-build` — Sequential subtasks: checkpoint + git read-back + publish-or-fail
- 🏗️ build-new · `workroom.gated-replan` — Gated replan + correction-into-the-plan + no-progress detection
- 🏗️ build-new · `workroom.verify-gate` — One fresh-context verifier + deterministic receipt evidence gate + hard gate
- 🔧 fix · `workroom.propose-change` — Multi-file propose_change host-side MCP → staged_drafts
- ✅ verify · `workroom.drafts-persist-verify` — Verify drafts persist durably + human-accept after teardown
- ✅ verify · `workroom.task-durability` — Task durability via operation_runs (no workroom_tasks table)
- 🏗️ build-new · `workroom.session-resume` — Session resume + stale-session replay (imported from agentkit)
- 🏗️ build-new · `workroom.safety-wiring` — Safety: egress-deny, curated env, injection guardrails
- 🏗️ build-new · `workroom.cost-latency` — Cost & latency: preflight, per-role models, caching, honest ceilings

## doc08 EXPERIENCE — 17 nodes  (rebuild:2, build-new:11, fix:3, verify:1)
- ♻️ rebuild · `experience.contract-registry` — ProxyMessage import-time registry — render-frame + channel_action set (CANONICAL, replaces pre-canonical)
- 🏗️ build-new · `experience.pydantic-channel-actions` — ChannelAction + render-frame Pydantic models (meeting_id UUID, Field(max_length), Literal selectors)
- 🏗️ build-new · `experience.capabilities-catalog` — Typed CAPABILITIES catalog + build-time UI manifest (never fetches, service-strings never ship to browser)
- ♻️ rebuild · `experience.dispatch-funnel` — The one dispatch funnel — rate-limit → registry-lookup → validate → meeting/entity isolation → route
- 🏗️ build-new · `experience.channel-projector` — ChannelProjector — AgentChunk delta stream → registered render frames (pure rendering, two event types)
- 🏗️ build-new · `experience.http-registry` — Contract-registry HTTP wrappers — protected()/PublicAuthzCtx + allowlist + route-scope test + safeError
- 🔧 fix · `experience.hmac-webhook-verify` — Recall webhook HMAC-signature verifier — the public route earns its exemption by proving the caller
- ✅ verify · `experience.capability-token-read` — Capability-token verifier — signed, short-TTL, meeting-scoped, revocable, read-only notes (built)
- 🏗️ build-new · `experience.connect-page` — The connect page — GitHub App install + REST readiness poll rendering all five states (build-new)
- 🔧 fix · `experience.notes-file-template` — The §2.6 notes-file template — attendees, 5-line worst-news-first summary, 'what Proxy did' + receipts
- 🏗️ build-new · `experience.meeting-home-page` — GET /m/{meeting_id} home — dual-mode read (session OR capability token): notes + staged-draft cards
- 🔧 fix · `experience.draft-accept-reject-routes` — POST /m/{meeting_id}/drafts/{draft_id}/{accept,reject} — CSRF + tenant + idempotent + audit, calls Doc 04 accept · ⚠️HUMAN-GATED
- 🏗️ build-new · `experience.tile-orb-state-machine` — The tile — orb bloom + §2.2 state machine, each state bound to a real system event (no state without a source)
- 🏗️ build-new · `experience.chat-formatters` — Deterministic chat formatters — decision/action note-lines, correction ack, draft card, reconciliation card
- 🏗️ build-new · `experience.field-diff-check` — Per-field produce/consume contract diff — flags a field produced by one side, consumed by neither (CI, un-trimmed)
- 🏗️ build-new · `experience.screen-content-modes` — V0 screen renders (structured-progress / pin-to-source / final-artifact-preview) + human-activated walkthrough/screen-share
- 🏗️ build-new · `experience.copy-guide` — Copy guide CI check (banned patterns) + three honesty shapes + seed-string artifact

## doc09 VERIFICATION (journeys) — 15 nodes  (build-new:15)
- 🏗️ build-new · `journey.registry-closed` — Contract check: assert_registry_closed() holds
- 🏗️ build-new · `journey.contracts-resolve` — Contract check: every doc's wire shape resolves to libs/contracts
- 🏗️ build-new · `journey.one-operation-runs` — Contract check: exactly one operation_runs table; reconcile reaps duplicates
- 🏗️ build-new · `journey.agentchunk-stream-deltas` — Contract check: AgentChunk consumers use stream_deltas (no raw TEXT read)
- 🏗️ build-new · `journey.cost-drafts-persist` — Contract check: meeting_cost + staged_drafts survive a simulated process kill
- 🏗️ build-new · `journey.s1-happy-arc` — S1 — the happy arc: connect → invite → grounded answer → notes posted
- 🏗️ build-new · `journey.s2-dep-graph-blast-radius` — S2 — 'what breaks if we change this table?' dep-graph blast radius
- 🏗️ build-new · `journey.s3-staged-draft-accept` — S3 — real work → staged draft persists → verified → human accept after teardown
- 🏗️ build-new · `journey.s4-barge-in-human-control` — S4 — barge-in + human control: stop speech <200ms, abort in-flight build
- 🏗️ build-new · `journey.s5-recycle-survives` — S5 — hours-long meeting survives an instance recycle
- 🏗️ build-new · `journey.s6-concurrency` — S6 — concurrency: several meetings on one host, no cross-meeting starvation
- 🏗️ build-new · `journey.s7-cost-circuit-breaker` — S7 — cost circuit-breaker: soft-cap degrade with disclosure, hard-cap notes-only
- 🏗️ build-new · `journey.s8-honest-failure` — S8 — honest failure: says what it can't see, never confident-wrong
- 🏗️ build-new · `journey.s9-tenant-isolation` — S9 — tenant isolation: message without a valid owned meeting_id is rejected
- 🏗️ build-new · `journey.demo-arc` — Demo arc — one continuous run spanning ALL docs
