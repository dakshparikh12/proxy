# PROXY — Handoff to Pranav

_2026-07-17. A runbook. Do §3 → §4 to get running, then read §5–§6._

---

## 1. What we're building

**Proxy** = an AI meeting participant that joins meetings already knowing a company's codebase. This repo is the **build harness** that generates Proxy from the 10 frozen specs in `product/v0-spec/` (`00-FOUNDATION` → `09-VERIFICATION`), one doc at a time:

> exhaustive acceptance criteria → tests that encode them → build until all tests green → independent fresh-context verification → next doc.

Principle: **maker ≠ checker** (the builder never grades itself), and a deterministic RTM gate enforces full coverage before anything seals.

---

## 2. Where things stand

- **Done & sealed:** `doc00` — 142 requirements / 154 criteria (RTM gate PASS 142/142), 166 tests. `doc01` has your pre-sealed criteria. Sealed docs skip regeneration and go straight to build.
- **Not yet proven:** **the build phase itself has never completed a doc.** The harness runs cleanly through *planning*; the build loop and everything after it (verification, sweep, regression) is unexercised. **That's the job: make the build actually complete `doc00`.**

---

## 3. Which account owns what (important)

| Thing | Account |
|---|---|
| **Code repo** `github.com/dakshparikh12/proxy` | Daksh's **Berkeley** GitHub. Pranav is added as a collaborator and clones with **his own** GitHub login — *not* proxy@. |
| **GCP + all SaaS** (Anthropic, Recall.ai, AssemblyAI, Cartesia, Nango) | the **proxy@** email. Sign into all of these as proxy@. |
| **Proxy GitHub App** (the product's runtime GitHub integration — separate from the code repo) | product infra — **confirm with Daksh which account it's under** (likely proxy@). This is where the App private key comes from, below. |

---

## 4. Setup — one copy-paste block

You have the `.env`, so this is just install + auth, no configuration. Run top to bottom on macOS:

```bash
# 1. Clone (Berkeley GitHub — use YOUR GitHub login; Daksh adds you as a collaborator)
git clone https://github.com/dakshparikh12/proxy.git
cd proxy

# 2. Put the .env Daksh sent at the repo root: proxy/.env
#    Then edit ONE line to point at your local App key (see §5): 
#    GITHUB_APP_PRIVATE_KEY_PATH=./secrets/github-app.pem

# 3. Local tooling
brew install --cask google-cloud-sdk docker
brew install uv node
brew tap hashicorp/tap && brew install hashicorp/tap/terraform
npm i -g pnpm

# 4. Google/GCP auth — sign in as PROXY@ (infra is already live; you are NOT re-provisioning)
gcloud auth login
gcloud auth application-default login
gcloud config set project proxy-meeting-dev

# 5. Python 3.12 env + the verify toolchain (the conductor installs per-doc runtime deps itself)
uv venv --python 3.12
uv pip install pytest pytest-asyncio ruff mypy bandit

# 6. Agent CLI — log in with the MAX-subscription account (the harness spawns claude -p agents)
claude        # run once → /login → exit

# 7. Launch so a closed terminal can't kill it
tmux new -s proxy
caffeinate -i bash orchestrator/supervise.sh 2>&1 | tee -a orchestrator/console.log
#   detach: Ctrl-b then d   ·   reattach: tmux attach -t proxy   ·   stop clean: touch orchestrator/STOP
```

Watch it: `tail -f orchestrator/run.log` · `git tag -l '*-done'` · `git log --oneline`.

---

## 5. Two file notes

- **`.env`** — Daksh sent it; place at the repo root. It's gitignored; never commit it.
- **GitHub App `.pem`** — not in the repo and not sent. Generate a fresh one: sign into the **Proxy GitHub App** (§3) → *Settings → Private keys → Generate a private key*, save it locally (e.g. `proxy/secrets/github-app.pem`), set `GITHUB_APP_PRIVATE_KEY_PATH` in your `.env` to that path (the value in Daksh's `.env` is his machine's path), and paste the key into Nango if prompted.

Everything else comes from `git pull` (code, sealed criteria, tests, and the seal state in `orchestrator/state/` that lets `doc00` skip to build) or from the cloud as proxy@. Nothing else to send.

---

## 6. The system, and what changed since your handoff

**The system.** `orchestrator/orchestrate.py` is the conductor (plain Python, not agent-driven). Per doc it runs: gen criteria → adversarial review → gen tests → **RTM coverage gate + promote + seal** → plan → **build loop** → mutation → independent verifier → completeness sweep → cross-doc regression → tag `<doc>-done`. `supervise.sh` restarts it from the first unfinished doc until done. `harness/guard.py` is a default-deny hook that blocks agents from writing into the arbiter trees (`acceptance/ tests/ criteria/ product/ harness/`). `AGENTS.md` holds the build laws (layering `services/` + `libs/`, all model calls via `libs/llm`).

**What we changed from the version you handed us** (all on `origin/main`):

1. **Fixed a promote path bug** that made the conductor regenerate all criteria on every run: `promote()` wrote the sealed bundle one directory too deep (`acceptance/doc00/doc00/`), so the "already sealed?" check never found it. It now recognizes sealed bundles and skips them.
2. **Slimmed criteria generation.** It used to emit ~⅓ of its output as formal-assurance paperwork the code never touches (`fault-model`, `dispositions`, `ambiguities`, `system-model`, `estates`, `protocols`, `assurance-limits`, `authority-index`). Dropped — the gate and seal read only `requirements.yaml` + `criteria.yaml`. Criteria generation is now minutes, not hours; **building is the long pole, as it should be.**
3. **Kept coverage rigor intact.** The RTM gate is still a hard wall (every requirement↔criterion, bidirectional), the adversarial review loop still runs up to 3× (but early-breaks once criteria are clean and covered), every criterion still becomes a test, and "done" still means `verify.sh` exits 0.
4. **Autonomy / durability:** the supervisor now `git pull`s before each restart (so fixes auto-apply), recoverable blockers are deferred-and-noted rather than halting, and the builder runs as one persistent session (fresh context is spent only on the independent checks).

| Commit | Change |
|---|---|
| `51aa848` | Recognize already-sealed bundles + fix promote double-nesting |
| `f69a26f` | Drop formal-assurance artifacts → requirements + criteria only |
| `d863024` | Coverage loop review 3 / sweep 2, both early-break |
| `5931193` | Supervisor `git pull` before each restart |
| `b5b2108` | Defer-and-note instead of halting |
| `04cbced` | Persistent builder session |

---

## 7. What to make work next

- **Complete the build loop on `doc00`.** On launch it skips criteria+tests and goes straight to build — the first real exercise of the loop. Expect to iterate here.
- **Planner turn budget** (`max_turns=60` in `orchestrator/orchestrate.py`) may be tight for a full plan + reviewer fold-in — raise it or slim `orchestrator/prompts/plan.md` if the plan comes out truncated.
- **Build-loop knobs** to tune once you see real behavior: `MAX_BUILD_SESSIONS=24`, `BUILD_TIMEOUT=90m`, `BUILD_TURNS=600`.
- **Always launch under `tmux`** — a closed terminal kills the whole run.
- **Docs are sequential** (they build on each other) — do not parallelize them.
- **Target rhythm:** ~minutes of criteria → ~30-min build → verify → ~1 hr/doc.

---

## 8. Sync checklist

| Item | How Pranav gets it |
|---|---|
| Code, sealed criteria, tests, seal state | `git pull` (all in the repo) |
| `.env` | Already sent — place at repo root |
| GitHub App `.pem` | Regenerate from the GitHub App settings (§5) |
| GCP infra, Secret Manager, Terraform state | Already in the cloud — access as proxy@ |
| `.venv`, ADC creds | Regenerate locally (§4) |

`git pull` + the `.env` you have + proxy@ logins (+ your own GitHub login for the repo) = fully synced.
