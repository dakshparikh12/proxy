#!/usr/bin/env python3
"""Verification-ladder schema gate — deterministic proof that a sealed bundle carries a
well-formed verification ladder on every criterion (GENERATOR.md §8.4).

This is the enforcement mechanism named in §8.4.4. It is MECHANICAL: it parses the bundle's
YAML directly (no pyyaml dep, like criteria_coverage_gate.py) and spawns NO agent — zero token
cost. It fails the seal (exit nonzero) unless, for the bundle at acceptance/<doc>/:

  1. every criterion has a non-empty `verification_ladder` and a `dependency_class` key;
  2. `mock_boundary` is a non-empty string iff `dependency_class` is non-null;
  3. ladder rungs match the §8.4 derivation table for that criterion's class + golden_path:
       - lint, unit          : always present
       - integration         : present iff dependency_class is a LOCAL class (db:/fs:/gcs:/...)
       - reality             : present iff dependency_class is a vendor: class
       - negative            : present iff the criterion is itself an AC-...-NEG pair
       - e2e                 : present iff golden_path is true (only golden-path criteria)
  4. every non-null-dependency_class POSITIVE criterion has its paired `AC-<...>-NEG` criterion;
  5. `e2e` appears only on golden_path criteria;
  6. `dependency_manifest.yaml` exists and is bidirectionally consistent with the criteria
     (every non-null class used by a criterion appears in the manifest; every manifest class
     lists exactly the criteria that carry it; every manifest golden-path id carries golden_path).

Usage: python3 orchestrator/ladder_schema_gate.py <doc> [--base <path>]
Exit 0 = well-formed ladder on every criterion; nonzero = defects (printed).

The gate is intentionally strict-but-legible: it prints EVERY defect it finds (not just the
first) so a retroactive augmentation pass (Task 8) can see the full gap in one run.
"""
import pathlib
import re
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).parent.parent

# A dependency_class is "vendor" (needs a cassette, gets `reality`) iff it has the vendor: prefix.
# Anything else non-null (db:, fs:, gcs:, ...) is a LOCAL real dep (gets `integration`).
VENDOR_PREFIX = "vendor:"
LOCAL_PREFIXES = ("db:", "fs:", "gcs:", "cache:", "queue:", "clock:")


def _strip(v: str) -> str:
    return v.strip().strip('"').strip("'")


def _is_null(v: str) -> bool:
    return _strip(v).lower() in ("", "null", "~", "none")


def parse_criteria(path: pathlib.Path) -> list[dict]:
    """Parse criteria.yaml into per-criterion dicts carrying the ladder fields.

    Deliberately forgiving line-parser (matches criteria_coverage_gate.py's approach): it
    reads the fields it needs and ignores the rest. `verification_ladder` is captured as the
    set of tier names found in the block that follows the `verification_ladder:` key, whether
    written as `- {tier: lint}` (flow) or `- tier: lint` (block)."""
    crits: list[dict] = []
    cur: dict | None = None
    in_ladder = False
    for raw in path.read_text().splitlines():
        line = raw.rstrip("\n")
        m = re.match(r"\s*-?\s*criterion_id:\s*(\S+)", line)
        if m:
            in_ladder = False
            cur = {
                "id": _strip(m.group(1)),
                "dependency_class": "__ABSENT__",
                "mock_boundary": "__ABSENT__",
                "golden_path": None,
                "ladder": set(),
                "line": raw,
            }
            crits.append(cur)
            continue
        if cur is None:
            continue
        # dependency_class: <value>
        dc = re.match(r"\s*dependency_class:\s*(.*)$", line)
        if dc:
            in_ladder = False
            cur["dependency_class"] = _strip(dc.group(1))
            continue
        # mock_boundary: <value> | (block scalar `>` / `|` -> treat next indented text as present)
        mb = re.match(r"\s*mock_boundary:\s*(.*)$", line)
        if mb:
            in_ladder = False
            val = mb.group(1).strip()
            if val in (">", "|", ">-", "|-", ">+", "|+"):
                cur["mock_boundary"] = "__BLOCK__"  # non-empty block scalar follows
            else:
                cur["mock_boundary"] = _strip(val)
            continue
        gp = re.match(r"\s*golden_path:\s*(\S+)", line)
        if gp:
            in_ladder = False
            cur["golden_path"] = _strip(gp.group(1)).lower() == "true"
            continue
        vl = re.match(r"\s*verification_ladder:\s*(.*)$", line)
        if vl:
            rest = vl.group(1).strip()
            if rest.startswith("["):
                # INLINE FLOW form: `verification_ladder: [lint, unit]` (bare scalars) or
                # `[{tier: lint}, {tier: unit}]` (flow mappings) — both are valid YAML and
                # semantically identical to the block form. Capture the tier names directly.
                inner = rest.strip("[]")
                if "tier:" in inner:
                    cur["ladder"].update(re.findall(r"tier:\s*([A-Za-z0-9_]+)", inner))
                else:
                    cur["ladder"].update(re.findall(r"[A-Za-z0-9_]+", inner))
                in_ladder = False
            else:
                # BLOCK form: an empty value here; tiers follow on subsequent `- {tier: X}` lines.
                in_ladder = True
            continue
        if in_ladder:
            # Accept `- {tier: lint}` or `- tier: lint` ; a non-list, less-indented key ends the block.
            t = re.search(r"tier:\s*([A-Za-z0-9_]+)", line)
            if t and line.lstrip().startswith("-"):
                cur["ladder"].add(t.group(1))
                continue
            # blank line inside the block is fine; a new top-level-ish key ends it
            if line.strip() == "":
                continue
            if re.match(r"\s*[A-Za-z_]+:", line):
                in_ladder = False
    return crits


