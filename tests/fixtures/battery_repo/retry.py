"""Retry helpers — exponential backoff for flaky upstream calls."""

import random
import time
from typing import Any, Callable

MAX_RETRIES = 4
BASE_DELAY_MS = 250
JITTER_MS = 50


def backoff_delays_ms(retries: int = MAX_RETRIES, base_ms: int = BASE_DELAY_MS) -> list[int]:
    """The delay schedule: base doubling per attempt — 250, 500, 1000, 2000 ms."""
    return [base_ms * (2**attempt) for attempt in range(retries)]


def with_backoff(fn: Callable[[], Any], *, retries: int = MAX_RETRIES) -> Any:
    """Call ``fn``, retrying on any exception with the doubling schedule."""
    last_error: Exception | None = None
    for delay_ms in backoff_delays_ms(retries):
        try:
            return fn()
        except Exception as exc:
            last_error = exc
            time.sleep((delay_ms + random.randint(0, JITTER_MS)) / 1000.0)
    assert last_error is not None
    raise last_error
