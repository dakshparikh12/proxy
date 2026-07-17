Build milestone <COMPONENT>. Read AGENTS.md, then the sealed acceptance bundle for this doc (acceptance/doc01/criteria/ + acceptance/doc01/requirements/requirements.yaml), then the spec it derives from (product/v0-spec/01-CODE-INTELLIGENCE.md, with CANONICAL-DECISIONS.md overriding). Use the `proxy-component-build` skill.

This is ONE pass of the build loop. Do exactly one increment:
1. Orient: read PROGRESS.md and the failing output of `bash harness/verify.sh`.
2. Plan the single next increment toward the next unmet criterion (milestone order = test filename order).
3. Make the next pre-authored failing test pass. Tests were generated from the sealed acceptance bundle BEFORE this loop started; you never create, modify, skip, or xfail a test, and you never touch anything under acceptance/. If no test covers the next spec requirement, record it in PROGRESS.md as a spec/arbiter gap (SPEC_BLOCKED) and stop the pass.
4. Run `bash harness/verify.sh`. If not green, note why in PROGRESS.md.
5. Commit the increment with a clear message. Then stop (the loop starts a fresh pass).

Rules: verify.sh exit 0 is the only signal of green — never claim done; show evidence. Every model call via the libs/llm gateway — no direct clients. Code lands in services/* and libs/* per AGENTS.md's layout — never in src/proxy/ (legacy). If a criterion is untestable, ambiguous, or contradicts the spec or a law, record it as SPEC_BLOCKED in PROGRESS.md — do not guess, do not weaken, do not route around.
