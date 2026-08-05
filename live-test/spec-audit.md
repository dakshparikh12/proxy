# Proxy Codebase Spec Audit
**Date:** 2026-08-04  
**Scope:** All packages — `services/{premeeting,in-meeting,control-plane,workroom}` + `libs/*`  
**Reference specs:** `PROXY_SYSTEM_SPEC.md`, `CLAUDE.md`, `SPEC.md v6`  
**Excluded (concurrent builder A):** transcript→resident-cached-conversation + marathon condensing + guardrail-in-sandbox (`services/in-meeting`)  
**Excluded (concurrent builder B):** private-repo auth-token injection + premeeting dead code (`services/premeeting`)  

---

## Counts

| Category | Count |
|---|---|
| Structural deviations from spec | 8 |
| Dead code / dead wiring items | 34 |
| Overengineering items | 3 |

---

## Section 1 — Structural Deviations from the Spec

These are places where the code disagrees with what the spec says the system is or how it works, beyond what builders A and B are addressing.

### SD-1 · `github_webhook.py`: dead `code_intel` pipeline dispatch branch always no-ops
**File:** `services/control-plane/src/control_plane/github_webhook.py:354–364`  
**Spec violation:** `PROXY_SYSTEM_SPEC.md` §WS1 + `connect.py:294–304` explicitly state "there is NO code_intel graph… this trigger no longer registers a code_intel pipeline." `LivePipelineRegistry` is always empty because the connect trigger never calls `registry.register()`. The block that calls `pipeline.webhook_handler.handle(webhook)` is therefore permanently unreachable — the real push-freshness path is `_maybe_refresh_map()` at line 371. The entire `LivePipelineRegistry` class (lines 63–88), `get_pipeline_registry()` (lines 90–103), `_PushWebhook` struct (lines 106–130), `_push_webhook_from_payload()`, and the dispatch block in the route are architecture remnants from the old `code_intel.pipeline.Pipeline` system.  
**Evidence:** `grep -rn "registry.register\|LivePipelineRegistry" services/` returns only `github_webhook.py` itself; `connect.py:666` calls `get_pipeline_registry()` but only to read it — never to write a pipeline into it.

### SD-2 · `github_webhook.py` module docstring describes old `code_intel.pipeline.Pipeline` architecture
**File:** `services/control-plane/src/control_plane/github_webhook.py:3–9`  
**Spec violation:** The module docstring opens with "The connect→index trigger builds a per-tenant `code_intel.pipeline.Pipeline` that carries a live freshness `webhook_handler`" — describing an architecture that was explicitly killed. The live path is `_maybe_refresh_map()` → `premeeting.refresh_on_push`. The docstring is architecturally false.

### SD-3 · `objectstore.py` is a local tmpdir stub where the spec requires GCS Object Versioning
**File:** `services/workroom/src/workroom/objectstore.py:14`  
**Spec violation:** `SPEC.md §Architecture` specifies "GCS (object-versioned)" as a durable substrate. `PROXY_SYSTEM_SPEC.md §WS8` lists "GCS object-store swap" as a deploy artifact. The objectstore today writes to `tempfile.gettempdir() / "proxy-object-store"` — data does not survive pod restart and is not shared across instances. The module docstring acknowledges this ("local filesystem stand-in for the MVP") but it remains unswapped.  
**Impact:** Draft artifacts (`propose_change()`) are lost on pod restart; multi-instance deployments see different draft stores.

### SD-4 · `ConsentGate` wired into transport exports but has no runtime consumer
**File:** `services/in-meeting/src/transport/join.py:24–39`, `services/in-meeting/src/transport/__init__.py:11`  
**Spec violation:** `SPEC.md` killed `HearingStage` in the workroom pivot. `ConsentGate`'s docstring says "the live HearingStage reads this gate" — that class does not exist in the new system. `MeetingRuntime` has no `consent_gate` field. No production file imports `ConsentGate` outside `transport/__init__.py`. The gate is a dead wire: exported but unconsumed and semantically referencing a deleted subsystem.

### SD-5 · WS gateway (`/ws`) is mounted with a no-op handler — transcript arrives via webhook, not WS
**File:** `services/control-plane/src/control_plane/gateway_route.py:138–215`, `app.py:205`  
**Spec violation:** `SPEC.md v6` and `PROXY_SYSTEM_SPEC.md` describe one transcript path: Recall webhook → `webhooks.py` drain → `MeetingSession.on_line()`. There is no WebSocket transcript channel. Yet `install_gateway_route(app)` mounts `GET /ws` on the live app. The sole handler `handle_channel_action()` in `libs/http/handlers/channel_action.py` is an explicit no-op stub: "No live fulfilling service is bound on this seam." This is dead infrastructure mounted on the live app.

