"""Engine/session factory + the dedup insert used by the pipeline."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models.raw_post import RawPost


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(get_settings().DATABASE_URL, pool_pre_ping=True)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def insert_new_posts(session: AsyncSession, rows: list[dict]) -> list[RawPost]:
    """Insert rows, silently skipping (source, external_id) duplicates (DESIGN §3.1).

    Returns only the rows that were actually new, as ORM objects. Does NOT commit:
    the caller owns the transaction, so insert + alerted_at + event rows land
    atomically — a crash mid-cycle re-surfaces the lead next cycle instead of
    silently losing it.
    """
    if not rows:
        return []
    stmt = (
        pg_insert(RawPost)
        .values(rows)
        .on_conflict_do_nothing(constraint="uq_raw_posts_source_external_id")
        .returning(RawPost)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
