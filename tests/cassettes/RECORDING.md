# One-time cassette recording — procedure + honest free-vs-paid account

Cassettes are recorded **once** by a human against real vendor accounts, then committed and replayed
forever in CI (`--record-mode=none`). This file is the procedure and an **honest** account of what is
free/sandbox vs. what needs a paid account — per the redesign's rule: *report honestly if anything
can't be automated for free.*

> **Bottom line (honest):** all three vendors require a human-created account + API key. An automated
> agent cannot sign up or self-provision these, so **the recording step must be run by a founder** with
> credentials. Two of the three (AssemblyAI, Cartesia) are recordable within a **free tier**; Recall.ai
> is the one that generally **consumes billable/trial credits** because a cassette requires a real bot
> joining a real meeting. Vendor pricing and free-tier terms change — verify current terms before
> recording; the figures below are directional, not a guarantee.

## Prerequisites

Put keys in the environment (or `.env`, already git-ignored) — never on the command line, never in a
committed file:

```
export RECALL_API_KEY=...
export ASSEMBLYAI_API_KEY=...
export CARTESIA_API_KEY=...
```

## Record

```bash
# Records any MISSING cassette by making the real call; replays existing ones. Never overwrites a
# committed cassette unless you pass --record-mode=all explicitly.
bash scripts/record_cassettes.sh                     # all reality/negative/e2e tiers
bash scripts/record_cassettes.sh -k cartesia         # just the Cartesia ones
```

The script sets `--record-mode=once`, runs the tier tests, then runs the cassette-hygiene scan and
**aborts if any real secret is detected** in a freshly written cassette.

## Per-vendor free-vs-paid (honest, verify current terms)

| Vendor | What a cassette needs | Free / sandbox? | Honest caveat |
|---|---|---|---|
| **AssemblyAI** (STT — `hearing`, transcript) | Upload a short audio clip, poll the transcript | **Yes — free signup includes trial credits**; a few-second clip is well within them | Free credits are finite; once exhausted, transcription bills. One short clip per cassette is cheap. |
| **Cartesia** (TTS — `speak`) | Synthesize one short line to audio bytes | **Yes — free tier includes a monthly character allowance**; one short line is a few dozen characters | Free tier is rate/character limited; fine for a handful of golden-path lines. |
| **Recall.ai** (meeting bot — `join`, `events`, transport) | Launch a bot that joins a real meeting and returns webhooks/media | **Not cleanly free** — requires a real meeting for the bot to join and generally **consumes trial/billable bot-minutes** | This is the one that can't be fully automated for free. Options: (a) a Recall.ai **test/sandbox meeting** if your plan offers one; (b) a throwaway Google Meet you host + a short join; (c) record only the **webhook-payload** shape (cheaper) and defer live-media e2e. Budget a small paid spend or trial credits. |

**Negative cassettes** (the `negative` tier) record a *failure* response — a 4xx/5xx, a timeout, or a
truncated body. For AssemblyAI/Cartesia these are free (send a deliberately bad request). For Recall a
malformed request also returns an error cheaply without consuming a full bot-minute.

## What is committed

- Cassettes: `tests/cassettes/<vendor>_<scenario>.yaml` — credentials scrubbed to `REDACTED`.
- Never committed: real keys, `.env`, any cassette that fails the hygiene scan.

## If you cannot record right now

The machinery is complete and the ladder is wired. Until cassettes exist, the reality/negative/e2e
rungs are reported by the ladder-runner as **`pending-cassette`** (not green, not silently skipped) —
an honest, visible state. Recording later turns them green with no further code change.
