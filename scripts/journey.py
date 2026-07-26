#!/usr/bin/env python3
"""journey.py — integration-contract gate (Doc 09 §2).

Proves the cross-doc seams close on the assembled tree.

Usage:
  python3 scripts/journey.py contracts        # run all 5 contract checks
  python3 scripts/journey.py scenario S1      # locate + report the S1 happy-arc test
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Directories that are product/source code (exclude venv, staging, scripts themselves)
_PRODUCT_DIRS = [str(ROOT / d) for d in ("libs", "services", "migrations")]
_TEST_DIRS = [str(ROOT / "tests")]


def _wire_workspace_src() -> None:
    """Add every workspace member's src/ dir to sys.path (mirrors conftest.py logic)."""
    for base in ("libs", "services"):
        base_dir = ROOT / base
        if not base_dir.is_dir():
            continue
        for member in sorted(base_dir.iterdir()):
            src = member / "src"
            if src.is_dir():
                s = str(src)
                if s not in sys.path:
                    sys.path.append(s)


# Wire paths immediately so subsequent imports work without PYTHONPATH.
_wire_workspace_src()


# ---------------------------------------------------------------------------
# Check helpers — each returns a (label, status, detail) tuple.
# status is one of: "PASS", "FAIL", "DEFERRED"
# Never raise outside the try/except — internal errors become FAIL lines.
# ---------------------------------------------------------------------------

CheckResult = tuple[str, str, str]


def _grep(pattern: str, *dirs: str, extra_flags: list[str] | None = None) -> list[str]:
    """Run grep on dirs; return non-pycache matching lines."""
    flags = ["grep", "-rn", "--include=*.py"] + (extra_flags or []) + [pattern] + list(dirs)
    r = subprocess.run(flags, capture_output=True, text=True)
    return [line for line in r.stdout.splitlines() if "__pycache__" not in line]


def check_registry_closed() -> CheckResult:
    """Check 1: the Doc-09 registry gate at the Doc-08 §4.8 strength (A19 / C-REGSCOPE).

    The Doc-09 journey §2 check was set-equality only; per A_decisions A19 (cite 08 §4.8,
    09 §16) it is strengthened to the FULL Doc-08 ``assert_registry_closed`` — which already
    proves set-equality + every inbound type has EXACTLY ONE handler + every outbound type
    has ≥1 projector + no signal-surface leak (§11.8) — AND the per-field produce/consume
    diff (``assert_contract_fields_consumed``, 08 §4.8 / CANONICAL §11.11): a field produced
    by one side and consumed by neither (or consumed under a name no model produces) fails
    the journey gate here, NAMING the orphan. So the cross-doc integration gate is no weaker
    than the Doc-08 build gate — the type graph AND the field graph must both close.
    """
    label = "registry"
    try:
        from contracts.registry import (
            assert_contract_fields_consumed,
            assert_registry_closed,
        )

        # (a) the full type-graph closure: set-equality + handlers + projectors + no leak.
        assert_registry_closed()
        # (b) the §4.8 field-level produce/consume diff (the added Doc-08 strength).
        field_orphans = assert_contract_fields_consumed()
        if field_orphans:
            return (label, "FAIL", f"field-diff orphans (§4.8): {field_orphans}")
        return (
            label,
            "PASS",
            "closed — enum==CHANNEL_REGISTRY, handlers+projectors covered, field-diff clean",
        )
    except AssertionError as exc:
        return (label, "FAIL", f"closed-graph violation: {exc}")
    except Exception as exc:
        return (label, "FAIL", f"import/runtime error: {exc}")


