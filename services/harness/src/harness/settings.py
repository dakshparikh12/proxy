"""Fail-fast configuration gate (Doc 00 §6/§7).

``~/platform``'s ``server.ts`` teaches one lesson: *fail loud at boot, not on
first use*. ``pydantic-settings BaseSettings`` validates the full config the
moment this module is imported; a missing **hard-gate** key is a startup crash
whose message NAMES the key -- never a lazy first-use failure.

The required-key manifest mirrors ``.env.example`` (the config contract):

  * unconditional hard gates: ``DATABASE_URL``, ``GCS_BUCKET``, ``RECALL_API_KEY``,
    the three per-domain AES credential keys, and at least one Claude/Anthropic
    auth mode (``ANTHROPIC_API_KEY`` / ``ANTHROPIC_AUTH_TOKEN`` / Vertex).
  * prod-gated: ``SESSION_SECRET`` and the GCP project id (required only when the
    process runs in a ``prod``/``production`` environment).

Numeric tunables live in ``config/defaults.toml`` (never here); env overrides are
for the secrets/seats above only.
"""
from __future__ import annotations

import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROD_ENVS = frozenset({"prod", "production"})


def _is_prod() -> bool:
    """True when the process is running in a production environment."""
    env = os.environ.get("PROXY_ENV", os.environ.get("ENVIRONMENT", "local"))
    return env.strip().lower() in _PROD_ENVS


class Settings(BaseSettings):
    """The validated config contract -- every field bound to its ``.env`` key."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    # -- unconditional hard gates --------------------------------------------
    database_url: str = Field(default="", validation_alias="DATABASE_URL")
    gcs_bucket: str = Field(default="", validation_alias="GCS_BUCKET")
    recall_api_key: str = Field(default="", validation_alias="RECALL_API_KEY")
    aes_key_recall: str = Field(default="", validation_alias="AES_KEY_RECALL")
    aes_key_stt: str = Field(default="", validation_alias="AES_KEY_STT")
    aes_key_calendar: str = Field(default="", validation_alias="AES_KEY_CALENDAR")

    # -- Claude / Anthropic auth -- keep all three modes (Doc 00 s7) ----------
    anthropic_api_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")
    anthropic_auth_token: str = Field(default="", validation_alias="ANTHROPIC_AUTH_TOKEN")
    claude_code_use_vertex: str = Field(
        default="", validation_alias="CLAUDE_CODE_USE_VERTEX"
    )

    # -- prod-gated ----------------------------------------------------------
    session_secret: str = Field(default="", validation_alias="SESSION_SECRET")
    gcp_project_id: str = Field(default="", validation_alias="GCP_PROJECT_ID")

    def anthropic_auth_configured(self) -> bool:
        """At least one of the three Claude SDK auth modes is present."""
        return bool(
            self.anthropic_api_key
            or self.anthropic_auth_token
            or self.claude_code_use_vertex
        )


def _missing_required(cfg: Settings) -> list[str]:
    """The env names of every required-but-unset hard-gate key."""
    missing: list[str] = []
    if not cfg.database_url:
        missing.append("DATABASE_URL")
    if not cfg.gcs_bucket:
        missing.append("GCS_BUCKET")
    if not cfg.recall_api_key:
        missing.append("RECALL_API_KEY")
    if not cfg.aes_key_recall:
        missing.append("AES_KEY_RECALL")
    if not cfg.aes_key_stt:
        missing.append("AES_KEY_STT")
    if not cfg.aes_key_calendar:
        missing.append("AES_KEY_CALENDAR")
    if not cfg.anthropic_auth_configured():
        # Name the primary mode; the OAuth/Vertex alternatives satisfy it too.
        missing.append("ANTHROPIC_API_KEY")
    if _is_prod():
        if not cfg.session_secret:
            missing.append("SESSION_SECRET")
        if not cfg.gcp_project_id:
            missing.append("GCP_PROJECT_ID")
    return missing


def load_settings() -> Settings:
    """Validate the environment and return the settings, or crash naming the gap."""
    cfg = Settings()
    missing = _missing_required(cfg)
    if missing:
        raise RuntimeError(
            "fail-fast boot gate: missing required config keys "
            f"({', '.join(missing)}) -- set them in .env / Secret Manager before boot"
        )
    return cfg


# Import-time validation: a missing hard-gate key crashes HERE (at import), with a
# message naming the key -- the s6 fail-loud-at-boot contract.
settings: Settings = load_settings()
