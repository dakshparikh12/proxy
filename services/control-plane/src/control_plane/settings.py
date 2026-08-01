"""Fail-fast configuration gate (Doc 00 §6/§7).

``~/platform``'s ``server.ts`` teaches one lesson: *fail loud at boot, not on
first use*. ``pydantic-settings BaseSettings`` validates the full config the
moment this module is imported; a missing **hard-gate** key is a startup crash
whose message NAMES the key -- never a lazy first-use failure.

The required-key manifest mirrors ``.env.example`` (the config contract):

  * unconditional hard gates: ``DATABASE_URL``, ``GCS_BUCKET``, ``RECALL_API_KEY``,
    the three per-domain AES credential keys, and at least one Claude/Anthropic
    auth mode (``ANTHROPIC_API_KEY`` / ``ANTHROPIC_AUTH_TOKEN`` / Vertex).
  * prod-gated: ``SESSION_SECRET``, the GCP project id, and the internal signing
    secrets ``SESSION_SIGNING_KEY`` / ``INTERNAL_RECONCILE_TOKEN`` /
    ``PROXY_INTERNAL_TOKEN`` (required only when the process runs in a
    ``prod``/``production`` environment — off-prod a clearly-dev fallback stands).
    A missing signing secret in prod would let an insecure literal escape the gate
    (session forgery / reconcile bypass), so the gate CRASHES at boot naming it.

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
    # The Recall webhook signing secret (whsec_<base64>, §11.10) — the HMAC key the
    # §4.6 verifier proves the caller with. Sourced from Secret Manager as env, never
    # a literal. Not a boot hard-gate (a deployment without inbound Recall webhooks
    # still boots); the webhook route fails CLOSED (401) when it is unset, so an
    # unverifiable delivery is never accepted.
    recall_webhook_secret: str = Field(
        default="", validation_alias="RECALL_WEBHOOK_SECRET"
    )
    # Back-compat only (B8): an earlier deployment used the env key
    # ``RECALL_WORKSPACE_VERIFICATION_SECRET``. The CANONICAL name is
    # ``RECALL_WEBHOOK_SECRET`` above; this fallback is resolved by
    # ``resolved_recall_webhook_secret`` when the canonical is unset so an old .env
    # doesn't silently disable webhook intake (verify would fail closed → 401 on
    # every delivery → no provisioning). Prefer the canonical name in every new env.
    recall_workspace_verification_secret: str = Field(
        default="", validation_alias="RECALL_WORKSPACE_VERIFICATION_SECRET"
    )
    # The GitHub-App webhook signing secret — the raw-UTF-8 HMAC key the §3.6
    # freshness push-ingress verifier (X-Hub-Signature-256) proves the caller with.
    # Sourced from Secret Manager as env, never a literal. Not a boot hard-gate; the
    # /webhooks/github route fails CLOSED (401) when it is unset, so an unverifiable
    # push delivery is never accepted (and never triggers a rebuild).
    github_webhook_secret: str = Field(
        default="", validation_alias="GITHUB_WEBHOOK_SECRET"
    )
    aes_key_recall: str = Field(default="", validation_alias="AES_KEY_RECALL")
    aes_key_stt: str = Field(default="", validation_alias="AES_KEY_STT")
    aes_key_calendar: str = Field(default="", validation_alias="AES_KEY_CALENDAR")

    # -- Claude / Anthropic auth -- keep all four modes (Doc 00 s7) -----------
    anthropic_api_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")
    anthropic_auth_token: str = Field(default="", validation_alias="ANTHROPIC_AUTH_TOKEN")
    claude_code_use_vertex: str = Field(
        default="", validation_alias="CLAUDE_CODE_USE_VERTEX"
    )
    # The founder's Claude Code SUBSCRIPTION token — the SAME credential the per-meeting
    # workroom carries into its sandbox so native ``claude`` authenticates (proven working).
    # The map-build SDK subprocess authenticates on it exactly like ``claude -p`` does, so a
    # deployment with ONLY the subscription can still build maps (the live founder setup).
    anthropic_oauth_token: str = Field(
        default="", validation_alias="CLAUDE_CODE_OAUTH_TOKEN"
    )

    # -- prod-gated ----------------------------------------------------------
    session_secret: str = Field(default="", validation_alias="SESSION_SECRET")
    gcp_project_id: str = Field(default="", validation_alias="GCP_PROJECT_ID")
    # The deployment's PUBLIC origin (the tunnel / host URL). LOAD-BEARING in prod: the
    # in-sandbox ``to_meeting`` relay URL, every draft approve link, and the output-media
    # page URL are all built from it (relay.py / provisioner.py / meetings.py). Empty in
    # prod means Proxy cannot reach the room and no draft is ever approvable — a silent
    # dead deployment — so a prod boot CRASHES naming the key. Off-prod it stays optional
    # (a local run degrades honestly to "" per the relay's own empty-origin handling).
    public_base_url: str = Field(default="", validation_alias="PUBLIC_BASE_URL")
    # The internal signing secrets (B4). Each has a clearly-dev fallback in its
    # consumer (session.py / ops.reconcile) so local runs work UNSET; in prod a
    # missing value would let that insecure literal escape into production (session
    # forgery / reconcile+offboard bypass), so all three are prod-gated below.
    session_signing_key: str = Field(
        default="", validation_alias="SESSION_SIGNING_KEY"
    )
    internal_reconcile_token: str = Field(
        default="", validation_alias="INTERNAL_RECONCILE_TOKEN"
    )
    proxy_internal_token: str = Field(
        default="", validation_alias="PROXY_INTERNAL_TOKEN"
    )

    def anthropic_auth_configured(self) -> bool:
        """At least one of the four Claude SDK auth modes is present."""
        return bool(
            self.anthropic_api_key
            or self.anthropic_auth_token
            or self.claude_code_use_vertex
            or self.anthropic_oauth_token
        )

    def resolved_recall_webhook_secret(self) -> str:
        """The Recall webhook signing secret — canonical name first, then back-compat.

        Prefers ``RECALL_WEBHOOK_SECRET`` (canonical, B8) and falls back to the
        legacy ``RECALL_WORKSPACE_VERIFICATION_SECRET`` only when the canonical is
        unset, so an older .env keyed on the legacy name doesn't silently disable
        webhook intake. Empty when neither is set (the route fails CLOSED at 401).
        """
        return self.recall_webhook_secret or self.recall_workspace_verification_secret


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
        # PUBLIC_BASE_URL is load-bearing in prod (relay + draft approve links + output-media
        # URL). Unset in prod is a silent dead deployment (Proxy can't reach the room, no
        # approvals) — crash at boot naming it rather than fail lazily on first use.
        if not cfg.public_base_url:
            missing.append("PUBLIC_BASE_URL")
        # B4 — the internal signing secrets must never fall back to their insecure
        # dev literals in prod (session forgery / reconcile+offboard bypass). A
        # missing value crashes at boot NAMING the key rather than silently serving
        # a forgeable session or an open reconcile/offboard endpoint.
        if not cfg.session_signing_key:
            missing.append("SESSION_SIGNING_KEY")
        if not cfg.internal_reconcile_token:
            missing.append("INTERNAL_RECONCILE_TOKEN")
        if not cfg.proxy_internal_token:
            missing.append("PROXY_INTERNAL_TOKEN")
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
