"""M5 weekly classifier-review flow."""

from datetime import UTC, datetime

from sqlalchemy import select

from app.db.session import insert_new_posts
from app.models.event import Event
from app.models.review import ReviewLabel
from app.packs import OfferPack, PackKeywords
from app.review import (
    format_review_card,
    record_review,
    review_candidates,
    run_weekly_review_nudge,
)
from tests.conftest import make_post_row


def pack(name: str = "robofox_web", threshold: int = 65) -> OfferPack:
    return OfferPack(name=name, threshold=threshold, keywords=PackKeywords(include=["need"]))


async def scored_post(session, *, external_id: str, score: int, pack_name: str = "robofox_web"):
    (post,) = await insert_new_posts(
        session,
        [
            make_post_row(
                external_id=external_id,
                pack=pack_name,
                fit_score=score,
                score={
                    "fit_score": score,
                    "one_line_summary": "<buyer> needs help",
                },
                classified_at=datetime.now(UTC),
            )
        ],
    )
    await session.commit()
    return post


async def test_candidates_are_unlabeled_subthreshold_and_round_robin(db_session):
    a = await scored_post(db_session, external_id="a", score=64)
    await scored_post(db_session, external_id="b", score=70)
    c = await scored_post(db_session, external_id="c", score=40, pack_name="zervvo")
    db_session.add(
        ReviewLabel(
            raw_post_id=a.id,
            pack=a.pack,
            label="not_demand",
            fit_score=a.fit_score,
            threshold=65,
            predicted_positive=False,
        )
    )
    await db_session.commit()

    rows = await review_candidates(
        db_session,
        [pack(), pack("zervvo", 60)],
        limit=10,
    )
    assert [row.id for row in rows] == [c.id]


async def test_record_review_updates_idempotently_and_audits(db_session):
    post = await scored_post(db_session, external_id="review-me", score=61)
    first = await record_review(db_session, post.id, "demand", threshold=65)
    second = await record_review(db_session, post.id, "not_demand", threshold=65)

    assert first.id == second.id
    assert second.label == "not_demand"
    labels = (await db_session.execute(select(ReviewLabel))).scalars().all()
    assert len(labels) == 1
    events = (
        await db_session.execute(
            select(Event).where(Event.kind == "review_labeled").order_by(Event.id)
        )
    ).scalars().all()
    assert [event.payload["label"] for event in events] == ["demand", "not_demand"]
    assert events[1].payload["previous_label"] == "demand"


async def test_review_card_escapes_untrusted_post_text(db_session):
    post = await scored_post(db_session, external_id="unsafe", score=50)
    post.title = "<script>alert(1)</script>"
    post.text = "<b>need help</b>"
    card, buttons = format_review_card(post, 65)
    assert "<script>" not in card
    assert "&lt;script&gt;" in card
    assert buttons[0][0]["callback_data"] == f"r:demand:{post.id}"


class FakeNotifier:
    def __init__(self):
        self.messages = []

    async def send(self, text):
        self.messages.append(text)
        return True


async def test_weekly_nudge_sends_once(db_factory):
    async with db_factory() as session:
        await scored_post(session, external_id="weekly", score=50)
    notifier = FakeNotifier()
    now = datetime(2026, 7, 13, 4, 0, tzinfo=UTC)

    first = await run_weekly_review_nudge(
        session_factory=db_factory,
        notifier=notifier,
        packs=[pack()],
        now=now,
    )
    second = await run_weekly_review_nudge(
        session_factory=db_factory,
        notifier=notifier,
        packs=[pack()],
        now=now,
    )

    assert first == {"available": 1, "sent": 1}
    assert second == {"available": 0, "sent": 0}
    assert len(notifier.messages) == 1
