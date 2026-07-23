# forge

**Spec → production-verified code.** A lean, agentic Claude Code plugin that takes *any*
spec and drives it to customer-shippable, fully-verified code — every behavior the spec
defines proven on real data + real infra, or a specific `BLOCKED` list. One command:

```
/forge <doc>
```

## The loop (5 phases, doc-agnostic)
1. **Understand** — a fresh-context analyst reads the spec → an intent brief (real intent, hidden obligations, risks, edge cases).
2. **Specify** — parallel extractors → acceptance criteria (EARS-phrased: behavior + oracle + threshold + evidence_class) + the atomic task list bound to criteria. A deterministic **coverage gate** + one fresh-context **criteria-auditor** guarantee every spec clause → criterion → task → test (both ways). Founder seals the bundle.
3. **Plan** — partition tasks into file-disjoint streams; per-stream plan reviewed and locked.
4. **Build + Verify** — parallel worktree streams build one task at a time (TDD); each task verified fresh-context up the ladder (static → unit → property → real-infra → real-data).
5. **Done** — `done-check` (5 conjuncts, incl. real-data eval) → returns "production-verified" or a `BLOCKED`/`SPEC_BLOCKED` list.

## The one rule
**Agents do judgment · deterministic scripts do the boolean · hooks do the physics.**
Claude's judgment drives reasoning; code does only the un-gameable math and the DONE
boolean; hooks enforce what must never be skipped. Model-driven, not a rigid template.

## Four guarantees
(a) 100% spec coverage · (b) maker ≠ checker in fresh context · (c) real-data verification · (d) no false-DONE.

## Layout
```
forge/
  .claude-plugin/plugin.json      plugin manifest
  .claude-plugin/marketplace.json local marketplace (install: claude plugin marketplace add ./forge)
  commands/forge.md               /forge <doc> — the orchestrator
  skills/                         understand · specify · plan · build-slice · eval
  agents/                         analyst · criteria-auditor · planner-reviewer · reviewer
  hooks/                          pretool_guard · post_edit · stop_gate · subagent_stop (wired in hooks/hooks.json)
  gates/                         coverage · done-check(5) · streams
```

## Install (local)
```bash
claude plugin marketplace add /Users/daksh/Desktop/proxy/forge
claude plugin install forge@forge-marketplace
# restart Claude Code to activate, then: /forge 00
```
