# Doc 01 · Code Intelligence — Build Spec

*Build order: 2nd (after Doc 00 scaffold). Reads from Doc 00 (runtime, tenancy, hosting). Consumed by Doc 03 (referent resolution), Doc 04 (Orchestrator), Doc 05 (Workroom — the primary consumer), Doc 06 (Proactive). This document is the complete description of what to build and exactly how it must work; acceptance criteria and tests are generated from it separately.*

*Conformance: this doc is bound by `CANONICAL-DECISIONS.md` (§0, §1.5, §6, §7, §8, §9, and the final reconciliation §12 — esp §12.2 code-intel locus + §12.6 code-intel honest & simple, which win over any divergent text here). The **core** built here is the mechanical code-intelligence layer — tree-sitter/PageRank structural overview + native grep/read/glob + a host-side language server (Serena/solid-lsp) + a mechanically-built dependency/call graph — all served by **one host-side `code_intel` service** (§12.2: read-only tools answer host-side; the E2B sandbox is Workroom-mutable-work only). The heavier **agentic knowledge map** (grep-anchored directory summaries, the L1/L2 capability map, lazy flow dossiers, and the `map_runs`/`map_artifacts`/`map_events` pipeline) is **deferred to Expansion** — see the final section — and is recorded as the first expansion seam we build off this core.*

---

# 1 · The end goal

Proxy is an AI participant that joins a company's meetings **already knowing their codebase**. This document builds the layer that makes that true.

Stated precisely: **when a meeting starts, Proxy's agent must be able to find anything in the company's repository, navigate it precisely, and ground real work in it — at meeting speed (~1–2 seconds for a lookup), with every answer cited to `file:line`, honestly labeled for how certain it is, on code that is always current with the repo's latest push — and this must hold for any repo at any scale.** The honesty boundary is explicit (CANONICAL §12.6): **any repo is answered; table-write blast-radius (`who_writes`) is exact only on supported (language, ORM) stacks — Django ORM, SQLAlchemy, Rails ActiveRecord — and is labeled a `lower-bound` everywhere else; we never emit a silent wrong-exact.**

Concretely, when this layer works:
1. A company connects a repo. Within minutes we hold a fresh copy of their code on our infrastructure and a complete index of where everything in it lives.
2. In a meeting, the agent can resolve "where's the checkout retry logic?", "who calls `chargeCard`?", "what would this change touch?", **"what breaks if we change this table?"**, **"which services share this table?"**, and **"who owns this?"** — correct, `file:line`-cited, in ~1–2s.
3. Every push updates our copy and index within seconds. The agent never works on stale code, and code never shifts underneath it mid-meeting.
4. Nothing is overstated: exact answers are labeled `resolved`; search-derived answers are labeled `lower-bound`; a `who_writes` on an unsupported (language, ORM) stack is **always** a labeled `lower-bound`, never a silent exact; "not found" never means "doesn't exist."

The **dependency/call graph is the core value-add** on top of raw grep. Grep-first, live, always-current retrieval is how every claim is grounded. The dependency graph is what lets the agent answer the impact questions grep alone answers badly — "who calls `chargeCard`?", "what writes the `payments` table?", "what breaks if we change this table?" — by resolving `symbol|table → its dependents/writers → their file:line` in **one tool call**, then reading the current code to confirm. That is the ~1–2s in-meeting answer.

**What this layer is NOT.** It is not the agent — the reasoning loop that *uses* this layer (planning, sandbox work, delivering answers) is **Doc 05**; this layer is the supply of code knowledge. It is not a knowledge graph as a source of truth — the dependency graph stores *where things are* and *what depends on what*, but a claim is **never** grounded on the graph's stored edge; understanding is produced live by the agent, every time, on current code (see the deference rules in §2). It is not multi-repo (cross-repo linking is out of scope here). The agentic knowledge map that translates business vocabulary ("can we change refunds?") into a prepared trace is **deferred to Expansion**.

---

# 2 · The design

**The design philosophy in one line: this is Claude Code operating on the customer's repo — plus the one thing Claude Code doesn't have, a prepared mechanical index and dependency graph for meeting-speed orientation and blast-radius answers.**

Claude Code proves that a capable agent with a real filesystem, grep, read, and a language server handles *any* code task on *any* repo with no pre-built knowledge structure. We replicate that exactly. The only reason we pre-build anything is **latency and orientation**: Claude Code on a laptop can hunt for tens of seconds; a meeting cannot. So we precompute the *minimum* mechanical artifacts that (a) remove the hunting (a ranked structural overview) and (b) answer impact questions in one call (the dependency graph) — and we do everything else live.

## 2.1 · Live retrieval is PRIMARY

The load-bearing capability is a **read-only clone on our cloud**, worked with the agent's **native tools** — grep/ripgrep, read, glob — exactly as Claude Code does, plus a **language server for exact symbol resolution**. Per CANONICAL §12.2 the execution locus is fixed: **all read-only code tools are served by the host-side `code_intel` service** (the graph from per-repo SQLite + the pinned clone + a **warm host-side LSP** — a sandbox has no warm LSP, so in-sandbox resolution is out). The **direct-answer wake turn** (Proxy's ~1–2s path) calls the `code_intel` internal API — **one ~50–100ms hop, not an E2B round-trip and not a Workroom session**. E2B is Workroom-only, and only there does the agent get **mutable** native tools (`bash`/`git`/`edit`) for actual code changes. Every grounded answer Proxy gives comes from reading the *current* code, live. The index and the dependency graph never answer a question; they only tell the agent where to look and what to check.

This is enforced by **three deference rules that are non-negotiable and mechanical:**

1. **The graph points, code answers.** Every grounded claim Proxy makes in a meeting cites `file:line` from the **current clone at the pinned commit** — never the dependency graph's stored edge or a cached overview. The graph's job ends the moment it has pointed the agent at the right neighborhood (the callers, the writers, the entry points); the agent then greps/reads the real file and grounds the claim there. `get_dependents("chargeCard")` returning `payments/refund.py::issue` is a *lead*, not a citation — the agent opens `payments/refund.py`, confirms the call site, and cites the actual line.
2. **Freshness deference.** The dependency graph is mechanical and re-derived on every push (§3.5), so it is rarely stale — but on any doubt, or if a graph node references a file that changed since the graph was last built, the agent **re-reads live** and trusts the code over the graph. A stale graph edge degrades to "a hint that might be wrong," never to "a wrong answer," because the agent always confirms against current code before citing.
3. **Cheap + shallow by default; precision on demand.** The mechanical structural overview + grep is the default, always-on surface — zero model calls, milliseconds per file. The **language server** (Serena/solid-lsp) is the one precision instrument, kept **warm host-side in the `code_intel` service** and exposed to the agent as the `find_references` tool for exact symbol resolution when it edits, refactors, or checks impact. It is not run in the E2B sandbox (no warm LSP there — §12.2). We never pay to deeply analyze code a meeting won't touch.

