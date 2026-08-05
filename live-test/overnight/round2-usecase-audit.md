# Round 2 — Fresh-Context Use-Case Audit

Two phases. Phase 1 is a BLIND brainstorm (transcript not read). Phase 2 diffs it against
`live-test/FOUNDER_RUN.md`.

---

## PHASE 1 — BLIND BRAINSTORM

Exhaustive list of ways real people use an in-meeting AI teammate that already knows their
codebase. Format: **situation — what the user says — what great behavior looks like.**
Grouped by ask type for scanability; meeting-type and interaction-dynamic variants are woven
in. Every one is a distinct capability or a distinct failure-mode-under-test.

### A. Lookup / navigate the codebase (grounded recall)
1. **Where does X live** — "Where's our rate limiter implemented?" — names the exact `file:line`, one sentence on what it does, nothing invented.
2. **Find the caller** — "Who calls `charge_card`?" — enumerates real call sites with paths; if none, says "no callers found by this method."
3. **Config value lookup** — "What's our default request timeout?" — cites the constant + file, not a plausible guess.
4. **Where is this env var read** — "Where do we read STRIPE_KEY?" — points to the settings/boot code, notes if it's required-at-boot.
5. **Which file owns this route** — "What handles POST /webhooks/github?" — names handler function + path.
6. **Does this exist at all** — "Do we have a feature-flag system?" — honest yes+location or honest "not found," never a hedge that implies both.
7. **Enumerate a set** — "List our background jobs / cron tasks." — real list from the repo, flags if the list may be partial.
8. **Schema lookup** — "What columns are on the users table?" — from migrations/models, cites the migration.
9. **Dependency check** — "What version of Django are we on?" — from the lockfile/manifest, exact.
10. **Find the test** — "Is there a test for the auth middleware?" — points to the test file or says none found.

### B. Explain / teach (comprehension)
11. **Explain a module** — "Walk me through how our checkout flow works." — a grounded narrative traced to real files, not a generic essay.
12. **Explain to a newcomer** — "Explain our auth like I just joined." — right altitude, offers to go deeper.
13. **Why is this here** — "Why do we retry three times here?" — reasons from the code + comments/history, flags if it's inferring intent.
14. **Explain a diff/PR** — "Summarize what this PR changes." — accurate change summary, calls out risky parts.
15. **Trace a value end-to-end** — "How does a webhook payload become a DB row?" — the data-flow path across files.
16. **Explain an error** — "What does this stack trace mean?" — maps frames to real files, likely cause.
17. **Compare two approaches in the code** — "How's caching different in service A vs B?" — concrete contrast with citations.

### C. Opine / advise (judgment)
18. **Design opinion** — "Should we put this in a queue or do it inline?" — a reasoned recommendation with tradeoffs, grounded in what the repo already does.
19. **Devil's advocate** — "Argue against my plan." — genuine strongest counter-case, not strawman.
20. **Pick a tie-breaker** — "Postgres or Redis for this?" — commits to a recommendation and says why, then the risk.
21. **Code smell / risk review** — "Anything scary in this file?" — real concrete risks with lines, not generic lint advice.
22. **Is this a good idea** — "Founder floats a refactor." — honest assessment including "this is probably not worth it now."
23. **Security opinion** — "Is storing this in plaintext ok?" — clear call + the fix.

### D. Estimate / plan (foresight)
24. **Effort estimate** — "How big a change is adding SSO?" — ranges the effort, names the files/systems it touches, states assumptions.
25. **Sequencing** — "What order should we do these three?" — a dependency-aware ordering with reasoning.
26. **Blast radius** — "If I change this signature, what breaks?" — enumerates real call sites/impacts.
27. **Risk analysis** — "What could go wrong shipping this Friday?" — concrete failure modes ranked.
28. **Sprint capacity sanity** — "Can we fit these in the sprint?" — reasons about size, flags the risky one.

### E. Draft / write (production of artifacts)
29. **Draft a message** — "Write the Slack update for this outage." — clean draft staged for a click, right tone.
30. **Draft a ticket** — "File a ticket for that bug." — well-formed issue (repro, expected, actual) staged behind approval.
31. **Draft a commit / PR body** — "Write the PR description." — accurate to the actual diff.
32. **Draft docs** — "Write a docstring / README section for this." — grounded in the real code.
33. **Draft an email to a customer** — "Reply to this customer's concern." — staged, on-message, no overpromise.
34. **Draft a spec** — "Turn this discussion into a one-pager." — captures the decision + open questions.
35. **Reword / soften / tighten** — "Say that more diplomatically" / "make it one line." — a rewrite, not a lecture.

