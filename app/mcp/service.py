"""Read-only M6 services. No MCP protocol concerns and no database mutations."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select, text

from app.db.session import get_session_factory
from app.models.draft import Draft
from app.models.lead import Lead
from app.models.llm_call import LlmCall
from app.models.raw_post import RawPost
from app.models.send import Send
from app.mcp.schemas import (
    DraftView,
    HealthResult,
    LeadDetail,
    LeadListItem,
    LeadSearchResult,
    SendView,
    StatsResult,
)

_MAX_LIMIT = 50
_MAX_TEXT = 2_000
_MAX_SUMMARY = 400
_WORKED = {"sent", "replied", "conversation", "won", "lost", "no_response"}
_REPLIED = {"replied", "conversation", "won"}
_CONVERSATIONS = {"conversation", "won"}


def _clip(value: str | None, limit: int) -> str:
    value = (value or "").strip()
    return value if len(value) <= limit else value[: limit - 3].rstrip() + "..."


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


class LeadReadService:
    """Bounded read operations shared by MCP tools and their tests."""

    def __init__(self, session_factory=None) -> None:
        self.session_factory = session_factory or get_session_factory()

    async def health(self) -> HealthResult:
        async with self.session_factory() as session:
            await session.execute(text("SELECT 1"))
        return HealthResult(status="ok", database=True, server_version="m6a")

    async def search_leads(
        self,
        *,
        status: str | None = None,
        pack: str | None = None,
        source: str | None = None,
        min_fit_score: int | None = None,
        cursor: int | None = None,
        limit: int = 20,
    ) -> LeadSearchResult:
        if not 1 <= limit <= _MAX_LIMIT:
            raise ValueError(f"limit must be between 1 and {_MAX_LIMIT}")
        if min_fit_score is not None and not 0 <= min_fit_score <= 100:
            raise ValueError("min_fit_score must be between 0 and 100")
        if cursor is not None and cursor <= 0:
            raise ValueError("cursor must be a positive lead id")

        query = select(Lead, RawPost).join(RawPost, RawPost.id == Lead.raw_post_id)
        if status:
            query = query.where(Lead.status == status)
        if pack:
            query = query.where(Lead.pack == pack)
        if source:
            query = query.where(RawPost.source == source)
        if min_fit_score is not None:
            query = query.where(RawPost.fit_score >= min_fit_score)
        if cursor is not None:
            query = query.where(Lead.id < cursor)
        query = query.order_by(Lead.id.desc()).limit(limit + 1)

        async with self.session_factory() as session:
            rows = (await session.execute(query)).all()

        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [
            LeadListItem(
                id=lead.id,
                status=lead.status,
                pack=lead.pack,
                source=post.source,
                fit_score=post.fit_score,
                community=post.community,
                title=_clip(post.title, 300) or None,
                summary=_clip((post.score or {}).get("one_line_summary"), _MAX_SUMMARY),
                url=post.url,
                created_at=lead.created_at,
                updated_at=lead.updated_at,
            )
            for lead, post in rows
        ]
        return LeadSearchResult(
            items=items,
            next_cursor=items[-1].id if has_more and items else None,
            has_more=has_more,
        )

    async def get_lead(self, lead_id: int) -> LeadDetail | None:
        if lead_id <= 0:
            raise ValueError("lead_id must be positive")
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    select(Lead, RawPost)
                    .join(RawPost, RawPost.id == Lead.raw_post_id)
                    .where(Lead.id == lead_id)
                )
            ).first()
            if row is None:
                return None
            lead, post = row
            drafts = (
                await session.execute(
                    select(Draft).where(Draft.lead_id == lead_id).order_by(Draft.variant)
                )
            ).scalars().all()
            sends = (
                await session.execute(
                    select(Send).where(Send.lead_id == lead_id).order_by(Send.id.desc()).limit(20)
                )
            ).scalars().all()

        return LeadDetail(
            id=lead.id,
            status=lead.status,
            pack=lead.pack,
            source=post.source,
            external_id=post.external_id,
            fit_score=post.fit_score,
            community=post.community,
            author_handle=post.author_handle,
            title=_clip(post.title, 300) or None,
            text=_clip(post.text, _MAX_TEXT),
            summary=_clip((post.score or {}).get("one_line_summary"), _MAX_SUMMARY),
            matched_keywords=list(post.matched_keywords or [])[:20],
            url=post.url,
            post_created_at=post.created_at,
            lead_created_at=lead.created_at,
            updated_at=lead.updated_at,
            approval_pushed_at=lead.approval_pushed_at,
            chosen_draft_id=lead.chosen_draft_id,
            drafts=[
                DraftView(
                    id=draft.id,
                    variant=draft.variant,
                    channel=draft.channel,
                    text=_clip(draft.text, _MAX_TEXT),
                    edited_text=_clip(draft.edited_text, _MAX_TEXT) if draft.edited_text else None,
                    risk_flags=list(draft.risk_flags or [])[:20],
                    is_gold=draft.is_gold,
                    created_at=draft.created_at,
                )
                for draft in drafts
            ],
            sends=[
                SendView(
                    id=send.id,
                    draft_id=send.draft_id,
                    platform=send.platform,
                    channel=send.channel,
                    status=send.status,
                    scheduled_at=send.scheduled_at,
                    sent_at=send.sent_at,
                    external_result_id=_clip(send.external_result_id, 300) or None,
                    error=_clip(send.error, 500) or None,
                )
                for send in sends
            ],
        )

    async def stats(self, *, period_days: int = 30, pack: str | None = None) -> StatsResult:
        if not 1 <= period_days <= 365:
            raise ValueError("period_days must be between 1 and 365")
        cutoff = datetime.now(UTC) - timedelta(days=period_days)

        lead_query = (
            select(Lead, RawPost)
            .join(RawPost, RawPost.id == Lead.raw_post_id)
            .where(Lead.created_at >= cutoff)
        )
        if pack:
            lead_query = lead_query.where(Lead.pack == pack)

        async with self.session_factory() as session:
            rows = (await session.execute(lead_query)).all()
            llm_query = select(
                func.count(LlmCall.id), func.coalesce(func.sum(LlmCall.cost_usd), Decimal("0"))
            ).where(LlmCall.ts >= cutoff)
            if pack:
                llm_query = llm_query.join(
                    RawPost, RawPost.id == LlmCall.raw_post_id
                ).where(RawPost.pack == pack)
            llm_calls, llm_cost = (await session.execute(llm_query)).one()

        statuses = Counter(lead.status for lead, _post in rows)
        sources = Counter(post.source for _lead, post in rows)
        worked = sum(lead.status in _WORKED for lead, _post in rows)
        replied = sum(lead.status in _REPLIED for lead, _post in rows)
        conversations = sum(lead.status in _CONVERSATIONS for lead, _post in rows)
        won = sum(lead.status == "won" for lead, _post in rows)
        fit_scores = [post.fit_score for _lead, post in rows if post.fit_score is not None]

        return StatsResult(
            period_days=period_days,
            pack=pack,
            total_leads=len(rows),
            status_counts=dict(sorted(statuses.items())),
            source_counts=dict(sorted(sources.items())),
            worked=worked,
            replied=replied,
            conversations=conversations,
            won=won,
            reply_rate=_rate(replied, worked),
            conversation_rate=_rate(conversations, worked),
            win_rate=_rate(won, worked),
            average_fit_score=round(sum(fit_scores) / len(fit_scores), 2) if fit_scores else None,
            llm_calls=int(llm_calls or 0),
            llm_cost_usd=round(float(llm_cost or 0), 6),
        )
