# BASICS GATE — everything to verify live BEFORE the transcript run

> The transcript tests the PRODUCT. This gate proves the PHYSICS the transcript stands on.
> Every item: how we test it · what we monitor · the pass bar. Run as short live mini-meetings
> (me driving Riya + Daksh optional), repeated until every item is consistently green.
> Monitors: the staged real-time monitor · per-wake records (tools/timing/cost/queued_ms) ·
> MEETING_NOTES + the resident-cache stats · the server log · our ears on the call.

## A. Warm-up & join
- **A1 Pre-warm at invite** — invite → brain fully ready BEFORE admission. Monitor: provision logs vs admit time. Bar: ready before or within seconds of admission; the FIRST ask pays zero provision penalty.
- **A2 Join hygiene** — consent posts once; no double-join artifacts. Bar: exactly one consent line.

## B. Hearing → context (the flow you named)
- **B1 Transcript → cache, continuously** — speak 5+ lines without addressing it. Monitor: notes lines appear within ~1s each; NO wake fires. Bar: all lines captured, zero false wakes, zero audio.
- **B2 Resident recall** — later ask "what did I say about X earlier?" Monitor: record shows ZERO file reads + correct recall. Bar: answered from the resident cache.
- **B3 Feed never blocks** — speak lines WHILE it's mid-answer. Monitor: notes still append in real time during the turn. Bar: no line waits for a turn to finish.

## C. The reactive turn (ask → prompt → Claude → back)
- **C1 Felt latency, warm** — 5 quick asks. Monitor: per-record queued_ms + ttft + deliver; stopwatch ask-end → audio-start. Bar: queued_ms ≈ 0 (no pipeline queuing); felt start ≈ STT-final + ttft; every stage accounted for, no unexplained gap.
- **C2 Back-to-back asks** — a second ask right after (and DURING) the first. Bar: seen immediately; sane handling; nothing invisible-queued for 30s.
- **C3 Right process by ask type** — chitchat = zero tools · a cova code question = zero-read grounded (once indexed) or one targeted lookup · research = web tools. Monitor: the record's tools list. Bar: matches the declared process.

## D. Speaking (the voice itself)
- **D1 Spoken style** — 3 markdown-tempting asks (sourced research; "give me three options"; a code answer). Bar: audio is clean human speech — zero URLs/markdown/code read aloud; detail/links land in chat.
- **D2 Audio quality** — gapless, ordered, natural pacing; no stutter between sentences.
- **D3 Silence is silent** — incidental "proxy" ×3 (e.g. "my proxy server died"). Monitor: record shows the judgment; TTS call count = 0 for that wake. Bar: zero audio, every time.

## E. Human control & interruption
- **E1 Barge-in** — talk over it mid-answer ×3. Monitor: partial-event → cut timing in the log. Bar: speech stops ≤ ~1.5s (webhook-partial bound — the accepted bar for now; 200ms needs a lower-level audio signal, logged as a known follow-up).
- **E2 "Stop talking"** — instant cut, not a queued turn.
- **E3 Mute/unmute on request** — audio actually stops/resumes.

## F. Channels
- **F1 Chat** — "post that in the chat" → lands in chat, correct content.
- **F2 Gist-aloud/detail-in-chat** — a detailed answer splits correctly by its own judgment.
- **F3 Screen/offer** — shows an artifact; a world-touching change arrives as an offer (no auto-apply).

## G. Real work + background (the transcript's core, pre-checked once)
- **G1 A real cova task** — small code question end-to-end (grounded, correct, spoken cleanly).
- **G2 Background work** — a multi-minute ask while we keep talking. Monitor: notes keep appending during work; present-back happens at the right moment; recall of the chatter after. Bar: all three.
- **G3 Opener discipline** — on a big task: an opener, then meaningful beats, no dead air, no spam; on a silent judgment: NO opener.

## H. Robustness
- **H1 Two meetings back-to-back** — teardown clean, second meeting fresh and correct.
- **H2 Recovery** — kill/restart the control-plane mid-meeting → rejoin/recover honestly, meeting continues.
- **H3 STT mishears** — a garbled/mangled ask (like the real "Roxy" case) → sensible behavior (clarify or stay silent, never a confident wrong act).

## Exit
Every item green repeatedly (the flaky-prone ones — B1, C1, D3, E1 — at least 3× each), all traces stored
to live-runs/, no unexplained latency anywhere in the chain. THEN the full transcript run.
