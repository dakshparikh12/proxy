"""Regression guard — CANARY ESCAPE #3: the D-035 evidence-gate argv canonicalization is
covered ONLY by a live-E2B test (gated behind ``WORKROOM_LIVE_E2E=1``, skipped normally).

``verify_gate._canonicalize_argv`` (~verify_gate.py:113) strips a SINGLE leading
``cd <sandbox_root> &&`` prefix, collapses whitespace, and requires exit 0. The real bug it
protects (per the D-035 docstring): the E2B backend runs ``cd <sandbox_root> && <command>`` while
the plan's verify line is the BARE command — an exact byte-compare force-fails EVERY real build to
``needs_review`` even when the tests genuinely passed. The only existing coverage of this exact
``cd``-prefix match is behind the skipped live-E2B suite, so a revert to exact-string matching
would leave the offline suite green while silently breaking every real build.

This is a DETERMINISTIC, OFFLINE test (no E2B, no network): it feeds ``evidence_backed`` a
host-observed receipt whose argv is ``cd <sandbox_root> && <verify_cmd>`` at exit 0, against a
plan verify line of the BARE ``<verify_cmd>``, and asserts they MATCH (evidence is backed).

Reverting ``_canonicalize_argv`` to an exact-string match (so the ``cd`` prefix is NOT stripped)
turns this RED.
"""
from __future__ import annotations

from uuid import uuid4

import pytest


def _receipt(argv: list[str], exit_code: int) -> dict[str, object]:
    """The host-observed receipt shape the sandbox tool transport emits (§3.5 / D-017).

    ``argv`` is the REAL captured argv; ``exit_code`` the REAL kernel status. The gate reads
    THESE, never the model's prose.
    """
    return {
        "command_id": uuid4().hex,
        "argv": list(argv),
        "exit_code": int(exit_code),
        "stdout_ref": "sha256:real-captured-stream",
        "artifact_hashes": [],
    }


@pytest.mark.integration
def test_cd_prefixed_receipt_matches_bare_plan_verify_line() -> None:
    """A ``cd <root> && <cmd>`` exit-0 receipt satisfies a bare ``<cmd>`` plan verify line."""
    from workroom.verify_gate import evidence_backed

    sandbox_root = "/home/user/proxy-workroom"
    verify_cmd = "python -m pytest tests/test_ratelimit.py -q"

    # The plan names the BARE command; the E2B backend actually ran it under a cwd change,
    # so the host-observed receipt's argv joins to ``cd <root> && <verify_cmd>`` (the gate keys
    # receipts by ``" ".join(argv)`` then canonicalizes, so the joined argv must START with cd).
    plan_verify_cmds = [verify_cmd]
    receipts = [_receipt(["cd", sandbox_root, "&&", *verify_cmd.split()], exit_code=0)]

    assert evidence_backed(plan_verify_cmds, receipts) is True, (
        "the D-035 canonicalization must strip the leading 'cd <root> &&' so a real E2B receipt "
        "matches the bare plan verify line — an exact byte-compare force-fails every real build"
    )


@pytest.mark.integration
def test_cd_prefixed_receipt_still_requires_exit_zero() -> None:
    """Canonicalization matches the command, but a NON-zero exit is still NOT backed (the gate
    strips the cwd prefix — it does not weaken the exit-0 requirement)."""
    from workroom.verify_gate import evidence_backed

    verify_cmd = "python -m pytest tests/test_ratelimit.py -q"
    receipts = [_receipt(["cd", "/home/user/proxy-workroom", "&&", *verify_cmd.split()], exit_code=1)]

    assert evidence_backed([verify_cmd], receipts) is False, (
        "a matched-but-failing (exit 1) receipt must NOT be evidence-backed"
    )
