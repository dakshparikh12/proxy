"""Task F2 — in-meeting config surface acceptance test.

TDD spec (red → green):
1. The config module surfaces the expected key/seat names.
2. Keys are read via the Secret Manager seam (pydantic-settings from env), not literals.
3. No hard-coded literal secret is present in services/in-meeting (grep check).
4. ``python -c "import e2b"`` succeeds (lock updated).
5. Model seats come from ``libs/llm.routing.model_for``, not duplicated constants.
"""
from __future__ import annotations

import importlib
import os
import pathlib
import subprocess
import sys
from unittest import mock


# ---------------------------------------------------------------------------
# F2-001: the config module is importable
# ---------------------------------------------------------------------------

def test_settings_module_importable() -> None:
    """``from in_meeting.settings import Settings`` must succeed."""
    from in_meeting.settings import Settings  # noqa: PLC0415

    assert Settings is not None


# ---------------------------------------------------------------------------
# F2-002: Settings exposes the required field names
# ---------------------------------------------------------------------------

def test_settings_exposes_required_fields() -> None:
    """Settings must expose all five vendor key fields and three seat accessor methods."""
    from in_meeting.settings import Settings  # noqa: PLC0415

    # Vendor key fields (sourced from Secret Manager via env)
    required_fields = {
        "recall_api_key",
        "cartesia_api_key",
        "e2b_api_key",
        "assemblyai_api_key",
        "anthropic_api_key",
    }
    actual_fields = set(Settings.model_fields.keys())
    missing = required_fields - actual_fields
    assert not missing, f"Settings is missing fields: {missing}"


# ---------------------------------------------------------------------------
# F2-003: seat accessors delegate to libs/llm model_for (not literals)
# ---------------------------------------------------------------------------

def test_seat_accessors_delegate_to_model_for() -> None:
    """orchestrator_model / worker_model / trigger_model must call model_for()."""
    from in_meeting import settings as settings_mod  # noqa: PLC0415

    assert callable(settings_mod.orchestrator_model)
    assert callable(settings_mod.worker_model)
    assert callable(settings_mod.trigger_model)

    # The functions must delegate to libs/llm routing (patch model_for and verify)
    with mock.patch("in_meeting.settings.model_for") as patched:
        patched.side_effect = lambda seat: f"test-model-{seat}"
        assert settings_mod.orchestrator_model() == "test-model-ORCHESTRATOR"
        assert settings_mod.worker_model() == "test-model-WORKROOM"
        assert settings_mod.trigger_model() == "test-model-GATE"


# ---------------------------------------------------------------------------
# F2-004: Settings reads keys from env (Secret Manager seam), not literals
# ---------------------------------------------------------------------------

def test_settings_reads_keys_from_env() -> None:
    """Settings must read each key from the correct env variable (mocked env)."""
    test_env = {
        "RECALL_API_KEY": "recall-test-key",
        "CARTESIA_API_KEY": "cartesia-test-key",
        "E2B_API_KEY": "e2b-test-key",
        "ASSEMBLYAI_API_KEY": "assemblyai-test-key",
        "ANTHROPIC_API_KEY": "anthropic-test-key",
    }
    # Temporarily inject test env vars
    with mock.patch.dict(os.environ, test_env, clear=False):
        from in_meeting.settings import Settings  # noqa: PLC0415

        cfg = Settings()
        assert cfg.recall_api_key == "recall-test-key"
        assert cfg.cartesia_api_key == "cartesia-test-key"
        assert cfg.e2b_api_key == "e2b-test-key"
        assert cfg.assemblyai_api_key == "assemblyai-test-key"
        assert cfg.anthropic_api_key == "anthropic-test-key"


# ---------------------------------------------------------------------------
# F2-005: no hard-coded literal secret in services/in-meeting
# ---------------------------------------------------------------------------

def test_no_hardcoded_literal_secrets() -> None:
    """grep services/in-meeting for literal secret patterns must return 0 matches."""
    repo_root = pathlib.Path(__file__).parents[2]
    in_meeting_dir = repo_root / "services" / "in-meeting"

    # Patterns that indicate hard-coded secrets
    forbidden_patterns = [
        "sk-",           # OpenAI/Anthropic API key prefix
        "Token ",        # Recall-style token literals
        "whsec_",        # webhook signing secret prefix
        "AKIA",          # AWS key prefix (sanity check)
    ]

    py_files = list(in_meeting_dir.rglob("*.py"))
    violations: list[str] = []
    for f in py_files:
        text = f.read_text(encoding="utf-8")
        for pattern in forbidden_patterns:
            if pattern in text:
                violations.append(f"{f}: contains '{pattern}'")

    assert not violations, "Hard-coded literal secrets found:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# F2-006: e2b is importable (lock updated)
# ---------------------------------------------------------------------------

def test_e2b_importable() -> None:
    """``import e2b`` must succeed (e2b installed via uv lock update)."""
    import e2b  # noqa: PLC0415

    assert e2b is not None


# ---------------------------------------------------------------------------
# F2-007: load_settings() works with all env vars set (no crash)
# ---------------------------------------------------------------------------

def test_load_settings_with_all_vars() -> None:
    """load_settings() must return a Settings instance when all required vars present."""
    test_env = {
        "RECALL_API_KEY": "recall-test",
        "CARTESIA_API_KEY": "cartesia-test",
        "E2B_API_KEY": "e2b-test",
        "ASSEMBLYAI_API_KEY": "assemblyai-test",
        "ANTHROPIC_API_KEY": "anthropic-test",
    }
    with mock.patch.dict(os.environ, test_env, clear=False):
        from in_meeting.settings import load_settings  # noqa: PLC0415

        cfg = load_settings()
        assert cfg is not None
        assert cfg.recall_api_key == "recall-test"
