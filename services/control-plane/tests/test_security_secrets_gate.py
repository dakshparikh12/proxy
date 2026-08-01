"""B4 — the session/internal signing secrets are prod-gated + compared constant-time.

Three insecure defaults escaped the boot gate: ``SESSION_SIGNING_KEY`` (session
forgery), ``INTERNAL_RECONCILE_TOKEN`` (reconcile/offboard bypass) and
``PROXY_INTERNAL_TOKEN`` (the /internal trust plane). These prove:

  * in prod, a missing signing secret CRASHES at ``load_settings`` NAMING the key
    (the §6 fail-fast boot gate) — never a silent insecure-default fall-through;
  * the internal-reconcile token compare is CONSTANT-TIME (``hmac.compare_digest``),
    never a plain ``==`` that leaks length/prefix by timing.
"""
from __future__ import annotations

import inspect

import pytest


def _prod_env(monkeypatch, **overrides: str) -> None:
    """A minimally-satisfied PROD env with every other hard-gate key set.

    Sets PROXY_ENV=prod + all unconditional gates so the ONLY missing key under
    test is the one the caller leaves out of ``overrides`` (set to "" to omit).
    """
    base = {
        "PROXY_ENV": "prod",
        "DATABASE_URL": "postgresql://u:p@localhost/db",
        "GCS_BUCKET": "b",
        "RECALL_API_KEY": "k",
        "AES_KEY_RECALL": "a",
        "AES_KEY_STT": "a",
        "AES_KEY_CALENDAR": "a",
        "ANTHROPIC_API_KEY": "a",
        "SESSION_SECRET": "s",
        "GCP_PROJECT_ID": "p",
        "SESSION_SIGNING_KEY": "sk",
        "INTERNAL_RECONCILE_TOKEN": "rt",
        "PROXY_INTERNAL_TOKEN": "it",
        "PUBLIC_BASE_URL": "https://proxy.example",
    }
    base.update(overrides)
    for key, val in base.items():
        if val == "":
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, val)


@pytest.mark.parametrize(
    "missing_key",
    ["SESSION_SIGNING_KEY", "INTERNAL_RECONCILE_TOKEN", "PROXY_INTERNAL_TOKEN"],
)
def test_prod_missing_signing_secret_crashes_at_load_naming_the_key(
    monkeypatch, missing_key: str
) -> None:
    """In prod, each internal signing secret is a hard boot gate: absent ⇒ crash NAMING it."""
    from control_plane.settings import load_settings

    _prod_env(monkeypatch, **{missing_key: ""})
    with pytest.raises(RuntimeError) as excinfo:
        load_settings()
    assert missing_key in str(excinfo.value)


def test_prod_missing_public_base_url_crashes_at_load_naming_the_key(monkeypatch) -> None:
    """BUG 4 — in prod, PUBLIC_BASE_URL is a hard boot gate: absent ⇒ crash NAMING it.

    PUBLIC_BASE_URL is load-bearing (the in-sandbox to_meeting relay + every draft approve link
    + the output-media page URL are built from it). Unset in prod is a silent dead deployment,
    so the §6 boot gate must fail loud at boot naming the key rather than lazily on first use.
    """
    from control_plane.settings import load_settings

    _prod_env(monkeypatch, PUBLIC_BASE_URL="")
    with pytest.raises(RuntimeError) as excinfo:
        load_settings()
    assert "PUBLIC_BASE_URL" in str(excinfo.value)


