"""End-to-end poll cycle with injected poller/notifier/classifier:
filter -> dedup -> store -> classify -> threshold gate -> alert -> events."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.classify import LeadScore
from app.draft import DraftVariant
from app.models.draft import Draft
from app.models.event import Event
from app.models.lead import Lead
from app.models.mute import Mute
from app.models.raw_post import RawPost
from app.packs import OfferPack
from app.pipeline import ClassifierBreaker, run_poll_cycle
from tests.test_notify import make_post

GOOD_VARIANTS = [
    DraftVariant(variant="A", channel="comment", text="Two specific points."),
    DraftVariant(variant="B", channel="dm", text="Quick DM with one idea."),
]


def fake_draft(variants):
    async def _draft(session, pack, row, score, lead_id):
        return variants

    return _draft

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


class FlakyClassifier:
    """Returns None until `recover_after` calls, then succeeds."""

    def __init__(self, recover_after: int, score: LeadScore):
        self.recover_after = recover_after
        self.score = score
        self.calls = 0

    async def __call__(self, session, pack, row, raw_post_id):
        self.calls += 1
        return self.score if self.calls > self.recover_after else None


class SpyNotifier:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.sent: list[str] = []
        self.cards: list[tuple[str, list]] = []

    async def send(self, text: str) -> bool:
        self.sent.append(text)
        return self.ok

    async def send_with_buttons(self, text: str, buttons: list) -> bool:
        self.cards.append((text, buttons))
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


async def test_cycle_stores_scores_drafts_and_pushes_approval_card(db_factory):
    notifier = SpyNotifier()
    classifier = SpyClassifier(make_score(82))
    summary = await run_poll_cycle(
        session_factory=db_factory,
        notifier=notifier,
        packs=[PACK],
        poll_fn=fake_poll(sample_posts()),
        classify_fn=classifier,
        draft_fn=fake_draft(GOOD_VARIANTS),
    )
    assert summary["fetched"] == 4
    assert summary["matched"] == 1
    assert summary["new"] == 1
    assert summary["classified"] == 1
    assert summary["surfaced"] == 1
    assert summary["alerted"] == 1
    # approval card, not a plain alert
    assert notifier.sent == []
    assert len(notifier.cards) == 1
    card, buttons = notifier.cards[0]
    assert "82" in card and "Bakery owner wants a website" in card
    assert "Two specific points." in card
    assert buttons[0][0]["text"] == "Send A"
    assert buttons[0][0]["callback_data"].startswith("a:send:A:")

    async with db_factory() as session:
        post = (await session.execute(select(RawPost))).scalars().one()
        assert post.fit_score == 82
        assert post.alerted_at is not None
        lead = (await session.execute(select(Lead))).scalars().one()
        assert lead.status == "drafted"
        assert lead.approval_pushed_at is not None
        drafts = (await session.execute(select(Draft))).scalars().all()
        assert {d.variant for d in drafts} == {"A", "B"}
        kinds = set((await session.execute(select(Event.kind))).scalars().all())
        assert kinds == {"approval_pushed", "poll_cycle"}


async def test_below_threshold_stored_but_not_surfaced(db_factory):
    notifier = SpyNotifier()
    summary = await run_poll_cycle(
        session_factory=db_factory,
        notifier=notifier,
        packs=[PACK],
        poll_fn=fake_poll(sample_posts()),
        classify_fn=SpyClassifier(make_score(30)),
        draft_fn=fake_draft(GOOD_VARIANTS),
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
        draft_fn=fake_draft(GOOD_VARIANTS),
        breaker=ClassifierBreaker(),  # isolate from the module singleton
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
            draft_fn=fake_draft(GOOD_VARIANTS),
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
        draft_fn=fake_draft(GOOD_VARIANTS),
    )
    assert summary["new"] == 1
    assert summary["alerted"] == 0
    async with db_factory() as session:
        post = (await session.execute(select(RawPost))).scalars().one()
        assert post.alerted_at is None
        kinds = set((await session.execute(select(Event.kind))).scalars().all())
        assert "alert_failed" in kinds


async def test_push_failure_retries_next_cycle_without_duplicates(db_factory):
    kwargs = dict(
        session_factory=db_factory,
        packs=[PACK],
        classify_fn=SpyClassifier(make_score(82)),
        draft_fn=fake_draft(GOOD_VARIANTS),
    )
    # cycle 1: telegram down -> lead drafted, card unpushed
    s1 = await run_poll_cycle(
        notifier=SpyNotifier(ok=False), poll_fn=fake_poll(sample_posts()), **kwargs
    )
    assert s1["alerted"] == 0
    # cycle 2: telegram back, no new posts -> outbox retry delivers exactly one card
    notifier = SpyNotifier()
    s2 = await run_poll_cycle(notifier=notifier, poll_fn=fake_poll([]), **kwargs)
    assert s2["alerted"] == 1
    assert len(notifier.cards) == 1
    # cycle 3: nothing left to push
    notifier3 = SpyNotifier()
    await run_poll_cycle(notifier=notifier3, poll_fn=fake_poll([]), **kwargs)
    assert notifier3.cards == []
    async with db_factory() as session:
        lead = (await session.execute(select(Lead))).scalars().one()
        assert lead.approval_pushed_at is not None


async def test_draft_failure_falls_back_to_plain_scored_alert(db_factory):
    notifier = SpyNotifier()
    summary = await run_poll_cycle(
        session_factory=db_factory,
        notifier=notifier,
        packs=[PACK],
        poll_fn=fake_poll(sample_posts()),
        classify_fn=SpyClassifier(make_score(82)),
        draft_fn=fake_draft(None),
    )
    assert summary["alerted"] == 1
    assert notifier.cards == []  # no approval card
    assert "82" in notifier.sent[0]  # plain scored alert still reached the phone
    async with db_factory() as session:
        lead = (await session.execute(select(Lead))).scalars().one()
        assert lead.status == "surfaced"  # never advanced without drafts


async def test_muted_community_and_keyword_are_skipped(db_factory):
    async with db_factory() as session:
        session.add(Mute(kind="community", value="smallbusiness", pack="testpack"))
        session.add(Mute(kind="keyword", value="landing page", pack=None))
        await session.commit()
    posts = [
        make_post(),  # community smallbusiness -> muted
        make_post(external_id="t3_kw", community="startups",
                  title="Landing page advice?", text="I want a landing page for launch"),
    ]
    summary = await run_poll_cycle(
        session_factory=db_factory,
        notifier=SpyNotifier(),
        packs=[OfferPack(name="testpack",
                         keywords={"include": ["need a website", "landing page"]})],
        poll_fn=fake_poll(posts),
        classify_fn=SpyClassifier(make_score(82)),
        draft_fn=fake_draft(GOOD_VARIANTS),
    )
    assert summary["matched"] == 0
    assert summary["new"] == 0


def many_matching_posts(n: int):
    return [make_post(external_id=f"t3_burst{i}") for i in range(n)]


async def test_breaker_opens_after_three_failures_and_stops_alert_spam(db_factory):
    notifier = SpyNotifier()
    breaker = ClassifierBreaker(threshold=3)
    summary = await run_poll_cycle(
        session_factory=db_factory,
        notifier=notifier,
        packs=[PACK],
        poll_fn=fake_poll(many_matching_posts(6)),
        classify_fn=SpyClassifier(None),
        draft_fn=fake_draft(GOOD_VARIANTS),
        breaker=breaker,
    )
    assert breaker.is_open()
    # posts 1-2: sporadic UNSCORED alerts; post 3 trips the breaker (down notice);
    # posts 4-6 deferred silently
    unscored = [t for t in notifier.sent if "UNSCORED" in t]
    notices = [t for t in notifier.sent if "classifier appears down" in t]
    assert len(unscored) == 2
    assert len(notices) == 1
    assert summary["deferred"] == 4
    async with db_factory() as session:
        kinds = (await session.execute(select(Event.kind))).scalars().all()
        assert kinds.count("classifier_breaker_open") == 1


async def test_open_breaker_probes_once_per_cycle(db_factory):
    notifier = SpyNotifier()
    breaker = ClassifierBreaker(threshold=1)
    classifier = SpyClassifier(None)
    await run_poll_cycle(
        session_factory=db_factory, notifier=notifier, packs=[PACK],
        poll_fn=fake_poll(many_matching_posts(3)), classify_fn=classifier, draft_fn=fake_draft(GOOD_VARIANTS), breaker=breaker,
    )
    assert classifier.calls == 1  # first call opened it; rest deferred
    await run_poll_cycle(
        session_factory=db_factory, notifier=notifier, packs=[PACK],
        poll_fn=fake_poll([]), classify_fn=classifier, draft_fn=fake_draft(GOOD_VARIANTS), breaker=breaker,
    )
    assert classifier.calls == 2  # exactly one probe on the stored backlog


async def test_recovery_drains_backlog(db_factory):
    notifier = SpyNotifier()
    breaker = ClassifierBreaker(threshold=1)
    classifier = FlakyClassifier(recover_after=1, score=make_score(82))
    posts = many_matching_posts(4)
    # cycle 1: first classify fails -> breaker opens, all 4 stored unclassified
    s1 = await run_poll_cycle(
        session_factory=db_factory, notifier=notifier, packs=[PACK],
        poll_fn=fake_poll(posts), classify_fn=classifier, draft_fn=fake_draft(GOOD_VARIANTS), breaker=breaker,
    )
    assert s1["deferred"] == 4 and s1["alerted"] == 0
    # cycle 2: probe succeeds (breaker closes), probe post alerted
    s2 = await run_poll_cycle(
        session_factory=db_factory, notifier=notifier, packs=[PACK],
        poll_fn=fake_poll(posts), classify_fn=classifier, draft_fn=fake_draft(GOOD_VARIANTS), breaker=breaker,
    )
    assert not breaker.is_open()
    assert s2["alerted"] == 1
    # cycle 3: breaker closed -> backlog drains fully
    s3 = await run_poll_cycle(
        session_factory=db_factory, notifier=notifier, packs=[PACK],
        poll_fn=fake_poll([]), classify_fn=classifier, draft_fn=fake_draft(GOOD_VARIANTS), breaker=breaker,
    )
    assert s3["classified"] == 3 and s3["alerted"] == 3
    async with db_factory() as session:
        remaining = (
            await session.execute(select(RawPost).where(RawPost.classified_at.is_(None)))
        ).scalars().all()
        assert remaining == []
        kinds = set((await session.execute(select(Event.kind))).scalars().all())
        assert "classifier_breaker_closed" in kinds
