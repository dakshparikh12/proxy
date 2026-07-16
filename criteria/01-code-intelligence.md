# Acceptance Criteria — Doc 01 · Code Intelligence
### Generated from `Doc 01` by `criteria/GENERATOR.md` (fresh context). Every criterion traces to a spec clause, is arbiter-tagged, names its grader + threshold + validity mutant + estate. The build loop stops only when ALL are green at pass^k on EVERY estate.

## Blocking pre-conditions (Phase-0 ratify — the loop cannot start until these are numbers/decisions)
- **Pin the tunables** (§4): blobless threshold `[~100k files]` · prepare-ahead `[~500k LOC]` · LS timeout `[~3s]` · freshness `[~10s]` · overview budget `[~few-thousand tokens]` · uninstall deletion `[15min]` · Zoekt p95 `[~300ms]` · warm-nav `[~2s]` · rename-recall floor per language · pass^k `k` · smoke-sample `N`.
- **Resolve the store conflict (spec-lint finding):** §3.4 uses **SQLite per repo**; `AGENTS.md` mandates **Postgres + pgvector behind the Store adapter as the only DB access**. Decision required: exempt the ephemeral code-index as a declared non-Store artifact, OR route it through the adapter. Blocks D-series graders.
- **Author the estate matrix + golden keys** (pin SHAs): `single`, `monorepo`(≥1M), `polyglot`, `deep-history`, `many-tiny-files`, `no-codeowners`, `unsupported-lang`, forks `secrets`/`live`/`tenant`.
- **Invariants this layer defends** (drives the adversarial set): self-host-credentials, zero-copy-of-secrets, permission-at-read, tenant-isolation, truth-is-live (freshness/pinning), cited-or-abstain (envelope).

---

## A · Connection & auth (§3.1)
### A1 · Least-privilege scope
- Traces: §3.1 · Arbiter: [unit-fixture] · Estate: fixture
- GIVEN the GitHub App manifest WHEN inspected THEN requested scopes == exactly {contents:read, metadata:read}, nothing else.
- Grader: deterministic (manifest assert) · Threshold: exact set · Validity mutant: add `pull_requests:write` → red.
### A2 · No write path exists
- Traces: §3.1 · Arbiter: [adversarial] · Estate: fixture
- GIVEN the RepoProvider surface WHEN statically analyzed THEN no method can push/write to origin.
- Grader: deterministic (static scan) · Threshold: 0 write-capable methods · Validity mutant: add `push()` → red.
### A3 · Real connect returns byte-exact content
- Traces: §3.1 · Arbiter: [eval-realrepo] · Estate: single
- GIVEN a real public repo connected via Nango+App WHEN a known file is fetched at SHA THEN bytes == GitHub's bytes at that SHA.
- Grader: deterministic (byte diff) · Threshold: exact, pass^k · Validity mutant: fetch wrong SHA → red.
### A4 · Same path, any language
- Traces: §3.1/§4 · Arbiter: [eval-realrepo] · Estate: polyglot (Py+Go+TS)
- GIVEN three repos of different languages WHEN each connects THEN all authenticate+clone through the identical code path, 0 per-repo branches.
- Grader: deterministic (code-path trace + config diff) · Threshold: 0 branches · Validity mutant: add a per-language branch → red.
### A5 · Tokens minted per-op, never persisted
- Traces: §3.1 · Arbiter: [adversarial] · Estate: fixture
- GIVEN a sequence of git operations WHEN each runs THEN a fresh token is minted per op, and no token appears on disk, in the index, or in any log.
- Grader: deterministic (mint-count + token-pattern scan of disk/logs) · Threshold: 0 leaked, 1 mint/op · Validity mutant: cache a token → red.
### A6 · RepoProvider contract
- Traces: §3.1 · Arbiter: [contract] · Estate: fixture
- GIVEN the RepoProvider interface WHEN exercised THEN it holds the shape {connect, clone→path, pull→delta, onPush(handler)} and a second host impl needs 0 downstream change.
- Grader: schema/contract test · Threshold: shape holds · Validity mutant: change a signature → downstream test red.

