"""Managed transport cost — the honest floor + elapsed×rate accrual (§4, AC-SEAM-13/14/15/22).

The managed rate card (bot $0.50 + STT $0.15 + TTS $0.10–0.20) lives ONCE in
``config/defaults.toml [transport]``. :func:`rate_card` reads those constants; both the
cost-floor check (:func:`floor_within_managed_ceiling`, AC-SEAM-13) and the accrual
(:class:`TransportCostAccrual`, AC-SEAM-14) multiply by the SAME constants — so the floor
binds real accrued spend, not a passes-by-construction constant sum (AC-SEAM-22). The
accrual is elapsed×rate (never a false flat all-in $1) and reloads across a harness
recycle — monotonic, never reset to 0 (AC-SEAM-15).
"""
from __future__ import annotations

from dataclasses import dataclass

from . import config

# The managed-stack cost envelope (§4): $0.75–0.85/hr, ≤$1/hr all-in with ~$0.20 headroom.
MANAGED_FLOOR_USD_PER_HR = 0.75
MANAGED_CEILING_USD_PER_HR = 0.85
ALLIN_CEILING_USD_PER_HR = 1.0


def rate_card() -> dict[str, float]:
    """The per-component managed rates — the SINGLE source of truth (AC-SEAM-22)."""
    return {
        "bot": config.get_float("bot_usd_per_hr"),
        "stt": config.get_float("stt_usd_per_hr"),
        "tts": config.get_float("tts_usd_per_hr"),
    }


def transport_rate() -> float:
    """The aggregate managed transport rate ($/hr) — the accrual multiplies by THIS."""
    return sum(rate_card().values())


def floor_within_managed_ceiling(rate: float | None = None) -> bool:
    """True iff the aggregate rate sits in the honest [$0.75, $0.85] managed band (AC-SEAM-13)."""
    total = transport_rate() if rate is None else rate
    return MANAGED_FLOOR_USD_PER_HR <= total <= MANAGED_CEILING_USD_PER_HR


@dataclass(frozen=True)
class TransportCostAccrual:
    """Elapsed×rate transport-cost accrual for one meeting (AC-SEAM-14/15).

    ``prior_usd`` carries accrued cost across a harness recycle so the value RELOADS
    (monotonic non-decreasing), never resets to 0 (AC-SEAM-15). ``rate`` defaults to the
    same :func:`transport_rate` the floor check binds (AC-SEAM-22).
    """

    rate_usd_per_hr: float
    prior_usd: float = 0.0

    @classmethod
    def new(cls, *, prior_usd: float = 0.0, rate: float | None = None) -> TransportCostAccrual:
        return cls(rate_usd_per_hr=transport_rate() if rate is None else rate, prior_usd=prior_usd)

    @classmethod
    def reload(cls, prior_usd: float, *, rate: float | None = None) -> TransportCostAccrual:
        """Resume after a recycle with the already-accrued cost (reload, not reset)."""
        return cls.new(prior_usd=prior_usd, rate=rate)

    def transport_usd(self, elapsed_hours: float) -> float:
        """Accrued transport cost at ``elapsed_hours`` — a computed accrual, not a constant."""
        return self.prior_usd + elapsed_hours * self.rate_usd_per_hr
