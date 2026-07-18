"""Alembic environment for the Proxy Postgres substrate.

Reads the target DSN from ``DATABASE_URL`` (set by callers / the harness) and
normalises it onto the psycopg-v3 SQLAlchemy driver (the workspace uses psycopg
3, never psycopg2). Migrations are raw-SQL (``op.execute``) so the schema stays
one canonical DDL surface with no ORM model layer.
"""
from __future__ import annotations

import os

from alembic import context
from sqlalchemy import engine_from_config, pool


def _libpq_kv_to_url(dsn: str) -> str:
    """Convert a libpq keyword/value DSN (``user=… dbname=… host=…``) to a URL.

    ``psycopg`` connections expose their DSN via ``conn.info.dsn`` in this
    keyword/value form; SQLAlchemy's ``make_url`` only parses URL DSNs, so callers
    that hand us a live connection's DSN would otherwise fail to migrate.
    """
    from psycopg.conninfo import conninfo_to_dict

    params = conninfo_to_dict(dsn)
    user = str(params.get("user", ""))
    password = params.get("password")
    host = str(params.get("host", ""))
    port = str(params.get("port", ""))
    dbname = str(params.get("dbname", ""))
    auth = f"{user}:{password}" if password else user
    netloc = f"{host}:{port}" if port else host
    return f"postgresql+psycopg://{auth}@{netloc}/{dbname}"


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL must be set to run migrations")
    # SQLAlchemy default driver for postgresql:// is psycopg2; force psycopg v3.
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    elif url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://") :]
    elif "://" not in url:
        # A libpq keyword/value DSN (e.g. from psycopg ``conn.info.dsn``).
        url = _libpq_kv_to_url(url)
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = context.config.get_section(context.config.config_ini_section) or {}
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
