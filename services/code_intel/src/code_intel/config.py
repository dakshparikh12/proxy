"""Read code_intel operational tunables from ``config/defaults.toml``.

Law 4 — code owns only physics/pipes; the operational floors live in config as
one value + unit + range each. Env never overrides these (secrets/seats only).
"""
from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[4]
_CONFIG_PATH = _REPO_ROOT / "config" / "defaults.toml"

_DEFAULTS: dict[str, Any] = {
    "lsp_timeout_s": 3,
    "blobless_file_threshold": 100000,
    "lsp_warm_loc_threshold": 500000,
    "get_dependents_limit": 50,
    "batch_read_max_files": 10,
    "ready_deadline_s": 900,
    "uninstall_delete_deadline_s": 900,
}


@lru_cache(maxsize=1)
def _table() -> dict[str, Any]:
    try:
        with _CONFIG_PATH.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return dict(_DEFAULTS)
    table = dict(_DEFAULTS)
    table.update(data.get("code_intel", {}))
    return table


def get_int(key: str) -> int:
    return int(_table().get(key, _DEFAULTS[key]))
