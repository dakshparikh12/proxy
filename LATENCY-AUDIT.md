# LATENCY-AUDIT.md

## STEP -1 — Force-sync to remote: COMPLETE

| Check | Result |
|---|---|
| Git repo present | yes — no clone needed |
| `git fetch --all --prune --tags` | OK. `origin/main` `c5bd306 → 1a7e548`; `origin/proxy-build` `c5bd306 → 868e97b` |
| Snapshot ref | `backup/pre-audit` @ `702c0a6` (commit "pre-audit local snapshot") |
| Deployed branch | **`origin/main`** — by the stated default, see below |
| `git checkout main` | OK (was on `cost-audit`) |
| `git reset --hard origin/main` | `HEAD is now at 1a7e548` |
| `git clean -fd` | removed `services/harness/`, `tests/eval/` |
| `git status` clean | **yes** — empty porcelain |
| `HEAD == origin/main` | **yes** — both `1a7e5488268125cb8118b8d66a3aa1f8546b016c` |
| Gitignored files survived | **yes** — `.env` (6974 bytes) and `.venv/` intact (`-fd`, not `-fdx`) |

**Deployed branch determination — undeterminable from config, defaulted to `origin/main`.** There are four workflows (`.github/workflows/{contracts-check,e2e-nightly,guards,verify}.yml`) and **none of them deploys**. `contracts-check.yml:8`, `guards.yml:2`, and `verify.yml:2` are all `on: [push, pull_request]` with **no `branches:` filter**, so they run on every branch and pin nothing. `e2e-nightly.yml:5-8` is `schedule` + `workflow_dispatch` only. Grepping all four for `deploy|gcloud run|cloud run|artifact registry` yields a single hit — a comment at `guards.yml:53` about a Terraform secret-map drift gate. There is no `cloudbuild.yaml`, no `vercel.json`, no `Procfile`, no `netlify.toml`. Deploy is manual: `deploy/README.md:60-66` instructs `PROJECT=… REGION=… TAG=v0 bash deploy/build-and-push.sh`, and `deploy/build-and-push.sh` contains no branch reference — it builds **whatever is checked out**. So nothing in the repo asserts which branch is deployed; `origin/main` is the stated fallback, not evidence.

**Recovering the pre-sync state:** `git checkout backup/pre-audit`. That ref holds the prior `cost-audit` work plus three untracked files the clean would otherwise have destroyed (`AUDIT-REPORT.md`, `FRONTEND-AUDIT.md`, `pr.md`). Delete with `git branch -D backup/pre-audit` when you no longer want it.

---

## BRANCH DIVERGENCE

### Which branches are unmerged

| origin branch | ahead of main | behind main |
|---|---|---|
| **`origin/proxy-build`** | **20** | **0** |
| `origin/feat/doc04-112-workroom-dispatch` | 53 | 47 |
| `origin/spec/doc07-amendments` | 39 | 126 |
| `origin/verification-system` | 21 | 366 |

**`origin/proxy-build` is 20 ahead and 0 behind — a strict superset of the deployed branch.** It contains every commit on `main` plus twenty more. `git diff --stat origin/main..origin/proxy-build` = **268 files changed, 35,572 insertions, 2,451 deletions**.

**This is the headline finding for a latency audit: the latency work is split across two branches, and the larger half is not on the deployed one.** `main`'s own HEAD is a latency commit — `1a7e548 latency: inline recent transcript into the wake prompt — kill the per-wake MEETING_NOTES.md read`. But `proxy-build` carries the rest:

```
85bcae1 latency: stream the agent's reply to voice — one call, first audio at the first clause
1227d56 latency: stream the agent's spoken opener before it acts + TTFT telemetry
5b0f048 live round 3: full-band audio + audibility-gated barge-in + one-prosody batching
1f465ab delivery layer: the 5 live-meeting fixes (voice-clean speech, true silence, barge-in, pre-warm, queued_ms)
c249203 live round: audio drop-oldest -> real backpressure; follow-up window on the real signals
0eb5251 THE chop root cause: per-chunk phase-reset in the 48k fallback resampler — now stateful streaming
868e97b audio telemetry + worklet fallback + SYSTEM_DESIGN.md
```

