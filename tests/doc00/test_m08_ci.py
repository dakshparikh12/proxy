"""Doc 00 · §10 CI/CD — GitHub Actions + Cloud Build, guarded (AC-CI-001..007).

Milestone m08. Every test maps to exactly one blocking criterion (id in the
docstring). This module imports ONLY stdlib + pytest + ``_support`` at the top
level, so it COLLECTS clean and FAILS red before the product's guard workflows
and ``.pre-commit-config.yaml`` exist.

Oracle strategy per PROTO-DETERMINISTIC-01: static TEXT scans over the product's
committed CI surface -- every ``.github/workflows/*`` file concatenated, plus
``.pre-commit-config.yaml``. Hermetic: no GitHub Actions / Cloud Build / docker
binary is invoked; the guard *jobs* and their required-check / schedule wiring
are asserted directly from committed YAML.

NOTE: a pre-existing harness workflow (``.github/workflows/verify.yml``) already
lives under ``.github/workflows`` -- it is NOT the product's CI. These oracles
assert the product's named guard jobs (``check-migration-order``,
``check-secret-bindings``, ``check-sdk-isolation-triad``, banned-strings,
field-contract) and its Cloud Build pipeline, which do not exist yet -> red now.
"""

import re
import tempfile

import pytest

import _support as S


# ---------------------------------------------------------------------------
# Shared scan helpers: the product's committed CI surface as text.
#   * ``_ci_text()``   -- every file under .github/workflows/* concatenated.
#   * ``_precommit()`` -- .pre-commit-config.yaml (or "" when absent).
# Each per-test asserts the relevant text is non-empty for the *product* guard
# first, so the criterion goes red before the product's CI is committed.
# ---------------------------------------------------------------------------
def _ci_text() -> str:
    return S.read_all_text("*", root_parts=(".github", "workflows"))


def _precommit() -> str:
    return S.read_text(".pre-commit-config.yaml") or ""


# The named guards that AC-CI-005 requires in BOTH pre-commit AND CI.
_PARITY_GUARDS = (
    "check-migration-order",
    "check-secret-bindings",
    "check-sdk-isolation-triad",
    "banned-strings",
    "field-contract",
)


def _has_guard(text: str, guard: str) -> bool:
    """A guard token, matched literally or with a normalized separator."""
    variants = {guard, guard.replace("-", "_"), guard.replace("-", " ")}
    return any(v in text for v in variants)


# ── AC-CI-001 ─────────────────────────────────────────────────────────────
@pytest.mark.deployment
def test_ci_001_fast_checks_are_required_merge_blocking():
    """AC-CI-001: ruff + mypy + unit + a fast security/contract suite are configured as required (merge-blocking) checks."""
    ci = _ci_text()
    assert ci.strip(), "no .github/workflows CI found (product CI not built)"

    # The fast-check GitHub Actions job(s) must configure all four required checks.
    required = {
        "ruff": r"\bruff\b",
        "mypy": r"\bmypy\b",
        "unit": r"\bunit\b|pytest",
        "security/contract": r"security|contract",
    }
    missing = [name for name, rx in required.items() if not re.search(rx, ci, re.I)]
    # thresholds.missing_fast_checks == 0
    assert not missing, f"fast checks must all be configured/required; missing: {missing}"

    # These fast checks must be merge-BLOCKING (required), never advisory. A
    # required check must NOT opt out of failure (continue-on-error: true is a
    # non-blocking escape hatch).
    blocking = re.search(r"required|branch.?protection|needs:\s*\[?.*(ruff|mypy|unit)", ci, re.I)
    fast_block = re.search(r"\b(ci|fast|checks?|verify|lint|test)\b", ci, re.I)
    assert blocking or fast_block, "fast checks must be wired as required/merge-blocking gates"
    assert not re.search(
        r"(ruff|mypy|unit|security|contract).{0,120}continue-on-error:\s*true",
        ci, re.I | re.S,
    ), "fast checks must be merge-blocking; a required check must not set continue-on-error: true"