These three rules are why the graph is not the stale knowledge graph we rejected: it is a **router with an honesty contract**, not a store of truth.

## 2.2 · The mechanical substrate + the dependency graph (the core)

From the clone we build a mechanical substrate whose job is **coverage, latency, and blast-radius**. Everything here is built by parsers and the language server — **no LLM, nothing to hallucinate, re-runnable on every change**:

**The structural substrate.** tree-sitter tag extraction + aider-style PageRank ranking over the tag-reference graph (a function used by twenty places outranks a private helper). It produces the **file-coverage record** and a **ranked overview** ("table of contents + neighborhoods"), and it is the source of the graph's nodes.

**The dependency/call graph — the core value-add.** Nodes (symbols: functions, methods, classes, routes, tables, modules — marked `exported` when they are a route / public symbol / table) + typed edges (`calls`, `imports`, `reads`, `writes`, `extends`, `implements`), built **mechanically from the LSP call graph + tree-sitter imports — no LLM, no CSV ingest.** We hold the code, so the graph is a free by-product of the substrate and carries zero freshness burden. It is what powers `get_dependents` (reverse-dependency / blast-radius), `who_writes(table)` (write-path for a schema change), `shares_table(table)` (the cross-module co-access on one table — the hidden coupling a meeting misses), and `list_entry_points()` (graph roots — routes/jobs/handlers). This is what lets the agent answer "what breaks if we change this table?" in one call. Both `shares_table` and `owner(path)` are the *same* mechanical graph read (plus, for `owner`, a CODEOWNERS file read) — no new substrate.

Depth stops there. No stored semantic meaning, no business-capability layer, no per-symbol summaries, no embeddings, no LLM in the core. The working motion is the entire latency win: **overview/graph → neighborhood → one grep/read lands it → grab → come back.**

## 2.3 · The agent works natively; the graph is advertised, not forced

**The one canonical tool matrix (CANONICAL §12.6):**

| tool | served by | mutable? |
|---|---|---|
| `get_dependents` · `who_writes` · `shares_table` · `list_entry_points` · `owner` · `batch_read` · `find_references` · `lookup_referent` | host-side `code_intel` MCP server (factory-per-query) | read-only |
| `grep` · `read` · `glob` | native, where the agent runs | read-only |
| `bash` · `git` · `edit` | E2B sandbox only (Workroom) | **mutable** |
| `propose_change` | host-side in-process MCP tool (CANONICAL §11.7) | staged draft |

The clone, the overview, and the dependency graph are held host-side; the read-only surface above is served by the host-side `code_intel` service, and the Workroom agent additionally gets native mutable tools inside its E2B sandbox. The graph/LSP/referent tools are **advertised, never forced**: the agent picks the cheapest tool for the question — one graph call to get the blast radius, then grep/read to ground — and falls back to raw search whenever the graph doesn't have what it needs. `find_references` (the warm host-side language server) is the one precision instrument, for exact symbol resolution when the agent edits, refactors, or checks impact. There is **no in-sandbox LSP** and **no second copy of these tool names** anywhere.

## 2.4 · Freshness that can't lie

The clone, the structural substrate, and the dependency graph refresh on every relevant push: a push pulls the delta and **rebuilds the whole graph for the repo** (§3.5) — full rebuild is simpler *and* correct (an in-place per-file invalidation misses cross-file dependents from unchanged callers), and it is mechanical + off the pinned view so it never disturbs a live meeting. A reconcile at meeting start catches anything missed; each meeting pins to one commit so nothing shifts mid-conversation. (Incremental re-derive is a measured, later optimization.)

**Why mechanical substrate + graph + live grounding (the intelligence split).** The substrate and graph are built by parsers and the language server — structure and call/dependency edges are what these extract perfectly, with nothing to hallucinate, re-runnable on every change. The expensive, always-correct intelligence lives **at query time**: the agent probes *live, current* code every time it grounds a claim. Parsers draw the structure and the graph; the live agent does the grounding.

