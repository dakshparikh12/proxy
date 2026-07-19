ass ask."**
AC-CHAT-02 tests only the literal string `"@proxy check the p95"`. AC-CHAT-04 tests the **reject** side (non-addressed). The middle case the spec explicitly names — a message *addressed to Proxy without the @ token* (e.g. "Proxy, can you…") — has no positive criterion. A build that keys strictly on the "@proxy" token passes the whole section while dropping the spec's broader "any addressed message" contract.

### 7. [Weak oracle] "detail goes to chat, not spoken" asserted-by-name but not verified
§2 ch.1 / §3.3: **"headlines only … detail goes to chat — both the UX rule and the cost lever."**
AC-SPEAK-03's then-clause reads "detail-length content is routed to chat, not spoken," but its oracle only sums synthesized chars (≤4000/hr) and flags over-cap lines. A build that speaks the headline and simply **drops** the detail (never posting it to chat) satisfies SPEAK-03. The detail→chat routing — the actual design lever — is only partially picked up by AC-CHAT-06 ("upstream hands this layer broadcast content"), and no criterion ties the *un-spoken* detail of a headlined answer to a chat post. (Low-to-medium; depends on whether the headline/detail split is Doc 02's or Doc 04's — if Doc 02's, this is a real hole; if upstream's, the SPEAK-03 then-clause overclaims and should be trimmed.)

### 8. [Omitted negative-scope — consistency] Screen-ingestion-deferred is not asserted, unlike its sibling negatives
§1: **"Screen-*ingestion* (reading what others present) is deferred — not in this doc."**
The bundle captures every *other* "what this is NOT" as an explicit criterion — no Smart Turn v3 in core (AC-TURN-02), no Pipecat/LiveKit (AC-SEAM-21), no live pixel mirror/cursor (AC-CANVAS-07), native buttons not used (AC-SEAM-18). Screen-ingestion-deferred is the one negative-scope boundary with **no** guarding criterion, so scope creep here (building a "read the presenter's screen" path) would trip nothing. Add a parallel negative-scope criterion for symmetry.

### 9. [Minor — parity completeness] Failure-honesty spoken lines not bound to the text-copy parity invariant
§3.7 / narrative §132: the gap line (**"it rejoins and says exactly that"**) and the **"voice is down — I'll type"** notice are user-visible spoken/emitted lines, but AC-FAIL-03 / AC-FAIL-12 don't bind them to the AC-SPEAK-05 verbatim text-copy parity invariant. Low severity (the gap line already targets notes/chat and the voice-down notice is text by construction), but a one-line tie-in closes it. (Matches prior sweep item 6 — still unaddressed.)

---

**Severity ranking:** #1 and #2 are load-bearing (a plausible build passes today while violating a manner or an SLO, or two P0 criteria are mutually unsatisfiable) and #1 additionally warrants an `ambiguities.yaml` route since the tension is spec-internal. #3–#5 are real weak-oracle/vision holes. #6–#9 are omission/completeness gaps. None of the remaining spec behaviors I walked were uncovered — the misses above are the exhaustive set.
