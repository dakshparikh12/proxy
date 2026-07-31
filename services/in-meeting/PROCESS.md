# Proxy Build & Verification — MASTER PROCESS (do not deviate)

This is the standing process for building + verifying the full Proxy product (pre-meeting + in-meeting,
one integrated system). Read it before every phase. `SPEC.md` is the source of truth for *what*; this doc
is the source of truth for *how we build and prove it*. If work drifts from this doc, the work is wrong.

## 0. Invariants (never violated)
- **Never hard-code.** No router, no capability catalog, no situation→action code. The agent composes.
- **Generalized fixes only.** When a gap is found, fix the *system* (infra / a prompt / an agent change /
  a seam) so it works for ANY input — never a special-case patch to satisfy one test.
- **Complete but simple.** Cover everything; add nothing unrequested. No over-engineering. Simplify infra
  to the minimum that runs on the cloud.
- **Honest residual.** Never claim "100% / pixel-perfect / complete." Report what is proven vs. not. The one
  thing sims cannot certify is the real-audio vendor path (text transcripts ≠ real audio) → the live smoke.
- **Two prompt homes, kept separate.** `CLAUDE.md` + subagent briefs = instructions for the BUILD agents.
  The **Proxy agent's own system prompt** = the PRODUCT's instructions (compose actions, ask when unsure,
  honest about limits, nothing scripted) — a first-class spec component, where the dynamic behavior lives.

## 1. Source of truth
`services/in-meeting/SPEC.md` — qualitative, complete, simple, with an explicit **NEVER-DO** section, the
13 components (below), and the Proxy-agent prompt spec'd. Nothing downstream may contradict it. Every
build step and every review checks against it.

## 2. The 13 components (nothing is missed if each has spec + tasks + verification)
1. Pre-meeting (repo → clone → `index.md` map → store) — exists.
2. Join / ingress (meeting URL → Recall bot joins → transcript in; the hosted invite route).
3. Connections (Recall in/out/chat/mute/video · AssemblyAI config · Cartesia speak · E2B sandbox).
4. Memory (the running transcript store, no model on it).
5. Trigger (engagement: addressed / reply-to-its-question / worker-done).
6. The loop (Claude agent: context → reason → act; per-ask isolation; prompt caching).
7. The Proxy prompt (the agent's system instructions — the behavior).
8. Worker (sandboxed heavy-task execution).
9. Safeguards (barge-in · reconnect+catch-up · graceful failure · no time-cap · monitor-while-working).
10. UX (one-circle orb · dynamic chat · screen-share · consent · approve-card).
11. Control-plane (connect API · GitHub webhook · Recall webhook · provision+launch · hosted invite route).
12. Integration seams (map→context load by (tenant,repo,pinned_sha) · meeting↔repo binding · referent→map).
13. Infra / hosting (simplified: the minimum deployable + DB + secrets to run on the cloud).
Plus **Cleanup**: delete all old/replaced code → the target repo structure.

## 3. Build process (per node)
Subagent-driven (superpowers:subagent-driven-development), Fable model, on `proxy-build`, in place.
Per node: **implement (TDD, generalized) → harsh fresh-context review against SPEC + the node's exact
criterion → fix Critical/Important generally → log to the ledger**. Only then the next node. Progress lives
in `.superpowers/sdd/progress.md` (survives compaction — trust it + `git log` after any resume).

## 4. Verification design (deepeval throughout — the spine)
All judging runs on the **Claude subscription** via a custom `DeepEvalBaseLLM` wrapping `claude_agent_sdk`
(build + verify this adapter first). Five layers:
1. **Component gates** — deterministic pytest for wiring/mechanics + **G-Eval** for behavior-bearing pieces.
2. **Trace instrumentation** — `@observe` the engine so every run emits a trace (span per component: trigger,
   context-load, agent turn, each tool call, worker, speak/chat/screen, notes) → see what fired + per-span metrics.
3. **Scenario generation** — deepeval **Synthesizer** generates hundreds of realistic meeting transcripts +
   embedded reactive asks (explain code · build PR · research · share screen · mute · ambiguous→clarify ·
   cross-talk · concurrent asks · interruption · can't-do), evolved for messiness. Diversity over raw count.
4. **End-to-end conversational sims (THE CORE)** — deepeval **ConversationSimulator** role-plays the
   meeting participant(s); each turn feeds the REAL Proxy engine (simulated transport = the turn is the
   transcript line, no vendor cost); Proxy runs the full flow with the real agent. Score with
   `ConversationCompletenessMetric` (every ask satisfied?), `TurnRelevancyMetric`, + custom conversational
   G-Eval (right nuance fired · nothing hard-coded · spec-faithful · handled interruption/ambiguity).
   This is where functional errors surface — run as many as rate limits allow.
5. **Whole-system gap-hunt + repo-clean** — fresh-context adversarial agents read the built system vs. SPEC:
   errors, hard-coding, spec deviations, unwired seams, missing pieces. Repo matches the target structure,
   imports resolve. Every gap → a generalized fix (§0).

"Walk through everything except the actual meeting" = the ConversationSimulator + `@observe` trace: the
command comes in, Proxy does it end-to-end, we watch every step fire. The only uncovered edge is real audio
→ the live-vendor smoke (bonus, creds are configured).

## 5. Cost / auth
Build agents + the Proxy agent + the deepeval judge ALL run on the Claude Max 20x subscription (SDK uses the
CLI subscription auth — no API key; a `RateLimitEvent` confirms it). ~$0; paced around plan usage resets.
Only a real live-vendor smoke has a small vendor cost. Vendor creds are configured.

## 6. The order (phases)
1. Write/lock **SPEC.md** (source of truth, complete+simple, NEVER-DO list, 13 components, Proxy prompt).
2. Write the **full integrated plan** (pre-meeting + in-meeting → one system: setup, wiring, every seam);
   **validate it fresh-context — nothing missing**; founder locks it.
3. **Build** subagent-driven with per-node spec-check (§3).
4. Build + verify the **subscription deepeval judge adapter**; instrument the engine with `@observe`.
5. **Run the verification** (§4 layers 3–4): Synthesizer scenarios → ConversationSimulator → scored; fix
   generally.
6. **Whole-system fresh-context review** vs. SPEC (§4 layer 5).
7. **Delete old/replaced code → target repo structure**; fresh-context validate the clean repo — no gaps.
8. **Simplify infra** to the minimum cloud deployable.
9. **Live-vendor smoke** (bonus).
10. **Report the honest true state** — proven vs. residual. Never a false "complete."
11. **Continuous deepeval refinement loop** — keep running the ConversationSimulator + G-Eval battery
    (subscription judge). For each *real* failure, apply a **generalized** fix (a prompt / agent / seam /
    infra change) and re-run, until it converges (all-pass or diminishing returns or a rate-limit ceiling).
    Refinement means **optimizing the existing simple system — NEVER adding complexity, a new capability, or
    a hard-coded patch.** The system's shape stays identical; only its correctness/robustness improves.

## 7. Definition of done (honest)
Every component built + spec-faithful; the ConversationSimulator battery passes on the real agent across
diverse messy scenarios; the fresh-context gap-hunt is clean; old code deleted; repo matches the target
structure + green; infra minimal + deployable. The real-audio path certified by the live smoke, or named as
the single residual. Reported truthfully — what's proven, what isn't.