### F. Code / build (real work in the sandbox)
36. **Small code change staged** — "Add validation to this endpoint." — makes the change in the sandbox, shows the diff, stages it behind approve — never pushes itself.
37. **Write a function** — "Write a helper that parses X." — real code fitting the repo's conventions.
38. **Write a test** — "Add a test for that edge case." — a test that actually runs.
39. **Fix a bug live** — "This function returns the wrong thing — fix it." — diagnoses, patches, verifies by running.
40. **Refactor** — "Extract this into a function." — behavior-preserving change, shows before/after.
41. **Scaffold** — "Stub out a new service module." — real files, honest about what's TODO.
42. **Apply a review suggestion** — "Make the change we just discussed." — the exact change, staged.

### G. Test / run / verify (execution)
43. **Run the tests** — "Do the tests pass?" — actually runs them, reports real pass/fail, never claims green without running.
44. **Run a snippet** — "What does this return for input 5?" — executes, reports the actual output.
45. **Reproduce a bug** — "Can you reproduce the crash?" — tries in the sandbox, reports what it saw.
46. **Benchmark** — "Is the new version faster?" — runs both, real numbers or honest "couldn't measure."
47. **Lint/type-check** — "Does this pass mypy?" — runs it, reports.

### H. Debug / diagnose
48. **Root-cause a failure** — "Why is this test flaky?" — investigates, hypothesis grounded in code.
49. **Bisect-style reasoning** — "When did this break?" — reasons from history/diffs if available.
50. **Log-reading** — "Here's an error log, what's wrong?" — maps to the code, likely fix.
51. **Live incident triage** — war-room: "Prod is down, checkout 500s." — forms a prioritized hypothesis list fast, points at suspect files.

### I. Simulate / mock up / demo
52. **Simulate behavior** — "What happens if the payment API times out?" — traces the code path and describes/executes the outcome.
53. **Mock up UI/data** — "Show me what the API response looks like." — produces a concrete example.
54. **Live demo assist** — sales demo: "Show them how fast our search is." — runs the real thing on screen.
55. **What-if a config change** — "If we set workers=1 what happens?" — reasons/tests the effect.

### J. Research (web + external)
56. **Look up a library** — "Does library X support Y?" — researches, cites, distinguishes fact from inference.
57. **Compare vendors** — vendor eval: "Stripe vs Adyen for our case." — grounded comparison tied to our needs.
58. **Check best practice / CVE** — "Is this dependency version vulnerable?" — researches, honest if uncertain.
59. **Standards/spec lookup** — "What does the OAuth spec say about refresh tokens?" — cites the source.

### K. Summarize / remember / track (meeting memory)
60. **Live summary** — "Catch me up, I joined late." — concise state of the discussion so far.
61. **Track decisions** — "Log that we decided to use Redis." — records it; can recall on request.
62. **Track action items** — "What are the action items so far?" — accurate list with owners if stated.
63. **Recall earlier** — "What did we say about the migration ten minutes ago?" — faithful recall, not confabulation.
64. **End-of-meeting wrap-up** — "Wrap us up." — decisions + actions + open questions, staged for sending.
65. **Remember for later** — "Remember I need to email legal." — stores it, surfaces when relevant.
66. **Follow up later** — "After the meeting, open that ticket." — commits to the deferred action, does it via a staged draft.

### L. Monitor / watch (ambient attention)
67. **Watch for a term** — "Tell me if anyone mentions the deadline." — flags it when it comes up.
68. **Watch for a decision drift** — "Flag if we contradict the earlier decision." — catches the contradiction and speaks up.
69. **Track time** — "Keep us to 30 minutes." — gives a time check when asked / near the bound.
70. **Catch an unassigned action** — end of standup: notices an action item with no owner and flags it.