**TTFT telemetry exists only on `proxy-build` (`1227d56`).** Any latency measurement taken against the synced deployed tree is therefore measuring a system with less instrumentation and an older speech path than the one under active development.

### Speech-path file divergence

| File | main vs proxy-build |
|---|---|
| `services/in-meeting/src/transport/tts.py` (TTS client) | 14 changed (+9 / −5) |
| `services/in-meeting/src/in_meeting/output_media.py` (Output Media page) | **653 changed (+601 / −52)** |
| `services/in-meeting/src/transport/recall.py` (bot creation) | 44 changed (+14 / −30) |
| `services/in-meeting/src/in_meeting/speak.py` (speak pipe) | 43 changed (+39 / −4) |
| `services/control-plane/src/control_plane/relay.py` | **identical** |

#### 1. TTS sample rate — 16 kHz → 44.1 kHz

**main (deployed)** — `services/in-meeting/src/transport/tts.py:32`:
```python
_SAMPLE_RATE_HZ = 16000
```
Consumed at `tts.py:37` (`"sample_rate": _SAMPLE_RATE_HZ` in the Cartesia request body) and `tts.py:146` (chunk-step arithmetic). Module docstring: *"raw `pcm_s16le` @ 16000 Hz mono"*, and `tts.py:146`'s comment describes *"16 kHz s16le mono ⇒ 32 bytes/ms"*.

**proxy-build** — `services/in-meeting/src/transport/tts.py:35-36`:
```python
SAMPLE_RATE_HZ = 44100
_SAMPLE_RATE_HZ = SAMPLE_RATE_HZ
```
with the rationale in the comment at `tts.py:33`: *"16 kHz was telephone-band and made Proxy audibly worse than every human in the room (live founder finding); 44.1 kHz is full-band, Cartesia-supported, and the output-media page resamples cleanly."* The byte-rate comment becomes *"44.1 kHz s16le mono ⇒ ~88 bytes/ms"*.

**Latency consequence:** ~2.75× the audio bytes per millisecond of speech on the wire between Cartesia, the host, and the Output Media page. `_MODEL_ID = "sonic-3"` is unchanged on both (`tts.py:30`).

#### 2. Output Media page — rewritten around a rolling buffer

**main** — `output_media.py:65 MAX_BUFFERED_FRAMES: Final[int] = 256`, documented at `:62` as *"~100 ms of 16 kHz s16le per"* frame. The page holds no sample-rate constant of its own.

**proxy-build** — `output_media.py:87 MAX_BUFFERED_FRAMES: Final[int] = 256` (unchanged) **plus a new exported constant** at `:93`:
```python
SAMPLE_RATE_HZ: Final[int] = 44_100
```
with `:90` stating the page, the pipe and the TTS client *"are all 44.1 kHz (`transport.tts.SAMPLE_RATE_HZ`); this mirrors that so the rates can never drift apart."*

The substantive change is the client-side audio path, `output_media.py:547-549`:
> *"…choppy/gravelly voice). A rolling cursor can't fix a per-chunk resample seam. So we [use a single buffer] that every incoming chunk is APPENDED to — no per-chunk buffers, no per-chunk resample…"*

This is commit `0eb5251 THE chop root cause: per-chunk phase-reset in the 48k fallback resampler — now stateful streaming`. **The deployed branch still has the per-chunk resample seam.**

#### 3. Bot creation — configurable name, roster/chat streams removed

**main** — `recall.py` hardcodes the bot name in the create body:
```python
body: dict[str, Any] = {"meeting_url": meeting_link, "bot_name": "Proxy"}
```

**proxy-build** — same line becomes `"bot_name": self._bot_name`. Additionally `proxy-build` **removes** four streaming methods present on main: `roster_events`, `chat_events`, `_ingest_roster`, `_ingest_chat`, and the module-level `_drain` helper. Net −30/+14 lines.

#### 4. Speak pipe — one-prosody batching

