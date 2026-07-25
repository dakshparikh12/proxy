"""D-004 / CANONICAL §12.7: the listening soft/hard caps are 0.8/1.0 × projected-hours,
NOT the old hard-coded $1.5/$3.0 constants. This acceptance test proves the caps SCALE
with the meeting's projected duration (D-013) — the behavior the fix introduces.

Runs the real product path (libs.ops.cost.MeetingCost.check_meeting_budget) — no mocks.
It computes the expected caps independently (0.8×/1.0× the projected hours) and asserts the
breaker state flips at exactly those boundaries, for several projected-hours values.
"""
from __future__ import annotations

import pytest

from libs.ops.cost import MeetingCost


def _baseline_meeting(*, listening_usd: float, projected_hours: float) -> MeetingCost:
    """A MeetingCost whose listening baseline is exactly ``listening_usd`` (via Scribe spend)."""
    mc = MeetingCost(projected_hours=projected_hours)
    mc.accrue_model(role="scribe", usd=listening_usd)  # listening subset
    return mc


@pytest.mark.integration
@pytest.mark.parametrize("projected_hours", [1.0, 2.0, 3.0, 4.0])
def test_caps_are_fractions_of_projected_hours(projected_hours: float) -> None:
    """soft = 0.8 × projected-hours, hard = 1.0 × projected-hours — not $1.5/$3.0."""
    expected_soft = 0.8 * projected_hours
    expected_hard = 1.0 * projected_hours

    mc = MeetingCost(projected_hours=projected_hours)
    assert mc.listening_soft_cap_usd() == pytest.approx(expected_soft, abs=1e-9), (
        f"soft cap must be 0.8×projected-hours ({expected_soft}); "
        f"got {mc.listening_soft_cap_usd()} — the old $1.5 constant must be gone"
    )
    assert mc.listening_hard_cap_usd() == pytest.approx(expected_hard, abs=1e-9), (
        f"hard cap must be 1.0×projected-hours ({expected_hard}); "
        f"got {mc.listening_hard_cap_usd()} — the old $3.0 constant must be gone"
    )
    # The old hard-coded constants are NOT what governs the caps anymore.
    if projected_hours != 2.0:  # a 2h meeting coincidentally shares hard=2.0≠3.0 anyway
        assert mc.listening_soft_cap_usd() != 1.5 or projected_hours == 1.875
        assert mc.listening_hard_cap_usd() != 3.0 or projected_hours == 3.0


@pytest.mark.integration
@pytest.mark.parametrize("projected_hours", [1.0, 2.0, 3.0])
def test_breaker_flips_at_projected_hours_boundaries(projected_hours: float) -> None:
    """The degrade / notes-only transitions happen at 0.8×/1.0× projected-hours."""
    soft = 0.8 * projected_hours
    hard = 1.0 * projected_hours

    # Just under the soft cap → normal (full).
    below = _baseline_meeting(listening_usd=soft - 0.05, projected_hours=projected_hours)
    assert below.check_meeting_budget().listening_state == "normal", (
        f"listening baseline just under soft cap ({soft}) at {projected_hours}h must stay normal"
    )

    # Between soft and hard → degrade.
    mid = _baseline_meeting(listening_usd=(soft + hard) / 2.0, projected_hours=projected_hours)
    assert mid.check_meeting_budget().listening_state == "degrade", (
        f"listening baseline between soft ({soft}) and hard ({hard}) at "
        f"{projected_hours}h must degrade (Haiku + widen Scribe), never a silent cliff"
    )

    # Above the hard cap → notes-only.
    above = _baseline_meeting(listening_usd=hard + 0.10, projected_hours=projected_hours)
    assert above.check_meeting_budget().listening_state == "notes_only", (
        f"listening baseline above hard cap ({hard}) at {projected_hours}h must go notes-only"
    )


@pytest.mark.integration
def test_default_projected_hours_is_two() -> None:
    """D-013: projected-hours defaults to 2h when the Recall duration is unknown at claim."""
    mc = MeetingCost()
    assert mc.projected_hours == pytest.approx(2.0), "default projected-hours must be 2h (D-013)"
    assert mc.listening_soft_cap_usd() == pytest.approx(1.6), "default soft cap = 0.8×2h = $1.6"
    assert mc.listening_hard_cap_usd() == pytest.approx(2.0), "default hard cap = 1.0×2h = $2.0"


@pytest.mark.integration
def test_longer_meeting_earns_a_higher_envelope_before_degrading() -> None:
    """A spend that degrades a 2h meeting is still normal for a 4h meeting (the caps scale)."""
    spend = 1.7  # > 1.6 (2h soft) but < 3.2 (4h soft)
    short = _baseline_meeting(listening_usd=spend, projected_hours=2.0)
    long = _baseline_meeting(listening_usd=spend, projected_hours=4.0)
    assert short.check_meeting_budget().listening_state == "degrade"
    assert long.check_meeting_budget().listening_state == "normal", (
        "a longer projected meeting must earn a proportionally larger listening envelope"
    )
