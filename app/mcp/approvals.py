"""Telegram-delivered, single-use MCP approval challenges (M6D)."""

from __future__ import annotations

import hashlib
import hmac
import html
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Callable

from sqlalchemy import select

from app.approval import ApprovalError, approve as copy_approve, queue_send
from app.core.config import Settings, get_settings
from app.db.session import get_session_factory
from app.models.draft import Draft
from app.models.event import Event
from app.models.lead import Lead
from app.models.mcp_approval_challenge import MCPApprovalChallenge
from app.notify import get_notifier
from app.mcp.schemas import ApprovalChallengeResult, ApprovalResult


class ChallengeError(ValueError):
    pass


def _draft_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _code_hash(secret: str, salt: str, lead_id: int, draft_id: int, code: str) -> str:
    message = f"{salt}:{lead_id}:{draft_id}:{code}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _environment_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ChallengeError(f"{name} must be an integer") from exc


class ApprovalChallengeService:
    def __init__(
        self,
        *,
        session_factory=None,
        settings: Settings | None = None,
        notifier=None,
        secret: str | None = None,
        ttl_seconds: int | None = None,
        max_attempts: int | None = None,
        code_factory: Callable[[], str] | None = None,
        now_factory: Callable[[], datetime] | None = None,
        copy_approve_fn=copy_approve,
        queue_send_fn=queue_send,
    ) -> None:
        self.session_factory = session_factory or get_session_factory()
        self.settings = settings or get_settings()
        self.notifier = notifier or get_notifier(self.settings)
        self.secret = secret if secret is not None else os.environ.get("MCP_APPROVAL_SECRET", "")
        self.ttl_seconds = (
            ttl_seconds
            if ttl_seconds is not None
            else _environment_int("MCP_APPROVAL_CODE_TTL_SECONDS", 300)
        )
        self.max_attempts = (
            max_attempts
            if max_attempts is not None
            else _environment_int("MCP_APPROVAL_MAX_ATTEMPTS", 5)
        )
        self.code_factory = code_factory or (lambda: f"{secrets.randbelow(1_000_000):06d}")
        self.now_factory = now_factory or (lambda: datetime.now(UTC))
        self.copy_approve_fn = copy_approve_fn
        self.queue_send_fn = queue_send_fn

    def _validate_configuration(self) -> None:
        if len(self.secret) < 32:
            raise ChallengeError("MCP_APPROVAL_SECRET must contain at least 32 characters")
        if not 30 <= self.ttl_seconds <= 900:
            raise ChallengeError("approval code TTL must be between 30 and 900 seconds")
        if not 1 <= self.max_attempts <= 10:
            raise ChallengeError("approval max attempts must be between 1 and 10")
        if not (self.settings.TELEGRAM_BOT_TOKEN and self.settings.TELEGRAM_CHAT_ID):
            raise ChallengeError("Telegram owner delivery must be configured before MCP approval")

    async def _lead_and_draft(self, session, lead_id: int, variant: str):
        lead = await session.get(Lead, lead_id)
        if lead is None:
            raise ChallengeError(f"lead #{lead_id} not found")
        if lead.status != "drafted":
            raise ChallengeError(f"lead #{lead_id} is {lead.status}, not awaiting approval")
        draft = await session.scalar(
            select(Draft)
            .where(Draft.lead_id == lead_id, Draft.variant == variant)
            .order_by(Draft.id.desc())
            .limit(1)
        )
        if draft is None:
            raise ChallengeError(f"variant {variant!r} not found for lead #{lead_id}")
        return lead, draft

    async def request_approval_code(self, lead_id: int, variant: str) -> ApprovalChallengeResult:
        self._validate_configuration()
        variant = variant.strip().upper()
        if variant not in {"A", "B", "C"}:
            raise ChallengeError("variant must be A, B, or C")
        now = self.now_factory()
        code = self.code_factory()
        if len(code) != 6 or not code.isdigit():
            raise ChallengeError("approval code generator must produce six digits")

        async with self.session_factory() as session:
            _lead, draft = await self._lead_and_draft(session, lead_id, variant)
            active = (
                await session.execute(
                    select(MCPApprovalChallenge)
                    .where(
                        MCPApprovalChallenge.lead_id == lead_id,
                        MCPApprovalChallenge.variant == variant,
                        MCPApprovalChallenge.used_at.is_(None),
                    )
                    .with_for_update()
                )
            ).scalars().all()
            for prior in active:
                prior.used_at = now
            await session.flush()

            salt = secrets.token_hex(16)
            expires_at = now + timedelta(seconds=self.ttl_seconds)
            challenge = MCPApprovalChallenge(
                lead_id=lead_id,
                draft_id=draft.id,
                variant=variant,
                code_salt=salt,
                code_hash=_code_hash(self.secret, salt, lead_id, draft.id, code),
                draft_sha256=_draft_digest(draft.text),
                expires_at=expires_at,
                attempts=0,
                max_attempts=self.max_attempts,
            )
            session.add(challenge)
            await session.flush()

            message = (
                f"🔐 <b>LeadFinder MCP approval</b>\n"
                f"Lead #{lead_id} · variant {variant} · mode {html.escape(self.settings.SEND_MODE)}\n"
                f"Code: <code>{code}</code>\n"
                f"Expires in {self.ttl_seconds // 60 or 1} minute(s). "
                "Do not share this code with an untrusted client."
            )
            if not await self.notifier.send(message):
                await session.rollback()
                raise ChallengeError("Telegram delivery failed; no approval challenge was saved")

            session.add(
                Event(
                    kind="mcp_approval_code_sent",
                    payload={
                        "challenge_id": challenge.id,
                        "lead_id": lead_id,
                        "draft_id": draft.id,
                        "variant": variant,
                        "expires_at": expires_at.isoformat(),
                    },
                )
            )
            await session.commit()
            return ApprovalChallengeResult(
                challenge_id=challenge.id,
                lead_id=lead_id,
                variant=variant,
                expires_at=expires_at,
                delivered=True,
            )

    async def approve(self, lead_id: int, variant: str, code: str) -> ApprovalResult:
        self._validate_configuration()
        variant = variant.strip().upper()
        if variant not in {"A", "B", "C"}:
            raise ChallengeError("variant must be A, B, or C")
        if len(code) != 6 or not code.isdigit():
            raise ChallengeError("code must contain six digits")
        now = self.now_factory()

        async with self.session_factory() as session:
            challenge = await session.scalar(
                select(MCPApprovalChallenge)
                .where(
                    MCPApprovalChallenge.lead_id == lead_id,
                    MCPApprovalChallenge.variant == variant,
                    MCPApprovalChallenge.used_at.is_(None),
                )
                .order_by(MCPApprovalChallenge.id.desc())
                .limit(1)
                .with_for_update()
            )
            if challenge is None:
                raise ChallengeError("no active approval challenge; request a new code")

            if challenge.expires_at <= now:
                challenge.used_at = now
                session.add(
                    Event(
                        kind="mcp_approval_code_rejected",
                        payload={"challenge_id": challenge.id, "reason": "expired"},
                    )
                )
                await session.commit()
                raise ChallengeError("approval code expired; request a new code")
            if challenge.attempts >= challenge.max_attempts:
                challenge.used_at = now
                await session.commit()
                raise ChallengeError("approval challenge is locked; request a new code")

            _lead, draft = await self._lead_and_draft(session, lead_id, variant)
            if draft.id != challenge.draft_id or _draft_digest(draft.text) != challenge.draft_sha256:
                challenge.used_at = now
                session.add(
                    Event(
                        kind="mcp_approval_code_rejected",
                        payload={"challenge_id": challenge.id, "reason": "draft_changed"},
                    )
                )
                await session.commit()
                raise ChallengeError("draft changed after code issuance; request a new code")

            expected = _code_hash(
                self.secret,
                challenge.code_salt,
                challenge.lead_id,
                challenge.draft_id,
                code,
            )
            if not hmac.compare_digest(expected, challenge.code_hash):
                challenge.attempts += 1
                challenge.last_attempt_at = now
                if challenge.attempts >= challenge.max_attempts:
                    challenge.used_at = now
                session.add(
                    Event(
                        kind="mcp_approval_code_rejected",
                        payload={
                            "challenge_id": challenge.id,
                            "reason": "wrong_code",
                            "attempts": challenge.attempts,
                        },
                    )
                )
                await session.commit()
                raise ChallengeError("approval code is invalid")

            challenge.used_at = now
            challenge.last_attempt_at = now
            session.add(
                Event(
                    kind="mcp_approval_code_accepted",
                    payload={
                        "challenge_id": challenge.id,
                        "lead_id": lead_id,
                        "draft_id": draft.id,
                        "variant": variant,
                        "mode": self.settings.SEND_MODE,
                    },
                )
            )
            try:
                if self.settings.SEND_MODE == "api":
                    send = await self.queue_send_fn(session, lead_id, variant)
                    return ApprovalResult(
                        lead_id=lead_id,
                        variant=variant,
                        mode="api",
                        status=send.status,
                        send_id=send.id,
                        scheduled_at=send.scheduled_at,
                    )
                payload = await self.copy_approve_fn(session, lead_id, variant)
                return ApprovalResult(
                    lead_id=lead_id,
                    variant=variant,
                    mode="copy",
                    status="sent",
                    url=payload.url,
                    text=payload.text,
                )
            except ApprovalError as exc:
                raise ChallengeError(str(exc)) from exc
