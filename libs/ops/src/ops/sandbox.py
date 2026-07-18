"""provision_sandbox — per-sandbox JWT secret minting (AC-INV-009).

Every E2B sandbox is minted a DISTINCT random HS256/JWT secret at provision time;
the host keeps the sandbox->secret map. There is deliberately NO fleet-shared
secret constant: in-sandbox code that exfiltrated a shared secret could forge a
token accepted by another sandbox, so each sandbox's secret is unique and random.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass


@dataclass(frozen=True)
class ProvisionedSandbox:
    """A provisioned sandbox and its per-sandbox minted JWT secret."""

    tenant: str
    meeting_id: str
    sandbox_id: str
    jwt_secret: str

    @property
    def hs256_secret(self) -> str:  # alternate spelling of the same secret
        return self.jwt_secret


def provision_sandbox(*, tenant: str, meeting_id: str) -> ProvisionedSandbox:
    """Provision a sandbox with a freshly minted, distinct random JWT secret."""
    # token_urlsafe(32) yields a ~43-char cryptographically-random secret; a new
    # one is minted on every call so no two sandboxes ever share a secret.
    jwt_secret = secrets.token_urlsafe(32)
    sandbox_id = f"sbx-{tenant}-{meeting_id}-{secrets.token_hex(4)}"
    return ProvisionedSandbox(
        tenant=tenant,
        meeting_id=meeting_id,
        sandbox_id=sandbox_id,
        jwt_secret=jwt_secret,
    )
