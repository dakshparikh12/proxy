# proxy-build

The build system for Proxy: **one ordered chain of tasks that, when every task is
built + integrated + verified, is provably the entire working Proxy product** — from
the smallest wiring to the biggest function.

It is a **plain folder, not a plugin**. The main Claude session drives it by following
`LOOP.md`. It reuses the existing spec bundles and the fresh-context reviewer agents under
`.claude/agents/`; its gates are self-contained under `build/gates/`. (The old `forge/` plugin
is deleted — its salvaged pieces were relocated here.)

## The files
| File | What it is |
|---|---|
| `SPEC.md` | The full spec of this system — principles, the node schema, the four completeness tiers, the phases, the drift guards, the definition of done. |
| `LOOP.md` | The operational routing the lead session follows: Phase 0 (preflight) → Phase 1 (plan) → Phase 2 (the 5-step node loop) → Phase 3 (sign-off), with the human halts. |
| `preflight.sh` | The Phase-0 env gate — hard-asserts every environment killer we've hit so it can't recur; freezes config. Run before every phase. |
| `observability.md` | Langfuse + native-OTel wiring, what's monitored (agent calls, tools, routing, config), the anti-drift trace assertions, and stall detection. |
| `chain.schema.json` | The JSON Schema every node in `chain.json` must satisfy. |
| `check_completeness.py` | The deterministic Tier-A closure checker (requirements ↔ nodes, wiring balance, journey coverage). Un-driftable — it is arithmetic on the chain, not an agent's opinion. |
| `gates/verify-node.sh` | Per-node acceptance runner — fail-closed (pytest exit-5/no-op = FAIL). Self-contained; no `done-check`/`slices` coupling. |
| `gates/signoff.sh` | Phase-3 whole-product static+unit gate (ruff + mypy --strict + bandit + offline suite). |
| `decisions.md` | Append-only log of technical-gap resolutions (reviewed at HALT 1). |
| `chain.json` | **The one artifact** — the ordered, dependency-validated task list. Produced in Phase 1, walked in Phase 2. (Generated; starts absent.) |
| `journeys.json` | Doc-09 integration journeys, enumerated in Phase 1 — the oracle for journey closure. (Generated; starts absent.) |
| `scenarios/` | The Tier-D scenario corpus (generated in Phase 1; reused as the real-data test suite in Phase 2/3). |

## Source of truth
`product/v0-spec/*` — the 8 spec docs (00–05, 08, 09). `CANONICAL-DECISIONS.md` overrides.
Nothing derived (including anything in this folder) ever overrides the specs.

## Run it
The lead session, following `LOOP.md`:
```
Phase 0:  bash build/preflight.sh              # env hardened + config frozen (or HALT)
Phase 1:  produce chain.json + journeys.json  →  python3 build/check_completeness.py  →  HALT 1
Phase 2:  walk chain.json, one node at a time (PLAN→BUILD→INTEGRATE→ verify-node.sh →ADVANCE)
Phase 3:  bash build/gates/signoff.sh + scenarios/deepeval on real infra  →  HALT
```
Observability on for the whole run (`observability.md`). Config stays frozen.
