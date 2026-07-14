"""M6A read-only MCP services and in-memory protocol wiring."""

from datetime import UTC, datetime, timedelta

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from sqlalchemy import func, select

from app.db.session import insert_new_posts
from app.mcp.server import create_server
from app.mcp.service import LeadReadService
from app.models.draft import Draft
from app.models.event import Event
from app.models.lead import Lead, transition
from app.models.send import Send
from tests.conftest import make_post_row


async def _make_lead(session, *, external_id: str, source: str = "reddit", fit: int = 80):
    (post,) = await insert_new_posts(
        session,
        [
            make_post_row(
                external_id=external_id,
                source=source,
                url=f"https://example.com/{external_id}",
                fit_score=fit,
                score={"one_line_summary": f"summary {external_id}"},
                classified_at=datetime.now(UTC),
            )
        ],
    )
    lead = Lead(raw_post_id=post.id, pack="robofox_web")
    session.add(lead)
    await session.flush()
    transition(lead, "drafted")
    session.add(
        Draft(
            lead_id=lead.id,
            variant="A",
            channel="comment",
            text="helpful reply",
            risk_flags=[],
        )
    )
    await session.commit()
    return lead, post


async def test_search_get_and_stats_are_bounded_and_read_only(db_factory):
    async with db_factory() as session:
        first, _ = await _make_lead(session, external_id="mcp1", fit=91)
        second, _ = await _make_lead(session, external_id="mcp2", source="threads", fit=72)
        transition(first, "sent")
        session.add(Event(kind="approval", payload={"lead_id": first.id}))
        await session.commit()
        events_before = await session.scalar(select(func.count(Event.id)))
        sends_before = await session.scalar(select(func.count(Send.id)))

    service = LeadReadService(db_factory)
    page = await service.search_leads(limit=1, min_fit_score=70)
    assert len(page.items) == 1
    assert page.has_more is True
    assert page.next_cursor == page.items[-1].id

    next_page = await service.search_leads(limit=10, cursor=page.next_cursor)
    assert [item.id for item in next_page.items] == [first.id]

    detail = await service.get_lead(second.id)
    assert detail is not None
    assert detail.source == "threads"
    assert detail.drafts[0].text == "helpful reply"
    assert not hasattr(detail, "raw")

    snapshot = await service.stats(period_days=30, pack="robofox_web")
    assert snapshot.total_leads == 2
    assert snapshot.status_counts == {"drafted": 1, "sent": 1}
    assert snapshot.source_counts == {"reddit": 1, "threads": 1}

    async with db_factory() as session:
        assert await session.scalar(select(func.count(Event.id))) == events_before
        assert await session.scalar(select(func.count(Send.id))) == sends_before


async def test_search_validates_limits(db_factory):
    service = LeadReadService(db_factory)
    with pytest.raises(ValueError, match="limit"):
        await service.search_leads(limit=51)
    with pytest.raises(ValueError, match="min_fit_score"):
        await service.search_leads(min_fit_score=101)
    with pytest.raises(ValueError, match="period_days"):
        await service.stats(period_days=0)


async def test_mcp_in_memory_transport_and_annotations(db_factory):
    async with db_factory() as session:
        lead, _ = await _make_lead(session, external_id="mcp-protocol")

    server = create_server(LeadReadService(db_factory))
    async with create_connected_server_and_client_session(server, raise_exceptions=True) as client:
        tools = await client.list_tools()
        by_name = {tool.name: tool for tool in tools.tools}
        assert {"health", "search_leads", "get_lead", "stats"} <= set(by_name)
        assert by_name["search_leads"].annotations.readOnlyHint is True

        result = await client.call_tool("get_lead", {"lead_id": lead.id})
        assert result.structuredContent["id"] == lead.id
        assert result.structuredContent["drafts"][0]["variant"] == "A"


async def test_stats_excludes_old_leads(db_factory):
    async with db_factory() as session:
        lead, _ = await _make_lead(session, external_id="old")
        lead.created_at = datetime.now(UTC) - timedelta(days=40)
        await session.commit()
    snapshot = await LeadReadService(db_factory).stats(period_days=30)
    assert snapshot.total_leads == 0
