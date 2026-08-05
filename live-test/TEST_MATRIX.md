# Live Test — the full list of everything to verify (source for the transcript)

> This is step 1: **everything that must work.** The one end-to-end transcript is built to
> hit every item below, several times, in different spots. Each item names what it tests, the
> expected Proxy behavior, and the pass criterion. Repo under test: **cova** (fresh, unseen).
> `[bot-gate]` = an explicit instruction the replica bots enact (speak-now / wait-for-Proxy /
> keep-talking / interrupt). `[accept]` = how WE judge pass/fail from real data.

## A. Foundation pipes (must work before anything else)
1. **Proxy joins** — bot in the room. `[accept]` present + consent line posts.
2. **Both replica bots join** — two "humans" in the room, each can speak.
3. **Hear + transcribe** — a bot speaks; Proxy receives it via real STT. `[accept]` the line appears in Proxy's transcript, words correct (incl. code terms).
4. **Transcript flows into context/cache** — the meeting accumulates, resident. `[accept]` Proxy later recalls an earlier line unprompted (was-in-the-room).
5. **Speak back** — Proxy replies via Cartesia, heard in the room. `[accept]` audible, gapless, natural.

## B. Codebase understanding resident (it *knows* cova)
6. **Zero-read grounded answer** — asked where something lives → correct `file:line`, no reading. `[accept]` path/line verified real, no file read in trace.
7. **Trust test** — a fact only the understanding could provide, answered zero-read. `[accept]` correct + grounded.
8. **Knows-where-to-look** — for a detail not in the understanding, ONE targeted lookup. `[accept]` single lookup, correct.

## C. Simple reactive round-trip
9. **Chitchat** — "how's it going" → quick natural reply. `[accept]` fast, human, no over-work.
10. **Instant opener** — before digging into any real task, an ack ≤~2.5s. `[accept]` first audio ≤ budget.
11. **Simple lookup delivered** — a small code question → correct answer spoken. `[accept]` correct + timely.

## D. Real work + present-back (the artifacts — SHOW them)
12. **Real coding task** — implement a real change in cova → actual code, run/verified, offered. `[accept]` the diff exists, ran, is correct; saved to live-runs.
13. **Real drafting task** — write a real document → actual artifact on screen/chat. `[accept]` the doc exists, is good; saved to live-runs.
14. **Web research** — research a topic → cited answer. `[accept]` grounded, cited.
15. **Above-and-beyond** — output is structured/verified, not minimal. `[accept]` judged clearly above baseline.
16. **Right channel** — gist aloud, detail in chat, artifact on screen, change as offer. `[accept]` channel choice correct per beat.

## E. The hard concurrency scenarios (your focus)
17. **Hear-while-working** — bots keep talking during a long task `[bot-gate: keep-talking]`. `[accept]` transcript keeps flowing into Proxy's context while it works (proven by later recall).
18. **Present-back at the right moment** — Proxy delivers its result though the convo moved on. `[accept]` delivered, clearly re-anchored to the ask.
19. **No dead air** — during long work, opener + meaningful beats, not silence, not spam. `[accept]` no gap > threshold, beats meaningful.
20. **New ask mid-work** — a second ask arrives while working `[bot-gate: speak-now]`. `[accept]` handled without dropping the first (head-of-line).
21. **Multiple / parallel tasks** — two real tasks in flight. `[accept]` both completed correctly, independent.

## F. The nuances (human-like interaction)
22. **Clarify on messy ask** — ambiguous request → ONE clarifying question `[bot-gate: wait-for-Proxy, then answer]` → continues. `[accept]` asks not guesses; resumes correctly.
23. **Blocker mid-work** — hits a blocker → says it + continues. `[accept]` communicated honestly, work continues.
24. **Ask→continue** — Proxy asks, bot answers, Proxy resumes the same task. `[accept]` resumes, no restart.
25. **Barge-in** — a bot talks over Proxy mid-sentence `[bot-gate: interrupt]`. `[accept]` speech stops fast (<~200ms).
26. **Self-echo / no-headphones** — Proxy's own voice on the mic. `[accept]` never wakes/interrupts itself.
27. **Cross-talk → silence** — bots talk to each other, "proxy" said incidentally `[bot-gate: don't address Proxy]`. `[accept]` no false wake.
28. **Concurrent asks** — two bots address Proxy at once `[bot-gate: both speak-now]`. `[accept]` both handled sanely.
29. **Honest degrade** — a can't-do / couldn't-run case. `[accept]` says so plainly, never fakes.
30. **No confabulation** — a "where is X" for something absent. `[accept]` "not found by this method," no guess.

## G. Channels / capabilities
31. **Chat (broadcast)** — posts to meeting chat. `[accept]` appears, correct content.
32. **DM** — direct message to one participant. `[accept]` delivered to the right person only.
33. **Screen-share** — shows an artifact. `[accept]` visible, correct, readable.
34. **Mute / unmute** — on request. `[accept]` audio actually stops/resumes.
35. **Offer (human-control)** — world-touching change staged behind a click. `[accept]` card posted; applies only on click.

## H. Reliability
36. **No crashes** — over a full-length meeting. `[accept]` runs end-to-end, no crash.
37. **Recovers from a hiccup** — vendor/network blip (transport-cancel). `[accept]` recovers, meeting unaffected.
38. **Long-meeting memory** — no forgetting late in a long session. `[accept]` recalls early content late.
39. **Cost tracked** — per-meeting cost recorded. `[accept]` number captured.

---
### How this becomes the transcript
Every item is a **beat** in one long meeting. Order = the natural flow: join → hear → it-knows-cova →
simple round-trip → real work (show artifacts) → the hard concurrency stretch → nuances woven throughout →
channels → reliability. Nuances (barge-in, clarify, cross-talk, self-echo) recur in several places so we
confirm they fire *consistently*, not once. Each beat carries: the messy human line(s), the `[bot-gate]`
the bots enact, the expected Proxy behavior, and the `[accept]` we judge live.
