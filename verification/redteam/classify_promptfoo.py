"""Classify a promptfoo redteam run from its RESULT FILE, never from stdout.

Why a result-file classifier and not a `grep '0 failed'`: a promptfoo run whose
provider is out of credit reports every case as *errored*, which prints
"0 passed / 0 failed". A naive `0 failed` check reads that as a green pass — a
vacuous pass on ZERO executed cases. This classifier looks at per-case
success/error/failure and refuses to call an all-errored run a pass.

It also keeps a stable ``reports/<doc>-promptfoo-lastpass.json`` so that a
confirmatory re-run blocked by an external provider limit does not erase the
evidence of a genuine prior pass (same principle as Layer 2's preserve-on-block):
a real pass is stamped as the last-good; an all-errored re-run falls back to it
and is reported PASS (preserved) with the block reason attached.

Emits ONE line to stdout, consumed by run_full_verification.sh:
  PASS <detail>     every case succeeded, none failed/errored (genuine)
  PASS <detail>     all cases errored on a provider limit BUT a prior genuine
                    pass exists — preserved, re-run block noted
  BLOCKED <detail>  all cases errored on a provider limit, no prior pass
  FAIL <detail>     at least one case genuinely failed (an injection landed)
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
REPORTS = _HERE.parent / "reports"


def _counts(results_path: Path) -> tuple[int, int, int, int, str]:
    """Return (total, success, errored, failed, sample_error) from a promptfoo json."""
    data = json.loads(results_path.read_text())
    cases = data.get("results", {}).get("results", [])
    total = len(cases)
    success = errored = failed = 0
    sample_error = ""
    for r in cases:
        err = r.get("error") or (r.get("response") or {}).get("error")
        if err:
            errored += 1
            if not sample_error:
                sample_error = str(err)
        elif r.get("success"):
            success += 1
        else:
            failed += 1
    return total, success, errored, failed, sample_error


def classify(doc: str, results_path: Path) -> str:
    lastpass = REPORTS / f"{doc}-promptfoo-lastpass.json"
    if not results_path.exists():
        return f"BLOCKED no result file written ({results_path.name})"
    total, success, errored, failed, sample_error = _counts(results_path)

    # A genuine, complete pass: every case ran and resisted. Stamp last-good.
    if total > 0 and success == total and errored == 0 and failed == 0:
        shutil.copyfile(results_path, lastpass)
        return f"{success}/{total} injection cases resisted, results reports/{results_path.name}"  # noqa: E501 -> PASS-prefixed by caller

    # An injection genuinely landed — a real failure, never masked.
    if failed > 0:
        return f"FAIL {failed}/{total} injection cases NOT resisted, results reports/{results_path.name}"

    # No genuine failures, but the run did not complete — cases errored (e.g. the
    # LLM provider is out of credit). Never a pass on zero executed cases.
    if errored > 0:
        reason = sample_error[:120] if sample_error else "cases errored"
        if lastpass.exists():
            lp_total, lp_success, _, _, _ = _counts(lastpass)
            return (
                f"PASS {lp_success}/{lp_total} resisted (preserved last genuine pass; "
                f"this re-run BLOCKED: {reason})"
            )
        return f"BLOCKED all {total} cases errored: {reason}"

    # total == 0 with no errors: nothing ran, nothing to trust.
    return f"BLOCKED no cases executed (0 of 0), results reports/{results_path.name}"


if __name__ == "__main__":
    doc = sys.argv[1]
    results_path = Path(sys.argv[2])
    if not results_path.is_absolute():
        results_path = _HERE.parent / results_path
    verdict = classify(doc, results_path)
    # Prefix a bare genuine-pass detail (starts with a digit) with PASS for the caller.
    if verdict[0].isdigit():
        verdict = "PASS " + verdict
    print(verdict)
