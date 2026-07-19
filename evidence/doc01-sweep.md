4-007 only checks `flag_reason` is non-empty generically. The "never silently lies" distinction is untested.

**3. The `~10s` push-to-queryable freshness SLO.** §3.6 (line 275): *"Target: `[~10s]` push-to-queryable on a pilot-scale repo."* §4 (line 375): *"substrate + graph push-to-fresh `[~10s]` (full rebuild, pilot scale)."* AC-M4-009/AC-M4-013 verify a full rebuild *happens*; AC-LAT-001 (first-text p50) and AC-LAT-002 (connect→ready 15 min) are different SLOs. No latency criterion measures push-to-queryable elapsed time.

**4. Deferred agentic-map tools absent from the core tool surface.** §3.5 (line 265): *"The deferred agentic-map tools (`get_capability`, `search_capabilities`, `get_flow`) are **not** in the core (CANONICAL §7)."* No criterion asserts these names are absent from the core `allowed_tools` — the parallel absence checks (AC-SANDBOX-001/002) only cover LSP-in-sandbox, not agentic-map-in-core.

**5. `alwaysLoad:true` on the `code_intel` MCP server.** §3.5 (line 265): *"`alwaysLoad:true` on this MCP server (Doc 00 III.8 — else an intermittent tool-less first turn)."* This is a stated config contract guarding a user-visible failure (a tool-less first turn); no criterion checks it.

**6. Sandbox re-provision re-seeds at the pinned SHA, not HEAD.** §3.6 (line 279): *"On a sandbox re-provision (Workroom recycle), the sandbox **re-seeds at `meeting.pinned_sha`**, not at HEAD."* No criterion covers the re-provision/recycle re-seed behavior. (Borderline cross-doc with Doc 05, but the requirement is asserted here in Doc 01.)

**7. Incremental parse — unchanged files keyed by hash are never re-touched.** §3.4 (line 123): *"The parse is incremental — a changed file re-parses in milliseconds; **unchanged files (keyed by hash) are never touched.**"* The graph *rebuild* is full (AC-M4-009), but the tree-sitter *parse* skip-by-hash is a distinct, separately-observable behavior with no criterion.

**8. The `too-large` flag reason.** §3.4 (line 127): flag_reason enum `unsupported-language | generated | too-large | excluded | submodule-uninitialized`. Criteria test `unsupported-language` (AC-M4-010) and `generated`/`vendor` (AC-M6-005), but no criterion exercises a file flagged `too-large`.

---

Lower-confidence (stated but arguably subsumed / delegated — flagging for completeness, not asserting they are hard gaps):

**9. The `~50–100ms` host-side `code_intel` API hop.** §4 (line 375) / §2.1: *"a host-side `code_intel` API call on the direct-answer path ~50–100ms."* Stated as a distinct sub-target; only the composite p50≤2s (AC-LAT-001) is measured, never the hop itself.

**10. Durable enqueue (survives restart), as opposed to dedup.** §3.6 (line 275): *"enqueued **durably** and deduplicated by delivery-GUID + commit SHA."* AC-M7-002 covers dedup; no criterion covers durability (an enqueued webhook survives a worker restart and still triggers the rebuild).

The full walk found gaps, so I am **not** emitting `SWEEP: NO GAPS`. Nothing was modified.
