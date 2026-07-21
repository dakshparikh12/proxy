"""AC-SCRIBE-09: an open-weight seat swap is never assumed equivalent (static).

No production code path or config in the Scribe source hard-codes an open-weight
model name; the seat is read from PROXY_MODEL_SCRIBE via the canonical seat table,
so a swap is an env/eval decision, not a code change.
"""
from __future__ import annotations

import pathlib

import scribe.call as call_mod

OPEN_WEIGHT_MARKERS = ("deepseek", "glm-", "openrouter", "qwen", "mixtral", "llama-")


def test_scribe_09_no_hardcoded_open_weight_model_in_scribe_source() -> None:
    scribe_src = pathlib.Path(call_mod.__file__).parent
    offenders = []
    for py in scribe_src.glob("*.py"):
        low = py.read_text().lower()
        for marker in OPEN_WEIGHT_MARKERS:
            if marker in low:
                offenders.append(f"{py.name}: open-weight marker {marker!r}")
    assert not offenders, offenders


def test_scribe_09_model_comes_from_seat_table_not_a_literal() -> None:
    src = pathlib.Path(call_mod.__file__).read_text()
    assert "model_for" in src
