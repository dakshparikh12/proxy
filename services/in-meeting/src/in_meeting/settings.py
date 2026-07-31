"""Config surface for the in-meeting engine (Task F2).

All vendor API keys are sourced ONLY from Secret Manager (surfaced as env vars
at deploy time via Secret Manager → Cloud Run env injection, or locally via
.env). No literal key ever appears here. This follows the identical pattern
used by ``services/harness/src/harness/settings.py``.

Model seats are NOT duplicated here — they are resolved on demand via
``libs/llm.routing.model_for`` (the single source of truth, CANONICAL §12.12).
The three accessor callables below expose only the seat *names* the in-meeting
engine uses, and delegate entirely to that table.
"""
from __future__ import annotations

# ``llm`` is a workspace member whose bare name is treated as ``ignore_missing_imports``
# by mypy (the check targets the source under libs/llm/src/llm). The return type of
# ``model_for`` is ``str`` (confirmed in routing.py), but mypy sees Any via the skip.
# We import it here and cast at call sites so ``--strict`` is satisfied without skipping.
from llm.routing import model_for as model_for  # re-export for tests (mypy: type: ignore[misc])
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated vendor-key config for the in-meeting engine.

    Every field is bound to its Secret-Manager-injected env var; no default
    literal, no hardcoded value. An empty string is the pydantic-settings
    sentinel for "not set"; callers that require the key must check it.
    """

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    # Recall: meeting bot transport (AC-F2 / Doc 00 §7)
    recall_api_key: str = Field(default="", validation_alias="RECALL_API_KEY")

    # Cartesia: TTS for Proxy's in-meeting voice (Sonic 3.5)
    cartesia_api_key: str = Field(default="", validation_alias="CARTESIA_API_KEY")

    # E2B: per-meeting Workroom sandbox (Doc 05 C10)
    e2b_api_key: str = Field(default="", validation_alias="E2B_API_KEY")

    # AssemblyAI: BYOK STT via Recall (pasted into Recall Transcription dashboard per region)
    assemblyai_api_key: str = Field(default="", validation_alias="ASSEMBLYAI_API_KEY")

    # Anthropic / Claude SDK auth (primary mode; OAuth and Vertex also supported)
    anthropic_api_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")


def load_settings() -> Settings:
    """Return a Settings instance populated from environment.

    Does NOT crash on missing keys — the in-meeting engine is launched inside a
    meeting where the full Secret Manager env is already injected; if a key is
    missing at call time, the component that needs it will surface the error
    at use time (fail-closed rather than fail-loud-at-boot, since boot happens
    via the harness which already has its own boot gate).
    """
    return Settings()


# ---------------------------------------------------------------------------
# Model seat accessors — delegate to libs/llm.routing (never duplicated here)
# ---------------------------------------------------------------------------

def orchestrator_model() -> str:
    """The model id for the in-meeting orchestrator seat (ORCHESTRATOR)."""
    return str(model_for("ORCHESTRATOR"))


def worker_model() -> str:
    """The model id for the Workroom worker seat (WORKROOM)."""
    return str(model_for("WORKROOM"))


def trigger_model() -> str:
    """The model id for the cheap trigger/gate seat (GATE)."""
    return str(model_for("GATE"))
