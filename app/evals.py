"""M5 evaluation metrics for classifier quality, outcomes, edits, latency, and cost."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from difflib import SequenceMatcher
from statistics import median

from sqlalchemy import select

from app.models.draft import Draft
from app.models.event import Event
from app.models.lead import Lead
from app.models.llm_call import LlmCall
from app.models.raw_post import RawPost
from app.models.review import ReviewLabel
from app.packs import OfferPack

_WORKED = {"sent", "replied", "conversation", "won", "lost", "no_response"}
_CONVERSATION = {"conversation", "won"}


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _blank_review() -> dict:
    return {
        "reviewed": 0,
        "skipped": 0,
        "demand": 0,
        "not_demand": 0,
        "tp": 0,
        "fp": 0,
        "fn": 0,
        "tn": 0,
        "precision": None,
        "recall": None,
    }


def _blank_outcomes() -> dict:
    return {
        "leads": 0,
        "worked": 0,
        "replied": 0,
        "conversations": 0,
        "won": 0,
        "reply_rate": None,
        "conversation_rate": None,
        "win_rate": None,
    }


def _finish_outcomes(row: dict) -> dict:
    row["reply_rate"] = _rate(row["replied"], row["worked"])
    row["conversation_rate"] = _rate(row["conversations"], row["worked"])
    row["win_rate"] = _rate(row["won"], row["worked"])
    return row


async def build_eval_snapshot(session, packs: list[OfferPack]) -> dict:
    """Build an all-time M5 snapshot using only auditable database evidence."""
    pack_names = {pack.name for pack in packs}

    labels = (await session.execute(select(ReviewLabel))).scalars().all()
    pack_names.update(label.pack for label in labels)
    reviews = {name: _blank_review() for name in sorted(pack_names)}
    for label in labels:
        row = reviews.setdefault(label.pack, _blank_review())
        if label.label == "skip":
            row["skipped"] += 1
            continue
        if label.label not in {"demand", "not_demand"}:
            continue
        row["reviewed"] += 1
        actual = label.label == "demand"
        predicted = bool(label.predicted_positive)
        row["demand" if actual else "not_demand"] += 1
        if predicted and actual:
            row["tp"] += 1
        elif predicted and not actual:
            row["fp"] += 1
        elif not predicted and actual:
            row["fn"] += 1
        else:
            row["tn"] += 1
    for row in reviews.values():
        row["precision"] = _rate(row["tp"], row["tp"] + row["fp"])
        row["recall"] = _rate(row["tp"], row["tp"] + row["fn"])

    lead_rows = (
        await session.execute(
            select(Lead, RawPost).join(RawPost, RawPost.id == Lead.raw_post_id)
        )
    ).all()
    pack_names.update(lead.pack for lead, _ in lead_rows)

    reply_events = (
        await session.execute(select(Event).where(Event.kind == "reply_detected"))
    ).scalars().all()
    replied_ids = {
        int(event.payload["lead_id"])
        for event in reply_events
        if isinstance(event.payload, dict) and str(event.payload.get("lead_id", "")).isdigit()
    }

    chosen_ids = {lead.chosen_draft_id for lead, _ in lead_rows if lead.chosen_draft_id}
    chosen = {}
    if chosen_ids:
        chosen = {
            draft.id: draft
            for draft in (
                await session.execute(select(Draft).where(Draft.id.in_(chosen_ids)))
            ).scalars().all()
        }

    outcomes = {name: _blank_outcomes() for name in sorted(pack_names)}
    variants: dict[str, dict] = {}
    for lead, _post in lead_rows:
        row = outcomes.setdefault(lead.pack, _blank_outcomes())
        row["leads"] += 1
        worked = lead.status in _WORKED
        replied = lead.id in replied_ids
        conversation = lead.status in _CONVERSATION
        won = lead.status == "won"
        row["worked"] += int(worked)
        row["replied"] += int(replied)
        row["conversations"] += int(conversation)
        row["won"] += int(won)

        draft = chosen.get(lead.chosen_draft_id)
        if draft is not None:
            key = f"{lead.pack}:{draft.variant}"
            variant = variants.setdefault(
                key,
                {
                    "pack": lead.pack,
                    "variant": draft.variant,
                    "channel": draft.channel,
                    **_blank_outcomes(),
                },
            )
            variant["leads"] += 1
            variant["worked"] += int(worked)
            variant["replied"] += int(replied)
            variant["conversations"] += int(conversation)
            variant["won"] += int(won)

    for row in outcomes.values():
        _finish_outcomes(row)
    for row in variants.values():
        _finish_outcomes(row)

    gold = (
        await session.execute(
            select(Draft).where(Draft.is_gold.is_(True), Draft.edited_text.is_not(None))
        )
    ).scalars().all()
    similarities = [
        SequenceMatcher(None, draft.text.strip(), (draft.edited_text or "").strip()).ratio()
        for draft in gold
        if draft.text.strip() and (draft.edited_text or "").strip()
    ]

    posts = (await session.execute(select(RawPost))).scalars().all()
    alert_latencies = [
        max(0.0, (post.alerted_at - post.created_at).total_seconds() / 60)
        for post in posts
        if post.alerted_at is not None and post.created_at is not None
    ]
    calls = (await session.execute(select(LlmCall))).scalars().all()
    total_cost = sum((call.cost_usd or Decimal("0")) for call in calls)
    surfaced_count = len(lead_rows)

    return {
        "generated_at": datetime.now(UTC),
        "reviews": reviews,
        "outcomes": outcomes,
        "variants": sorted(variants.values(), key=lambda row: (row["pack"], row["variant"])),
        "edits": {
            "gold_samples": len(gold),
            "average_similarity": round(sum(similarities) / len(similarities), 4)
            if similarities
            else None,
            "average_change": round(1 - (sum(similarities) / len(similarities)), 4)
            if similarities
            else None,
        },
        "ops": {
            "post_to_alert_p50_minutes": round(float(median(alert_latencies)), 2)
            if alert_latencies
            else None,
            "llm_calls": len(calls),
            "llm_cost_usd": round(float(total_cost), 6),
            "cost_per_surfaced_lead_usd": round(float(total_cost) / surfaced_count, 6)
            if surfaced_count
            else None,
            "surfaced_leads": surfaced_count,
        },
    }
