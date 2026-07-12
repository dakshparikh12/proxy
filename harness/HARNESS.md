# /harness — the hardened execution substrate (from your Complete Build System doc, UNCHANGED)
These are incident-tested; do not rewrite them. Copy them in verbatim from your build guide:
- `guard.py` — PreToolUse wall: default-deny tool whitelist, protected-path block, gateway enforcement.
- `stop_verify.py` — Stop hook: no stop with uncommitted work.
- `post_edit_test.py` — PostToolUse: quick test pass after edits (NEW — add this small hook: run the fast pytest subset, non-blocking, prints failures).
- `verify.sh` — SOLE code arbiter: ruff/mypy/bandit + milestone-ordered pytest (portable frontier parse).
- `runner.py` — overnight loop: fresh session per pass, stall detection, cost ceilings, bypassPermissions, live output.
- `scripts/bootstrap.sh` — pinned supply chain. `prompts/pass_prompt.md` — the per-pass procedure.
Wiring lives in `.claude/settings.json` (already in this repo). CI (`.github/workflows/verify.yml`) + branch protection incl. admins: unchanged from your doc, plus a nightly eval job for the Tier-2 gate.
