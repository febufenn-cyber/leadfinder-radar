"""Drafting service (DESIGN §3.5): prompts, validation, hard-rule enforcement."""

from sqlalchemy import select

from app.classify import LeadScore
from app.draft import (
    BANNED_OPENERS,
    DraftVariant,
    build_draft_prompts,
    draft_lead,
    enforce_rules,
    load_persona,
)
from app.models.event import Event
from app.packs import OfferPack
from tests.conftest import make_post_row

PACK = OfferPack(
    name="robofox_web",
    description="Websites for small businesses.",
    keywords={"include": ["need a website"]},
    community_rules={"forhire": "Direct pitch acceptable here."},
)

SCORE = LeadScore(
    is_demand_post=True,
    offer_pack="robofox_web",
    intent="explicit_request",
    buyer_type="business_owner",
    budget_signal="stated",
    urgency="now",
    disqualifiers=[],
    fit_score=85,
    one_line_summary="Bakery owner wants a $500 website",
)

GOOD_PAYLOAD = {
    "variants": [
        {
            "variant": "A",
            "channel": "comment",
            "text": "For a bakery site with ordering, look at whether you need real-time inventory or just a menu with a WhatsApp order button — the second is far cheaper to run.",
            "risk_flags": [],
        },
        {
            "variant": "B",
            "channel": "dm",
            "text": "Saw your bakery post — a menu-plus-WhatsApp-orders site fits your budget. Happy to sketch what that would look like if useful.",
            "risk_flags": [],
        },
    ]
}


class FakeRunner:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def run_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.payload


def test_persona_loads_empty_starter():
    persona = load_persona("robofox_web")
    assert persona["facts"] == []
    assert load_persona("no_such_pack") == {"facts": [], "availability_line": ""}


def test_prompts_forbid_claims_without_persona_and_carry_rules():
    system, user = build_draft_prompts(PACK, load_persona("robofox_web"),
                                       make_post_row(community="forhire"), SCORE)
    assert "zero claims" in system.lower() or "no persona facts" in system.lower()
    assert "Direct pitch acceptable here." in system
    assert "Great question" in system  # banned openers listed
    assert "untrusted_post_data" in user
    assert "bakery" in user


def test_conservative_default_rule_for_unknown_community():
    system, _ = build_draft_prompts(PACK, load_persona("robofox_web"),
                                    make_post_row(community="randomsub"), SCORE)
    assert "assume self-promotion is NOT allowed" in system


def test_enforce_rules_flags_overlong_and_banned_openers():
    long_comment = DraftVariant(variant="A", channel="comment", text="word " * 130, risk_flags=[])
    assert "over_length" in enforce_rules(long_comment).risk_flags
    long_dm = DraftVariant(variant="B", channel="dm", text="word " * 90, risk_flags=[])
    assert "over_length" in enforce_rules(long_dm).risk_flags
    opener = DraftVariant(
        variant="A", channel="comment", text="Great question! Consider a static site.", risk_flags=[]
    )
    assert "banned_opener" in enforce_rules(opener).risk_flags
    ok = DraftVariant(variant="A", channel="comment", text="Short and specific.", risk_flags=[])
    assert enforce_rules(ok).risk_flags == []


async def test_draft_lead_success(db_session):
    runner = FakeRunner(GOOD_PAYLOAD)
    variants = await draft_lead(runner, db_session, PACK, make_post_row(), SCORE, lead_id=3)
    assert len(variants) == 2
    assert variants[0].variant == "A"
    assert runner.calls[0]["tier"] == "standard"  # DESIGN §3.5: tier standard
    assert runner.calls[0]["purpose"] == "draft"


async def test_draft_lead_failure_returns_none_and_logs_event(db_session):
    runner = FakeRunner({"variants": "nope"})
    variants = await draft_lead(runner, db_session, PACK, make_post_row(), SCORE, lead_id=3)
    assert variants is None
    kinds = (await db_session.execute(select(Event.kind))).scalars().all()
    assert "draft_failed" in kinds


def test_banned_openers_sane():
    assert "great question" in [b.lower() for b in BANNED_OPENERS]
