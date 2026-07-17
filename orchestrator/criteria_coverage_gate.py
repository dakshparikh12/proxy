#!/usr/bin/env python3
"""RTM coverage gate — deterministic proof that a sealed acceptance bundle covers its spec.

The completeness guarantee for acceptance criteria: bidirectional requirement<->criterion
traceability. Every requirement must be covered by >=1 criterion; every criterion must trace
to a real requirement; no criterion may float without authority. A gap = the criteria set is
INCOMPLETE, and the gate exits nonzero (fail the seal). This is what turns "did we cover the
whole spec?" from an agent's hope into a decidable check.

Dependency-free (no pyyaml) — parses the bundle's regular machine-generated YAML directly.

Usage: python3 orchestrator/criteria_coverage_gate.py <doc>   e.g. doc01
Exit 0 = fully covered; nonzero = gaps (printed).
"""
import re, sys, pathlib
from collections import defaultdict

ROOT = pathlib.Path(__file__).parent.parent


def parse_requirements(path: pathlib.Path) -> dict[str, str]:
    """requirement_id -> criticality."""
    reqs, cur = {}, None
    for line in path.read_text().splitlines():
        m = re.match(r"\s*-?\s*requirement_id:\s*(\S+)", line)
        if m:
            cur = m.group(1).strip().strip('"')
            reqs[cur] = "?"
            continue
        c = re.match(r"\s*criticality:\s*(\S+)", line)
        if c and cur:
            reqs[cur] = c.group(1).strip()
    return reqs


def parse_criteria(path: pathlib.Path) -> list[dict]:
    """list of {id, refs:[...], criticality, blocking}."""
    crits, cur = [], None
    for line in path.read_text().splitlines():
        m = re.match(r"\s*-?\s*criterion_id:\s*(\S+)", line)
        if m:
            cur = {"id": m.group(1).strip().strip('"'), "refs": [], "criticality": "?", "blocking": None}
            crits.append(cur)
            continue
        if cur is None:
            continue
        r = re.match(r"\s*authority_refs:\s*\[(.*)\]", line)
        if r:
            cur["refs"] = [x.strip().strip('"') for x in r.group(1).split(",") if x.strip()]
        c = re.match(r"\s*criticality:\s*(\S+)", line)
        if c:
            cur["criticality"] = c.group(1).strip()
        b = re.match(r"\s*blocking:\s*(\S+)", line)
        if b:
            cur["blocking"] = b.group(1).strip().lower() == "true"
    return crits


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    doc = args[0] if args else "doc01"
    base = ROOT / "acceptance" / doc
    if "--base" in sys.argv:
        base = pathlib.Path(sys.argv[sys.argv.index("--base") + 1])
    if not (base / "requirements" / "requirements.yaml").exists():
        print(f"GATE FAIL: no requirements at {base}")
        sys.exit(1)
    reqs = parse_requirements(base / "requirements" / "requirements.yaml")
    crits = parse_criteria(base / "criteria" / "criteria.yaml")

    # Build the bidirectional map
    covered = defaultdict(list)          # req_id -> [criterion_ids]
    dangling = []                        # (criterion_id, ref) where ref is not a real requirement
    authorityless = []                   # criterion_ids with no refs
    for c in crits:
        if not c["refs"]:
            authorityless.append(c["id"])
        for ref in c["refs"]:
            if ref in reqs:
                covered[ref].append(c["id"])
            else:
                dangling.append((c["id"], ref))

    uncovered = [r for r in reqs if r not in covered]
    p0_uncovered = [r for r in uncovered if reqs[r] in ("P0", "P1")]

    # Report
    print(f"=== RTM COVERAGE GATE — {doc} ===")
    print(f"requirements: {len(reqs)}  |  criteria: {len(crits)}  |  covered requirements: {len(covered)}/{len(reqs)}")
    by_crit = defaultdict(lambda: [0, 0])
    for r, cr in reqs.items():
        by_crit[cr][1] += 1
        if r in covered:
            by_crit[cr][0] += 1
    for cr in sorted(by_crit):
        cov, tot = by_crit[cr]
        print(f"  {cr}: {cov}/{tot} covered")

    ok = True
    if uncovered:
        ok = False
        print(f"\nUNCOVERED REQUIREMENTS ({len(uncovered)}) — spec behavior with NO criterion:")
        for r in sorted(uncovered):
            print(f"  {r}  [{reqs[r]}]")
    if dangling:
        ok = False
        print(f"\nDANGLING authority_refs ({len(dangling)}) — criterion cites a non-existent requirement:")
        for cid, ref in dangling:
            print(f"  {cid} -> {ref}")
    if authorityless:
        ok = False
        print(f"\nAUTHORITYLESS CRITERIA ({len(authorityless)}) — unapproved scope (no requirement):")
        for cid in authorityless:
            print(f"  {cid}")

    if ok:
        print("\nGATE PASS: every requirement is covered, every criterion traces to a real requirement.")
        sys.exit(0)
    print(f"\nGATE FAIL: {'; '.join(x for x, n in [('uncovered requirements', len(uncovered)), ('dangling refs', len(dangling)), ('authorityless criteria', len(authorityless))] if n)}."
          f"  A sealed bundle MUST NOT ship with coverage gaps.")
    if p0_uncovered:
        print(f"  {len(p0_uncovered)} of the uncovered are P0/P1 (blocking) — highest priority to fix.")
    sys.exit(1)


if __name__ == "__main__":
    main()
