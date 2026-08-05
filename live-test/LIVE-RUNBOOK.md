# LIVE-RUNBOOK — bring up Proxy for a real meeting, and watch the chain

This is the exact bring-up for the live retry. The full chain is proven offline by
`services/control-plane/tests/test_meeting_in_a_box.py` (a fed real-shape webhook → wake →
spoken PCM on the output-media channel + mirrored wake record). What remains live-only is:
the real E2B microVM, the real Anthropic subscription round-trip inside it, and Recall's
actual ingestion of the output-media page's PCM. Everything else is covered by tests.

The chain, stage by stage (what to watch at each):

```
Recall POST /webhooks/recall  →  webhook_events row  →  drain_pending_webhooks (0.25s poll)
  →  provision_launch (make_provision_launcher)  →  provision_meeting: atomic claim +
     _assemble_workroom → provision_workroom (E2B sandbox + warm session_host.py)
  →  runtime registered + session wired (pre-wire buffer flushed)
  →  MeetingSession.on_line: append + feed MEETING_NOTES.md + WAKE GATE (\bproxy\b)
  →  run_ask: append WAKE_IN, poll WAKE_OUT/<id>.json, mirror to $PROXY_WAKE_OUT
  →  MeetingConnection replay  →  SpeakPipe  →  output_media channel PCM  →  Recall bot mic
```

---

## 1. Environment variables

The settings boot gate (`control_plane/settings.py`) crashes at boot **naming** any missing
hard key. Run in a **non-prod** env for the live test so the prod-only extra gates
(`SESSION_SECRET`, `SESSION_SIGNING_KEY`, `INTERNAL_RECONCILE_TOKEN`) don't fire — but you
must STILL set the load-bearing ones below yourself, because they are silently required by the
runtime even in non-prod.

```bash
# --- process env (do NOT set PROXY_ENV=prod for the tunnel test) ---
export PROXY_ENV=local

# --- boot hard-gates (unconditional; boot crashes naming any that are unset) ---
export DATABASE_URL="postgresql://…"          # the Cloud SQL / local Postgres DSN
export GCS_BUCKET="proxy-…"                    # object store (map cache; any real bucket)
export RECALL_API_KEY="…"                      # Recall dashboard API key
export AES_KEY_RECALL="…"                      # 32-byte base64 (any of the three AES keys)
export AES_KEY_STT="…"
export AES_KEY_CALENDAR="…"

# --- Anthropic auth: the SUBSCRIPTION token satisfies the gate (no ANTHROPIC_API_KEY needed) ---
export CLAUDE_CODE_OAUTH_TOKEN="…"            # the founder subscription; the ONLY cred the sandbox gets

# --- LOAD-BEARING even in non-prod (the live chain silently dies without these) ---
export PUBLIC_BASE_URL="https://<your-tunnel>.ngrok-free.app"   # relay + approve links + output-media origin
export RECALL_WEBHOOK_URL="https://<your-tunnel>.ngrok-free.app/webhooks/recall"  # Recall delivers transcripts HERE
export RECALL_OUTPUT_MEDIA_URL="https://<your-tunnel>.ngrok-free.app"  # origin the bot streams as its camera
export RECALL_WEBHOOK_SECRET="…"              # HMAC secret; /webhooks/recall fails CLOSED (401) if unset+signed
export CARTESIA_API_KEY="…"                   # TTS (the voice); no key ⇒ synth faults, no audio

# --- the monitored-smoke taps (test-plane only) ---
export PROXY_INTERNAL_TOKEN="pick-a-long-random-string"   # gates /admin/test-provision, /admin/transcript, /admin/replica-say

# --- the host-side wake-record MIRROR: set this so you can SEE each wake's DID trace host-side ---
export PROXY_WAKE_OUT="/tmp/proxy_wake_out"    # Workroom mirrors WAKE_OUT/<id>.json here (tools/sent/timing)

# --- optional: snappier warm-up / diagnosis ---
# export PROXY_WORKROOM_MODEL="claude-sonnet-4-6"   # the default; do not use Haiku (over-explores, never delivers)
# export CONTEXT7_API_KEY="…"                        # only if you WANT the pre-wired live-docs MCP (egress-dependent)
```

