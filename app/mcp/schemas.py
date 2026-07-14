"""Stable, bounded structured outputs exposed by the LeadFinder MCP server."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class HealthResult(BaseModel):
    status: str
    database: bool
    server_version: str


class LeadListItem(BaseModel):
    id: int
    status: str
    pack: str
    source: str
    fit_score: int | None
    community: str | None
    title: str | None
    summary: str
    url: str
    created_at: datetime
    updated_at: datetime


class LeadSearchResult(BaseModel):
    items: list[LeadListItem]
    next_cursor: int | None = None
    has_more: bool = False


class DraftView(BaseModel):
    id: int
    variant: str
    channel: str
    text: str
    edited_text: str | None
    risk_flags: list[str]
    is_gold: bool
    created_at: datetime


class SendView(BaseModel):
    id: int
    draft_id: int
    platform: str
    channel: str
    status: str
    scheduled_at: datetime
    sent_at: datetime | None
    external_result_id: str | None
    error: str | None


class LeadDetail(BaseModel):
    id: int
    status: str
    pack: str
    source: str
    external_id: str
    fit_score: int | None
    community: str | None
    author_handle: str | None
    title: str | None
    text: str
    summary: str
    matched_keywords: list[str]
    url: str
    post_created_at: datetime
    lead_created_at: datetime
    updated_at: datetime
    approval_pushed_at: datetime | None
    chosen_draft_id: int | None
    drafts: list[DraftView] = Field(default_factory=list)
    sends: list[SendView] = Field(default_factory=list)


class StatsResult(BaseModel):
    period_days: int
    pack: str | None
    total_leads: int
    status_counts: dict[str, int]
    source_counts: dict[str, int]
    worked: int
    replied: int
    conversations: int
    won: int
    reply_rate: float | None
    conversation_rate: float | None
    win_rate: float | None
    average_fit_score: float | None
    llm_calls: int
    llm_cost_usd: float


class RedraftResult(BaseModel):
    lead_id: int
    archived_revision_ids: list[int]
    active_draft_ids: list[int]
    variants: list[str]
    guidance_sha256: str


class MuteResult(BaseModel):
    kind: str
    value: str
    pack: str | None
    created: bool