def check_contracts_resolve_to_libs() -> CheckResult:
    """Check 2: no doc re-declares a shared wire type locally.

    Greps libs/ and services/ for class definitions of the canonical shared
    wire types (Bundle, Envelope, AgentChunk, ReadinessReport) outside
    libs/contracts.  scribe/schema.py intentionally defines its own NoteDelta
    (LLM extraction schema — ops list, not the wire op/note_id/body form);
    that is a different type serving a different purpose, so we do not flag it.
    The venv is excluded by searching only product dirs.
    """
    label = "contracts_resolve"
    # These are the shared wire types whose class definition must live only in libs/contracts.
    shared_types = ["Bundle", "Envelope", "AgentChunk", "ReadinessReport"]
    try:
        hits: list[str] = []
        for t in shared_types:
            lines = _grep(f"^class {t}", *_PRODUCT_DIRS)
            for line in lines:
                # Allow any path that contains "contracts" (the authoritative home)
                if "contracts" not in line:
                    hits.append(line.strip())

        if hits:
            return (
                label,
                "FAIL",
                f"shared wire types re-declared outside libs/contracts: {hits}",
            )
        return (
            label,
            "PASS",
            f"grep confirmed: {', '.join(shared_types)} defined only in libs/contracts",
        )
    except Exception as exc:
        return (label, "FAIL", f"grep error: {exc}")


def check_one_operation_runs() -> CheckResult:
    """Check 3: exactly one CREATE TABLE operation_runs; zero meeting_harness.

    Searches only migrations/ and product source dirs (not scripts/, not tests/).
    """
    label = "operation_runs"
    search_dirs = _PRODUCT_DIRS  # migrations + libs + services
    try:
        op_runs_lines = _grep(
            "CREATE TABLE operation_runs",
            *search_dirs,
            extra_flags=["--include=*.sql"],
        )
        harness_lines = _grep(
            "CREATE TABLE meeting_harness",
            *search_dirs,
            extra_flags=["--include=*.sql"],
        )

        if len(op_runs_lines) != 1:
            return (
                label,
                "FAIL",
                f"expected exactly 1 CREATE TABLE operation_runs; found {len(op_runs_lines)}: {op_runs_lines}",
            )
        if harness_lines:
            return (
                label,
                "FAIL",
                f"found forbidden meeting_harness table defs: {harness_lines}",
            )
        short = op_runs_lines[0].split(str(ROOT))[-1].lstrip("/")
        return (
            label,
            "PASS",
            f"exactly 1 operation_runs def ({short}); 0 meeting_harness",
        )
    except Exception as exc:
        return (label, "FAIL", f"grep error: {exc}")


def check_agent_chunk_stream_deltas() -> CheckResult:
    """Check 4: AgentChunk consumers use stream_deltas; no raw TEXT accumulation.

    Confirms stream_deltas is present in the product tree.  Greps for the
    raw-TEXT-accumulation anti-pattern (appending/concatenating chunk.text
    directly outside the delta-izer).  The delta-izer itself (deltas.py) is
    the only legitimate site that reads chunk.text for accumulation.
    """
    label = "stream_deltas"
    try:
        # Confirm stream_deltas exists in product code (non-test)
        sd_lines = [
            line
            for line in _grep("stream_deltas", *_PRODUCT_DIRS)
            if "test" not in line.lower()
        ]
        # Anti-pattern: direct chunk.text concatenation/accumulation outside the deltaizer
        anti_lines = [
            line
            for line in _grep(r"chunk\.text", *_PRODUCT_DIRS)
            if "deltas.py" not in line and "test" not in line.lower()
        ]
        # chunk.text reads in contexts like `chunk.text or ""` inside the deltaizer are fine;
        # only flag sites that accumulate (+=, append, +) outside the single deltaizer
        accumulate_lines = [
            line
            for line in anti_lines
            if "+=" in line or ".append(" in line or (
                "+" in line and "chunk.text" in line and "deltas.py" not in line
            )
        ]

        if not sd_lines:
            return (label, "FAIL", "stream_deltas not found in product dirs — may have been replaced")

        details = (
            f"stream_deltas present at {len(sd_lines)} product site(s); "
            f"accumulation anti-pattern hits: {len(accumulate_lines)}"
        )
        if accumulate_lines:
            return (label, "FAIL", f"{details}; violations: {accumulate_lines[:3]}")
        return (label, "PASS", details)
    except Exception as exc:
        return (label, "FAIL", f"grep error: {exc}")