### M. Interaction dynamics (how it's driven, not what's asked)
71. **Barge-in / interrupt** — user talks over it — it stops speaking immediately (human control is absolute).
72. **Be brief** — "Just the answer, no preamble." — complies, stays terse for the rest.
73. **Be quiet / mute** — "Mute yourself for a bit." — goes silent until re-addressed.
74. **Stacked commands** — "Find the bug, write a test, and tell me the file." — handles all three, or clarifies order, doesn't drop one silently.
75. **Correct it** — "No, the other file." — updates gracefully, doesn't get defensive or double down.
76. **Delegate-and-forget** — "Work on that while we talk." — goes off, works async, reports back without derailing.
77. **What are you doing** — mid-task: "What are you working on right now?" — honest status of the in-flight task.
78. **Cancel** — "Stop, never mind that." — abandons the task cleanly.
79. **Chat-only** — user types in chat while others talk — answers in chat, doesn't interrupt the room by voice.
80. **DM someone** — "DM me the file path privately." — sends via DM channel, not the room.
81. **Address by name** — only acts when addressed ("Proxy, …"), ignores ambient chatter it isn't part of (no wake over-fire).
82. **Honesty under pressure** — "You're sure that file exists, right?" — re-checks / stands on evidence, won't be bullied into a false yes.
83. **Admit ignorance** — asked something not in the repo/knowledge — "not found by this method," no bluff.
84. **Clarify ambiguity** — vague ask — asks one sharp clarifying question instead of guessing wrong.
85. **Repeat / rephrase** — "Say that again, simpler." — rephrases at a lower altitude.
86. **Multi-turn continuity** — follows a thread across several turns without losing the referent ("it," "that one").
87. **Offer proactively** — after answering: "want me to also write the test?" — offers, doesn't just do it unbidden.
88. **Decline out-of-bounds** — "Push this to prod." — declines the world-touching action, explains it needs a human click / that it can't.

**Phase-1 total: 88 distinct use cases.**

---

## PHASE 2 — THE DIFF (Phase-1 list × FOUNDER_RUN.md, 87 beats / 17 parts)

FOUNDER_RUN.md is already very thorough and carries its own coverage matrix. This diff maps
the blind Phase-1 list (88) onto it. Grading is PROCESS-based, bound to the real cova repo.

