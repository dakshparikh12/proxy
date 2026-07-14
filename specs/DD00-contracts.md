# PROXY · DD00 — Contracts & Amendments
Owns everything no single DD owns. Where this file and a DD disagree, THIS FILE WINS.
Items tagged [RATIFY] are proposed defaults awaiting both founders' sign-off.

## 1. The Mouth Arbiter (owns the voice channel)
One serializer owns TTS output. Priority: human ask response > hard-floor surface >
dial-gated surface. One utterance on the wire at a time; newer contribution on the same
anchor supersedes an in-flight one. BARGE-IN: any human speech >400ms pauses Proxy's TTS
within 300ms [RATIFY]; if the human continues >2s, the utterance is banked (reveal or
re-surface at next natural break); else resume with a brief re-anchor ("—as I was saying").

## 2. Participant → Estate Identity Mapping
Ladder: calendar-invite email → org directory/SSO email → source-host account (GitHub
noreply/commit email match) → verified link stored per person. Unmapped participant ⇒
that person contributes NO principals; on join Proxy states scope honestly. The meeting's
permission context = intersection of mapped participants' principals [RATIFY].

## 3. Proactive Permission Context (patches DD04's bundle)
The proactive bundle carries identity = the room-intersection principal set from §2.
All proactive grounding reads run under it. A catch grounded in content outside the
whisper-target's own access may not be whispered to them.

## 4. The `fact` Node Type (patches DD01 — merge-back landing place)
fact(id, attached_node_id, text, source[meeting_id|human|doc], stated_by, at,
supersedes_id|null, verify_state). Meeting decisions, human answers to open questions,
and DD04 Recall reads/writes all use this type. Supersede-with-timestamp, never overwrite.

## 5. Expert Registry (assigns the orphan to DD01 System 4)
Standing up an expert ALSO writes its registry entry: {pack_id, unit meta, example
queries (generated at carve time), embedding}. Registry syncs on re-carve (CRUD by
unit-id diff). DD03/DD05 find_experts reads this. Owner: DD01.

## 6. Crash / Reconnect Recovery
Bot or process death mid-meeting: rejoin (Recall dedup on meeting-id — never two bots),
replay utterance log from last committed page stamp, rebuild page state; in-flight
workroom jobs resume from the DD05 jobs table at envelope boundaries. Target: recovered
within 60s with zero lost committed state [RATIFY].

## 7. Tenancy
v0 = single-tenant-per-deployment (one Postgres, one customer). All schemas still carry
tenant_id from day one so multi-tenant later is a deploy change, not a migration [RATIFY].

## 8. Amendments to the DDs (each overrides its DD)
- DD02: utterance→committed-page-update latency budget p95 ≤ 2.0s [RATIFY]. Referent
  resolution accuracy floor ≥ 90% on the labeled set; sub-floor ⇒ open question [RATIFY].
  Page-coverage floor ≥ 85% of ground-truth claims/decisions on replay [RATIFY].
  Week-1 spike REQUIRED: Graphiti vs. single-pass Postgres page updater, measured
  against the 2.0s budget on a real transcript; loser deleted. Retention: utterance
  log + meeting graph deletable per-meeting on request (GDPR erasure); "append-only"
  means no in-place edits, not no deletion.
- DD04: outcome target — ≤ 1 unwanted surfacing per meeting-hour at default dial,
  measured in shadow before live [RATIFY]. Hard floors also fire on implicit consensus:
  momentum=converging + no new info for N turns triggers the decision-final check even
  without an explicit status write.
- DD06: dial live-preview is scoped PER-VIEWER — it never shows another person's
  whisper-scoped item. Recall.ai sits behind a MeetingTransport adapter (join, audio-in,
  canvas-out) — defined before the Output Media spike, so the fallback path exists
  before we learn whether we need it.
- DD05: receipts attach in full to artifacts/actions; spoken answers carry a one-line
  receipt with "expand" on the tile (receipt-fatigue guard).

## 9. Founder work no loop can do (tracked here so it isn't lost)
Record + label real meetings for the DD02 replay corpus (start NOW — weeks of lead
time). Ratify every [RATIFY]. Run the DD02 spike. Author golden fixtures.
