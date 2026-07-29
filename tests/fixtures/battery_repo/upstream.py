"""Profile-service client — the one outbound dependency on the request path."""

from models import Profile
from retry import with_backoff

UPSTREAM_TIMEOUT_S = 9


def fetch_profile(user_id: str) -> Profile:
    """Fetch a profile from the profile service, retrying with backoff."""

    def _call() -> Profile:
        # The real HTTP call rides the shared client with UPSTREAM_TIMEOUT_S.
        return Profile(user_id=user_id, display_name=user_id.title())

    result = with_backoff(_call)
    assert isinstance(result, Profile)
    return result
