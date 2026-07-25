#!/usr/bin/env python3
"""Merge per-doc node files (build/_nodes/*.json) into build/chain.json, in a configurable
region order (dependency-ordered). Cross-doc wiring/forward-refs are then reconciled by
iterating on REGION_ORDER + node moves until check_completeness.py is green."""
import json
import sys
from pathlib import Path

BUILD = Path(__file__).resolve().parent
NODES = BUILD / "_nodes"

# dependency order: foundation → engines → workroom → orchestrator (consumes workroom.envelope)
# → experience → journey (last wave). Refined against check_completeness output.
REGION_ORDER = ["foundation", "codeintel", "transport", "scribe",
                "workroom", "orchestrator", "experience", "journey"]

EXTERNAL_INPUTS = ["recall.webhook", "github.webhook", "chat.inbound",
                   "human.accept_click", "human.speech", "stt.stream"]
PRODUCT_ENDPOINTS = ["notes.finalized", "draft.accepted", "spoken.answer",
                     "connect.rendered", "meeting_home.rendered",
                     "experience.connect_page", "experience.meeting_home", "experience.tile"]


def main() -> None:
    nodes = []
    missing = []
    for region in REGION_ORDER:
        f = NODES / f"{region}.json"
        if not f.exists():
            missing.append(region); continue
        part = json.loads(f.read_text())
        if not isinstance(part, list):
            print(f"ERROR: {f} is not a JSON array"); sys.exit(1)
        nodes.extend(part)
        print(f"  {region}: {len(part)} nodes")
    if missing:
        print(f"WAITING on: {', '.join(missing)} — not merging yet."); sys.exit(2)

    # ── reconcile depends_on: node-id stays; interface-id → its producer node; drop external/unknown
    node_ids = {n["id"] for n in nodes}
    producer: dict[str, str] = {}
    for n in nodes:
        for iface in n.get("exposes", []):
            producer.setdefault(iface, n["id"])
    for n in nodes:
        deps = []
        for d in n.get("depends_on", []):
            if d in node_ids:
                deps.append(d)
            elif d in producer:
                deps.append(producer[d])            # interface-id → producing node
            # else external_input / endpoint / dangling → not a build-order dep
        n["depends_on"] = sorted(set(deps) - {n["id"]})

    # ── topological sort (Kahn, stable by original index) so depends_on is backward-only
    idx = {n["id"]: i for i, n in enumerate(nodes)}
    indeg = {n["id"]: len(n["depends_on"]) for n in nodes}
    ready = sorted([nid for nid, d in indeg.items() if d == 0], key=lambda x: idx[x])
    order, byid = [], {n["id"]: n for n in nodes}
    dependents: dict[str, list[str]] = {n["id"]: [] for n in nodes}
    for n in nodes:
        for d in n["depends_on"]:
            dependents[d].append(n["id"])
    while ready:
        nid = ready.pop(0)
        order.append(nid)
        for m in dependents[nid]:
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
        ready.sort(key=lambda x: idx[x])
    if len(order) != len(nodes):
        cyc = sorted(set(byid) - set(order))
        print(f"ERROR: dependency CYCLE among {len(cyc)} nodes: {cyc[:12]}"); sys.exit(1)
    nodes = [byid[nid] for nid in order]

    chain = {
        "version": "1",
        "docs": ["00", "01", "02", "03", "04", "05", "08", "09"],
        "external_inputs": EXTERNAL_INPUTS,
        "product_endpoints": PRODUCT_ENDPOINTS,
        "nodes": nodes,
    }
    (BUILD / "chain.json").write_text(json.dumps(chain, indent=2) + "\n")
    print(f"→ build/chain.json written: {len(nodes)} nodes across {len(REGION_ORDER)} regions")


if __name__ == "__main__":
    main()
