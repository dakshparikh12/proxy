# PROXY · DEEP DIVE 05

# The Workroom — Universal Hands

How Proxy does a task instead of only talking about it — and proves the result before it says a word. Part A is the product: what the hands deliver, across documents, actions, analysis, and code. Parts B–C are the build: the one agent every request flows through, its tools, how it plans and proves, and how we keep it safe. Part D is how it stays fast, cheap, and durable at any size. Everything else in Proxy is judgment about *what* to do; this is the *doing* — and the *proving*.

---

## THE ONE IDEA TO HOLD

**One general agentic loop, given the right tools, is a universal worker.** This is not a bespoke pipeline of task types — it is a single agent (a Claude Agent SDK loop, the same shape as production coding agents like Claude Code) that receives a thin bundle, **decides its own plan, does the work, and validates it**, using whatever tools fit. Everything Proxy has built is one of those tools: the Meeting Page and The Read, the estate graph and its expert packs, the live connectors, the document skills, and the sandbox. The agent chooses among them dynamically.

The whole design follows from one refusal: **we do not hard-code task categories.** We do not branch on "is this a document, a lookup, or a build." We give the agent a small set of powerful tools and a disposition — *know it → answer; need a fact → read; must produce or do something → act; cheapest correct path; prove everything; stage anything that touches the world* — and let it work out the rest per task. What differs between a race-bug fix, a revenue model, a staged Jira ticket, and a Word document is the *data and the tool*, never the engine.

> **WHAT MAKES ONE MACHINE HONESTLY DO "ANY TASK"**
> Not a giant library of task-specific skills. It's that every task decomposes into the same moves — understand what "done" means, plan how to reach it, do it, and prove it — and a general agent with the right tools runs those moves against anything. Capability rides in **tools, connectors, and skills**, so a new class of task or a new integration is added by *registration*, not engineering. Where no tool exists, the agent says so specifically and files a follow-up. That single property is what lets one workroom span a document, an external action, an analysis, and a code change without a line of per-vertical logic.

---

# PART A — THE PRODUCT

## What the hands do, and why one worker covers everything

The workroom delivers a single promise: Proxy can do essentially any task and prove it — not summarize, not suggest, but build the artifact, run the check, take the action, and hand back evidence. Four things are always true.

- **It handles any kind of work.** Produce a document (Word / PowerPoint / Excel / PDF via Skills), a chart, a spreadsheet model, an interactive calculator, or a mockup; fire an external action through a connector (file a ticket, draft an email, post to Slack, open a PR, trigger a deploy); read a number, run a test, rebuild a model, walk a dependency. New kinds of task need no new engine — they arrive as the same bundle and flow down the same path.
- **It is agnostic to size and vertical.** The identical worker serves a coding meeting, a finance review, a sales call, a design crit, a legal check, and a board discussion. Nothing in it knows what industry it's in — only the retrieved experts and connected systems differ.
- **It always ends in proof.** Nothing is asserted unproven. A result comes back with a receipt — what ran, against which sources, when, and honestly what could *not* be proven.
- **Anything that changes the world is a draft first.** A pull request, a posted message, a filed ticket — each returns as a **staged draft a named human confirms**. Proxy proposes; a human commits.

### The same worker, four very different jobs

- **Coding** — "checkout double-charges under load." The worker writes a stress harness in the sandbox, reproduces the double-charge, writes the fix, re-runs the test — and returns a **staged pull request with the failing-then-passing test as its receipt**. If the race was too flaky to reproduce deterministically, it stages the fix and says so on the receipt.
- **Finance** — "what does next year look like at 9% churn?" It rebuilds the model in the sandbox, runs a Monte-Carlo, and hands back the projection with confidence bands — the point estimate spoken now, the full bands landing in the panel seconds later, both as staged drafts with the exact inputs on the receipt.
- **Sales** — "show them what they'd save." It generates a small working ROI calculator during the call, pre-filled with the customer's own usage numbers where they're attached to the meeting, and returns it as an interactive artifact the account team drives on screen. Where the customer's real data isn't available, it degrades gracefully to a template with sensible defaults, clearly marked — never a fabricated saving. ("File the follow-ups from this call" is the same worker: drafted tickets/emails as a one-tap draft bundle.)
- **Design / docs** — "give me three takes on the settings screen" or "turn this into a one-pager." It generates the artifact from the company's own design tokens (so it looks like the product, not a generic template) or a document skill, flagging exactly which parts aren't yet bound to real components.

