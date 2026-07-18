"""Doc 00 · §9 The Dockerfile (AC-DOCK-001..004).

Milestone m07. Every test maps to exactly one blocking criterion (id in the
docstring). NO product code is imported at module top level -- these are static
TEXT oracles over the product's committed Dockerfile(s) and Alembic env.py, so
this module COLLECTS clean and FAILS red before ``deploy/`` / ``services/*``
Dockerfiles exist.

Oracle sources per PROTO-DETERMINISTIC-01:
  * [deployment]  DOCK-001, DOCK-002, DOCK-004 -- static text scans over the
    product's committed Dockerfile(s). Hermetic: no docker binary. Each asserts
    the concatenated Dockerfile text is non-empty FIRST, so it goes red before
    any Dockerfile exists.
  * [integration] DOCK-003 -- the parallel-boot migrate schedule reduces to a
    static scan of the Dockerfile CMD (the ``until alembic upgrade head`` retry
    loop, 30x5s, then ``exec``) plus the Alembic ``env.py`` wrapping the upgrade
    in ``pg_advisory_lock``. Hermetic and deterministic: the lock + bounded
    retry is what serializes N parallel boots. Red before either file exists.

Spec §9 literal (source_quotes encoded below):
    FROM python:3.12-slim AS builder
    COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
    RUN uv sync --frozen --no-dev --package harness   # per-service, self-contained
    FROM python:3.12-slim
    ARG SANDBOX_IMAGE_HASH
    LABEL proxy.sandbox-image-hash=$SANDBOX_IMAGE_HASH
    RUN useradd -m -u 1001 appuser   # HOME REQUIRED -- Claude Agent SDK writes to $HOME
    USER appuser
    ENV PORT=8080 HOME=/home/appuser
    CMD ["sh","-c","n=0; until alembic upgrade head; do n=$((n+1)); [ $n -ge 30 ] && exit 1; sleep 5; done && exec python -m harness.server"]
"""

import re

import pytest

import _support as S


# ---------------------------------------------------------------------------
# Shared helper: the product's committed Dockerfile(s), as one text blob.
# Concatenating every Dockerfile under deploy/ + services/ + the repo root makes
# the scan robust to layout. Empty string => no Dockerfile built yet => the
# per-test non-empty guard goes red BEFORE the files exist.
# ---------------------------------------------------------------------------
def _dockerfile_text() -> str:
    return (
        S.read_all_text("Dockerfile*", root_parts=("deploy",))
        + "\n"
        + S.read_all_text("*Dockerfile*", root_parts=("deploy",))
        + "\n"
        + S.read_all_text("Dockerfile*", root_parts=("services",))
        + "\n"
        + S.read_all_text("*Dockerfile*", root_parts=("services",))
        + "\n"
        + S.read_all_text("Dockerfile*")  # repo-root Dockerfiles
    )


def _alembic_env_text() -> str:
    """Every Alembic ``env.py`` in the product source (migration entrypoint)."""
    return (
        S.read_all_text("env.py", root_parts=("services",))
        + "\n"
        + S.read_all_text("env.py", root_parts=("libs",))
        + "\n"
        + S.read_all_text("env.py", root_parts=("alembic",))
        + "\n"
        + S.read_all_text("env.py", root_parts=("migrations",))
    )


# ── AC-DOCK-001 ───────────────────────────────────────────────────────────
@pytest.mark.deployment
def test_dock_001_multistage_uv_per_service_frozen_sync():
    """AC-DOCK-001: multi-stage uv build; builder runs uv sync --frozen --no-dev --package <svc>."""
    dock = _dockerfile_text()
    assert dock.strip(), "no deploy/ or service Dockerfile found (product not built)"

    # Multi-stage: a named builder stage from python:3.12-slim, plus a runtime stage.
    assert re.search(r"FROM\s+python:3\.12-slim\s+AS\s+builder", dock, re.I), (
        "build must be multi-stage with a `FROM python:3.12-slim AS builder` stage"
    )
    from_stages = re.findall(r"^\s*FROM\s+\S+", dock, re.I | re.M)
    assert len(from_stages) >= 2, (
        f"build must be multi-stage (>=2 FROM stages: builder + runtime); found {from_stages}"
    )

    # The uv binary is copied in from the pinned uv image (multi-stage uv build).
    assert re.search(r"COPY\s+--from=ghcr\.io/astral-sh/uv", dock, re.I), (
        "builder must `COPY --from=ghcr.io/astral-sh/uv ... /bin/uv` (multi-stage uv build)"
    )

    # The builder runs a per-service, self-contained, frozen, no-dev uv sync.
    # source_quote: "RUN uv sync --frozen --no-dev --package harness"
    sync = re.search(r"uv\s+sync\b[^\n]*", dock)
    assert sync, "builder must `RUN uv sync ...` (dependency install step absent)"
    sync_line = sync.group(0)
    # non_frozen_sync == 0: every uv sync must be --frozen.
    assert "--frozen" in sync_line, (
        f"uv sync must be --frozen (non_frozen_sync==0); got: {sync_line!r}"
    )
    # dev_deps_in_prod == 0: production image must be --no-dev.
    assert "--no-dev" in sync_line, (
        f"uv sync must be --no-dev (dev_deps_in_prod==0); got: {sync_line!r}"
    )
    # Per-service, self-contained: --package <svc> pins the single service package.
    assert re.search(r"--package\s+\S+", sync_line), (
        f"uv sync must be per-service (--package <svc>, self-contained); got: {sync_line!r}"
    )


