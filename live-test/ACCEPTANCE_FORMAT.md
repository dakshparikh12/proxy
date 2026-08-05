# Acceptance format — grade the PROCESS + ROUTING, declared beforehand (the contract)

We are **not grading the output.** The output is non-deterministic and will vary; it must be *good*
(grounded, verified, above-and-beyond) but that is not the check. **The check is the "how": the exact
PROCESS Proxy should take and the ROUTING it should choose — the efficient path we envisioned —
declared BEFORE the run, then compared against the real agent trace.** If the trace deviates from the
declared process/routing, that's either a bug or an optimization signal → stop, understand it, fix.

Judged per **chunk** (not every sentence — every meaningful chunk / reactive moment).

## What we declare per chunk (beforehand)
- **EXPECTED PROCESS** — the exact internal path it should take, e.g.:
  - answers **from the resident cache with ZERO reads** (transcript + codebase already known)
  - **one targeted lookup** for a tail detail (not a full re-explore)
  - **parallelizes** independent sub-work / **runs two things at once** / keeps a long task in the background
  - **asks a clarifying question** on a vague ask before acting (doesn't guess)
  - **runs + verifies** on real data before saying "done"; grounds in real `file:line` or says "not found"
  - hits a blocker → **does the work it can AND flags the blocker** to continue
  - forms a real **opinion/recommendation** when asked ("how should we move forward") — reasoned, not hedged
- **EXPECTED ROUTING** — the channel + the way it comes back to the meeting: gist aloud · detail in chat ·
  artifact on screen · DM when personal · change as an offer (behind a click) · mute · **present-back at the
  right moment**. The routing must be the *efficient* one we intended.
- **OUTPUT (should be good, NOT the grade)** — a one-line note of what a good result looks like, for sanity
  only. We do not score the exact output here.

## Graded against the TRACE (this is the point)
At each chunk's checkpoint we read the agent trace and compare the ACTUAL process + routing to the DECLARED:
- turns · tools called · **reads vs. resident-recall** · what ran in parallel/background · the channel chosen ·
  whether it clarified / verified / offered · latency.
```
declared process met?  ·  declared routing met?  ·  (output sane?)
   matches → GO (log to the coverage ledger)
   deviates → STOP → understand WHY from the trace → it's a bug or an optimization → fix → re-run the chunk
```

## Core things get declared + tested MANY times, in combination
The non-negotiable internal processes are exercised **repeatedly across the meeting, in different forms and
at the same time**, and the coverage ledger tracks how many times each was hit:
- **Caching** — transcript continuously resident (recall early facts much later, zero reads) AND codebase
  understanding resident (zero-read grounded answers).
- **Parallelism / background** — two things at once · a long task while the meeting keeps talking · a new ask
  mid-work · concurrent asks.
- **Present-back / communication** — bringing results back to the room at the right moment, the right way.
- **Fast-from-memory answers · right routing every time.**

The output may vary; the **process and the routing must be exactly what we envisioned, every time.**