def check_cost_drafts_persist() -> CheckResult:
    """Check 5: meeting_cost and staged_drafts survive a simulated process kill.

    Locates the existing kill-survival / process-recycle tests in tests/ and
    reports their ids.  Does NOT re-run the heavy tests here (they require a
    live Postgres; CI runs them separately).
    """
    label = "cost_drafts_persist"
    patterns = [
        "test_w06_workroom_task_cost_survives_recycle",
        "test_w07_staged_draft_survives_sandbox_teardown",
        "test_sub_026_recycled_orchestrator_reloads_spent_cost",
        "test_sub_027_staged_drafts_persisted_at_creation",
        "test_sub_028_human_accept_reads_persisted_draft",
    ]
    try:
        found: list[str] = []
        for pat in patterns:
            lines = _grep(pat, *_TEST_DIRS)
            for line in lines:
                path_part = line.split(":")[0]
                p = pathlib.Path(path_part)
                rel = str(p.relative_to(ROOT)) if p.exists() else path_part
                found.append(f"{rel}::{pat}")
                break  # one confirmation per pattern is enough

        if not found:
            return (
                label,
                "DEFERRED",
                "no persistence-survival test found via grep — searched recycle/survive patterns",
            )
        return (
            label,
            "PASS",
            f"persistence-survival tests located (not re-run here — need live Postgres): {'; '.join(found)}",
        )
    except Exception as exc:
        return (label, "FAIL", f"search error: {exc}")


# ---------------------------------------------------------------------------
# Scenario resolution
# ---------------------------------------------------------------------------


def resolve_scenario_s1() -> None:
    """Locate the S1 happy-arc e2e test and report its id + gating env."""
    try:
        # Search for the DOC03_LIVE_E2E gate marker in tests
        gate_lines = _grep("DOC03_LIVE_E2E", *_TEST_DIRS)
        fn_lines = _grep("def test_live_full_pipeline", *_TEST_DIRS)

        if not gate_lines and not fn_lines:
            print("S1: DEFERRED: no e2e/happy-arc test found (searched for DOC03_LIVE_E2E)")
            return

        test_path = "tests/doc03/e2e/test_live_e2e.py::test_live_full_pipeline_real_infra"
        if fn_lines:
            raw = fn_lines[0]
            file_part = raw.split(":")[0]
            p = pathlib.Path(file_part)
            if p.exists():
                rel = p.relative_to(ROOT)
                test_path = f"{rel}::test_live_full_pipeline_real_infra"

        gating = "DOC03_LIVE_E2E=1 + TEST_DATABASE_URL + DOC03_STORE_GCS_BUCKET + ANTHROPIC_API_KEY"
        live_set = os.environ.get("DOC03_LIVE_E2E") == "1"
        status = "LIVE (env set — would run)" if live_set else "GATED (skipped in CI)"
        print(f"S1: test_id={test_path}")
        print(f"S1: gating_env={gating}")
        print(f"S1: status={status}")
        print("S1: note=live vendors NOT run here; resolve-and-report only (per spec)")
    except Exception as exc:
        print(f"S1: FAIL: error resolving scenario: {exc}")


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------

RESULT_ORDER = [
    check_registry_closed,
    check_contracts_resolve_to_libs,
    check_one_operation_runs,
    check_agent_chunk_stream_deltas,
    check_cost_drafts_persist,
]


def run_contracts() -> int:
    """Run all 5 contract checks; return 0 iff all PASS."""
    results: list[CheckResult] = []
    for fn in RESULT_ORDER:
        try:
            label, status, detail = fn()
        except Exception as exc:  # absolute never-crash boundary
            label = fn.__name__
            status = "FAIL"
            detail = f"unexpected internal error: {exc}"
        results.append((label, status, detail))
        print(f"{label}: {status} — {detail}")

    all_pass = all(status == "PASS" for _, status, _ in results)
    return 0 if all_pass else 1


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("usage: journey.py contracts | journey.py scenario S1", file=sys.stderr)
        return 2

    cmd = args[0]
    if cmd == "contracts":
        return run_contracts()
    elif cmd == "scenario":
        if len(args) < 2:
            print("usage: journey.py scenario S1", file=sys.stderr)
            return 2
        scenario = args[1]
        if scenario == "S1":
            resolve_scenario_s1()
            return 0
        else:
            print(f"unknown scenario: {scenario!r}; only S1 is implemented", file=sys.stderr)
            return 2
    else:
        print(f"unknown command: {cmd!r}; expected 'contracts' or 'scenario'", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
