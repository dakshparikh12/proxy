#!/usr/bin/env python3
"""The verification-ladder runner (Task 3) — one runner that climbs every criterion's ladder.

For a doc it: (1) runs the MECHANICAL tiers (lint/unit/integration) for ALL criteria in PARALLEL
— pure subprocess, ZERO agent, zero token cost (Task 6.2 discipline is structural here: only the
cassette tiers may ever call a critic); (2) runs the reality/negative tiers with a FRESH-context
critic per criterion and zero shared context (delegated to ladder_critics.run_critic, Task 4);
(3) runs the e2e tier for golden-path criteria only, replaying vcrpy cassettes (--record-mode=none).

State is tracked per (criterion, tier) and persisted to evidence/<doc>-ladder.json. A re-run skips
rungs already green and re-runs ONLY the specific failed/pending (criterion, tier) — it never
re-climbs a criterion's ladder from the bottom. A doc is `ladder-complete` only when every criterion's
full ladder (as its dependency_class defines) is green, top to bottom, no rung skipped.

This REPLACES the ad-hoc P7 (refute) + P7.5 (sweep) verification in orchestrate.py with one tiered
system — there are not two parallel verifiers. The extraction-count gate (Task 5) supplies the
completeness half that P7.5 used to cover.

Exit codes:  0 = ladder-complete (all required rungs green)
             1 = a rung is RED  (genuine defect — halt)
             2 = no red, but rungs are pending-cassette (honest incomplete — awaiting founder cassettes)

Usage:
  python3 orchestrator/ladder_runner.py <doc>                     # climb the whole ladder
  python3 orchestrator/ladder_runner.py <doc> --tier unit         # re-run only one tier
  python3 orchestrator/ladder_runner.py <doc> --json              # machine-readable report only
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ladder_schema_gate import parse_criteria, parse_manifest  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = os.environ.get("PROXY_PY", str(ROOT / ".venv" / "bin" / "python"))
if not pathlib.Path(PY).exists():
    PY = sys.executable

MECHANICAL_TIERS = ("lint", "unit", "integration")   # NEVER spawn an agent (Task 6.2)
CASSETTE_TIERS = ("reality", "negative", "e2e")       # cassette-backed; may call a critic

# Status vocabulary
GREEN, RED, PENDING, NOT_RUN = "green", "red", "pending-cassette", "not-run"

TOOL_TIMEOUT = 60 * 15


def _run(cmd: list[str], *, timeout: int = TOOL_TIMEOUT) -> subprocess.CompletedProcess:
    """Bounded, process-group-isolated local subprocess (no orphan leak on timeout). Mirrors
    orchestrate.run_tool so the runner is safe to use standalone or under the conductor."""
    p = subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, start_new_session=True)
    try:
        out, _ = p.communicate(timeout=timeout)
        return subprocess.CompletedProcess(cmd, p.returncode, out or "", "")
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(p.pid), 15)
            time.sleep(1)
            os.killpg(os.getpgid(p.pid), 9)
        except Exception:
            pass
        try:
            out, _ = p.communicate(timeout=5)
        except Exception:
            out = ""
        return subprocess.CompletedProcess(cmd, 124, (out or "") + f"\nTIMEOUT after {timeout//60}m", "")


# ── tier selectors (pytest -m expressions over tests/<tests_doc>) ─────────────────────────────────
def _pytest(tests_doc: str, mark_expr: str, *extra: str) -> list[str]:
    td = f"tests/{tests_doc}"
    return [PY, "-m", "pytest", td, "-m", mark_expr, "-q", "--no-header", *extra]


def _mechanical_cmd(doc: str, tier: str) -> tuple[list[str], list[list[str]]]:
    """Return (pytest_selection, extra_static_tool_cmds) for a mechanical tier."""
    if tier == "lint":
        # static-marked tests (grep-over-source seam checks) + the doc-agnostic static tool gates.
        surfaces = [d for d in ("services", "libs", "src") if (ROOT / d).is_dir()]
        tools = [
            [PY, "-m", "ruff", "check", *surfaces],
            [PY, "-m", "mypy", "--strict", *surfaces],
        ]
        return _pytest(doc, "static or static_"), tools
    if tier == "unit":
        return _pytest(doc, "not static and not static_ and not integration "
                            "and not reality and not negative and not e2e"), []
    if tier == "integration":
        return _pytest(doc, "integration"), []
    raise ValueError(tier)


def _run_mechanical_tier(doc: str, tier: str) -> tuple[str, str]:
    """Run one mechanical tier; return (status, log_tail). PURE subprocess — no agent, ever."""
    sel, tools = _mechanical_cmd(doc, tier)
    logs: list[str] = []
    # static tools first (fail fast, cheap)
    for tc in tools:
        r = _run(tc)
        logs.append(f"$ {' '.join(tc[1:5])}…\n{r.stdout[-400:]}")
        if r.returncode not in (0,):
            return RED, "\n".join(logs)
    r = _run(sel)
    logs.append(f"$ pytest {sel[3]} -m {sel[5]!r}\n{r.stdout[-600:]}")
    # pytest rc 5 == "no tests collected for this selection" -> tier is vacuously satisfied
    if r.returncode == 5:
        return GREEN, "\n".join(logs) + "\n(no tests for this tier selection — vacuously green)"
    return (GREEN if r.returncode == 0 else RED), "\n".join(logs)


def _run_cassette_tier(doc: str, tier: str, criterion: dict, manifest: dict,
                       critic) -> tuple[str, str]:
    """Run one cassette-backed tier for ONE criterion. If the required cassette is absent, report
    pending-cassette (honest — NOT green, NOT a defect, NOT silently skipped). If present, replay it
    and, for reality/negative, additionally run the fresh-context reason-first critic (Task 4)."""
    dc = criterion["dependency_class"]
    cassettes = _cassettes_for(dc, manifest)
    have_cassette = any(c.exists() for c in cassettes) if cassettes else False
    if not have_cassette:
        return PENDING, (f"{tier}: dependency_class={dc} — no cassette present "
                         f"({', '.join(str(c.relative_to(ROOT)) for c in cassettes) or 'none declared'}); "
                         f"machinery ready, awaiting founder recording (tests/cassettes/RECORDING.md)")
    # cassette exists → replay the tier test selecting this criterion, then critique.
    sel = _pytest(doc, tier, "--record-mode=none", "-k", criterion["id"].replace("-NEG", ""))
    r = _run(sel)
    if r.returncode not in (0, 5):
        return RED, f"{tier} cassette replay FAILED for {criterion['id']}:\n{r.stdout[-600:]}"
    if tier in ("reality", "negative") and critic is not None:
        verdict = critic(doc, criterion, tier, have_cassette=True)
        if not verdict.get("passed"):
            return RED, f"{tier} critic REFUTED {criterion['id']}: {verdict.get('reason','')[:400]}"
    return GREEN, f"{tier}: cassette replayed + critic clean for {criterion['id']}"


def _cassettes_for(dependency_class: str, manifest: dict) -> list[pathlib.Path]:
    """Resolve the cassette glob a dependency_class declares in the manifest to concrete paths."""
    info = manifest.get(dependency_class)
    glob = (info or {}).get("cassette", "")
    if not glob:
        return []
    base = ROOT
    return list(base.glob(glob)) if glob else []


# ── required-tier derivation (mirror ladder_schema_gate.expected_ladder, read from the bundle) ────
def _required_tiers(criterion: dict) -> list[str]:
    """The tiers this criterion must pass — taken from the bundle's own verification_ladder (the
    schema gate already proved it matches the §8.4 table), preserving ladder order."""
    order = ["lint", "unit", "integration", "reality", "negative", "e2e"]
    have = criterion["ladder"]
    return [t for t in order if t in have]


def _state_path(doc: str) -> pathlib.Path:
    return ROOT / "evidence" / f"{doc}-ladder.json"


def _load_state(doc: str) -> dict:
    p = _state_path(doc)
    if p.is_file():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    return {}


def _save_state(doc: str, state: dict) -> None:
    p = _state_path(doc)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=1))


def run_ladder(doc: str, *, only_tier: str | None = None, critic=None,
               force: bool = False, base: pathlib.Path | None = None,
               tests_doc: str | None = None) -> dict:
    """Climb the ladder for `doc`. Returns the full per-criterion/per-tier report dict.

    `base` overrides the bundle location (default acceptance/<doc>); `tests_doc` overrides the
    pytest tree (default <doc>) — both let a staged bundle be verified against a chosen test tree."""
    base = base or (ROOT / "acceptance" / doc)
    tests_doc = tests_doc or doc
    crits = parse_criteria(base / "criteria" / "criteria.yaml")
    manifest = parse_manifest(base / "dependency_manifest.yaml")
    prior = {} if force else _load_state(doc)
    prior_tiers = prior.get("tiers", {})  # {criterion_id: {tier: status}}

    report: dict = {"doc": doc, "criteria": len(crits), "tiers": {}, "logs": {},
                    "no_ladder": [c["id"] for c in crits if not c["ladder"]]}

    def _tier_all_green(tier: str) -> bool:
        rel = [c for c in crits if tier in c["ladder"]]
        return bool(rel) and all(prior_tiers.get(c["id"], {}).get(tier) == GREEN for c in rel)

    # 1) MECHANICAL tiers — computed ONCE per doc (doc-wide selections), fanned out in PARALLEL.
    # Skip a mechanical tier whose criteria are ALL already green (never re-climb a passed rung),
    # unless --force or it is the explicitly targeted --tier.
    mech_needed = [t for t in MECHANICAL_TIERS
                   if any(t in c["ladder"] for c in crits)
                   and (only_tier in (None, t))
                   and (force or only_tier == t or not _tier_all_green(t))]
    mech_result: dict[str, tuple[str, str]] = {}
    if mech_needed:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(mech_needed)) as ex:
            futs = {ex.submit(_run_mechanical_tier, tests_doc, t): t for t in mech_needed}
            for fut in concurrent.futures.as_completed(futs):
                mech_result[futs[fut]] = fut.result()

    # 2+3) per-criterion tier statuses
    for c in crits:
        cid = c["id"]
        req = _required_tiers(c)
        prev = prior_tiers.get(cid, {})
        tiers: dict[str, str] = {}
        for t in req:
            prev_status = prev.get(t)
            # targeted re-run (--tier X): only (re)compute X; every other rung keeps its prior status.
            if only_tier and t != only_tier:
                tiers[t] = prev_status or NOT_RUN
                continue
            # full re-run: never re-climb a rung already green.
            if not force and only_tier is None and prev_status == GREEN:
                tiers[t] = GREEN
                continue
            if t in MECHANICAL_TIERS:
                status, log = mech_result.get(t, (prev_status or NOT_RUN, ""))
                tiers[t] = status
                if log:
                    report["logs"].setdefault(t, log)  # one shared log per mechanical tier
            else:
                status, log = _run_cassette_tier(tests_doc, t, c, manifest, critic)
                tiers[t] = status
                report["logs"][f"{cid}:{t}"] = log
        report["tiers"][cid] = tiers

    # roll-up
    all_status = [s for tt in report["tiers"].values() for s in tt.values()]
    report["summary"] = {
        "green": all_status.count(GREEN),
        "red": all_status.count(RED),
        "pending_cassette": all_status.count(PENDING),
        "not_run": all_status.count(NOT_RUN),
        "total_rungs": len(all_status),
    }
    report["ladder_complete"] = (
        report["summary"]["red"] == 0
        and report["summary"]["pending_cassette"] == 0
        and report["summary"]["not_run"] == 0
        and not report["no_ladder"]            # a criterion with no ladder = bundle not ladder-ready
    )
    report["recorded_at"] = time.strftime("%F %T")
    _save_state(doc, report)
    return report


def _print_report(report: dict) -> None:
    s = report["summary"]
    print(f"=== LADDER RUN — {report['doc']} ===")
    print(f"criteria: {report['criteria']}  |  rungs: {s['total_rungs']}  "
          f"green={s['green']} red={s['red']} pending-cassette={s['pending_cassette']} not-run={s['not_run']}")
    # show reds first (defects), then a compact pending summary
    reds = [(cid, t) for cid, tt in report["tiers"].items() for t, st in tt.items() if st == RED]
    if reds:
        print(f"\nRED rungs ({len(reds)}) — genuine defects, halt:")
        for cid, t in reds[:100]:
            log = report["logs"].get(f"{cid}:{t}") or report["logs"].get(t, "")
            print(f"  {cid} · {t}\n      {log.strip()[:300]}")
    pend = [(cid, t) for cid, tt in report["tiers"].items() for t, st in tt.items() if st == PENDING]
    if pend:
        by_tier: dict[str, int] = {}
        for _cid, t in pend:
            by_tier[t] = by_tier.get(t, 0) + 1
        print(f"\nPENDING-CASSETTE rungs ({len(pend)}): "
              + ", ".join(f"{t}×{n}" for t, n in sorted(by_tier.items()))
              + "  — machinery ready; awaiting founder-recorded cassettes")
    if report.get("no_ladder"):
        print(f"\nNO-LADDER criteria ({len(report['no_ladder'])}): bundle not yet ladder-augmented "
              f"(run the ladder schema gate / Task-8 augmentation first) — e.g. "
              f"{', '.join(report['no_ladder'][:4])}…")
    verdict = "LADDER-COMPLETE" if report["ladder_complete"] else (
        "HALT (red defect)" if s["red"] else
        "NOT LADDER-READY (criteria missing ladders)" if report.get("no_ladder") else
        "INCOMPLETE (pending cassettes)")
    print(f"\n{verdict}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("doc")
    ap.add_argument("--tier", choices=[*MECHANICAL_TIERS, *CASSETTE_TIERS], default=None,
                    help="re-run ONLY this tier (keeps already-green rungs)")
    ap.add_argument("--force", action="store_true", help="ignore prior state; re-run every rung")
    ap.add_argument("--json", action="store_true", help="print machine-readable report only")
    ap.add_argument("--base", default=None, help="override bundle dir (default acceptance/<doc>)")
    ap.add_argument("--tests-doc", default=None, help="override pytest tree tests/<X> (default <doc>)")
    args = ap.parse_args()

    report = run_ladder(args.doc, only_tier=args.tier, force=args.force,
                        base=pathlib.Path(args.base) if args.base else None,
                        tests_doc=args.tests_doc, critic=_default_critic())
    if args.json:
        print(json.dumps(report, indent=1))
    else:
        _print_report(report)
    if report["summary"]["red"]:
        sys.exit(1)
    if report["summary"]["pending_cassette"] or report["summary"]["not_run"]:
        sys.exit(2)
    sys.exit(0)


def _default_critic():
    """The fresh-context reason-first critic (Task 4). Imported lazily so the mechanical-tier path
    never even loads the agent machinery. Returns None if unavailable (cassette tiers then rely on
    the cassette-replay result alone)."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from ladder_critics import run_critic  # noqa: PLC0415
        return run_critic
    except Exception:
        return None


if __name__ == "__main__":
    main()