# ── AC-CI-002 ─────────────────────────────────────────────────────────────
@pytest.mark.contract
def test_ci_002_check_migration_order_fail_loud_never_skip():
    """AC-CI-002: check-migration-order fails loud on Alembic multiple-heads vs main and cannot be skipped."""
    ci = _ci_text()
    assert ci.strip(), "no .github/workflows CI found (product CI not built)"

    # The named guard job must be present in CI.
    assert _has_guard(ci, "check-migration-order"), (
        "check-migration-order guard job must be present in CI"
    )
    # It exists to catch Alembic multiple heads relative to main.
    assert re.search(r"heads?", ci, re.I) and re.search(r"\bmain\b", ci), (
        "check-migration-order must assert Alembic heads vs main (multiple-heads gate)"
    )
    assert re.search(r"alembic|migration", ci, re.I), (
        "check-migration-order must operate over Alembic migrations"
    )

    # fail-loud-never-skip: thresholds.skip_path_present == 0. The migration
    # guard step must NOT carry a conditional-pass / skip escape (if: ... skip,
    # continue-on-error: true) that would let multiple heads pass silently.
    seg = re.search(r"check-migration-order.*?(?=\n\S|\Z)", ci, re.S)
    block = seg.group(0) if seg else ci
    assert not re.search(r"continue-on-error:\s*true", block, re.I), (
        "check-migration-order must be fail-loud (no continue-on-error: true)"
    )
    assert not re.search(r"\|\|\s*true|:\s*skip\b|allow[_-]?failure", block, re.I), (
        "check-migration-order must never skip / conditionally-pass (fail-loud-never-skip)"
    )


# ── AC-CI-003 ─────────────────────────────────────────────────────────────
@pytest.mark.contract
def test_ci_003_check_sdk_isolation_triad_on_every_query_site():
    """AC-CI-003: check-sdk-isolation-triad fails when a query() site omits the triad flags or the SDK_LOCAL_TOOLS block-list."""
    ci = _ci_text()
    assert ci.strip(), "no .github/workflows CI found (product CI not built)"

    # The named guard job must be present in CI.
    assert _has_guard(ci, "check-sdk-isolation-triad"), (
        "check-sdk-isolation-triad guard job must be present in CI"
    )

    # It asserts the SDK-isolation triad flags AND the SDK_LOCAL_TOOLS block-list
    # on EVERY query() call site -- an automated check, not a manual re-audit.
    assert re.search(r"query\s*\(|query\(\)|query call", ci, re.I), (
        "check-sdk-isolation-triad must scan query() call sites"
    )
    assert re.search(r"triad|isolation", ci, re.I), (
        "check-sdk-isolation-triad must assert the SDK-isolation triad flags"
    )
    assert "SDK_LOCAL_TOOLS" in ci, (
        "check-sdk-isolation-triad must assert the SDK_LOCAL_TOOLS block-list is present"
    )
    # Every-site coverage, not a manual re-audit: thresholds.untriaded_query_site_accepted == 0.
    assert re.search(r"every|all\b|each\b", ci, re.I), (
        "check-sdk-isolation-triad must cover EVERY query() call site (not a manual re-audit)"
    )


# ── AC-CI-004 ─────────────────────────────────────────────────────────────
@pytest.mark.deployment
def test_ci_004_cloud_build_pipeline_shape_with_separate_migrations():
    """AC-CI-004: Cloud Build runs build -> Artifact Registry -> deploy (dev on trigger; prod via promote) with a separate migrations step."""
    # Scan the whole deploy/CI surface: Cloud Build config lives under deploy/*.
    ci = _ci_text()
    cb = S.read_all_text("*", root_parts=("deploy",)) + "\n" + ci
    assert cb.strip(), "no Cloud Build / deploy config found (product CI not built)"

    # Pipeline stages: build -> Artifact Registry -> deploy. thresholds.missing_pipeline_stage == 0.
    stages = {
        "build": r"\bbuild\b|docker\s+build|gcr\.io/cloud-builders/docker",
        "artifact registry": r"artifact.?registry|-docker\.pkg\.dev|pkg\.dev",
        "deploy": r"\bdeploy\b|gcloud run deploy",
    }
    missing = [name for name, rx in stages.items() if not re.search(rx, cb, re.I)]
    assert not missing, f"Cloud Build pipeline missing stages: {missing}"

    # dev on trigger; prod via promote.
    assert re.search(r"trigger", cb, re.I), "dev must auto-deploy on a Cloud Build trigger"
    assert re.search(r"promote", cb, re.I), "prod must ship via a promote job (not direct push)"

    # Migrations run as a SEPARATE step, distinct from build/deploy.
    assert re.search(r"alembic|migrat", cb, re.I), (
        "Cloud Build must run migrations (alembic upgrade)"
    )
    assert re.search(r"(id|name):\s*[\"']?[\w-]*migrat", cb, re.I) or re.search(
        r"migrat\w*[-_ ]?step|step[-_ ]?migrat", cb, re.I
    ), "migrations must run as a SEPARATE Cloud Build step (own id/step)"


