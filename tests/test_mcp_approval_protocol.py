"""Protocol-level M6D checks for tool annotations and verification-value redaction."""

from datetime import UTC, datetime

from mcp.shared.memory import create_connected_server_and_client_session
from sqlalchemy import select

from app.core.config import Settings
from app.db.session import insert_new_posts
from app.mcp.approvals import ApprovalChallengeService
from app.mcp.runtime import SlidingWindowRateLimiter, ToolRuntime
from app.mcp.server import create_server
from app.mcp.service import LeadReadService
from app.models.draft import Draft
from app.models.event import Event
from app.models.lead import Lead, transition
from tests.conftest import make_post_row


class FakeNotifier:
    async def send(self, text: str) -> bool:
        return True


async def _lead(session) -> Lead:
    (post,) = await insert_new_posts(
        session,
        [
            make_post_row(
                external_id="protocol-approval",
                url="https://example.com/protocol-approval",
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
            text="safe protocol reply",
            risk_flags=[],
        )
    )
    await session.commit()
    return lead


async def test_protocol_redacts_verification_value_and_marks_approval_destructive(db_factory):
    async with db_factory() as session:
        lead = await _lead(session)

    settings = Settings(
        _env_file=None,
        SEND_MODE="copy",
        TELEGRAM_BOT_TOKEN="configured-for-test",
        TELEGRAM_CHAT_ID="12345",
    )
    challenge_service = ApprovalChallengeService(
        session_factory=db_factory,
        settings=settings,
        notifier=FakeNotifier(),
        secret="p" * 32,
        ttl_seconds=300,
        max_attempts=5,
        code_factory=lambda: "482193",
    )
    runtime = ToolRuntime(
        session_factory=db_factory,
        limiter=SlidingWindowRateLimiter(20),
        timeout_seconds=5,
    )
    server = create_server(
        LeadReadService(db_factory),
        settings=settings,
        challenge_service=challenge_service,
        runtime=runtime,
    )

    async with create_connected_server_and_client_session(server, raise_exceptions=True) as client:
        tools = await client.list_tools()
        by_name = {tool.name: tool for tool in tools.tools}
        assert by_name["approve"].annotations.destructiveHint is True

        requested = await client.call_tool(
            "request_approval_code",
            {"lead_id": lead.id, "variant": "A"},
        )
        assert "code" not in requested.structuredContent

        approved = await client.call_tool(
            "approve",
            {
                "lead_id": lead.id,
                "variant": "A",
                "verification_value": "482193",
            },
        )
        assert approved.structuredContent["status"] == "sent"

    async with db_factory() as session:
        audits = (
            await session.execute(
                select(Event).where(Event.kind == "mcp_tool_call").order_by(Event.id)
            )
        ).scalars().all()
    approval_audit = next(event for event in audits if event.payload["tool"] == "approve")
    assert approval_audit.payload["arguments"]["approval_code"] == "[redacted]"
    assert "482193" not in str([event.payload for event in audits])
