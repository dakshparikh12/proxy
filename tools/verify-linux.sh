#!/usr/bin/env bash
# Reproducible green gate for the code_intel target environment.
#
# WHY THIS EXISTS
# `harness/verify.sh` is the sole arbiter (exit 0 == green). Two of its criteria
# are satisfiable ONLY on the real code_intel runtime, not on a read-only-`/`
# macOS dev host:
#   * AC-M2-001 (tests/test_m2_clone.py) asserts the clone path literally starts
#     with `/tenants/<tenant>/` — the per-tenant encrypted volume mount from
#     product/v0-spec/01-CODE-INTELLIGENCE.md:111 / CANONICAL-DECISIONS §12.2.
#     macOS SIP forbids creating `/tenants` at `/`; a Linux root container has it.
#   * The suite's Doc-00 substrate tests need a real Postgres and ripgrep (`rg`).
#
# This runs the UNMODIFIED verify.sh inside a Linux root container where
# `/tenants` is writable, Postgres + ripgrep are installed, and the workspace
# venv is rebuilt for Linux. No product code, no sealed test, and no threshold
# is changed — this only provides the environment the spec assumes. It is the
# "code-complete path" the acceptance adjudication prescribes.
#
# USAGE:  bash tools/verify-linux.sh
# Requires: a running Docker daemon. The repo is copied (not mounted rw) into a
# builder-owned dir inside the container, so the host checkout is never mutated.
set -uo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
IMAGE="ghcr.io/astral-sh/uv:python3.12-bookworm"

docker run --rm \
  -v "$REPO":/work:ro \
  -w /work \
  "$IMAGE" bash -euo pipefail -c '
    export UV_PROJECT_ENVIRONMENT=/opt/venv DEBIAN_FRONTEND=noninteractive
    apt-get update -qq >/dev/null
    apt-get install -y -qq git postgresql-15 ripgrep >/dev/null   # conftest finds /usr/lib/postgresql/15/bin
    useradd -m builder                                            # initdb/postgres refuse to run as root
    mkdir -p /build /tenants
    tar -C /work --exclude=./.venv --exclude=./node_modules --exclude=./.git/hooks -cf - . | tar -C /build -xf -
    chown -R builder:builder /build /tenants
    uv venv /opt/venv >/dev/null 2>&1
    uv pip install --python /opt/venv -q -r /build/tools/linux-verify-requirements.txt
    uv pip install --python /opt/venv -q -e /build/libs/contracts
    chown -R builder:builder /opt/venv
    runuser -u builder -- bash -lc "
      export PROXY_PY=/opt/venv/bin/python
      export PATH=/usr/lib/postgresql/15/bin:/opt/venv/bin:\$PATH
      cd /build && bash harness/verify.sh
    "
  '
