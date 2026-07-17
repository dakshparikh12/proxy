#!/usr/bin/env python3
"""Autonomous multi-doc conductor. Chains docs 00->09; per doc runs the phase pipeline in
ORCHESTRATION.md, each phase a FRESH Claude Code session (separate authority). The builder is
never the authority that decides done: Phase 7 is an independent fresh-context verifier whose
`VERDICT: DONE` gates advancement — not the builder's claim.

Composes existing pieces UNCHANGED: runner.py (per-doc build loop), criteria/GENERATOR.md
(criteria gen), .claude/agents+skills (fresh-context review/verify), tools/derive_goldens.py.

STATUS: conductor scaffold. It will REFUSE to build a doc whose sealed evidence layer
(tests+fixtures+goldens) is absent — that authoring is a supervised per-doc step (see
ORCHESTRATION Phase 3), and letting the builder author its own arbiter would collapse
maker!=checker. Wire each phase's agent invocation to your Claude Code CLI before a live run;
the phase contracts and gating logic below are the spec.
"""
import argparse, subprocess, sys, pathlib, json

ROOT = pathlib.Path(__file__).parent.parent
DOCS = ["doc00", "doc01", "doc02", "doc03", "doc04", "doc05", "doc08", "doc09"]  # SPINE-REGISTER order


def _fresh_session(prompt: str, read_only: bool = False) -> subprocess.CompletedProcess:
    """One fresh Claude Code session. read_only verifier/review phases get no write tools."""
    mode = "plan" if read_only else "bypassPermissions"   # plan-mode ~ read-only for the verifier
    return subprocess.run(
        ["claude", "-p", prompt, "--permission-mode", mode, "--max-turns", "120"],
        cwd=ROOT, timeout=60 * 60,
    )


def _bundle_sealed(doc: str) -> bool:
    m = ROOT / "acceptance" / doc / "manifest.yaml"
    return m.exists() and "status: SEALED" in m.read_text() \
        and "pending-golden-derivation" not in m.read_text()


def _evidence_present(doc: str) -> bool:
    """Rung-1 gate cannot run without the evidence layer. Never let the builder author it."""
    tests = list((ROOT / "tests").glob(f"test_*{doc[-2:]}*")) or list((ROOT / "tests").glob("test_*"))
    fixtures = (ROOT / "tests" / "fixtures").exists()
    return bool(tests) and fixtures


def build_doc(doc: str) -> str:
    spec = f"product/v0-spec/{doc}"  # resolved to the real filename by the phase prompts
    print(f"\n########## DOC {doc} ##########")

    # PHASE 1-2: generate + adversarially review criteria (skip if already sealed)
    if not _bundle_sealed(doc):
        print(f"[{doc}] PHASE 1 generate criteria (GENERATOR.md A-E)")
        _fresh_session(f"Run criteria/GENERATOR.md Phases A-E on {spec}; emit acceptance/{doc}/.")
        print(f"[{doc}] PHASE 2 adversarial criteria review (separate authority)")
        _fresh_session(f"Use the spec-compliance-review skill to attack acceptance/{doc}/ vs {spec}; "
                       f"report omitted product behaviors, weak/circular oracles, vision not captured.")
        return f"{doc}: criteria drafted — SEAL required (Phase 3-4: author evidence + derive goldens "
        # NOTE: returns here — evidence authoring + seal is the supervised checkpoint.

    # PHASE 3 evidence must exist before the build loop can be graded
    if not _evidence_present(doc):
        return (f"{doc}: SEALED bundle but evidence layer (tests/fixtures/goldens) absent. "
                f"Author it (supervised, ORCHESTRATION Phase 3) — the builder may NOT author its own arbiter.")

    # PHASE 5 plan + review
    print(f"[{doc}] PHASE 5 plan + planner-reviewer")
    _fresh_session(f"Plan the implementation of {spec} against acceptance/{doc}/; then request the "
                   f"planner-reviewer subagent to critique it. Lock the plan to PROGRESS.md.")

    # PHASE 6 build loop until rung-1 green (runner.py owns caps/integrity/stall)
    print(f"[{doc}] PHASE 6 build loop (runner.py)")
    r = subprocess.run([sys.executable, str(ROOT / "runner.py"), "--component", doc],
                       cwd=ROOT)
    if r.returncode != 0:
        return f"{doc}: build loop stopped without rung-1 green (stall/spec-blocked/caps). See runner.log + PROGRESS.md."

    # PHASE 7 independent verification gate — the anti-falsification centerpiece
    print(f"[{doc}] PHASE 7 INDEPENDENT VERIFICATION (fresh context, read-only)")
    vprompt = (ROOT / "orchestrator" / "verify_doc.md").read_text().replace("<DOC>", doc)
    _fresh_session(vprompt, read_only=True)
    verdict = ROOT / "evidence" / doc / "verification-verdict.txt"
    if not (verdict.exists() and "VERDICT: DONE" in verdict.read_text()):
        return (f"{doc}: independent verifier did NOT return DONE. Refutations → feed back to Phase 6. "
                f"(orchestrator loops build<->verify; it does NOT advance on the builder's word.)")

    return f"{doc}: DONE — independently verified against the sealed bundle."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default="doc00")
    ap.add_argument("--only", default=None, help="build a single doc")
    args = ap.parse_args()
    docs = [args.only] if args.only else DOCS[DOCS.index(args.start):]
    for doc in docs:
        result = build_doc(doc)
        print(f"\n==> {result}")
        if not result.endswith("independently verified against the sealed bundle."):
            print("Halting the chain: sequential dependency means later docs cannot start on an "
                  "unfinished foundation. Resolve, then re-run --from this doc.")
            return
    print("\nALL DOCS INDEPENDENTLY VERIFIED. Collect evidence + dual signoff -> merge.")


if __name__ == "__main__":
    main()
