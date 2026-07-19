#!/usr/bin/env python3
"""Merge doc02 part slices into the sealed bundle shape: requirements.yaml + criteria.yaml.

Handles the three shapes the section authors produced:
  - bare YAML list of requirement/criterion blocks
  - a dict with a `requirements:` and/or `criteria:` key (EVENTS, SEAM, CANVAS, FAIL)
Dedupes by id (first occurrence wins), validates authority_refs bidirectionally, writes the bundle.
"""
import glob
import os
import sys

import yaml

PARTS = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(PARTS, "..", "acceptance", "doc02")
REQ_OUT = os.path.join(OUT, "requirements", "requirements.yaml")
CRIT_OUT = os.path.join(OUT, "criteria", "criteria.yaml")

SECTIONS = ["JOIN", "EVENTS", "HEAR", "SPEAK", "CHAT", "CANVAS", "TURN", "FAIL", "SEAM", "XCUT"]


def load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def collect_from(doc, list_key):
    """Return the list of blocks whether doc is a bare list or a dict carrying list_key."""
    if doc is None:
        return []
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict):
        return doc.get(list_key, []) or []
    return []


def part_files(section, kinds):
    out = []
    for k in kinds:
        p = os.path.join(PARTS, f"{section}.{k}")
        if os.path.exists(p):
            out.append(p)
    return out


def main():
    reqs = {}
    crits = {}
    req_order, crit_order = [], []

    for s in SECTIONS:
        # requirements: standalone .requirements.yaml / .reqs.yaml, or embedded in .criteria.yaml
        for p in part_files(s, ["requirements.yaml", "reqs.yaml", "criteria.yaml"]):
            doc = load(p)
            for r in collect_from(doc, "requirements"):
                if not isinstance(r, dict) or "requirement_id" not in r:
                    continue
                rid = r["requirement_id"]
                if rid not in reqs:
                    reqs[rid] = r
                    req_order.append(rid)
        # criteria: only from .criteria.yaml (bare list or dict['criteria'])
        for p in part_files(s, ["criteria.yaml"]):
            doc = load(p)
            for c in collect_from(doc, "criteria"):
                if not isinstance(c, dict) or "criterion_id" not in c:
                    continue
                cid = c["criterion_id"]
                if cid not in crits:
                    crits[cid] = c
                    crit_order.append(cid)

    # Bidirectional RTM validation
    req_ids = set(reqs)
    referenced = set()
    dangling = []
    for cid in crit_order:
        for ref in crits[cid].get("authority_refs", []) or []:
            referenced.add(ref)
            if ref not in req_ids:
                dangling.append((cid, ref))
    orphan_reqs = [rid for rid in req_order if rid not in referenced]

    os.makedirs(os.path.dirname(REQ_OUT), exist_ok=True)
    os.makedirs(os.path.dirname(CRIT_OUT), exist_ok=True)

    req_list = [reqs[r] for r in req_order]
    crit_list = [crits[c] for c in crit_order]

    header_r = ("# Doc 02 (Voice & Transport) — Acceptance Bundle: requirements\n"
                "# Merged from parts/ by the Criteria Author Lead. One atomic EARS requirement per block.\n"
                f"# {len(req_list)} requirements across sections: {', '.join(SECTIONS)}.\n")
    header_c = ("# Doc 02 (Voice & Transport) — Acceptance Bundle: criteria\n"
                "# Merged from parts/ by the Criteria Author Lead. Deterministic-preferred oracles; zeros explicit.\n"
                f"# {len(crit_list)} criteria across sections: {', '.join(SECTIONS)}.\n")

    with open(REQ_OUT, "w") as f:
        f.write(header_r)
        yaml.safe_dump(req_list, f, sort_keys=False, allow_unicode=True, width=1000)
    with open(CRIT_OUT, "w") as f:
        f.write(header_c)
        yaml.safe_dump(crit_list, f, sort_keys=False, allow_unicode=True, width=1000)

    # Per-section counts
    def sec_of(i):
        return i.split("-")[2] if i.count("-") >= 2 else "?"
    rc, cc = {}, {}
    for rid in req_order:
        rc[sec_of(rid)] = rc.get(sec_of(rid), 0) + 1
    for cid in crit_order:
        s = cid.split("-")[1] if cid.count("-") >= 1 else "?"
        cc[s] = cc.get(s, 0) + 1

    print("=== MERGE COMPLETE ===")
    print(f"requirements: {len(req_list)}  criteria: {len(crit_list)}")
    print("req by section:", {k: rc[k] for k in sorted(rc)})
    print("crit by section:", {k: cc[k] for k in sorted(cc)})
    print(f"dangling authority_refs (criterion -> missing req): {len(dangling)}")
    for cid, ref in dangling:
        print(f"   DANGLING: {cid} -> {ref}")
    print(f"orphan requirements (no criterion refs them): {len(orphan_reqs)}")
    for rid in orphan_reqs:
        print(f"   ORPHAN: {rid}")
    print(f"wrote: {os.path.normpath(REQ_OUT)}")
    print(f"wrote: {os.path.normpath(CRIT_OUT)}")
    return 0 if not dangling else 1


if __name__ == "__main__":
    sys.exit(main())
