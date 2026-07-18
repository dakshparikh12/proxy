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


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL must be set to run migrations")
    # SQLAlchemy default driver for postgresql:// is psycopg2; force psycopg v3.
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    elif url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://") :]
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