# ── AC-DOCK-002 ───────────────────────────────────────────────────────────
@pytest.mark.deployment
def test_dock_002_nonroot_uid_1001_with_home_set():
    """AC-DOCK-002: runtime runs as non-root uid 1001 appuser with USER appuser and HOME=/home/appuser set."""
    dock = _dockerfile_text()
    assert dock.strip(), "no deploy/ or service Dockerfile found (product not built)"

    # A non-root user (uid 1001, appuser) is created.
    # source_quote: "RUN useradd -m -u 1001 appuser"
    assert re.search(r"useradd\b[^\n]*-u\s+1001\b[^\n]*\bappuser\b", dock), (
        "runtime must create a non-root user `useradd -m -u 1001 appuser`"
    )

    # USER appuser is set (runs_as_root == 0): the last effective USER is appuser, not root.
    user_dirs = re.findall(r"^\s*USER\s+(\S+)", dock, re.M)
    assert user_dirs, "runtime must set `USER appuser` (no USER directive => runs as root)"
    assert user_dirs[-1] == "appuser", (
        f"final USER must be `appuser`, never root (runs_as_root==0); got {user_dirs}"
    )
    assert "root" not in user_dirs[-1:], "runtime must NOT run as root"

    # HOME is set (=/home/appuser) -- REQUIRED because the Claude Agent SDK writes to $HOME.
    # source_quote: "... HOME=/home/appuser"  (home_unset == 0)
    assert re.search(r"\bENV\b[^\n]*\bHOME\s*=\s*/home/appuser\b", dock), (
        "runtime ENV must set HOME=/home/appuser (home_unset==0; Claude Agent SDK writes to $HOME)"
    )


# ── AC-DOCK-003 ───────────────────────────────────────────────────────────
@pytest.mark.integration
def test_dock_003_parallel_boot_migrate_advisory_lock_retry_then_exec():
    """AC-DOCK-003: CMD retry-loops alembic upgrade (30x5s) then exec server; env.py wraps upgrade in pg_advisory_lock."""
    dock = _dockerfile_text()
    assert dock.strip(), "no deploy/ or service Dockerfile found (product not built)"

    # The CMD retries `alembic upgrade head` in an `until ... done` loop.
    # source_quote CMD: until alembic upgrade head; do ... [ $n -ge 30 ] && exit 1; sleep 5; done && exec ...
    assert re.search(r"until\s+alembic\s+upgrade\s+head", dock), (
        "CMD must retry-loop `until alembic upgrade head` (losers retry while the winner migrates)"
    )
    # Bounded retry: 30 attempts x 5s sleep (~150s, inside the startup-probe deadline).
    assert re.search(r"-ge\s+30\b", dock), (
        "migrate retry must be bounded at 30 attempts (`[ $n -ge 30 ]`)"
    )
    assert re.search(r"sleep\s+5\b", dock), (
        "migrate retry must sleep 5s between attempts (30x5s)"
    )
    # After the migration wins, all boots `exec` the server (no lingering shell).
    assert re.search(r"exec\s+python\s+-m\s+harness\.server", dock), (
        "after migrate, the CMD must `exec python -m harness.server` (retry-then-serve)"
    )

    # env.py serializes the race: the upgrade is wrapped in a Postgres advisory lock,
    # so exactly one container migrates at a time (concurrent_migrations==0).
    # source_quote: "env.py wraps the upgrade in SELECT pg_advisory_lock(...)"
    env = _alembic_env_text()
    assert env.strip(), "no Alembic env.py found (parallel-migrate lock not built)"
    assert re.search(r"pg_advisory_lock", env, re.I), (
        "env.py must wrap the upgrade in SELECT pg_advisory_lock(...) so exactly one "
        "container migrates at a time (concurrent_migrations==0, partial_migration==0)"
    )
    # The lock must be acquired around the actual migration run (serialize before run_migrations).
    assert re.search(r"run_migrations", env), (
        "env.py must call run_migrations() -- the advisory lock guards this migration run"
    )


# ── AC-DOCK-004 ───────────────────────────────────────────────────────────
@pytest.mark.deployment
def test_dock_004_sandbox_image_hash_label_pins_release_to_base():
    """AC-DOCK-004: image carries a proxy.sandbox-image-hash LABEL from the SANDBOX_IMAGE_HASH ARG (fail-closed provenance)."""
    dock = _dockerfile_text()
    assert dock.strip(), "no deploy/ or service Dockerfile found (product not built)"

    # The release<->sandbox provenance coordinate enters as a build ARG.
    # source_quote: "ARG SANDBOX_IMAGE_HASH   # release<->sandbox-image provenance"
    assert re.search(r"^\s*ARG\s+SANDBOX_IMAGE_HASH\b", dock, re.M), (
        "runtime must declare `ARG SANDBOX_IMAGE_HASH` (release<->sandbox-image provenance coordinate)"
    )

    # The built image carries a proxy.sandbox-image-hash LABEL sourced from that ARG.
    # source_quote: "LABEL proxy.sandbox-image-hash=$SANDBOX_IMAGE_HASH"
    label = re.search(r"LABEL\s+proxy\.sandbox-image-hash\s*=\s*(\S+)", dock)
    assert label, (
        "image must carry `LABEL proxy.sandbox-image-hash=...` (promote-time provenance coordinate)"
    )
    # The label value must be the ARG, not a hardcoded/empty literal -- otherwise
    # a mismatched app/sandbox pair could not be detected at promote/boot.
    label_val = label.group(1).strip("\"'")
    assert re.search(r"\$\{?SANDBOX_IMAGE_HASH\}?", label_val), (
        f"proxy.sandbox-image-hash LABEL must be driven by $SANDBOX_IMAGE_HASH (not hardcoded); got {label_val!r}"
    )
    assert label_val not in ("", '""', "''"), (
        "proxy.sandbox-image-hash LABEL must not be empty (a blank hash would let a "
        "mismatched app/sandbox pair run instead of failing closed; mismatched_pair_runs==0)"
    )
