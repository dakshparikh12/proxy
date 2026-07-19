e n>=100") never exclude LSP-bound/multi-pass samples. The measured population is undefined: legitimately-exempt LSP answers could drag the distribution over threshold (false fail), or the gate is gamed. The exclusion predicate must be in the oracle.

**4. OMITTED "never" RULE — no criterion forbids Pipecat/LiveKit / a voice framework.**
Spec §2: *"**The big simplification: no voice framework.** We deliberately skip Pipecat/LiveKit-class voice-agent frameworks."* Closing stack line: *"**explicitly no Pipecat/LiveKit** (Recall owns transport)."*
grep for `pipecat|livekit|voice.framework` across both bundle files: **zero hits.** This is a structural dependency-absence "never" rule, exactly the kind the bundle *does* enforce elsewhere (AC-SEAM-08 "no libs/transport", AC-SEAM-17 "zero per-platform code", AC-SEAM-07 "no bus/broker/wire"). Its omission is inconsistent — a build could vendor Pipecat and pass.

**5. VISION-REDUCED-TO-TECHNICALITY — AC-SEAM-13 cost floor is a rate-card tautology.**
Spec §1: *"hold the cost floor (~$0.75–0.85/meeting-hour managed …)."*
AC-SEAM-13 oracle: *"Sum the component rates; assert 0.75 <= total_hr <= 0.85."* This asserts that hard-coded rate-card constants sum to the range they are defined to sum to — it passes by construction and binds nothing about the built system's actual spend. The honest-accrual side is caught by AC-SEAM-14 (elapsed×rate), but the "hold the floor" product intent is reduced to a constant-sum check a wrong build trivially satisfies.

**6. WEAK ORACLE — AC-FAIL-11 uses `type: judge` where a deterministic oracle exists.**
Criterion checks *"no over-promised buffering claim … mark-lost is the wired fallback; confirm-at-build note present."* All three are statically decidable: absence of a buffer-resume code path, presence of the mark-lost path, presence of the note — which the sibling AC-HEAR-11 already does via `analysis_plus_assertion`. Per review directive #2 (judge where a deterministic oracle exists), this should be tightened from `judge` to the deterministic form.

**7. (lower-confidence) BOUNDARY GAP — the initial "who is present" roster snapshot has no dedicated criterion.**
Spec §3.1: *"The transport streams participant events — **who is present**, joins, leaves, with names."*
AC-EVENTS-02/03 cover join and leave deltas; R-doc02-EVENTS-01 names "presence," but no criterion asserts the initial present-set (participants already in the room at Proxy's join) is emitted. It's partially shadowed by AC-EVENTS-05's "participant list" metadata pass-through, so this is the softest item — flagging for completeness of the join/leave/**present** triad.

---

Items 1–4 are the load-bearing ones (an omitted SLO, a threshold that contradicts the pinned source-of-truth, a mis-scoped P0 latency population, and a missing structural "never"). I did not route any of these to `ambiguities.yaml` because none is a spec contradiction — they are coverage/oracle defects in the bundle, fixable without a spec change.