def test_non_prod_does_not_require_public_base_url(monkeypatch) -> None:
    """BUG 4 — off-prod PUBLIC_BASE_URL stays optional (a local run degrades honestly to "")."""
    from control_plane.settings import load_settings

    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("PROXY_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setenv("GCS_BUCKET", "b")
    monkeypatch.setenv("RECALL_API_KEY", "k")
    monkeypatch.setenv("AES_KEY_RECALL", "a")
    monkeypatch.setenv("AES_KEY_STT", "a")
    monkeypatch.setenv("AES_KEY_CALENDAR", "a")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    cfg = load_settings()  # must not raise — PUBLIC_BASE_URL is prod-gated only
    assert cfg.public_base_url == ""


def test_env_example_documents_public_base_url() -> None:
    """BUG 4 — .env.example documents PUBLIC_BASE_URL with a deploy-fact comment."""
    import pathlib

    # repo root is four levels up from this test file (services/control-plane/tests/<f>).
    root = pathlib.Path(__file__).resolve().parents[3]
    text = (root / ".env.example").read_text(encoding="utf-8")
    line = next((ln for ln in text.splitlines() if ln.startswith("PUBLIC_BASE_URL")), None)
    assert line is not None, "PUBLIC_BASE_URL must be documented in .env.example"
    assert "deploy fact" in line.lower() or "load-bearing" in line.lower()


def test_non_prod_does_not_require_the_internal_signing_secrets(monkeypatch) -> None:
    """Outside prod the local dev fallbacks stand — the boot gate must not crash."""
    from control_plane.settings import load_settings

    for key in (
        "SESSION_SIGNING_KEY",
        "INTERNAL_RECONCILE_TOKEN",
        "PROXY_INTERNAL_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PROXY_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setenv("GCS_BUCKET", "b")
    monkeypatch.setenv("RECALL_API_KEY", "k")
    monkeypatch.setenv("AES_KEY_RECALL", "a")
    monkeypatch.setenv("AES_KEY_STT", "a")
    monkeypatch.setenv("AES_KEY_CALENDAR", "a")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    # must not raise — the local fallbacks are permitted off-prod
    cfg = load_settings()
    assert cfg is not None


def test_internal_reconcile_token_compare_is_constant_time() -> None:
    """The internal-token compare uses ``hmac.compare_digest`` — no naked ``==``."""
    from ops import reconcile

    src = inspect.getsource(reconcile._valid_internal_token)
    assert "compare_digest" in src, "internal-token compare must be constant-time"
    assert "token == expected" not in src, "must not use a timing-leaky == compare"


def test_session_signing_key_no_hardcoded_prod_literal_off_prod(monkeypatch) -> None:
    """Off-prod the session signing still works (dev fallback); the literal is dev-only."""
    from control_plane import session

    monkeypatch.delenv("SESSION_SIGNING_KEY", raising=False)
    monkeypatch.setenv("PROXY_ENV", "local")
    signed = session._sign("abc")
    assert session._verify(signed) == "abc"


def test_recall_webhook_secret_resolves_from_canonical_name(monkeypatch) -> None:
    """B8 — settings resolves the Recall webhook secret from the CANONICAL env key."""
    from control_plane.settings import Settings

    monkeypatch.setenv("RECALL_WEBHOOK_SECRET", "whsec_canonical")
    monkeypatch.delenv("RECALL_WORKSPACE_VERIFICATION_SECRET", raising=False)
    assert Settings().resolved_recall_webhook_secret() == "whsec_canonical"


def test_recall_webhook_secret_falls_back_to_legacy_name(monkeypatch) -> None:
    """B8 — the legacy RECALL_WORKSPACE_VERIFICATION_SECRET is honoured when canonical unset."""
    from control_plane.settings import Settings

    monkeypatch.delenv("RECALL_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("RECALL_WORKSPACE_VERIFICATION_SECRET", "whsec_legacy")
    assert Settings().resolved_recall_webhook_secret() == "whsec_legacy"


def test_recall_webhook_secret_canonical_wins_over_legacy(monkeypatch) -> None:
    """B8 — when BOTH are set, the canonical name wins (no silent legacy override)."""
    from control_plane.settings import Settings

    monkeypatch.setenv("RECALL_WEBHOOK_SECRET", "whsec_canonical")
    monkeypatch.setenv("RECALL_WORKSPACE_VERIFICATION_SECRET", "whsec_legacy")
    assert Settings().resolved_recall_webhook_secret() == "whsec_canonical"
