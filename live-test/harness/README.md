# Proxy live-test harness

Operator-driven, chunk-by-chunk replay of the scripted cova meeting
(`live-test/MEETING_TRANSCRIPT.md`) into a **real** Proxy meeting. Two "replica"
Recall bots speak the transcript lines (Cartesia TTS → Recall Output-Media
webpage — the SAME primitives `services/in-meeting` uses); Proxy hears via real
STT and responds. You **stop at each chunk**, read Proxy's trace, grade the
PROCESS/ROUTING against the declared acceptance, fix generalizably if wrong,
replay, and continue.

The pause is simply that **you** call the next command — there are no timeouts or
stalls in the loop.

## What it does NOT do
It never grades the *output* (non-deterministic). It grades the **HOW**: the
process + routing in the trace, declared beforehand in the transcript, against
the real per-turn record. See `../ACCEPTANCE_FORMAT.md`.

## Layout
```
src/harness/
  transcript.py   parse MEETING_TRANSCRIPT.md → ordered CP-1..CP-11 chunks + beats
  config.py       env/.env + CLI flags → HarnessConfig (fail-fast on missing keys)
  replica.py      the speaking bots (RecallTransport + CartesiaTTS + OutputMedia)
  driver.py       setup / play-chunk / replay-chunk / teardown; the gate matrix
  record.py       DID — the session_host per-turn record (the PRIMARY grader)
  monitor.py      bundle N → live-runs/<run>/CP-N/ (JSON + readable summary)
  langfuse.py     OPTIONAL trace fallback (Langfuse is only thinly wired in-repo)
  live.py         live taps (proxy-present, HEARD, notes, artifacts)
  smoke.py        the tiny end-to-end pipe check
  cli.py          the operator CLI
```

## Setup
Uses the repo's existing `.venv` — no new deps. Run from the **repo root** so
`libs.http` and the product transport resolve:

```bash
cd /Users/daksh/Desktop/proxy
export PYTHONPATH="$PWD:$PWD/live-test/harness/src"
alias harness=".venv/bin/python -m harness.cli"
```

Config (from `.env` in the repo root, or flags):
`MEETING_URL`, `RECALL_OUTPUT_MEDIA_URL` (public output-media/tunnel origin),
`CONTROL_PLANE_URL`, `RECALL_API_KEY`, `CARTESIA_API_KEY`,
`LANGFUSE_*` (optional), `RUN_ID` (optional), plus for the monitored smoke:
`PROXY_INTERNAL_TOKEN` (the internal admin bearer the `test-provision` +
`/admin/transcript` taps require) and `MEETING_ID` (the live meeting the HEARD
tap reads — printed by `test-provision`).

**The wake-record bridge (DID trace, sandbox → host).** The per-turn record is
written INSIDE the E2B sandbox (`/tmp/wake_out/<id>.json`). The host `Workroom`
mirrors each record it reads out of the sandbox to a HOST directory named by
`PROXY_WAKE_OUT` (it also honours `PROXY_WAKE_OUT_MIRROR`; either works). So set
the **same** `PROXY_WAKE_OUT` on BOTH the control-plane process and the harness —
the control-plane writes the mirror there, the harness monitor reads it. Default
`live-runs/<run>/wake_out`. Capture the host stdout to `live-runs/<run>/run.log`
for the `[usage]` cache trail.

```bash
# one dir, exported on BOTH processes:
export PROXY_WAKE_OUT=/Users/daksh/Desktop/proxy/live-test/harness/live-runs/cova-run-1/wake_out
mkdir -p "$PROXY_WAKE_OUT"
```