**The core build is mechanical — no `query()`, so the SDK-isolation triad doesn't apply to it.** Per CANONICAL §8, the SDK-isolation triad (Doc 00 III.2) guards any pre-meeting `query()` call. The core index/dep-graph build makes **zero** `query()` calls (it's tree-sitter + PageRank + LSP + SQLite writes), so the triad is not needed for it. The **core has no pre-meeting `query()` at all**; if a future pass adds one, that call — and only that call — would need the triad. (The `code_intel` MCP tools run **host-side** in the `code_intel` service — §12.2 — invoked by both the direct-answer wake turn and the Workroom agent; the triad guards only the Workroom agent's own `query()`.)

**What we rejected, so it is never rebuilt:** a semantic estate/knowledge graph *as a source of truth* (stale-by-design) · standing per-area expert agents (staleness + a warming subsystem for no gain) · a standing SCIP pipeline / our own maintained reference graph (the on-demand language server already gives precise navigation with zero freshness burden) · embeddings/vector search (grep beats vector retrieval for agentic code search) · an LLM in the structural/graph build · the **CSV dependency-graph ingest path** (we build the graph from our own LSP/tree-sitter).

---

# 3 · The build

Data flow:
```
GitHub App (auth via Nango)
  → connect → clone (per-tenant encrypted volume, our cloud)
     → exclusions (gitleaks + policy)
        → STRUCTURAL SUBSTRATE (mechanical, no LLM):
             tree-sitter tags + PageRank + LSP call graph
             → file-coverage record (SQLite) + ranked overview
             → dependency/call graph: nodes + typed edges  (SQLite, same per-repo DB)
           → host-side code_intel service: pinned clone + warm LSP + graph
                → serves the read-only tool surface (get_dependents/who_writes/
                  list_entry_points/batch_read/find_references/lookup_referent + grep/read/glob)
                → direct-answer wake turn calls this internal API (~50–100ms hop, no E2B)
                → Workroom (Doc 05) mounts the same read-only surface + mutable bash/git/edit in E2B
freshness: push webhook → pull delta → FULL graph rebuild for the repo (off the pinned view) ;
           reconcile at meeting start ; pin per meeting
prepare-ahead (scaled to repo size): warm the language server ; snapshot for seeding
readiness: file-coverage + graph smoke check gate "ready to join meetings"
           → Readiness enum (connecting|cloning|indexing|ready|not_ready) + coverage_pct + gaps
```

### 3.1 · Connect (access)

The customer clicks "connect" in our UI → we launch the **GitHub App** installation (GitHub requires installs to start from our UI) → they grant access to the chosen repo(s). The App requests exactly **`contents:read` + `metadata:read`** — nothing else. That single read scope also delivers the `push` webhook that powers freshness. Read-only, code and config only, never runtime systems or customer data: minimal access is what lets a customer approve in an afternoon.

**Nango** stores the App credentials and mints the short-lived installation token for each operation (tokens expire ~hourly — mint per operation, never cache, never log). Nango's job is credentials only; it is also our auth layer for every future connector (Slack, Jira, GitLab), so auth is never rewritten per service. All git-host-specific logic (clone, pull, webhook parsing) lives behind one thin **`RepoProvider`** interface — GitHub is the only implementation now; a second host later is a new implementation, zero downstream changes.

### 3.2 · Clone (private infra)

On connect we clone the repo to **our cloud infrastructure** — a per-tenant, encrypted persistent volume (e.g. `/tenants/<tenant>/repos/<repo>/`), one tenant never sharing a volume, process, or index with another. (Provider choice and infra layout are Doc 00's; this doc's requirement is per-tenant encrypted persistent storage with compute next to it.)

Mechanics: standard clone; repos above `[~100k files]` clone **blobless** (`git clone --filter=blob:none`) — structure and full history arrive immediately, file contents fetch lazily — so huge repos clone in minutes and `git blame` still works (we never use *shallow* clones; they break history). **Git LFS: pointers only by default** (LFS blobs are almost never code-intelligence-relevant). **Submodules:** recorded explicitly — a file can be absent because of sparse-checkout *or* an uninitialized submodule, and the coverage record distinguishes the two so "complete" never silently lies. After the first clone we only ever **pull deltas** — never re-clone; update cost is proportional to change, not repo size.

Two hard rules: we never push to origin, and **the canonical copy never executes repository code** — no installs, no hooks, no tests. Only the disposable per-meeting sandbox (Doc 05's, seeded from this copy) ever runs the repo's code.

### 3.3 · Exclusions (secrets and noise)

After clone and after every delta pull, **gitleaks** scans the changed files. Its hits plus policy globs — `.env*`, keys/keystores, `node_modules/`, `vendor/`, `linguist-generated` paths — form the repo's **exclusion set**. Excluded paths **stay on disk** (the language server may need a vendored type to resolve first-party code) but are **never indexed, never in the graph, never in any result, never copied into a meeting sandbox, never logged**; detected secret values are additionally redacted at the read path. (The honest boundary: "never cloned" is impossible — secrets live in git history objects. The enforceable guarantee is *excluded from everything the agent, the meeting, or a log ever sees*.) The substrate parse and the graph build run on the *post-exclusion* tree, so a secret can never leak into an artifact.

### 3.4 · The structural substrate + the dependency graph (mechanical — no LLM, always-fresh)

For every non-excluded source file we parse with **tree-sitter** using each language's tag queries to extract declarations, then rank importance with the **aider-style PageRank** over the tag-reference graph (a function used by twenty places outranks a private helper). No LLM anywhere (verifiable from logs: zero model calls). The parse is incremental — a changed file re-parses in milliseconds; unchanged files (keyed by hash) are never touched. Grammarless languages are **flagged** in the coverage record and stay covered by the file tree + live search.

Three mechanical outputs, all stored in one **SQLite DB per repo** (the aider pattern; more robust than a flat JSON manifest under concurrent webhook updates — **not** the deferred Postgres `map_*` pipeline):

- **The file-coverage record** — one row per file: `{path, status: indexed | flagged, flag_reason: unsupported-language | generated | too-large | excluded | submodule-uninitialized}`. **Invariant: `indexed + flagged` accounts for every tracked file. Nothing is ever silently absent.**
- **The ranked overview** — the compact, token-budgeted "table of contents + neighborhoods": areas → files → key symbols with signatures, importance-ranked, elided to `[~a few thousand tokens]`. Injected at workroom session start so the agent begins oriented.
- **The dependency/call graph** — the core value-add. Two simple tables in the same per-repo SQLite DB:

```sql
-- services/code_intel/graph.sql   (per-repo SQLite DB; NOT the deferred Postgres map_* pipeline)
CREATE TABLE graph_nodes (
  id         TEXT PRIMARY KEY,   -- canonical symbol id, e.g. "payments/charge.py::ChargeProcessor.charge"
                                 -- TABLE nodes use the fixed form "table::<name>" (e.g. "table::refunds")
                                 -- so who_writes matches on kind=='table' (NOT an id endswith — kills the
                                 -- "...::refunds" method-name collision, CANONICAL §12.6)
  kind       TEXT NOT NULL,      -- function|method|class|route|table|module
  file_path  TEXT NOT NULL,
  line       INTEGER NOT NULL,
  exported   INTEGER NOT NULL DEFAULT 0,   -- 1 = route / public symbol / table (part of the public surface)
  built_at_sha TEXT NOT NULL     -- the commit this node was extracted at (freshness-deference)
);
CREATE TABLE graph_edges (
  source     TEXT NOT NULL,      -- node id that depends on / calls / writes
  target     TEXT NOT NULL,      -- node id depended upon
  kind       TEXT NOT NULL,      -- calls|imports|reads|writes|extends|implements
  file_path  TEXT NOT NULL,      -- the site of the edge (for the file:line lead)
  line       INTEGER NOT NULL
);
CREATE INDEX graph_edges_target_idx ON graph_edges (target, kind);   -- reverse lookups: dependents, writers
CREATE INDEX graph_edges_source_idx ON graph_edges (source);
CREATE INDEX graph_nodes_file_idx   ON graph_nodes (file_path);      -- file-path lookups (full rebuild replaces rows wholesale)
```

Nodes come from tag extraction (marked `exported` when they are a route / public symbol / table; a **table** node gets the fixed id `table::<name>`); edges come from the **language-server call graph** and tree-sitter imports. We build this directly from the code — **no CSV ingest.** This is the strictly-better version of the sibling repo's approach: they had to ingest a `DBA_DEPENDENCIES` edge-list CSV because they don't hold the code; we do, so the graph is a free by-product of the substrate and carries zero freshness burden. On every relevant push we **rebuild the whole per-repo graph** (drop + re-extract all rows), not an in-place per-file `DELETE WHERE file_path=?` — the incremental path silently misses cross-file dependents whose *unchanged* caller now points at a changed symbol; full rebuild is both simpler and correct, is purely mechanical, and runs off the pinned view so no live meeting sees the swap (§3.6).

### 3.5 · How the agent accesses the code (native + the `code_intel` server, advertised not forced)

The clone and the substrate live host-side in the `code_intel` service. The read-only tool surface (the graph tools + `find_references` + `lookup_referent` + `batch_read`, alongside native `grep`/`read`/`glob`) is served **host-side** and reached by the direct-answer wake turn over the internal API (§12.2). The **mutable** native tools — `bash`/`git`/`edit` — exist **only** in the Workroom's E2B sandbox (Doc 05), seeded from the canonical copy at `meeting.pinned_sha`, where real code changes happen. `propose_change` is a host-side in-process tool (CANONICAL §11.7), never a sandbox tool.

**The `code_intel` MCP server (the in-meeting grounding surface).** The graph is wrapped as a **fresh, connection-bound, in-memory SDK MCP server**, minted per query from a **factory** (SDK MCP servers are connection-bound — never share one instance across concurrent workers or reuse it after a transport closes; store factories, mint per-query). The graph tables are read from the per-repo SQLite DB (or held in a small in-memory adjacency loaded from it) — no live model calls:

```python
# services/code_intel/map_mcp.py
from claude_agent_sdk import create_sdk_mcp_server, tool

def make_code_intel_server(graph: DependencyGraph, lsp: LanguageServerPool, overview: Overview):
    """FACTORY, not an instance. `graph`/`overview` are immutable + shared (loaded from the
    per-repo SQLite DB); `lsp` is the warm HOST-SIDE language-server pool. Only the cheap
    wrapper is rebuilt per query. Callers store `lambda: make_code_intel_server(...)` and
    resolve one fresh instance at the query chokepoint. Runs host-side (§12.2); the
    direct-answer wake turn calls it over the code_intel internal API. Native grep/read/glob
    run where the agent runs; bash/git/edit are sandbox-only (Workroom)."""
    fwd, rev = _adjacency(graph)   # forward: who I depend on; reverse: who depends on me

    @tool("get_dependents", "Transitive REVERSE-dependency set of a symbol OR table — 'what breaks if we change this?'. Returns the top-N caller/importer/writer symbol ids (ranked by PageRank) with file:line, plus truncated_count. A LEAD; confirm each cited line live.", {"symbol": str, "limit": int})
    async def get_dependents(args):
        limit = args.get("limit", 50)                                # default top-50 by PageRank
        deps = _closure(rev, args["symbol"], graph)
        ranked = _rank_by_pagerank(deps, graph)
        return _text({"symbol": args["symbol"], "dependents": ranked[:limit],
                      "truncated_count": max(0, len(ranked) - limit)})

    @tool("who_writes", "Every symbol that WRITES a given table, with file:line — the write-path blast radius for a schema change. EXACT (tag 'resolved') only on supported (lang,ORM): Django ORM / SQLAlchemy / Rails ActiveRecord; a labeled 'lower-bound' everywhere else — never a silent exact.", {"table": str})
    async def who_writes(args):
        node_id = f"table::{args['table']}"                          # canonical table node id
        writers = [{"symbol": e.source, "at": f"{e.file_path}:{e.line}"} for e in graph.edges
                   if graph.node(e.target).kind == "table" and e.target == node_id
                   and e.kind in ("writes", "read_write")]
        tier = _who_writes_tier(graph, args["table"])               # exact-supported | symbol-exact | search-only
        return _text({"table": args["table"], "writers": writers,
                      "confidence": "resolved" if tier == "exact-supported" else "lower-bound"})

    @tool("shares_table", "Which INDEPENDENT modules/services both touch a given table — the hidden cross-module coupling a meeting misses ('two services on one DB table'). Distinct from who_writes (write-path) and get_dependents (symbol-callers): this is cross-module CO-ACCESS. A graph query over existing nodes/edges — no new substrate. Same honesty tiering as who_writes: 'resolved' on supported (lang,ORM), a labeled 'lower-bound' elsewhere — never a silent exact.", {"table": str})
    async def shares_table(args):
        node_id = f"table::{args['table']}"                          # canonical table node id
        # every symbol with a read/write/read_write edge into the table, grouped to its owning
        # module/service (by top-level package / service dir) — the "who else is on this table" set
        touchers = [{"symbol": e.source, "module": _module_of(e.source, graph),
                     "access": e.kind, "at": f"{e.file_path}:{e.line}"} for e in graph.edges
                    if e.target == node_id and graph.node(e.target).kind == "table"
                    and e.kind in ("reads", "writes", "read_write")]
        modules = _distinct_modules(touchers)                        # the independent modules/services co-accessing it
        tier = _who_writes_tier(graph, args["table"])               # reuse the who_writes support matrix
        return _text({"table": args["table"], "modules": modules, "touchers": touchers,
                      "shared": len(modules) > 1,                    # >1 independent module = the dangerous coupling
                      "confidence": "resolved" if tier == "exact-supported" else "lower-bound"})

    @tool("list_entry_points", "Graph roots — nodes with no incoming edges (routes/jobs/handlers/top-level entry points).", {})
    async def list_entry_points(args):
        return _text({"entry_points": _graph_roots(graph)})

    @tool("owner", "Who owns a path — the natural 'who would know this?' meeting question. CODEOWNERS is authoritative (tagged 'resolved'); recent `git blame` on the path is a HINT fallback (tagged 'lower-bound') when no CODEOWNERS rule matches. Mechanical, read-only — no model call.", {"path": str})
    async def owner(args):
        rule = _codeowners_match(args["path"])                       # longest-matching CODEOWNERS pattern, or None
        if rule:
            return _text({"path": args["path"], "owners": rule.owners,
                          "source": "CODEOWNERS", "confidence": "resolved"})
        blame = _recent_blame_authors(args["path"])                  # top recent authors as a hint fallback
        return _text({"path": args["path"], "owners": blame,
                      "source": "git-blame", "confidence": "lower-bound"})

    @tool("find_references", "Exact symbol references via the warm HOST-SIDE language server (Serena/solid-lsp). Tagged 'resolved'; on timeout/unsupported language falls back to grep tagged 'lower-bound'. Not run in the sandbox — no warm LSP there.", {"symbol": str})
    async def find_references(args):
        return _text(await lsp.references(args["symbol"]))           # {refs:[file:line], confidence}

    @tool("lookup_referent", "Deterministic (no LLM) map from a meeting term to what it refers to — a graph node id, a core-overview area name, or None. Resolves 'the refunds table' / 'checkout' to a concrete anchor the other tools take.", {"term": str})
    async def lookup_referent(args):
        return _text(_resolve_referent(args["term"], graph, overview))   # {"node_id"|"area"|None}

    @tool("batch_read", "Read up to [N=10] related files in ONE call — internally parallel, partial-failure-tolerant. A grounded answer usually needs 3–6 related files; this batches them into one round-trip, not N. Returns one compact block; a file that fails comes back as {path, error} without aborting the rest.", {"paths": list, "max_lines_per_file": int})
    async def batch_read(args):
        paths = args["paths"][:10]                                   # hard cap N=10 per call
        cap = args.get("max_lines_per_file", 150)                    # default 150 lines/file
        # read all requested files concurrently on the pinned clone; a per-file failure
        # (missing/excluded/binary) is captured as {path, error}, never raised — the batch
        # always returns whatever it could read, as one compact block.
        results = await asyncio.gather(*[_read_one(p, cap) for p in paths])
        return _text({"files": results})   # each: {"path","text"} on success | {"path","error"} on failure

    return create_sdk_mcp_server(name="code_intel", version="1.0.0",
                                 tools=[get_dependents, who_writes, shares_table,
                                        list_entry_points, owner, find_references,
                                        lookup_referent, batch_read])
```

The `code_intel` tools the in-meeting agent sees, served host-side, mounted alongside native read-only `grep`/`read`/`glob` (mutable `bash`/`git`/`edit` exist only in the Workroom sandbox):

| tool | answers |
|---|---|
| `get_dependents(symbol\|table, limit=50)` | "what calls `chargeCard` / what breaks if I change this table or symbol?" — top-50 by PageRank + `truncated_count` |
| `who_writes(table)` | "what writes the `payments` table?" — `resolved` on supported (lang,ORM) stacks, `lower-bound` elsewhere (never silent exact) |
| `shares_table(table)` | "which independent modules/services both touch this table?" — the hidden cross-module coupling a meeting misses; same tiering/labels as `who_writes` (`resolved` on supported (lang,ORM), `lower-bound` elsewhere) |
| `list_entry_points()` | "where does this system get triggered?" |
| `owner(path)` | "who owns this?" — CODEOWNERS authoritative (`resolved`), recent `git blame` a hint fallback (`lower-bound`) |
| `find_references(symbol)` | exact references via the warm host-side language server; `resolved`, or grep-fallback `lower-bound` |
| `lookup_referent(term)` | "the refunds table" / "checkout" → a concrete `{node_id \| area \| None}` (deterministic, no LLM) |
| `batch_read(paths, max_lines_per_file=150)` | "read these 3–6 related files at once" — a **sibling to native single `read`** (keep both); one round-trip for the several files a grounded answer usually needs |

**`shares_table` — the hidden-coupling detector (catch what the meeting misses).** `who_writes` answers the write-path ("what writes this table"); `get_dependents` answers symbol-callers ("what calls this function"). `shares_table` answers the third, orthogonal question that *is* the "catch what the meeting misses" value prop: **which independent modules or services both touch one table** — two services quietly coupled through a shared DB table, the blast radius a schema change discussion overlooks precisely because nothing in the code *calls* across the boundary. It is **mechanical — a graph query over the existing `graph_nodes`/`graph_edges` (every `reads`/`writes`/`read_write` edge into the `table::<name>` node, grouped to its owning module), no new substrate, no model call.** It carries the **same `who_writes` support-matrix tiering and honesty labels**: `resolved` only on supported (lang, ORM) stacks, a labeled `lower-bound` everywhere else, never a silent exact; every toucher returned as a `file:line` lead the agent confirms live (deference rule 1).

**`batch_read` — one round-trip for the files an answer needs.** A grounded in-meeting answer usually reads **3–6 related files** (the cited call site, its callers, the schema, the config). Doing that with single `read` is N sequential tool round-trips; `batch_read(paths)` fetches up to `[N=10]` files in **one** call, reading them internally in parallel and tolerating partial failure (a missing/excluded/binary file returns `{path, error}` and never aborts the batch), returning **one compact block**. It is a *sibling* to native single `read`, not a replacement — the agent uses single `read` for a one-file confirm and `batch_read` when it already knows the handful of files it needs — and it directly serves the ~1–2s target by collapsing N round-trips to one.

Every one of these returns *leads with `file:line`* — deference rule (1) means the agent then reads the current file and cites *that*. `alwaysLoad:true` on this MCP server (Doc 00 III.8 — else an intermittent tool-less first turn). The **`code_intel` tools run host-side in the `code_intel` service** (§12.2), invoked by **both** the direct-answer wake turn (Doc 04, over the internal API — no E2B) and the Workroom (Doc 05). The core build is mechanical (no `query()`, so no SDK-isolation triad — §2.4); the triad guards only the Workroom agent's own `query()`. The deferred agentic-map tools (`get_capability`, `search_capabilities`, `get_flow`) are **not** in the core (CANONICAL §7); they return at Expansion.

**The one precision instrument: `find_references`, a warm host-side language server.** Grep finds *text* (same-named symbols false-positive; dynamic dispatch missed); the language server resolves the *actual symbol*. For answering questions, grep + reasoning usually suffices; the server earns its place when the agent **edits, refactors, or checks impact** — where a missed reference is a wrong change. It runs **warm inside the host-side `code_intel` service** (a sandbox has no warm LSP — §12.2), exposed to the agent as the `find_references` tool; there is **no in-sandbox LSP**. We run it via **Serena/solid-lsp** (adopted OSS managing language-server lifecycles), one server per detected language (pyright, gopls, tsserver, rust-analyzer, …). **Hardening is mandatory:** every call under a hard timeout (`[~3s]`), falling back to grep with an honest `lower-bound` tag; hung servers restart; the agent never blocks on a dead server. References into uninstalled third-party deps are labeled `external-references-not-resolved`, never dropped. *(Claude Code ships a native LSP capability that overlaps Serena; unverified, so this doc freezes Serena, with a gated convergence-watch spike.)* The same host-side language server is the source of the graph's `calls`/`extends`/`implements` edges at build time.

**Within-meeting tool-result reuse (a per-meeting cache).** The same file and symbol get asked about repeatedly across a meeting, so identical `grep` / `read` / `batch_read` / `get_dependents` / `who_writes` calls recur. A simple **per-meeting in-memory cache keyed on `(tool, args)`** returns the stored result for a repeat call instead of re-running it — cutting redundant round-trips and reinforcing the ~1–2s target. It is **invalidated on a repo change** (the push webhook / mid-meeting "repo advanced" notice, §3.6): any cached result is dropped when the pinned view's underlying files move, so freshness deference (rule 2) is never traded away for the cache. Scope is the single meeting; it dies at meeting-end teardown.

**The honesty contract (mechanical, on everything the layer returns):** every location cited `file:line`; language-server-resolved results tagged `resolved`; search-derived results tagged `lower-bound`, naming what they might miss (dynamic dispatch, config wiring, external types); **graph-derived leads tagged as leads, confirmed against live code before any claim** (deference rule 1); "not found" always means *not found by this method in this scope*; no result claims a completeness ("these are ALL the callers") its method can't back. A confidently-wrong answer in a live meeting is the one unforgivable failure.

### 3.6 · Freshness, sessions, and preparing ahead

**On every relevant push:** the webhook is signature-validated, then enqueued durably and **deduplicated by delivery-GUID + commit SHA** → pull the delta → **rebuild the whole per-repo graph** (drop + re-extract all `graph_nodes`/`graph_edges`, stamp the new `built_at_sha`). Full rebuild replaces the buggy in-place `DELETE WHERE file_path=?` invalidation — that path silently misses cross-file dependents whose *unchanged* caller now points at a changed symbol — and is simpler *and* correct; it is purely mechanical and runs **off the pinned view**, so it never disturbs a live meeting. A **force-push, a parser/grammar upgrade, or a large change-set forces the full rebuild** (the small-delta case already rebuilds too — the rebuild is unconditional for V0; incremental re-derive is a measured, later optimization). Target: `[~10s]` push-to-queryable on a pilot-scale repo.

**At meeting start — the load-bearing backstop:** GitHub does **not** auto-retry failed webhook deliveries, so the session opens with a **reconcile** (`ls-remote` vs our tip); any drift is pulled and the graph rebuilt, *before* readiness is confirmed. Then the session **pins to one commit** — the default branch's tip, or the **PR head** for a meeting about a PR — written to `meetings.pinned_sha` (CANONICAL §11.1).

**During the meeting:** `code_intel` **answers at `meeting.pinned_sha`** (§12.2). Because pushes trigger a full rebuild, the service **retains the graph at each active meeting's pinned SHA** — a light per-repo SQLite retention keyed on the set of currently-pinned SHAs, **GC'd when a SHA is no longer pinned by any live meeting**. The pinned view never mutates mid-meeting, so citations, in-flight edits, and `built_at_sha` stamps stay mutually consistent; a mid-meeting push emits a **"repo advanced N commits" notice** (Doc 04 decides whether to surface it) but the meeting keeps answering at its pinned SHA. On a sandbox re-provision (Workroom recycle), the sandbox **re-seeds at `meeting.pinned_sha`**, not at HEAD. If a graph node's `built_at_sha` is behind the pinned commit for a file the agent is about to cite, deference rule (2) fires: re-read live.

**Preparing ahead (how any-scale stays fast).** Repos are connected long before meetings, so heavy work runs ahead, **scaled to repo size**: every repo gets clone + substrate + graph at connect and a full rebuild on push. Repos above `[~500k LOC]` *also* get their language server(s) **kept warm**. Sandbox seeding uses **prebuilt snapshots** (the production agent-cold-start pattern), not clone-on-demand — so even a huge monorepo answers its first query warm, in seconds. Search is **ripgrep-only for V0**; a **Zoekt** trigram index (for multi-million-file monorepos where live ripgrep can exceed the meeting) is **Expansion**, wired when a giant-monorepo customer lands (CANONICAL §12.6). All of this is preparation, never architecture: capability is identical at every size.

### 3.7 · Readiness (the gate — emits the canonical enum)

A repo may not join meetings until the layer verifies itself. Doc 01 emits the **canonical `Readiness` enum** (CANONICAL §1.5), which Doc 08's connect page renders and Doc 04 consumes:

```python
# libs/contracts — the canonical shape (see CANONICAL §1.5); do not re-describe elsewhere
Readiness = Literal["connecting", "cloning", "indexing", "ready", "not_ready"]  # + coverage_pct: float, gaps: list[str]
```

The states map to the build: `connecting` (GitHub App / Nango token handshake) → `cloning` (§3.2) → `indexing` (the single mechanical build phase: tree-sitter + PageRank + LSP + dependency graph — there is **no** separate `mapping` state, the agentic map is deferred) → `ready` / `not_ready`. The gate (CANONICAL §12.6) checks:
- **100% files classified** — the SQLite coverage record accounts for every tracked file (`indexed + flagged == git ls-files`); nothing silently absent;
- **100% parse on exact-supported files** — every non-flagged file in an exact-supported (language, ORM) stack parses cleanly (**excluding `generated` / `vendor`**, which are legitimately flagged);
- **capability tiers recorded** — each area/stack carries its `who_writes` tier (exact-supported / symbol-exact / search-only) so the honesty labels are pre-computed, not guessed at query time;
- **graph smoke check** — a sample of known symbols each resolve to the correct `file:line` through the live path; `get_dependents` and `who_writes` return the expected callers/writers for a sample of known symbols/tables;
- **`indexed_at` + pinned SHA** — the readiness record stamps `indexed_at` and is **tied to the commit SHA it was built at** (so readiness is never claimed for a stale build).

`ready` requires all of the above. **`coverage_pct` is reported, not a gate** — a deterministic, non-LLM read (§3.7.1) surfaces how much of the tracked surface is indexed and lists the specific `gaps` for honesty, but the ratio itself no longer blocks a join (a repo that is 100% classified with its gaps honestly labeled is joinable). Otherwise the state is `not_ready` with the specific `gaps` — never a silent partial join. All checks run at first build and re-run per push against the newly-rebuilt graph.

#### 3.7.1 · The deterministic coverage read (no LLM)

`coverage_pct` + `gaps` are computed by a pure, deterministic function over the file-coverage record and the graph — no model call, same inputs always yield the same result:

```python
# services/code_intel/coverage.py
def compute_coverage(coverage_rows: list[FileCoverage], graph: DependencyGraph) -> tuple[float, list[str]]:
    """coverage_pct = fraction of tracked files that are indexed (not flagged).
    gaps = the honest blind-spot list: flagged areas by reason + exported graph nodes
    with no resolved edges (grammarless / external-only). Pure + deterministic."""
    indexed = [r for r in coverage_rows if r.status == "indexed"]
    total = len(coverage_rows)
    coverage_pct = (len(indexed) / total) if total else 1.0

    gaps: list[str] = []
    for reason, rows in _group_by_flag_reason(coverage_rows):
        gaps.append(f"{len(rows)} files flagged: {reason}")   # e.g. "312 files flagged: unsupported-language"
    # exported symbols/routes/tables the graph could resolve NO edge for = weak spots
    resolved_targets = {e.target for e in graph.edges} | {e.source for e in graph.edges}
    orphan_exported = [n for n in graph.nodes if n.exported and n.id not in resolved_targets]
    if orphan_exported:
        gaps.append(f"{len(orphan_exported)} exported symbols/tables with no resolved dependency edge")
    return coverage_pct, gaps
```

This is **warning-only** for graph orphans (they never fail a build — grammarless and external-only symbols legitimately have no first-party edges); file-completeness is the hard gate. The `gaps` list also feeds Proxy's own honesty ("I've indexed checkout and refunds fully; the `legacy/` COBOL area is grammarless — let me grep it live").

## 3.8 · Build steps (in this order — each step ends in something provable)
1. **Connection.** Register the GitHub App (`contents:read` + `metadata:read`); wire Nango to hold credentials and mint per-operation tokens; build the thin `RepoProvider` interface (connect / clone / pull / onPush). *Provable: connect a real repo and fetch one known file byte-identical to GitHub.*
2. **The clone service.** Blobless clone into the per-tenant path; delta pulls thereafter; Git LFS pointers-only; submodule state recorded. *Provable: file set == `git ls-files`; `git blame` resolves on a large repo.*
3. **Exclusions.** gitleaks + policy globs → the exclusion set; read-path redaction. *Provable: a planted fake secret never appears in any output or the graph.*
4. **The structural substrate + dependency graph.** tree-sitter tag extraction → symbol locations + `exported` flags; aider PageRank → the ranked overview; the SQLite file-coverage record; the `graph_nodes`/`graph_edges` tables built from the LSP call graph + tree-sitter imports. No LLM. *Provable: overview matches real structure; `indexed + flagged == ls-files`; a known symbol returns correct `file:line`; `get_dependents` and `who_writes` resolve on a known symbol/table.*
5. **The `code_intel` MCP tools.** The host-side factory-per-query server (`get_dependents`, `who_writes`, `shares_table`, `list_entry_points`, `owner`, `find_references`, `lookup_referent`, plus `batch_read` as a sibling to native single `read`), reading the per-repo SQLite graph + the warm host-side LSP, mounted alongside grep/read/glob with `alwaysLoad`; the per-meeting `(tool, args)` result cache, invalidated on push. *Provable: the direct-answer path resolves "who writes `payments`?" in one host-side `who_writes` call (~50–100ms, no E2B), then cites the live line, tagged `resolved` on a Django repo and `lower-bound` on an unsupported stack; `shares_table("payments")` returns >1 independent module co-accessing a shared table (each a `file:line` lead) tagged with the same tier as `who_writes`; `owner("payments/refund.py")` returns the CODEOWNERS owners tagged `resolved`, and a path with no matching CODEOWNERS rule falls back to recent `git blame` authors tagged `lower-bound`; `lookup_referent("the refunds table")` returns `table::refunds`; `batch_read` returns 6 files in one call with a missing file surfaced as `{path, error}` (batch not aborted); a repeat identical call is served from cache and dropped after a push; a fresh server instance is minted per concurrent worker (no shared transport).*
6. **Coverage read + readiness.** The deterministic `compute_coverage` → `coverage_pct` + `gaps` (**reported, not the gate**); the readiness gate (100% classified + 100% parse on exact-supported ex generated/vendor + capability tiers + `indexed_at` + pinned SHA) emitting the canonical `Readiness` enum. *Provable: a repo missing a required-parse file shows `not_ready` + the specific gaps, never a silent partial; a 100%-classified repo with honestly-labeled gaps is `ready` regardless of `coverage_pct`; the coverage ratio is reproducible from the same inputs; the connect page renders all five states.*
7. **Freshness.** Webhook receiver (signature-validated, GUID+SHA-deduped, durable queue) → delta pull → **full per-repo graph rebuild (drop + re-extract all rows), off the pinned view** → atomic pointer swap; `ls-remote` reconcile at meeting start; per-meeting SHA pinning with retention of active-meeting SHAs (GC when unpinned); force-push / parser-upgrade / large-change-set force the rebuild. *Provable: a real push is queryable in ~10s with the whole graph consistent (a cross-file dependent from an unchanged caller is now correct — the case the old per-file invalidation missed); a mid-meeting push notifies without mutating the pinned view, which still answers at `meeting.pinned_sha`.*
8. **Precise navigation.** Serena/solid-lsp integration, **warm host-side** (not in-sandbox); language detection → per-language servers; hard timeout + grep fallback on every call; exposed as `find_references`. *Provable: `find_references` on a known symbol returns the real sites tagged `resolved`; a killed server degrades to a labeled `lower-bound`, never a hang.*
9. **Prepare-ahead + seeding.** Warm the language server on connect/push; produce the snapshot the per-meeting sandbox seeds from (at `meeting.pinned_sha`). Search is ripgrep-only (Zoekt = Expansion). *Provable: on a large repo, the first precise query at meeting start returns warm, in seconds.*

## 3.9 · What the core actually looks like (illustrative, not a format spec)
The ranked overview (substrate) is areas → load-bearing symbols with signatures. The dependency graph answers the impact questions:
```
DEPENDENCY GRAPH — acme/checkout @ 41d3f2a

who_writes("refunds") →
  payments/refund.py:88   RefundProcessor.issue        [writes]
  payments/reconcile.py:140  ReconcileJob.settle       [read_write]
  jobs/nightly.py:52      nightly_reconcile             [writes]     [LEADS — confirm live]

get_dependents("payments/refund.py::RefundProcessor.issue") →
  api/routes.py:212       refund()  (route POST /v1/refunds)         [calls]
  jobs/nightly.py:52      nightly_reconcile                          [calls]  [LEADS — confirm live]

shares_table("refunds") →   shared=true (2 independent services on one table)
  payments/     RefundProcessor.issue @ refund.py:88                [writes]
  billing/      InvoiceJob.reconcile  @ billing/sync.py:61          [reads]   [LEADS — confirm live]

list_entry_points() →
  api/routes.py:212  POST /v1/refunds · jobs/nightly.py:52 nightly-reconcile (cron) · refund.created (queue consumer)
```
Enough that the agent reads it and *knows where to go and what breaks*; nothing that claims meaning it didn't trace, and every lead re-confirmed against current code before it becomes an answer.

# 4 · Key variables

**Any scale.** Capability is constant; only time and preparation vary. The agent always works on the relevant slice (the overview + graph orient it; it never loads the repo); the substrate and graph are built ahead per push. A genuinely hard trace on a giant repo may take longer — Doc 05 surfaces it async — but it never fails or loses capability. We never respond to scale by dropping features; we respond with more preparation.

**Any language.** Full experience = tree-sitter grammar (substrate + graph nodes) + language server (precision + graph edges) — true for all mainstream languages. Grammar but no server: overview + search + node extraction work; precision and `calls` edges fall back to grep/imports, tagged `lower-bound`. Neither: files flagged in the coverage record; file tree + search still cover them. Every tier is visible in the result tags; degraded is never disguised as exact.

**`who_writes` support matrix (honesty, not scope-cut — CANONICAL §12.6).** Any repo is answered; the *precision* of table-write blast-radius is tiered and always labeled:
- **Tier-1 exact-supported** = **Django ORM · SQLAlchemy · Rails ActiveRecord** (the §11.12 spike targets) → the writers set is `resolved` (exact).
- **Tier-2 symbol-exact** = the table symbol resolves but writes can't be proven exhaustively → a `lower-bound` (a proven floor, may miss dynamic/ORM-magic writes).
- **Tier-3 search-only** = no ORM model resolvable → grep-derived `lower-bound`.

An unsupported (language, ORM) stack **never** emits an exact `who_writes` — it is **always** a labeled `lower-bound`, **never a silent wrong-exact**. Framing: *any repo answered; table-write blast-radius exact on supported (lang, ORM), labeled lower-bound elsewhere, never silent wrong-exact.* The tier is pre-computed at index time (§3.7) and carried on the result.

**Latency targets (defaults; pin during build):** oriented lookup (one graph call + one grep) ~1–2s · a host-side `code_intel` API call on the direct-answer path ~50–100ms · `get_dependents`/`who_writes`/`list_entry_points`/`lookup_referent` from SQLite/in-memory sub-100ms · warm language-server (`find_references`) query sub-second to `[~2s]` · first query at meeting start on a prepared large repo ≤ `[~2s]` · substrate + graph push-to-fresh `[~10s]` (full rebuild, pilot scale). Search is ripgrep; giant-monorepo trigram indexing (Zoekt) is Expansion.

**Cost targets.** The **entire core is zero model calls** — substrate, dependency graph, and coverage are all mechanical (tree-sitter + PageRank + LSP + SQLite). The only compute cost is parsing and language-server processing, not model tokens. This keeps the whole code-intelligence layer effectively out of the per-meeting model-cost envelope (Doc 00 PART XIII) — the deep spend lives in the Workroom (Doc 05), not the index. (The deferred agentic map adds cheap-model calls; see Expansion.)

**Failure behavior (never dead-end, never lie):** language server hangs → timeout → grep fallback tagged `lower-bound` · graph node stale vs pinned commit → deference rule (2), re-read live · missed webhook → caught at meeting-start reconcile · force-push → safe full rebuild · unparseable/mid-edit file → valid spans index, broken span flagged, search covers it · App uninstalled → clone, index, graph, and snapshots hard-deleted (target `[15 min]`), never a stale ghost copy.

**Security posture:** least-privilege read-only scopes · per-tenant isolation, encrypted at rest, `tenant_id` scoping on stored artifacts · tokens minted per-operation, never cached or logged · secrets excluded from index/graph/results/sandbox/logs, values redacted at read · the core build is mechanical (no `query()`, so no SDK-isolation triad — §2.4); the `code_intel` tools run **host-side** (§12.2, read-only), the SDK-isolation triad guarding only the Workroom agent's own sandboxed `query()` · canonical copy never executes repo code — only Doc 05's disposable sandbox does. (SOC 2 / VPC compliance is designed-for, delivered later; out of scope here.)

**Tunable defaults (pin before build, tune with measurements):** blobless threshold `[~100k files]` · prepare-ahead (warm-LSP) threshold `[~500k LOC]` · language-server timeout `[~3s]` · substrate + graph freshness target `[~10s]` (full rebuild) · overview budget `[~few-thousand tokens]` · `coverage_pct` — **reported, not a gate** (readiness = 100% classified + 100% parse on exact-supported ex generated/vendor) · uninstall-deletion `[15 min]` · `get_dependents` `limit=50` (top-N PageRank + `truncated_count`) · `batch_read` max files/call `[~10]` and `max_lines_per_file` `[150]` · per-meeting tool-result cache scope = one meeting, invalidated on push.

---

**The stack:** GitHub App + **Nango** (auth) · `git`/`gh` (clone/pull) · **gitleaks** (exclusions) · **tree-sitter** + tag queries and **aider-style PageRank** (structural substrate) · **SQLite** (per-repo file-coverage record + dependency graph — one DB per repo; **schema is code-managed, not Alembic** — CANONICAL §12.12) · **Claude Agent SDK** `create_sdk_mcp_server` (host-side factory-per-query `code_intel` tools) · **ripgrep** (search; agent-native, the only V0 search backend) · **Serena/solid-lsp** (language server, warm **host-side**; source of graph edges + the `find_references` tool) · MCP/workspace mounting per Doc 05. Explicitly **not** used / **cut**: **Zoekt** (Expansion — giant-monorepo trigram index only) · SCIP pipelines · embeddings/vector DBs · universal-ctags (fallback-only) · in-sandbox LSP · LLMs in the structural/graph build · the **CSV dependency-graph ingest** (we build the graph from our own LSP/tree-sitter) · in-place per-file graph invalidation (full rebuild instead). Code lives under **`services/code_intel`** (CANONICAL §9 — not `libs/code_intel`).

*One correct interaction, end to end:* a company connects `acme/checkout`. Minutes later: copy held, substrate + dependency graph built (nodes + typed edges, mechanical, zero model calls), coverage green, readiness `ready`. In the "refunds refactor" meeting a PM asks **"if we change the `refunds` table, what breaks?"** — the agent calls `who_writes("refunds")` and `get_dependents` (one round-trip each, sub-100ms), gets the writer + caller leads with `file:line`, opens the handful of cited files in the pinned clone in **one `batch_read` call** (not one `read` per file), confirms the lines live, and answers with the exact write sites and blast radius — each cited `file:line`, tagged `resolved`, at the pinned SHA — in ~1–2s. A push lands mid-meeting → "repo advanced 1 commit," the agent's pinned view unchanged (it keeps answering at `meeting.pinned_sha`); the whole per-repo graph rebuilds in the background off the pinned view. Next session reconciles, re-pins, and answers on the freshly-rebuilt graph. At no point did the graph *answer* anything — it only pointed; the code answered, always with a citation, an honesty tag, and the commit it was true at.

---

# 5 · Expansion (post-core) — the agentic knowledge map

*This is the **first expansion seam** off the mechanical core above. It was the largest net-new, least-validated subsystem in the original design, so per CANONICAL §0/§6 it is **deferred out of the core** and sketched here — not fully specified. The core (index + grep + LSP + dependency graph) already delivers fast grounded answers and "what breaks if we change this table." The map adds the one thing the core can't: translating **business vocabulary** ("can we change refunds?") into a prepared trace, so the agent skips even the grep. It sits on top of the core, never replaces it, and stays subordinate to live code (the same three deference rules apply).*

- **Grep-anchored per-directory summaries (Tier 1a).** One dense, greppable-anchor-rich Markdown summary per depth-2 directory, built by a cheap agent fanned out over the repo (deterministic enumeration → `run_with_concurrency`, concurrency-capped, per-unit resumable). Turns the symbol graph into prose the in-meeting agent navigates in one call.
- **The L1/L2 capability map (Tier 1b).** A single cheap agent infers business domains (L1) → capabilities (L2) in the room's vocabulary ("checkout flow," "refunds"), each L2 carrying its surface area (`file_paths`, `endpoints`, `db_objects`, `external_integrations`, `entry_points`). This is the bridge from meeting-words to code — the seam that turns "can we change refunds?" into a one-call resolution.
- **Lazy flow dossiers (Tier 2).** Per-capability end-to-end traces (`ui → controller → logic → storage` with evidence + `tables_touched`), built **on demand** only for the handful of L2s a meeting is about (from the agenda / linked ticket), then cached — never fanned out over the whole repo. The deep "walk me through how refunds works" answer.
- **The `map_runs` / `map_artifacts` / `map_events` Postgres pipeline.** The persistence for the three artifacts above: `*_runs` (build observability + stale-row reaper) + `*_artifacts` (jsonb, one row per unit, partial-unique-indexed for per-unit `ON CONFLICT` upsert + `built_at_sha` freshness) + append-only `*_events` (progress/audit). This is the heavier storage the core deliberately avoids — the core keeps the graph in per-repo SQLite instead.
- **The `get_capability` / `search_capabilities` / `get_flow` MCP tools.** The capability-map + flow-dossier query surface, mounted alongside the core `code_intel` tools when this seam ships (CANONICAL §7: these are **not** in the core `allowed_tools`). `search_capabilities("refunds")` → L2 id; `get_capability(id)` → its surface area; `get_flow(l2_id)` → the layered trace as leads. Each still returns leads confirmed against live code, per the deference rules.