## B · Holding the code (§3.2)
### B1 · Full history / blame survives the clone strategy
- Traces: §3.2 · Arbiter: [eval-realrepo] · Estate: monorepo (blobless)
- GIVEN a blobless-cloned large repo WHEN `git blame` runs on every source file THEN it resolves for all of them.
- Grader: deterministic (blame over ls-files) · Threshold: 100% · Validity mutant: switch to shallow clone → blame breaks → red.
### B2 · Clone strategy by size
- Traces: §3.2 · Arbiter: [unit-fixture] · Estate: fixture
- GIVEN repo size WHEN cloning THEN >`[100k files]` uses `--filter=blob:none`, below uses standard clone.
- Grader: deterministic (clone-cmd assert) · Threshold: correct branch · Validity mutant: always-standard → OOM/slow flag on large fixture → red.
### B3 · Deltas only after first clone
- Traces: §3.2 · Arbiter: [eval-realrepo] · Estate: live
- GIVEN a connected repo WHEN it updates THEN we `git fetch` the delta and never re-clone.
- Grader: deterministic (build-log: 0 re-clones) · Threshold: 0 re-clones · Validity mutant: re-clone on update → red.
### B4 · Tenant isolation
- Traces: §3.2/§4-security · Arbiter: [adversarial] · Estate: tenant (2 tenants)
- GIVEN tenant A and tenant B WHEN B attempts to reach A's volume/process/index THEN it is denied; no shared path/process/db.
- Grader: deterministic (cross-tenant probe) · Threshold: 0 cross-tenant reads · Validity mutant: shared volume path → red.
### B5 · Canonical copy never executes repo code
- Traces: §3.2 · Arbiter: [adversarial] · Estate: fixture (repo with a malicious postinstall/build hook)
- GIVEN the canonical working copy WHEN indexed/maintained THEN no `npm install`/build/test/hook of the repo ever executes on it.
- Grader: deterministic (process audit + honeypot-file-not-touched) · Threshold: 0 executions · Validity mutant: run install on canonical copy → honeypot fires → red.

## C · Secret & vendored exclusion (§3.3)
### C1 · Planted secrets never surface
- Traces: §3.3 · Arbiter: [eval-realrepo] · Estate: secrets (20 planted)
- GIVEN a repo with planted secrets WHEN connected THEN 0 of them appear in the index, any tool result, the overview, a sandbox seed, or a log.
- Grader: deterministic (scan all outputs vs planted manifest) · Threshold: 100% excluded / 0 leaked, pass^k · Validity mutant: disable gitleaks stage → red.
### C2 · Excluded-on-disk, invisible-to-agent
- Traces: §3.3 · Arbiter: [unit-fixture] · Estate: fixture
- GIVEN an excluded path WHEN queried THEN it stays on disk (LS can resolve vendored types) but never indexed/returned/rendered.
- Grader: deterministic · Threshold: on-disk yes, in-output no · Validity mutant: index a vendored path → red.
### C3 · Read-path redaction defeats odd encodings
- Traces: §3.3 · Arbiter: [adversarial] · Estate: secrets (split-line / base64 secrets)
- GIVEN a secret split across lines or oddly encoded WHEN any raw file-read surfaces it THEN the value is redacted at the read path.
- Grader: deterministic (read-path scan) · Threshold: 0 raw values · Validity mutant: remove read-path redaction → red.
### C4 · Exclusion is total & disjoint (property)
- Traces: §3.3 · Arbiter: [unit-property] · Estate: synthetic
- GIVEN any generated path matching an exclusion rule (`.env*`, node_modules/, vendor/, linguist-generated, gitleaks-hit) WHEN indexed THEN it never appears in search/overview output.
- Grader: Hypothesis property · Threshold: no counterexample · Validity mutant: drop a glob → counterexample found → red.

