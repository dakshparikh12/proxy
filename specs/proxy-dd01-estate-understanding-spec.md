# PROXY · DEEP DIVE 01 — The Estate Understanding System

*(formerly "The Pre-Meeting Knowledge System" — renamed because it is not only pre-meeting; it is Proxy's continuously-current model of the company's software, maintained across, during, and after meetings.)*

How Proxy learns a company's software before it joins a meeting, and keeps that knowledge true forever after. **Part A** is the product — what it does and every capability it unlocks, before any implementation detail. **Parts B–C** are the build — the architecture end to end, then piece by piece: what each is, how we wire it in, and how we optimize it, specific enough to implement. **Part D** is scaling. The qualitative acceptance criteria close the document. Code-level implementation is left to Claude Code; this spec fixes *what* each piece is, *how the pieces work together*, and *how each is tuned*.

---

## THE ONE IDEA TO HOLD

We precompute exactly **one** thing — a deep, cited **graph** of the company's code: a real graph of dots (systems, files, functions, tables) joined by labeled, cited lines (which calls which, which shares a database with which). Then we cut that graph into team-sized pieces and give each piece a live expert agent that reads its territory **just-in-time**, the way a sharp engineer greps a repo rather than memorizing it. The graph is the durable skeleton; the *meaning* is generated on demand and never goes stale.

Five systems own this, each responsible for exactly one area. Tools live *inside* the systems; a tool is never the system.

> **Access → Graph → Carve → Experts → Freshness**, over a shared **Coverage Ledger**.

**Three rules hold everywhere and are the only things hard-coded:**
1. **Grade against reality, never confidence.** Every check bottoms out in running the code and observing what happens — never a model asserting it's sure.
2. **Deterministic-first.** Mechanical parsers, deterministic diffs, and algorithms do the bulk at near-zero cost; agents appear only where judgment is irreducible (naming a unit, formulating a question, answering).
3. **Fail closed to abstention.** Any uncertainty becomes a known-unknown, never a guess.

---

## PART A — THE PRODUCT

### What it does, and everything it makes possible

This system produces three things, and every later part of Proxy reads from them.

- **The Estate Graph** — the map. A fine-grained, typed graph of the real code (files, functions, classes, tables, and how they call, import, and share data). Every edge is backed by a citation to the real source (`file:line`) or it does not appear.
- **Per-unit expert agents** — the map is cut into team-sized territories, and each gets one AI agent that is a genuine authority on it. When a meeting touches `checkout`, the `checkout-expert` shows up already knowing its code, owners, and everything it connects to.
- **The Coverage Ledger** — an honest, running list of what we *don't* know yet, each item tagged with the specific person who probably does. This is what lets Proxy say "I'm not certain — Maya would know" instead of guessing.

### The capabilities this unlocks

Because the map is a real graph of typed, cited relationships, Proxy can do things a summarizer never could:

- **"What breaks if we change checkout?"** — answered by walking every line out of the `checkout` dot, across code and org.
- **"Who owns this?"** — answered instantly from ownership edges.
- **"Is that true?"** — answered by going to the exact cited file and reading it live.
- **It knows the edge of its own knowledge** — so it can be trusted to act, because it abstains rather than bluffs.

### It works for any company, at any size

The map is fractal: the same shape at every scale. A two-person startup is one dense cluster; a ten-thousand-person enterprise is that same shape repeated — each team a cluster, the org a smaller graph of team-clusters joined by a few important lines. We never hold "the whole company"; the largest thing we ever hold at once is one team-sized unit and its edges. The identical machinery runs on a solo founder's single repo and on one product-area of a giant enterprise — nothing in it assumes a size.

### It works for any kind of company

Nothing here knows or cares what industry the company is in. It reads code and configuration and builds a graph of whatever is there — a fintech's payment services, a healthcare data pipeline, a game studio's engine modules. The parsers are language-based, not industry-based; the experts are trained on whatever the estate contains. There is no vertical-specific logic anywhere, so a new industry needs zero new code.

### Scope for v0: code only

