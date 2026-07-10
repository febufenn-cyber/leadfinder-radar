"""Poll cycle (fetch -> filter -> dedup -> classify -> threshold -> surfaced lead)
and draft cycle (surfaced lead -> sonnet variants -> outbox approval card), decoupled."""

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
from app.pipeline import ClassifierBreaker, run_draft_cycle, run_poll_cycle
from tests.test_notify import make_post

GOOD_VARIANTS = [
    DraftVariant(variant="A", channel="comment", text="Two specific points."),
    DraftVariant(variant="B", channel="dm", text="Quick DM with one idea."),
]

PACK = OfferPack(
    name="testpack",
    keywords={"include": ["need a website"], "exclude": ["for free"]},
)


def fake_draft(variants):
    async def _draft(session, pack, row, score, lead_id):
        return variants

    return _draft


class CountingDraft:
    def __init__(self, variants):
        self.variants = variants
        self.calls = 0

    async def __call__(self, session, pack, row, score, lead_id):
        self.calls += 1
        return self.variants


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


async def surface_one_lead(db_factory, notifier=None) -> dict:
    """Run one poll cycle that surfaces exactly one 82-fit lead."""
    return await run_poll_cycle(
        session_factory=db_factory,
        notifier=notifier or SpyNotifier(),
        packs=[PACK],
        poll_fn=fake_poll(sample_posts()),
        classify_fn=SpyClassifier(make_score(82)),
    )


# ---------------------------------------------------------------- poll cycle


async def test_poll_cycle_stores_scores_and_creates_surfaced_lead(db_factory):
    notifier = SpyNotifier()
    summary = await surface_one_lead(db_factory, notifier)
    assert summary["fetched"] == 4
    assert summary["matched"] == 1
    assert summary["new"] == 1
    assert summary["classified"] == 1
    assert summary["surfaced"] == 1
    assert summary["alerted"] == 0  # drafting/push is the draft cycle's job
    assert notifier.sent == [] and notifier.cards == []

    async with db_factory() as session:
        post = (await session.execute(select(RawPost))).scalars().one()
        assert post.fit_score == 82
        assert post.score["urgency"] == "now"
        assert post.alerted_at is None
        lead = (await session.execute(select(Lead))).scalars().one()
        assert lead.status == "surfaced"
        assert lead.draft_attempts == 0


async def test_below_threshold_stored_but_not_surfaced(db_factory):
    notifier = SpyNotifier()
    summary = await run_poll_cycle(
        session_factory=db_factory,
        notifier=notifier,
        packs=[PACK],
        poll_fn=fake_poll(sample_posts()),
        classify_fn=SpyClassifier(make_score(30)),
    )
    assert summary["surfaced"] == 0
    assert summary["suppressed"] == 1
    assert notifier.sent == []
    async with db_factory() as session:
        post = (await session.execute(select(RawPost))).scalars().one()
        assert post.fit_score == 30  # stored for eval/tuning (DESIGN §3.3)
        assert (await session.execute(select(Lead))).scalars().all() == []


async def test_classifier_failure_alerts_unscored(db_factory):
    notifier = SpyNotifier()
    summary = await run_poll_cycle(
        session_factory=db_factory,
        notifier=notifier,
        packs=[PACK],
        poll_fn=fake_poll(sample_posts()),
        classify_fn=SpyClassifier(None),
        breaker=ClassifierBreaker(),  # isolate from the module singleton
    )
    assert summary["alerted"] == 1  # over-alerting beats losing a lead
    assert "UNSCORED" in notifier.sent[0]


async def test_second_cycle_is_all_dupes_and_skips_classifier(db_factory):
    posts = sample_posts()
    classifier = SpyClassifier(make_score(82))
    for _ in range(2):
        summary = await run_poll_cycle(
            session_factory=db_factory,
            notifier=SpyNotifier(),
            packs=[PACK],
            poll_fn=fake_poll(posts),
            classify_fn=classifier,
        )
    assert summary["new"] == 0
    assert classifier.calls == 1  # dupes never reach the LLM (token discipline)
    async with db_factory() as session:
        leads = (await session.execute(select(Lead))).scalars().all()
        assert len(leads) == 1  # no duplicate lead either


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
    )
    assert summary["matched"] == 0


# ---------------------------------------------------------------- draft cycle