## D · Index build (§3.4)
### D1 · Coverage accounts for every file
- Traces: §3.4 · Arbiter: [eval-realrepo] · Estate: all
- GIVEN a built index WHEN the coverage record is summed THEN `indexed + flagged == git ls-files (minus excluded)`; 0 files silently absent.
- Grader: deterministic (reconciliation) · Threshold: exact, every estate · Validity mutant: drop a file from indexing without flagging → red.
### D2 · Known symbol → correct file:line
- Traces: §3.4 · Arbiter: [eval-realrepo] · Estate: single (golden symbol set)
- GIVEN a known symbol WHEN looked up in the symbol table THEN it returns the correct file:line.
- Grader: deterministic (vs golden) · Threshold: 100% on golden set · Validity mutant: off-by-one line → red.
### D3 · Zero LLM calls in the build
- Traces: §3.4 · Arbiter: [unit-fixture] · Estate: fixture
- GIVEN an index build WHEN it runs THEN 0 calls pass through `src/proxy/llm.py`.
- Grader: deterministic (call-audit) · Threshold: 0 · Validity mutant: inject one llm call → red.
### D4 · Incremental by hash
- Traces: §3.4 · Arbiter: [unit-fixture] · Estate: fixture
- GIVEN a single changed file WHEN reindexed THEN only it re-parses; unchanged files (same hash) are untouched.
- Grader: deterministic (parse-log) · Threshold: exactly the changed set · Validity mutant: full re-parse → red.
### D5 · Completes at scale, coverage holds
- Traces: §3.4 · Arbiter: [eval-realrepo] · Estate: monorepo (≥1M LOC)
- GIVEN a ≥1M-LOC repo WHEN indexed THEN the build completes and D1 still holds (longer time allowed).
- Grader: deterministic · Threshold: D1 exact · Validity mutant: silent truncation at N files → red.
### D6 · Symbol-table schema
- Traces: §3.4 · Arbiter: [contract] · Estate: fixture
- GIVEN any symbol row THEN it validates `{name, kind∈enum, file, line, signature}`.
- Grader: schema · Threshold: 100% valid · Validity mutant: emit a row missing `kind` → red.
### D7 · Partition totality (property)
- Traces: §3.4 · Arbiter: [unit-property] · Estate: synthetic
- GIVEN any synthetic repo WHEN indexed THEN every file has exactly one coverage status (total & disjoint).
- Grader: Hypothesis property · Threshold: no counterexample · Validity mutant: allow a file with 0 statuses → red.
### D8 · Broken file degrades gracefully
- Traces: §3.4/§4-failure · Arbiter: [adversarial] · Estate: fixture (mid-edit/unparseable file)
- GIVEN an unparseable or mid-edit file WHEN indexed THEN its valid spans index, the broken span is flagged, search still covers it, and the build never crashes.
- Grader: deterministic · Threshold: no crash, span flagged · Validity mutant: crash on parse error → red.

## E · Overview (§3.5)
### E1 · Top modules match reality
- Traces: §3.5 · Arbiter: [eval-realrepo] · Estate: single
- GIVEN `get_overview()` WHEN compared to the real directory tree THEN top-level modules match.
- Grader: deterministic (tree match) + G-Eval on organization (κ-gated) · Threshold: ≥[N]% · Validity mutant: shuffle the tree → red.
### E2 · Load-bearing symbols ranked up
- Traces: §3.5 · Arbiter: [eval-realrepo] · Estate: single (golden "load-bearing" set)
- GIVEN the overview WHEN inspected THEN high-PageRank symbols (called by many) appear and private helpers don't dominate.
- Grader: DeepEval G-Eval calibrated (κ≥0.6) · Threshold: ≥[N]% of golden load-bearing set present · Validity mutant: rank by file size instead → red.
### E3 · Budget + no prose
- Traces: §3.5 · Arbiter: [unit-fixture] · Estate: fixture
- GIVEN the overview THEN it is within `[token budget]` and contains 0 LLM-written descriptions (structural content only).
- Grader: deterministic (token count + prose detector) · Threshold: ≤budget, 0 prose · Validity mutant: inject a generated description → red.

## F · Search (§3.6)
### F1 · Search == ripgrep ground truth
- Traces: §3.6 · Arbiter: [eval-realrepo] · Estate: single
- GIVEN a string that exists WHEN searched THEN results == ripgrep ground-truth over the working copy.
- Grader: deterministic (set match) · Threshold: exact · Validity mutant: drop a hit → red.
### F2 · Zoekt latency at scale
- Traces: §3.6/§4 · Arbiter: [latency] · Estate: monorepo (>100k files)
- GIVEN a >100k-file repo WHEN text search runs (N=[100] samples, warm) THEN p95 ≤ `[300ms]`.
- Grader: Langfuse pXX · Threshold: p95 ≤ 300ms, pass^k · Validity mutant: force the ripgrep path at scale → p95 blows → red.
### F3 · "Where is X" spawns no process
- Traces: §3.6 · Arbiter: [unit-fixture] · Estate: fixture
- GIVEN a symbol lookup WHEN served THEN it comes from the symbol table with no subprocess spawned.
- Grader: deterministic (subprocess audit) · Threshold: 0 spawns · Validity mutant: route to ripgrep → red.

