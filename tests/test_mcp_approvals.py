"""M6D Telegram-gated single-use approval challenge tests."""

import asyncio
import re
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.config import Settings
from app.db.session import insert_new_posts
from app.mcp.approvals import ApprovalChallengeService, ChallengeError
from app.models.draft import Draft
from app.models.event import Event
from app.models.lead import Lead, transition
from app.models.mcp_approval_challenge import MCPApprovalChallenge
from app.models.send import Send
from tests.conftest import make_post_row


class FakeNotifier:
    def __init__(self, succeeds=True):
        self.succeeds = succeeds
        self.messages = []

    async def send(self, text):
        self.messages.append(text)
        return self.succeeds


def _settings(mode="copy"):
    return Settings(
        _env_file=None,
        SEND_MODE=mode,
        TELEGRAM_BOT_TOKEN="test-telegram-token",
        TELEGRAM_CHAT_ID="12345",
        JITTER_MIN_MINUTES=2,
        JITTER_MAX_MINUTES=2,
    )


async def _drafted_lead(session, *, external_id="approval", text="approved reply"):
    (post,) = await insert_new_posts(
        session,
        [
            make_post_row(
                external_id=external_id,
                url=f"https://example.com/{external_id}",
            )
        ],
    )
    lead = Lead(raw_post_id=post.id, pack="robofox_web")
    session.add(lead)
    await session.flush()
    transition(lead, "drafted")
    draft = Draft(
        lead_id=lead.id,
        variant="A",
        channel="comment",
        text=text,
        risk_flags=[],
    )
    session.add(draft)
    await session.commit()
    return lead, draft


def _extract_code(message: str) -> str:
    match = re.search(r"<code>(\d{6})</code>", message)
    assert match
    return match.group(1)


def _service(db_factory, notifier, *, settings=None, now=None, attempts=5):
    return ApprovalChallengeService(
        session_factory=db_factory,
        settings=settings or _settings(),
        notifier=notifier,
        secret="s" * 32,
        ttl_seconds=300,
        max_attempts=attempts,
        code_factory=lambda: "482193",
        now_factory=(lambda: now[0]) if now is not None else None,
    )


async def test_request_delivers_code_but_never_persists_or_returns_plaintext(db_factory):
    async with db_factory() as session:
        lead, _draft = await _drafted_lead(session)
    notifier = FakeNotifier()
    service = _service(db_factory, notifier)

    result = await service.request_approval_code(lead.id, "A")
    code = _extract_code(notifier.messages[0])
    assert code == "482193"
    assert "code" not in result.model_dump()

    async with db_factory() as session:
        challenge = (await session.execute(select(MCPApprovalChallenge))).scalars().one()
        events = (await session.execute(select(Event))).scalars().all()
    assert challenge.code_hash != code
    assert code not in challenge.code_hash
    assert code not in challenge.code_salt
    assert code not in str([event.payload for event in events])
    assert challenge.draft_sha256


async def test_copy_approval_is_single_use_and_creates_exactly_one_approval(db_factory):
    async with db_factory() as session:
        lead, _draft = await _drafted_lead(session, external_id="copy")
    notifier = FakeNotifier()
    service = _service(db_factory, notifier)
    await service.request_approval_code(lead.id, "A")

    with pytest.raises(ChallengeError, match="invalid"):
        await service.approve(lead.id, "A", "000000")
    result = await service.approve(lead.id, "A", "482193")
    assert result.mode == "copy"
    assert result.text == "approved reply"

    with pytest.raises(ChallengeError, match="no active"):
        await service.approve(lead.id, "A", "482193")

    async with db_factory() as session:
        approvals = await session.scalar(select(func.count(Event.id)).where(Event.kind == "approval"))
        sends = await session.scalar(select(func.count(Send.id)))
        challenge = (await session.execute(select(MCPApprovalChallenge))).scalars().one()
    assert approvals == 1
    assert sends == 0
    assert challenge.used_at is not None
    assert challenge.attempts == 1


async def test_expired_and_changed_draft_codes_are_consumed(db_factory):
    now = [datetime(2026, 7, 14, 6, 0, tzinfo=UTC)]
    async with db_factory() as session:
        expired_lead, _ = await _drafted_lead(session, external_id="expired")
    expired_service = _service(db_factory, FakeNotifier(), now=now)
    await expired_service.request_approval_code(expired_lead.id, "A")
    now[0] += timedelta(minutes=6)
    with pytest.raises(ChallengeError, match="expired"):
        await expired_service.approve(expired_lead.id, "A", "482193")

    async with db_factory() as session:
        changed_lead, changed_draft = await _drafted_lead(session, external_id="changed")
    changed_service = _service(db_factory, FakeNotifier())
    await changed_service.request_approval_code(changed_lead.id, "A")
    async with db_factory() as session:
        draft = await session.get(Draft, changed_draft.id)
        draft.text = "changed after challenge"
        await session.commit()
    with pytest.raises(ChallengeError, match="draft changed"):
        await changed_service.approve(changed_lead.id, "A", "482193")


async def test_attempt_limit_locks_challenge(db_factory):
    async with db_factory() as session:
        lead, _ = await _drafted_lead(session, external_id="locked")
    service = _service(db_factory, FakeNotifier(), attempts=2)
    await service.request_approval_code(lead.id, "A")
    for _ in range(2):
        with pytest.raises(ChallengeError, match="invalid"):
            await service.approve(lead.id, "A", "111111")
    with pytest.raises(ChallengeError, match="no active"):
        await service.approve(lead.id, "A", "482193")


async def test_api_mode_uses_existing_queue_send_path(db_factory, monkeypatch):
    settings = _settings("api")
    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)
    async with db_factory() as session:
        lead, _ = await _drafted_lead(session, external_id="api")
    service = _service(db_factory, FakeNotifier(), settings=settings)
    await service.request_approval_code(lead.id, "A")
    result = await service.approve(lead.id, "A", "482193")
    assert result.mode == "api"
    assert result.status == "queued"
    assert result.send_id is not None

    async with db_factory() as session:
        approvals = await session.scalar(select(func.count(Event.id)).where(Event.kind == "approval"))
        sends = (await session.execute(select(Send))).scalars().all()
    assert approvals == 1
    assert len(sends) == 1
    assert sends[0].approval_event_id is not None


async def test_concurrent_replay_allows_only_one_approval(db_factory):
    async with db_factory() as session:
        lead, _ = await _drafted_lead(session, external_id="race")
    service = _service(db_factory, FakeNotifier())
    await service.request_approval_code(lead.id, "A")

    results = await asyncio.gather(
        service.approve(lead.id, "A", "482193"),
        service.approve(lead.id, "A", "482193"),
        return_exceptions=True,
    )
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, ChallengeError) for result in results) == 1

    async with db_factory() as session:
        approvals = await session.scalar(select(func.count(Event.id)).where(Event.kind == "approval"))
    assert approvals == 1


async def test_delivery_failure_rolls_back_new_challenge(db_factory):
    async with db_factory() as session:
        lead, _ = await _drafted_lead(session, external_id="delivery")
    service = _service(db_factory, FakeNotifier(succeeds=False))
    with pytest.raises(ChallengeError, match="delivery failed"):
        await service.request_approval_code(lead.id, "A")
    async with db_factory() as session:
        count = await session.scalar(select(func.count(MCPApprovalChallenge.id)))
    assert count == 0
