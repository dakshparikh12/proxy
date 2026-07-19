"""Read transport tunables from ``config/defaults.toml`` (Law 4 — code owns pipes,
config owns the floors; one value + unit + range each; env overrides secrets/seats only).

The §12.8-pinned latency SLOs stay single-homed in ``[latency_slo]`` (reused, never
redeclared here — AC-XCUT-09). The ``[transport]`` block carries only numbers NOT pinned
by §12.8: the small Output-Media chunk size, the max buffered-audio bound, and the
barge-in stop budget (§3.6/§4/Law-3).
"""
from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[4]
_CONFIG_PATH = _REPO_ROOT / "config" / "defaults.toml"

_DEFAULTS: dict[str, Any] = {
    # Chunk + buffer stay BELOW barge_in_budget_ms so a surviving in-flight chunk's
    # residual playout can't exceed the 200ms stop budget (AC-TURN-10); ≤ AC-SPEAK-08's
    # 250 ceiling. Mirrors config/defaults.toml.
    "tts_chunk_ms": 120,
    "max_buffered_audio_ms": 120,
    "barge_in_budget_ms": 200,
    "headline_char_soft_cap": 240,
    "max_spoken_chars_per_hour": 4000,
    "outbound_sends_per_second": 4,
    "bot_usd_per_hr": 0.50,
    "stt_usd_per_hr": 0.15,
    "tts_usd_per_hr": 0.15,
}


@lru_cache(maxsize=1)
def _table() -> dict[str, Any]:
    try:
        with _CONFIG_PATH.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return dict(_DEFAULTS)
    table = dict(_DEFAULTS)
    table.update(data.get("transport", {}))
    return table


def get_int(key: str) -> int:
    """One transport tunable, read from config (never a hardcoded literal in code)."""
    return int(_table().get(key, _DEFAULTS[key]))


def get_float(key: str) -> float:
    """One float transport tunable (e.g. a rate-card USD/hr), read from config."""
    return float(_table().get(key, _DEFAULTS[key]))
