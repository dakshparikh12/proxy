"""Doc 00 · §14 hard rule — the ``call-external`` AST guard (AC-CON-004, AC-CMP-010).

Milestone hardening node ``foundation.call-external-guard`` (decisions.md D-002).

CLAUDE.md §14: *every external call wrapped with retry + cost telemetry*, enforced
by "the single ``call_external`` seam in ``libs/http`` (no raw client lives anywhere
else)." Until now that single-seam claim was convention-only — nothing *statically*
forbade a raw ``AsyncAnthropic()`` / ``httpx.AsyncClient()`` / ``storage.Client()``
outside ``libs/http``. This oracle binds the new AST guard
(``ops.check_call_external``, mirroring ``ops.check_sdk_isolation_triad``):

  (a) it RUNS clean (exit 0) on the committed tree — every raw client already lives
      in ``libs/http/external.py`` (the seam), so the guard passes honestly today;
  (b) it CATCHES a planted raw ``AsyncAnthropic()`` constructed OUTSIDE ``libs/http``
      (an EXECUTED gate, not a YAML text-scan — the phantom-module failure mode a
      wrong ``python -m`` invocation cannot pass); and
  (c) it does NOT false-flag the legitimate ``libs/http`` home nor a
      ``TYPE_CHECKING``-only import of a vendor type.

It also asserts guard PARITY: the guard is wired into BOTH ``.github/workflows/
guards.yml`` and ``.pre-commit-config.yaml`` (never CI-only) — the §14 discipline
that every named guard runs in both places.

Oracle strategy: import + EXECUTE the real guard module (no mocks); plus a static
scan of the committed CI surface for parity. Hermetic — no network, no DB.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

import _support as S


def _ci_text() -> str:
    return S.read_all_text("*", root_parts=(".github", "workflows"))


def _precommit() -> str:
    return S.read_text(".pre-commit-config.yaml") or ""


def _has_guard(text: str, guard: str) -> bool:
    """A guard token, matched literally or with a normalized separator."""
    variants = {guard, guard.replace("-", "_"), guard.replace("-", " ")}
    return any(v in text for v in variants)


# ── AC-CON-004 (call_external_guard) — the module exists, runs, and gates ──────
@pytest.mark.static
def test_con_004_call_external_guard_module_runs_clean_and_catches_a_planted_raw_client():
    """The NEW ``ops.check_call_external`` guard imports, runs clean (0) on the committed
    tree (every raw vendor client lives in libs/http/external.py), and CATCHES a raw
    ``AsyncAnthropic()`` planted OUTSIDE libs/http — an executed gate, not a text-scan."""
    # (1) The module imports (phantom-module check) and exposes the seam-mirroring API.
    from ops import check_call_external

    assert hasattr(check_call_external, "check"), "guard must expose check(root)"
    assert hasattr(check_call_external, "main"), "guard must expose main() CLI"

    # (2) It runs CLEAN on the committed tree: every raw client is in libs/http/external.py.
    assert check_call_external.main([]) == 0, (
        "check-call-external must pass honestly on the current tree "
        "(every raw vendor client lives in libs/http)"
    )
    assert check_call_external.check() == 0, "check() must return 0 on the clean tree"
    assert check_call_external.raw_client_sites_outside_seam(S.ROOT) == [], (
        "no raw vendor-client construction may live outside libs/http today"
    )

    # (3) It CATCHES a planted raw AsyncAnthropic() OUTSIDE libs/http (the whole point).
    tmp = Path(tempfile.mkdtemp())
    svc = tmp / "services" / "rogue"
    svc.mkdir(parents=True)
    (svc / "bad.py").write_text(
        "from anthropic import AsyncAnthropic\n"
        "def go():\n"
        "    return AsyncAnthropic(api_key='x')  # raw client, bypasses the seam\n"
    )
    offenders = check_call_external.raw_client_sites_outside_seam(tmp)
    assert offenders, "guard must flag a raw AsyncAnthropic() built outside libs/http"
    assert any("services/rogue/bad.py" in o for o in offenders), (
        f"offender must name the offending file:line; got {offenders}"
    )

    # And the CLI exits NON-zero (naming the site) when a violation exists.
    import io
    import contextlib

    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rc = check_call_external.main([str(tmp)])
    assert rc != 0, "main() must exit non-zero when a raw client lives outside libs/http"
    assert "services/rogue/bad.py" in err.getvalue(), (
        f"main() must print the offending file:line to stderr; got {err.getvalue()!r}"
    )


@pytest.mark.static
def test_call_external_guard_recognizes_every_client_form_and_spares_the_seam():
    """The guard flags every raw-client construction FORM (Anthropic / httpx / GCS,
    aliased + lazy-in-function imports) yet spares the legitimate libs/http home and a
    TYPE_CHECKING-only vendor-type import."""
    from ops import check_call_external

    tmp = Path(tempfile.mkdtemp())

    # The legitimate seam home under libs/http — must NEVER be flagged, even though it
    # constructs every raw client and TYPE_CHECKING-imports a vendor type.
    seam = tmp / "libs" / "http" / "src" / "http"
    seam.mkdir(parents=True)
    (seam / "external.py").write_text(
        "from __future__ import annotations\n"
        "from typing import TYPE_CHECKING, Any\n"
        "import httpx\n"
        "if TYPE_CHECKING:\n"
        "    from anthropic import AsyncAnthropic\n"
        "def anthropic_client(**kw: Any) -> 'AsyncAnthropic':\n"
        "    from anthropic import AsyncAnthropic\n"
        "    return AsyncAnthropic(**kw)\n"
        "def http_client(**kw: Any) -> httpx.AsyncClient:\n"
        "    return httpx.AsyncClient(**kw)\n"
        "def gcs_bucket(name: str) -> Any:\n"
        "    from google.cloud import storage\n"
        "    return storage.Client().bucket(name)\n"
    )
    assert check_call_external.raw_client_sites_outside_seam(tmp) == [], (
        "the libs/http seam home must never be flagged (it is the ONE legitimate home)"
    )

    # Now plant raw clients OUTSIDE the seam in several forms — every one must be caught.
    svc = tmp / "services" / "a"
    svc.mkdir(parents=True)
    (svc / "httpx_raw.py").write_text(
        "import httpx\n"
        "def f():\n"
        "    return httpx.AsyncClient()\n"
        "def g():\n"
        "    return httpx.Client()\n"
    )
    (svc / "gcs_raw.py").write_text(
        "from google.cloud import storage\n"
        "def f():\n"
        "    return storage.Client()\n"
    )
    (svc / "aliased.py").write_text(
        "from anthropic import AsyncAnthropic as _AA\n"
        "def f():\n"
        "    return _AA()\n"
    )
    offenders = check_call_external.raw_client_sites_outside_seam(tmp)
    files = {o.rsplit(":", 1)[0].replace(str(tmp) + "/", "") for o in offenders}
    assert "services/a/httpx_raw.py" in files, f"httpx.AsyncClient()/Client() must be caught: {offenders}"
    assert "services/a/gcs_raw.py" in files, f"storage.Client() must be caught: {offenders}"
    assert "services/a/aliased.py" in files, f"aliased AsyncAnthropic must be caught: {offenders}"
    # A TYPE_CHECKING-only import is NOT a construction; the seam file above proved it
    # is not flagged. The seam home stays clean.
    assert not any("libs/http" in o for o in offenders), (
        f"libs/http must never appear among offenders: {offenders}"
    )


# ── AC-CI-005 parity (call_external_guard) — wired into BOTH pre-commit AND CI ──
@pytest.mark.static
def test_call_external_guard_has_precommit_and_ci_parity():
    """The check-call-external guard is wired into BOTH .github/workflows/guards.yml
    and .pre-commit-config.yaml (guard parity — never CI-only, never pre-commit-only)."""
    ci = _ci_text()
    pc = _precommit()
    assert ci.strip(), "no .github/workflows CI found (product CI not built)"
    assert pc.strip(), "no .pre-commit-config.yaml found (product pre-commit not built)"

    guard = "check-call-external"
    in_ci = _has_guard(ci, guard)
    in_pc = _has_guard(pc, guard)
    assert in_ci, "check-call-external must be present in CI (.github/workflows/guards.yml)"
    assert in_pc, "check-call-external must be present in pre-commit (.pre-commit-config.yaml)"

    # It must invoke the real module, not a phantom path.
    assert "ops.check_call_external" in ci, "CI must invoke `python -m ops.check_call_external`"
    assert "ops.check_call_external" in pc, "pre-commit must invoke `python -m ops.check_call_external`"

    # Fail-loud-never-skip: no continue-on-error escape hatch in the CI step.
    import re

    seg = re.search(r"check-call-external.*?(?=\n {6}# |\n {6}- name:|\Z)", ci, re.S)
    block = seg.group(0) if seg else ci
    assert "continue-on-error: true" not in block, (
        "check-call-external must be fail-loud (no continue-on-error: true)"
    )