**proxy-build** adds a batching change at the synth boundary (`speak.py`):
> *"Drain EVERYTHING queued as one synth unit: the first sentence rides alone (it queued first — fast start), and sentences that accumulated [are batched]"*

This is `5b0f048 live round 3: … one-prosody batching`. The deployed branch synthesizes without it. Latency-relevant in both directions: it preserves a fast first clause while reducing per-sentence synth round-trips for everything after.

---

## Steps 0–4 — NOT RUN

**The instruction ends with "Run Steps 0–4 against this synced tree," but Steps 0–4 were never provided.** This conversation contains STEP -1 only. I have not invented them, and I have not guessed at what a latency audit's Steps 0–4 would contain — that would produce a document that looks authoritative and measures the wrong things.

Send Steps 0–4 and I will run them against this tree, which is now verified clean at `origin/main` `1a7e548`.

Two things worth deciding before those steps run, because they change what the numbers mean:

1. **Which tree should be measured?** The deployed default (`origin/main`) lacks the TTFT telemetry, the 44.1 kHz full-band audio, the stateful resampler, and the one-prosody batching. Measuring it produces honest numbers for what is deployed and misleading numbers for what is being built. `origin/proxy-build` is a strict superset and would need no merge to measure.
2. **The environment has known blockers** recorded in the two prior audits — the app could not import FastAPI until I installed `annotated-doc`, `anyio`, `annotated_types` and repaired `asyncpg` in `.venv` (those installs persist and are unaffected by the `git clean`), and the Anthropic API key returns HTTP 400 `credit balance is too low`, so any step requiring real inference will not produce dollar or token figures.

---

# STEP M0 — MERGE: DONE, NOT PUSHED

`git merge --ff-only origin/proxy-build` **succeeded** — fast-forward, no conflicts. `HEAD = 868e97b`, 20 ahead of `origin/main`.

**The suite is not green, so per instruction I stopped and did not push.**

| run | result |
|---|---|
| bare env | **24 failed, 370 passed** |
| with `.env` loaded into the process | **11 failed, 383 passed** |

13 of the 24 were environment-only: `settings.py` runs its fail-fast gate at *import* time, so any test importing `control_plane.settings` without the seven required vars dies before its own body runs (`test_security_secrets_gate.py` x9, `test_map_seam_wiring.py` x2, and two others).

The remaining 11 are, as far as I could verify, **all Windows path-separator artifacts, not merge regressions**:

- `tests/premeeting/test_pm_symbol_map.py:121` — asserts `"src/click/core.py:"` is in a map that contains `src\click\core.py`
- `tests/premeeting/test_pm_pipeline.py:113` — asserts `"src/server.py:"` against output containing `src\\models.py`, `tests\\test_x.py`
- plus `test_pm_map_build`, `test_pm_understanding`, `test_verify_url_domain_prose` x3, `test_pm_repo_context` x2, `tests/e2e/test_one_operation_runs.py` (asserts `startswith("migrations/")` against `migrations\versions\...`)

The one I could not classify is `services/control-plane/tests/test_meeting_in_a_box.py::test_meeting_in_a_box_full_chain`.

**CI runs `ubuntu-latest` (`guards.yml`), where every one of these path assertions would pass.** So this is very likely green on CI and red only here. I did not push anyway: the instruction was conditional on green, and "probably green on a platform I cannot test from" is not green. Push is one command once you confirm on Linux.

**One environment change was required to get this far:** the merge introduced `networkx>=3.4` (declared, `services/premeeting/pyproject.toml:15`; locked in `uv.lock`). I ran `uv sync --all-packages`, which installed it plus `scipy`, `tree-sitter`, `tree-sitter-language-pack` — and pruned `pytest-timeout`, which I reinstalled along with the `annotated-doc`/`anyio`/`annotated_types` fastapi shims. No repo change.

---

# STEP M1 — AUDIT OF THE MERGED TREE (`868e97b`)

No live inference was run (Anthropic key returns HTTP 400, no credit). Every finding below is static, with `file:line`.

## A. Recall create-bot payload — `variant` is ABSENT

`services/in-meeting/src/transport/recall.py:197-215` is the complete body:

