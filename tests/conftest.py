"""Shared pytest configuration.

Two jobs: the marker registry, and **the DSN gate**.

THE DSN GATE. Store-backed tests across this suite skip when there is no
``TEST_DATABASE_URL``. Individually that is reasonable; collectively it is a trap. A run
that forgets the DSN reports "N passed, M skipped" and reads as green, while every claim
about what POSTGRES does went unasked. That is the same failure mode as the two defects
that shipped unit-green on this branch — ``post_meeting_tasks.operation_ref`` holding the
wrong uuid (an FK violation no fake had an FK to catch) and ``planned_at`` being read
though the column never existed (so nothing ever expired). In all three cases the suite
said success while the substrate was never consulted.

So: **when no DSN is resolvable at all, a skip that blames the missing DSN becomes a
failure.** The conversion is done in ``pytest_runtest_makereport`` rather than by editing
each gate, which means it catches every way a test can express the gate — a module-level
``skipif``, a fixture's ``pytest.skip``, an in-body call — and it needs no guess about
which tests are store-backed. The test already said so in its own skip reason.

The gate fires ONLY when there is no DSN anywhere. With one set, every existing gate keeps
its own semantics untouched (Doc 03's suites, for instance, additionally require
``DOC03_STORE_SPEC_DB`` because their DB carries a divergent schema; those skips still skip).

The single escape hatch is ``PROXY_TESTS_ALLOW_NO_DB=1``, for a contributor with no local
Postgres who wants the unit tiers. It has to be typed on purpose, and the run then says
plainly at the bottom that the substrate went unverified. It is a TEST-harness switch, not
a product feature flag, so doc00 §7's zero-runtime-flags rule does not apply to it.
"""
import os

import pytest

_ALLOW_NO_DB = "PROXY_TESTS_ALLOW_NO_DB"

#: Substrings that identify a skip as "skipped because there is no database".
_DSN_BLAMED = ("TEST_DATABASE_URL", "DATABASE_URL", "no local Postgres", "live Postgres")

MISSING_DSN = (
    "No database DSN. This test asserts what POSTGRES does, so without one it proves "
    "nothing — and a quiet skip would make the run look greener than the truth.\n\n"
    "  Fix:  set TEST_DATABASE_URL (or DATABASE_URL), e.g.\n"
    "        TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/proxy\n"
    "        then: alembic upgrade head\n\n"
    f"  Or, to run the unit tiers only:  {_ALLOW_NO_DB}=1\n"
    "        (an explicit choice to leave the substrate unverified)"
)


def resolve_dsn() -> str | None:
    """The DSN this run will use, or ``None``. The one place the vars are read."""
    for var in ("TEST_DATABASE_URL", "DATABASE_URL"):
        value = os.environ.get(var, "").strip()
        if value:
            return value
    return None


def _waived() -> bool:
    return os.environ.get(_ALLOW_NO_DB, "").strip() in {"1", "true", "yes"}


def pytest_configure(config):
    config.addinivalue_line("markers", "smoke: cheap/fast subset for per-pass runs")


@pytest.fixture(scope="session")
def dsn() -> str:
    """The DSN store-backed tests run against. FAILS if absent — it does not skip."""
    resolved = resolve_dsn()
    if resolved:
        return resolved
    if _waived():
        pytest.skip(f"{_ALLOW_NO_DB} is set — substrate deliberately unverified")
    pytest.fail(MISSING_DSN, pytrace=False)
    raise AssertionError("unreachable")  # pragma: no cover - pytest.fail raises


@pytest.hookimpl(hookwrapper=True, trylast=True)
def pytest_runtest_makereport(item, call):
    """Turn a no-DSN skip into a failure, whatever form the gate took.

    ``trylast`` so the skipping plugin has already produced its report and we rewrite the
    finished outcome. Only skips that themselves blame the database are touched; a skip for
    any other reason (a missing optional dep, an unsupported platform) is left alone.
    """
    outcome = yield
    if resolve_dsn() or _waived():
        return
    report = outcome.get_result()
    if not report.skipped:
        return
    reason = str(report.longrepr[2] if isinstance(report.longrepr, tuple) else report.longrepr)
    if not any(needle in reason for needle in _DSN_BLAMED):
        return
    report.outcome = "failed"
    report.longrepr = f"{MISSING_DSN}\n\n  the gate that skipped: {reason}"


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    """State plainly, every run, whether the substrate was actually exercised.

    The trap this closes is a human reading "N passed" and believing the database was
    involved. One line at the bottom removes the ambiguity in both directions.
    """
    if resolve_dsn():
        terminalreporter.write_line(
            "database: store-backed tests ran against a REAL Postgres.", green=True
        )
        return
    terminalreporter.write_line(
        "database: NONE — store-backed tests were NOT verified. "
        + (f"({_ALLOW_NO_DB} was set, so they skipped.)" if _waived()
           else "(They FAILED; set TEST_DATABASE_URL.)"),
        yellow=True,
    )