Repos and their configuration — source, config, migrations, `CODEOWNERS`, and git history for blame. No documents, tickets, or diagrams are ingested in v0. Anything the parsers can't resolve becomes an honest Coverage Ledger entry rather than a guess. This keeps the system simple and lets it stay truthful about exactly what it hasn't mapped.

---

## PART B — HOW IT WORKS

### The whole flow, in one line, then in words

> **Access** gets the code → **Graph** builds the cited map → **Carve** cuts it into units → **Experts** gives each unit a live agent → **Freshness** keeps it all current — and **Coverage** records everything we couldn't resolve.

Read it left to right:

1. **Access** connects one team's repositories read-only and stamps everything it pulls with who is allowed to see it.
2. **Graph** turns raw code into the structural, *cited* bones of the map using fast parsers only — no AI, so it costs pennies and can re-run constantly. It then verifies that map against ground truth at zero model cost.
3. **Carve** cuts the finished graph into team-sized units small enough for one agent to master, and names and assigns an owner to each.
4. **Experts** gives each unit its own named agent — not by loading files into a prompt, but by handing it a compact, ranked *map of where to look* and letting it read the live source on demand.
5. **Freshness** runs forever: it re-reads only what changed on every PR, and folds every meeting's decisions back into the map, so the map compounds human insight on top of technical insight.

