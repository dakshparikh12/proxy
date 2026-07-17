# Estates — the real-repo eval roster (v1)

*Home: `fixtures/estates/` (this doc) + `acceptance/estates/repositories.lock` (pinned SHAs, sealed per bundle). Purpose: the real repos Proxy is graded against, how ground truth is derived for each, and the guardrails that keep AI-derived goldens honest.*

## Selection criteria (why these six)

A repo earns a slot only if it stresses a distinct failure surface AND its ground truth is derivable by one of the three sanctioned methods (§Derivation). Redundant repos are cost, not coverage.

## The roster

| Estate | Repo | Role / failure surface it stresses | Tier | Golden derivation |
|---|---|---|---|---|
| `estate-cova` | Cova backend (private, read-only GitHub App) | Real startup mess: Supabase client writes (the D2 ORM tier-1 target), Next.js/Modal split, env-routed config. Closest shape to a design partner. | **DEV** | founder-authored + mechanical |
| `estate-proxy` | Proxy repo itself | Python uv-monorepo, the exact stack we build in; dogfood loop — Proxy answering questions about Proxy. | **DEV** | founder-authored + mechanical |
| `estate-flask` | `pallets/flask` @ pinned SHA | Small, clean, canonical Python package. The calibration estate: if anything fails here, the failure is ours, not the repo's. Goldens cheap to hand-verify. | **DEV** | mechanical (live, demo goldens exist) |
| `estate-fastapi` | `fastapi/fastapi` @ pinned SHA | Docs-heavy: doc↔code cross-references, huge `docs/`, single-package core. Stresses reference preservation + docs-grounded answers. | **HOLDOUT** | mechanical + docs-derived |
| `estate-django-app` | `netbox-community/netbox` @ pinned SHA (mid-size Django app, strong docs) — *swap for whatever framework the first design partner actually runs; verify stack against the Tusk/Rovr/Helix list before sealing* | The D2 test: Django ORM `who_writes` on a real app (models, migrations, signals), not the framework's own repo. | **HOLDOUT** | mechanical (imports) + cross-family (ORM writes) |
| `estate-messy` | `home-assistant/core` scoped subtree @ pinned SHA | Config-routed indirection (`async_load_platform`-style dynamic loading) that defeats static analysis. Exists to keep honest-abstention non-empty: high Unknown rates here are CORRECT behavior. | **HOLDOUT** | mechanical for the static part; **dynamic-routing cases are abstention goldens** (right answer = "not found by this method") |

Deferred (add on a measured failure, not before): a ≥1M-LOC monorepo estate (scale), a polyglot estate (TS+Python), a deep-history estate. Pilot repos are 10–100× smaller than these stress; goldening them now is over-planning.

## Tier rules (the anti-bias mechanism, kept thin)

- **DEV**: tune freely, debug against, iterate daily. Overfitting to dev is expected and harmless *because dev never gates alone*.
- **HOLDOUT**: goldens authored once, sealed into the acceptance bundle (`repositories.lock` + golden hashes), run only at section gates. **No fix is debugged against a holdout.** A holdout failure → reproduce the failure class on a dev estate → fix there → fresh holdout case replaces the burned one (burned case rotates into dev).
- **The dev↔holdout score gap is a tracked metric.** That number *is* the Cova-bias measurement — report it in every section's evidence folder.

## Derivation — the three sanctioned methods, in order

**1. Mechanical (preferred, unlimited, zero-cost).** Ground truth computed by a toolchain deliberately DISJOINT from Proxy's pipeline. Proxy uses tree-sitter + SCIP/LSP; goldens use Python stdlib `ast`, ripgrep, pydeps-class import walkers. Different implementations → no shared-bug blindness → maker≠checker holds at the eval-data level. `tools/derive_goldens.py` is the first derivation tool (reverse-import blast radius); extend the same pattern for symbol references and config-file readers. **A mechanical golden needs no human review beyond a spot-check of the tool itself.**

**2. Cross-family AI analysis (for judgment calls).** Where a golden requires an opinion — "which of these importers would a senior engineer flag as *at risk*," ORM write-attribution, docs↔code linkage — the analysis session runs on a **non-Anthropic frontier model** (GPT/Gemini), using the protocol in `GOLDEN-ANALYSIS-PROTOCOL.md`. Proxy runs on Claude; Claude-authored judgment goldens would let one family grade itself. Cross-family authoring is the same discipline as our existing cross-family spec audits. Claude Code MAY run the *mechanical* derivations (the tool is deterministic; the model just invokes it) and MAY draft candidate cases that a cross-family session then verifies — draft-and-verify across families is acceptable; author-and-grade within one family is not.

**3. Founder-authored (dev estates only).** Cova and Proxy goldens, where we ARE the senior engineer. Never the bottleneck for holdouts.

## Guardrails (the honest-limits section)

- **Static-analysis goldens inherit static-analysis limits.** `derive_goldens.py` sees `import X`, not `importlib.import_module(name)`. That is by design: dynamic-routing blind spots are what `estate-messy` exists to test, as *abstention* goldens. Never paper over the limit by hand-adding dynamic edges to a mechanical golden — that silently converts it to method 2 without its review discipline.
- **Every golden carries `pinned_sha` + `_derivation`.** A golden that doesn't say how it was made cannot be trusted or regenerated. Re-derive on any SHA bump; a golden diff on re-derivation is itself a signal (repo changed or tool bug).
- **Seal before first gate.** Holdout goldens + repo SHAs hash into the acceptance bundle before the first section gate runs against them (Generator v2 §1.1 sealing applies to estates, not just criteria).
- **License note:** flask (BSD-3), fastapi (MIT), netbox (Apache-2.0), home-assistant (Apache-2.0) — all clone-and-analyze clean. We ingest read-only, store derived artifacts only (zero-copy invariant applies to eval fixtures same as production).

## What this covers vs. what it can't

The estate matrix tests **code intelligence** (Docs 01, workroom read-paths, blast-radius, honesty). It cannot test the meeting surface (S1–S9's live scenarios: barge-in, recycle-survival, notes accuracy, referent resolution). That corpus comes from recorded founder standups about Cova/Proxy — separate work item, weeks of lead time, start recording now. Do not stretch repo estates to cover meeting behavior; they can't.
