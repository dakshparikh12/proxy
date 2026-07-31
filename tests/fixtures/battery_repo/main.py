"""Request entry point — rate limit, then auth, then caches, then upstream."""

from auth import login, verify_token
from cache_lru import LRUCache
from cache_redis import RedisCache
from ratelimit import TokenBucket
from upstream import fetch_profile

_bucket = TokenBucket()
_sessions = RedisCache()
_profiles = LRUCache()


def handle_login(username: str, password: str) -> dict[str, object]:
    if not _bucket.allow():
        return {"status": 429}
    token = login(username, password)
    if token is None:
        return {"status": 401}
    _sessions.put(username, token)
    return {"status": 200, "token": token}


def handle_profile(token: str) -> dict[str, object]:
    if not _bucket.allow():
        return {"status": 429}
    session = verify_token(token)
    if session is None:
        return {"status": 401}
    cached = _profiles.get(session.user_id)
    if cached is not None:
        return {"status": 200, "profile": cached}
    profile = fetch_profile(session.user_id)
    _profiles.put(session.user_id, profile)
    return {"status": 200, "profile": profile}
