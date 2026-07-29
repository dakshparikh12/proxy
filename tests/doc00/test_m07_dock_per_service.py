"""Doc 00 · §9 — per-service Dockerfile hardening (foundation.docker-migrate).

Companion to ``test_m07_dock.py``. That module scans a *concatenation* of every
Dockerfile under ``deploy/`` + ``services/`` + the repo root, so a single hardened
file (the root ``deploy/Dockerfile``) can satisfy every literal while the three
per-service images remain unhardened stubs. This module closes that hole: it reads
EACH of the three per-service Dockerfiles individually and asserts that file, on
its own, carries the full hardening — non-root ``USER appuser`` + uid 1001, a set
``ENV HOME``, the ``proxy.sandbox-image-hash`` provenance LABEL, the bounded
``until alembic upgrade head`` self-migrate retry loop, and the correct
per-service server module in its own ``exec``.

Static TEXT oracles only — no docker binary, hermetic and deterministic. Each
per-service assertion reports the offending file path so a gap in one image can
never be masked by another.

The DoD (Doc 00 §9): ALL THREE per-service deployable images
(control_plane, meeting_runtime, code_intel) must be independently deployable and
independently hardened.
"""

import re

import pytest

import _support as S

# The three per-service deployable images and the server module each must exec
# after a successful migrate. control_plane + meeting_runtime ship from the
# harness package; each still exec's its OWN server module.
PER_SERVICE = {
    "control_plane": "control_plane.server",
    "meeting_runtime": "control_plane.server",
    "code_intel": "code_intel.server",
}


def _service_dockerfile(service: str) -> str:
    """The committed text of one per-service Dockerfile (empty string if absent)."""
    return S.read_text("deploy", service, "Dockerfile") or ""


@pytest.mark.parametrize("service", sorted(PER_SERVICE))
@pytest.mark.deployment
def test_per_service_dockerfile_multistage_frozen_per_package_sync(service):
    """Each per-service image is a multi-stage uv build with a frozen, no-dev, per-package sync."""
    dock = _service_dockerfile(service)
    assert dock.strip(), f"deploy/{service}/Dockerfile is missing (per-service image not built)"

    # Multi-stage: at least a builder + a runtime FROM.
    from_stages = re.findall(r"^\s*FROM\s+\S+", dock, re.I | re.M)
    assert len(from_stages) >= 2, (
        f"deploy/{service}/Dockerfile must be multi-stage (>=2 FROM stages); found {from_stages}"
    )

    # A single, per-service, self-contained, frozen, no-dev uv sync.
    sync = re.search(r"uv\s+sync\b[^\n]*", dock)
    assert sync, f"deploy/{service}/Dockerfile must `RUN uv sync ...` (dependency install absent)"
    sync_line = sync.group(0)
    assert "--frozen" in sync_line, (
        f"deploy/{service}/Dockerfile uv sync must be --frozen; got: {sync_line!r}"
    )
    assert "--no-dev" in sync_line, (
        f"deploy/{service}/Dockerfile uv sync must be --no-dev; got: {sync_line!r}"
    )
    assert re.search(r"--package\s+\S+", sync_line), (
        f"deploy/{service}/Dockerfile uv sync must pin a --package <svc>; got: {sync_line!r}"
    )


@pytest.mark.parametrize("service", sorted(PER_SERVICE))
@pytest.mark.deployment
def test_per_service_dockerfile_nonroot_uid_1001_with_home(service):
    """Each per-service image runs as non-root uid 1001 appuser with USER appuser and HOME set."""
    dock = _service_dockerfile(service)
    assert dock.strip(), f"deploy/{service}/Dockerfile is missing (per-service image not built)"

    # Non-root user (uid 1001, appuser) created in THIS file.
    assert re.search(r"useradd\b[^\n]*-u\s+1001\b[^\n]*\bappuser\b", dock), (
        f"deploy/{service}/Dockerfile must create a non-root user `useradd -m -u 1001 appuser`"
    )

    # The final effective USER in THIS file must be appuser, never root.
    user_dirs = re.findall(r"^\s*USER\s+(\S+)", dock, re.M)
    assert user_dirs, (
        f"deploy/{service}/Dockerfile must set `USER appuser` (no USER => runs as root)"
    )
    assert user_dirs[-1] == "appuser", (
        f"deploy/{service}/Dockerfile final USER must be `appuser`, never root; got {user_dirs}"
    )

    # HOME set (=/home/appuser) — the Claude Agent SDK writes to $HOME.
    assert re.search(r"\bENV\b[^\n]*\bHOME\s*=\s*/home/appuser\b", dock), (
        f"deploy/{service}/Dockerfile ENV must set HOME=/home/appuser (Claude Agent SDK writes to $HOME)"
    )


@pytest.mark.parametrize("service", sorted(PER_SERVICE))
@pytest.mark.deployment
def test_per_service_dockerfile_sandbox_image_hash_label(service):
    """Each per-service image carries a proxy.sandbox-image-hash LABEL from the SANDBOX_IMAGE_HASH ARG."""
    dock = _service_dockerfile(service)
    assert dock.strip(), f"deploy/{service}/Dockerfile is missing (per-service image not built)"

    assert re.search(r"^\s*ARG\s+SANDBOX_IMAGE_HASH\b", dock, re.M), (
        f"deploy/{service}/Dockerfile must declare `ARG SANDBOX_IMAGE_HASH` (provenance coordinate)"
    )
    label = re.search(r"LABEL\s+proxy\.sandbox-image-hash\s*=\s*(\S+)", dock)
    assert label, (
        f"deploy/{service}/Dockerfile must carry `LABEL proxy.sandbox-image-hash=...` (provenance)"
    )
    label_val = label.group(1).strip("\"'")
    assert re.search(r"\$\{?SANDBOX_IMAGE_HASH\}?", label_val), (
        f"deploy/{service}/Dockerfile proxy.sandbox-image-hash LABEL must be driven by "
        f"$SANDBOX_IMAGE_HASH (not hardcoded); got {label_val!r}"
    )


@pytest.mark.parametrize("service,module", sorted(PER_SERVICE.items()))
@pytest.mark.integration
def test_per_service_dockerfile_self_migrate_loop_then_exec_correct_module(service, module):
    """Each per-service CMD retry-loops alembic upgrade (30x5s) then exec's its OWN server module."""
    dock = _service_dockerfile(service)
    assert dock.strip(), f"deploy/{service}/Dockerfile is missing (per-service image not built)"

    # Bounded self-migrate retry loop: until alembic upgrade head; 30 attempts; sleep 5.
    assert re.search(r"until\s+alembic\s+upgrade\s+head", dock), (
        f"deploy/{service}/Dockerfile CMD must retry-loop `until alembic upgrade head`"
    )
    assert re.search(r"-ge\s+30\b", dock), (
        f"deploy/{service}/Dockerfile migrate retry must be bounded at 30 attempts (`[ $n -ge 30 ]`)"
    )
    assert re.search(r"sleep\s+5\b", dock), (
        f"deploy/{service}/Dockerfile migrate retry must sleep 5s between attempts (30x5s)"
    )

    # After the migration wins, THIS image exec's ITS OWN server module.
    assert re.search(rf"exec\s+python\s+-m\s+{re.escape(module)}\b", dock), (
        f"deploy/{service}/Dockerfile CMD must `exec python -m {module}` after migrate "
        f"(retry-then-serve, correct per-service module)"
    )
