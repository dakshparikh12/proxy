# PROXY — Handoff to Pranav

_2026-07-17. This is a runbook. Do §3 in order to get synced, then read §4–§5._

---

## 0. TL;DR

- **What:** the autonomous harness that builds Proxy from the specs in `product/v0-spec/`.
- **Where we are:** criteria + tests for `doc00` are done and sealed; the harness runs cleanly through *planning*. **The actual build phase has never completed** — that's the open problem.
- **What to do:** pull the repo, drop in the `.env` Daksh sent, log into everything as **proxy@**, then launch (§3). On launch `doc00` skips straight to build — that build is what you're here to make work.

---

## 1. What we're building (the goal)

**Proxy** = an AI meeting participant that joins meetings already knowing a company's codebase. This repo is not Proxy — it's the **build harness** that generates Proxy from 10 frozen spec docs (`00-FOUNDATION` → `09-VERIFICATION`), one at a time, each via:

> exhaustive acceptance criteria → tests that encode them → build until all tests green → independent fresh-context verification → next doc.

Principle: **maker ≠ checker** (the builder never grades itself) and a deterministic RTM gate enforces full coverage before anything seals.

---

## 2. Where we are (status)

**Done & proven (phases P1–P5):**
- `doc00`: **142 requirements / 154 criteria, sealed**; RTM gate **PASS 142/142**; **166 tests** collect clean. `doc01` has your pre-sealed criteria.
- Fast-path works: sealed docs **skip regeneration** and go straight to build (45 min → ~2 s).