**Critical correctness note (suspect b — egress):** the E2B sandbox is created with
`AsyncSandbox.create(...)`; E2B's default is **`allow_internet_access=True`**, and the product
does **not** override it. So the sandbox DOES have internet → `api.anthropic.com` (the warm
session's Claude) and `PUBLIC_BASE_URL` (the relay) are reachable. The code comments that say
"egress is denied" are **inaccurate** — the Law-3 boundary is enforced by the *absence of
push/send credentials*, not by egress. **Do NOT "fix" the comments by passing
`allow_internet_access=False`** — that would make every wake hang forever with no WAKE_OUT
record (exactly the live symptom). Leave internet ON.

---

## 2. Start the server

```bash
cd /Users/daksh/Desktop/proxy
# fail-fast config gate runs on import; then uvicorn on $PORT (default 8080)
.venv/bin/python -m control_plane.server
```

Expected boot log order (the s6 sequence): `tracing → pool → database → provisioner_ready →
reaper → routers`. If it crashes, it will name the missing key: fix that env var and re-run.

Watch for: `webhook_drain` starting (the 0.25s poll loop) and no `map-build seam wiring failed`
fatal.

## 3. Tunnel

```bash
ngrok http 8080          # or cloudflared tunnel --url http://localhost:8080
```

Copy the public https URL into `PUBLIC_BASE_URL` / `RECALL_WEBHOOK_URL` /
`RECALL_OUTPUT_MEDIA_URL` **before** starting the server (they're read at request/join time,
but set them first to avoid a restart). Verify the tunnel:

```bash
curl -s https://<tunnel>/health           # → {"status":"healthy"}
curl -s https://<tunnel>/output-media/probe  # → the orb page HTML (proves the surface is mounted)
```

## 4. Put Proxy in a real meeting (skip the Google-OAuth wall)

Start a Google Meet (or Zoom/Teams) yourself, then:

```bash
curl -s -X POST https://<tunnel>/admin/test-provision \
  -H "X-Internal-Token: $PROXY_INTERNAL_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"meeting_url":"https://meet.google.com/abc-defg-hij","repo":"https://github.com/calcom/cal.com"}'
# → 200 {"meeting_id":"…","bot_id":"…"}
```

This drives the REAL `invite_proxy` → a REAL Recall bot with the full join body
(`realtime_endpoints` webhook → your `/webhooks/recall`, `assembly_ai_v3_streaming` transcript,
`output_media.camera` webpage = `<tunnel>/output-media/<meeting_id>`).

**Watch the logs (in order):**
- Recall bot launches, the consent chat posts (you see it in the meeting).
- `workroom provisioned: sandbox=… repo=… relay=True warm=True` — the sandbox came up and the
  warm session host launched.
- `warm host readiness at provision: … ready=True` — the in-sandbox Claude session is open
  (SDK client + `.mcp.json` `to_meeting` loaded). If `ready=False`, the first wake will
  restart-and-retry; if it's ALWAYS false, the sandbox can't reach `api.anthropic.com`
  (check egress / the OAuth token) — this is the #1 live failure.

## 5. Speak to Proxy — the moment of truth

Say out loud in the meeting, clearly: **"Hey Proxy, can you hear me? Just say hi back."**

**Watch, in order:**
1. **STT lands:** a `transcript.data` row in `webhook_events` (or the drain log). Confirm the
   words with the HEARD tap:
   ```bash
   curl -s "https://<tunnel>/admin/transcript?meeting_id=<meeting_id>" \
     -H "X-Internal-Token: $PROXY_INTERNAL_TOKEN"
   # → {"lines":[{"ts":…,"speaker":"<you>","text":"Hey Proxy can you hear me…"}], "captured":true}
   ```
   If `captured:false` / no lines → Recall isn't delivering transcripts → check
   `RECALL_WEBHOOK_URL` is EXACTLY `<tunnel>/webhooks/recall` and the tunnel is reachable from
   the internet, and `RECALL_WEBHOOK_SECRET` matches what's configured for the delivery.
2. **Wake fires:** the wake gate is `\bproxy\b` (case-insensitive) — "Hey Proxy…" matches. No
   log line for the gate itself; you'll see the wake proceed to run_ask.
3. **Wake serviced:** a new file appears in `$PROXY_WAKE_OUT/` (the mirror):
   ```bash
   ls -t /tmp/proxy_wake_out/ | head; cat /tmp/proxy_wake_out/$(ls -t /tmp/proxy_wake_out | head -1)
   # → {"tools":[…],"text":"…","sent":[{"content":"…hi…","medium":"say",…}], …}
   ```
   If NO file EVER appears → the warm session never answered: the sandbox can't reach Claude
   (egress/token) OR the host crashed — check the sandbox `session_host.log` and the
   `warm host heartbeat frozen` / `warm session unavailable after restart` error lines.
4. **You HEAR it:** Proxy speaks "Hi — yes, I can hear you…" through the bot's mic. The audio
   path is: the record's `say` → `MeetingConnection.to_meeting(medium="say")` (or, in relay
   mode, the sandbox streams sentences to `POST /meetings/<id>/relay` mid-turn) → `SpeakPipe`
   (Cartesia synth) → `output_media.channel_for(<meeting_id>)` PCM → the page at
   `/output-media/<meeting_id>/ws` → the Recall bot's webpage-mic.

   **If the record exists (step 3) but you hear NOTHING** — the failure is on the audio leg,
   not the brain. Confirm the channel id matches: the bot's page loaded
   `<tunnel>/output-media/<meeting_id>` and the speak path writes to
   `channel_for(<meeting_id>)` — SAME id (verified consistent in code). Sanity-check the audio
   leg in isolation with the replica-say tap:
   ```bash
   curl -s -X POST https://<tunnel>/admin/replica-say \
     -H "X-Internal-Token: $PROXY_INTERNAL_TOKEN" -H "Content-Type: application/json" \
     -d '{"channel":"<meeting_id>","text":"testing one two three"}'
   # → {"chunks":N,"clients":M}   (clients>0 means the bot's page IS attached)
   ```
   `clients:0` ⇒ the bot's page never connected to the WS (bad `RECALL_OUTPUT_MEDIA_URL`, or the
   tunnel drops WebSocket upgrades). `chunks:0` ⇒ Cartesia synth produced no audio (check
   `CARTESIA_API_KEY`).

