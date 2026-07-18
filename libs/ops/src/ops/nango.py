"""Per-operation GitHub token minting over Nango (self-hosted) — AC-TEN-003.

A GitHub installation token is minted FRESH for every operation via the injected
Nango client, handed to the operation body, and then dropped. It is NEVER cached
between operations and NEVER written to any log:

  * caching would let a rotated/revoked installation token linger past its
    intended single-operation lifetime, and
  * logging would leak a live credential to stdout.

The Nango client is injected (constructor keyword ``nango``) so a stub can record
mint calls in tests; production passes the real self-hosted Nango connection.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, TypeVar

T = TypeVar("T")

# Natural spellings a Nango client may expose for "mint an installation token",
# probed in order. Self-hosted Nango surfaces a connection/installation token
# under one of these method names.
_MINT_METHODS: tuple[str, ...] = (
    "get_token",
    "mint_token",
    "create_connection_token",
)


class NangoClient(Protocol):
    """The minimal surface :class:`RepoProvider` needs from a Nango client."""

    def get_token(self, *args: Any, **kwargs: Any) -> str: ...


class RepoProvider:
    """Mints a GitHub token per operation via Nango — never cached, never logged.

    The minted token lives only for the duration of the operation body; it is
    never stored on the instance (no cache) and never emitted to a logger.
    """

    def __init__(self, *, nango: NangoClient) -> None:
        self._nango = nango

    def _mint(self) -> str:
        """Mint one fresh token via the injected Nango client."""
        for name in _MINT_METHODS:
            method = getattr(self._nango, name, None)
            if callable(method):
                return str(method())
        raise AttributeError("Nango client exposes no token-mint method")

    def with_token(self, body: Callable[[str], T]) -> T:
        """Mint a FRESH token, run ``body(token)``, and return its result.

        A new token is minted on every call (one Nango mint per operation); the
        value is a local handed to ``body`` and is never retained or logged.
        """
        token = self._mint()
        return body(token)

    # Alternate natural spellings for the per-operation token flow.
    def operation(self, body: Callable[[str], T]) -> T:
        return self.with_token(body)

    def run_operation(self, body: Callable[[str], T]) -> T:
        return self.with_token(body)
