"""Secret-binding preflight — fail fast when a required secret is unbound."""
from __future__ import annotations

from collections.abc import Mapping

REQUIRED_SECRETS: tuple[str, ...] = (
    "DATABASE_URL",
    "ANTHROPIC_API_KEY",
    "RECALL_API_KEY",
    "ASSEMBLYAI_API_KEY",
    "INTERNAL_RECONCILE_TOKEN",
)


def check_secret_bindings(env: Mapping[str, str]) -> list[str]:
    """Return the names of required secrets that are missing/empty in ``env``."""
    return [name for name in REQUIRED_SECRETS if not env.get(name)]
