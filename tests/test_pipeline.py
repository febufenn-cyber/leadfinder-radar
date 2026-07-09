"""End-to-end poll cycle with injected poller/notifier/classifier:
filter -> dedup -> store -> classify -> threshold gate -> alert -> events."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.classify import LeadScore
from app.models.event import Event
from app.models.raw_post import RawPost
from app.packs import OfferPack
from app.pipeline import run_poll_cycle
from tests.test_notify import make_post

PACK = OfferPack(
    name="testpack",
    keywords={"include": ["need a website"], "exclude": ["for free"]},
)


def make_score(fit: int) -> LeadScore:
    return LeadScore(
        is_demand_post=True,
        offer_pack="testpack",
        intent="explicit_request",
        buyer_type="business_owner",
        budget_signal="stated",
        urgency="now",
        disqualifiers=[],
        fit_score=fit,
        one_line_summary="Bakery owner wants a website",
    )


class SpyClassifier:
    def __init__(self, score: LeadScore | None):
        self.score = score
        self.calls = 0

    async def __call__(self, session, pack, row, raw_post_id):
        self.calls += 1
        return self.score


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


async def test_cycle_stores_scores_alerts_and_logs_events(db_factory):
    notifier = SpyNotifier()
    classifier = SpyClassifier(make_score(82))
    summary = await run_poll_cycle(
        session_factory=db_factory,
        notifier=notifier,
        packs=[PACK],
        poll_fn=fake_poll(sample_posts()),
        classify_fn=classifier,
    )
    assert summary["fetched"] == 4
    assert summary["matched"] == 1
    assert summary["new"] == 1
    assert summary["classified"] == 1
    assert summary["surfaced"] == 1
    assert summary["alerted"] == 1
    assert len(notifier.sent) == 1 and "testpack" in notifier.sent[0]
    assert "82" in notifier.sent[0]  # scored card
    assert "Bakery owner wants a website" in notifier.sent[0]

    async with db_factory() as session:
        posts = (await session.execute(select(RawPost))).scalars().all()
        assert len(posts) == 1
        assert posts[0].fit_score == 82
        assert posts[0].score["urgency"] == "now"
        assert posts[0].classified_at is not None
        assert posts[0].alerted_at is not None
        kinds = set((await session.execute(select(Event.kind))).scalars().all())
        assert kinds == {"alert_sent", "poll_cycle"}


async def test_below_threshold_stored_but_not_surfaced(db_factory):
    notifier = SpyNotifier()
    summary = await run_poll_cycle(
        session_factory=db_factory,
        notifier=notifier,
        packs=[PACK],
        poll_fn=fake_poll(sample_posts()),
        classify_fn=SpyClassifier(make_score(30)),
    )
    assert summary["new"] == 1
    assert summary["surfaced"] == 0
    assert summary["suppressed"] == 1
    assert summary["alerted"] == 0
    assert notifier.sent == []
    async with db_factory() as session:
        post = (await session.execute(select(RawPost))).scalars().one()
        assert post.fit_score == 30  # stored for eval/tuning (DESIGN §3.3)
        assert post.alerted_at is None


async def test_classifier_failure_alerts_unscored(db_factory):
    notifier = SpyNotifier()
    summary = await run_poll_cycle(
        session_factory=db_factory,
        notifier=notifier,
        packs=[PACK],
        poll_fn=fake_poll(sample_posts()),
        classify_fn=SpyClassifier(None),
    )
    assert summary["alerted"] == 1  # over-alerting beats losing a lead
    assert "UNSCORED" in notifier.sent[0]
    async with db_factory() as session:
        post = (await session.execute(select(RawPost))).scalars().one()
        assert post.fit_score is None


async def test_second_cycle_is_all_dupes_and_skips_classifier(db_factory):
    posts = sample_posts()
    classifier = SpyClassifier(make_score(82))
    for _ in range(2):
        notifier = SpyNotifier()
        summary = await run_poll_cycle(
            session_factory=db_factory,
            notifier=notifier,
            packs=[PACK],
            poll_fn=fake_poll(posts),
            classify_fn=classifier,
        )
    assert summary["new"] == 0
    assert summary["alerted"] == 0
    assert notifier.sent == []
    assert classifier.calls == 1  # dupes never reach the LLM (token discipline)


async def test_failed_alert_keeps_post_unalerted(db_factory):
    summary = await run_poll_cycle(
        session_factory=db_factory,
        notifier=SpyNotifier(ok=False),
        packs=[PACK],
        poll_fn=fake_poll(sample_posts()),
        classify_fn=SpyClassifier(make_score(82)),
    )
    assert summary["new"] == 1
    assert summary["alerted"] == 0
    async with db_factory() as session:
        post = (await session.execute(select(RawPost))).scalars().one()
        assert post.alerted_at is None
        kinds = set((await session.execute(select(Event.kind))).scalars().all())
        assert "alert_failed" in kinds
