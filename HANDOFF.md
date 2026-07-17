# PROXY — Handoff to Pranav

_Written 2026-07-17 by Daksh + Claude. Read top to bottom once, then use §7 as the runbook._

---

## 1. The end goal

**Proxy** = an AI meeting participant that joins meetings already knowing a company's codebase, so it can answer questions and act during the call.

What's in this repo is not Proxy itself — it's the **autonomous build harness** that builds Proxy from the 10 frozen v0-spec docs in `product/v0-spec/` (`00-FOUNDATION` → `09-VERIFICATION`). For each doc the harness runs a spec-driven loop:

> generate exhaustive acceptance criteria → generate tests that encode them → build code until every test is green → independent fresh-context verification → advance to the next doc.

The design principle is **maker ≠ checker**: the agent that writes code never grades its own work; a separate fresh-context agent verifies, and a deterministic RTM coverage gate enforces completeness before anything is sealed.

---

## 2. Current status — the honest version

**Proven and working:**
- Harness architecture end-to-end through *planning* (phases P1–P5).
- `doc00` acceptance bundle: **142 requirements / 154 criteria, sealed**, RTM coverage gate **PASS 142/142** (P0 15/15, P1 89/89, P2 38/38).
- `doc00` tests: **166 tests, collect clean**. `doc01` has Pranav's pre-sealed criteria.
- The fast-path: already-sealed docs **skip regeneration** and go straight to build (verified — 45 min of wasted regen → ~2 seconds).

**NOT proven — this is the crux:**
- **The build phase (P6) has never completed for any doc.** Every run so far has died during criteria or planning. The closest we got: `doc00` reached P5, the planner produced a 210-line plan (`PROGRESS.md`, commit `990c9d8`), and then the process died.
- Therefore **everything past planning is unexercised**: the build loop, mutation gate, independent verifier, completeness sweep, cross-doc regression. Assume none of it has run in anger.

**Why the last run stopped:** not a code bug. There is no traceback and no exit code in `orchestrator/console.log` — the whole process group (supervisor + caffeinate + conductor + planner) vanished at once. That signature = **the launching terminal was closed** (SIGHUP), which kills everything. Lesson in §7: launch it so it survives the terminal (tmux/nohup) or keep the terminal open.

---

## 3. Why it's been slow — and what we changed this session

The pipeline was built like an **aerospace-certification harness, not a build harness.** Two concrete causes, both now fixed:

1. **A promote path bug caused full criteria REgeneration on every single run (~45 min wasted each time).** `promote()` copied `staging/<doc>/acceptance/` (the parent) into `acceptance/<doc>/`, nesting it one level too deep (`acceptance/doc00/doc00/`). The "already sealed?" check looked one level up and never found it → regenerated from scratch, forever.
2. **A third of the criteria output was formal paperwork the code never touches** — `fault-model` (1,245 lines!), `dispositions`, `ambiguities`, `system-model`, `estates`, `protocols`, `assurance-limits`, `authority-index`. Plus 3× review cycles, 3× completeness sweeps, and mutation testing. 15 fresh-context LLM passes per doc, each re-reading GENERATOR + EXHAUSTIVE-COVERAGE + full spec + AGENTS.

**Changes committed this session** (all pushed to `origin/main`):

| Commit | Change | Why |
|---|---|---|
| `51aa848` | Fix promote double-nesting + `bundle_dir()` recognizes already-sealed bundles | Stop the perpetual regen; skip sealed docs |
| `813e2f9` | Discard redundant `doc00` staging regen | The sealed bundle in `acceptance/` is source of truth |
| `f69a26f` | Drop formal-assurance artifacts from criteria gen (requirements + criteria only) | Verified the gate/seal read *only* those two files — the rest was pure overhead (~⅓ of output) |
| `d863024` | Restore generous coverage loop (review 3 / sweep 2, both **early-break**) | Speed comes from cutting ceremony, NOT from weakening coverage. A clean doc costs 1 cycle; gaps trigger more |
| `5931193` | Supervisor `git pull --rebase` before each restart | Fixes pushed mid-run auto-apply |
| `b5b2108` | Defer-and-note instead of halting on type debt / verifier refute / sweep gaps | Autonomy — recoverable blockers don't stop the night |
| `c82aca5` | Fix two earlier conductor halts (DEPS comment parse, collect substring) | — |
| `04cbced` | Persistent builder session (fresh context only for checks) | Don't re-spawn a whole Claude session per build increment |