# ── AC-CI-005 ─────────────────────────────────────────────────────────────
@pytest.mark.static
def test_ci_005_every_guard_in_both_precommit_and_ci():
    """AC-CI-005: each named guard appears in BOTH pre-commit AND CI; no guard is CI-only or pre-commit-only."""
    ci = _ci_text()
    pc = _precommit()
    assert ci.strip(), "no .github/workflows CI found (product CI not built)"
    assert pc.strip(), "no .pre-commit-config.yaml found (product pre-commit not built)"

    # thresholds.guard_only_in_one_place == 0: parity across the named guard set.
    ci_only = [g for g in _PARITY_GUARDS if _has_guard(ci, g) and not _has_guard(pc, g)]
    pc_only = [g for g in _PARITY_GUARDS if _has_guard(pc, g) and not _has_guard(ci, g)]
    missing_both = [g for g in _PARITY_GUARDS if not _has_guard(ci, g) and not _has_guard(pc, g)]

    assert not missing_both, f"guards absent from BOTH pre-commit and CI: {missing_both}"
    assert not ci_only, f"guards present in CI but missing from pre-commit (CI-only): {ci_only}"
    assert not pc_only, f"guards present in pre-commit but missing from CI (pre-commit-only): {pc_only}"

    # Skipped guards must NOT be adopted anywhere (check-websocket-scopes; the AST-ban ruff rule).
    both = ci + "\n" + pc
    assert "check-websocket-scopes" not in both and "check_websocket_scopes" not in both, (
        "check-websocket-scopes must NOT be adopted (Python type-checks; skipped)"
    )
    assert not re.search(r"ast[-_ ]?ban|ban[-_ ]?ast|flake8-forbid-ast|ast-grep-ban", both, re.I), (
        "the AST-ban ruff rule must NOT be adopted at V0 (a code-review convention suffices)"
    )


# ── AC-CI-006 ─────────────────────────────────────────────────────────────
@pytest.mark.static
def test_ci_006_fast_blocks_e2e_nightly_never_gates_on_flake():
    """AC-CI-006: only the fast unit+security suite is merge-blocking; heavy E2E runs nightly via Cloud Scheduler and is not a merge gate."""
    ci = _ci_text()
    assert ci.strip(), "no .github/workflows CI found (product CI not built)"

    # The fast unit+security suite is what blocks merges.
    assert re.search(r"\bunit\b|pytest", ci, re.I) and re.search(r"security", ci, re.I), (
        "the fast unit+security suite must be the merge-blocking gate"
    )

    # A heavy E2E suite exists and runs nightly via Cloud Scheduler / a schedule,
    # NOT on push/pull_request. thresholds.e2e_gates_merge == 0.
    assert re.search(r"e2e|end.?to.?end", ci, re.I), "a heavy E2E suite must exist"
    assert re.search(r"schedule|cron|cloud.?scheduler|nightly", ci, re.I), (
        "heavy E2E must run nightly (schedule / cron / Cloud Scheduler)"
    )

    # The E2E job must not be a merge gate: not triggered on pull_request and not
    # a required check (isolate the e2e job/workflow region and prove it is
    # schedule-driven, not PR-driven).
    e2e_seg = re.search(r"(e2e|end.?to.?end).*?(?=\n(?:name:|jobs:|# ===)|\Z)", ci, re.I | re.S)
    e2e_block = e2e_seg.group(0) if e2e_seg else ci
    assert re.search(r"schedule|cron|nightly", e2e_block, re.I), (
        "the E2E job must be schedule-driven (nightly), not a merge gate"
    )
    # Never gates on flake: the E2E job must not be listed as a required/needs gate for merges.
    assert not re.search(r"required:\s*true.*e2e|e2e.*required:\s*true", ci, re.I | re.S), (
        "heavy E2E must never gate merges (never gates on flake)"
    )


