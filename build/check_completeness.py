#!/usr/bin/env python3
"""Tier-A completeness checker — the deterministic, un-driftable proof that chain.json
IS the whole product. It is arithmetic on the chain + the sealed acceptance bundles, not
an agent's opinion. Exit 0 iff ALL closures hold; fail-closed on anything malformed.

Three closures (SPEC.md §3):
  A1 requirement closure (both ways) — every sealed criterion is cited by >=1 node,
                                        and every node cites >=1 real sealed criterion.
  A2 wiring closure                   — every `consumes` is produced by an EARLIER `exposes`
                                        or a declared external_input; every `exposes` is
                                        consumed by someone or a declared product_endpoint.
  A3 journey closure                  — every enumerated Doc-09 journey maps onto nodes.
Plus order-validity: every `depends_on` points to an EARLIER node (the acyclic guarantee).

Usage:  python3 build/check_completeness.py [path/to/chain.json]
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # repo root (parent of build/)
BUILD = ROOT / "build"


def die(msg: str) -> "NoReturn":                        # fail-closed helper
    print(f"FAIL (fail-closed): {msg}")
    sys.exit(1)


def load_chain(argv: list[str]) -> dict:
    p = Path(argv[1]) if len(argv) > 1 else BUILD / "chain.json"
    if not p.exists():
        die(f"{p} not found — Phase 1 must produce the chain before Tier A can run.")
    try:
        chain = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        die(f"{p} is not valid JSON: {e}")
    if not isinstance(chain.get("nodes"), list) or not chain["nodes"]:
        die("chain.json has no `nodes` list.")
    return chain


def sealed_criteria() -> set[str]:
    """Every AC-* criterion id across all sealed acceptance bundles = the obligation set."""
    ids: set[str] = set()
    for yml in ROOT.glob("acceptance/doc*/criteria/*.yaml"):
        for m in re.finditer(r"^\s*-?\s*criterion_id:\s*(\S+)", yml.read_text(), re.M):
            ids.add(m.group(1).strip().strip("'\""))
    return ids


def enumerated_journeys() -> set[str] | None:
    """Doc-09 journeys, enumerated by Phase 1 into build/journeys.json as {"journeys":[ids]}.
    Absent → we cannot prove journey closure, so A3 fails (never silently passes)."""
    p = BUILD / "journeys.json"
    if not p.exists():
        return None
    try:
        return set(json.loads(p.read_text()).get("journeys", []))
    except json.JSONDecodeError as e:
        die(f"build/journeys.json is not valid JSON: {e}")


def check_order(nodes: list[dict]) -> list[str]:
    seen: set[str] = set()
    bad: list[str] = []
    for n in nodes:
        nid = n.get("id", "?")
        for dep in n.get("depends_on", []):
            if dep not in seen:
                bad.append(f"{nid} depends_on '{dep}' which is not an EARLIER node")
        seen.add(nid)
    return bad


def check_requirements(nodes: list[dict], sealed: set[str]) -> list[str]:
    problems: list[str] = []
    cited: set[str] = set()
    for n in nodes:
        crit = [c for c in n.get("criterion_ids", []) if c in sealed]
        if not crit:
            problems.append(f"node '{n.get('id','?')}' cites no valid sealed criterion "
                            f"(scope-creep / unbuilt-bundle): {n.get('criterion_ids', [])}")
        cited.update(crit)
    uncovered = sealed - cited
    if uncovered:
        show = ", ".join(sorted(uncovered)[:20])
        more = f" (+{len(uncovered) - 20} more)" if len(uncovered) > 20 else ""
        problems.append(f"{len(uncovered)} sealed criteria covered by NO node: {show}{more}")
    return problems


def check_wiring(chain: dict) -> list[str]:
    nodes = chain["nodes"]
    external = set(chain.get("external_inputs", []))
    endpoints = set(chain.get("product_endpoints", []))
    produced_before: set[str] = set(external)
    problems: list[str] = []
    all_consumed: set[str] = set()
    all_produced: set[str] = set()
    for n in nodes:                                     # forward pass: consumes must be ready
        for c in n.get("consumes", []):
            all_consumed.add(c)
            if c not in produced_before:
                problems.append(f"node '{n.get('id','?')}' consumes '{c}' with no earlier "
                                f"producer and no external_input (routes-together gap)")
        for e in n.get("exposes", []):
            all_produced.add(e)
            produced_before.add(e)
    for e in sorted(all_produced):                      # every producer must be used
        if e not in all_consumed and e not in endpoints:
            problems.append(f"interface '{e}' is exposed but consumed by no node and is not a "
                            f"product_endpoint (dead code / missing wiring)")
    return problems


def check_journeys(nodes: list[dict], journeys: set[str] | None) -> list[str]:
    if journeys is None:
        return ["Doc-09 journeys not enumerated — Phase 1 must write build/journeys.json "
                "before journey closure can be proven."]
    covered: set[str] = set()
    for n in nodes:
        covered.update(n.get("journeys_now_live", []))
    uncovered = journeys - covered
    if uncovered:
        return [f"Doc-09 journey '{j}' is realized by no node" for j in sorted(uncovered)]
    return []


def section(name: str, problems: list[str]) -> bool:
    if problems:
        print(f"[FAIL] {name}")
        for p in problems:
            print(f"       - {p}")
        return False
    print(f"[PASS] {name}")
    return True


def main() -> None:
    chain = load_chain(sys.argv)
    nodes = chain["nodes"]
    sealed = sealed_criteria()
    if not sealed:
        die("no sealed criteria found under acceptance/doc*/criteria/ — nothing to close against.")
    journeys = enumerated_journeys()

    print(f"== Tier A: completeness closure ({len(nodes)} nodes, {len(sealed)} sealed criteria) ==")
    ok = True
    ok &= section("order-validity (depends_on backward-only)", check_order(nodes))
    ok &= section("A1 requirement closure (both ways)", check_requirements(nodes, sealed))
    ok &= section("A2 wiring closure (producer/consumer balance)", check_wiring(chain))
    ok &= section("A3 journey closure (Doc-09)", check_journeys(nodes, journeys))
    print("─" * 60)
    if ok:
        print("Tier A: CLOSED — the chain covers the whole product structurally.")
        sys.exit(0)
    print("Tier A: NOT CLOSED — real gap(s) above. Fix chain.json (this is arithmetic).")
    sys.exit(1)


if __name__ == "__main__":
    main()