```python
body: dict[str, Any] = {"meeting_url": meeting_link, "bot_name": self._bot_name}
if self._webhook_url:
    body["recording_config"] = {
        "transcript": {"provider": {"assembly_ai_v3_streaming": {}}},
        "realtime_endpoints": [{"type": "webhook", "url": self._webhook_url,
                                "events": list(_REALTIME_EVENTS)}],
    }
if self._output_media_url:
    body["output_media"] = {"camera": {"kind": "webpage",
                                       "config": {"url": self._output_media_url}}}
return body
```

**There is no `variant` key, and `web_4_core` appears nowhere in the repo.** The bot therefore runs on Recall's default `web` variant. **VERDICT: NOT SET — confirmed by absence across the whole tree.**

## B. Speech path — Output Media webpage, but a clip sink also exists

The conversational path is the Output Media webpage (`recall.py:209-215`, `output_media.py`). **However, `POST /bot/{id}/output_audio/` is present and implemented** at `recall.py:101-108`:

```python
async def write_audio(self, chunk: AudioChunk) -> None:
    if self._is_muted is not None and self._is_muted():
        return
    body = {"kind": "mp3", "b64_data": base64.b64encode(chunk.pcm).decode("ascii")}
    await self._call_external(
        lambda: self._via_api("POST", f"/bot/{self._bot_id}/output_audio/", body),
        service="recall")
```

Its own docstring at `recall.py:78` and `:112` calls it "this (unused) clip sink". `automatic_audio_output` appears only in a docstring (`recall.py:73`), never in a payload. **VERDICT: PARTIAL — the webpage is the live path, but a second audio sink exists in code. I did not trace which `write_audio` implementation the production speak pipe is bound to (`speak.py:303` calls `self._channel.write_audio(pcm)` on an injected channel), so I cannot state from code which one runs. NOT VERIFIED.**

## C. Chop fix `0eb5251` — VERIFIED FIXED

All sub-questions, from `services/in-meeting/src/in_meeting/output_media.py`:

- **AudioContext rate pinned:** `:650` — `audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: SAMPLE_RATE });` with `:514 const SAMPLE_RATE = 44100;`. **Pinned to 44100, not defaulting to 48000.**
- **Per-chunk resampler:** **no longer engages per chunk.** `:548-553` — "an AudioWorklet pulls from a single Float32 FIFO that every incoming chunk is APPENDED to — no per-chunk buffers, no per-chunk resample, no seams... we ask for a 44100 context (Chrome honors it, zero resampling in our path) and, only if the created context reports a different rate, resample ONCE at append (never per chunk)."
- **Can pinning delete the resample path entirely?** **No.** It is already conditional and dead on Chrome; it survives only as a fallback for a browser that refuses the requested rate. Deleting it would break those browsers. Nothing to gain.
- **Ring buffer vs per-chunk nodes:** ring buffer — `class StreamProcessor extends AudioWorkletProcessor` at `:563` owns the FIFO.
- **Underrun counter:** present — `:577 this._underruns = 0;`, incremented at `:620`, reported at `:587` with `cuts` and `fifoDepth`.
- **Jitter prefill:** `:554 const PREBUFFER_S = 0.35;` — **350 ms banked before emitting after silence.** `:555 RAMP_S = 0.005` (5 ms click-free fade).

**This is a real fix, correctly implemented. But `PREBUFFER_S = 0.35` is a deliberate, unconditional 350 ms added to every speech onset after silence — the largest single latency constant I can name with certainty in this tree.**

## D. Cartesia — HTTP, per utterance, and fully buffered before any chunk is returned

`services/in-meeting/src/transport/tts.py:143-151`:

```python
pcm = bytearray()
async with http_client(timeout=_HTTP_TIMEOUT_S) as client:
    async with client.stream("POST", f"{_CARTESIA_BASE}/tts/bytes",
                             headers=headers, json=body) as resp:
        resp.raise_for_status()
        async for part in resp.aiter_bytes():
            pcm.extend(part)
step = max(_SAMPLE_RATE_HZ * _BYTES_PER_SAMPLE * self._chunk_ms // 1000, _BYTES_PER_SAMPLE)
return [bytes(pcm[i : i + step]) for i in range(0, len(pcm), step)]
```

