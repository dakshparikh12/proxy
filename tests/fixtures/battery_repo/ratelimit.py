"""Token-bucket rate limiting for the public API."""

import time

RATE_PER_MINUTE = 90
BURST = 20


class TokenBucket:
    """Refills at RATE_PER_MINUTE/60 per second, holds at most BURST tokens."""

    def __init__(self, rate_per_minute: int = RATE_PER_MINUTE, burst: int = BURST) -> None:
        self._rate_per_s = rate_per_minute / 60.0
        self._burst = float(burst)
        self._tokens = float(burst)
        self._last = time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        self._tokens = min(self._burst, self._tokens + (now - self._last) * self._rate_per_s)
        self._last = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False
