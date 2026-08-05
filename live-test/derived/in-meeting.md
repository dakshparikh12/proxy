# services/in-meeting — Exhaustive derived test surface

Everything that must be tested to trust the in-meeting engine is customer-deployable in ANY
meeting scenario. Grouped by file/component. Category legend: **[CAPABILITY]** behavior/feature ·
**[WIRING]** integration point (caller/callee/contract) · **[EDGE]** boundary/concurrency ·
**[FAILURE]** dependency error/timeout/malformed/cancel/race + expected behavior · **[NUANCE]**
timing/ordering/idempotency/isolation/secrets/human-control/injection/self-echo/wake-gating.

Scope note: the in-meeting tree is `src/in_meeting/*` (engine, connection, output-media, prime,
speak, workroom, sandbox MCP, warm session host) + `src/transport/*` (recall/tts/consent/join/
media/signals/config/seams/external). The reactive LOOP that drives them
(`control_plane.meeting_session`, `meeting_runtime`, `relay`) lives one tree over but is the
primary caller — its contract with in-meeting is covered here and in the cross-subsystem section.

---

## in_meeting/meeting_connection.py — the host-side `to_meeting` driver

Product: the ONE dynamic interface Proxy uses to reach the room. Proxy picks content + medium
(say/chat/dm/screen/offer/mute); this maps that choice to the physical Recall/Cartesia op. Holds
meeting creds host-side (never in sandbox). Drives barge-in stop + self-echo record.

