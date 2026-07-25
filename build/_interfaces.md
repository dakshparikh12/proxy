# Canonical interface vocabulary (the seam spine)

Every chain node's `consumes`/`exposes` MUST use an id from THIS list (so Tier-A wiring-closure
balances). Synthesized from the Phase-1 readers + CANONICAL-DECISIONS.md. Producer doc in [ ].

## Foundation / contracts [00] — imported everywhere
- `contracts.Bundle` — 04→05 task packet {ask,speaker,timestamp,notes_ref,transcript_tail,task_id}
- `contracts.Envelope` — 05→04 result {headline,detail,artifact,receipts,status,verification,draft_id,task_id,question?}
- `contracts.AgentChunk` — normalized SDK stream (6 variants) + `agentkit.stream_deltas`
- `contracts.NoteOp` — note-delta ops add/patch/close
- `contracts.Readiness` — connecting|cloning|indexing|ready|not_ready + coverage_pct + gaps
- `contracts.ChannelReport` — dm_available
- `contracts.registry` — ProxyMessage registry + assert_registry_closed (client wire types only)
- `substrate.operation_runs` — the universal durable row (claim/heartbeat/fence/close/workroom-task)
- `substrate.meeting_cost` · `substrate.staged_drafts` · `substrate.webhook_events` · `substrate.transcript_segments`
- `http.call_external` — the one external-call seam · `http.dispatch` — the one inbound funnel · `http.internal_notes` — GET /internal/notes
- `ops.claim_meeting` · `ops.with_meeting_lock` · `ops.check_meeting_budget` · `ops.AbortRegistry` · `ops.resume_with_fallback` · `ops.sandbox_provider` · `ops.reconcile` · `ops.affinity`
- `agentkit.BehaviorRunner` · `agentkit.stream_deltas` · `agentkit.tools` (never-throw) · `agentkit.wake_cache`
- `llm.routing` (model seats) · `lint.naming` · `ops.check_secret_bindings` · `ops.check_sdk_isolation_triad`

## code_intel [01]
- `codeintel.readiness` (produces `contracts.Readiness`) · `codeintel.mcp_tools` (8-tool server: get_dependents/who_writes/shares_table/list_entry_points/owner/find_references/lookup_referent/batch_read)
- `codeintel.direct_api` (host-side answer API, no E2B) · `codeintel.lookup_referent` (→03) · `codeintel.snapshot` (pinned-SHA clone →05) · `codeintel.freshness` (push webhook)

## transport [02] — signal surface + delivery verbs (in-process carrier)
- `transport.signal_carrier` (the one fan-out) · `transport.transcript` · `transport.chat` · `transport.roster` · `transport.speaking` · `transport.boundary` · `transport.barge_in` · `transport.bot_status` · `transport.meeting_end` · `transport.channel_report`
- `transport.speak` · `transport.send_chat` · `transport.show_screen` (delivery verbs) · `transport.join_consent` (gated join)

## scribe [03]  (consumes transport.transcript, transport.chat, codeintel.lookup_referent)
- `scribe.note_deltas` · `scribe.notes_object` (folded live notes) · `scribe.material_events` (→04) · `scribe.close_artifact` (notes.md) · `scribe.cost` (→meeting_cost)

## orchestrator [04] — the spine (consumes codeintel.*, transport.*, scribe.*, workroom.envelope, substrate.*)
- `orchestrator.run_loop` (asyncio event queue) · `orchestrator.wake_turn` · `orchestrator.behaviors` · `orchestrator.name_gate` · `orchestrator.ack_reflex`
- `orchestrator.direct_answer` (mounts codeintel.direct_api) · `orchestrator.bundle_dispatch` (→05) · `orchestrator.cost_breaker` · `orchestrator.accept_handler` · `orchestrator.close` · `orchestrator.provider_seam`

## workroom [05] — the hands (consumes contracts.Bundle, codeintel.snapshot, codeintel.mcp_tools, http.internal_notes)
- `workroom.envelope` (→04) · `workroom.sandbox` (E2B sidecar) · `workroom.verify_gate` (fresh verifier + evidence) · `workroom.propose_change` (→staged_drafts) · `workroom.isolation_triad`

## experience [08] — the surfaces (consumes contracts.AgentChunk, codeintel.readiness, scribe.note_deltas, substrate.staged_drafts)
- `experience.dispatch_funnel` (impl of http.dispatch) · `experience.projector` (AgentChunk→render frames) · `experience.capabilities` · `experience.connect_page` · `experience.meeting_home` · `experience.accept_route` · `experience.notes_template` · `experience.tile`

## external_inputs (enter the product — satisfy a consume with no producer)
`recall.webhook` · `github.webhook` · `chat.inbound` · `human.accept_click` · `human.speech` · `stt.stream`

## product_endpoints (leave the product — satisfy an expose with no consumer)
`notes.finalized` · `draft.accepted` · `spoken.answer` · `connect.rendered` · `meeting_home.rendered`
