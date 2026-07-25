# decisions.md — technical-gap resolutions (append-only)

Every gap the comprehension resolves *itself* (technical/implementation gaps — LOOP.md §1.1b)
is logged here with its rationale, so the human can review the judgment at HALT 1 and the
builder can see why an under-specified seam was implemented a given way. Product-level gaps are
NOT resolved here — they go to the human as a bounded question and the answer is recorded below
once given.

Format:
```
## D-NNN  <short title>   [technical | product-answered]
- Gap: <what the specs left open, cite spec_refs>
- Options considered: <briefly>
- Decision: <what we chose>
- Rationale: <why it best aligns with the product intent>
- Nodes affected: <node ids>
```

<!-- entries appended below during Phase 1 -->

## D-001  webhook_events schema drift  [technical]
- Gap: migration `0001_substrate.py:149` has `{delivery_guid, status, payload, created_at, processed_at}` but CANONICAL §12.10 canon is `{provider, delivery_guid, sha, received_at, status: pending|processed|failed}`. Missing `provider`/`sha`/`received_at`; `status` has no CHECK domain.
- Decision: align to CANONICAL §12.10 (add columns + status CHECK). GitHub-push dedupe needs `sha`; multi-provider dedupe needs `provider` (else GitHub+Recall GUIDs share one uniqueness namespace).
- Rationale: CANONICAL is the designated override; the drift is a real correctness risk (webhook dedupe).
- Nodes affected: a doc00 substrate node (migration) — a VERIFY-mode fix, human-gated (it is a migration).

## D-002  call_external seam has no static guard  [technical]
- Gap: the invariants reader found ZERO raw-client violations today, but nothing *statically* forbids a raw `httpx`/`AsyncAnthropic`/`storage.Client()` outside `libs/http` (convention-only). CLAUDE.md claims "no raw client lives anywhere else."
- Decision: add an AST guard (mirroring `check_sdk_isolation_triad.py`) as a doc00 hardening node, wired into CI + pre-commit.
- Rationale: high blast radius (a raw client silently bypasses retry + cost telemetry); the CON-004 regression proved this class of bug is real.
- Nodes affected: new doc00 hardening node `foundation.call-external-guard`.

## D-003  two heartbeat mechanisms (doc04)  [technical]
- Gap: `libs/ops/operation_run.py` (canonical `operation_runs` fencing heartbeat, ~10s) vs `services/harness/heartbeat.py` (Healthchecks.io dead-man ping, not in spec).
- Decision: the ops `operation_runs` fencing heartbeat is canonical (spec §3.7 mandates it); the Healthchecks ping is auxiliary observability, not the liveness authority.
- Rationale: spec §3.7 + CANONICAL §2/§12.10 name the operation_runs heartbeat as the fence; two liveness stories is a split-brain risk.
- Nodes affected: doc04 `orchestrator.heartbeat-fence`.

## D-004  cost thresholds: code vs spec  [technical — flag at HALT 1]
- Gap: `libs/ops/cost.py:33` hard-codes listening soft/hard caps at $1.5/$3.0, but 04§3.13 + CANONICAL §12.7 say soft/hard = 0.8/1.0 × projected-hours (~$0.95–1.25/hr baseline).
- Decision: govern by CANONICAL §12.7 (0.8/1.0 × projected-hours); update cost.py accordingly during the doc04 build.
- Rationale: CANONICAL is the override; the cost envelope is a product SLA it explicitly pins. **CONFIRM at HALT 1** that no sealed doc00/01 criterion locks the old $1.5/$3.0 constant (if one does, it is a founder-gated sealed-test contradiction, not an auto-fix).
- Nodes affected: doc04 `orchestrator.cost-circuit-breaker`.

## D-005  Envelope `question` field  [technical]
- Gap: 05§3.12 requires "the one question" for `needs_clarification`, but `libs/contracts/envelopes.py` Envelope has no `question` field.
- Decision: resolve per CANONICAL §1.2 — add `question: str | None = None` to Envelope (it is the cleanest; riding `detail` overloads it).
- Rationale: a `needs_clarification` criterion cannot be written without a field to carry the question.
- Nodes affected: a doc00/contracts node + doc05 consumer.

## D-006  doc08 contract registry is pre-canonical  [technical — breaking, spec-mandated]
- Gap: `libs/contracts/registry.py` encodes the superseded `connect-repo/approve-draft/invite-proxy` + WS-connect shape; CANONICAL §12.9/§12.12 deletes `TILE_ADDRESS`, moves connect to REST, and mandates the render-frame + `channel_action` set.
- Decision: REBUILD the registry to CANONICAL (render frames + channel_action family + MESSAGE_PRODUCERS/HANDLERS/PROJECTORS maps); migrate any importer of the old `MessageType`.
- Rationale: CANONICAL overrides; the old shape is explicitly deleted. This is a cross-cutting edge, not an isolated node — sequence it early with a migration wave.
- Nodes affected: doc08 `experience.contract-registry` + every old-MessageType importer.