## G · Precise navigation (§3.7) — the real-world core
### G1 · Definition resolves, tagged resolved
- Traces: §3.7 · Arbiter: [eval-realrepo] · Estate: single
- GIVEN a known symbol WHEN `find_definition` runs THEN it returns the actual definition, tagged `resolved`.
- Grader: deterministic (vs golden) · Threshold: 100% on golden, pass^k · Validity mutant: return wrong file → red.
### G2 · Reference recall via rename-recompile (THE integration test)
- Traces: §3.7 · Arbiter: [eval-realrepo] · Estate: single, polyglot (per-language)
- GIVEN `[20]` symbols renamed at their definition and the repo recompiled/typechecked THEN every file the compiler now errors in was in `find_references`'s returned set.
- Grader: deterministic (rename → typecheck → compare; recall = caught/total) · Threshold: recall ≥ `[floor]` per language, pass^k · Validity mutant: delete one reference edge → recall < 1 → red.
### G3 · rename_preview == find_references
- Traces: §3.7/§3.8 · Arbiter: [eval-realrepo] · Estate: single
- GIVEN a symbol WHEN both tools run THEN `rename_preview` sites == `find_references` sites.
- Grader: deterministic (set equality) · Threshold: exact · Validity mutant: make rename_preview drop test files → red.
### G4 · Hung server → timeout → tagged fallback
- Traces: §3.7 · Arbiter: [adversarial] · Estate: fixture (injected hang)
- GIVEN a language server that hangs WHEN a nav call is made THEN it times out at `[3s]`, falls back to search, the result is tagged `lower-bound`, the server is restarted, and the agent never blocks.
- Grader: deterministic (fault injection) · Threshold: <timeout+ε, tag=lower-bound, restart observed · Validity mutant: remove the timeout wrapper → hang → red.
### G5 · External refs labeled, not dropped
- Traces: §3.7 · Arbiter: [eval-realrepo] · Estate: single (uninstalled-dep reference)
- GIVEN a reference into an uninstalled third-party type WHEN `find_references` runs THEN it is labeled `external-references-not-resolved`, never silently omitted.
- Grader: deterministic · Threshold: labeled, 0 silent drops · Validity mutant: drop the external ref silently → red.
### G6 · SCIP cache only on SHA match
- Traces: §3.7 · Arbiter: [eval-realrepo] · Estate: monorepo
- GIVEN a precomputed SCIP index WHEN its commit ≠ the session's pinned SHA THEN it is ignored and the live server answers; WHEN it matches THEN it may serve.
- Grader: deterministic (stale-SCIP injection) · Threshold: 0 stale-served results · Validity mutant: serve SCIP regardless of SHA → wrong result → red.

## H · Tool surface & envelope (§3.8)
### H1 · Every result validates the envelope (contract centerpiece)
- Traces: §3.8 · Arbiter: [contract] · Estate: fixture (all tools)
- GIVEN any result from any tool THEN it validates `{results, citations:[file:line…], resolution∈{resolved,lower-bound,not-found-by-<method>}, sha}`.
- Grader: schema / Schemathesis over the tool surface · Threshold: 100%, 0 unlabeled/uncited · Validity mutant: return a result missing `resolution` → red.
### H2 · Never claims completeness
- Traces: §3.8 · Arbiter: [unit-fixture] · Estate: fixture
- GIVEN any nav result THEN it never asserts "these are ALL callers"; the strongest claim is `resolved` for sites found.
- Grader: deterministic (output-language assert) · Threshold: 0 completeness claims · Validity mutant: emit "all references" → red.
### H3 · sha stamped == session pin (property)
- Traces: §3.8/§3.9 · Arbiter: [unit-property] · Estate: fixture
- GIVEN any result during a pinned session THEN its `sha` == the session's pinned commit.
- Grader: Hypothesis property · Threshold: no counterexample · Validity mutant: stamp `HEAD` instead of the pin → red.
### H4 · owner_of schema
- Traces: §3.8/§3.? · Arbiter: [contract] · Estate: fixture
- GIVEN `owner_of(path)` THEN it returns `{owner, source∈{codeowners,blame}, confidence}`; unknown → `no-confident-owner`.
- Grader: schema · Threshold: 100% valid · Validity mutant: return a bare string → red.
### H5 · Ownership correctness
- Traces: §2.3/§3.8 · Arbiter: [eval-realrepo] · Estate: single (has CODEOWNERS) + no-codeowners
- GIVEN a path with a CODEOWNERS entry THEN `owner_of` returns that owner (source=codeowners); with none THEN `no-confident-owner`, never a guess.
- Grader: deterministic (vs CODEOWNERS) · Threshold: 100% · Validity mutant: guess an owner when none exists → red.

