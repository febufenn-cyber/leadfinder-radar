"""End-to-end poll cycle with injected poller/notifier: filter -> dedup -> store -> alert -> events."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models.event import Event
from app.models.raw_post import RawPost
from app.packs import OfferPack
from app.pipeline import run_poll_cycle
from tests.test_notify import make_post

PACK = OfferPack(
    name="testpack",
    keywords={"include": ["need a website"], "exclude": ["for free"]},
)


class SpyNotifier:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.sent: list[str] = []

    async def send(self, text: str) -> bool:
        self.sent.append(text)
        return self.ok


def fake_poll(posts):
    async def _poll(pack, client):
        return list(posts)

    return _poll


def sample_posts():
    return [
        make_post(),  # fresh, matches "need a website"
        make_post(external_id="t3_nomatch", title="Growing my gym", text="200 members now"),
        make_post(
            external_id="t3_stale",
            created_at=datetime.now(UTC) - timedelta(minutes=999),
        ),
        make_post(external_id="t3_excl", text="I need a website but only for free"),
    ]


async def test_cycle_stores_matches_alerts_and_logs_events(db_factory):
    notifier = SpyNotifier()
    summary = await run_poll_cycle(
        session_factory=db_factory,
        notifier=notifier,
        packs=[PACK],
        poll_fn=fake_poll(sample_posts()),
    )
    assert summary["fetched"] == 4
    assert summary["matched"] == 1
    assert summary["new"] == 1
    assert summary["alerted"] == 1
    assert len(notifier.sent) == 1 and "testpack" in notifier.sent[0]

    async with db_factory() as session:
        posts = (await session.execute(select(RawPost))).scalars().all()
        assert len(posts) == 1
        assert posts[0].pack == "testpack"
        assert posts[0].matched_keywords == ["need a website"]
        assert posts[0].alerted_at is not None
        kinds = set((await session.execute(select(Event.kind))).scalars().all())
        assert kinds == {"alert_sent", "poll_cycle"}


async def test_second_cycle_is_all_dupes(db_factory):
    posts = sample_posts()
    for _ in range(2):
        notifier = SpyNotifier()
        summary = await run_poll_cycle(
            session_factory=db_factory,
            notifier=notifier,
            packs=[PACK],
            poll_fn=fake_poll(posts),
        )
    assert summary["new"] == 0
    assert summary["alerted"] == 0
    assert notifier.sent == []


async def test_failed_alert_keeps_post_unalerted(db_factory):
    summary = await run_poll_cycle(
        session_factory=db_factory,
        notifier=SpyNotifier(ok=False),
        packs=[PACK],
        poll_fn=fake_poll(sample_posts()),
    )
    assert summary["new"] == 1
    assert summary["alerted"] == 0
    async with db_factory() as session:
        post = (await session.execute(select(RawPost))).scalars().one()
        assert post.alerted_at is None
        kinds = set((await session.execute(select(Event.kind))).scalars().all())
        assert "alert_failed" in kinds