### to_meeting routing
- [CAPABILITY] `medium="say"` (and aliases `speak`/`voice`) synthesizes + plays into the room and returns `MeetingSend("say", True)`.
- [CAPABILITY] `medium="chat"` (aliases `message`/`post`) posts to meeting chat via `room.post_chat(bot_id, content)` and returns ok.
- [CAPABILITY] `medium="dm"` (aliases `direct`/`whisper`) with a valid `to` sends via `room.send_dm(bot_id, content, to)` and returns `detail="to=<id>"`.
- [CAPABILITY] `medium="mute"`/`silence` calls `audio_mute(True)` THEN `room.mute(bot_id)` and returns ok.
- [CAPABILITY] `medium="unmute"`/`resume` calls `audio_mute(False)` THEN `room.unmute(bot_id)` and returns ok.
- [CAPABILITY] `medium="screen"`/`show`/`share` calls `screen(content)` and returns the shown URL in `detail`.
- [CAPABILITY] `medium="offer"`/`propose`/`draft`/`approve` stages via `offer(content, to)`, then posts the approve link to chat, returns approve_url in detail.
- [CAPABILITY] medium is normalized: whitespace-stripped, lowercased, empty/None ⇒ "say".
- [EDGE] unknown medium (e.g. `"yell"`) falls back to speaking the content, returns `detail="unknown medium 'yell' → said"` — never silently drops the agent's words.
- [EDGE] `dm` with no `to` returns `MeetingSend("dm", False, "no recipient given")` — never calls `send_dm` with an empty recipient.
- [EDGE] `screen` when `screen` sink is None returns `("screen", False, "screen surface not available")`.
- [EDGE] `offer` when `offer` sink is None returns `("offer", False, "offer path not available")`.
- [EDGE] `offer` that yields an empty approve_url ("" from stage) returns ok=True but does NOT post a chat line (no "approve:" spam on an unavailable stage).
- [EDGE] `mute`/`unmute` when `audio_mute` is None still fire the Recall room verb (pre-wiring behavior) and return ok.
- [NUANCE] mute silences the webpage channel FIRST (where PCM rides) then the Recall clip verb — order matters so the human hears silence immediately (Law 3).
- [FAILURE] any sink raising inside `_route` is caught; `to_meeting` returns `MeetingSend(ok=False, detail=<exc>)` and NEVER re-raises (a bad send can't crash the meeting — hard rule "tool handlers return errors, never throw").
- [FAILURE] a sink exception with empty `str(exc)` falls back to `exc.__class__.__name__` in detail (never a blank error).
- [NUANCE] every send (ok or not) is appended to `self.sent` in order — the host-observed audit record, never the model's prose.
- [WIRING] `TO_MEETING_TOOL` schema is the single shared definition (content required, medium+to optional) used by both the MCP wrapper and tests; must not drift from the in-sandbox server's tool signature.

### barge-in + cut latch
- [CAPABILITY] `barge_in()` raises `cut_latched=True` AND awaits `speak.cut()` — the physical stop of in-flight speech (Law 3).
- [NUANCE] while `cut_latched` is up, a subsequent `say` returns `("say", False, "dropped: barged-in")` and does NOT call `speak.say` — later sentences of the interrupted turn are silenced.
- [NUANCE] `cut_latched` silences ONLY the spoken channel — a `chat`/`dm` chosen after a barge-in still lands (voice barge-in silences voice, not typing).
- [NUANCE] `begin_turn()` lowers the latch so the NEXT wake's spoken output flows; the latch only ever silences the one interrupted turn.
- [FAILURE] `barge_in()` never raises the loop even if `speak.cut()` faults (cut is the never-throw barge-in primitive).
- [EDGE] two `barge_in()` calls in a row are idempotent (latch stays up; second cut is harmless).
- [EDGE] `begin_turn()` on a fresh connection (latch already down) is a safe no-op.

### self-echo record (`_record_spoken` / `spoken`)
- [CAPABILITY] a successful `say` appends `(wall_ts, text)` to `spoken` — the ground truth the loop matches echoes against.
- [NUANCE] ONLY the `say` channel records to `spoken` — chat/dm are text and never echo acoustically.
- [NUANCE] a `say` that was dropped by the barge-in latch does NOT record (it never actually spoke).
- [EDGE] empty/whitespace `content` on `say` speaks nothing recordable — `_record_spoken` skips blank text.
- [EDGE] `spoken` is bounded to `_SPOKEN_LOG_MAX` (64) — a marathon meeting never grows it unbounded; oldest entries are dropped.
- [NUANCE] the wall-clock `time.time()` stamp must span playback + STT latency for the loop's 45s echo window to work (timing coupling with `meeting_session._ECHO_WINDOW_S`).

---

## in_meeting/output_media.py — the Recall Output-Media webpage + audio channel + orb

Product: the surface Recall streams as the bot's camera/mic. Serves the orb page + a per-meeting
WS feed carrying PCM (binary) and state (JSON: speaking/screen). The speak path writes into it.

### OutputMediaChannel — buffering + backpressure
- [CAPABILITY] `write_audio(pcm)` enqueues one PCM frame and wakes the drain loop.
- [CAPABILITY] `set_speaking(bool)` enqueues a `{"speaking": bool}` JSON state frame for the orb pulse.
- [CAPABILITY] `set_screen(url)` records the URL, enqueues `{"screen": url}`, and returns the cleaned URL; empty url returns to the orb.
- [CAPABILITY] `screen_url()` returns the currently-shown surface ("" = orb).
- [NUANCE] frames (bytes + str) ride ONE ordered deque so a speaking-state flip stays ordered relative to the audio around it (orb never flickers out-of-sync with audio).
- [EDGE] buffer is bounded to `MAX_BUFFERED_FRAMES` (256, ~25s audio); overflow drops the OLDEST frame atomically (`deque(maxlen=)`) — live audio never stalls, a dead page never grows memory.
- [NUANCE] producers (`write_audio`/`set_speaking`) never block and never raise, even on a different event loop than the WS handler.
- [FAILURE] `_notify` across a dead consumer loop swallows `RuntimeError` (loop gone) — frames stay buffered for a reconnect, no crash.

### mute (C5, Law 3)
- [CAPABILITY] `mute()` sets `_muted=True` AND drops all in-flight buffered PCM (bytes) immediately — audio stops NOW.
- [NUANCE] `mute()` KEEPS ordered state frames (str: speaking/screen) so the page stays in sync while muted.
- [NUANCE] while muted, every `write_audio` enqueue is DROPPED (returns early) — no PCM plays into the room until unmute.
- [CAPABILITY] `unmute()` clears `_muted`; later `write_audio` rides again.
- [EDGE] `mute()`/`unmute()` are idempotent (double mute/unmute harmless).
- [CAPABILITY] `muted()` reflects the current state.
- [NUANCE] a page attaching WHILE muted still gets state frames but no dropped PCM — the human-mute wins regardless of page connect timing.

### page attach / drain / detach (`_attach`/`_pump`/`_detach`/`_close`)
- [CAPABILITY] a late-attaching page receives the retained buffered tail (wake set on attach delivers immediately).
- [NUANCE] latest attach wins: `_attach` supersedes the prior wake (releases the stale pump so it exits) — only one live page per meeting.
- [EDGE] `_pump` on a superseded token stops draining (checks `_attachment is token`).
- [FAILURE] a `send_bytes`/`send_text` failure mid-frame puts the frame BACK (appendleft) and re-raises so a reconnecting page still gets it — no dropped frame on transient send fault.
- [EDGE] a frame that lands between the drain and the `wake.clear()` is not lost (re-checks `_frames` before waiting).
- [NUANCE] `close_channel(meeting_id)` is the deliberate end-of-meeting teardown; a page disconnect alone KEEPS the channel (reconnect allowed).
- [FAILURE] `_close` clears frames and sets the wake so a blocked pump exits promptly.
- [EDGE] `channel_for` creates on first use and returns the SAME channel per meeting id (registry idempotency).
- [NUANCE] tenant isolation: two meeting ids get distinct channels; `close_channel` never leaks a channel past meeting end (registry pop).

### WS route (`build_output_media_router` / `_await_disconnect`)
- [WIRING] `GET /output-media/{meeting_id}` returns the orb HTML; `WS /output-media/{meeting_id}/ws` is the feed — mounted by control-plane via `app.include_router(output_media.router)`.
- [FAILURE] a failed `websocket.accept()` returns cleanly (never-throw boundary) — a churny page can't crash the route.
- [NUANCE] on WS exit, `_detach(token)` MUST land before any await in the finally (detach always happens even under re-cancel).
- [FAILURE] `_await_disconnect` returns on `websocket.disconnect` or any receive exception — normal churn, clean exit.
- [FAILURE] the test-client cancel scope re-cancelling in the final gather is swallowed — detach already happened, exit stays clean.
- [NUANCE] a page reconnect (WS onclose → setTimeout(connect,1000)) resumes cleanly against the retained buffer.

### the inline orb HTML page
- [CAPABILITY] the page plays s16le/16kHz PCM gaplessly (rolling `nextStartTime` clock, never scheduling in the past).
- [CAPABILITY] the page toggles the `speaking` pulse class on `{"speaking": bool}` messages.
- [NUANCE] resamples on playback if the AudioContext ended at a different hardware rate (buffer declares 16kHz).
- [EDGE] a zero-sample (empty) PCM chunk is a no-op (`sampleCount === 0` returns).
- [FAILURE] a non-JSON text frame is ignored (try/catch returns) — no page crash on malformed state.
- [NUANCE] SECURITY: a hostile `meeting_id` cannot smuggle `</script>` — `_render_page` JSON-encodes the WS path and escapes `<` to `<` (XSS/script-injection guard through the URL).
- [EDGE] the page must render standalone in Recall's headless browser — no external assets, no build step, autoplay allowed + `audioCtx.resume()` fallback.
- [NUANCE] the SAMPLE_RATE in the page (16000) must match the synth's output (`tts._SAMPLE_RATE_HZ` = 16000) and `SAMPLE_RATE_HZ` const — a mismatch = chipmunk/slow audio.

---

## in_meeting/prime.py — the workroom behavioral prime + MEETING_INFO

Product: `CLAUDE.md` seeded into the sandbox — who Proxy is + how to behave, dynamic (no
situation→action rules). `MEETING_INFO.md` tells Proxy who's in the room.

- [CAPABILITY] `WORKROOM_PRIME` instructs: speak by writing, use `mcp__meeting__to_meeting` ONLY for non-spoken channels, ground in file:line or say "not found", verify by running or say you couldn't.
- [NUANCE] the prime must stay lean + byte-stable so it stays prompt-cached (a byte change invalidates the resident cache → latency regression on every wake).
- [NUANCE] the prime forbids overstating: "I couldn't run this here" honesty line, never claim verified when not run (Law 2).
- [NUANCE] the prime encodes the offer-is-delivery rule: world-touching changes go via `medium='offer'`, never described aloud (Law 3 human-control gate).
- [NUANCE] the prime names the tool by its EXACT MCP name `mcp__meeting__to_meeting` + "already loaded, don't search" — must match the actually-registered server name (`meeting` + `to_meeting`).
- [NUANCE] the prime tells Proxy to lead with the answer (no wasted "on it" when reply is a second away) — coupling with the opener watchdog's suppression.
- [CAPABILITY] `render_meeting_info` renders title, agenda, participants; renders `- Name (id: <pid>)` when an id is known, `- Name` otherwise.
- [CAPABILITY] renders a DM note: set `to` to the participant id shown, never the name — so `send_dm` gets a valid Recall id.
- [EDGE] `_participant_line` accepts a bare string, a `(name, id)` tuple, or a `{"name","id"}` mapping; empty/absent id degrades to name-only.
- [EDGE] `render_meeting_info` with no metadata renders `(no meeting metadata available)` — never an empty file.
- [EDGE] name/id are stripped; a `(name, id)` tuple of length 1 renders name-only without IndexError.
- [NUANCE] MEETING_INFO must carry no internal component names (naming law over user-facing artifacts, though this is model-facing).

---

## in_meeting/speak.py — SpeakPipe: text deltas → sentences → synth → channel

Product: the physics pipe turning Proxy's streamed text into spoken audio, with sentence
buffering, ordering, barge-in cut, and never-throw. THIS is the host-side voice channel used on
the LIVE path (distinct from the in-sandbox streaming in session_host).

### sentence buffering + synth
- [CAPABILITY] `say(delta)` accumulates deltas; completed sentences (terminator `.!?` + whitespace/EOB) synthesize immediately for low first-word latency.
- [CAPABILITY] the trailing partial (no terminator) flushes after `flush_after_s` (0.5s) of quiet — timer resets per delta.
- [NUANCE] ONE synth in flight at a time (FIFO worker) — overlapping synths would interleave audio garbled.
- [EDGE] `_split_sentences`: `client.py.` at EOB closes as one sentence (inner dots followed by letters don't); abbreviations/decimals may split a beat early (documented, acceptable).
- [EDGE] whitespace between sentences is consumed; a whitespace-only buffer strips to no tail.
- [CAPABILITY] `commit_tail()` pushes this turn's buffered partial into the queue WITHOUT waiting for drain — so a following turn's first delta can't concatenate onto it as one merged sentence.
- [NUANCE] `commit_tail` preserves cross-turn FIFO order without over-serializing concurrent turns (holds the one-mouth lock only long enough to close its own utterance).

### speaking state + orb
- [NUANCE] `set_speaking(True)` fires right before the FIRST audio write of an utterance; `set_speaking(False)` only once idle (queue drained AND no tail buffered) — never toggled per chunk.
- [NUANCE] speaking stays True across a pending tail so the orb doesn't flicker mid-utterance.
- [EDGE] a whitespace-only tail delta still wakes the worker so the idle branch fires `set_speaking(False)` — otherwise the orb stays lit forever.
- [CAPABILITY] `speaking` property is True while ANY of: audio playing, worker mid-sentence, sentence queued, or buffer un-flushed — the barge-in trigger's guard; idle pipe = False (never cut on nothing).

### 2-byte alignment
- [NUANCE] an odd-length synth chunk carries its trailing byte into the next chunk; every `write_audio` payload is even-length (s16le breaks on misaligned writes).
- [EDGE] a final dangling odd byte at stream end is padded with one zero byte (half a sample of silence, inaudible).

### barge-in / close / never-throw
- [CAPABILITY] `cut()` drops buffered text, queued sentences, and the in-flight synth immediately; sets speaking False.
- [NUANCE] `cut()` cancels the worker task and awaits it (gather return_exceptions) — no orphaned synth after a barge-in.
- [CAPABILITY] `aclose()` (meeting end) flushes the tail, waits out the queue, leaves speaking False.
- [FAILURE] a synth OR channel fault mid-sentence is swallowed into honest no-audio for that sentence (recorded on `last_error`) — the turn/engine never crashes (Law 2).
- [NUANCE] cancellation is NEVER swallowed (only real faults) — meeting-end teardown stays prompt.
- [FAILURE] `flush()` absorbs the worker's terminal state incl. a cut cancelling it mid-drain, without re-raising to the caller.
- [EDGE] concurrent `say` + `flush` + `cut` interleavings never lose the idle `set_speaking(False)` (no awaits between the idle check and worker exit → no lost wake-ups).

### wiring
- [WIRING] `build_speak_sink(synthesize, channel, flush_after_s)` returns a SpeakPipe that satisfies both `engine.SpeakFn` (async-callable) and `engine.SpeakSink` (async `say`).
- [WIRING] `real_speak_sink(meeting_id)` lazily imports `transport.tts.CartesiaTTS` + `libs.http.call_external` + `output_media.channel_for` — the production wiring; the synth round-trips ride the single call_external seam.
- [NUANCE] `AudioChunkLike.pcm` is a read-only property so the frozen `transport.media.AudioChunk` conforms structurally (Protocol match).
- [NUANCE] `SpeakPipe._TERMINATORS` here is `.!?` (no `;…`) while session_host's `_TERMINATORS` is `.!?;…` — the two sentence splitters differ; verify each path's chunking matches its channel.

---

## in_meeting/session_host.py — the WARM permanent Claude session (in-sandbox)

Product: ONE persistent `ClaudeSDKClient` per meeting, started at provision, so each wake is
~1-3s (not a ~11-13s cold spawn). Streams spoken prose sentence-by-sentence to the room live,
runs the opener safety net, writes atomic turn results, heartbeats liveness.

### warm turn + streaming (`_run_turn`)
- [CAPABILITY] a wake is `client.query(prompt)` on the already-warm session; the prime + MCP tool are loaded ONCE, not per turn.
- [CAPABILITY] spoken prose streams to the room sentence-by-sentence as generated (first audio at first clause, not the whole answer) via `_deliver_say`.
- [CAPABILITY] captures tool names, cost_usd, turns, ttft, deliver_at, and the agent's own `to_meeting` intents into the turn record.
- [NUANCE] `deliver_at` = elapsed to the FIRST thing the room hears (first streamed sentence OR first to_meeting call) — the real perceived latency, before wrap-up/result-write/poll.
- [NUANCE] `ttft` = query → first text delta (pure model TTFT), the floor of deliver_at.
- [NUANCE] the model's reasoning (thinking) is NOT streamed as text_delta and is never spoken (display omitted + receive loop only flushes text_delta).
- [EDGE] `_sentence_end`: closes on `.!?;…` followed by whitespace, NOT preceded by a digit (`3.14` stays whole), NOT the tail of an abbreviation (`e.g.`/`Dr.`), NOT mid-token (`core.py`/`main()`).
- [EDGE] the final partial sentence (no trailing whitespace) is force-flushed at stream end so the answer's last words are never dropped.
- [NUANCE] when the model is about to call a tool, the pre-tool prose is force-flushed (`_flush_ready(final=True)`) so a natural opener ("let me check…") is heard NOW, not after the tool finishes.
- [NUANCE] sentences are AWAITED in order — the room hears them strictly in order even though `_relay_post` runs off-loop via `to_thread`.

### opener safety net (`_opener_watchdog`)
- [CAPABILITY] emits ONE canned "On it — give me a moment." only when the model has committed to multi-step work (first real tool) AND stayed silent `_OPENER_AFTER_TOOL_S` (2.0s) past it.
- [CAPABILITY] a no-tool backstop fires the opener after `_OPENER_HARD_FLOOR_S` (15.0s) of total silence (rare pure-reasoning heavy answer).
- [NUANCE] the opener NEVER fires during the judge-then-answer/decline phase — a direct answer or a silent cross-talk decline emits no tool before its text, so the tool-gate never triggers on them.
- [NUANCE] the model's OWN opener always wins (no double-speak): the watchdog stands down the instant `spoke`/`ttft`/`say_buf`/`opener_fired` is set; the check-then-set is atomic (no await between test and set).
- [NUANCE] `to_meeting` calls do NOT count as "committed to work" — only non-to_meeting tools arm the opener (delivery ≠ work).
- [FAILURE] a spurious "On it…" must NEVER be spoken on a turn that then chooses SILENCE (cross-talk) — the historical bug; the tool-gate is the fix.
- [EDGE] `PROXY_OPENER_BUDGET_S` / `PROXY_OPENER_HARD_FLOOR_S` override; unparsable keeps defaults.
- [NUANCE] the opener text is generic (never situation-specific) — Law 4, no code maps ask→words.

### turn record / result write
- [CAPABILITY] `_write_result` writes `<id>.json` atomically (temp + rename) so the driver's poll never reads a half-file.
- [CAPABILITY] `_parse_intents` mirrors `workroom._parse_intents` byte-for-byte so warm + cold produce identical `sent` (skips relay-error/malformed lines).
- [NUANCE] `_reset_intents` truncates the intents file before each turn so a wake's `sent` is EXACTLY that turn's calls.
- [FAILURE] a per-turn exception returns `{"error": ...}` after force-flushing already-composed prose — never a crash; the driver degrades honestly.
- [EDGE] a salvage path: an abnormally-terminated turn (no result event but prose streamed) still returns the last assistant text.
- [EDGE] `turns>0 and no result and no intents` ⇒ `error="turn did not complete"` (honest incompletion signal).
- [FAILURE] `_deliver_say` on a relay fault records a `relay_error` line and does NOT crash the turn; falls back to appending to the intents file.

### serve loop + heartbeat + startup
- [CAPABILITY] `_serve` tails `PROXY_WAKE_IN`, consumes only new lines (append-only), serves each wake, writes the result, re-polls every `_POLL_S` (0.1s).
- [FAILURE] a malformed/missing-key wake line is SKIPPED (the driver's poll times out → restart path) — never a crash.
- [CAPABILITY] `_heartbeat` rewrites `_host.ready` every `_HEARTBEAT_S` (2.0s) CONCURRENTLY with serving — a long working turn keeps the mtime advancing so it's not mistaken for dead.
- [NUANCE] if the process is SIGKILLed/OOM'd the heartbeat ticks stop → the file goes stale → the driver detects dead in seconds (vs 15min of dead air).
- [FAILURE] a fatal startup fault writes a `_host.err` breadcrumb and re-raises (never silent) → the driver's poll times out → cold/restart path.
- [WIRING] `main()` opens ONE `ClaudeSDKClient` with `permission_mode=bypassPermissions`, `setting_sources=["project"]` (loads CLAUDE.md once), `thinking=adaptive`, fixed `effort`, `include_partial_messages=True`, `max_turns` (40 default).
- [NUANCE] TOOL-WORKSHOP: `tools`/`allowed_tools`/`disallowed_tools` MUST be UNSET so the agent keeps its full native toolset (setting `[]` disables all; a list shrinks it).
- [NUANCE] SECRETS/isolation: the ONLY credential in the sandbox is `CLAUDE_CODE_OAUTH_TOKEN`; no push/send creds — the Law-3 boundary by construction.
- [NUANCE] `EFFORT` is validated against `_EFFORT_ALLOWED` (falls back to "high" on garbage) and FIXED for the meeting so CLI flags are byte-identical every turn (cache stays warm).
- [NUANCE] `MODEL` default is Sonnet, not Haiku — Haiku over-explores and exhausts the budget WITHOUT delivering (a fast model that says nothing is useless); overridable via `PROXY_WORKROOM_MODEL`.
- [WIRING] the `meeting` MCP server is configured `alwaysLoad: True` so `to_meeting` skips tool-search deferral — never behind a ToolSearch round-trip on the critical delivery path.
- [WIRING] Context7 MCP loads ONLY when `CONTEXT7_API_KEY` is set (egress-gated) — absent ⇒ nothing added; standing it up under denied egress would fake a capability (Law 2).
- [EDGE] `PROXY_USAGE` print of cache_read/cache_write proves the resident-prime cache is engaging turn-over-turn (cache_read growing = caching works).

---

## in_meeting/sandbox_meeting_mcp.py — the in-sandbox `to_meeting` MCP server

Product: the standalone stdio MCP server INSIDE the sandbox exposing the ONE `to_meeting` tool.
Records intents to a file (proof) or POSTs to the host relay (live). Dependency-light, imports
nothing from the workspace.

- [CAPABILITY] exposes exactly one tool `to_meeting(content, medium="chat", to="")`.
- [CAPABILITY] proof mode (no `PROXY_MEETING_RELAY`): appends one JSON line `{ts, content, medium, to}` to `PROXY_MEETING_OUT` and returns `"delivered via <medium>"`.
- [CAPABILITY] live mode (`PROXY_MEETING_RELAY` set): POSTs `{ts, content, medium, to}` to the host, returns the host's response body.
- [NUANCE] the tool's docstring says SPEAK by writing (not this tool) — this tool is ONLY for chat/dm/screen/offer/mute; matches the prime + workroom prompt.
- [NUANCE] default medium here is `"chat"` (vs `"say"` on the host connection) — because speaking is done by writing, not by calling the tool; verify the default doesn't cause a mis-route.
- [FAILURE] a relay POST exception is caught: records a `relay_error` line locally AND returns `"send via <medium> failed: <exc>"` — never crashes the agent's turn.
- [NUANCE] SECRETS: the relay bearer `PROXY_MEETING_TOKEN` is sent as `Authorization: Bearer` only when set; the token is never logged.
- [EDGE] medium is stripped + lowercased; empty ⇒ "chat".
- [NUANCE] `urlopen` timeout is 15s — a slow/hung host relay can't block the tool indefinitely.
- [WIRING] the recorded JSONL is the exact shape `session_host._parse_intents` / `workroom._parse_intents` read — a schema drift silently drops the agent's deliveries.
- [NUANCE] a live relay POST returning an empty body still returns `"delivered via <medium>"` (never a blank tool result).

---

## in_meeting/workroom.py — the per-meeting E2B sandbox driver

Product: provisions + seeds the sandbox (repo, CLAUDE.md w/ resident map, MEETING_NOTES,
MCP wiring), starts the warm host, drives every wake via file round-trips with restart-and-retry
and dead-host detection, feeds the transcript, tears down. THE most integration-dense file.

### provisioning + seeding (`provision_workroom`)
- [CAPABILITY] provisions an E2B sandbox (pre-baked template when available, else base + setup at warm time).
- [CAPABILITY] `resume_id` reconnects a paused warm snapshot (~1s) via `Sandbox.connect(id)`; a resume fault degrades to a fresh cold provision — never a crash/stuck meeting.
- [CAPABILITY] setup shallow-clones the repo, pip-installs pinned `mcp` + `claude-agent-sdk`, ensures native `claude` present.
- [NUANCE] the two heavyweight installs (pip + npm) are SERIALIZED — concurrent installs OOM the ~478MB base sandbox and fail the WHOLE provision (no meeting at all).
- [NUANCE] `&&` chaining fails setup on either install error — no silent half-provision.
- [EDGE] `sha=None` ⇒ shallow `--depth 1` clone; a pinned `sha` ⇒ full clone + `git checkout` (with `|| true` so a bad sha doesn't crash provision).
- [CAPABILITY] seeds CLAUDE.md = `compose_resident_prime(prime, map_text)` (lean prime + resident understanding block); writes REPO_MAP.md fallback + empty MEETING_NOTES.md.
- [EDGE] `compose_resident_prime` with empty/whitespace map_text appends nothing (clean degrade — prime stays exactly as-is).
- [CAPABILITY] writes `sandbox_meeting_mcp.py`, `.mcp.json` (with `alwaysLoad: True`), `session_host.py` from packaged source, starts the warm host, waits for readiness at provision (`_WARM_PROVISION_WAIT_S`, 90s) BEFORE meeting goes live.
- [NUANCE] the ONLY injected credential is `CLAUDE_CODE_OAUTH_TOKEN`; egress stays default-deny — the Law-3 gate by construction.
- [WIRING] every E2B round-trip rides `call` (the `call_external` seam) — no raw E2B client outside the seam.
- [FAILURE] a warm-host launch fault leaves `warm=False`; the first wake self-heals (restart-and-retry) — provision still returns.
- [EDGE] `sandbox_class` injectable so tests pass a fake sandbox; production resolves `e2b_sandbox_class()` lazily (avoids shadowing stdlib `http`).
- [NUANCE] tenant isolation: the sandbox is per-meeting; repo + transcript + scratch never shared across meetings.
- [NUANCE] `.mcp.json` names the server `meeting` — must match `mcp__meeting__to_meeting` in the prime/prompt or the tool is unreachable.

### run_ask (the wake driver)
- [CAPABILITY] `run_ask(ask, recent)` builds the wake prompt, runs the warm turn, and returns a `WorkroomResult` — ONE delivery path (warm session).
- [CAPABILITY] on a warm miss (never started / crashed / timed out) it restarts the host ONCE and retries; still missing ⇒ honest `WorkroomResult.error` ("workroom session unavailable").
- [FAILURE] a genuine caller-drain `CancelledError` (`cancelling()>0`, meeting end) is RE-RAISED so shutdown stays prompt (Law 3).
- [FAILURE] a spurious transport `CancelledError` (`cancelling()==0`, E2B HTTP/2 reset under load) is ABSORBED into `error="workroom transport interrupted this turn"` — never crashes the meeting loop (WS6 regression: 2 cancels / 26 wakes).
- [NUANCE] `run_ask` NEVER raises — every fault is an honest `WorkroomResult.error`.
- [WIRING] `WorkroomResult` fields (text, tools, turns, cost_usd, error, deliver_at, ttft, sent) are the exact contract `meeting_session._handle` consumes to decide relay vs replay vs degrade.

### warm round-trip (`_run_ask_warm`) + dead-host detection
- [CAPABILITY] confirms host ready, appends the wake `{id, prompt}` to WAKE_IN via `printf` + `shlex.quote` (exactly one valid JSON line, no partial/concatenated line), polls `WAKE_OUT/<id>.json`.
- [NUANCE] the wake JSON is safely quoted so embedded quotes in the prompt can't corrupt the appended line the host reads.
- [CAPABILITY] dead-host watch: reads the heartbeat breadcrumb; a value frozen for the whole `_DEAD_HOST_TIMEOUT_S` (20s) ⇒ host dead/hung ⇒ abort the wait fast (restart-and-retry), NOT the 900s ceiling.
- [NUANCE] a long WORKING turn keeps the heartbeat moving, so it's never cut short — only a truly frozen host aborts.
- [CAPABILITY] adaptive poll backoff: 0.25s < 5s elapsed, 0.5s < 30s, 1.0s beyond — cuts long-task read count ~8x to avoid E2B files-API contention.
- [FAILURE] a corrupt result JSON returns None ⇒ restart-and-retry (never present garbage to the room).
- [FAILURE] a WAKE_IN write fault returns None ⇒ restart-and-retry.
- [FAILURE] a spurious transport cancel on the RESULT read is "not written yet" (keep polling) — abandoning it would orphan a wake that succeeded server-side (room hears nothing on a success).
- [EDGE] the full `ASK_TIMEOUT_S` (900s) applies only while the host is LIVING (heartbeat advancing).
- [NUANCE] an honest per-turn host error is surfaced as a real result (no retry) — that IS the answer, the session degrades.

### readiness + restart (`_await_host_ready` / `_read_heartbeat` / `_restart_session_host`)
- [CAPABILITY] `_await_host_ready` polls the readiness breadcrumb (bounded), latches `_host_ready` so only the FIRST wake pays the wait.
- [NUANCE] a restart clears `_host_ready` so the fresh host's readiness is re-checked.
- [CAPABILITY] `_restart_session_host` pkills the lingering host (`|| true` so no-match is fine), relaunches, awaits readiness; returns True iff the fresh host opened.
- [FAILURE] `_await_host_ready`/`_read_heartbeat` never raise; a spurious transport cancel is "not ready/no beat this poll"; a genuine cancel is re-raised.
- [EDGE] `_WARM_READY_TIMEOUT_S` (30s) is only paid until the host is seen ready once; env-overridable for a slow bake.
- [FAILURE] a restart with a launch fault returns False ⇒ honest degrade (no whole second cold engine to maintain).

### feed_transcript / pause / teardown
- [CAPABILITY] `feed_transcript(md)` rewrites MEETING_NOTES.md so a woken turn reads the up-to-date room.
- [FAILURE] a transcript-sync fault is logged and swallowed (never crashes the meeting) — the meeting continues.
- [CAPABILITY] `pause()` pauses the sandbox for a ~1s warm resume; returns the paused id or None (no clean pause) — best-effort, never raises.
- [CAPABILITY] `teardown()` kills the sandbox; a kill fault is logged, never raised.
- [WIRING] `_wake_envs` supplies `CLAUDE_CODE_OAUTH_TOKEN`, `PROXY_MEETING_RELAY`, `PROXY_MEETING_TOKEN`, `PROXY_MEETING_OUT`, and Context7 key only when present.
- [NUANCE] empty `relay_url` ⇒ intents recorded locally only (file mode) — the session replays them; honest degrade with no live relay.

### wake prompt (`_wake_prompt`)
- [NUANCE] the prompt is built once so warm + restart-retry hand Claude the SAME instructions (one behavior contract).
- [CAPABILITY] the recent transcript is INLINED so a wake needs no MEETING_NOTES.md read just to judge/answer (a turn saved every wake).
- [NUANCE] JUDGE-IF-ADDRESSED: instructs to STAY COMPLETELY SILENT (write NO words, don't call the tool, don't explore) on incidental "proxy"/cross-talk — a spoken "not addressed" is itself an unwanted interruption.
- [NUANCE] DELIVER-IN-ONE-TURN: the real result/artifact must be delivered THIS turn; never "I'll bring it back" then stop with nothing.
- [NUANCE] NO-OVERSTATE: never tell the room "I already did this"/"previous turn"/"as I showed earlier" — internal steps are not earlier exchanges (Law 2).
- [NUANCE] the ONE exception: a genuine blocker/ambiguity ⇒ ask ONE crisp question and stop; the reply brings Proxy back to continue (couples with meeting_session's pending-question latch).
- [NUANCE] world-touching ⇒ produce the real artifact + `medium='offer'` (offer IS the delivery; no push creds by design).

---

## transport/recall.py — the Recall.ai carrier (TransportProvider)

Product: bot join + per-speaker audio + Output Media + chat + roster across Meet/Zoom/Teams
behind one API. Every round-trip through `call_external`; no raw client held here.

### join body + join
- [CAPABILITY] `join(link)` POSTs `/bot` with the create-bot body and returns the launched bot id.
- [FAILURE] a `/bot` response with no `id` raises "no bot launched" — never a shared/placeholder bot id (would collide across meetings — P0 isolation).
- [CAPABILITY] `_join_body` carries `meeting_url`, `bot_name="Proxy"`, and (when `webhook_url` set) `recording_config.transcript.provider.assembly_ai_v3_streaming` + `realtime_endpoints` subscribed to `transcript.data`/`transcript.partial_data`/`participant_events.chat_message`.
- [NUANCE] `bot_name="Proxy"` so Recall labels Proxy's own transcribed speech "Proxy" — the self-wake guard (`PROXY_SPEAKER=="Proxy"`) then filters it (coupling with meeting_session).
- [NUANCE] AssemblyAI is BYOK (key in Recall dashboard) — the provider object rides EMPTY; no credential ever enters the body (secrets law).
- [EDGE] with no `webhook_url` the body carries NO `recording_config` (a transport with no receiver can't consume transcripts — and never ships an empty-string URL Recall rejects).
- [CAPABILITY] `output_media.camera = {kind:"webpage", config:{url}}` appears only when `output_media_url` configured.
- [NUANCE] status events (`connected`/`dropped`/`rejoined`) are NOT subscribable per-bot — they arrive only via the dashboard account webhook (verify the config doesn't try to subscribe them here).

### chat / dm / mute / leave
- [CAPABILITY] `post_chat` POSTs `/bot/{id}/send_chat_message/` (trailing slash) with `{message, pin}` — NOT `/chat`/`pinned`.
- [CAPABILITY] `send_dm` POSTs the same endpoint with `{message, to: participant_id}`.
- [NUANCE] per-participant DM is Zoom-only; on other platforms Recall degrades to "everyone" — honest platform degrade, not a fabricated private send.
- [CAPABILITY] `mute(bot_id)` sets a sink-side suppression flag (NO wire call — Recall has no bot-mute endpoint); `unmute` clears it.
- [NUANCE] the mute flag lives on the transport so a sink created before `mute()` still honors it (observed live via `is_muted`) — human mute wins regardless of the wire (Law 3).
- [CAPABILITY] `leave(bot_id)` POSTs `/bot/{id}/leave`.

### output media sink (`_RecallOutputMedia`)
- [CAPABILITY] `write_audio` POSTs `/bot/{id}/output_audio/` with `{kind:"mp3", b64_data}` (mp3 is the ONLY allowed kind; the clip path, rate-limited 300/min).
- [NUANCE] while muted, `write_audio` is suppressed sink-side — ZERO wire calls (C5).
- [CAPABILITY] `write_frame` POSTs `/bot/{id}/output_video/` with `{kind:"jpeg", b64_data}`.
- [CAPABILITY] `flush` is a NO-OP (barge-in/mute silence at the webpage channel, not this clip sink).
- [FAILURE] an unbound sink (`_api is None`) raises INSIDE the op — surfaced through the seam, absorbed by the delivery never-throw boundary, never a silent fake success (Law 2).

### the raw HTTP round-trip (`_api`) + region
- [NUANCE] the raw client is constructed ONLY inside `libs.http.http_client`, imported lazily — transport holds no client/SDK at import time (static-scan-provable hard rule).
- [NUANCE] auth is `Authorization: Token <key>`; the key is never logged, never in a body (secrets law).
- [FAILURE] a non-2xx `raise_for_status()` (honest degrade) — retried/absorbed by the seam + never-throw boundary.
- [EDGE] a 204 (Recall DELETE endpoints) returns `{}` (nothing to parse) — never a JSON-decode crash.
- [CAPABILITY] `_recall_base()` resolves `RECALL_REGION` at construction (never import time) — a key minted in one region is 401'd on other hosts (proven live); unset ⇒ us-east-1 default.
- [WIRING] `roster_events`/`chat_events` yield from in-process queues fed by `_ingest_roster`/`_ingest_chat` (the harness webhook drain) — the carrier-to-loop path stays in-process asyncio.
- [EDGE] `output_media(bot_id)` returns a sink bound to the current mute-state closure — per-bot isolation.

---

## transport/tts.py — Cartesia Sonic-3 (TTSProvider)

- [CAPABILITY] `synthesize(text)` streams the exact verbatim text (no headline extraction/substitution) to `/tts/bytes` and yields `AudioChunk`s (s16le/16kHz/mono).
- [CAPABILITY] a single configured `(voice_id, register)` is used for ALL synthesis — one voice identity, never varied line-to-line.
- [NUANCE] `voice` rides as `{mode:"id", id}`; `register` is an internal tag that NEVER rides the wire (Cartesia has no register field).
- [NUANCE] auth is `Authorization: Bearer <key>` + pinned `Cartesia-Version` header; the key never enters the body, never logged (secrets law).
- [CAPABILITY] the streamed bytes are re-framed into ≤ `tts_chunk_ms` chunks (16kHz s16le ⇒ 32 bytes/ms) so a surviving in-flight chunk can't defeat barge-in.
- [EDGE] `is_final` is set on the last chunk; a single-chunk stream marks it final.
- [EDGE] with `call_external=None` the stream yields nothing (a clean empty degrade, not a crash).
- [FAILURE] a non-2xx `raise_for_status()` — honest degrade, absorbed by the seam + SpeakPipe never-throw.
- [NUANCE] the raw client is only in `libs.http.http_client` (lazy import) — no raw Cartesia client/SDK in the package.
- [EDGE] `chunk_ms`/`voice_id` default to settings surfaces (`CARTESIA_VOICE_ID`, config `tts_chunk_ms`) when not injected; the fallback voice id is a real resolvable id.
- [NUANCE] the output_format (`raw`/`pcm_s16le`/16000) must match Recall's + the orb page's audio convention exactly.
- [EDGE] the re-framing `step` is floored at 1 sample (2 bytes) so a tiny `chunk_ms` can't produce zero-length steps.

## transport/consent.py — the consent notice

- [CAPABILITY] `consent_notice()` returns the one-line notice: AI participant · observes/records · anyone can address it.
- [NUANCE] the notice carries NO internal component name (naming law) — enforced by `notice_is_valid`'s `_FORBIDDEN_INTERNAL_NAMES` check (names built via concatenation so they don't appear as literals).
- [CAPABILITY] `notice_is_valid` structurally verifies: single line, all three required elements present, no internal name — a guard against a broken edit.
- [EDGE] a multi-line or element-missing notice fails validation.

## transport/join.py — join + consent hard-gate FSM

- [CAPABILITY] `JoinSession.join(link)` joins link-only (no host install), posts the consent notice as the FIRST observable action, transitions PENDING→JOINING→IN_MEETING→LISTENING.
- [NUANCE] consent is a HARD GATE: `can_observe()` is True only once `notice_posted` AND state is LISTENING — nothing observed/recorded before the notice.
- [FAILURE] a join failure returns `JoinResult(joined=False, failed=True, reason=...)` — never a false "joined" (Law 2, AC-JOIN-16).
- [FAILURE] a consent-notice post failure halts (state FAILED, `notice_posted=False`) and reports honestly — never a false "posted".
- [NUANCE] the bot belongs to the ROOM, not the inviter — no inviter-identity gate anywhere.
- [CAPABILITY] `on_participant_join` re-posts the notice to a late joiner (unpinned) — they never miss it (AC-JOIN-07).
- [EDGE] `on_participant_join` is a no-op if the notice hasn't posted / no bot / meeting ENDED.
- [CAPABILITY] `on_objection` defers to the organizer (never a unilateral continue); `on_hard_removal` LEAVES (terminal, not mute/pause).
- [NUANCE] `ConsentGate` starts CLOSED (fail-closed): a runtime whose gate is never granted drops every record rather than defaulting to always-allow.
- [CAPABILITY] `JoinSource.LINK` and `.CALENDAR` reach the identical gated in-meeting state (no per-source branch).
- [NUANCE] the Recall-backed path serves Meet/Zoom/Teams with no per-platform branch.
- [CAPABILITY] `join_to_listening_s` measures invite→listening (SLO ≤10s).
- [EDGE] `on_bot_launched` callback (bot_id write-back to the meetings row) fires exactly once on a successful join, before the consent post.

## transport/media.py + signals.py — media units + the emitted signal surface

- [CAPABILITY] `AudioChunk`/`AudioFrame`/`CanvasFrame` are frozen dataclasses; `CanvasFrame.payload` aliases `data` (backward compat).
- [NUANCE] `RosterEvent.__post_init__` raises on an out-of-surface `kind` (not present/join/leave) — a bad kind never enters the stream silently.
- [NUANCE] `BotStatus.__post_init__` raises on a status not in {connected,dropped,rejoined}.
- [NUANCE] `EMITTED_SIGNAL_NAMES` = the sealed `SIGNAL_SURFACE_EVENTS` + `chat`; must stay DISJOINT from the client `ProxyMessage` registry (contracts-closed hard rule) — a static oracle should prove no extra (e.g. screen-ingestion) signal crept in.
- [CAPABILITY] `signal_name(sig)` maps each signal dataclass to its wire name.
- [NUANCE] `channel-report` remains a sealed wire NAME but carries no dataclass value — DM availability is the agent's `to_meeting` judgment, not a transport signal.

## transport/config.py + external.py + seams.py

- [CAPABILITY] `config.get_int`/`get_float` read tunables from `config/defaults.toml` (env never overrides these — Law 4, config owns floors); fall back to `_DEFAULTS` on a missing/corrupt file.
- [NUANCE] `tts_chunk_ms`/`max_buffered_audio_ms` stay BELOW `barge_in_budget_ms` (200) so a surviving in-flight chunk can't exceed the stop budget.
- [NUANCE] `barge_in_onset_min_ms` (200) — a blip below this must never cut Proxy (couples with meeting_session `_BARGE_MIN_TOKENS`).
- [NUANCE] `rejoin_reset_after_connected_s` (900) is PER-EPISODE; `rejoin_cap_per_meeting` (3) bounds flapping to an honest terminal stop.
- [EDGE] `_table` is lru_cached — a config change requires a fresh process (verify the cache doesn't serve stale in a long-lived host).
- [WIRING] `CallExternal` Protocol is the structural type of `libs.http.call_external` (op, service, unit_cost_usd) — the sole external-call seam; transport holds no raw client, imports no provider SDK (static-scan-provable).
- [WIRING] `TransportProvider`/`TTSProvider`/`OutputMediaSink` Protocols are `runtime_checkable` — callers depend ONLY on the Protocol, never a concrete client type (provider swap = migration, not redesign).

---

## Cross-subsystem integration points

Every dependency in/out of services/in-meeting.

### Inbound (who calls in-meeting)
- [WIRING] `control_plane.meeting_session.MeetingSession` is THE driver: calls `workroom.feed_transcript` per line, `workroom.run_ask(ask, recent=...)` on a wake, `connection.to_meeting(...)` to replay file-mode intents, `connection.barge_in()`/`begin_turn()`, and reads `connection.spoken`/`connection.speak.speaking`.
- [WIRING] `MeetingSession` wake gate `is_addressed` — voice `\bproxy\b` / chat `@proxy\b`, filters `speaker==PROXY_SPEAKER` (Proxy's own lines) — must match Recall's `bot_name="Proxy"` label.
- [WIRING] `MeetingSession` self-echo (`_is_self_echo`) reads `connection.spoken` (the log `MeetingConnection._record_spoken` writes) with a 45s window, 4-token min, 0.7 containment — relabels an echo to Proxy so it never re-wakes.
- [WIRING] `MeetingSession` barge-in reads `connection.speak.speaking` (the SpeakPipe property) + `_is_barge_in` (≥2 tokens) → `connection.barge_in()` — the full barge-in reflex spans session + connection + speak pipe + output_media mute/cut.
- [WIRING] `MeetingSession` ASK→ANSWER→CONTINUE latch keys off the agent's delivered content ending in "?" — couples with the wake prompt's "ask ONE crisp question and stop".
- [WIRING] `control_plane.relay` (`POST /meetings/{id}/relay`) authenticates the per-meeting bearer against `workroom.relay_token`, resolves `runtime.connection`, lands `connection.to_meeting(content, medium, to)` — the LIVE path from the in-sandbox MCP server.
- [WIRING] `control_plane.meeting_runtime.end_meeting` teardown calls `session.drain()`, `speak_pipe.aclose()`, `workroom.teardown()`, `output_media.close_channel(meeting_id)` — bounded by TEARDOWN_TIMEOUT_S each.
- [WIRING] `control_plane` mounts `in_meeting.output_media.router` (the orb page + WS feed) on the FastAPI app.
- [WIRING] the control-plane provisioner calls `in_meeting.workroom.provision_workroom(...)` with the repo url, subscription token, map_text, relay_url/token, resume_id; builds `MeetingConnection` with the Recall/Cartesia sinks; builds `real_speak_sink(meeting_id)`.
- [WIRING] the webhook drain feeds `RecallTransport._ingest_roster`/`_ingest_chat` and calls `MeetingRuntime.ingest_line(speaker, text, is_chat)` — the transcript→wake seam.

### Outbound (what in-meeting calls)
- [WIRING] → `libs.http.call_external` / `http_client` / `e2b_sandbox_class` — the SOLE external-call + raw-client seam (Recall, Cartesia, E2B); retry + cost telemetry.
- [WIRING] → E2B AsyncSandbox: `commands.run` (fg + background), `files.write`/`files.read`, `create`/`connect`/`kill`/`pause` — all through `call`.
- [WIRING] → Recall API (`api.recall.ai`/region host): `/bot`, `/bot/{id}/leave`, `/send_chat_message/`, `/output_audio/`, `/output_video/`.
- [WIRING] → Cartesia API: `/tts/bytes` streaming synth.
- [WIRING] → the host relay (from inside the sandbox): `sandbox_meeting_mcp._relay` and `session_host._relay_post` POST to `PROXY_MEETING_RELAY` (= `relay_url_for(meeting_id)`).
- [WIRING] → Anthropic via `claude-agent-sdk.ClaudeSDKClient` (in-sandbox warm host) + native `claude` CLI; auth = `CLAUDE_CODE_OAUTH_TOKEN` (subscription).
- [WIRING] → `mcp` SDK FastMCP (in-sandbox stdio server) + optional Context7 MCP (npx, egress-gated).
- [WIRING] → `contracts.registry.SIGNAL_SURFACE_EVENTS` (signals.py) — the sealed wire-name surface.
- [WIRING] → `config/defaults.toml` `[transport]` block (config.py tunables).

### End-to-end invariants that span the whole subsystem (must be tested live)
- [NUANCE] TWO sentence splitters exist (`speak._TERMINATORS`=`.!?` vs `session_host._TERMINATORS`=`.!?;…`) — verify the LIVE relay path (session_host streams sentences → relay → connection.say) and the file/replay path both produce natural, in-order audio.
- [NUANCE] delivery de-dup: relay mode (in-sandbox POSTs live, `result.sent` empty) must NOT double-send; file mode (`result.sent` non-empty) replays; keyed off `result.sent` not the shared `connection.sent` so overlapping concurrent wakes never drop each other.
- [NUANCE] Law-3 human control end-to-end: mute silences the Output-Media webpage channel (where PCM rides) immediately + sets the Recall flag; barge-in cuts mid-word within `barge_in_budget_ms` (200ms) across session→connection→speak→channel.
- [NUANCE] Law-1 grounded-or-silent + Law-2 never-overstate are enforced only by the prime + wake prompt (dynamic, not code) — must be verified on real transcripts, not asserted in a unit test.
- [NUANCE] tenant isolation P0: per-meeting sandbox, per-meeting Output-Media channel, per-meeting relay token, per-bot Recall queues — a cross-meeting read is a breach; verify no shared placeholder bot id, no channel leak, no token reuse.
- [NUANCE] a wake spawned as a background task means the room keeps flowing while Proxy works (monitor-while-working) — verify concurrent wakes, mid-task follow-ups, and meeting-end drain don't corrupt each other or hang teardown.
- [FAILURE] every dependency fault (Recall/Cartesia/E2B/Anthropic/relay error, timeout, cancel, malformed webhook line, OOM at provision, dead host, corrupt result) must degrade honestly — never a fake success, never a crash of the meeting loop, and a needed response is met with either the answer or ONE honest degrade line, never total silence.