## 6. End the meeting

Remove the bot (or end the call). The `bot.call_ended`/removed webhook drains →
`registry.end_meeting` → drains in-flight turns, closes the speak pipe, kills the sandbox,
drops the output-media channel.

---

## Quick failure → cause table

| Symptom (live) | Most likely cause | Where to look |
|---|---|---|
| No `transcript.data` rows ever | `RECALL_WEBHOOK_URL` wrong/unreachable, or secret mismatch | `/admin/transcript` `captured:false`; tunnel logs |
| Rows land but no provision | bot_id can't resolve to a meeting | `get_by_bot_id` returns None; check the write-back at join |
| `ready=False` forever / no WAKE_OUT file ever | sandbox can't reach `api.anthropic.com` OR bad OAuth token | `session_host.log`; `warm session unavailable` |
| WAKE_OUT record exists but silence | audio leg: page not attached / bad Cartesia key / channel-id mismatch | `/admin/replica-say` (`clients`/`chunks`) |
| Wakes on "our proxy server" chit-chat | the loose `\bproxy\b` gate fired; the AGENT is meant to judge & stay silent | expected — the wake prompt handles false wakes |
| Wake hangs ~15 min then silence | dead/hung host not detected (heartbeat) | should self-heal in ~20s via `PROXY_DEAD_HOST_TIMEOUT_S` |