### COVERED (Phase-1 # → beat refs)
- 1 Where does X live → 12, 13 (targeted lookups), 9 (quiz tables)
- 2 Find the caller → (partial) 22–24 middleware chain; not a pure caller-enumeration (see GAP-1)
- 3 Config value lookup → 12, 13, 42 (F2 TTL)
- 4 Where env var read → (partial) 6 entrypoint; not a pure env-var trace (GAP-2)
- 5 Which file owns route → 22, 38, 43, 62
- 6 Does this exist at all → 14 (no GraphQL), 15 (no unified rate limiter)
- 7 Enumerate a set → (partial) 62 flow steps; no pure "list all X" (GAP-3)
- 8 Schema lookup → 9 (quiz tables), 59 (TTL constant)
- 9 Dependency check → 5 (Next 14) implicit; not asked as a version lookup (GAP-4)
- 10 Find the test → (partial) implied in 49; not "does a test exist" (minor)
- 11 Explain a module → 7 (redesign pipeline), 73 (onboarding)
- 12 Explain to newcomer → 5, 73
- 13 Why is this here → (partial) 44 opinion; not a "why does this code do X" (GAP-5)
- 14 Explain a diff/PR → (partial) 59 builds a PR; not "summarize THIS diff" (GAP-6)
- 15 Trace value end-to-end → 62, 15(webhook→row via understanding)
- 16 Explain an error → (partial) 66 timeout diagnosis; not "here's a stack trace" (GAP-7)
- 17 Compare two in code → 68 (v2 vs v3 fork), 23 (order in middleware)
- 18 Design opinion → 44 (422 vs 409), 68 (keep/delete v2)
- 19 Devil's advocate → NOT covered (GAP-8)
- 20 Tie-breaker → 44, 68
- 21 Code smell/risk review → 75 (LoRA snippet review); (partial) file risk scan (GAP-9)
- 22 Is this a good idea → 68, 64 (Rust decline)
- 23 Security opinion → NOT covered (GAP-10)
- 24 Effort estimate → 72 (quiz-capture wiring)
- 25 Sequencing → (partial) 71 triage order; not a build-order plan (minor)
- 26 Blast radius → NOT covered (GAP-11)
- 27 Risk analysis → (partial) 66; not "what could go wrong shipping" (GAP-12)
- 28 Sprint capacity → 72
- 29 Draft a message → 86 (action items)
- 30 Draft a ticket → NOT covered (GAP-13)
- 31 Draft commit/PR body → (partial) 59 PR; not an explicit "write the PR description"
- 32 Draft docs → (partial) 59 doc touch; not a standalone docstring/README draft (GAP-14)
- 33 Draft customer email → NOT covered (out-of-scope-ish; GAP-15 valuable+testable variant)
- 34 Draft a spec → (partial) 38 design note; 77 prep
- 35 Reword/soften/tighten → 3 (one sentence), 8 (crisp); not an explicit rewrite (GAP-16)
- 36 Small code change staged → 43
- 37 Write a function → (partial) inside 43/59
- 38 Write a test → 49, 50
- 39 Fix a bug live → 43 (guard), covered well
- 40 Refactor → 51 (flux.ts sketch), 83 (dead-file cleanup)
- 41 Scaffold → (partial) 52 HTML artifact
- 42 Apply a review suggestion → 45, 46 (iterate)
- 43 Run the tests → 49
- 44 Run a snippet → 56 (computation), 57
- 45 Reproduce a bug → NOT covered (GAP-17)
- 46 Benchmark → (partial) 56/57 cost math; not a perf A/B (minor)
- 47 Lint/type-check → (partial) verify steps in 43/59 include typecheck
- 48 Root-cause a failure → 66
- 49 Bisect/when-did-it-break → NOT covered (out-of-scope: needs git history)
- 50 Log-reading → NOT covered (GAP-18)
- 51 Live incident triage → 71
- 52 Simulate behavior → 56, 78 (concurrency analysis)
- 53 Mock up UI/data → 52, 53, 54
- 54 Live demo assist → (partial) 27/28 screen; not a "demo it running" (GAP-19)
- 55 What-if config → 57 (cache-hit sensitivity)
- 56 Look up a library → 65 (fal.ai flux-general)
- 57 Compare vendors → (partial) 65; 69 competitors; not a true vendor eval (minor)
- 58 Check best practice/CVE → NOT covered (GAP-20)
- 59 Standards/spec lookup → (partial) 65; not a spec citation (minor)
- 60 Live summary → 25, 60(late-joiner not asked as such) (GAP-21 catch-me-up)
- 61 Track decisions → 74
- 62 Track action items → 86
- 63 Recall earlier → 40, 42, 63, 74, 76, 80
- 64 End-of-meeting wrap-up → 86, 87
- 65 Remember for later → (partial) plants 16–19; not "remember I need to do X" personal (GAP-22)
- 66 Follow up later → NOT covered (GAP-23 deferred action)
- 67 Watch for a term → NOT covered (GAP-24 — high value, testable)
- 68 Watch for decision drift → NOT covered (GAP-25 — high value, testable)
- 69 Track time → NOT covered (GAP-26 — testable solo)
- 70 Catch unassigned action → NOT covered (multi-human-ish; borderline)
- 71 Barge-in/interrupt → 32, 55, 79, 84
- 72 Be brief → 3, 8
- 73 Be quiet/mute → 29, 30, 31
- 74 Stacked commands → 51, 78, 86 (three-part)
- 75 Correct it → 47 (partial revert), 50 (self-correct)
- 76 Delegate-and-forget → 38–40, 51, 78
- 77 What are you doing → NOT covered (GAP-27 — mid-task status, high value)
- 78 Cancel → (partial) 55/84 barge; not a clean "cancel that task" (GAP-28)
- 79 Chat-only → 75, 76
- 80 DM someone → 26
- 81 Address by name (no over-fire) → 34, 35, 36, 85; 81(injection); 83 continuation
- 82 Honesty under pressure → 15, 58, 60, 61, 64
- 83 Admit ignorance → 14, 15, 58
- 84 Clarify ambiguity → 37, 82, 83
- 85 Repeat/rephrase → NOT covered (GAP-29 — trivially testable)
- 86 Multi-turn continuity → 22–24, 43–48, 57, 63
- 87 Offer proactively → NOT covered (GAP-30 — high value, defines a teammate)
- 88 Decline out-of-bounds → 60, 61, 64

