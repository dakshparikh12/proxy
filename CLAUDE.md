# Proxy — Claude Code context (read every session)
Constitution is `AGENTS.md` (method, invariants, stack, shared types) — read it first. Build process is `docs/BUILD-SYSTEM.md`. You build ONE component from `/components/<id>.md` via the `proxy-component-build` skill.

## Non-negotiables (hook-enforced where possible)
- `verify.sh` exit 0 is the ONLY definition of a green pass. Never claim done; show evidence.
- Real-data eval (the `eval-gate` skill) is the product bar. Green pytest ≠ done.
- Never edit: tests/ fixtures/ verify.sh hooks/ harness/ spec/ .claude/ evidence/ AGENTS.md CLAUDE.md components/. Tests define done; record conflicts in PROGRESS.md.
- Every model call through `src/proxy/llm.py` (metered gateway). No direct clients.
- No network beyond api.anthropic.com and pip.

## Code style (ruff/mypy check the mechanical half; review checks the rest)
- No function over ~40 lines; split first.
- No new abstraction (interface, base class, config flag) until a SECOND concrete use exists.
- No commented-out code, TODO stubs, or dead branches — delete or finish.
- Prefer editing an existing file over creating a new one for a small change.
- No defensive code for cases a type or contract test already rules out.
- Every public function: one-line docstring saying WHAT, not HOW.
- Use fresh-context subagents to review your own work — you over-report your own correctness.
