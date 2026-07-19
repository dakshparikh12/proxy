§2/§3.5 product intent: *"we render that live view onto the presented screen, so the room watches the work form instead of waiting on a spinner"* and *"'Can you search this up?' → Proxy shares its screen and visibly does it."* CANONICAL §12.11 correctly bounds V0 to a structured progress view (AC-CANVAS-07), but the surviving coverage is weak on the *feel*:
- AC-CANVAS-06 only asserts the sandbox live-work view is "rendered onto the presented screen."
- AC-CANVAS-15 makes frame-rate/smoothness an explicitly **non-gating** pinned measurement (`frame_rate_is_hard_gate: 0`).

A once-rendered, essentially static progress card with no meaningful update cadence satisfies CANVAS-06 + CANVAS-07 + CANVAS-15 while delivering the *"waiting on a spinner"* experience the spec sets out to avoid. Nothing pins a minimum update/liveness cadence for the live-work view. **Recommend a liveness/update-cadence criterion so "watches the work form" isn't satisfiable by a static frame.**

### 5. [Weak oracle + over/under-specification] Swap "announcement" — channel and semantics unpinned
§3.9 step 6: *"announced swaps"*; §3.5: *"speak the headline, then swap to the screen; swap back when done."* AC-CANVAS-10 asserts only `count(announcements) == count(swap_transitions)` and does not:
- Tie the announcement to any **channel** (a silent log line "announces" and passes) — so the oracle is satisfiable without any human-observable announcement.
- Tie it to the speak-path constraints it implies if spoken (boundary-gated per SPEAK-07, one-voice per SPEAK-03, text-copy parity per SPEAK-05).
- Match the spec's asymmetry: the spec announces *before swap-to-screen* (the spoken headline) but says *"swap back when done"* with no announcement — yet AC-CANVAS-10 demands an announcement on **every** swap including swap-back.

(Note also: R-doc02-CANVAS-10's `source_quote: "the swap itself is announced"` is not verbatim spec text — the spec says "announced swaps"/"speak the headline, then swap.") **Recommend pinning the announcement channel and reconciling the swap-back case.**

### 6. [Minor — parity gap] Failure-honesty spoken lines not bound to text-copy parity
§3.7 / narrative §132: the gap line (*"it rejoins and says exactly that"*) and the *"voice is down — I'll type"* notice are user-visible spoken/emitted lines. Naming is covered (AC-XCUT-01 lists "disconnect-gap line"/"ack line"), but AC-FAIL-03 / AC-FAIL-12 don't bind these lines to the SPEAK-05 text-copy parity invariant. Low severity (the gap line already targets notes/chat, and the voice-down notice is text by construction), but worth a one-line tie-in for completeness.

---

Items 1–3 are the load-bearing gaps (a wrong build passes today); 4–5 are real weak-oracle/vision holes; 6 is a completeness nit. None rise to an impossible/contradictory spec bug requiring `ambiguities.yaml` routing, except the *contingent* impossibility flagged in item 2, which should instead be closed with a confirm-at-build hedge like its siblings.
