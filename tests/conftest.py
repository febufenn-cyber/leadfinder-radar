"""Shared fixtures. DB tests run against the tmpfs postgres-test container (:5433)."""

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://leadfinder:leadfinder@localhost:5433/leadfinder_test",
)


@pytest.fixture
async def db_session():
    """Fresh schema per test — engine bound to this test's event loop."""
    import app.models.event  # noqa: F401 — register tables on Base.metadata
    import app.models.raw_post  # noqa: F401
    from app.db.base import Base

    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def make_post_row(**overrides) -> dict:
    """A valid raw_posts row dict matching the DESIGN §2 adapter contract."""
    row = {
        "source": "reddit",
        "external_id": "t3_test01",
        "url": "https://www.reddit.com/r/smallbusiness/comments/test01/need_a_website/",
        "author_handle": "/u/shopowner42",
        "author_url": "https://www.reddit.com/user/shopowner42",
        "community": "smallbusiness",
        "title": "Need a website for my bakery",
        "text": "I need a website for my bakery, budget around $500.",
        "created_at": datetime.now(UTC),
        "pack": "robofox_web",
        "matched_keywords": ["need a website"],
        "raw": {"id": "t3_test01"},
    }
    row.update(overrides)
    return row
