"""Dedup: UNIQUE(source, external_id) + ON CONFLICT DO NOTHING (DESIGN §3.1)."""

from sqlalchemy import func, select

from app.db.session import insert_new_posts
from app.models.raw_post import RawPost
from tests.conftest import make_post_row


async def test_insert_returns_new_rows(db_session):
    inserted = await insert_new_posts(db_session, [make_post_row()])
    assert len(inserted) == 1
    assert inserted[0].external_id == "t3_test01"
    assert inserted[0].matched_keywords == ["need a website"]


async def test_duplicate_insert_is_noop(db_session):
    await insert_new_posts(db_session, [make_post_row()])
    again = await insert_new_posts(db_session, [make_post_row()])
    assert again == []
    count = await db_session.scalar(select(func.count()).select_from(RawPost))
    assert count == 1


async def test_same_external_id_different_source_both_kept(db_session):
    await insert_new_posts(db_session, [make_post_row()])
    other = await insert_new_posts(db_session, [make_post_row(source="hn")])
    assert len(other) == 1


async def test_mixed_batch_returns_only_new(db_session):
    await insert_new_posts(db_session, [make_post_row()])
    batch = [make_post_row(), make_post_row(external_id="t3_test02")]
    inserted = await insert_new_posts(db_session, batch)
    assert [p.external_id for p in inserted] == ["t3_test02"]