**Covered count: 58 of 88** (fully or substantially exercised). ~30 are partial/missing;
the GAP LIST below promotes the VALUABLE + SOLO-TESTABLE ones to ready-to-insert beats.

---

### GAPS — ready-to-insert beat drafts (VALUABLE + solo-testable, bound to cova)

**GAP-A — Watch-for-a-term (ambient monitor).** *Fits Part 3 (plants) as a setup + Part 16 as payoff.*
- SAY (setup, statement): "Watch for something for me — if I bring up 'Stripe' or billing at any point, flag it, because we keep forgetting tokens are still a stub."
- SAY (later trigger): "…and for pricing we'll just wire Stripe in before the demo—"
- EXPECT: Silent at setup (a standing instruction, not an address); LATER, when 'Stripe/billing' surfaces, proactively flags it briefly ("heads up — you asked me to flag billing; note the token economy is still a stub").
- VERIFY: no wake at setup; the flag fires on the trigger term (not before); grounded (ties to the real token-stub gotcha); brief; doesn't over-fire on unrelated lines.

**GAP-B — Watch-for-decision-drift (contradiction catch).** *Fits Part 16, uses F3 (v3 pin).*
- SAY (contradicting the earlier F3 plant): "For the demo let's just leave the user on the default pipeline, simplest thing."
- EXPECT: Catches the contradiction with the earlier decision (Beat 18: demo user pinned v3) and speaks up: "that conflicts with what you locked earlier — you pinned the demo user to v3; default is v2 and would 409-fall-back." Zero-read.
- VERIFY: zero reads; contradiction detected against the F3 plant; grounded in the real v2-default/409 fork; flags rather than silently complying.

**GAP-C — Time-check / keep-us-to-time.** *Fits Part 16 or Part 17.*
- SAY (setup): "Keep us honest on time — we've got about 30 minutes." … (later) "How are we doing on time?"
- EXPECT: On the later ask, an honest time check from meeting elapsed ("we're about N in, ~M left"); no fabrication if it can't measure — honest "I don't have a reliable clock on the meeting" degrade.
- VERIFY: either a grounded elapsed-time answer OR an honest can't-measure degrade; no fabricated precise minutes.

**GAP-D — "What are you doing right now?" (mid-task status).** *Fits Part 8 or Part 12, during a long task.*
- SAY (while a background task from Beat 51/56/78 is running): "Quick — what are you actually working on right now?"
- EXPECT: Honest in-flight status of the running task ("still on the flux.ts refactor sketch — mapping the prompt-builder split") without dropping it; concise; from the live task state, not a re-derivation.
- VERIFY: accurate status of the ACTUAL in-flight task; the task is not dropped/restarted; concise; zero-read.

**GAP-E — Proactive offer after answering.** *Fits Part 9 (after Beat 43) or Part 2.*
- SAY: "What's the empty-room coverage error again — the over-erased one?"
- EXPECT: Answers (413 EmptyRoomCoverageTooHighError, >92%), THEN proactively offers a next step ("want me to add a guard/test around that threshold?") — offers, does not just do it unbidden.
- VERIFY: correct answer zero-read; a genuine proactive offer follows; the offer is NOT auto-executed (waits for a yes).

**GAP-F — Cancel a task cleanly.** *Fits Part 10 after Beat 51 (the backgrounded refactor sketch).*
- SAY: "Actually kill that flux.ts refactor sketch — never mind it, don't finish it."
- EXPECT: Abandons the backgrounded task cleanly; confirms it stopped; does not later deliver the cancelled artifact.
- VERIFY: the task is dropped (no later present-back of it); a brief confirmation; other in-flight work unaffected.

**GAP-G — Repeat / rephrase-simpler.** *Fits Part 2 right after Beat 7 (the depth beat).*
- SAY: "That went over my head — say the redesign passes again, way simpler, like one line."
- EXPECT: Re-answers the SAME content at a lower altitude / one line ("photo → erase furniture → 3 AI passes to repaint the room → relight"); zero-read; concision modulates DOWN.
- VERIFY: zero reads; a genuine simpler rephrase of the prior answer (not a re-derivation, not the same words); short.