Two things about this flow are the whole design. First, **only the structure is precomputed; the meaning is just-in-time.** The graph (cheap, durable, and the one thing agentic search can't reconstruct on the fly) is stored; the plain-English understanding is read live by the expert at the moment it's needed. Second, **the systems don't call each other through a fixed script — they hand off through shared state.** Graph writes nodes/edges; Carve reads them and writes units; Experts read units and answer; Freshness watches for change and updates the graph. Add a new extractor or a new expert and the behavior grows with no rewiring.

### What "a graph" actually means here — the schema

A graph is nodes (the dots) and typed edges (the labeled lines). We never say two things are vaguely "connected"; every edge has a type and a citation.

```
node(id, type[service|module|file|function|class|table|config],
     name, path, unit_id, permission_ref, confidence, freshness_at)

edge(src, dst, type[calls|imports|shares_table|api_contract|owns|data_flow],
     citation "billing/client.py:412"  -- MANDATORY, or the edge doesn't exist,
     confidence, extractor[scip|stack_graphs|tree_sitter|coupling|cpg], freshness_at)
```

`shares_table` — two services on one database table — is the strongest and most dangerous hidden coupling, and exactly the kind of thing a wrong decision in a meeting misses. It almost never shows up as a code call; it lives in migrations, ORM models, and connection config, which is why recovering it is a first-class job (Part C, System 2, Tier 3).

### The graph is real; the database is boring on purpose

We store the graph as tables in one Postgres instance — `node`, `edge`, `permission`, `coverage` — because Postgres is cheap, proven, and something every engineer already knows. We walk it as a graph with recursive queries (follow edges outward from a node to compute blast-radius), and if we ever need heavier graph operations we switch on Apache AGE, an openCypher extension *inside the same Postgres*, so we never run or pay for a second database. `pgvector` is present but unused in v0. (We deliberately do **not** adopt an embedded graph DB — Kùzu was archived Oct 2025.)

### Why understanding is just-in-time, not stored

For code, pre-computed meaning is a liability: an index of proprietary code is an attack surface, it goes stale the moment a file changes, and — per Anthropic's own results — agentic search *outperforms* it. So we store only the structural graph and let the expert read its unit live and cite what it just read. There are no stored "pages" to go stale, which deletes an entire re-verification subsystem the original design carried.

---

## PART C — THE BUILD

Every piece, exactly. For each: first the **overview** (what it is and why it's here, in plain terms), then the **technical specifics** (how we wire it in and tune it). Dense, but built to be understood. Code-level detail is Claude Code's job; this fixes the parts that aren't in its training data — how the pieces plug together and how each is optimized.

### System 1 — Access: getting the code, safely and minimally

**Overview.** A company connects one team's repositories and config. We ask for the least possible access — read-only code and configuration, never production systems, customer data, or live monitoring. This single choice is what lets a team say yes in an afternoon instead of after a three-month security review, and it's a promise we can state plainly.

**Nango — the key-holder.**
*What it is.* An open-source service that manages OAuth tokens — the digital keys that let our app read the company's GitHub/GitLab. We self-host it (free), so those keys live entirely inside our infrastructure.
*How we wire and optimize it.* Every read (a repo pull, a config fetch) goes through Nango, so credentials are never scattered across our code. The same key-holder is reused by the action layer later: when Proxy needs to *do* something in a meeting, a gateway mints a short-lived, narrowly-scoped token from Nango's stored key, so the sandbox doing the work never holds a real secret. One integration, used in two places.

**Permission stamps — how one shared map stays private.**
*What it is.* Everything we ingest is stamped with who is allowed to see it, copied directly from the source system's own permissions — so a person only ever sees what they could already open themselves, and one shared graph is safe for every team.
*How we wire and optimize it.* The stamp is a row `permission(node_prefix, allowed_principals[], source_system, synced_at)`, where `allowed_principals` is resolved from repo collaborators, team membership, and `CODEOWNERS`. We stamp at **repo/directory granularity** — matching how hosts actually grant access; per-file ACLs are expensive and fictional. Every knowledge query is filtered with a list-overlap check (`current_user_principals && allowed_principals`). We re-pull permissions on a change-driven cadence via webhooks, and enforce revocation **at read time, never cached** — the moment someone loses access on the host, the next query returns nothing. That correctness point is what makes enterprises comfortable.

**What we pull, and what we skip.** Source, config, migrations, `CODEOWNERS`, and enough git history for blame. We skip secrets (`.env`, keystores), binaries, vendored deps (`node_modules`, `vendor/`), and generated artifacts. Large repos are shallow-cloned and thereafter fetched incrementally.

*Interface:* `SourceConnector.pull(repo) → {files, config, permissions}`.

### System 2 — Graph: turning code into a cited map, mechanically

**Overview.** When code arrives, we build the map with fast parsing tools — no AI, so it costs pennies and can re-run constantly. Four tiers each do one job, and every edge they produce carries a citation to the real source or it does not exist. This is the stage people usually gloss over, so here is exactly what each tool is and how it plugs in.

**Tier 1 · tree-sitter — read each file into a precise structure.**
*What it is.* A parser that turns a source file into a syntax tree — a precise, machine-readable outline of "here is a function, here's a call to another function." Fast, incremental, ~40+ languages via `tree-sitter-language-pack` (130+).
*How we wire and optimize it.* We run `tags.scm` queries against each file's tree to pull declarations and call sites, producing `file`/`function`/`class` nodes and provisional `calls`/`imports` edges. Because it's incremental, one changed file re-parses in milliseconds — this is what makes staying current cheap.

**Tier 2 · SCIP — make the edges exact and cited.**
*What it is.* A precise code-indexing standard (the technology behind Sourcegraph's "go to definition"). Language indexers (`scip-python`, `scip-typescript`, `scip-java`, `rust-analyzer`, `scip-clang`, `scip-dotnet`) emit an index of every symbol's exact definition and every use, with file-and-line precision.
*How we wire and optimize it.* tree-sitter can see "a call to something named `charge`" but not *which* `charge`. We load the SCIP index to resolve that — upgrading a guessed edge into a resolved, cited edge (`calls billing/client.py:charge`). Every resolved edge carries that citation, which is what lets the whole product cite its sources. On very large monorepos we index per-package (SCIP indexes compose) and run indexing in the customer's own CI, which respects their secrets and uses their compute.

**Tier 3 · stack-graphs — resolve names with no build environment.**
*What it is.* GitHub's open-source framework (Rust) that resolves name bindings incrementally, per-file, without a build — the technology behind GitHub's precise code navigation.
*How we wire and optimize it.* SCIP is precise but some indexers need a working build (`scip-java` needs compiler plugins, `scip-clang` needs `compile_commands.json`). Where a build isn't available, stack-graphs resolves edges anyway. The full resolution ladder is the honesty guarantee: **SCIP → stack-graphs → tree-sitter+heuristic → known-unknown, never a guessed edge.**

**Tier 3 (coupling) · config / migration / ORM parsers — recover `shares_table` and dynamic dispatch.**
*What it is.* Per-ecosystem parsers that read the files where hidden coupling actually lives.
*How we wire and optimize it.* Migration files name tables; ORM models map class→table (`schema.prisma`, Django/Alembic migrations, ActiveRecord, SQLAlchemy, JPA entities); connection configs name which service points at which database. We parse these to emit `table` nodes and cited `shares_table` edges. In code-only scope this is the *only* place the most dangerous coupling appears, so it is first-class, not a fallback.

**Tier 4 · Joern / CPG — data-flow depth, optional.**
*What it is.* The security-grade Code Property Graph (data-flow, control-flow, taint).
*How we wire and optimize it.* For repos that need value-flow-precise blast-radius (not just "A calls B"), Joern adds data-flow edges, exposed to experts as a tool via the `codebadger` MCP pattern. Off by default; on per-repo where it pays. It sits behind the same `GraphExtractor` seam, so turning it on changes nothing downstream.

**What static parsing can't see, and how we recover it honestly.** Config-driven dispatch (code that picks a service from a config value) doesn't appear in the syntax tree. We recover it two ways: (1) parse the config/infra files, which literally name the endpoints and databases; (2) run the code on synthetic inputs in the sandbox and watch what it reaches. We never touch a live system. Whatever is still unresolved becomes a Coverage Ledger entry — flagged, never guessed.

**Verification — three free, deterministic layers.** The map is judged on being *correct*, and the ground truth is the code's own behavior, never a model's opinion. Cheapest first:
- *Differential agreement (free).* SCIP, stack-graphs, and tree-sitter have already run; where all agree on an edge → high confidence; where they disagree → flag. Every edge gets its `confidence` from this. It costs nothing extra because the extractors already ran.
- *Property invariants (free).* Deterministic checks: every cited `file:line` provably contains its symbol; no dangling nodes; every node has a permission ref.
- *Execution-as-oracle (compute-only).* Run the repo's own test suite under the language tracer (`python -m trace`/`coverage.py`, `c8`/`nyc`, JaCoCo, `go test -cover`); the *fired* edges are ground truth → measure the static graph's precision (no spurious edges) and recall (no missing edges). Zero LLM spend.

**How we optimize verification.** It runs cheapest-first and mostly free; the execution-oracle rung runs sampled and on merged PRs (whose tests we're re-running anyway), so continuous verification adds no dedicated cost.

**Visualization — the verify-and-iterate surface.** The graph lives as Postgres tables but renders on demand as an interactive graph via **Cytoscape.js** (Sigma.js is the WebGL escape hatch for a full-estate overview). A subgraph query (a unit + blast-radius neighbors) becomes Cytoscape elements colored by confidence — green (extractors agree), yellow (disagreement), red (unverified) — with known-unknowns lit as gaps and the execution-oracle diff overlaid. You watch the graph get more correct as you iterate, and the same surface is the DD06 adoption explorer.

*Interfaces:* `GraphExtractor.extract(repo) → {nodes, edges}`; `CouplingExtractor.extract(repo) → edges`; both behind one swappable seam.

### System 3 — Carve: cut the map into pieces one agent can master

**Overview.** A whole company is far too much for one agent to know well. So we cut the graph into units — team-sized territories of roughly 10–40 files, small enough that everything about a unit fits in one agent's working memory. The cutter finds the natural clusters: files that talk to each other far more than to anything outside.

**Leiden community detection — how we run it.**
*What it is.* A well-established algorithm for finding tightly-connected clusters in a graph (`leidenalg`).
*How we wire and optimize it, in order:*
1. **Build a weighted graph** (`igraph`): one node per file, each edge weighted by type (`shares_table` heaviest, `imports` lighter) — so "coupled" means structurally connected.
2. **Excise the super-hubs first.** Shared utilities (logging, auth helpers) connect to everything and smear every cluster into mush. Compute degree centrality and lift the top ~1–2% into a pinned platform layer *before* clustering. This one step is the difference between clean units and useless ones.
3. **Run Leiden with a resolution knob γ**, tuned so the median unit lands in a token-bounded band (~100–120K tokens, so a Haiku-class expert fits with room for tool results). Multi-level Leiden gives nested levels for free: file → unit → domain → org.
4. **Enforce the band.** A unit too big re-runs at higher γ to split; fragments too small merge into their strongest-connected neighbor.
5. **Pin stable IDs.** On every re-scan, match new clusters to old by overlap (shared files ÷ total); above ~50%, the unit keeps its old ID — so last week's `checkout` is still `checkout`, and experts don't reshuffle underneath.

**The carve agent — the judgment part.**
*What it is.* A small Claude Agent SDK pass that does the parts an algorithm can't: naming, boundary sanity, ownership.
*How we wire and optimize it.* It names each unit from its top-PageRank symbols and path prefixes, refines obviously-wrong boundaries, and assigns an owner by majority vote of `CODEOWNERS` + git-blame over the unit's files. A human owner sees the proposed cut once and can rename/merge/split in a couple of minutes; after that it's automatic and stable.

```
SYSTEM PROMPT — CARVE AGENT (ILLUSTRATIVE)
You are naming and sanity-checking one proposed unit of a codebase.
Inputs: the unit's files, its top-ranked symbols, its in/out edges, CODEOWNERS + blame.
- Give the unit a short, human name from what it actually does (a service/domain noun).
- If two proposed units are obviously one thing, say MERGE; if one is obviously two, say SPLIT.
- Assign an owner: the majority owner across CODEOWNERS and recent blame.
Do NOT invent structure. Only reorganize what the edges already imply.
```

*Interface:* `Carver.carve(graph) → units[{id, name, file_ids[], owner, level, token_estimate}]`.

### System 4 — Experts: give each unit a live agent that knows where to look

**Overview.** For each unit we stand up one named agent — `checkout-expert`, `billing-expert`. "Training" is **not** loading files into a prompt or writing pages. It is handing the agent a compact, ranked **map of where to look**, warm in cache, and letting it read the live source on demand. It knows the terrain cold and digs for detail just-in-time.

**The expert pack — what's cached.** `{ repo_map, graph_slice, tool_manifest, unit_meta }`. The `graph_slice` is the unit's nodes plus its edges in/out and blast-radius neighbors and owners; `unit_meta` is id/name/owner/freshness.

**The repo-map — the "knows where to look" mechanism.**
*What it is.* A ranked, elided, token-budgeted overview of the unit — the thing that lets the expert orient instantly and skip ~80% of exploratory searches.
*How we wire and optimize it.* From the unit's files: tree-sitter extracts defs/refs → build a reference graph → **PageRank** ranks the load-bearing symbols (a function called by twenty others outranks a private helper) → render elided (signatures + structure only, via `grep_ast`/TreeContext) → **binary-search-fill to the token budget** (aider's technique — pack the *most important* content until full). A personalization vector biases the ranking toward the meeting's current topic so the expert walks in already oriented.

**Context management.** Prompt caching (1h cache write 2×, reads ~10%) makes a large pack cheap to hold and instant to reuse; a compaction step summarizes/evicts the lowest-rank content on overflow; and the KNOWN/LOOKUP/WORK split is the natural boundary — what's *in* the pack answers instantly, what's ranked below the line is a live lookup, heavy compute is async.

**The answer loop.** An Agent SDK session (think → call a tool → read → repeat). Its tools follow the industry-standard layered pattern: **ripgrep scoped to its own unit** (instant, because Carve bounded the search space — no Zoekt index needed in v0), **SCIP/stack-graphs find-refs** for symbol precision, **read**, and the **E2B sandbox** to run things. It cites the live `file:line` it just read, and **abstains** (logging a known-unknown) when it can't verify.

```
SYSTEM PROMPT — UNIT EXPERT (ILLUSTRATIVE)
You are the authority on the unit {unit_name}. Your map (ranked overview + graph slice)
is in context; the live source is one tool call away.
- Answer from your map when you can (KNOWN). Otherwise do ONE targeted live read (LOOKUP)
  with ripgrep/find-refs and cite the exact file:line. Real work goes to the sandbox (WORK).
- Every claim carries a live file:line citation, or you don't make it.
- If you cannot verify something, say so and log a known-unknown. Never guess. Silence is valid.
```

**How we optimize it.** Experts are built **lazily** — only on first reference in a meeting or agenda pre-warm, never the whole estate (this is what keeps enterprise cost sane). Search runs in the expert's own sub-context so exploration never pollutes the answer, and up to ~8 tool calls fire in parallel per turn (8 greps ≈ 1 grep of latency).

*Interface:* `Expert.ask(question) → {answer, citations[]} | abstain`.

### System 5 — Freshness: stay current, and learn from meetings

**Overview.** Code changes constantly, and meetings produce knowledge that never lived in any file. Freshness handles both, cheaply — and it is not a pre-meeting warm-up; it is Proxy's living model of the company, always current.

**CocoIndex — the incremental engine.**
*What it is.* A Rust incremental engine: you declare `target = F(source)` and it keeps the target in sync, recomputing only the delta, memoized on `hash(input)+hash(code)`, with native tree-sitter.
*How we wire and optimize it.* Every PR/push webhook reprocesses only changed files, updates the affected graph edges, and invalidates the touched units' repo-maps (re-warmed lazily on next reference). A one-line edit re-maps one unit, not the estate. Because understanding is JIT, **nothing goes stale** — maintenance is just keeping the cheap structural graph current. CocoIndex sits behind the `Ingestor` seam with a webhook+tree-sitter-incremental fallback, so we're never locked in.

**Continuous verification.** A merged PR ships with tests, so on merge we re-run the *changed* tests under the tracer and re-verify the affected edges' confidence. Verification is continuous, not a one-time setup.

**Learning from meetings.** When a meeting decides something, that decision writes back onto the affected graph nodes as a new cited fact, with the meeting as its source — a Postgres UPSERT with `ON CONFLICT` that **supersedes-with-timestamp** (both truths kept, the new one stamped, re-verify queued), never a silent overwrite. Recurring meetings pre-seed from their predecessor via a `continues` edge, so a meeting series becomes a queryable thread and open questions carry forward.

*Interface:* `Ingestor.on_change(event) → {updated nodes, edges}, invalidated unit_ids`.

### Shared — the Coverage Ledger: what happens when we don't know

**Overview.** Every knowledge system is judged on how it handles ignorance. Ours is built to never guess silently. This is what makes Proxy trustworthy enough to act.

**How it works.** Five conditions raise a flag: a reference that leads nowhere, an edge that failed its grade, a config that contradicts the code, two extractors that disagree on a boundary, or a belief that can't be cited. On any flag, the system runs a **self-resolution ladder — make it right, don't guess:**
1. **Infer harder** — widen the search, read one hop further out.
2. **Synthetic execution** — run the unresolved path in the sandbox on synthetic inputs and *witness* the real target (the same execution-as-oracle mechanism as verification). This resolves the config-driven and dynamic-dispatch edges static parsing can't see.
3. **Differential** — does another extractor resolve it?

Only if all three fail does an agent write a **well-formed open question** with a computed `likely_owner` into the **open-questions document**. `likely_owner` is the overlap of `CODEOWNERS`, git-blame, and (later) ticket-resolvers, so the question goes to the specific person and asks a specific thing. Until answered, the expert abstains on that point. In v0 the questions land in a document; wiring the actual asking into Slack/enterprise connectivity is deferred — the buildable value now is the self-resolution ladder plus generating the *right specific question* and the *right owner*.

*Row:* `coverage(unit, description, why_matters, likely_owner, resolver, re_verify_at)`.

---

## PART D — SCALING AND OPTIMIZATION

**Cost.** Extraction is compute-only (no AI). The only AI spend is the tiny carve agent and lazy per-unit expert warming, on demand and prompt-cached. Verification is zero-model. No vector embeddings in v0. The two biggest line items in the original design — a global understanding pass and stale-page re-verification — are deleted, not optimized. A design partner's first month lands in the low tens of dollars.

**Latency.** Blast-radius is a precomputed graph walk, and the top-K hot nodes are precomputed so the most common query ("what breaks if we change X") is a cache hit. Catches are warm-cached + prefetched. Real agent-trace data shows search speed is <1% of wall-clock, so the levers are the repo-map as a pre-loaded mental model, ~8 parallel tool calls per turn, expert search in its own sub-context, and compact `file:line`+signature results — not raw search speed.

**Freshness at scale.** Incremental delta means a million-file monorepo costs the same to keep current as a small one — proportional to change, not size. Huge monorepos index per-package in the customer's CI.

**Downstream gating.** Per-edge confidence flows into DD04: a low-confidence edge can never feed a hard-floor interruption; at most it prompts a "let me verify" offer. DD01's measured quality governs how boldly the proactive engine speaks.

**Shadow-first.** Built and verified in shadow; an expert isn't trusted live until its unit clears a graph-health bar (% edges verified, % test-covered, closed-PR exam) — which is also the DD06 self-test the buyer watches.

**Seams are the scaling lever.** Every extractor sits behind `GraphExtractor`, every coupling parser behind `CouplingExtractor`, ingestion behind `Ingestor`, storage behind one seam, search behind `CodeSearch`. A new language, ecosystem, or better tool is one adapter — never a change to Carve, Experts, or storage. And because everything degrades to honest known-unknowns, the identical pipeline runs at every scale and in every vertical.

---

## DONE WHEN

Every edge carries a live-source citation and nothing uncited survives; the permission filter provably works, including immediately after revocation; the graph verifies against its own test-trace oracle at zero model cost; the carve yields token-bounded units with stable IDs that don't reshuffle on re-scan; each unit has a named expert that answers with live citations and abstains at its edge; on any realistically messy repo the Coverage Ledger is non-empty and every item names an owner and a resolver; a PR delta re-checks only what changed; a decision made in a meeting appears as a cited fact on the right node afterward; and the identical pipeline runs on a one-repo startup and one product-area of a large enterprise, with nothing industry-specific anywhere.

---

## ACCEPTANCE CRITERIA (qualitative)

The clear bars each system must clear — what "correct" means, stated as capabilities, not exhaustive test cases. These are the spine that unit tests and, later, real-data evals hang off.

**Access — must:** pull only read-only code+config, never write scopes or production; stamp every node with source-derived permissions at repo/directory granularity; return nothing a user couldn't already open and reflect a revocation within one webhook cycle; reuse the key-holder to mint scoped short-lived tokens for the sandbox.

**Graph — must:** produce typed edges (`calls`/`imports`/`shares_table`/`api_contract`/`owns`), each cited to a real `file:line`; resolve via the ladder and never emit a guessed edge; recover `shares_table`/dynamic dispatch from config/migration/ORM, not just calls; attach a confidence to every edge from differential agreement; verify itself at zero model cost (differential + property invariants + execution-as-oracle); render on demand as an interactive graph colored by confidence with known-unknowns lit as gaps; degrade gracefully on any language/repo to honest known-unknowns.

**Carve — must:** produce token-bounded units (~10–40 files) that follow natural clustering, with super-hubs excised into a platform layer; keep stable IDs across re-scans; name every unit and assign an owner; let a human rename/merge/split the cut in one pass.

**Experts — must:** exist as one named agent per unit, built lazily; hold a token-budget-filled, PageRank-ranked, prompt-cached map plus graph slice and tool manifest; answer from the map (KNOWN), one live read (LOOKUP), or the sandbox (WORK), always citing a live `file:line`; abstain and log a known-unknown when it can't verify — never fabricate.

**Freshness — must:** reprocess only changed files on a PR and invalidate only affected unit maps; keep nothing stale because understanding is read live; re-verify affected edges from a merged PR's own tests; write meeting decisions back as cited facts under supersede-with-timestamp; keep cost proportional to change, not repo size.

**Coverage Ledger — must:** attempt inference → synthetic execution → differential before asking a human; never guess — an unresolved item becomes a well-formed open question with a named likely owner; keep the expert abstaining until answered; be non-empty on any messy repo, every item naming an owner and resolver.

**System-wide — must:** keep every stated fact cited or abstained; run the identical pipeline at any size and in any vertical with nothing industry-specific in the code; grade every check against reality (execution), never against a model's confidence.