- **Persistent per meeting or per utterance?** **Per utterance.** `async with http_client(...)` opens and closes a client inside the synth call. No pooling across utterances, no persistent socket.
- **WebSocket with `contexts` / `continue: true`?** **No.** It is a one-shot HTTP `POST /tts/bytes`. Neither `contexts` nor `continue` appears in the file.
- **Reconnect on hot path?** Every utterance is a fresh TLS + HTTP setup — that *is* a connect on the hot path.
- **The critical part:** despite calling `client.stream(...)`, the loop accumulates **the entire response** into `pcm` and only then slices it. **The function cannot return a first chunk until Cartesia has finished synthesizing the whole utterance.** Streaming is used for transport, not for latency.

**VERDICT: per-utterance HTTP, fully buffered. Highest-value latency finding in this audit.**

## E. First-clause streaming (`85bcae1`) across all spoken paths — NOT VERIFIED

Commit `85bcae1` is "stream the agent's reply to voice — one call, first audio at the first clause"; `1227d56` adds "stream the agent's spoken opener before it acts + TTFT telemetry". Given finding D — the TTS client returns only after full synthesis — any "first audio at the first clause" must be implemented by *sentence batching upstream in `speak.py`*, not by streaming Cartesia's bytes. I found the batching comment in `speak.py` ("Drain EVERYTHING queued as one synth unit: the first sentence rides alone — fast start") but **did not trace every spoken path to confirm all of them go through it.** Resolve by reading `speak.py` end to end against each `to_meeting` medium.

## F. Audio topology — double hop, creds host-side

Cartesia -> backend (`tts.py`, host-side, `CARTESIA_API_KEY` from settings) -> Output Media page over the WS in `output_media.py`. **The page never talks to Cartesia directly**, which is the correct choice for credential containment (`CLAUDE.md` hard rule: the sandbox and the page hold no vendor creds). It is nonetheless a **double hop on the audio path**, unavoidable without moving credentials into the browser.

## G. AssemblyAI end-of-turn / finalization gating — NOT VERIFIED

`recording_config.transcript.provider.assembly_ai_v3_streaming` is `{}` — **an empty config object** (`recall.py:200`). No end-of-turn parameters, no `format_turns`, no confidence threshold, no partial/final selector is passed. Whether STT finalization gates LLM dispatch depends on `_REALTIME_EVENTS` and the webhook handler's partial-vs-final filtering, which I did not trace. Resolve by reading `_REALTIME_EVENTS` in `recall.py` and the transcript branch in `control_plane/webhooks.py`.

## H. LLM hot path — partially verified

- **`cache_control`: ABSENT.** Zero occurrences across `services/` and `libs/`, re-confirmed on the merged tree. No explicit prompt caching is requested anywhere.
- **Model / streaming / max_tokens / tool round-trips before first spoken token:** NOT VERIFIED on this tree. The prior audit established `session_host.py` `DEFAULT_MODEL = "claude-sonnet-4-6"` and `max_turns=40` pre-merge; I did not re-verify those constants after the merge, and did not trace how many tool round-trips precede the first `to_meeting` call.

## I. Join path awaits / E2B template — template still NOT baked

`services/in-meeting/src/in_meeting/workroom.py` retains `DEFAULT_TEMPLATE: str | None = None` with the comment "Until it is baked, None provisions a base sandbox and sets it up at warm time (proven path)". **The known bug is still live: every meeting installs the toolchain at join rather than booting a baked template.** Full await-chain enumeration and parallelizability analysis not completed. PARTIAL.

## J. Repo-map rebuild debounce — still ABSENT (bug live)

`services/premeeting/src/premeeting/refresh.py` contains no debounce, coalescing, lock, or dedup, and `control_plane/github_webhook.py`'s `_maybe_refresh_map` still applies no `ref`, `sender.type`, or `forced` gate. **Unchanged by the merge — every push to any branch, including bot commits, triggers a full map rebuild.**