### SD-6 · `premeeting/repo_context.py` builds a `code_intel` MCP toolbelt not used in the new workroom path
**File:** `services/premeeting/src/premeeting/repo_context.py` (entire file)  
**Spec violation:** `PROXY_SYSTEM_SPEC.md §WS1` says delete the old code_intel indexer. `connect.py:294` confirms "NO code_intel graph and NO `mcp__code_intel__*` mount in the new system: native Claude in the workroom greps the real repo directly." `RepoContext` and `build_server()` build the old MCP tool manifest — callers are zero in the production service path.  
**Note:** Builder B may address this; flagged here for completeness per instructions (do not re-report if B covers it).

### SD-7 · `RecallTransport` internal queue infrastructure has no consumers in the new architecture
**File:** `services/in-meeting/src/transport/recall.py` — `_roster`, `_chat` dicts, `roster_events()`, `chat_events()`, `_ingest_roster()`, `_ingest_chat()`  
**Spec violation:** The new architecture feeds transcript via `webhooks.py → runtime.ingest_line()` directly; roster/chat queue consumers do not exist outside `recall.py` itself. The old `HearingStage` that would have drained these queues is gone. These queues accumulate unconsumed events in memory.  
**Evidence:** `grep -rn "roster_events\|chat_events" services/` returns only `recall.py` and `transport/__init__.py` (export only).

### SD-8 · `libs/contracts/channel.py` render-frame types are registered in the contracts system but never consumed
**File:** `libs/contracts/src/contracts/channel.py` — `ResponseChunk`, `ResponseStart`, `ResponseEnd`, `VoiceSpeak`, `ToolStart`, `DraftCard`, `CanvasPatch`, `TileState`, `NoteLine`  
**Spec violation:** `SPEC.md §11` says "delete the old in-meeting engine." These render-frame types were the outbound wire contracts of the old engine (now replaced by `to_meeting` MCP + relay). They are still registered via `assert_registry_closed` which makes the build guard check them, polluting the contracts system with dead types.  
**Evidence:** `grep -rn "ResponseChunk\|ResponseStart\|ResponseEnd\|VoiceSpeak\|ToolStart\|DraftCard\|CanvasPatch\|TileState\|NoteLine" services/` returns zero hits.

---

## Section 2 — Dead Code / Dead Wiring Inventory

Dead = has no live caller on any production path, confirmed by grep. Sorted by package.

### `services/in-meeting`

| # | Item | File | Evidence |
|---|---|---|---|
| DC-01 | `ConsentGate` class | `transport/join.py:24–39` | Zero imports in control-plane or in-meeting production code |
| DC-02 | `Signal` union + `Transcript`, `Speaking`, `Boundary`, `BargeIn`, `BotStatus`, `MeetingEnd`, `MeetingMetadata`, `signal_name()` | `transport/signals.py` | Zero production consumers; `ChatMessage`/`RosterEvent` used internally in `recall.py` only |
| DC-03 | `RecallTransport._roster`, `_chat` dicts; `roster_events()`, `chat_events()`, `_ingest_roster()`, `_ingest_chat()` | `transport/recall.py` | Queues fill; no drain. HearingStage is gone. |
| DC-04 | `transport/__init__.py` re-exports for all dead Signal types + ConsentGate | `transport/__init__.py:11,29` | Exports dead symbols into the namespace |

### `services/control-plane`

| # | Item | File | Evidence |
|---|---|---|---|
| DC-05 | `LivePipelineRegistry` class + `get_pipeline_registry()` | `github_webhook.py:63–103` | Registry never populated; `connect.py:304` confirms |
| DC-06 | `pipeline.webhook_handler.handle()` dispatch block | `github_webhook.py:354–364` | Unreachable: pipeline is always None |
| DC-07 | `_PushWebhook` struct + `_push_webhook_from_payload()` | `github_webhook.py:106–225` | Only used to feed the dead dispatch block above |
| DC-08 | `handle_channel_action()` (no-op stub) | `libs/http/handlers/channel_action.py` | Explicit "no-op stub" comment; no fulfilling service |
| DC-09 | `install_gateway_route()` WS endpoint | `gateway_route.py:138–215` | Mounted on live app; only handler is DC-08 |
| DC-10 | `build_live_dispatch_ctx()` | `gateway_route.py:96–113` | Only caller is the no-op gateway; unused in meeting path |

### `services/premeeting`

