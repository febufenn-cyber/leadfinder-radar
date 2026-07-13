"""M5 evaluation snapshot tests."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.db.session import insert_new_posts
from app.evals import build_eval_snapshot
from app.models.draft import Draft
from app.models.event import Event
from app.models.lead import Lead
from app.models.llm_call import LlmCall
from app.models.review import ReviewLabel
from app.packs import OfferPack, PackKeywords
from tests.conftest import make_post_row


def pack(name: str = "robofox_web", threshold: int = 65) -> OfferPack:
    return OfferPack(name=name, threshold=threshold, keywords=PackKeywords(include=["need"]))


async def add_post(session, external_id: str, *, score: int, minutes_to_alert: int):
    created = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
    (post,) = await insert_new_posts(
        session,
        [
            make_post_row(
                external_id=external_id,
                fit_score=score,
                score={"fit_score": score, "one_line_summary": "buyer request"},
                classified_at=created + timedelta(minutes=1),
                created_at=created,
            )
        ],
    )
    post.alerted_at = created + timedelta(minutes=minutes_to_alert)
    await session.flush()
    return post


async def test_eval_snapshot_confusion_outcomes_variants_edits_and_ops(db_session):
    won_post = await add_post(db_session, "won", score=88, minutes_to_alert=4)
    sent_post = await add_post(db_session, "sent", score=72, minutes_to_alert=6)
    false_negative = await add_post(db_session, "fn", score=50, minutes_to_alert=10)
    true_negative = await add_post(db_session, "tn", score=30, minutes_to_alert=8)

    won = Lead(raw_post_id=won_post.id, pack="robofox_web", status="won")
    sent = Lead(raw_post_id=sent_post.id, pack="robofox_web", status="sent")
    db_session.add_all([won, sent])
    await db_session.flush()

    draft_a = Draft(
        lead_id=won.id,
        variant="A",
        channel="comment",
        text="A generic opening and three long sentences.",
        edited_text="Three specific suggestions for your bakery website.",
        is_gold=True,
        risk_flags=[],
    )
    draft_b = Draft(
        lead_id=sent.id,
        variant="B",
        channel="dm",
        text="Short DM",
        risk_flags=[],
    )
    db_session.add_all([draft_a, draft_b])
    await db_session.flush()
    won.chosen_draft_id = draft_a.id
    sent.chosen_draft_id = draft_b.id

    db_session.add_all(
        [
            ReviewLabel(
                raw_post_id=won_post.id,
                pack="robofox_web",
                label="demand",
                fit_score=88,
                threshold=65,
                predicted_positive=True,
            ),
            ReviewLabel(
                raw_post_id=false_negative.id,
                pack="robofox_web",
                label="demand",
                fit_score=50,
                threshold=65,
                predicted_positive=False,
            ),
            ReviewLabel(
                raw_post_id=true_negative.id,
                pack="robofox_web",
                label="not_demand",
                fit_score=30,
                threshold=65,
                predicted_positive=False,
            ),
        ]
    )
    db_session.add(Event(kind="reply_detected", payload={"lead_id": won.id}))
    db_session.add_all(
        [
            LlmCall(
                purpose="classify",
                tier="fast",
                model="haiku",
                input_tokens=10,
                output_tokens=2,
                cached_input_tokens=0,
                cost_usd=Decimal("0.200000"),
                duration_ms=100,
                success=True,
                raw_post_id=won_post.id,
            ),
            LlmCall(
                purpose="draft",
                tier="standard",
                model="sonnet",
                input_tokens=20,
                output_tokens=5,
                cached_input_tokens=0,
                cost_usd=Decimal("0.300000"),
                duration_ms=200,
                success=True,
                raw_post_id=won_post.id,
            ),
        ]
    )
    await db_session.commit()

    snapshot = await build_eval_snapshot(db_session, [pack()])

    review = snapshot["reviews"]["robofox_web"]
    assert review | {"precision": review["precision"], "recall": review["recall"]}
    assert (review["tp"], review["fp"], review["fn"], review["tn"]) == (1, 0, 1, 1)
    assert review["precision"] == 1.0
    assert review["recall"] == 0.5

    outcomes = snapshot["outcomes"]["robofox_web"]
    assert outcomes["leads"] == 2
    assert outcomes["worked"] == 2
    assert outcomes["replied"] == 1
    assert outcomes["won"] == 1
    assert outcomes["reply_rate"] == 0.5

    variant_a = next(row for row in snapshot["variants"] if row["variant"] == "A")
    assert variant_a["replied"] == 1
    assert variant_a["won"] == 1
    assert snapshot["edits"]["gold_samples"] == 1
    assert 0 < snapshot["edits"]["average_change"] <= 1

    assert snapshot["ops"]["post_to_alert_p50_minutes"] == 7.0
    assert snapshot["ops"]["llm_cost_usd"] == 0.5
    assert snapshot["ops"]["cost_per_surfaced_lead_usd"] == 0.25


async def test_eval_snapshot_handles_empty_database(db_session):
    snapshot = await build_eval_snapshot(db_session, [pack()])
    assert snapshot["reviews"]["robofox_web"]["precision"] is None
    assert snapshot["outcomes"]["robofox_web"]["reply_rate"] is None
    assert snapshot["variants"] == []
    assert snapshot["edits"]["average_change"] is None
    assert snapshot["ops"]["cost_per_surfaced_lead_usd"] is None