## K. Key fallthrough — there is NO fallthrough; the failure mode is different

`libs/agentkit/src/agentkit/sdk_provider.py:201-210`:

```python
if api_key:        env["ANTHROPIC_API_KEY"] = api_key
elif auth_token:   env["ANTHROPIC_AUTH_TOKEN"] = auth_token
elif oauth_token:  env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token
```

This is **static precedence at construction time, not a runtime fallthrough on failure.** There is no retry, no credit check, no catch that re-mints with the OAuth token.

**So the premise of K is incorrect: production is NOT silently running on the personal token.** The opposite holds — if `ANTHROPIC_API_KEY` is present, it wins unconditionally, and because it is out of credit **every map build fails and the subscription token is never reached.** `.env` currently contains both keys, so this failure is live wherever that `.env` is loaded. The fix is to unset `ANTHROPIC_API_KEY`, not to change code.

## L. Regions — UNVERIFIABLE FROM CODE

`terraform/cloud_run.tf:17` uses `var.region` with no default in `terraform.tfvars.example`; `cloud_run.tf:85` passes a separate `var.recall_region`. Cartesia and AssemblyAI regions are vendor-side. **Cloud Run `min-instances` does not appear in `cloud_run.tf`.** Resolve in the GCP console and the Recall/Cartesia dashboards.

## M. Telemetry — TTFT and underruns exist; the named span markers do not

Counted across `services/**/*.py` on the merged tree:

| marker | occurrences |
|---|---|
| `ttft` | **27** |
| `underrun` | **40** |
| `queued_ms` | **12** |
| `stt_final` | 0 |
| `llm_dispatch` | 0 |
| `llm_first_token` / `first_token` | 0 |
| `tts_dispatch` | 0 |
| `tts_first_chunk` | 0 |
| `page_first_chunk` | 0 |
| `playback_start` | 0 |

The merge brought real instrumentation — TTFT, underrun counters surfaced from inside the worklet (`output_media.py:587`), and `queued_ms`. **But none of the seven requested span boundaries exists by name**, so the end-to-end budget cannot currently be decomposed into stt -> llm -> tts -> page -> playback.

## Ranked fix table

| # | Fix | Files | ms saved | Certainty | Diff lines |
|---|---|---|---|---|---|
| 1 | Stop buffering the whole Cartesia response — yield chunks as `aiter_bytes` produces them instead of accumulating into `pcm` and slicing after | `transport/tts.py:143-151` | full synth duration of the first utterance; hundreds of ms to seconds | **GUARANTEED** (structural — a first chunk currently cannot exist before the last) | ~15 |
| 2 | Set `"variant": "web_4_core"` in the create-bot body | `transport/recall.py:197` | Recall's own stated choppy-audio cause; ms unquantified | **PROBABLE** (vendor claim, not measured here) | 1 |
| 3 | Reuse one HTTP client/connection across utterances instead of `async with http_client(...)` per synth | `transport/tts.py:143` | one TLS+TCP setup per utterance | **GUARANTEED** (direction), **NEEDS-DATA** (magnitude) | ~10 |
| 4 | Add the seven missing span markers so the budget can be decomposed | `speak.py`, `webhooks.py`, `output_media.py` | 0 directly — unblocks every other estimate | **NEEDS-DATA** | ~40 |
| 5 | Tune `PREBUFFER_S` down from 0.35 once #1 and #3 reduce arrival jitter | `output_media.py:554` | up to 350 ms at speech onset | **NEEDS-DATA** (lowering it before fixing #1/#3 will cause underruns) | 1 |
| 6 | Bake the E2B template | `workroom.py:99` + `deploy/e2b/` | toolchain install per join | **PROBABLE** | 1 + a bake |
| 7 | Unset `ANTHROPIC_API_KEY` so the subscription token is reached | `.env` (no code change) | not latency — unbreaks map builds | **VERIFIED** (precedence at `sdk_provider.py:201`) | 0 |
| 8 | Gate map rebuild on default-branch + non-bot | `github_webhook.py:227` | not latency — cost | **VERIFIED** (absence) | ~4 |

