"""Alembic environment — async engine, URL from app settings."""

import asyncio
import logging

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

import app.models.event  # noqa: F401 — register tables on Base.metadata
import app.models.draft  # noqa: F401
import app.models.draft_revision  # noqa: F401
import app.models.halt  # noqa: F401
import app.models.lead  # noqa: F401
import app.models.llm_call  # noqa: F401
import app.models.mute  # noqa: F401
import app.models.send  # noqa: F401
import app.models.raw_post  # noqa: F401
import app.models.review  # noqa: F401
from app.core.config import get_settings
from app.db.base import Base

logging.basicConfig(level=logging.INFO)

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().DATABASE_URL)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
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
    asyncio.run(run_async_migrations())
