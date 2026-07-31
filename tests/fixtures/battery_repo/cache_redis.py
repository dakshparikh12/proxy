"""Session cache backed by redis (client stubbed in-process for tests)."""

import time

DEFAULT_TTL_S = 600


class RedisClient:
    """A minimal in-process stand-in for the real redis client."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, float]] = {}

    def setex(self, key: str, ttl_s: int, value: str) -> None:
        self._store[key] = (value, time.time() + ttl_s)

    def get(self, key: str) -> str | None:
        hit = self._store.get(key)
        if hit is None:
            return None
        value, expires_at = hit
        if expires_at < time.time():
            del self._store[key]
            return None
        return value


class RedisCache:
    """Write-through session cache; entries live for DEFAULT_TTL_S and expire by TTL."""

    def __init__(self, client: RedisClient | None = None) -> None:
        self._client = client if client is not None else RedisClient()

    def put(self, key: str, value: str, ttl_s: int = DEFAULT_TTL_S) -> None:
        self._client.setex(key, ttl_s, value)

    def get(self, key: str) -> str | None:
        return self._client.get(key)
