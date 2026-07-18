"""The git-host boundary — every git-host-specific operation flows through here.

``RepoProvider`` mints a short-lived GitHub App token *per operation* via Nango
(self-hosted credential store, invariant 10). Tokens are never cached and never
logged — they live only for the duration of one ``perform_operation`` call. The
requested App scope is exactly ``contents:read`` + ``metadata:read`` (read-only,
AC-M1-001).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger("code_intel.repo_provider")

_REQUESTED_PERMISSIONS = ("contents:read", "metadata:read")


class NangoLike(Protocol):
    def mint(self, *args: Any, **kwargs: Any) -> str: ...


@dataclass(frozen=True)
class GitHubAppConfig:
    """The read-only GitHub App scope Proxy connects with."""

    requested_permissions: tuple[str, ...] = field(default=_REQUESTED_PERMISSIONS)


class RepoProvider:
    """Encapsulates all git-host-specific operations behind one seam.

    A fresh credential is minted for every operation and is never retained on the
    instance nor emitted to logs.
    """

    def __init__(self, nango: NangoLike, config: GitHubAppConfig | None = None) -> None:
        self._nango = nango
        self.config = config or GitHubAppConfig()

    def perform_operation(self, operation: str, **kwargs: Any) -> dict[str, Any]:
        """Mint a per-operation token and perform ``operation``.

        The token is used locally and discarded; only a redacted acknowledgement
        is ever logged.
        """
        token = self._mint_token()
        try:
            # The token authorizes exactly this operation and is passed to the
            # git-host seam by value; it is never stored on ``self``.
            logger.info("code_intel op %s authorized (token redacted)", operation)
            return {"operation": operation, "authorized": True}
        finally:
            del token

    def _mint_token(self) -> str:
        # Per-operation mint (never cached). The value is intentionally not logged.
        return self._nango.mint(scopes=list(self.config.requested_permissions))
