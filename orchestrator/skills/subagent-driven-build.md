---
name: subagent-driven-build
description: Phase-6 execution discipline — build one milestone by decomposing it into independent sub-tasks, each built + self-verified by a fresh subagent, then integrated. Pattern borrowed from Superpowers (subagent-driven-development); authored here, no plugin dependency.
---
# Subagent-driven build (one milestone)

You are the lead for one milestone of doc <DOC>, working against the SEALED acceptance bundle (read-only). Do NOT write a big blob and hope. Decompose and verify each piece.

## Procedure
1. **Read the sealed criteria for this milestone** (`acceptance/<DOC>/criteria/`) and the failing `verify.sh` output. The next criterion in milestone order (test-filename order) is your target — exactly one.
2. **Decompose** the increment into the smallest independent sub-tasks (e.g. "the `Cloner.clone` path shape", "the blobless-threshold branch", "the git-interceptor recording"). Independent = no shared mutable state, buildable in isolation.
3. **For each sub-task, dispatch a fresh subagent** with: the one criterion, the specific contract it must satisfy (from the test the subagent will make pass), and the rule *make the pre-authored test pass — you may not edit tests, fixtures, or anything under acceptance/*. Parallelize only genuinely independent sub-tasks.
4. **Integrate** the sub-results into `services/*`/`libs/*` per AGENTS.md's layout. Never `src/proxy/` (legacy). Every model call via `libs/llm`.
5. **Verify before claiming anything:** run `bash harness/verify.sh`. Exit 0 is the only green. If red, read the failure, fix root cause (do not weaken the test), re-run.
6. **Commit** the increment with a message stating exactly which criterion it satisfies and the evidence (the test that now passes). The commit message is a claim; the diff + verify.sh are the evidence.

## Hard rules
- One criterion per pass. If no test covers the next spec requirement → `SPEC_BLOCKED` in PROGRESS.md, stop the pass. Never guess, never route around, never weaken.
- A subagent that reports "done" without a passing test proved nothing — re-verify yourself.
- If a sub-task is impossible without changing the arbiter, that's a spec bug (`SPEC_BLOCKED`), not license to edit the arbiter.