**NOT proven (P6 onward) — the reason you're picking this up:**
- **The build loop (P6) has never run to completion for any doc.** Best we reached: `doc00` produced a 210-line plan (`PROGRESS.md`), then the process died — because the **terminal that launched it was closed** (no crash; the whole process group got SIGHUP'd). Fix = launch under `tmux` (§3, step 8).
- Everything past planning (build, mutation, verifier, sweep, regression) is therefore unexercised.

---

## 3. Get synced — do these in order

### Step 1 — Pull the repo (everything non-secret is here)
```bash
git clone https://github.com/dakshparikh12/proxy.git && cd proxy
# already cloned:  git pull origin main
```
This carries the code, the sealed criteria bundles, the tests, AND the seal state (`orchestrator/state/`, force-committed so your `doc00` skips straight to build).

### Step 2 — Drop in secrets
Daksh already sent you **`.env`** — put it at the repo root (`proxy/.env`). It's gitignored on purpose; never commit it.

### Step 3 — GitHub App private key (`.pem`)
The `.pem` is not sent (it doesn't live in the repo). Generate a fresh one:
1. Log into GitHub as **proxy@** → the Proxy GitHub App → **Settings → Private keys → Generate a private key**.
2. Save the downloaded `.pem` locally (e.g. `proxy/secrets/github-app.pem`).
3. Point `.env` at it: set `GITHUB_APP_PRIVATE_KEY_PATH=` to that local path (the path in Daksh's `.env` is *his* machine's path — change it to yours).
4. Also paste that key into Nango if prompted (per `SETUP.md`).

### Step 4 — Access: everything is under the proxy@ account
We provisioned all of it on the **proxy email**. You already have that account, so there are **no IAM invites to wait on** — just authenticate as proxy@ everywhere:
```bash
gcloud auth login                        # sign in as proxy@
gcloud auth application-default login    # ADC for Terraform/libraries, also proxy@
```
- **GCP infra is already live in the cloud** (Cloud SQL, GCS, Secret Manager, Artifact Registry, KMS) — you do **not** re-provision. `gcloud config set project <GCP_PROJECT_ID>` (value is in `.env`).
- GCP Console, Secret Manager, and every SaaS dashboard (Anthropic, Recall.ai, AssemblyAI, Cartesia, Nango, GitHub App) — all the **same proxy@ login**.
- Terraform state lives in a GCS bucket; `terraform init` picks it up. Don't recreate the state bucket.

### Step 5 — Local tooling (SETUP.md Part 1)
Install: `gcloud` CLI, Terraform, `uv`, Docker Desktop, node + pnpm.

### Step 6 — Python env
```bash
uv venv && uv pip install -r requirements.txt
# confirm the venv Python version is what you intend (we hit a 3.12-vs-3.14 drift; pin it)
```

### Step 7 — Authenticate the agent CLI (REQUIRED)
The harness spawns `claude -p` sub-agents. If the CLI isn't logged in, the conductor's prelaunch check fails with "Reached max turns (1)".
```bash
claude          # run interactively once, then /login with the MAX-subscription account, then exit
```

### Step 8 — Launch so it survives the terminal
```bash
tmux new -s proxy
caffeinate -i bash orchestrator/supervise.sh 2>&1 | tee -a orchestrator/console.log
# detach: Ctrl-b then d   |   reattach: tmux attach -t proxy   |   stop clean: touch orchestrator/STOP
```
Watch: `tail -f orchestrator/run.log`, `git tag -l '*-done'`, `git log --oneline`.

---

## 4. What we changed in the build loop this session

The pipeline was built like an aerospace-certification harness. Two root causes of the slowness, both fixed (all on `origin/main`):

1. **A promote path bug regenerated all criteria on every run (~45 min wasted each time).** `promote()` copied one directory too high, so the sealed bundle landed at `acceptance/doc00/doc00/` and the "already sealed?" check never found it. → `bundle_dir()` now tolerates it and sealed docs skip.
2. **A third of criteria output was formal paperwork the code never uses** (`fault-model` 1,245 lines, `dispositions`, `ambiguities`, `system-model`, `estates`, `protocols`, `assurance-limits`, `authority-index`). Dropped — verified the gate/seal read only `requirements.yaml` + `criteria.yaml`.

| Commit | Change |
|---|---|
| `51aa848` | Fix promote double-nesting; `bundle_dir()` recognizes already-sealed bundles → skip regen |
| `813e2f9` | Discard redundant `doc00` staging regen (sealed bundle is source of truth) |
| `f69a26f` | Criteria gen: drop formal-assurance artifacts → requirements + criteria only (gen = minutes) |
| `d863024` | Restore generous coverage loop (review 3 / sweep 2, both early-break) — speed from cutting ceremony, not coverage |
| `5931193` | Supervisor `git pull` before each restart (pushed fixes auto-apply) |
| `b5b2108` | Defer-and-note instead of halting on type debt / verifier refute / sweep gaps |
| `c82aca5` | Fix two earlier conductor halts (DEPS comment parse, collect substring) |
| `04cbced` | Persistent builder session (fresh context only for the checks) |

**Coverage was not traded for speed:** the RTM gate is a hard wall (every requirement↔criterion, bidirectional), the review loop still runs up to 3× (early-breaks when clean), every criterion still becomes a test, and "done" still means `verify.sh` exits 0.

---

## 5. What's not working & what to build/configure next

**The one real task: make P6 (the build loop) actually complete `doc00`.** When you launch, `doc00` skips criteria+tests and goes straight to build — that's the first genuine exercise of P6. Expect to iterate there.

Things to check/harden as you go:
- **Planner may be hitting `max_turns=60`** — last run committed "draft plan (pending planner-reviewer fold-in)", i.e. it may not have finished the review fold-in. Raise the plan turn budget or slim `orchestrator/prompts/plan.md`.
- **Build loop knobs** (`orchestrator/orchestrate.py`): `MAX_BUILD_SESSIONS=24`, `BUILD_TIMEOUT=90m`, `BUILD_TURNS=600` — tune once you see real build behavior.
- **Launch durability** — always `tmux`; a closed terminal killed the last run.
- **Python version** — pin the venv (3.12 per spec; local was 3.14).
- **Target rhythm** we're aiming for: ~few-min criteria → ~30-min build → verify → **~1 hr/doc**, sequential across docs (they build on each other — do **not** parallelize docs).

---

## 6. Sync checklist — is anything missing?

| Item | How Pranav gets it |
|---|---|
| Code, sealed criteria, tests, seal state | **`git pull`** (all in the repo, incl. force-committed `orchestrator/state/`) |
| `.env` (all API keys, DB URL, secrets) | **Daksh already sent it** — place at repo root, gitignored |
| GitHub App `.pem` | **Regenerate** from the GitHub App settings as proxy@ (Step 3) |
| GCP infra, Secret Manager, Terraform state | **Already in the cloud** — access as proxy@ (`gcloud auth`), no re-provisioning |
| SaaS accounts | Same **proxy@** login on each dashboard |
| `.venv`, ADC creds, caches | **Regenerate locally** (Steps 5–7) |
| `.claude/settings.local.json` | Not needed — machine-local Claude Code perms; you'll have your own |

Nothing else to send. A `git pull` + the `.env` you already have + proxy@ logins = fully synced.
