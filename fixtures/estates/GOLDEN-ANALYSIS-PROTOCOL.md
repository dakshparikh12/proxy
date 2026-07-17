# Golden-Analysis Protocol — deriving ground truth from a public repo

*Home: `fixtures/estates/`. This is the runnable procedure for turning a cloned repo into graded golden cases. It is written as a prompt+checklist so an agent session (Claude Code for mechanical steps; a cross-family session for judgment steps) executes it verbatim. One run per estate; re-run on SHA bump.*

## Phase 0 — Pin and inventory (mechanical; any agent)

1. Clone at depth 1; record `git rev-parse HEAD` as `pinned_sha`. Everything downstream embeds it.
2. Inventory: file count, LOC by language, src layout, test layout, docs layout, config/dynamic-loading markers (`importlib`, `__import__`, entry_points, plugin registries). Write to `estates/<name>/inventory.json`.
3. Classify the repo against the estate's declared role (ESTATES.md). If the inventory contradicts the role (e.g., "docs-heavy" estate has thin docs at this SHA), STOP and flag — don't golden a repo that no longer stresses what it was picked for.

## Phase 1 — Mechanical goldens (Claude Code may run; tool decides, model only invokes)

For each target in the target list (below): run `tools/derive_goldens.py <repo> <src> <target>` → `estates/<name>/golden/<target>.json`.

**Target selection rule (deterministic, no taste):** rank modules by reverse-import in-degree (the tool emits this); take the top 5 + 2 random mid-degree + 1 leaf. High-degree targets test breadth handling; the leaf tests the "small honest answer" case.

Per-golden checks before commit:
- `direct_importers` nonempty for non-leaf targets (empty on a hub = derivation bug);
- spot-verify ONE edge by hand per golden (`grep -rn "from <pkg> import"` on the named file) — a 30-second tool-sanity check, not a review;
- `known_limits` field present and accurate for this repo (does it use dynamic loading? say so).

## Phase 2 — Judgment goldens (CROSS-FAMILY ONLY — GPT/Gemini session, never Claude)

Only where a criterion needs an opinion. Per case, the session receives: the question, the mechanical golden (as the candidate fact base), and the relevant source slices. It must:
1. Answer the question as a senior engineer would ("which importers are *actually at risk* if X's return type changes" / "which model writes to table T, counting signals and bulk ops");
2. Cite `file:line` for every claim — an uncited claim is discarded;
3. Mark each claim `certain | probable | needs-human`, and every `needs-human` goes to a founder (expect <10%);
4. Output the golden JSON with `_derivation: "cross-family:<model> @ <date>, verified-against mechanical base"`.

**Ban:** a Claude session authoring OR verifying a judgment golden. Claude drafts candidates at most; the cross-family session is the authority. (Proxy runs on Claude; one family must not grade itself. Same maker≠checker rule as the build loop, applied to eval data.)

## Phase 3 — Abstention goldens (estate-messy specialty)

For dynamic-routing targets (config-loaded platforms, string-routed imports): the golden's correct answer is **"not found by this method" + the named boundary**, not the resolved edge set. Author these directly from the Phase-0 dynamic-loading inventory: each `importlib`/registry site with in-repo callers becomes one abstention case. Grading: Proxy MUST NOT emit a confident edge here; a cited abstention scores 1.0, a fabricated edge scores 0 and fails the honesty criterion.

## Phase 4 — Package and seal

1. `estates/<name>/`: `inventory.json`, `golden/*.json`, `scenarios.jsonl` (one line per golden: id, criterion ref, grader, threshold);
2. Holdout estates: hash the golden dir + `pinned_sha` into `acceptance/estates/repositories.lock`; after sealing, edits require a new bundle version;
3. Dev estates: no seal; goldens grow freely (every real failure → a new case, per the living-dataset rule).

## Budget guidance

Per estate: Phase 0+1 ≈ 30–60 min agent time, ~$1–3. Phase 2 ≈ 10–20 cases × a few cents. Founder time ≈ 20–40 min (spot-checks + needs-human items). If any estate exceeds ~2 founder-hours, the target list is too big — cut targets, not rigor.

## Output contract (every golden, no exceptions)

```json
{
  "estate": "...", "pinned_sha": "...", "target": "...",
  "question": "one human-phrased question",
  "answer": { "...": "the graded structure" },
  "grader": "deterministic:<check> | cross-family-judged",
  "_derivation": "mechanical:derive_goldens.py | cross-family:<model> | founder:<name>",
  "known_limits": "what this golden cannot see, stated plainly"
}
```
A golden missing `pinned_sha`, `_derivation`, or `known_limits` is rejected at the RTM/evidence gate.
