#!/usr/bin/env bash
# Reproducible verification env: provisions the local test DB (Cloud SQL shape -> localhost:5432),
# migrates it, exports .env + all integration/eval flags, then execs the given command.
# Usage:  build/setup-test-env.sh .venv/bin/python -m pytest tests/doc03 -q
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"; V=.venv/bin
read U P D < <($V/python -c "from dotenv import dotenv_values;import re;m=re.match(r'postgresql://([^:]+):([^@]+)@[^/]*/([^?]+)',dotenv_values('.env')['DATABASE_URL']);print(m.group(1),m.group(2),m.group(3))")
psql -h localhost -p 5432 -U postgres -d postgres -tc "SELECT 1 FROM pg_roles WHERE rolname='$U'" 2>/dev/null | grep -q 1 || psql -h localhost -p 5432 -U postgres -d postgres -c "CREATE ROLE $U LOGIN PASSWORD '$P' SUPERUSER" >/dev/null 2>&1
psql -h localhost -p 5432 -U postgres -d postgres -tc "SELECT 1 FROM pg_database WHERE datname='$D'" 2>/dev/null | grep -q 1 || psql -h localhost -p 5432 -U postgres -d postgres -c "CREATE DATABASE $D OWNER $U" >/dev/null 2>&1
LOCALDB="postgresql://$U:$P@localhost:5432/$D"
while IFS= read -r l; do eval "export $l"; done < <($V/python -c "from dotenv import dotenv_values;import shlex;[print(f'{k}={shlex.quote(v)}') for k,v in dotenv_values('.env').items() if v is not None]")
export DATABASE_URL="$LOCALDB" TEST_DATABASE_URL="$LOCALDB" DOC03_STORE_SPEC_DB="$LOCALDB"
export DOC03_STORE_GCS_BUCKET="${GCS_BUCKET:-}" DOC03_CLOSE_GCS_BUCKET="${GCS_BUCKET:-}"
# The unversioned bucket is a SEPARATE, real bucket with Object Versioning OFF (AC-STORE-09-NEG needs
# history-collapse, which the versioned notes bucket can never show). Honour the value already exported
# from .env (DOC03_STORE_GCS_BUCKET_UNVERSIONED); if unset the test skips cleanly. NEVER alias it to the
# versioned GCS_BUCKET — that made the negative test assert the opposite of the truth and fail.
export DOC03_STORE_GCS_BUCKET_UNVERSIONED="${DOC03_STORE_GCS_BUCKET_UNVERSIONED:-}"
$V/alembic upgrade head >/dev/null 2>&1 || true
exec "$@"
