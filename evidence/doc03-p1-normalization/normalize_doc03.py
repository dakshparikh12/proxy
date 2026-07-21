#!/usr/bin/env python3
"""One-time P1 remediation: normalize doc03 staged criteria to the canonical §8.4 verification
ladder. The ladder is DEFINED as derived from dependency_class + golden_path (FORMAT-TEMPLATE:
"do not free-choose"), so recomputing it is canonicalization, not fabrication. dependency_class
itself (the real judgment) is preserved from the author for the 190 already-laddered criteria and
taken from the 3 fresh-context classmaps for the 101 un-laddered ones, with 4 grounded
reclassifications and the -N/-NEG2 naming fixes. Reuses ladder_augment's official derivation so the
result matches the canonical tool exactly.
"""
import json, pathlib, sys
import yaml

ORCH = pathlib.Path("/Users/daksh/Desktop/proxy/orchestrator")
sys.path.insert(0, str(ORCH))
from ladder_augment import _ladder, _mock_boundary  # official §8.4 derivation

BASE = pathlib.Path("/Users/daksh/Desktop/proxy/staging/doc03/acceptance/doc03")
SP = pathlib.Path("/private/tmp/claude-501/-Users-daksh-Desktop-proxy/fe5a0099-6879-406a-9cb2-91327fba0a65/scratchpad")
CLASSMAP = json.load(open(SP / "merged_classmap.json"))

# Grounded reclassifications of already-laddered criteria whose assertion does NOT genuinely
# exercise a real external dependency (verified by reading each behavior block):
RECLASSIFY_NULL = {
    "AC-SCRIBE-07",  # non-blocking concurrency property (consumer doesn't wait on refresh)
    "AC-SCRIBE-08",  # request-construction: request.model == env value, no real vendor response
    "AC-STORE-04",   # static source scan: "no ORM import exists" — touches no DB
    "AC-XCUT-01",    # no-fabrication property over notes output, verifiable on fixtures
}
# Naming fixes so the gate's `-NEG` convention recognizes existing negatives:
RENAMES = {
    "AC-SCHEMA-16N": "AC-SCHEMA-16-NEG", "AC-SCHEMA-18N": "AC-SCHEMA-18-NEG",
    "AC-SCHEMA-19N": "AC-SCHEMA-19-NEG", "AC-SCHEMA-20N": "AC-SCHEMA-20-NEG",
    "AC-SCHEMA-21N": "AC-SCHEMA-21-NEG", "AC-SCHEMA-22N": "AC-SCHEMA-22-NEG",
    "AC-CLOSE-06-NEG2": "AC-CLOSE-06B-NEG",  # 2nd negative variant -> valid lone -NEG
}


def _norm_dc(v):
    if isinstance(v, list):            # some CLOSE criteria carry a multi-system list; take primary
        v = v[0] if v else None
    if v in (None, "", "null", "~", "none", "None"):
        return None
    return str(v)


def _dc_for(cid, existing):
    if cid in CLASSMAP:
        return _norm_dc(CLASSMAP[cid]["dependency_class"])
    if cid in RECLASSIFY_NULL:
        return None
    return _norm_dc(existing)


def _golden_for(cid, existing):
    if cid in CLASSMAP:
        return bool(CLASSMAP[cid].get("golden_path", False))
    return bool(existing)


def _set_ladder(c):
    cid = c["criterion_id"]
    is_neg = cid.endswith("-NEG")
    dc = _dc_for(cid, c.get("dependency_class"))
    golden = _golden_for(cid, c.get("golden_path"))
    c["dependency_class"] = dc
    c["golden_path"] = golden
    c["verification_ladder"] = [{"tier": t} for t in _ladder(dc, golden, is_neg)]
    if dc:
        c["mock_boundary"] = _mock_boundary(dc, is_neg)
    else:
        c.pop("mock_boundary", None)
    return dc, is_neg


def _make_neg(pos):
    dc = pos["dependency_class"]
    return {
        "criterion_id": f"{pos['criterion_id']}-NEG",
        "name": f"{pos.get('name','(criterion)')} — degrades honestly when {dc} errors/times out/returns garbage",
        "evidence_class": pos.get("evidence_class", "[unit]"),
        "authority_refs": list(pos.get("authority_refs", [])),
        "criticality": "P1",
        "blocking": True,
        "golden_path": False,
        "dependency_class": dc,
        "mock_boundary": _mock_boundary(dc, negative=True),
        "verification_ladder": [{"tier": t} for t in _ladder(dc, False, negative=True)],
        "behavior": {
            "given": f"The dependency {dc} is exercised via the real seam",
            "when": "It errors, times out, or returns malformed/garbage data",
            "then": ["The system degrades honestly (surfaces the failure; no silent proceed; no corruption)"],
        },
        "primary_oracle": {"type": "fault_injection",
                           "artifact": f"inject a real {dc} fault at the seam; assert honest degradation, no corruption"},
    }