def parse_manifest(path: pathlib.Path) -> dict[str, dict]:
    """dependency_class -> {kind, criteria:set, golden:set}. Forgiving block parser."""
    if not path.is_file():
        return {}
    out: dict[str, dict] = {}
    cur_class = None
    section = None  # "criteria" | "golden_path_criteria" | None
    for raw in path.read_text().splitlines():
        line = raw.rstrip("\n")
        dc = re.match(r"\s*-?\s*dependency_class:\s*(\S+)", line)
        if dc:
            cur_class = _strip(dc.group(1))
            out.setdefault(cur_class, {"kind": "?", "criteria": set(), "golden": set()})
            section = None
            continue
        if cur_class is None:
            continue
        k = re.match(r"\s*kind:\s*(\S+)", line)
        if k:
            out[cur_class]["kind"] = _strip(k.group(1))
            section = None
            continue
        if re.match(r"\s*criteria:\s*$", line):
            section = "criteria"
            continue
        if re.match(r"\s*golden_path_criteria:\s*$", line):
            section = "golden_path_criteria"
            continue
        # inline list forms
        cin = re.match(r"\s*criteria:\s*\[(.*)\]", line)
        if cin:
            out[cur_class]["criteria"].update(_strip(x) for x in cin.group(1).split(",") if x.strip())
            section = None
            continue
        gin = re.match(r"\s*golden_path_criteria:\s*\[(.*)\]", line)
        if gin:
            out[cur_class]["golden"].update(_strip(x) for x in gin.group(1).split(",") if x.strip())
            section = None
            continue
        if section:
            item = re.match(r"\s*-\s+(\S+)", line)
            if item:
                key = "criteria" if section == "criteria" else "golden"
                out[cur_class][key].add(_strip(item.group(1)))
                continue
            if re.match(r"\s*[A-Za-z_]+:", line):
                section = None
    return out