**Net effect:** criteria gen should now be minutes, not hours, and **building becomes the long pole — as it should be.** Target rhythm: ~few-min criteria → ~30-min build → verify → ~1 hr/doc.

**Important nuance for Pranav:** coverage was NOT sacrificed for speed. The **RTM gate is a hard wall** — it refuses to seal unless every requirement is covered by a criterion *and* every criterion traces to a requirement (bidirectional). Every criterion still becomes a test; "done" still means `verify.sh` exits 0.

---

## 4. Harness architecture (map to navigate the code)

- **`orchestrator/orchestrate.py`** — THE conductor (plain Python, not agent-driven). Runs the per-doc phase sequence P1→P8. This is the file to read first.
  - P1 gen criteria (fresh agent → staging) · P2 adversarial review · P3 gen tests/fixtures (→ staging) · P4 **coverage gate + promote + seal** (deterministic, this process) · P5 plan + planner-review · **P6 build loop** · P6.5 mutation · P7 independent verifier (fresh, refute) · P7.5 completeness sweep · P8 cross-doc regression → tag `<doc>-done`.
- **`orchestrator/supervise.sh`** — restarts the conductor from the first unfinished doc until all are done or a real blocker needs a human. `git pull`s before each restart. Stop with `touch orchestrator/STOP`.
- **`orchestrator/criteria_coverage_gate.py`** — the RTM gate. Reads *only* `requirements/requirements.yaml` + `criteria/criteria.yaml`.
- **`orchestrator/prompts/`** — every phase prompt (gen_criteria, review_criteria, gen_evidence, plan, build_pass, verify_doc_prompt, completeness_sweep, adjudicate, unstall).
- **`harness/guard.py`** — a default-deny PreToolUse hook. Blocks agent writes into protected trees (`tests/ acceptance/ criteria/ product/ harness/ fixtures/ .claude/ .env` …). **Note:** it substring-matches `.claude/`, so it also blocks writes to the `~/.claude/.../memory/` dir (a false-positive worth knowing).
- **`acceptance/<doc>/`** — sealed criteria bundles (protected). **Legacy layout note:** `doc00` currently sits at `acceptance/doc00/doc00/` (double-nested from the old bug); `bundle_dir()` tolerates this. New docs promote correctly to `acceptance/<doc>/`.
- **`AGENTS.md`** — the laws/invariants every builder must honor (contract homes, layering: `services/` + `libs/`, never `src/`, all model calls via `libs/llm` gateway).
- **`PROGRESS.md`** — the locked build plan per doc.

---

## 5. Known issues & open risks (Pranav's to-do / to-verify)

1. **P6 build has never completed — highest priority.** Launch it, let it actually build `doc00`, and see what breaks. This is the real unknown.
2. **The planner may be hitting `max_turns=60`.** Last run committed "draft plan (pending planner-reviewer fold-in)" — the fold-in may not have finished. Consider raising the plan turn budget or slimming the planner prompt.
3. **Terminal-death kills the run.** Launch under `tmux`/`nohup`/`caffeinate` and keep it alive (see §7).
4. **Python version drift.** Spec says 3.12; the local run used 3.14 (`.venv`). Confirm the venv Python and pin it — 3.14's lazy annotations masked one issue already.
5. **`guard.py` is broad** — blocks any path containing `.claude/`. Fine for the build, but know it if you script around the repo.
6. **Docs are sequential** — they build on each other (foundation → code-intelligence → voice → orchestrator → workroom …). Do **not** build them in parallel. (The `deps` field lists pip packages, not doc ordering.)

---

## 6. What Pranav needs on his end

### 6a. GCP — already provisioned; he needs ACCESS, not re-setup
All GCP resources live **in the cloud project** and persist there — they are **not** in git and do **not** get "pushed." Provisioning is already done (per `SETUP.md`): Cloud SQL Postgres 15, GCS bucket (Object Versioning on), Secret Manager, Artifact Registry, KMS. Pranav needs:
- **IAM access to the project** — Daksh adds him (`gcloud projects add-iam-policy-binding <GCP_PROJECT_ID> --member=user:<pranav-email> --role=roles/owner` — or Editor + Secret Manager Accessor if you want tighter).
- **His own auth on his machine:** `gcloud auth login` **and** `gcloud auth application-default login` (ADC, for Terraform/libraries).
- **Terraform state access:** he can read/manage infra via the GCS state bucket once he has project access (`terraform init` + `plan`). He should NOT re-create the state bucket.