## I · Freshness, session, prep (§3.9)
### I1 · Push reflected within freshness target
- Traces: §3.9 · Arbiter: [eval-realrepo] · Estate: live
- GIVEN a push changing a function WHEN the next query runs THEN within `[10s]` it reflects the new code and the old is gone.
- Grader: deterministic (mutate→poll→diff) · Threshold: ≤10s, pass^k · Validity mutant: skip reindex → stale → red.
### I2 · Only changed files re-parse
- Traces: §3.9 · Arbiter: [eval-realrepo] · Estate: live
- GIVEN a push WHEN reindexing THEN only changed files re-parse (build-log).
- Grader: deterministic · Threshold: exactly the delta · Validity mutant: full rebuild → red.
### I3 · Atomic swap under read load (THE race test)
- Traces: §3.9 · Arbiter: [unit-property] · Estate: fixture
- GIVEN continuous reads WHILE a reindex+swap runs THEN every read observes the old-complete OR new-complete index, never a partial one.
- Grader: Hypothesis `RuleBasedStateMachine` (forced interleavings) · Threshold: no partial-read counterexample · Validity mutant: non-atomic pointer flip → partial observed → red.
### I4 · Webhook idempotency & ordering
- Traces: §3.9 · Arbiter: [unit-property] · Estate: fixture
- GIVEN the same push webhook delivered 3× and out of order THEN one consistent index version results, no corruption.
- Grader: Hypothesis property (dup/reorder) · Threshold: no counterexample · Validity mutant: non-idempotent enqueue → duplicate/corrupt → red.
### I5 · Missed-webhook reconcile at meeting start
- Traces: §3.9 · Arbiter: [eval-realrepo] · Estate: live
- GIVEN a missed webhook (simulated drift) WHEN a meeting starts THEN `ls-remote` reconcile detects it and re-indexes the drift before readiness flips.
- Grader: deterministic · Threshold: drift caught pre-ready, pass^k · Validity mutant: skip reconcile → stale join → red.
### I6 · Force-push safe re-derive
- Traces: §3.9/§4-failure · Arbiter: [adversarial] · Estate: live (force-push)
- GIVEN a force-push where the old tip is gone WHEN reindexing THEN the changed set is re-derived against our stored tip (no fast-forward assumption) and the index is correct after.
- Grader: deterministic · Threshold: correct post-state · Validity mutant: assume fast-forward → wrong delta → red.
### I7 · Mid-meeting push doesn't mutate the pin
- Traces: §3.9 · Arbiter: [eval-realrepo] · Estate: live
- GIVEN a pinned meeting session WHEN a push lands mid-meeting THEN the pinned checkout/index is unchanged and a "repo advanced N commits" notice is emitted.
- Grader: deterministic · Threshold: 0 pin mutation, notice emitted · Validity mutant: mutate the pinned view → red.
### I8 · First precise query warm at scale
- Traces: §3.9/§4 · Arbiter: [latency] · Estate: monorepo (prepared)
- GIVEN a prepared ≥1M-LOC repo WHEN the meeting's first precise query runs THEN it returns ≤ `[2s]`.
- Grader: Langfuse pXX · Threshold: ≤2s, pass^k · Validity mutant: skip pre-warm → cold multi-minute → red.

## J · Readiness (§3.10)
### J1 · Ready only when complete + smoke-green
- Traces: §3.10 · Arbiter: [eval-realrepo] · Estate: all
- GIVEN a repo WHEN readiness is evaluated THEN it flips `ready` only after coverage(D1) AND the smoke check pass; a failing repo shows `not-ready` with the specific gaps, and Proxy never joins a not-ready repo.
- Grader: deterministic · Threshold: no ready-with-gap, never join not-ready · Validity mutant: flip ready with a known gap → red.
### J2 · Smoke check resolves sampled symbols
- Traces: §3.10 · Arbiter: [eval-realrepo] · Estate: single
- GIVEN `[N]` known symbols sampled from the table WHEN each is resolved via the lookup path THEN each returns the correct file:line; re-runs on the changed slice on every change.
- Grader: deterministic · Threshold: 100% of sample · Validity mutant: corrupt one symbol's line → red.
### J3 · Uninstall leaves no ghost
- Traces: §3.10/§4-failure · Arbiter: [adversarial] · Estate: single
- GIVEN an App uninstall WHEN it is processed THEN the clone, index, and any snapshots are deleted within `[15min]` and the repo shows `disconnected` — no stale ghost copy.
- Grader: deterministic (store audit) · Threshold: 0 residue ≤15min · Validity mutant: retain the clone → red.

