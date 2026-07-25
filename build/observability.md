# observability.md — real-time drift & stall detection (Langfuse + native OTel)

Two layers, and it matters which does what:
- **Gates PREVENT drift** (in-loop, structural): preflight, Tier-A closure, the fresh
  read-only verifier, `done-check`, config-freeze. A run physically cannot advance through
  one of these.
- **Observability SURFACES drift + stalls fast** (out-of-loop, real-time): every agent call,
  tool call, config touch, and phase/node transition is a trace you can watch and alert on —
  *before* a gate would catch it.

Do not rely on observability alone to stop drift; rely on it to SEE it early. The gates are the guarantee.

## 1. Wiring — Claude Code native OTel → Langfuse
Claude Code exports three independent OTel signals (traces, metrics, logs). **Per-agent and
per-tool-call SPANS are behind a beta flag** — `CLAUDE_CODE_ENABLE_TELEMETRY=1` alone gives only
metrics + logs; you MUST also set `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1` to get the tool-call /
agent spans (that is the whole point of "watch what the agent is doing"). Span names/attributes
are beta and may change. The native CLI export needs no instrumentation dependency. Enable per run
(keys from Secret Manager, never hard-coded/logged):
```
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1   # ← REQUIRED for tool-call/agent SPANS (beta)
export OTEL_TRACES_EXPORTER=otlp
export OTEL_METRICS_EXPORTER=otlp
export OTEL_LOGS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT=https://us.cloud.langfuse.com/api/public/otel
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic <base64(pk:sk)>"
export OTEL_RESOURCE_ATTRIBUTES="service.name=proxy-build,run.branch=$(git rev-parse --abbrev-ref HEAD)"
```
Note: Langfuse's OTLP endpoint above works but is their *secondary* path (their primary is the
Langfuse SDK with `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`) — if traces don't arrive, fall back
to the SDK. Traces/logs flush every ~5s, metrics every ~60s — near-real-time.

## 2. What we watch (the four things you asked to monitor)
| You want to monitor | How it shows up |
|---|---|
| **agent calls** | one span per subagent (reader / critic / builder / verifier) — who ran, inputs, outputs, tokens, latency, cost |
| **what the agent is doing** | tool-call spans nested under each agent — every Read/Edit/Bash with args |
| **routing** | phase + node tags on each span → the PLAN→BUILD→INTEGRATE→VERIFY→ADVANCE sequence is visible per node |
| **config** | any Edit to `pyproject.toml` / `.venv` / `.claude/settings.json` is a tool-span we alert on (config is frozen mid-run) |

Tagging: **session = the whole build run; trace group = one node; span = one agent/tool call.**
Set `run.id`, `phase`, `node.id` as span attributes so the dashboard reconstructs the route.

## 3. Anti-drift assertions (the "is it on the right track" part)
Raw traces show *what happened*; these assertions flag when the route deviates from `LOOP.md`.
They run against the event stream (a lightweight watcher tailing the OTel logs / git), and any
hit pings immediately:

1. **No build before HALT 1** — a builder/Edit span in Phase 1 before chain approval → drift.
2. **No ADVANCE without VERIFY** — a node's `status→verified` (a commit) with no preceding
   fresh-verifier span for that node → drift (self-grading / skipped verify).
3. **Verifier is read-only** — a verifier span that emitted an Edit/Write → drift (must have no write tools).
4. **Completeness ran** — HALT 1 reached with no `check_completeness.py` span exiting 0 → drift.
5. **Config frozen** — any config-file Edit span mid-run → drift (→ preflight would also fail next run).
6. **No forward dep** — a builder starts a node whose `depends_on` aren't all `verified` → drift.
7. **Stall** — no new span for a node beyond its soft budget AND no test/gate process running → stalled.

## 4. Stall detection (replaces the old hand-rolled monitor)
Per-span latency + Langfuse latency alerts replace `forge_monitor.py`. Soft per-phase budgets
live in `LOOP.md` as *expectations*, not config: exceeding one triggers "look, don't kill" —
diagnose (hung test / looping agent / genuinely hard node), fix, continue. Never a silent hour.

## 5. Score-based early warning (offline)
Langfuse online-evals on a sample + the Tier-D scenario scores as tracked metrics: a drop in a
faithfulness/pass score is the earliest signal a node regressed. Alert on the score, not just latency.

## Honest scope
Native OTel gives excellent *visibility* (calls, tools, routing, cost, timing, config touches)
and stall/latency alerts. The semantic "is the output correct" guarantee is still the in-loop
fresh verifier + `done-check` + real-data eval — observability is the early-warning radar on top.