## D-007  doc05 agent_config.py is an actively wrong isolation stub  [technical — P0]
- Gap: `services/workroom/agent_config.py` allows host built-ins `Read/Grep/Glob` and omits `Task` from `disallowed_tools`; no `strict_mcp_config`/`setting_sources=[]`/computed `tools=[]`. §3.4 BLOCKS those — this is a P0 isolation hole.
- Decision: REBUILD (delete + author fresh) with the full SDK-isolation triad; do NOT extend the stub (extending inherits the hole). The CI guard `check_sdk_isolation_triad.py` goes load-bearing the moment the first real `query()` lands.
- Rationale: the triad IS the lethal-trifecta containment boundary; a live meeting is a richer injection surface than batch jobs.
- Nodes affected: doc05 `workroom.sdk-isolation-triad` (must precede any `query()` node).

## D-008  orchestrator run-loop is missing (not a gap — the core build)  [technical]
- Gap: doc04's substrate primitives (claim/fence/cost/reconcile/affinity/recovery) exist and are tested, but the asyncio event-queue harness that wires them — standing pipes, name-gate, ack reflex, wake dispatch to BehaviorRunner, `behaviors/` dir, real SDK provider seam, `resume_with_fallback` (currently `NotImplementedError`) — does NOT exist.
- Decision: this is the single largest BUILD in the chain; sequence doc04 as the integration spine after 00–03 verify and the doc00 contract/registry fixes. `wake.py`'s code-branch quick-vs-workroom pick (`wake.py:60`) must be rebuilt as model judgment (`config-not-code`).
- Rationale: everything downstream (wake-turn, durability, cost-metering-off-stream, tripwire) blocks on the SDK provider seam + run loop.
- Nodes affected: the doc04 node cluster (provider-seam → behavior-runner → wake-turn → run-loop).

## D-009  .claude/rules/* missing  [technical]
- Gap: §3/§14 promise path-scoped per-tree conventions; CLAUDE.md references `.claude/rules/*`; Glob is empty.
- Decision: create path-scoped rules as the trees that need them are built (not a blocker); track as a doc00 follow-up node.
- Rationale: low-risk; the conventions matter most once 04/05/08 add new trees.
- Nodes affected: doc00 `foundation.path-rules` (low priority).

## D-010  vendor confirm-at-build items  [technical — confirm-at-build, not now]
- Gap: CANONICAL §11.10 flags several wire shapes NOT to assume: live `claude_agent_sdk` message→AgentChunk mapping + structured-output API, E2B `timeout`/`maxRunDuration` + sidecar, Recall webhook/DM shapes.
- Decision: each affected node carries a "confirm live SDK/vendor shape at build" step in its plan (per-node, at build time) rather than guessing now. Not a Phase-1 blocker.
- Rationale: the spec itself names guessing these as the anti-pattern; they are build-time confirmations, doc-local.
- Nodes affected: doc04 `provider-seam`/`sdk-mapping-pin`, doc05 `xport-sidecar`, doc03 `close` (Python SDK structured-output).

**1.1b conclusion:** CANONICAL-DECISIONS.md settles every surfaced ambiguity — there are **no unresolved product-level gaps** requiring a human decision at this stage. D-004 (cost thresholds) is the one item to *confirm* at HALT 1 (it may touch a sealed constant). Everything else defers to CANONICAL/spec as source of truth.

## D-011  Doc 05 §2.6 proactive tasks reference the CUT Doc 06 — no V0 chain node  [technical — Tier-B]
- Gap: Doc 05 §2.6 references proactive tasks, which belong to the CUT Doc 06 (CANONICAL §8 scopes proactive tasks to Expansion, not V0).
- Decision: V0 has NO proactive tasks → correctly NO chain node. The stale §2.6 spec reference is a dangling pointer into the cut doc; it does not change the V0 build.
- Rationale: CANONICAL §8 overrides the spec prose; building a proactive-task node would be scope-creep beyond V0. The chain stays complete without it (no requirement/journey traces to §2.6).
- Nodes affected: none (deliberately — §2.6 is out of V0 scope).

## D-012  Tenant offboarding is a build-time addition to the reconcile scope, not a new node  [technical — Tier-B]
- Gap: CANONICAL §12.9 mandates tenant offboarding (extend run_reconcile_sweep to delete a tenant's Postgres rows + GCS prefixes) — a real safety/data-lifecycle obligation, but an offboarding edge rather than a core V0 flow.
- Options considered: (a) a separate `foundation.tenant-offboarding` build-new node; (b) fold it into `foundation.observability-floor`'s reconcile scope as a build-time criterion.
- Decision: fold it into `foundation.observability-floor` — the reconcile sweep it already owns is extended to delete tenant Postgres rows + GCS prefixes on offboarding (add its criterion_ids there if any surface at HALT 1). Kept minimal; no separate node.
- Rationale: it is one more sweep behavior on an existing owner, not a new seam; a standalone node would over-fragment the reconcile responsibility. The isolation triad + object-versioned GCS make the deletion boundary well-defined.
- Nodes affected: `foundation.observability-floor` (reconcile scope extended; criterion_ids appended if/when sealed).