Fix 1 is the one to do first: it is structural, provable by reading, and no amount of downstream tuning can compensate for a TTS client that cannot emit a first chunk until synthesis completes.

---

# TASK 6 — the four NOT-VERIFIED items, closed

Static, read-only, against `main` with the four local latency commits applied.

## E. First-clause streaming covers ALL spoken paths — VERDICT: ALL

There is **exactly one TTS dispatch site in the product**, so the question resolves structurally rather than by enumeration: every spoken path funnels through the same synth loop, and the TASK 1 streaming change therefore covers all of them, not just the opener.

The funnel, from the two entry points down:

- `services/in-meeting/src/in_meeting/meeting_connection.py:162` — the `say` / `speak` / `voice` medium:
  ```python
  if self.cut_latched:
      return MeetingSend("say", False, "dropped: barged-in")
  await self.speak.say(content)
  ```
- `services/in-meeting/src/in_meeting/meeting_connection.py:201` — the unknown-medium fallback, which deliberately speaks rather than dropping the agent's words:
  ```python
  # Unknown medium → default to voice rather than dropping the agent's words silently.
  await self.speak.say(content)
  ```

Both call `SpeakPipe.say` (`speak.py:169`), which only **enqueues** — `:175` `self._queue.extend(completed)`, `:189` and `:216` `self._queue.append(tail)`. Nothing synthesizes at the call site. The queue is drained by one worker whose sole synth call is:

- `services/in-meeting/src/in_meeting/speak.py:287` — `async for chunk in self._synthesize(sentence):`

**The opener is not a separate path.** The canned opener is a sandbox-side safety net (`session_host.py:339 opener_fired`, described at `:93` as *"A SAFETY NET, not a replacement: the model's own opener suppresses it (no double-speak)"*). It leaves the sandbox as an ordinary `to_meeting` intent and therefore lands on `meeting_connection.py:162` like any other spoken content. `session_host.py:130` confirms the silent-sentinel path suppresses *"no opener, no TTS, no relay say"* as one unit.

So: one dispatch site, two entry points into it, opener included. **Streaming now applies to every spoken reply.**

## G. AssemblyAI end-of-turn params — VERDICT: UNSET; only FINALS trigger LLM dispatch

**End-of-turn parameters: none.** `services/in-meeting/src/transport/recall.py:200` passes an empty provider config:

```python
"transcript": {"provider": {"assembly_ai_v3_streaming": {}}},
```

No `end_of_turn_confidence_threshold`, no `min_end_of_turn_silence_when_confident`, no `max_turn_silence`, no `format_turns`. AssemblyAI's defaults govern how long it waits before declaring a turn finished.

**Partials are subscribed but never dispatch the LLM.** `recall.py:53-57`:

```python
_REALTIME_EVENTS = (
    "transcript.data",
    "transcript.partial_data",
    "participant_events.chat_message",
)
```

Both arrive, and the drain routes them differently. `control_plane/webhooks.py:42` separates them (`_PARTIAL_TRANSCRIPT_EVENTS = frozenset({"transcript.partial_data", "transcript.partial"})`), and `:301-314` sends a partial to exactly one place:

```python
elif is_partial:
    # A NON-FINAL (partial) line → BARGE-IN ONLY (BUG 3, Law 3). ...
    await runtime.ingest_partial(speaker, text, ts=ts)
```

The comment at `webhooks.py:38-39` states it outright: *"a partial NEVER wakes, never provisions"*.

**So STT finalization gates LLM dispatch.** The wake gate never sees a word until AssemblyAI declares the turn over. Partials are spent entirely on the barge-in reflex — cutting Proxy's speech ~0.5–1.5 s earlier than a final would (`webhooks.py:37`).

**Latency consequence, and it is real but unmeasured.** AssemblyAI's default end-of-turn silence window sits directly on the critical path between a human finishing their sentence and Proxy beginning to think. It is currently unconfigured, so nobody has chosen it — it is a vendor default inherited by omission. Tuning `min_end_of_turn_silence_when_confident` is a one-key change in the payload at `recall.py:200` and is the only latency lever in this audit that costs nothing to try. I have **not** quantified it: doing so requires a live meeting, which needs credit.