| # | Item | File | Evidence |
|---|---|---|---|
| DC-11 | `RepoContext` + `build_server()` | `repo_context.py` (entire) | Zero callers in production; `connect.py:294` confirms "NO code_intel" |
| DC-12 | `_build_map_llm()` | `map_build.py` (legacy, pre-cleanup) | Explicitly marked DEPRECATED; builder B is cleaning |
| DC-13 | All `_build_map_llm` helper constants: `MAP_BUILD_READ_TOOLS`, `MAP_BUILD_BLOCKED_TOOLS`, `DEFAULT_MAX_TURNS`, `DEFAULT_MAX_OUTPUT_TOKENS`, `_DEGRADE_NOTE`, `_DEFAULT_MAP_MODEL`, `build_skeleton()`, `collect_high_yield()`, `_build_prompt()`, `_capture_terminal_text()`, `_record_tool_log()`, `_is_failed_build()`, `_BUILD_FAILURE_MARKERS`, `_degraded_map()`, `_default_map_model()` | `map_build.py` | All only reachable via `_build_map_llm()`; builder B cleaning |

### `services/workroom`

| # | Item | File | Evidence |
|---|---|---|---|
| DC-14 | `recover_task()`, `should_restart()`, `RecoverResult`, `WORKROOM_OP_PREFIX` | `recovery.py` (entire) | `grep -rn "recover_task\|should_restart" services/` → zero hits outside this file; exported but never imported |
| DC-15 | `make_propose_change_server()`, `make_propose_change_tool()`, `PROPOSE_CHANGE_TOOL_DISPOSITION` | `drafts.py` | Old host-side MCP server mounting pattern; zero callers in production |

### `libs/contracts`

| # | Item | File | Evidence |
|---|---|---|---|
| DC-16 | `Bundle` (Orchestrator→Workroom wire type) | `bundle.py` (entire) | `grep -rn "from contracts.*bundle\|import Bundle" services/` → zero hits |
| DC-17 | `ResponseChunk`, `ResponseStart`, `ResponseEnd`, `VoiceSpeak`, `ToolStart`, `DraftCard`, `CanvasPatch`, `TileState`, `NoteLine` | `channel.py` | Old render-frame contracts; zero service consumers |
| DC-18 | `ChannelAction` — handler is a no-op (DC-08); the type itself is technically live (passed to stub) but functionally dead | `channel.py` | Handler is stub; no fulfilling service |
| DC-19 | `NoteDelta`, `NoteOp` | `notes.py` | `grep -rn "NoteDelta\|NoteOp" services/` → zero hits |
| DC-20 | `MaterialChangeKind` | `material_change.py` | `grep -rn "MaterialChangeKind" services/` → zero hits |
| DC-21 | All `__init__.py` re-exports for DC-16 through DC-20 | `contracts/__init__.py:51–54,148–150` | Keeps dead types in the contracts-closed check |

### `libs/agentkit`

| # | Item | File | Evidence |
|---|---|---|---|
| DC-22 | `pick_provider()`, `register_provider()`, `_PROVIDERS`, `_DEFAULT_PROVIDER` | `provider.py:179–205` | BehaviorRunner remnants; `grep -rn "pick_provider\|register_provider" services/` → zero service callers |
| DC-23 | `thinking_policy()`, `compute_builtin_tools()` | `provider.py:134–175` | Same; no service callers |
| DC-24 | `stream_deltas()` | `deltas.py` | Only caller was `_build_map_llm()` (dead); exported in `agentkit/__init__.py` but zero service imports |
| DC-25 | `agentkit/__init__.py` re-exports for DC-22 through DC-24 | `agentkit/__init__.py:14,16–19,28–33` | Publishes dead symbols |

### `libs/http`

| # | Item | File | Evidence |
|---|---|---|---|
| DC-26 | `handle_channel_action()` | `handlers/channel_action.py` | Explicit no-op stub with "No live fulfilling service" comment |
| DC-27 | `DispatchCtx` + its builder machinery | `dispatch.py` (relevant portions) | Only consumer is `build_live_dispatch_ctx()` in DC-10 (which feeds DC-08) |

---

## Section 3 — Overengineering

These are places where the code is more elaborate than the spec requires — infrastructure solving problems the current system doesn't have.

