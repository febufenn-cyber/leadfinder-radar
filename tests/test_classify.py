"""Classifier: prompt build, LeadScore validation, graceful failure (DESIGN §3.3)."""

from pathlib import Path

from sqlalchemy import select

from app.classify import LeadScore, build_prompts, classify_post, load_fewshots
from app.models.event import Event
from app.packs import OfferPack
from tests.conftest import make_post_row

PACK = OfferPack(
    name="robofox_web",
    description="Websites for small businesses.",
    keywords={"include": ["need a website"]},
)

GOOD_SCORE = {
    "is_demand_post": True,
    "offer_pack": "robofox_web",
    "intent": "explicit_request",
    "buyer_type": "business_owner",
    "budget_signal": "stated",
    "urgency": "now",
    "disqualifiers": [],
    "fit_score": 82,
    "one_line_summary": "Bakery owner wants a $500 website",
}


class FakeRunner:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def run_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.payload


def test_leadscore_validates_and_bounds():
    score = LeadScore.model_validate(GOOD_SCORE)
    assert score.fit_score == 82
    assert LeadScore.model_validate(GOOD_SCORE | {"fit_score": 150}, strict=False) is not None


def test_build_prompts_contain_pack_and_post_and_schema():
    system, user = build_prompts(PACK, load_fewshots(PACK.name), make_post_row())
    assert "robofox_web" in system
    assert "fit_score" in system  # schema described
    assert "ONLY" in system  # json-only instruction
    assert "bakery" in user  # post text present
    assert "smallbusiness" in user  # community context


def test_fewshots_load_for_known_pack_and_empty_for_unknown():
    shots = load_fewshots("robofox_web")
    assert len(shots) >= 4
    assert load_fewshots("no_such_pack") == []


async def test_classify_post_success(db_session):
    runner = FakeRunner(GOOD_SCORE)
    score = await classify_post(runner, db_session, PACK, make_post_row(), raw_post_id=1)
    assert isinstance(score, LeadScore)
    assert score.fit_score == 82
    assert runner.calls[0]["tier"] == "fast"  # DESIGN §3.3: tier fast
    assert runner.calls[0]["purpose"] == "classify"


async def test_classify_post_invalid_payload_returns_none_and_logs_event(db_session):
    runner = FakeRunner({"fit_score": "not even close"})
    score = await classify_post(runner, db_session, PACK, make_post_row(), raw_post_id=1)
    assert score is None
    kinds = (await db_session.execute(select(Event.kind))).scalars().all()
    assert "classify_failed" in kinds


async def test_classify_post_runner_failure_returns_none(db_session):
    runner = FakeRunner(None)
    score = await classify_post(runner, db_session, PACK, make_post_row(), raw_post_id=1)
    assert score is None


def test_fit_score_clamped():
    assert LeadScore.model_validate(GOOD_SCORE | {"fit_score": 150}).fit_score == 100
    assert LeadScore.model_validate(GOOD_SCORE | {"fit_score": -5}).fit_score == 0


def test_pack_threshold_default():
    assert PACK.threshold == 65


async def test_classify_writes_llm_call_row_end_to_end(db_factory, db_session):
    """DoD: every LLM call logged — through the REAL run_json, not a stubbed one."""
    import json as _json

    from app.models.llm_call import LlmCall
    from sqlalchemy import select
    from tests.test_claude_runner import FakeRunner, ok_event

    runner = FakeRunner(result_event=ok_event(_json.dumps(GOOD_SCORE)))
    runner.audit_factory = db_factory
    score = await classify_post(runner, db_session, PACK, make_post_row(), raw_post_id=7)
    assert score is not None and score.fit_score == 82
    async with db_factory() as session:
        call = (await session.execute(select(LlmCall))).scalars().one()
        assert call.purpose == "classify"
        assert call.tier == "fast"
        assert call.raw_post_id == 7
        assert call.success is True


PACKS_DIR = Path(__file__).resolve().parent.parent / "packs"
