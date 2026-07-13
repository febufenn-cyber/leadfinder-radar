"""M5 classifier-review workflow.

The owner labels a bounded sample of classifier decisions from Telegram. Labels
are evaluation evidence only: they never auto-change thresholds or prompts.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import desc, select

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.models.event import Event
from app.models.raw_post import RawPost
from app.models.review import ReviewLabel
from app.notify import get_notifier
from app.packs import OfferPack, load_packs

_REPO_ROOT = Path(__file__).resolve().parents[1]
_LABELS = {"demand", "not_demand", "skip"}


class ReviewError(ValueError):
    pass


def load_review_packs() -> list[OfferPack]:
    settings = get_settings()
    path = Path(settings.PACKS_DIR)
    if not path.is_absolute():
        path = _REPO_ROOT / path
    return load_packs(path)


def _thresholds(packs: list[OfferPack]) -> dict[str, int]:
    return {pack.name: pack.threshold for pack in packs}


async def review_candidates(
    session,
    packs: list[OfferPack],
    *,
    limit: int = 10,
    pack_name: str | None = None,
) -> list[RawPost]:
    """Return recent, unlabeled, sub-threshold posts for false-negative review.

    Results are round-robin across packs so one noisy source cannot consume the
    entire weekly sample.
    """
    if limit <= 0:
        return []
    thresholds = _thresholds(packs)
    if pack_name and pack_name not in thresholds:
        return []

    reviewed_ids = select(ReviewLabel.raw_post_id)
    rows = (
        await session.execute(
            select(RawPost)
            .where(
                RawPost.classified_at.is_not(None),
                RawPost.fit_score.is_not(None),
                ~RawPost.id.in_(reviewed_ids),
            )
            .order_by(desc(RawPost.fetched_at))
            .limit(max(200, limit * 30))
        )
    ).scalars().all()

    buckets: dict[str, list[RawPost]] = {}
    for post in rows:
        threshold = thresholds.get(post.pack)
        if threshold is None or (pack_name and post.pack != pack_name):
            continue
        if post.fit_score is None or post.fit_score >= threshold:
            continue
        buckets.setdefault(post.pack, []).append(post)

    selected: list[RawPost] = []
    names = sorted(buckets)
    while len(selected) < limit and any(buckets.get(name) for name in names):
        for name in names:
            bucket = buckets.get(name, [])
            if bucket:
                selected.append(bucket.pop(0))
                if len(selected) >= limit:
                    break
    return selected


def format_review_card(post: RawPost, threshold: int) -> tuple[str, list[list[dict[str, str]]]]:
    community = f"r/{post.community}" if post.community else post.source
    summary = (post.score or {}).get("one_line_summary", "")
    title = post.title or "(no title)"
    preview = (post.text or "").strip()
    if len(preview) > 700:
        preview = preview[:697].rstrip() + "..."
    card = (
        f"🧪 <b>Classifier review</b> · {html.escape(post.pack)}\n"
        f"Predicted <b>not surfaced</b>: score {post.fit_score}/{threshold}\n"
        f"{html.escape(community)} · post #{post.id}\n\n"
        f"<b>{html.escape(title)}</b>\n"
        f"{html.escape(summary) + chr(10) if summary else ''}"
        f"{html.escape(preview)}\n\n"
        f'<a href="{html.escape(post.url)}">Open post</a>\n\n'
        "Should this have counted as a demand lead?"
    )
    buttons = [[
        {"text": "✅ Demand", "callback_data": f"r:demand:{post.id}"},
        {"text": "❌ Not lead", "callback_data": f"r:not_demand:{post.id}"},
        {"text": "⏭ Skip", "callback_data": f"r:skip:{post.id}"},
    ]]
    return card, buttons


async def record_review(
    session,
    raw_post_id: int,
    label: str,
    *,
    threshold: int,
) -> ReviewLabel:
    if label not in _LABELS:
        raise ReviewError(f"unknown review label {label!r}")
    post = await session.get(RawPost, raw_post_id)
    if post is None or post.fit_score is None:
        raise ReviewError(f"raw post #{raw_post_id} is not reviewable")

    existing = await session.scalar(
        select(ReviewLabel).where(ReviewLabel.raw_post_id == raw_post_id)
    )
    previous = existing.label if existing else None
    if existing is None:
        existing = ReviewLabel(
            raw_post_id=post.id,
            pack=post.pack,
            label=label,
            fit_score=post.fit_score,
            threshold=threshold,
            predicted_positive=post.fit_score >= threshold,
        )
        session.add(existing)
    else:
        existing.label = label
        existing.fit_score = post.fit_score
        existing.threshold = threshold
        existing.predicted_positive = post.fit_score >= threshold

    session.add(
        Event(
            kind="review_labeled",
            payload={
                "raw_post_id": post.id,
                "pack": post.pack,
                "label": label,
                "previous_label": previous,
                "fit_score": post.fit_score,
                "threshold": threshold,
            },
        )
    )
    await session.commit()
    return existing


def _owner_week_start(now: datetime, owner_tz: str) -> datetime:
    local = now.astimezone(ZoneInfo(owner_tz))
    monday = (local - timedelta(days=local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return monday.astimezone(UTC)


async def run_weekly_review_nudge(
    *,
    session_factory=None,
    notifier=None,
    packs: list[OfferPack] | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """Send at most one review reminder per owner-local ISO week."""
    settings = get_settings()
    session_factory = session_factory or get_session_factory()
    notifier = notifier or get_notifier(settings)
    packs = packs if packs is not None else load_review_packs()
    now = now or datetime.now(UTC)
    week_start = _owner_week_start(now, settings.OWNER_TZ)

    async with session_factory() as session:
        already_sent = await session.scalar(
            select(Event.id)
            .where(Event.kind == "review_nudge", Event.ts >= week_start)
            .limit(1)
        )
        if already_sent:
            return {"available": 0, "sent": 0}

        available = await review_candidates(session, packs, limit=10)
        if not available:
            return {"available": 0, "sent": 0}

        sent = await notifier.send(
            f"🧪 LeadFinder weekly classifier review is ready "
            f"({len(available)} sampled posts). Send /review10 to label them."
        )
        if sent:
            session.add(
                Event(
                    kind="review_nudge",
                    payload={
                        "available": len(available),
                        "week_start": week_start.isoformat(),
                    },
                )
            )
            await session.commit()
        return {"available": len(available), "sent": int(bool(sent))}