### OE-1 · WS gateway + dispatch funnel for a no-op handler
**Files:** `services/control-plane/src/control_plane/gateway_route.py`, `libs/http/src/http/dispatch.py`, `libs/http/src/http/gateway.py`  
**Issue:** The `/ws` WebSocket endpoint mounts a full dispatch context (`DispatchCtx.build()`) with a repos-backed store, an async WS accept loop, and a structured handler protocol — all to call a no-op stub. The spec describes one transcript path (Recall webhook drain); there is no spec for a WS transcript channel. This is elaborate infrastructure serving no live function.  
**Remedy:** Remove the gateway route mount from `app.py`, delete `gateway_route.py`, prune `dispatch.py`/`gateway.py` of anything only consumed there.

### OE-2 · Contracts registry machinery checking dead wire types
**File:** `libs/contracts/src/contracts/registry.py`, `contracts/__init__.py`  
**Issue:** The `assert_registry_closed` machinery (MESSAGE_HANDLERS, MESSAGE_PRODUCERS, MESSAGE_PROJECTORS, SIGNAL_SURFACE_EVENTS) is a correctness guard for the old Orchestrator→Workroom message bus, where the set of wire types had to be closed at build time. In the new system there is only one wire type in active use on the meeting path (`MeetingSend` / relay POST body). The registry currently guards 9 render-frame types (DC-17) and `Bundle` (DC-16) that have zero production consumers — it's a build gate protecting dead code.  
**Remedy:** After deleting dead types from `channel.py` and `bundle.py`, the registry complexity can be dramatically reduced; `assert_registry_closed` may be removed or simplified to just the types that are live.

### OE-3 · Provider registry in `agentkit/provider.py` for a system with one provider
**Files:** `libs/agentkit/src/agentkit/provider.py:179–205`, `libs/agentkit/src/agentkit/__init__.py`  
**Issue:** `register_provider()` / `pick_provider()` / `_PROVIDERS` / `_DEFAULT_PROVIDER` implement a dynamic model-id → provider dispatch table. In the new system, `ClaudeAgentProvider` is the only provider ever constructed; nothing calls `register_provider()` or `pick_provider()`. The registry was designed for a multi-provider BehaviorRunner world that no longer exists.  
**Remedy:** Delete the registry functions and the `_PROVIDERS` / `_DEFAULT_PROVIDER` module-level state. `ClaudeAgentProvider` is instantiated directly where needed (`make_map_provider()` in `sdk_provider.py`).

---

## Top 10 Most Important Items

Ranked by impact on correctness, product integrity, and consolidation leverage:

1. **SD-1 / DC-05 / DC-06** — `github_webhook.py` `LivePipelineRegistry` + dead dispatch block: the webhook route's own docstring describes architecture that was killed. The live `_maybe_refresh_map()` path is buried after code that always short-circuits. Confusion risk and dead weight in the most security-sensitive route.

2. **SD-5 / DC-09** — WS gateway mounted on the live app: `GET /ws` is a live route on the Cloud Run service with a no-op handler. It's attack surface with no product function. Should be removed from `app.py`.

3. **DC-14** — `services/workroom/recovery.py` entirely dead: `recover_task()` / `should_restart()` are exported but never called. The file represents a recovery strategy for an old architecture. Zero risk of accidental use; pure dead weight.

4. **DC-16** — `libs/contracts/bundle.py` `Bundle` type: the Orchestrator→Workroom wire contract is dead. Removing it unblocks simplifying the contracts registry (OE-2).

5. **SD-8 / DC-17** — Nine render-frame types in `channel.py` (`ResponseChunk`, `ResponseStart`, etc.) with zero service consumers. Removing them reduces the contracts-closed check to only live types.

6. **SD-4 / DC-01** — `ConsentGate` exported from transport with a docstring pointing to deleted `HearingStage`. No consumer. Misleads future readers about the session lifecycle.

7. **DC-22 / DC-23** — Dead provider registry in `libs/agentkit/provider.py` (`pick_provider`, `register_provider`, `thinking_policy`, `compute_builtin_tools`): these are exported at the lib level and look authoritative, but are never called by any service. OE-3.

8. **SD-7 / DC-03** — `RecallTransport` internal roster/chat queues (`_roster`, `_chat`, `roster_events()`, `chat_events()`): events queue unconsumed, a memory leak in long meetings. The new architecture doesn't drain them.

9. **SD-3** — `objectstore.py` tmpdir stub: draft artifacts are lost on pod restart and not shared across instances. The spec requires GCS Object Versioning. This is the only structural deviation that affects production data durability today.

10. **DC-19 / DC-20** — `NoteDelta`/`NoteOp`/`MaterialChangeKind` in contracts with zero service consumers: cleanup needed alongside the render-frame type removal to bring the contracts registry back to only live types.

---

## Report path
`/Users/daksh/Desktop/proxy/live-test/spec-audit.md`
