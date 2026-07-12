Build component <COMPONENT>. Read AGENTS.md, then components/<COMPONENT>.md. Use the `proxy-component-build` skill.

This is ONE pass of the build loop. Do exactly one increment:
1. Orient: read PROGRESS.md and the failing output of `bash harness/verify.sh`.
2. Plan the single next increment toward the next unmet [pytest] acceptance criterion.
3. TDD: write/adjust the milestone test first, then make it pass. Do NOT edit existing tests, fixtures, the spec, or harness files.
4. Run `bash harness/verify.sh`. If not green, note why in PROGRESS.md.
5. Commit the increment with a clear message. Then stop (the loop starts a fresh pass).

Rules: verify.sh exit 0 is the only signal of green — never claim done. Every model call via src/proxy/llm.py. Show evidence, not assertions. If a criterion is untestable/ambiguous, record it in PROGRESS.md as a spec bug — do not guess.
