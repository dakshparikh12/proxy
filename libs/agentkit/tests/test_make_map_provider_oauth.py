"""BLOCKER B — the map-build provider accepts the Claude Code SUBSCRIPTION token.

The founder's live deployment authenticates on the Claude Code SUBSCRIPTION, whose credential
is ``CLAUDE_CODE_OAUTH_TOKEN`` (the SAME token the workroom's native ``claude`` proves working
inside the sandbox). Before this fix ``make_map_provider`` only understood ``api_key`` /
``auth_token`` / Vertex, so a subscription-only deployment got ``map_provider = None`` → no
``repo_maps`` → ``POST /meetings`` 409 "not indexed". These prove the fourth auth mode:

* with ONLY the subscription token, a REAL provider is built whose ``auth_env`` carries
  ``CLAUDE_CODE_OAUTH_TOKEN`` and NO ``ANTHROPIC_*`` key (the subscription is self-sufficient);
* with NOTHING configured, the honest no-op holds (``None`` — boot still succeeds, Law 2);
* an explicit ``api_key`` still wins (priority preserved), and the token is never logged.
"""
from __future__ import annotations

import logging

from agentkit import ClaudeAgentProvider, make_map_provider

_SUBSCRIPTION = "sk-ant-oat01-EXAMPLE-subscription-token"


def test_subscription_token_alone_builds_a_real_provider() -> None:
    """ONLY the subscription token → a real provider whose auth_env carries CLAUDE_CODE_OAUTH_TOKEN."""
    provider = make_map_provider(oauth_token=_SUBSCRIPTION)

    assert isinstance(provider, ClaudeAgentProvider)  # a REAL provider, not the None no-op
    assert provider.auth_env.get("CLAUDE_CODE_OAUTH_TOKEN") == _SUBSCRIPTION
    # The subscription token is self-sufficient — no ANTHROPIC_* key smuggled in alongside it.
    assert "ANTHROPIC_API_KEY" not in provider.auth_env
    assert "ANTHROPIC_AUTH_TOKEN" not in provider.auth_env


def test_no_auth_of_any_kind_is_the_honest_none_no_op() -> None:
    """With NOTHING configured (incl. the subscription), the honest no-op holds — boot succeeds."""
    assert make_map_provider() is None
    assert make_map_provider(api_key="", auth_token="", use_vertex="", oauth_token="") is None


def test_api_key_wins_over_subscription_when_both_present() -> None:
    """Priority preserved: an explicit API key takes precedence over the subscription token."""
    provider = make_map_provider(api_key="sk-ant-api-xyz", oauth_token=_SUBSCRIPTION)
    assert isinstance(provider, ClaudeAgentProvider)
    assert provider.auth_env.get("ANTHROPIC_API_KEY") == "sk-ant-api-xyz"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in provider.auth_env


def test_subscription_token_is_never_logged(caplog: object) -> None:
    """The secret rides auth_env only — constructing the provider logs nothing carrying it."""
    import pytest

    assert isinstance(caplog, pytest.LogCaptureFixture)
    with caplog.at_level(logging.DEBUG):
        provider = make_map_provider(oauth_token=_SUBSCRIPTION)
    assert isinstance(provider, ClaudeAgentProvider)
    assert _SUBSCRIPTION not in caplog.text  # never logged (Hard Rule: Secrets)
