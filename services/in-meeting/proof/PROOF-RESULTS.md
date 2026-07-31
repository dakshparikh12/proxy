# Real-data proof — reactive workroom v6 on cal.com

**What was proven:** native Claude in a live E2B sandbox, on the founder's subscription, with the
cal.com repo cloned in + the v6 nuance prime (`in_meeting/prime.py`) + the one-tool meeting MCP
(`in_meeting/sandbox_meeting_mcp.py`), served a battery of reactive meeting asks — deciding
dynamically what to do and how to respond, with NO hard-coded situation→action logic.

Real infra: E2B (Firecracker), Anthropic subscription (CLAUDE_CODE_OAUTH_TOKEN), `mcp==1.28.1`.
Meeting I/O simulated (transcript fed in as a file; `to_meeting` calls captured) — the live
Recall/Cartesia audio round-trip is the founder's manual test.

## Full nuance battery (one sandbox, default model)

| scenario | latency | responded | channel | outcome |
|---|---|---|---|---|
| code-lookup | 29.5s | yes | say | grounded: `packages/lib/slugify.ts`, correct unicode behavior, ran the real code |
| chit-chat | 12.0s | yes | say | brief friendly reply, proportional (light work) |
| mute-command | 13.5s | yes | **mute** | chose the mute medium itself ("Muting myself now.") |
| ambiguous-clarify | 21.8s | yes | say | asked ONE clarifying question — did not guess at an unrecoverable change |
| research | 26.9s | yes | say | web-researched Stripe proration summary |
| world-touching-offer | 70.3s | yes | **offer** | wrote a real unit test, OFFERED it — did not falsely claim a PR (Law 3) |
| cross-talk-immunity | 11.0s | **no** | — | correctly stayed silent on "our proxy server…" (false-wake immunity) |
| busy-room channel | 19.2s | no (held) | — | held because Alice said "wait before that" — defensible read-the-room; nuance to tune |

`mcp__meeting__to_meeting` fired cleanly on every responding scenario.

## Latency finding (the key optimization)

The ~11–13s floor is **cold-start overhead**, not model compute: the cross-talk scenario did almost
no work yet still took 11s, because this proof **cold-starts `claude` on every ask**. Model routing
confirms it — Haiku made the lookup 20.9s / $0.048 (vs 29.5s / $0.411) but did NOT speed chit-chat
(startup-bound, not compute-bound).

**Production fix = a warm session:** keep one persistent Claude session per meeting (Agent SDK
streaming session) so the ~11s startup is paid once at join, not per ask. Then per-ask latency ≈
model generation + tool work, and the first spoken sentence streams to TTS immediately. Model routing
(a fast model for trivial/lookup asks) is an additional cost+latency lever.

## Reproduce

`uv run --package in-meeting python services/in-meeting/proof/proof_full_battery.py`
(loads creds from `.env`; scenarios in `battery.json`). Requires E2B + a subscription token in `.env`.

## Honest residual
- Warm-session latency is argued from the cold-start evidence; verify it directly when the production
  persistent-session runtime is built.
- `busy-room` channel choice (hold vs. drop-to-chat) is a judgment nuance to observe on real meetings.
- Live Recall/Cartesia audio round-trip = the founder's manual test (not covered by the simulated I/O).
