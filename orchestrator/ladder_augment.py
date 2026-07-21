#!/usr/bin/env python3
"""Deterministic ladder augmenter (Task 8) — applies the §8.4 verification ladder to an existing
criteria.yaml, given a per-criterion JUDGMENT MAP.

Law-4 split (dynamic, never hard-coded): the situation->dependency_class mapping is MODEL JUDGMENT
and arrives as a small JSON map {criterion_id: {dependency_class, golden_path}} produced by a
fresh-context agent that read the spec. This script owns only the MECHANICS (physics/pipes): given
that map it injects dependency_class / mock_boundary / golden_path / verification_ladder into each
criterion block, generates the paired AC-<id>-NEG criterion for every non-null class, and emits
dependency_manifest.yaml. No dependency_class is invented here — an id absent from the map defaults
to null (no ladder rungs beyond lint/unit), which the schema gate will then flag if the map was
incomplete. That keeps the judgment honest and auditable.

Usage:
  python3 orchestrator/ladder_augment.py <criteria.yaml> <classmap.json> <out_dir>
Writes <out_dir>/criteria/criteria.yaml (augmented) + <out_dir>/dependency_manifest.yaml.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

VENDOR_PREFIX = "vendor:"
SEAM = "transport.external.call_external"


def _mock_boundary(dc: str, negative: bool) -> str:
    if dc.startswith(VENDOR_PREFIX):
        if negative:
            return ("negative cassette only (recorded 5xx / timeout / truncated body) at the "
                    "call_external layer; MUST NOT replace the seam — the real error path must execute")
        return ("may mock the vendor HTTP response body via a vcrpy cassette at the call_external/"
                "transport layer; MUST NOT replace the call_external seam, the request construction, "
                "or the client object with Mock()")
    # local real dep (db:/fs:/gcs:/...)
    kind = dc.split(":", 1)[0]
    if negative:
        return f"real {kind} fault injection (refused connection / missing target); MUST NOT stub the {kind} seam"
    return f"real {kind} only; no in-memory substitute for the integration tier"


def _ladder(dc: str | None, golden: bool, negative: bool) -> list[str]:
    tiers = ["lint", "unit"]
    if dc:
        if negative:
            tiers.append("negative")
        elif dc.startswith(VENDOR_PREFIX):
            tiers.append("reality")
        else:
            tiers.append("integration")
    if golden and not negative:
        tiers.append("e2e")
    return tiers


def _ladder_yaml(tiers: list[str], indent: str = "  ") -> str:
    lines = [f"{indent}verification_ladder:"]
    lines += [f"{indent}  - {{tier: {t}}}" for t in tiers]
    return "\n".join(lines)


def _name_of(block: str) -> str:
    m = re.search(r"^\s*name:\s*(.+)$", block, re.M)
    return m.group(1).strip().strip("'\"") if m else "(criterion)"


def _authority_yaml(block: str) -> str:
    """Return the authority_refs lines of a block verbatim (inline or multi-line), for the NEG pair."""
    m = re.search(r"^(\s*authority_refs:.*(?:\n\s*-\s+\S.*)*)", block, re.M)
    return m.group(1).rstrip() if m else "  authority_refs: []"


def augment(criteria_path: pathlib.Path, classmap: dict, out_dir: pathlib.Path) -> dict:
    text = criteria_path.read_text()
    # Split into a preamble (comments before first criterion) + per-criterion blocks.
    parts = re.split(r"(?m)(?=^-\s*criterion_id:)", text)
    preamble = parts[0] if parts and not parts[0].lstrip().startswith("- criterion_id:") else ""
    blocks = [p for p in parts if p.lstrip().startswith("- criterion_id:")]

    out_blocks: list[str] = []
    manifest: dict[str, dict] = {}
    stats = {"augmented": 0, "non_null": 0, "neg_added": 0, "golden": 0, "unmapped": 0}

    for blk in blocks:
        cid = re.match(r"\s*-\s*criterion_id:\s*(\S+)", blk).group(1).strip().strip("'\"")
        entry = classmap.get(cid, {})
        dc = entry.get("dependency_class")
        if dc in ("", "null", None):
            dc = None
        golden = bool(entry.get("golden_path", False))
        if cid not in classmap:
            stats["unmapped"] += 1

        # Build the ladder fields block (2-space indent to match list-item body).
        fields = [f"  dependency_class: {dc if dc else 'null'}"]
        if dc:
            fields.append(f"  mock_boundary: >-\n    {_mock_boundary(dc, negative=False)}")
        fields.append(f"  golden_path: {'true' if golden else 'false'}")
        fields.append(_ladder_yaml(_ladder(dc, golden, negative=False)))
        # Append the ladder fields to the end of the existing block (before trailing blank lines).
        aug = blk.rstrip("\n") + "\n" + "\n".join(fields) + "\n"
        out_blocks.append(aug)
        stats["augmented"] += 1
        if golden:
            stats["golden"] += 1

        if dc:
            stats["non_null"] += 1
            manifest.setdefault(dc, {"kind": "vendor" if dc.startswith(VENDOR_PREFIX) else "local",
                                     "criteria": [], "golden": []})
            manifest[dc]["criteria"].append(cid)
            if golden:
                manifest[dc]["golden"].append(cid)
            # Generate the paired negative criterion (§8.4.1).
            neg_id = f"{cid}-NEG"
            manifest[dc]["criteria"].append(neg_id)
            neg = (
                f"- criterion_id: {neg_id}\n"
                f"  name: '{_name_of(blk)} — degrades honestly when {dc} errors/times out/returns garbage'\n"
                f"{_authority_yaml(blk)}\n"
                f"  criticality: P1\n"
                f"  blocking: true\n"
                f"  behavior:\n"
                f"    given: The dependency {dc} is exercised via the real seam\n"
                f"    when: It errors, times out, or returns malformed/garbage data\n"
                f"    then:\n"
                f"    - The system degrades honestly (surfaces the failure, no silent proceed, no corruption)\n"
                f"  dependency_class: {dc}\n"
                f"  mock_boundary: >-\n    {_mock_boundary(dc, negative=True)}\n"
                f"  golden_path: false\n"
                f"{_ladder_yaml(_ladder(dc, False, negative=True))}\n"
            )
            out_blocks.append(neg)
            stats["neg_added"] += 1

    # Write augmented criteria.yaml
    crit_out = out_dir / "criteria" / "criteria.yaml"
    crit_out.parent.mkdir(parents=True, exist_ok=True)
    crit_out.write_text(preamble + "".join(out_blocks))

    # Write dependency_manifest.yaml
    man_lines = [f"# dependency_manifest.yaml — generated by ladder_augment.py (GENERATOR.md §8.4.3)",
                 f"doc: {out_dir.name}", "dependencies:"]
    for dc in sorted(manifest):
        info = manifest[dc]
        man_lines.append(f"  - dependency_class: {dc}")
        man_lines.append(f"    kind: {info['kind']}")
        man_lines.append(f"    seam: {SEAM if info['kind'] == 'vendor' else 'libs/db|libs/fs'}")
        man_lines.append(f"    mock_boundary: >-")
        man_lines.append(f"      {_mock_boundary(dc, negative=False)}")
        if info["kind"] == "vendor":
            man_lines.append(f"    cassette: tests/cassettes/{dc.split(':',1)[1]}_*.yaml")
        man_lines.append(f"    criteria:")
        man_lines += [f"      - {c}" for c in info["criteria"]]
        man_lines.append(f"    golden_path_criteria:")
        man_lines += [f"      - {c}" for c in info["golden"]] or ["      []"]
    (out_dir / "dependency_manifest.yaml").write_text("\n".join(man_lines) + "\n")
    return stats


def main() -> None:
    if len(sys.argv) < 4:
        print("usage: ladder_augment.py <criteria.yaml> <classmap.json> <out_dir>")
        sys.exit(2)
    stats = augment(pathlib.Path(sys.argv[1]), json.loads(pathlib.Path(sys.argv[2]).read_text()),
                    pathlib.Path(sys.argv[3]))
    print(f"augmented {stats['augmented']} criteria "
          f"({stats['non_null']} non-null class, {stats['neg_added']} -NEG pairs added, "
          f"{stats['golden']} golden-path, {stats['unmapped']} unmapped->null)")


if __name__ == "__main__":
    main()
