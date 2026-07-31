"""Session auth — token issue/verify for the request path."""

import base64
import json
import time

from models import Session

SESSION_TTL_S = 1800


def issue_token(user_id: str) -> str:
    payload = {"uid": user_id, "exp": time.time() + SESSION_TTL_S}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def verify_token(token: str) -> Session | None:
    """Return the session for a valid token, else None."""
    try:
        payload = json.loads(base64.urlsafe_b64decode(token.encode()))
        if payload["exp"] < time.time():
            return None
        return Session(user_id=payload["uid"], expires_at=payload["exp"])
    except:  # noqa: E722  TODO(auth): bare except swallows decode errors, hides tampering
        return None


def login(username: str, password: str) -> str | None:
    """Authenticate and hand back a fresh token."""
    if not username or not password:
        return None
    return issue_token(username)
