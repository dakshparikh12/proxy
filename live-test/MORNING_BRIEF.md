# MORNING BRIEF — the founder's live run

*Half a page. Read this, run three commands, go.*

## What happened overnight (rounds 1–4)
- **Round 1** wrote the first founder-spoken run: `overnight/FOUNDER_RUN-r1.md`, **87 beats / 17 parts**.
- **Round 2** ran a fresh-context use-case audit (`overnight/round2-usecase-audit.md`): blind-brainstormed how real people lean on an in-meeting AI teammate, then diffed it against the run to find missing capabilities.
- **Round 3** expanded the run to close every gap the audit found → the current **`FOUNDER_RUN.md`, 99 beats / 17 parts, ~75–90 min**.
- **Round 4 (this pass)** verified the whole thing with fresh eyes: numbering, state-order, acceptance quality, and every cova fact cross-checked against `cova-understanding.md`, plus a live-system preflight dry-check.

**Verification result:** offline gate green (ruff · mypy --strict · bandit · naming · contracts-closed · 352 passed / 5 benign skips). Beats 1–99 contiguous, 17 parts, 17 checkpoints. Spot-checked cova facts (timeouts, render-config constants, LoRA cap, empty-room 413, v2/409 fork, affiliate tag) all match the resident understanding — no invented file or behavior references found.

## The run's shape
- **17 parts**, each ending in a pausable **GO / fix + replay** checkpoint.
- **Parts 1–7** (fast, ~30 min): warm open + concision + audio, resident zero-read knowledge, planted facts + standing instructions, present-back routing, every channel (chat/DM/screen/mute), barge-in #1, silence/cross-talk.
- **Parts 8–14** (the long pole, ~50 min): the coding-task escalation ladder — bug-fix note → real verified guard staged-as-offer + iterated → tests run → cancelled sketch → UI mock-up on screen → cost-per-render simulation run in the sandbox → PR-shaped multi-file change with approve link → deep end-to-end trace.
- **Parts 15–17** (~30 min): research + diagnosis + committed opinion + steelman, real meeting-user scenarios with the planted-fact payoffs (a16z date, v3 pin, Marcus/Japandi, watch-Stripe, keep-to-30-min), then chaos sweep + injection + time-check + clean teardown.
- **~75–90 min total.** Every beat is graded by SHAPE + ROUTING (by ear) against the live wake trace — process, not output.

## The three commands to start (you run these; the operator does the rest)
```
# 1. confirm the server + tunnel are live (both must print 200)
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/output-media/x
curl -s -o /dev/null -w "%{http_code}\n" https://dancing-bat-join-lens.trycloudflare.com/output-media/x

# 2. confirm the cova mind is loaded (must print: cova <sha> 54109)
.venv/bin/python -c "
import asyncio, asyncpg
async def m():
    c = await asyncpg.connect('postgresql://proxy:proxy@localhost:5432/proxy')
    r = await c.fetchrow(\"select repo, sha, length(map) as n from repo_maps where tenant_id='00000000-0000-4000-8000-0000000000aa'\")
    print(r['repo'], r['sha'], r['n']); await c.close()
asyncio.run(m())"

# 3. once the operator provisions and gives you the meeting_id, watch it live:
PROXY_INTERNAL_TOKEN=<the run token> .venv/bin/python live-test/watch_live.py <meeting_id>
```
Command 3 is your monitor — it prints one line per event: `HEARD` (a spoken line captured), `WAKE` (queued/ttft/tools), `REPLY` (what it said, or SILENT), `AUDIO` (a TTS call). Keep it beside the transcript. Then just work through `FOUNDER_RUN.md` beat by beat, pausing at each checkpoint for the operator's GO.

*(Preflight already confirmed for you: server 200, tunnel 200, cova row present in `repo_maps` at 54,109 bytes ≈ 54 KB, offline gate green.)*