## The loop
```bash
# 0. sanity (offline, no creds): list the parsed chunks
harness chunks

# 1a. put Proxy in the Meet WITHOUT the browser OAuth (real invite_proxy: real
#     Recall bot + the drain's real E2B workroom). Needs PROXY_INTERNAL_TOKEN set
#     on BOTH .env and the control-plane process. Prints the meeting_id to monitor.
harness test-provision --repo pgoel813/cova
#     ... then export the printed id so the HEARD tap can read it:
export MEETING_ID=<printed meeting_id>

# 1b. smoke — one replica joins, says "Proxy, can you hear me?", Proxy responds,
#    the trace is pulled from $PROXY_WAKE_OUT. Confirms the whole pipe.
harness smoke

# 2. setup — join the replica bots + confirm Proxy is present (idempotent)
harness setup

# 3. per chunk: play → bundle → grade → (fix → replay) → next
harness play-chunk  CP-1
harness bundle      CP-1        # writes live-runs/<run>/CP-1/{bundle.json,summary.txt}
#   ... read summary.txt; grade DID against ACCEPTANCE. If it deviates:
#   ... fix the product generalizably, then:
harness replay-chunk CP-1
harness bundle       CP-1
#   ... GO → next chunk:
harness play-chunk   CP-2
# ... through CP-11.

# 4. teardown — remove the replica bots
harness teardown
```

Pass `--run-id` to keep the same run across commands (otherwise each invocation
defaults to a fresh timestamped id — set `RUN_ID` in the env for a stable loop):

```bash
export RUN_ID=cova-run-1
```

## The monitoring bundle (`bundle N`)
Stored to `live-runs/<run>/CP-N/`:
- **SAID** — the driver's beat log (what the replicas spoke, per gate).
- **HEARD** — Proxy's STT for the window + whether the sandbox `MEETING_NOTES.md`
  captured the transcript (proves capture even when Proxy is silent).
- **DID** — the `session_host` per-turn record(s): tools called,
  **reads-vs-resident-cache** (where it answered from), ms timing (ttft/deliver),
  and the `cache_read` trail (residency). **This is the grader.**
- **ARTIFACTS** — the real diff / run output from the E2B sandbox (ground truth
  that it actually ran).
- **OUTPUT** — every channel choice (say/chat/dm/screen/offer/mute) from the
  record's `sent`.
- **RESIDENCY** — the cache trail + which wakes were declared zero-read.
- **ACCEPTANCE (declared)** — the transcript's process/routing per beat, to grade
  the DID against.

## Offline tests
```bash
cd live-test/harness && ../../.venv/bin/python -m pytest -q
```
The whole flow is exercised with mocked transport + mocked sources (no network):
parser, per-gate audio routing, DID record parsing (reads-vs-cache, timing),
bundle assembly.

## Needs live tuning (not buildable offline)
- **`proxy_speaking` signal** — the `wait-for-Proxy-done` / `interrupt` /
  `keep-talking` gates want a real "is Proxy speaking now" signal. There is no
  first-class endpoint yet, so those gates currently fall back to a **bounded
  wait** (flagged `LIVE-TUNING-NEEDED` in the SAID log). Wire `build_proxy_probes`
  in `live.py` to a control-plane speaking flag (or the Proxy output-media channel
  state) at smoke time. `speak-now` / `don't-address` are solid without it.
- **artifacts tap** — needs an E2B `files.read` / `git diff` tap into the sandbox;
  stubbed to empty for now (post-smoke leg 5; bundle records the gap honestly).
- **`confirm_proxy`** — currently probes `GET /admin/meetings` for a "Proxy"
  bot; adjust to the real roster route.

## Wired for the monitored smoke (legs 1–4)
- **HEARD / notes tap** — `live.py`'s `heard` + `notes` now read the control-plane
  `GET /admin/transcript?meeting_id=<id>` (the sandbox `MEETING_NOTES.md` surfaced
  host-side), authenticated with `PROXY_INTERNAL_TOKEN` and scoped to `MEETING_ID`.
  Empty when the id/token is unset or the meeting has no live workroom (honest gap).
- **DID trace bridge** — the host mirrors each per-wake record to `PROXY_WAKE_OUT`
  (see Setup); the `RecordStore` reads it there — no reach into the sandbox.
- **direct provision** — `POST /admin/test-provision` (the `test-provision`
  command) drives the real `invite_proxy` behind the internal admin bearer, so a
  headless smoke needs no Google session.
