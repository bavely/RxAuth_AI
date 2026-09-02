"""Alembic environment.

The URL comes from `RXAUTH_DATABASE_URL` through the same `Settings` object the
application uses, so a migration cannot run against a database the application
would not talk to, and no connection string is committed.
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from rxauth_ai.config import get_settings
from rxauth_ai.persistence.tables import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError(
            "No RXAUTH_DATABASE_URL is set. Alembic reads it from the environment so that "
            "no connection string is committed:\n"
            "    export RXAUTH_DATABASE_URL=postgresql+psycopg://rxauth:rxauth@localhost:5432/rxauth"
        )
    return settings.database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