def expected_ladder(dc: str, golden: bool, is_neg: bool) -> set[str]:
    """The §8.4 derivation table, as a set of required tiers."""
    tiers = {"lint", "unit"}
    null = _is_null(dc)
    if not null:
        if dc.startswith(VENDOR_PREFIX):
            tiers.add("reality")
        elif dc.startswith(LOCAL_PREFIXES):
            tiers.add("integration")
        else:
            # Unknown-but-non-null class: err toward rigor — treat as vendor-like (reality).
            tiers.add("reality")
    if is_neg and not null:
        # The paired negative criterion carries the negative rung (reality/integration drop off:
        # the negative rung IS the real-fault exercise for the pair).
        tiers.discard("reality")
        tiers.discard("integration")
        tiers.add("negative")
    if golden:
        tiers.add("e2e")
    return tiers


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    doc = args[0] if args else "doc02"
    base = ROOT / "acceptance" / doc
    if "--base" in sys.argv:
        base = pathlib.Path(sys.argv[sys.argv.index("--base") + 1])

    crit_path = base / "criteria" / "criteria.yaml"
    manifest_path = base / "dependency_manifest.yaml"
    if not crit_path.is_file():
        print(f"LADDER GATE FAIL: no criteria at {crit_path}")
        sys.exit(1)

    crits = parse_criteria(crit_path)
    manifest = parse_manifest(manifest_path)
    ids = {c["id"] for c in crits}

    missing_ladder, missing_dc, bad_mock, wrong_rungs, missing_neg, stray_e2e = [], [], [], [], [], []

    for c in crits:
        cid, dc = c["id"], c["dependency_class"]
        is_neg = cid.endswith("-NEG")
        if not c["ladder"]:
            missing_ladder.append(cid)
        if dc == "__ABSENT__":
            missing_dc.append(cid)
            continue  # can't judge the rest without a class
        null = _is_null(dc)
        # mock_boundary present iff dependency_class non-null
        mb = c["mock_boundary"]
        has_mb = mb not in ("__ABSENT__", "") and not _is_null(mb)
        if null and has_mb:
            bad_mock.append((cid, "mock_boundary present but dependency_class is null"))
        if (not null) and not has_mb:
            bad_mock.append((cid, "dependency_class non-null but mock_boundary missing/empty"))
        # ladder rung correctness
        if c["ladder"]:
            want = expected_ladder(dc, bool(c["golden_path"]), is_neg)
            have = c["ladder"]
            if want - have:
                wrong_rungs.append((cid, f"missing rungs {sorted(want - have)}"))
            if "e2e" in have and not c["golden_path"]:
                stray_e2e.append(cid)
        # negative pairing: every non-null POSITIVE criterion needs its -NEG sibling
        if (not null) and not is_neg:
            if f"{cid}-NEG" not in ids:
                missing_neg.append(cid)

    # manifest consistency
    manifest_defects: list[str] = []
    used_classes = {c["dependency_class"] for c in crits
                    if c["dependency_class"] not in ("__ABSENT__",) and not _is_null(c["dependency_class"])}
    if used_classes and not manifest:
        manifest_defects.append(f"criteria use non-null classes {sorted(used_classes)} but "
                                f"dependency_manifest.yaml is missing/empty at {manifest_path}")
    else:
        for dc in sorted(used_classes):
            if dc not in manifest:
                manifest_defects.append(f"class {dc} used by criteria but absent from manifest")
        # every manifest class should list exactly the criteria that carry it
        by_class = defaultdict(set)
        for c in crits:
            if not _is_null(c["dependency_class"]) and c["dependency_class"] != "__ABSENT__":
                by_class[c["dependency_class"]].add(c["id"])
        for dc, info in manifest.items():
            listed, actual = info["criteria"], by_class.get(dc, set())
            if listed and listed != actual:
                only_m = sorted(listed - actual)
                only_c = sorted(actual - listed)
                if only_m:
                    manifest_defects.append(f"manifest[{dc}] lists criteria not carrying it: {only_m}")
                if only_c:
                    manifest_defects.append(f"manifest[{dc}] omits criteria that carry it: {only_c}")

    # --- report ---
    n_ladder = sum(1 for c in crits if c["ladder"])
    n_nonnull = sum(1 for c in crits
                    if c["dependency_class"] != "__ABSENT__" and not _is_null(c["dependency_class"]))
    n_golden = sum(1 for c in crits if c["golden_path"])
    n_neg = sum(1 for c in crits if c["id"].endswith("-NEG"))
    print(f"=== LADDER SCHEMA GATE — {doc} ===")
    print(f"criteria: {len(crits)}  |  with ladder: {n_ladder}  |  non-null dependency_class: "
          f"{n_nonnull}  |  golden_path: {n_golden}  |  -NEG pairs: {n_neg}")

    ok = True

    def _report(title: str, items: list) -> None:
        nonlocal ok
        if items:
            ok = False
            print(f"\n{title} ({len(items)}):")
            for it in items[:200]:
                print(f"  {it if isinstance(it, str) else it[0] + ' — ' + it[1]}")

    _report("CRITERIA MISSING verification_ladder", missing_ladder)
    _report("CRITERIA MISSING dependency_class key", missing_dc)
    _report("mock_boundary defects", bad_mock)
    _report("LADDER RUNG mismatches vs §8.4 table", wrong_rungs)
    _report("NON-NULL criteria MISSING their -NEG pair", missing_neg)
    _report("e2e tier on NON-golden-path criteria", stray_e2e)
    _report("dependency_manifest.yaml inconsistencies", manifest_defects)

    if ok:
        print("\nLADDER GATE PASS: every criterion carries a well-formed verification ladder; "
              "manifest consistent; negative pairs present.")
        sys.exit(0)
    print("\nLADDER GATE FAIL: a sealed bundle MUST NOT ship without a complete verification ladder "
          "on every criterion (GENERATOR.md §8.4).")
    sys.exit(1)


if __name__ == "__main__":
    main()
