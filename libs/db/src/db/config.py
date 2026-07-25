"""Tunable loader for ``config/defaults.toml`` (one value + unit + range each).

Resolved by walking up from this module to the repo root so the values are
readable regardless of the process working directory. Env never overrides these
operational tunables (env is for secrets/seats only) — the defaults file is the
single source of truth, with conservative in-code fallbacks if it is absent.
"""
from __future__ import annotations

import pathlib
import tomllib
from functools import lru_cache
from typing import Any

_FALLBACK: dict[str, dict[str, Any]] = {
    "ops": {"stale_after_s": 40, "heartbeat_s": 10, "reconcile_interval_s": 300},
    "sandbox": {
        "timeout_s": 3600,
        "ttl_s": 3600,
        "mcp_port": 8081,
        "jwt_ttl_s": 900,
        "jwt_refresh_margin_s": 300,
    },
    "stt": {"refresh_interval_s": 600},
}


def _find_defaults() -> pathlib.Path | None:
    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "config" / "defaults.toml"
        if candidate.is_file():
            return candidate
    return None


@lru_cache(maxsize=1)
def load_defaults() -> dict[str, Any]:
    path = _find_defaults()
    if path is None:
        return _FALLBACK
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return _FALLBACK


def _get(section: str, key: str) -> int:
    data = load_defaults()
    sect = data.get(section, {}) if isinstance(data, dict) else {}
    value = sect.get(key, _FALLBACK[section][key])
    return int(value)


def stale_after_s() -> int:
    """Seconds of heartbeat silence after which a running row is orphaned."""
    return _get("ops", "stale_after_s")


def heartbeat_s() -> int:
    """Owner fencing-heartbeat cadence."""
    return _get("ops", "heartbeat_s")


def sandbox_timeout_s() -> int:
    """E2B-native sandbox timeout backstop set at provision."""
    return _get("sandbox", "timeout_s")


def sandbox_ttl_s() -> int:
    """TTL past which the reconcile sweep destroys a leaked sandbox."""
    return _get("sandbox", "ttl_s")


def sandbox_mcp_port() -> int:
    """The per-sandbox MCP-over-HTTP tool-transport sidecar port inside E2B (§3.5)."""
    return _get("sandbox", "mcp_port")


def sandbox_jwt_ttl_s() -> int:
    """Short-TTL of the per-sandbox HS256 JWT the token_provider mints (§3.5)."""
    return _get("sandbox", "jwt_ttl_s")


def sandbox_jwt_refresh_margin_s() -> int:
    """Re-mint margin: re-sign once the cached JWT is within this many seconds of exp (§3.5)."""
    return _get("sandbox", "jwt_refresh_margin_s")


def stt_refresh_interval_s() -> int:
    """In-process STT credential refresh cadence (never the reconcile cron)."""
    return _get("stt", "refresh_interval_s")
