"""Alembic migration environment.

Async-aware. Reads ``CZ_MTG_DATABASE_URL`` so the same alembic config
works for tests (sqlite+aiosqlite), local dev, and prod (postgres+asyncpg).
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from cz_mtg_compare.db.config import DatabaseSettings
from cz_mtg_compare.db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Pull the URL from the env each invocation so secrets never live in
# alembic.ini. Falls back to in-memory sqlite for the migration sanity
# tests; production deployments set CZ_MTG_DATABASE_URL.
config.set_main_option("sqlalchemy.url", DatabaseSettings.from_env().url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Generate SQL without a live DB. Useful for review of pending migrations."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