### Three product features ride directly on this layer

Because every real job produces a reusable recipe: **Saved Actions** (any completed job becomes a named, parameterized, shareable card — "save that as weekly-metrics-pull" — invocable and schedulable later; a team's library becomes real switching cost); **Dry-run** ("what would you do here?" returns the plan without executing); **Show-your-work** ("where did you get this?" expands any result to its full trace). Suggestions never interrupt a meeting — they wait in the panel or the post-meeting brief.

---

# PART B — HOW IT WORKS

## The machine, end to end

```
TASK BUNDLE in  (from reactive, proactive, an expert consult, or a training probe — all identical)
   {ask · WHO = expert pack id(s) · one starting pointer · flags: identity/permissions · effects · stakes/freshness}
        │
        ▼
~10ms gate at the door: does this need to build/act, or can it be answered/read?  (freshness + effects; no model)
        │
        ▼
ONE AGENT LOOP  (Claude Agent SDK · while the model emits tool calls: act → observe → re-infer)
   tools:  find_experts(query) · read(pointer) · act(spec) · meeting_page(op)
   • load expert pack(s) by WHO → the pack is a SELF-DESCRIBING MAP of where this system's context lives
   • JUST-IN-TIME planning: the agent plans, executes, and replans from feedback — no fixed plan
   • effort EMERGES: answer-from-knowledge → one read → act.  No task buckets.
   • read & act are PROGRAMMATIC (code-execution) → intermediate outputs stay out of context
   • plan-flash before multi-step or side-effecting work
   • FAN-OUT to parallel expert subagents ONLY for breadth-first / blast-radius asks (rare ~15× path)
        │
        ▼
VERIFY  (independent, cheapest-first): deterministic check → HHEM grounding → cross-family judge (high stakes only)
        │
        ▼
RESULT ENVELOPE out  — artifact + receipt (what ran, sources, timestamps, what couldn't be proven)
   • anything side-effecting → STAGED DRAFT a named human approves
        │
        ▼
back onto the Meeting Page (single-writer gate) · delivered on the right surface · >2s → background job + ping
```

**Same hands, different callers.** Four parts of Proxy call the workroom with the identical bundle: a person's spoken/typed ask (Deep Dive 03), a proactive candidate (Deep Dive 04), an expert consult, and an estate-training probe (Deep Dive 01). To the workroom they are indistinguishable — a task, some pointers, an identity, a freshness need. That's what makes it universal: nothing upstream has to know how the hands work, and the hands never have to know who called.

> **ROUTING TO THE RIGHT EXPERT IS RETRIEVAL, NOT A DECISION**
> The bundle already names WHO (retrieved by Deep Dive 03 / the agent's own `find_experts`). Loading that expert's pack gives the agent the system's knowledge *and* a map of where everything else about it lives — check-pointer addresses for live values, code pointers for raw source, connection edges for dependencies. A demand no system owns goes to a fully-capable generalist (all tools, every expert available as a consultant — a real floor, never a shrug). A demand spanning two systems has the primary lead while neighbors are consulted, all sharing one context.

---

# PART C — THE BUILD

## Component 1 — The one agent loop

**Overview.** The worker is a single Claude Agent SDK session running the same loop every production coding agent runs: the model thinks, calls a tool, reads the result, and re-infers, stopping when it emits an answer with no tool call. One flat history, no competing personas. The loop is fixed; adding a tool never changes it. This is the shipped architecture of Claude Code, Cursor, and Codex — a general loop plus the right tools is the universal worker, and we adopt it directly rather than inventing a bespoke pipeline.

**How we wire and optimize it.** It runs on a Sonnet-class model (escalating to Opus only for high-stakes build/verify), event-driven — it exists only while a task is being worked, so it costs nothing between tasks. Its context stays small: it loads the expert pack(s) named in the bundle and fetches everything else **just-in-time via a belief-state BRIEF** — it keeps a checklist of what the task actually needs and fetches (against the pack's map, the graph, or the Page) only the unchecked items, stopping when the list is satisfied, rather than dumping a big context blob and hoping. The loop is capped (`max_turns`, `max_budget_usd`) so a task can't run away. The bundle in and the result out are **immutable, freshness-stamped envelopes** — a stage never edits its input, it emits the next — which is what makes a long job **pausable and resumable at tool-call boundaries** without re-running any tool call (a job spanning past the meeting resumes from the last envelope boundary rather than starting over). The result envelope carries the artifact, the receipt (what ran, sources, timestamps, what couldn't be proven), the audit trace, the status, and the **cost in tokens, dollars, and wall-clock**. Envelopes are rows in the same Postgres as the estate graph — no second store. **Planning is just-in-time** (Component 4): the agent does not receive or follow a hard-coded plan — it plans, executes, and replans from feedback, which is the current standard for dynamic tasks and is what lets one loop serve both a one-line lookup and a multi-hour build.

## Component 2 — The four tools

**Overview.** The agent's entire capability is four tools. Everything Proxy has built is reached through them; the thousands of enterprise experts and connectors are all deferred and discovered on demand, so the prompt never bloats.

- **`find_experts(query)`** — retrieves the matching expert pack(s) from the estate registry (the **Tool Search Tool** / `defer_loading` pattern over Deep Dive 01's private MCP subregistry). Returns pack id(s) or none (→ generalist). This is the router, and it's a retrieval call, not a routing subsystem.
- **`read(pointer)`** — resolves a pack pointer: a check-pointer address (live value via MCP), a code pointer (raw source via SCIP/Zoekt/tree-sitter), or the Meeting Page slice. **Programmatic** (code-execution): the agent can read a 10,000-row table or re-query without the intermediate output ever entering its context.
- **`act(spec)`** — does or produces anything, backed by three capability kinds: **Skills** (Word / PowerPoint / Excel / PDF and other document artifacts), the **sandbox** (compute, models, tests, charts, mockups, calculators), and **MCP connectors** (external actions — ticket, email, Slack, PR, deploy). Connectors live behind an **MCP registry + gateway**: the gateway mints scoped tokens per job that live at most 24 hours and permit only the operations this job needs; tool-search discovers the right 3–5 of thousands and loads only their schemas. For a read with no maintained connector, it falls back to **browser automation** (Browserbase / Stagehand); for a truly custom internal API, it writes a disposable client in the sandbox itself. Opens the sandbox/connector only past the ~10ms gate; every side effect returns as a staged draft. Also programmatic, so multi-step actions stay out of context (~38–63% fewer tokens, ~50% fewer turns in reported cases).
- **`meeting_page(op)`** — reads The Read's signals (goal, blocker, decision-health, stakes) and writes results back through Deep Dive 02's single-writer acceptance gate.

**How we optimize it.** Deferred tools mean only the search tool plus these four load upfront (~85% token reduction vs. loading a full catalog). The expert pack is the index for `read` — the agent never searches blindly; it reads its pack's map and fetches exactly the named address. What varies from a race-bug fix to a Monte-Carlo model to a mockup is only three things — the acceptance criteria the agent synthesizes, the 3–5 tools it retrieves, and the compute rung it picks; the loop, the sandbox, the proof cascade, and the safety stack are identical. That is what "any task" means concretely: the data varies, never the code.

> **THE THREE EMERGENT EFFORT LEVELS (they emerge, they are not routed)**
> The gate and the agent together produce three effort levels — the same three the original design named, now emergent rather than pre-classified. **KNOWN**: the answer is in the loaded pack or on the Page, static-and-valid — one cached call, ~1–2s, the existing citation is the proof, no sandbox. **LOOKUP**: checkable but versioned/expired — one or two live reads through the pack's check-pointer, ~3–5s, the retrieval is the proof, no sandbox. **WORK**: nothing cached answers it — the sandbox/connector opens, and effort scales *inside* WORK too (bounded: a two-line criteria and one-shot check; real: the full plan-and-prove; heavy: bigger compute, always async). These are outcomes of the agent's cheapest-correct-path disposition, not buckets it chooses from.

## Component 3 — The gate at the door: ~10ms, no model

**Overview.** The single most important cost control: a tiny deterministic check decides whether a task even needs the sandbox/connector before any expensive machinery runs. Simple asks structurally cannot open the sandbox — the code never routes them there.

**How we wire and optimize it.** Each pointer the bundle carries has a freshness stamp (STATIC / VERSIONED / EVENT; valid / expired) and an effects flag (read-only / draft-staging). The gate reads those and lets a task through to `act`'s sandbox/connector only when it genuinely must build or mutate; otherwise the agent answers from knowledge or one `read`. Ambiguity always rounds **up** (a stale answer spoken confidently is the unforgivable failure; an unnecessary live check costs cents). In practice the full build path runs only ~3–8 times per meeting. There is also a ~10ms exact-answer cache in front of the whole workroom (Deep Dive 03) for repeats.

## Component 4 — Planning: just-in-time, dynamic, capped

**Overview.** The agent decides its own plan. It does not receive a fixed plan and it does not always plan — planning materializes only when the task needs it, which is why the instant case stays instant (deciding-to-answer and answering are one inference) while a build gets a real plan.

**How we wire and optimize it.** We adopt **just-in-time planning** — one agent that plans, executes, and replans from feedback — as the default, because it unifies the cheap and hard cases without a separate control flow. For genuine build work the agent produces a plan into a fixed schema (`acceptance-criteria · steps · tools · evidence-needed · stop-conditions · compute-class · live|async`), guarded three cheap ways: **constrained decoding (XGrammar)** guarantees the plan is schema-valid as it's generated — chosen because it's fast enough (up to 14× quicker on JSON, 80× on complex grammars) to fit inside a 2–4s plan budget; a **semantic-entropy ambiguity check** interprets the criteria a few times in parallel and, on disagreement, asks one question or refuses before spending a cent; and a **deterministic verifier** (plain code — the "LLM-modulo" pattern: the model proposes, cheap code disposes) checks every referent resolves and every criterion maps to a step. For **multi-step or side-effecting work it plan-flashes** the human-readable plan first (silence = go). **Replanning** triggers on a failed step or when a step's output contradicts the plan's assumptions — but is **hard-capped (~3–4 turns)**, because the evidence is clear that excessive replanning is brittle and gains flatten fast. The plan's instruction text is not hand-authored forever — it's compiled from the shadow ledger (Component 8). And mid-work, when the agent hits a gap only a human can close — a missing credential, a requirement only the asker can settle — it fires **one targeted question to the mapped owner** rather than guessing or dead-ending: the same clarify-or-declare discipline as Deep Dive 03, now during execution instead of before it.

```
SYSTEM PROMPT — THE WORKROOM AGENT (ILLUSTRATIVE)
You receive a task bundle: {ask · WHO · one pointer · flags}. Everything Proxy knows is a TOOL:
  find_experts(query) · read(pointer) · act(spec: skill|sandbox|connector) · meeting_page(op)

Decide the CHEAPEST correct path — do not classify into task types:
  • If you already know it (from the loaded pack): answer.
  • If one live value settles it: read(pointer), then answer.
  • If something must be produced or done: plan just enough, then act.
Load the expert pack(s) named in WHO; it maps where this system's context lives — read exactly what you need,
  never search blindly. Fetch just-in-time; keep your context small.
Plan-flash any multi-step or side-effecting work before running. Replan on failure — but stop after ~3 tries.
Fan out to neighbor experts ONLY for genuinely cross-system asks.
Prove everything (Component 5). Every side effect is a STAGED DRAFT a human approves — never a silent action.
Say "I can't" only when you lack ACCESS, and name which. Return an honest partial receipt over a false claim.
If a gap can only be closed by a human — a missing input, an ambiguity only the asker can resolve — ask ONE
  targeted question to the person who'd know, rather than guessing or dead-ending.
```

## Component 5 — Verify: independent, cheapest-first

**Overview.** Nothing leaves the workroom unproven, and the check must be **independent** — a proposer that approves itself is the documented failure mode of agentic systems (circular verification). So verification is a separate pass with fresh, bounded context, cheapest check first.

**How we wire and optimize it.** A cascade that short-circuits on the first clean pass: (1) **deterministic checks** (microseconds–milliseconds, no model) — schema/contract match, property tests, re-query equality, test exit code; if plain code settles it, done. (2) A **grounding verifier** (**Vectara HHEM-2.1-Open**, Apache-2.0, ~1.5s CPU) scores factual consistency of any natural-language claim against its retrieved evidence — catching a statement that looks grounded but drifted. (3) An **agent-as-judge from a different model family**, only for high stakes (read from The Read's decision-health) — re-derives the claim with real tools rather than reading the text. The result is packaged as **artifact + receipt** (what ran, sources, timestamps, and honestly what couldn't be proven); for a novel task with no standard check, the plan synthesizes a verification plan that a cheap judge clears before work begins, and PROVE degrades gracefully to an honest partial receipt rather than a false claim or a dead end — which matters because verification of a genuinely novel task fails outright roughly a third of the time even with retries, so the honest partial receipt turns that ceiling into a trustworthy behavior instead of a lie.

## Component 6 — The safety stack, stated honestly

**Overview.** The workroom runs code and tools influenced by untrusted, meeting-derived input while holding real capabilities. We defend in layers, out-of-band (not by trusting the model), and we're honest about the one risk no one can zero out.

**How we wire it.**
- **Staged-drafts-only side effects — the keystone.** Every world-changing action is a proposed draft a named human confirms. This is the load-bearing property: it converts "injection causes an action" into "injection causes a rejected proposal." Guard it absolutely.
- **Capability / taint tracking (CaMeL-style).** A privileged plan works only from the trusted ask; untrusted content is quarantined and stripped of authority to dispatch a sensitive tool; data derived from untrusted input is tainted and gates sensitive calls (session-level: once any untrusted-content read runs, later side-effect args route through approval).
- **Firecracker microVM**, unprivileged VMM + seccomp — the isolation floor, asserted pre-flight (refuse to launch a privileged VMM). **Egress allowlist, default-deny** per job — the strongest control against exfiltration. **No live secrets in the sandbox** — the gateway (via Nango) mints scoped, short-lived proxy tokens per job; the sandbox holds a limited stand-in, never the real key. **A PreToolUse hook** lints/typechecks/dry-runs every mutation before it lands. **Full tool-call telemetry** for audit and red-teaming. **Risk-scaled autonomy** — low for reads, higher only for staged pushes.
- **The residual risk, stated plainly.** A prompt-injected agent can still surface subtly-wrong information *within its allowed reads*. The draft gate catches bad *actions*; it does not catch bad *information*, and that cannot be driven to zero today — every detection-based defense has been broken under adaptive attack. The honest posture: escape-proof to enterprise standard; injection-resistant by design with human-confirmed drafts as the backstop; not injection-proof — and we show exactly where the line is.

## Component 7 — Context discipline for long jobs

**Overview.** A long job's context must not rot or silently lose safety constraints.

**How we wire it.** We prefer **structured eviction / offload over summarization**: large intermediate outputs are written to the sandbox filesystem and replaced in context with path handles (programmatic tool calling does most of this automatically). Summarization is avoided because it silently drops file paths and permission constraints; after any compaction, non-negotiable safety/permission constraints are re-injected. This keeps the live context small and the provenance intact.

## Component 8 — The learned layer: only schemas, floors, and gates are fixed

**Overview.** Nothing semantic is hand-written and frozen. The only fixed things are the plan schema, the deterministic verifiers, the ambiguity/effects gates, the tool-permission scopes, and the latency/cost budgets. Everything else is compiled from our own logs or generated fresh at runtime.

**How we wire it.** The intake-enrichment, criteria-synthesis, and validation-rubric prompts are **compiled offline (DSPy-style) from the shadow ledger** and deployed only past a held-out regression gate (over-optimized prompts can bloat and lose accuracy, so every deploy is gated). Compilation runs on the cold path (nightly, reading Postgres logs), never on the hot path. The brief, the plan instance, the tool selection, and which claims escalate to the judge are generated by the agent per job. So the workroom gets cheaper and more accurate per org over weeks, with zero hand-authored runtime prompts.

---

# PART D — SCALING AND OPTIMIZATION

- **The compute ladder, routed at plan time.** No sandbox for answer/read tasks. **E2B / Firecracker** (warm-pooled snapshots, sandbox ready in <200ms, cold boot 125–200ms → 5–30ms restored) is the default for most build work. **Modal / Daytona** for heavy CPU/RAM or long sessions; **Modal-GPU** for ML inference; a self-host escape hatch for extreme scale. Escalation is a plan-time decision, never a runtime scramble.
- **Cheapest-first everywhere.** The ~10ms gate keeps simple asks out of the sandbox structurally; PROVE short-circuits on the first clean deterministic check; the expensive judge fires only for high stakes; a single strong run plus a verification gate beats best-of-N voting (reserved only for a narrow high-stakes class if it measurably helps).
- **Programmatic tool calling** keeps intermediate tool outputs out of the model context — the main token/latency lever for multi-tool tasks.
- **Cache engineering.** Prompt-cache the agent's stable context (pin the 1-hr TTL beta header for long jobs — the default silently regressed to 5-min in some sessions). Tool-result caching with TTL for idempotent reads. Warm sandbox pool to eliminate cold starts.
- **Pre-spend cost circuit-breakers.** Each expensive step checks a per-job token/dollar cap before spending, plus an org ceiling and a kill switch — the documented failure mode is an unbounded retry loop, and pre-spend caps are the defense.
- **Durability without a heavy orchestrator.** The hot path is a plain Postgres jobs table plus Agent SDK session resume — nothing more. Only the cold, long, cross-meeting path (a job outliving the meeting, a human-confirmation pause) uses a durable-execution engine (Inngest). There is deliberately **no Temporal, LangGraph, or CrewAI on the hot path** — orchestration hops cost latency and multi-agent loses on this shape; durable execution earns its place only where a named constraint forces it.
- **Fan-out economics.** Multi-agent costs ~15× tokens and helps only breadth-first tasks; the default is a single agent with the right pack(s) loaded, and fan-out is guarded behind a breadth-first check (an ask that provably decomposes into independent threads, e.g. blast-radius).

---

# DONE WHEN

**v0 — the core is done when:**

- the ~10ms gate routes a task to answer / read / act with no model call, and simple tasks provably never open the sandbox;
- the one agent loop, given the four tools, completes a task by deciding its own just-in-time plan — answer, one read, or an `act` — with no hard-coded task categories anywhere;
- `find_experts` retrieves the owning expert(s) from the registry (deferred/tool-search), and the loaded pack maps where all further context lives, so the agent fetches just-in-time and never searches blindly;
- `act` produces a document (Skills), runs a computation (sandbox), and fires an external action (connector) through the identical tool — proving capability rides in tools, not code;
- every result carries a class-appropriate receipt; a novel task with no standard verifier degrades to an honest partial receipt rather than a block or a false claim;
- verification is **independent** (a separate cascade — deterministic → HHEM → cross-family judge — never the agent grading itself), and scales with stakes;
- **every side-effecting result is a staged draft chained to a named human's confirmation** in the audit log; the safety invariants (unprivileged VMM, egress allowlist, no live secrets, taint tracking) are enforced as tested pre-flight assertions;
- replanning is capped (~3–4 turns) and the loop is bounded by per-job token/dollar caps with a kill switch;
- a new task class or connector is added by **registration** and exercisable by both a reactive ask and a proactive candidate with no engine change;
- the same bundle from all four callers (reactive, proactive, consult, probe) flows through the identical machine.

**v1 — added on measurement:**

- DSPy-compiled runtime prompts (intake, criteria, rubric) deployed only past a held-out regression gate, with per-org improvement from the shadow ledger;
- Saved Actions (named, parameterized, schedulable), Dry-run, and Show-your-work as product features;
- the heavy compute ladder (Modal / Daytona / GPU) and durable cold-path execution (Inngest) for jobs that outlive the meeting;
- **parallel fan-out** to expert subagents for breadth-first / blast-radius asks (v0 handles cross-system asks with one agent consulting neighbors sequentially; parallel fan-out is a speed optimization added when it's worth the ~15× tokens);
- best-of-N verification for a narrow high-stakes class, only if it measurably beats single-run-plus-gate.