# ── AC-CI-007 ─────────────────────────────────────────────────────────────
@pytest.mark.contract
def test_ci_007_banned_strings_fails_on_dead_token_and_excludes_gce_per_meeting():
    """AC-CI-007: the banned-strings CI test fails on dead tokens and the banned set does NOT include 'GCE-per-meeting' (A-007 revived it)."""
    ci = _ci_text()
    pc = _precommit()
    surface = ci + "\n" + pc
    assert ci.strip(), "no .github/workflows CI found (product CI not built)"

    # The banned-strings guard must exist in the CI surface.
    assert _has_guard(surface, "banned-strings"), (
        "banned-strings CI test must be present"
    )

    # It bans the superseded dead tokens -- at least one canonical dead token
    # must appear in the banned set. thresholds.dead_token_accepted == 0.
    dead_tokens = ("session_transcripts", "ManagedResource", "warm pool", "TILE_ADDRESS")
    present_dead = [t for t in dead_tokens if t in surface]
    assert present_dead, (
        "banned-strings must enumerate the superseded dead tokens "
        f"(one of {dead_tokens}) so a reintroduced token fails the build"
    )

    # A-007 (resolved): 'GCE-per-meeting' / 'GCE per meeting' was REVIVED for
    # meeting_runtime (AC-HOST-005), so it must NOT be in the banned set --
    # banning it would collide with the A1 deploy definition.
    # thresholds.gce_per_meeting_in_banned_set == 0.
    assert not re.search(r"gce[-_ ]per[-_ ]meeting", surface, re.I), (
        "'GCE-per-meeting'/'GCE per meeting' must be ABSENT from the banned set "
        "(A-007 revived GCE-MIG-one-process-per-meeting for meeting_runtime)"
    )


# ── AC-CI-008 ─────────────────────────────────────────────────────────────
@pytest.mark.contract
def test_ci_008_named_guard_modules_exist_and_actually_gate():
    """AC-CI-008: the named guard MODULES exist, import, run clean on this tree, AND
    fail on an injected violation — an EXECUTED check, not a YAML text-scan. This is the
    gate a phantom `python -m services.ops.check_*` invocation (wrong module path / no
    implementation) cannot pass.
    """
    from pathlib import Path

    # (1) Every guard module imports and runs clean (0) on the committed tree.
    from ops import (  # noqa: F401 — import IS the phantom-module check
        check_banned_strings,
        check_field_contract,
        check_sdk_isolation_triad,
        check_secret_bindings,
    )

    assert check_field_contract.main([]) == 0, "contracts registry must be closed on this tree"
    assert check_banned_strings.main([]) == 0, "no banned token may appear in product code"
    assert check_sdk_isolation_triad.main([]) == 0, "every query() site must carry the triad"
    assert check_secret_bindings.main([]) == 0

    # (2) The scanners are real gates: an injected violation must be caught, not passed.
    tmp = Path(tempfile.mkdtemp())
    svc = tmp / "services" / "x"
    svc.mkdir(parents=True)
    (svc / "bad.py").write_text('SQL = "SELECT * FROM session_transcripts WHERE 1=1"\n')
    assert check_banned_strings.scan(tmp), "banned-strings must flag a real dead-token usage"

    svc2 = tmp / "services" / "y"
    svc2.mkdir(parents=True)
    # A bare query() call site with NO triad markers in the module -> must be flagged.
    (svc2 / "call.py").write_text("def go(prompt):\n    return query(prompt)\n")
    offenders = check_sdk_isolation_triad.query_sites_missing_triad(tmp)
    assert offenders, "check-sdk-isolation-triad must flag a query() site missing the triad"
