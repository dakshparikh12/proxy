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