# The two P0 requirements with zero covering criteria (coverage-gate gaps) — authored here.
NEW_CRITERIA = [
    {
        "criterion_id": "AC-SCHEMA-25",
        "name": "Claim model enforces its full field contract (kind, caps, defaults, referents<=8)",
        "evidence_class": "[unit]",
        "authority_refs": ["R-doc03-SCHEMA-05"],
        "criticality": "P0",
        "blocking": True,
        "golden_path": False,
        "dependency_class": None,
        "behavior": {
            "given": "the Claim pydantic model",
            "when": "instances are validated at construction",
            "then": [
                'kind is fixed to "claim" (any other value rejected)',
                "text longer than 1000 characters is rejected",
                "verified defaults to False when omitted",
                "a referents list with more than 8 elements is rejected",
                "said_at_s accepts a float (meeting-relative seconds), and firmness/provenance are required",
            ],
        },
        "primary_oracle": {"type": "unit_assertion",
                           "artifact": "assert Claim(kind='x') raises; assert text>1000 raises; assert Claim(...).verified is False; assert 9-element referents raises"},
        "verification_ladder": [{"tier": "lint"}, {"tier": "unit"}],
    },
    {
        "criterion_id": "AC-SCHEMA-26",
        "name": "CloseOp model enforces its field contract (op fixed, target_id, resolution<=300)",
        "evidence_class": "[unit]",
        "authority_refs": ["R-doc03-SCHEMA-16"],
        "criticality": "P0",
        "blocking": True,
        "golden_path": False,
        "dependency_class": None,
        "behavior": {
            "given": "the CloseOp pydantic model",
            "when": "instances are validated at construction",
            "then": [
                'op is fixed to "close" (any other value rejected)',
                "target_id is required (references an existing entry id)",
                "resolution longer than 300 characters is rejected",
            ],
        },
        "primary_oracle": {"type": "unit_assertion",
                           "artifact": "assert CloseOp(op='x') raises; assert resolution>300 raises; assert CloseOp(op='close', target_id='e1', resolution='ok') validates"},
        "verification_ladder": [{"tier": "lint"}, {"tier": "unit"}],
    },
]


def section(cid):
    import re
    m = re.match(r"AC-([A-Z]+)", cid)
    return m.group(1) if m else "?"


def main():
    crits = yaml.safe_load((BASE / "criteria" / "criteria.yaml").read_text())
    crits = [c for c in crits if isinstance(c, dict) and "criterion_id" in c]
    # 1. apply renames
    for c in crits:
        if c["criterion_id"] in RENAMES:
            c["criterion_id"] = RENAMES[c["criterion_id"]]
    # 2. insert the 2 new criteria after AC-SCHEMA-04 (keep them in the SCHEMA neighborhood)
    out = []
    for c in crits:
        out.append(c)
        if c["criterion_id"] == "AC-SCHEMA-04":
            out.extend(NEW_CRITERIA)
    if not any(c["criterion_id"] == "AC-SCHEMA-25" for c in out):
        out.extend(NEW_CRITERIA)  # fallback: append
    crits = out
    # 3. normalize ladder on every criterion
    for c in crits:
        _set_ladder(c)
    # 4. generate any missing -NEG pair for a non-null positive
    ids = {c["criterion_id"] for c in crits}
    final = []
    for c in crits:
        final.append(c)
        cid = c["criterion_id"]
        dc = c["dependency_class"]
        if dc and not cid.endswith("-NEG") and f"{cid}-NEG" not in ids:
            neg = _make_neg(c)
            final.append(neg)
            ids.add(neg["criterion_id"])
    crits = final
    # 5. emit YAML with section-divider comments
    chunks = ["# doc03 criteria — normalized to canonical §8.4 verification ladders "
              "(dependency_class preserved/classmapped; ladders recomputed).\n"]
    cur = None
    for c in crits:
        s = section(c["criterion_id"])
        if s != cur:
            chunks.append(f"\n# ── {s} ──────────────────────────────────────────────\n")
            cur = s
        chunks.append(yaml.safe_dump([c], sort_keys=False, allow_unicode=True, width=100))
    text = "".join(chunks)
    # validate it re-parses AND every criterion_id is unique (no collisions)
    reparsed = yaml.safe_load(text)
    assert isinstance(reparsed, list) and len(reparsed) == len(crits), "reparse mismatch"
    all_ids = [c["criterion_id"] for c in reparsed]
    dupes = {i for i in all_ids if all_ids.count(i) > 1}
    assert not dupes, f"DUPLICATE criterion_ids: {sorted(dupes)}"
    (BASE / "criteria" / "criteria.yaml").write_text(text)

    # 6. regenerate dependency_manifest.yaml from the final criteria
    from collections import defaultdict
    by_class = defaultdict(lambda: {"criteria": [], "golden": []})
    for c in crits:
        dc = c["dependency_class"]
        if dc:
            by_class[dc]["criteria"].append(c["criterion_id"])
            if c.get("golden_path"):
                by_class[dc]["golden"].append(c["criterion_id"])
    man = ["# dependency_manifest.yaml — regenerated from normalized criteria (GENERATOR.md §8.4.3)",
           "doc: doc03", "dependencies:"]
    SEAM = "transport.external.call_external"
    for dc in sorted(by_class):
        kind = "vendor" if dc.startswith("vendor:") else "local"
        info = by_class[dc]
        man.append(f"  - dependency_class: {dc}")
        man.append(f"    kind: {kind}")
        man.append(f"    seam: {SEAM if kind == 'vendor' else 'libs/db|libs/fs'}")
        man.append(f"    mock_boundary: >-")
        man.append(f"      {_mock_boundary(dc, negative=False)}")
        if kind == "vendor":
            man.append(f"    cassette: tests/cassettes/{dc.split(':',1)[1]}_*.yaml")
        man.append("    criteria:")
        man += [f"      - {x}" for x in info["criteria"]]
        man.append("    golden_path_criteria:")
        man += ([f"      - {x}" for x in info["golden"]] or ["      []"])
    (BASE / "dependency_manifest.yaml").write_text("\n".join(man) + "\n")

    # report
    n_neg = sum(1 for c in crits if c["criterion_id"].endswith("-NEG"))
    n_nonnull = sum(1 for c in crits if c["dependency_class"])
    print(f"criteria: {len(crits)}  non-null: {n_nonnull}  -NEG: {n_neg}  classes: {sorted(by_class)}")


if __name__ == "__main__":
    main()