## B. `/output_audio/` and `automatic_audio_output` — VERDICT: NOT zero. 12 hits.

The expectation was zero. It is not met.

| file:line | what |
|---|---|
| `transport/recall.py:106` | **live code** — `lambda: self._via_api("POST", f"/bot/{self._bot_id}/output_audio/", body)` inside `write_audio` |
| `transport/recall.py:71, 73, 78, 112, 189, 282, 284, 295` | docstrings/comments describing the clip sink and `automatic_audio_output` |
| `services/in-meeting/tests/test_output_media_mute.py:98, 113` | tests asserting mute no longer issues `DELETE .../output_audio/` |
| `services/in-meeting/tests/test_speak_targets_output_media_webpage.py:4` | test asserting the speak path targets the webpage, *not* the clip endpoint |

**`automatic_audio_output` never appears in a request payload** — only in the `recall.py:73` docstring, which explains that the clip endpoint *would* require it. That half of the claim holds.

The `/output_audio/` half does not. `recall.py:106` is a real, reachable POST. Whether it ever fires depends on which object is bound as the speak channel, and the production wiring binds the Output-Media page: `provisioner.py:686` constructs `MeetingConnection(speak=_SpeakSink(pipe=speak_pipe), ...)`, and the pipe writes to the output-media channel. **I could not find any production binding of the Recall clip sink**, and `recall.py:78` and `:112` both call it "this (unused) clip sink" in their own words.

**Verdict: dead code, not a live second audio path — but it is code, not comments, and it is rate-limited vendor surface sitting one wiring mistake away from the speech path.** Deleting it would make the claim true by construction. That is a deletion, not an audit finding, so I have not made it.

## H. Hot-path LLM — VERDICT: streaming YES, cache_control NO, tool round-trips UNBOUNDED-BUT-CAPPED

**Streaming: yes.** `session_host.py:434` — `async for msg in client.receive_response():`. The turn is consumed incrementally, and the code depends on that: `:129` describes detecting the silent sentinel *"in the FIRST streamed content"*, and `:145-149` holds the first streamed tokens without speaking them until the content either completes or rules out the sentinel — *"so no partial 'staying silent' ever leaks to voice (the live BUG-2 failure)"*.

**`cache_control`: absent. Zero occurrences** across `services/` and `libs/` (re-confirmed on this tree, count = 0). The options block at `session_host.py:634-644` sets `model`, `max_turns`, `mcp_servers`, and deliberately sets **no** `allowed_tools` / `tools` / `disallowed_tools` (`:612` — "THE TOOL-WORKSHOP GUARANTEE (do NOT add ... here)"). No caching is requested. Whether the underlying `claude` CLI caches implicitly is invisible from this repo and remains unmeasured — it needs one funded call to read `cache_read_input_tokens`.

**`max_tokens`: not set** in the options block. The SDK/CLI default governs.

**Sequential tool round-trips before the first spoken token: not bounded to zero, and by design.** `max_turns` is 40 (`session_host.py:643`, env `PROXY_MAX_TURNS`). Nothing forces the model to call `to_meeting` before doing tool work — the wake prompt asks it to deliver in one turn, but that is instruction, not enforcement. Two mitigations exist and both are prompt-level, not structural:

- the canned opener safety net (`:93`, `:339 opener_fired`) fires *only while nothing else has spoken*, so a slow research turn still gets an early acknowledgment;
- `:88` records the reason: *"adaptive thinking at `EFFORT=high` makes the model think hard BEFORE any text streams"*, and `:92` — *"COMMITTED TO WORK, we emit ONE short canned acknowledgment, THEN let the real work + answer stream."*

So the true answer is: **an arbitrary number of tool round-trips may precede the first *substantive* spoken token, up to the 40-turn cap; the opener bounds the first *audible* token instead.** Those are different guarantees and the distinction matters for any TTFT metric — measuring "first audio" flatters the system relative to "first answer".