async def test_draft_cycle_drafts_and_pushes_card(db_factory):
    await surface_one_lead(db_factory)
    notifier = SpyNotifier()
    summary = await run_draft_cycle(
        session_factory=db_factory,
        notifier=notifier,
        packs=[PACK],
        draft_fn=fake_draft(GOOD_VARIANTS),
    )
    assert summary["drafted"] == 1
    assert summary["pushed"] == 1
    assert len(notifier.cards) == 1
    card, buttons = notifier.cards[0]
    assert "82" in card and "Two specific points." in card
    assert buttons[0][0]["callback_data"].startswith("a:send:A:")

    async with db_factory() as session:
        lead = (await session.execute(select(Lead))).scalars().one()
        assert lead.status == "drafted"
        assert lead.approval_pushed_at is not None
        drafts = (await session.execute(select(Draft))).scalars().all()
        assert {d.variant for d in drafts} == {"A", "B"}
        post = (await session.execute(select(RawPost))).scalars().one()
        assert post.alerted_at is not None
        kinds = set((await session.execute(select(Event.kind))).scalars().all())
        assert "approval_pushed" in kinds


async def test_draft_failure_capped_then_fallback_alert(db_factory):
    await surface_one_lead(db_factory)
    notifier = SpyNotifier()
    failing = CountingDraft(None)
    for _ in range(4):  # one more cycle than the cap
        summary = await run_draft_cycle(
            session_factory=db_factory,
            notifier=notifier,
            packs=[PACK],
            draft_fn=failing,
        )
    assert failing.calls == 3  # capped — no infinite sonnet spend
    assert summary["drafted"] == 0
    assert len(notifier.sent) == 1  # single plain-card fallback on the final attempt
    assert "82" in notifier.sent[0]
    assert notifier.cards == []
    async with db_factory() as session:
        lead = (await session.execute(select(Lead))).scalars().one()
        assert lead.status == "surfaced"
        assert lead.draft_attempts == 3
        kinds = (await session.execute(select(Event.kind))).scalars().all()
        assert "draft_gave_up" in kinds


async def test_push_failure_retries_next_cycle_without_duplicates(db_factory):
    await surface_one_lead(db_factory)
    drafter = CountingDraft(GOOD_VARIANTS)
    # cycle 1: telegram down -> drafted, card unpushed
    s1 = await run_draft_cycle(
        session_factory=db_factory, notifier=SpyNotifier(ok=False),
        packs=[PACK], draft_fn=drafter,
    )
    assert s1["drafted"] == 1 and s1["pushed"] == 0
    # cycle 2: telegram back -> outbox retry delivers exactly one card, no re-draft
    notifier = SpyNotifier()
    s2 = await run_draft_cycle(
        session_factory=db_factory, notifier=notifier, packs=[PACK], draft_fn=drafter,
    )
    assert drafter.calls == 1
    assert s2["pushed"] == 1
    assert len(notifier.cards) == 1
    # cycle 3: nothing left
    notifier3 = SpyNotifier()
    await run_draft_cycle(
        session_factory=db_factory, notifier=notifier3, packs=[PACK], draft_fn=drafter,
    )
    assert notifier3.cards == []


# ---------------------------------------------------------------- circuit breaker


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
        breaker=breaker,
    )
    assert breaker.is_open()
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
        poll_fn=fake_poll(many_matching_posts(3)), classify_fn=classifier, breaker=breaker,
    )
    assert classifier.calls == 1  # first call opened it; rest deferred
    await run_poll_cycle(
        session_factory=db_factory, notifier=notifier, packs=[PACK],
        poll_fn=fake_poll([]), classify_fn=classifier, breaker=breaker,
    )
    assert classifier.calls == 2  # exactly one probe on the stored backlog


async def test_recovery_drains_backlog_into_surfaced_leads(db_factory):
    notifier = SpyNotifier()
    breaker = ClassifierBreaker(threshold=1)
    classifier = FlakyClassifier(recover_after=1, score=make_score(82))
    posts = many_matching_posts(4)
    # cycle 1: first classify fails -> breaker opens, all 4 stored unclassified
    s1 = await run_poll_cycle(
        session_factory=db_factory, notifier=notifier, packs=[PACK],
        poll_fn=fake_poll(posts), classify_fn=classifier, breaker=breaker,
    )
    assert s1["deferred"] == 4 and s1["surfaced"] == 0
    # cycle 2: probe succeeds (breaker closes), probe post surfaced
    s2 = await run_poll_cycle(
        session_factory=db_factory, notifier=notifier, packs=[PACK],
        poll_fn=fake_poll(posts), classify_fn=classifier, breaker=breaker,
    )
    assert not breaker.is_open()
    assert s2["surfaced"] == 1
    # cycle 3: breaker closed -> backlog drains fully
    s3 = await run_poll_cycle(
        session_factory=db_factory, notifier=notifier, packs=[PACK],
        poll_fn=fake_poll([]), classify_fn=classifier, breaker=breaker,
    )
    assert s3["classified"] == 3 and s3["surfaced"] == 3
    async with db_factory() as session:
        leads = (await session.execute(select(Lead))).scalars().all()
        assert len(leads) == 4  # every burst post became a surfaced lead
        kinds = set((await session.execute(select(Event.kind))).scalars().all())
        assert "classifier_breaker_closed" in kinds