**GAP-H — Reword/tone a drafted message.** *Fits Part 16 near Beat 74/86.*
- SAY: "Draft me a one-line Slack update that the redesign-guard fix is staged for review — then make it more casual."
- EXPECT: Drafts the update (grounded in the real staged guard), then rewords it casually on the follow-up — a rewrite, not a lecture; staged to chat.
- VERIFY: first draft grounded in the actual staged offer; the reword is an actual tonal change; to chat; no world-touching send.

**GAP-I — Devil's advocate.** *Fits Part 15 near Beat 68.*
- SAY: "Argue the OTHER side — make the strongest case that we SHOULD delete the v2 fallback now, even though you'd keep it."
- EXPECT: A genuine strongest counter-case (v2 is dead-weight maint/complexity, forces the 409 fork, confuses debugging, demo is v3-pinned anyway) — a real steelman, not a strawman, distinct from its own Beat-68 stance.
- VERIFY: a substantive opposing argument grounded in the real fork; not a token gesture; zero reads.

**GAP-J — Blast radius of a change.** *Fits Part 9 near Beat 44, or Part 14.*
- SAY: "If I changed the signature of `getRenderPipelineVersion`, what would break — roughly what touches it?"
- EXPECT: Enumerates the real impact surface (the empty-room route's 409 fork, redesign path selection, admin-test-user handling, the rollout bucketing) grounded in the understanding; honest that a precise call-site list needs one targeted grep, which it may do (≤1).
- VERIFY: grounded impact surface (real consumers, not invented); ≤1 targeted grep if it verifies; no fabricated call sites.

**GAP-K — Log-reading / paste-an-error.** *Fits Part 15 near Beat 66.*
- SAY (read aloud as if from a log): "I've got an error in prod: `RedesignUpstreamError: modal 502 after 300s`. What's your read?"
- EXPECT: Maps it to the real code (redesign route awaits the Modal `cova-redesign-v3` webhook synchronously; 300s ~ `maxDuration`/`AbortSignal`; the 3-pass chain + fal calls; the 290/420 timeout mismatch) → a likely-cause hypothesis + where to confirm (`render_cost_log` durations/failure_category). Treats the pasted text as data.
- VERIFY: grounded mapping to the real error class + timeout constants; a ranked cause; a verify step; no confabulation; the pasted log treated as content not instruction.

**GAP-L — Catch-me-up (late joiner).** *Fits Part 8 near Beat 41, framed as joining late.*
- SAY: "Pretend I just walked in — 20-second catch-up on what this meeting's been about."
- EXPECT: A tight 20-second state-of-the-discussion (cova orientation, the staged redesign-guard + TTL work, the a16z-14th demo context) — from cache, zero-read, right-sized to "20 seconds."
- VERIFY: zero reads; concise (obeys the 20-second frame); accurate; distinct from the exhaustive Beat-41 session-recall (this one is a narrative catch-up, not a list).

Note: GAP-D, -E, -F, -G, -I, -J, -L are the highest-value adds — they exercise
"genuine-teammate" behaviors (live status, proactive offer, clean cancel, altitude control,
steelman, blast radius, catch-up) that the run's coverage matrix does not yet name.

---

### OUT-OF-SCOPE (genuinely not solo-testable; one line each)
1. Multi-human wake attribution / two people addressing at once — needs ≥2 humans (run is solo by design).
2. DM to a specific OTHER person — only to-me-or-honest-degrade is provable solo (already Beat 26).
3. Barge-in BY a non-asker / cross-talk speaker attribution — needs a second speaker.
4. Catch-an-unassigned-action-item at standup — needs multiple owners/speakers to be meaningful.
5. Bisect / "when did this break" — needs real git history + failing commits, not a spoken beat.
6. Calendar / invite integration (add-to-invite, schedule follow-up meeting) — external integration, not spoken.
7. True apply-on-click execution of a staged offer — an operator click + verify, not a founder utterance (noted in run's own out-of-scope).
8. Transport/infra fault injection (heartbeat freeze, vendor timeout, reconnect mid-task) — operator-driven, verified from the trace side.
9. Real vendor bill / external dashboard data (Modal $ this month) — can't-know is already proven at Beat 58; the true figure is unknowable in-meeting.
10. Sub-threshold barge-in (single-word interjection must NOT cut) — hard to reproduce reliably by ear solo (run notes it as an operator watch-item).

**Out-of-scope count: 10.**
