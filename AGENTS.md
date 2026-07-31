# AGENTS.md — Proxy shared method + laws (read FIRST, every session)

The shared build method, the five laws, and where the contracts live. Read alongside
`CLAUDE.md` (the constitution) and **`SPEC.md` — the product source of truth**
(`SPEC.md` supersedes the archived `product/v0-spec/*`).

## What Proxy is
An AI teammate that joins a company's meeting already knowing their codebase and does the
work live. A team connects a repo once (read-only GitHub App); Proxy holds a fresh clone +
a repo map, current on every push. Invited to a meeting, it waits in a warm per-meeting E2B
sandbox with the repo, the map, and the live transcript. When it is **addressed** ("proxy…",
a reply to its own question, or a follow-up right after it just engaged) it reasons, does the
real work in the sandbox, verifies, and responds through its one live meeting connection —
choosing itself whether to speak, chat, DM, show a screen, or offer a staged draft. Reactive
only: Proxy speaks when prompted, never unprompted.

## The whole system, in one loop (see SPEC.md)
Everything goes into the workroom — there is no router, no capability catalog, no per-ask
subsystem. A transcript line arrives → is Proxy addressed? → if no, idle (costs nothing); if
yes, the agent reasons/plans/does the work in the sandbox (only as much as the ask needs),
verifies, and responds through its meeting access, then goes idle. The transcript keeps
flowing into the sandbox while a task runs, so mid-task replies and follow-ups are seen live.

Two phases: **pre-meeting** (`services/premeeting`: connect → clone → build map → store) and
**meeting** (`services/{in-meeting,control-plane,workroom}`: warm sandbox → transcript feed →
wake → native Claude → respond via the `to_meeting` connection through the host relay).

## The five standing laws (every service obeys; every visible behavior traces to one)
1. **Grounded or silent** — cite real `file:line` from the current clone, or say "not found
   by this method"; a confident wrong answer is the one unforgivable failure.
2. **Never overstate** — plain results; failures spoken honestly, never faked; a build is
   "verified" only after it ran on real data past a fresh-context check.
3. **Human control is absolute** — barge-in stops speech; "quiet" silences; every
   world-touching action is a staged draft behind a human click. Guaranteed by the credential
   boundary: the sandbox holds no push/send creds.
4. **Dynamic, never hard-coded** — no code maps situation→action; the agent composes
   everything live. Code owns only physics, pipes, and the durable substrate.
5. **Talk-and-glance** — operable entirely by speaking and glancing; nothing to install
   mid-meeting.

## The build method (TDD on real data)
- Work one atomic task at a time; write the failing acceptance test that runs the **real
  path**, code to green, show the evidence, then flip the task done. Never edit tests,
  cassettes, goldens, or fixtures to pass (a lean PreToolUse guard enforces this).
- A task is done ONLY after its real path RAN on real / held-out data and the output was
  shown as evidence — not that unit tests pass. A node is verified only when live-wired on
  the real product path.
- Maker ≠ checker: a fresh-context sub-agent judges deep work; the author over-reports its
  own correctness.
- A task that fails N identical times flags `BLOCKED:<reason>` and continues — never deadlock,
  never silently claim done.
- The verification bar is proven on **real meetings on the cal.com repo, on real infra**
  (E2B + Anthropic + Recall/Cartesia). The nuance prime (SPEC §6) and latency (SPEC §4) are
  tuned there, not asserted on paper.

## Stack
Python 3.12 · **uv-workspace monorepo** (root `pyproject.toml`, members `services/*` +
`libs/*`, one shared `uv.lock`) · **src-layout** packages · `apps/*` = Vite static builds
(own pnpm workspace, NOT in `uv sync`) · ONE Cloud SQL **Postgres** + **GCS**
(object-versioned) for all durable state · **E2B** per-meeting sandboxes running native
Claude (Agent SDK, unmodified) · Recall (transport/bot) + AssemblyAI (STT) + Cartesia (TTS)
· Alembic migrations (Postgres only) · pytest + Hypothesis · ruff + mypy `--strict` + bandit.
The clone + repo map are a rebuildable derived cache; truth is Postgres + GCS.

## Deployable + services
**One Cloud Run service** — `control_plane` — is the whole hosted estate (webhooks, connect
page, auth, meeting provisioning, WS gateway + host relay). Meeting work runs in per-meeting
E2B sandboxes (no GKE / Pub-Sub / multi-region / custom orchestrator). Source trees:
`services/{premeeting, in-meeting, control-plane, workroom}` · `libs/{http, db, contracts,
ops, agentkit, llm}` · `apps/{connect, ...}` · `infra/` (Terraform) · `deploy/` (Cloud Run).

## Contract homes (imported, NEVER re-defined per service)
- `libs/contracts` — all wire types + the `ProxyMessage` registry + `assert_registry_closed()`
  (a produced-but-unregistered message type fails the build), the Readiness enum
  (`connecting|cloning|indexing|ready|not_ready`), note deltas.
- `libs/http` — the ONE external-call seam: `call_external` / `dispatch` (retry + cost
  telemetry); no raw vendor client lives anywhere else.
- `libs/llm` — the metered model gateway; every model call goes through it (env overrides
  seats/secrets only).
- `libs/agentkit` — the provider seam, the abort/resume registry (reused for barge-in), and
  the ONE shared injection guardrail (`guardrails.py`).
- `libs/db` — asyncpg pool + repos + Alembic. `libs/ops` — `with_operation_run`, atomic
  claim, sandbox-TTL reconcile, and the naming/copy lints.

## Definition of Done
The reactive workroom system proven on real meetings on real infra — every task correct, or
an honest clarify, or an honest decline (zero wrong/faked answers) · ruff + mypy `--strict` +
bandit + naming + contracts-closed green · no law-violating path · evidence committed.
**Done means the product is proven on real data — not that the code compiles.**