### 6b. Secrets — sent out-of-band, NEVER git
`.env` is gitignored (correctly). Two options, pick one:
- **Preferred:** grant Secret Manager access — the values are already stored there (`DATABASE_URL`, `SESSION_SECRET`, all API keys). He pulls them into his local `.env`.
- **Or:** send the `.env` file over a secure channel (1Password / encrypted), not Slack/email plaintext.

Keys the `.env` must contain (names only — values via the channel above):
`ANTHROPIC_API_KEY`, `DATABASE_URL`, `SESSION_SECRET`, `GOOGLE_CLIENT_ID/SECRET`, `GCP_PROJECT_ID`, `GCP_REGION`, `GCS_BUCKET`, `RECALL_API_KEY` + `RECALL_REGION` + `RECALL_WORKSPACE_VERIFICATION_SECRET`, `ASSEMBLYAI_API_KEY`, `CARTESIA_API_KEY`, `NANGO_SECRET_KEY` + `NANGO_HOST`, `GITHUB_APP_ID/CLIENT_ID/CLIENT_SECRET`, `GITHUB_WEBHOOK_SECRET`, `GITHUB_APP_PRIVATE_KEY_PATH`, `AES_KEY_RECALL/STT/CALENDAR`, and the `PROXY_MODEL_*` model routing vars.

**One separate file:** the **GitHub App private key `.pem`** (`GITHUB_APP_PRIVATE_KEY_PATH`) is a local file, gitignored — send it out-of-band too, and it must also be pasted into Nango.

### 6c. Local tooling — he installs on his machine (SETUP.md Part 1)
`gcloud` CLI, Terraform, `uv` (Python 3.12), Docker Desktop (testcontainers), node + pnpm. Then create the `.venv` and install deps.

### 6d. The `claude` CLI must be authenticated (critical)
The harness spawns `claude -p` sub-agents. On his machine he must run `claude` interactively once and `/login` with a **Max-subscription** account, or the conductor's prelaunch check fails ("Reached max turns (1)"). We hit this twice.

### 6e. SaaS dashboards — optional, for management
Anthropic Console, Recall.ai, AssemblyAI, Cartesia, Nango, and the GitHub App settings. Add him as a team member where possible, or he uses the shared keys.

---

## 7. Get synced & resume — step by step

```bash
# 1. Get the code
git clone https://github.com/dakshparikh12/proxy.git && cd proxy   # or: git pull

# 2. Local tooling — follow SETUP.md Part 1 (gcloud, terraform, uv, docker, node)

# 3. GCP access (after Daksh grants IAM)
gcloud auth login
gcloud auth application-default login

# 4. Secrets → create .env (from Secret Manager or the file Daksh sends), place the GitHub App .pem

# 5. Python env
uv venv && uv pip install -r requirements.txt   # confirm the venv Python is the intended version

# 6. Authenticate the agent CLI (REQUIRED — the harness spawns claude -p agents)
claude            # then /login with the Max account, then exit

# 7. Launch it so it survives the terminal closing (this bit us):
tmux new -s proxy
caffeinate -i bash orchestrator/supervise.sh 2>&1 | tee -a orchestrator/console.log
#   detach with Ctrl-b d ; reattach with `tmux attach -t proxy`
#   stop cleanly:  touch orchestrator/STOP
```

**Watch progress:** `tail -f orchestrator/run.log` · `git tag -l '*-done'` · `git log --oneline`. Phase banners look like `[doc00] P6 build loop`. Exceptions/deferrals land in `evidence/<doc>-EXCEPTIONS.md`.

**Where it'll resume:** `doc00` skips criteria+tests (sealed) and re-plans → **build**. That build is the first real test of P6 — expect to iterate there.

---

## 8. Send-Pranav checklist

- [x] `git push` (code + this HANDOFF.md + all fixes) — on `origin/main`.
- [ ] GCP IAM invite to `<GCP_PROJECT_ID>`.
- [ ] `.env` (secure channel) **or** Secret Manager access.
- [ ] GitHub App private-key `.pem` (secure channel).
- [ ] SaaS dashboard invites (Anthropic, Recall, AssemblyAI, Cartesia, Nango, GitHub App) — optional.
- [ ] Confirm he can run `claude` (Max account) locally.