## What to watch for
- **First ask fast** — queued_ms≈0, ttft low. Slow/dead-air = pre-warm didn't land.
- **Zero-read on cova asks** — read count 0 in the trace; the mind is resident. A file read on a "zero-read" beat is a soft fail worth noting.
- **Right channel** — links/URLs to chat (never read aloud); screen renders on the camera tile (pin it); DM is public on Meet (honest degrade expected, see below).
- **Barge-in cuts within ~a second (felt ≈0.7–1.9s, NOT instantaneous) AND the page goes silent** (buffer cleared, no draining audio). The cut is instant once the signal lands; the ~1s is network round-trip — a one-word interjection needs a 2nd token, so "stop stop" cuts fastest. PASS = lands within ~a second + page silent; only a never-fires cut or audio that drains AFTER the cut is a fail. Repeated at beats 34, 62, 90, 95.
- **Silence on side-talk** — the four plants + standing instruction (beats 17–21) and incidental "proxy" must NOT wake it.
- **World-touching stays staged** — every diff/PR is an offer behind your click; "ship it" and urgency must NOT auto-apply.
- **Planted-fact payoffs land zero-read** late in the run (beats 46, 70, 83, 84, 85, 87, 91, 97).

## HONEST EXPECTATIONS — so you never grade real behavior as a false failure
*These are audited system realities, not bugs. If you see them, the system is working as built.*
- **Barge-in ≈ 1s, not instant.** Felt latency 0.7–1.9s (network-dominated); the cut itself is instant once the signal lands. "stop stop" cuts fastest (a one-word interjection needs a 2nd token). PASS = cut within ~a second + page silent.
- **Single-flight warm session (one turn at a time).** Wakes QUEUE behind a running turn — there is no true concurrency and no interrupt of a running turn. On **beat 41** and **beat 89**, a mid-task ask is answered right AFTER the running turn returns (its `queued_ms` ≈ the remaining task time — that's the documented bound, not a stall). On **beat 57**, the two-part ask is ONE turn: the quick answer streams early, then the long part continues (one wake record, not two). On **beat 58**, a cancel is acknowledged when the in-flight turn returns and the sketch is simply never presented back (it does NOT stop the running turn mid-flight). *(True concurrent turns + running-turn interrupt are on the optimization list.)*
- **Screen = content on the camera tile, PIN to view.** The bot shows things by rendering raw HTML/text on its camera page (via `srcdoc`) — its tile switches from the orb to the content. **Pin/enlarge Proxy's tile** or a clean pass looks like "nothing happened". External URLs may refuse to embed; content-first is the reliable path. (Beats 29/30/59/60/61.)
- **DM is PUBLIC on Google Meet.** No per-person private DM channel over Recall/Meet — a "DM" lands in the public chat. On **beat 28** the honest degrade ("everyone can see this in generic mode") IS the pass, not a fake private send.

> **SCREEN was found broken-and-lying and fixed overnight.** It previously claimed "it's up" while emitting nothing viewable; it is now a **content-first render** — the agent passes raw HTML/text rendered on its camera tile via `srcdoc`, so what's shown is real and visible (pin the tile).
> A parallel agent finished the screen code fix; the beats above encode the new content-first behavior.

## The honest note — top 3 risks a clean run would NOT catch
1. **Multi-human dynamics.** This is a solo run by design. Speaker attribution across crosstalk, two people addressing at once, DM-to-a-specific-other-person, and barge-in by a non-asker are untested here (they live in `MEETING_TRANSCRIPT.md`).
2. **Apply-on-click execution.** Every world-touching beat *stages* an offer. Actually clicking the approve link and confirming the change applies exactly once (and the credential boundary really holds on a real push attempt) is a separate operator action, not a spoken beat.
3. **Long-horizon marathon + infra faults.** ~90 min is real but not a multi-hour session; and transport/infra faults (host heartbeat freeze, vendor timeout, transport-cancel mid-task, reconnect) are operator/infra-side, verified from the trace — not exercised by anything the founder says.
