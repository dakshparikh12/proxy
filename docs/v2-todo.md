# v2 — running to-do (living doc)

**North star:** every doc goes **spec → 100% spec compliance → production-ready, verified
& tested** (locally *and* on real infra/data) through the v2 loop.

**v2-harden mandate (broadened):** not just "make the loop work" — capture **any**
optimization that makes future builds better: accuracy, planning quality, acceptance-
criteria strength, verification rigor (local + real infra), and process. doc00–03 are the
proving ground: if v2 can take them to prod-ready-verified, it's earned doc04–09.

---

## 🔴 YOUR ACTIONS (do these — exact steps)

### 1. Start the Cloud SQL instance (it's provisioned but STOPPED)
```bash
gcloud sql instances patch proxy-dev-pg --project proxy-meeting-dev --activation-policy=ALWAYS
# verify it comes up (wait ~1-2 min):
gcloud sql instances describe proxy-dev-pg --project proxy-meeting-dev --format="value(state)"   # want: RUNNABLE
```

### 2. Install the Cloud SQL Auth Proxy (to reach Cloud SQL locally)
```bash
brew install cloud-sql-proxy
# find the instance connection name:
gcloud sql instances describe proxy-dev-pg --project proxy-meeting-dev --format="value(connectionName)"
# then run it in a spare terminal (leave running during verification):
cloud-sql-proxy --port 5433 <CONNECTION_NAME_FROM_ABOVE>
```
(Port 5433 avoids clashing with any local Postgres on 5432. Tell me the connection name and I'll wire a local `TEST_DATABASE_URL` for real-infra runs.)

### 3. Decide the "No Haiku" question (founder call)
`.env` sets `PROXY_MODEL_SCRIBE/SCRIBE_CLOSE/GATE/QUALITY_GATE = claude-haiku-4-5`.
- Does the "No Haiku" directive retire Haiku from the **product routing** too, or only the build loop?
- If keeping Haiku, the valid id is `claude-haiku-4-5-20251001` (current seat drops the date suffix) — confirm and I'll fix.

---

## 🟢 MY ACTIONS (I'll do these)

- [ ] Fix `.env` `DATABASE_URL` typo: `ostgresql://` → `postgresql://` (local, gitignored).
- [ ] `uv sync --all-packages` + pinned tools → restore `google-cloud-storage>=2.14` (declared but pruned) so GCS code runs.
- [ ] After you do #1/#2: verify a real Cloud SQL + GCS round-trip (connect, migrate, write/read a versioned object).
- [ ] Make the verification rungs REAL (v2 harden): C5 integration actually *runs* on live infra; C7 eval `≥ baseline` implemented with real per-doc eval; C6 invariants actually enforce.
- [ ] doc00–03: audit acceptance-criteria quality, build/fix to real spec compliance, verify local + real infra.

---

## 🟡 OPEN DECISIONS (need your call — will ask/confirm)

- **No-Haiku** (see YOUR ACTIONS #3).
- **Acceptance-bundle changes** — process for strengthening sealed criteria when found weak (brainstorm Q2).
- **Sequencing** — depth-first per doc vs. breadth-first vs. hybrid pilot (brainstorm Q3).
- `test_sub_034` — sealed-test-vs-`§3.3` `meeting_id NOT NULL` contradiction (founder adjudication).
- `gitpython` CVE bump (done-check C10, founder-gated).

---

## 🗒️ v2 OPTIMIZATION BACKLOG (found during review — apply as we go)

- **decompose binding** — bind criterion→test via docstring `criterion_id: AC-XXX` (Claude-native), not the `-k` name regex; unblocks doc02/03 verification.
- **done-check C8 breadth** — mutation spot-check samples N tasks, not just the first.
- **C6 invariants** — install pre-commit / make the ops fallback actually run.
- **C7 eval baseline** — implement the numeric floor + per-doc real-data eval tests.
- **eval-runner** — reconcile its v1 `/components/<id>.md` + `/fixtures/<id>/` layout with the v2 doc-based loop.
- **drive.sh** — a cost producer (nothing currently appends spend), or document the contract.
- Minor/hygiene: journey allow-list by path not whole-line; stall-hash `tail -c`; orchestrator/state cruft.

_(Deferred-not-dropped: these are "make future builds better," per the broadened mandate.)_
