"""Harness config — env + CLI args, one place (Law 4: no baked-in deployment facts).

Everything the live run needs that is a deployment fact (the meeting URL, the
public output-media origin, credentials, the run id) is read HERE from the
environment (``.env`` in the repo root is loaded on import if present) or
overridden per-invocation by CLI args. Nothing is hard-coded.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
_ENV_PATH = _REPO_ROOT / ".env"


def load_dotenv(path: Path = _ENV_PATH) -> None:
    """Load ``KEY=VALUE`` lines from ``.env`` into ``os.environ`` (no overwrite).

    Deliberately tiny (no third-party dep): blank lines and ``#`` comments are
    skipped; an already-set env var always wins so an explicit shell export or a
    CI secret is never clobbered by the file.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def default_run_id() -> str:
    """A timestamped run id (``run-YYYYmmdd-HHMMSS``) when none is supplied."""
    return "run-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


@dataclass(frozen=True)
class HarnessConfig:
    """The resolved live-run configuration (env + args), validated fail-fast."""

    meeting_url: str
    output_media_origin: str  # public https origin serving /output-media/{id}
    control_plane_url: str  # base URL of the running control-plane (HEARD/OUTPUT)
    #: The live meeting id (returned by the test-provision) the HEARD/transcript taps address.
    #: Empty until a meeting is provisioned; set via ``--meeting-id`` / ``MEETING_ID`` for a run
    #: that monitors an already-provisioned meeting.
    meeting_id: str
    #: The internal admin bearer (``PROXY_INTERNAL_TOKEN``) the smoke taps require. Sent as the
    #: ``X-Internal-Token`` header to POST /admin/test-provision + GET /admin/transcript.
    internal_token: str
    replicas: int
    run_id: str
    runs_root: Path
    # credentials (Secret Manager in prod; .env for the live test)
    recall_api_key: str
    cartesia_api_key: str
    langfuse_base_url: str
    langfuse_public_key: str
    langfuse_secret_key: str
    transcript_path: Path

    @property
    def run_dir(self) -> Path:
        return self.runs_root / self.run_id


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name, default) or "").strip()


def build_config(
    *,
    meeting_url: str | None = None,
    output_media_origin: str | None = None,
    control_plane_url: str | None = None,
    meeting_id: str | None = None,
    internal_token: str | None = None,
    replicas: int | None = None,
    run_id: str | None = None,
    runs_root: Path | None = None,
    transcript_path: Path | None = None,
    load_env: bool = True,
) -> HarnessConfig:
    """Resolve the config from args first, then env, then defaults.

    ``load_env`` reads ``.env`` (real-run default). Offline tests pass explicit
    values and ``load_env=False`` so no host secret leaks into a unit test.
    """
    if load_env:
        load_dotenv()
    return HarnessConfig(
        meeting_url=meeting_url if meeting_url is not None else _env("MEETING_URL"),
        output_media_origin=(
            output_media_origin
            if output_media_origin is not None
            else _env("RECALL_OUTPUT_MEDIA_URL")
        ),
        control_plane_url=(
            control_plane_url
            if control_plane_url is not None
            else _env("CONTROL_PLANE_URL", "http://localhost:8080")
        ),
        meeting_id=meeting_id if meeting_id is not None else _env("MEETING_ID"),
        internal_token=(
            internal_token if internal_token is not None else _env("PROXY_INTERNAL_TOKEN")
        ),
        replicas=replicas if replicas is not None else int(_env("REPLICAS", "2") or "2"),
        run_id=run_id or _env("RUN_ID") or default_run_id(),
        runs_root=runs_root or (Path(__file__).resolve().parents[3] / "live-runs"),
        recall_api_key=_env("RECALL_API_KEY"),
        cartesia_api_key=_env("CARTESIA_API_KEY"),
        langfuse_base_url=_env("LANGFUSE_BASE_URL", "https://cloud.langfuse.com"),
        langfuse_public_key=_env("LANGFUSE_PUBLIC_KEY"),
        langfuse_secret_key=_env("LANGFUSE_SECRET_KEY"),
        transcript_path=(
            transcript_path
            if transcript_path is not None
            else Path(__file__).resolve().parents[3] / "MEETING_TRANSCRIPT.md"
        ),
    )


def missing_live_keys(cfg: HarnessConfig) -> list[str]:
    """The credential/URL fields a LIVE run needs but that are empty (fail-fast)."""
    required = {
        "MEETING_URL": cfg.meeting_url,
        "RECALL_OUTPUT_MEDIA_URL": cfg.output_media_origin,
        "RECALL_API_KEY": cfg.recall_api_key,
        "CARTESIA_API_KEY": cfg.cartesia_api_key,
        "LANGFUSE_PUBLIC_KEY": cfg.langfuse_public_key,
        "LANGFUSE_SECRET_KEY": cfg.langfuse_secret_key,
    }
    return [name for name, value in required.items() if not value]