## K · Any scale & diversity (§4)
### K1 · Full suite on small AND ≥1M
- Traces: §4 · Arbiter: [eval-realrepo] · Estate: single + monorepo
- GIVEN both a `[10k-LOC]` and a `[≥1M-LOC]` repo WHEN the full A–J suite runs THEN it passes on both (latency may rise; no capability missing).
- Grader: run all criteria per estate · Threshold: all green, pass^k · Validity mutant: a size branch that drops a capability → red.
### K2 · Diversity matrix
- Traces: §4 · Arbiter: [eval-realrepo] · Estate: polyglot, deep-history, many-tiny-files, no-codeowners, unsupported-lang
- GIVEN each diversity estate WHEN the suite runs THEN it passes or degrades **honestly** (envelope tags correct) on each — never a silent wrong answer.
- Grader: per-estate suite + honesty audit · Threshold: all green or honestly-tagged · Validity mutant: unsupported-lang file returned as `resolved` → red.

## L · Language degradation ladder (§4)
### L1 · Tiered behavior is honest
- Traces: §4 · Arbiter: [eval-realrepo] · Estate: polyglot + unsupported-lang
- GIVEN a language with {grammar+server | grammar-only | neither} THEN respectively {full precision `resolved` | map+search work, nav falls back `lower-bound` | files flagged `unsupported-language`, search still works}; the envelope tag always tells the agent its tier.
- Grader: deterministic (per-tier fixtures) · Threshold: correct tier + tag each · Validity mutant: present a grammar-only nav result as `resolved` → red.

## M · Measurement protocol (governs every [latency])
### M1 · Latency is measured, not asserted
- Traces: §4 · Arbiter: [latency] · Estate: as named per criterion
- Every `[latency]` criterion (F2, I8, warm-nav) is graded as: **N=[100] samples, warm cache, report p95, on pinned hardware, from Langfuse traces**. A single-sample or cold measurement is invalid.
- Grader: Langfuse · Threshold: the criterion's pXX · Validity mutant: measure cold / N=1 → unstable/flapping → protocol rejects it.

## N · Security posture (standing, cross-cutting §4)
### N1 · No token in any log, ever
- Traces: §4-security · Arbiter: [adversarial] · Estate: fixture
- GIVEN all operations run WHEN logs are scanned THEN 0 tokens/secrets appear.
- Grader: deterministic (log scan) · Threshold: 0 · Validity mutant: log a token → red.
### N2 · Encrypted at rest, per-tenant
- Traces: §4-security · Arbiter: [unit-fixture] · Estate: fixture
- GIVEN a tenant volume THEN it is encrypted at rest and not shared across tenants.
- Grader: deterministic (volume assert) · Threshold: encrypted + isolated · Validity mutant: plaintext/shared volume → red.

---

## Global gates (apply to the whole set)
- **pass^k:** every `[eval-realrepo]`/`[latency]` case runs k×; all must be green. Flakiness fails the gate.
- **Grader-validity:** every criterion's named mutant must be caught; a per-section negative build must go red or the gate fails regardless of green.
- **Judge calibration:** every G-Eval/DAG judge (E1, E2) clears Cohen's κ ≥ 0.6 vs a human gold set before it may gate.
- **Coverage:** every §2 promise and every §3 clause maps to ≥1 criterion above; a clause with no criterion is a gap to fill.

## Coverage ledger (spec clause → criteria)
§3.1→A1–A6 · §3.2→B1–B5 · §3.3→C1–C4 · §3.4→D1–D8 · §3.5→E1–E3 · §3.6→F1–F3 · §3.7→G1–G6 · §3.8→H1–H5 · §3.9→I1–I8 · §3.10→J1–J3 · §4-scale→K1–K2 · §4-lang→L1 · §4-latency→M1 · §4-security→N1–N2. **~55 criteria across unit / property / contract / real-repo / adversarial / latency.**
