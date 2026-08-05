# BASICS GATE — battery run 1 (live) — bug ledger + check results
Meeting: https://meet.google.com/xqw-roey-ohm · date: 2026-08-05
| # | Check | Result | Evidence / bug |
|---|-------|--------|----------------|
| 1 | A1 pre-warm | **BUG FOUND → FIXED → PASS** | Fix agent REPORTED bug-4 pre-warm as done but never landed the code (rubber-stamp escape). Wired for real at both invite paths (dev_smoke + meetings_route). Retest: invite 03:16:03 → E2B sandbox 201 at 03:16:04 (1.5s, pre-admission). |
| 2 | A1b first-ask warm | **PASS** | queued_ms=114.8, ttft 2.1s, spoke ~3.3s, zero tools, clean reply. Cold-start penalty gone. |
| 3 | B1 silent capture | **BUG** | Unaddressed chatter woke it TWICE and it spoke ("Got it — Aug 20th…", "Got it — $750 hard cap…"). Cause: its greeting ended "What's on your mind?" → follow-up latch treated side-chatter as replies. Fix: latch closes after one reply + prompt principle (side-talk ≠ your reply). |
| 4 | Audio clarity | **BUG (founder #1)** | Voice choppy/unclear; replica leading words clipped in STT. Suspect: page WebAudio player naive chunk scheduling (gaps between per-sentence TTS calls), no jitter buffer. Fix in flight. |
| 5 | Barge-in live | **BUG (founder #2)** | Host-side cut lands but the PAGE keeps playing already-buffered audio → feels like no barge-in at all. Fix in flight: cut control-frame → page stops+clears buffers; interrupted remainder dropped; responds to the interruption. |
| 6 | Answer length | **BUG** | Replies verbose for simple asks. Fix in flight: prime principle — concise conversational default (1-3 sentences), depth only when asked, gist-aloud/detail-in-chat. |

## RUN-DAY FIX LIST (apply at CHECKPOINT 1 unless blocking)
| # | Fix | Why (evidence) | Type |
|---|-----|----------------|------|
| F1 | **Follow-up window**: after a Proxy turn, route the next human lines (short window) to the MODEL's judgment instead of dropping at the name-gate | Founder had to re-address ("Cool. The audio was choppy…" had no 'proxy' → dropped; he was obviously mid-exchange). Also explains "said it a few times" from attempt 1 | wiring (general) |
| F2 | **Jitter buffer 150ms → ~450ms** in the page player | "A little choppy" persists; sentence joins are gapless in-trace → residual = tunnel jitter > 150ms → mid-sentence underruns | page (needs fresh bot) |
| F3 | **Concision watch**: Beat 2 gave 3 sentences + a check-in tail (82 words) on a "2-3 sentences" ask | borderline; tighten the principle only if the pattern repeats | prompt (hold) |
