"""A7 (C-FRESHFLAG) — server-side >90s freshness_lagging bool (03 §3.8/§4).

The notes object's currency is reported as a ``freshness_lagging`` bool computed
SERVER-SIDE from the fold's ``as_of`` vs ``now()`` at a configurable threshold
(default 90s per §4's ``[>90s lag]`` lever). It rides OUTSIDE the byte-canonical
body so the fold stays byte-stable (AC-CSREAD-10).

These run under plain pytest — the fold is pure and the clock is injected, so the
trip point is exercised for real (no DB, no mocks).
"""
from __future__ import annotations

import importlib
import os
from datetime import datetime, timedelta, timezone

import scribe.notes_reader as nr


def _flag(as_of: str | None, delta_count: int = 1):
    return nr.FreshnessFlag(as_of=as_of, delta_count=delta_count, is_empty=as_of is None)


def _fold(as_of: str):
    # A single 'add' delta whose created_at is the given as_of.
    return nr.Notes.fold_all(
        [{"entry_id": "E1", "op": "add", "payload": "{}", "created_at": as_of}]
    )


NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _iso(seconds_ago: float) -> str:
    return (NOW - timedelta(seconds=seconds_ago)).isoformat()


# ── The trip point: strictly > threshold (89s false, 91s true) ────────────────
def test_freshness_lagging_true_above_90s() -> None:
    flag = _flag(_iso(91))
    assert flag.freshness_lagging(now=NOW, threshold_s=90.0) is True


def test_freshness_lagging_false_below_90s() -> None:
    flag = _flag(_iso(89))
    assert flag.freshness_lagging(now=NOW, threshold_s=90.0) is False


def test_freshness_lagging_false_exactly_at_threshold() -> None:
    # Strictly greater-than: at exactly the threshold the object is still fresh.
    flag = _flag(_iso(90))
    assert flag.freshness_lagging(now=NOW, threshold_s=90.0) is False


def test_empty_ledger_is_never_lagging() -> None:
    # No as_of → nothing to be stale about.
    assert _flag(None, delta_count=0).freshness_lagging(now=NOW) is False


# ── It clears once a fresh delta lands (AC-PERF-FRESHNESS trip-then-clear) ─────
def test_lagging_clears_when_a_fresh_write_arrives() -> None:
    stale = _fold(_iso(120))
    assert stale.freshness_flag.freshness_lagging(now=NOW, threshold_s=90.0) is True
    fresh = _fold(_iso(1))
    assert fresh.freshness_flag.freshness_lagging(now=NOW, threshold_s=90.0) is False


# ── It is computed from the fold as_of, not a cached clock (§3.9) ─────────────
def test_lagging_reads_fold_as_of_over_naive_timestamp() -> None:
    # A naive (tz-less) created_at is treated as UTC and still trips correctly.
    naive = (NOW - timedelta(seconds=200)).replace(tzinfo=None).isoformat()
    flag = _flag(naive)
    assert flag.freshness_lagging(now=NOW, threshold_s=90.0) is True


# ── The threshold is configurable (AC-PERF-FRESHNESS-THRESHOLD-CONFIGURABLE) ──
def test_threshold_configurable_via_env_override() -> None:
    prev = os.environ.get("NOTES_FRESHNESS_LAG_THRESHOLD_S")
    os.environ["NOTES_FRESHNESS_LAG_THRESHOLD_S"] = "60"
    try:
        importlib.reload(nr)
        assert nr.notes_freshness_lag_threshold_s() == 60.0
        # At 61s lag with the 60s override -> lagging; at 59s -> not.
        f = nr.FreshnessFlag(as_of=_iso(61), delta_count=1, is_empty=False)
        assert f.freshness_lagging(now=NOW) is True
        f2 = nr.FreshnessFlag(as_of=_iso(59), delta_count=1, is_empty=False)
        assert f2.freshness_lagging(now=NOW) is False
    finally:
        if prev is None:
            os.environ.pop("NOTES_FRESHNESS_LAG_THRESHOLD_S", None)
        else:
            os.environ["NOTES_FRESHNESS_LAG_THRESHOLD_S"] = prev
        importlib.reload(nr)


def test_default_threshold_is_90s_from_defaults_toml() -> None:
    # No env override -> the [scribe].notes_freshness_lag_threshold_s default (90).
    prev = os.environ.pop("NOTES_FRESHNESS_LAG_THRESHOLD_S", None)
    try:
        importlib.reload(nr)
        assert nr.notes_freshness_lag_threshold_s() == 90.0
    finally:
        if prev is not None:
            os.environ["NOTES_FRESHNESS_LAG_THRESHOLD_S"] = prev
        importlib.reload(nr)


# ── It rides OUTSIDE the byte-canonical body (AC-CSREAD-10 preserved) ─────────
def test_lagging_not_in_canonical_json_body() -> None:
    n = _fold(_iso(200))
    canonical = n.to_canonical_json()
    assert "freshness_lagging" not in canonical  # never busts the byte-stable body
    # But the response wrapper DOES surface it at the top level.
    resp = n.to_response_dict(now=NOW, threshold_s=90.0)
    assert resp["freshness_lagging"] is True
    assert "freshness_flag" in resp  # canonical flag still present alongside


def test_canonical_json_stays_byte_stable_regardless_of_now() -> None:
    n = _fold(_iso(200))
    first = n.to_canonical_json()
    for _ in range(5):
        assert _fold(_iso(200)).to_canonical_json() == first  # clock-free body
