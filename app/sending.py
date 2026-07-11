"""Send cycle (M4, DESIGN §3.7): execute approved, due, guardrail-clean sends.

Every guardrail re-checks at EXECUTION time — the jitter window may have
consumed a cap, opened a halt, or crossed into quiet hours. A send exists only
because the owner tapped approve on that specific draft.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import select, update

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.guardrails import check_send
from app.models.event import Event
from app.models.lead import Lead, transition
from app.models.raw_post import RawPost
from app.models.send import Send
from app.notify import get_notifier

log = logging.getLogger(__name__)

_BATCH = 5


async def _execute(send: Send, client: httpx.AsyncClient) -> tuple[bool, str | None, str | None]:
    """Dispatch to the platform sender. Returns (ok, external_result_id, error)."""
    settings = get_settings()
    if send.platform == "reddit":
        from app.senders.reddit_user import get_reddit_user_client

        reddit = get_reddit_user_client()
        if reddit is None:
            return False, None, "reddit user credentials not configured (REDDIT_USERNAME/PASSWORD)"
        if send.channel == "dm":
            return await reddit.send_dm(client, send.recipient, "re: your post", send.text)
        return await reddit.post_comment(client, send.target_external_id, send.text)
    if send.platform == "threads":
        from app.senders.threads_send import post_reply

        if not settings.THREADS_ACCESS_TOKEN:
            return False, None, "THREADS_ACCESS_TOKEN not configured"
        return await post_reply(
            client, settings.THREADS_ACCESS_TOKEN, send.target_external_id, send.text
        )
    return False, None, f"unknown platform {send.platform}"


async def run_send_cycle(
    *,
    session_factory=None,
    notifier=None,
    execute_fn=None,
    now: datetime | None = None,
) -> dict:
    settings = get_settings()
    session_factory = session_factory or get_session_factory()
    notifier = notifier or get_notifier(settings)
    summary = {"executed": 0, "deferred": 0, "halted": 0, "failed": 0}
    now = now or datetime.now(UTC)

    async with session_factory() as session:
        # Crash recovery: an 'executing' row at cycle start means a previous cycle
        # died between the API call and its commit — the reply may or may not be
        # live. NEVER re-execute it (that's the double-post); fail it and make the
        # owner look at the thread.
        orphans = (
            (await session.execute(select(Send).where(Send.status == "executing"))).scalars().all()
        )
        for orphan in orphans:
            orphan.status = "failed"
            orphan.error = (
                "worker crashed mid-execution — the reply MAY already be live; "
                "check the thread before re-approving"
            )
            summary["failed"] += 1
            session.add(
                Event(
                    kind="send_orphaned",
                    payload={"send_id": orphan.id, "lead_id": orphan.lead_id},
                )
            )
            await notifier.send(
                f"⚠️ send #{orphan.id} (lead #{orphan.lead_id}) was interrupted mid-post — "
                f"check the thread before re-approving."
            )
        if orphans:
            await session.commit()

        due = (
            await session.execute(
                Send.__table__.select()
                .where(Send.status == "queued", Send.scheduled_at <= now)
                .order_by(Send.scheduled_at)
                .limit(_BATCH)
            )
        ).all()
        due_ids = [row.id for row in due]

    async with httpx.AsyncClient(
        headers={"User-Agent": settings.REDDIT_USER_AGENT}, timeout=30.0
    ) as client:
        for send_id in due_ids:
            async with session_factory() as session:
                send = await session.get(Send, send_id)
                if send is None or send.status != "queued":
                    continue  # cancelled while we were iterating

                verdict = await check_send(session, send, settings, now=now)
                if not verdict.allowed:
                    if verdict.retry_at is not None:
                        send.scheduled_at = verdict.retry_at
                        summary["deferred"] += 1
                        session.add(
                            Event(
                                kind="send_deferred",
                                payload={"send_id": send.id, "reason": verdict.reason},
                            )
                        )
                        log.info("send %s deferred: %s", send.id, verdict.reason)
                    else:
                        send.status = "halted"
                        summary["halted"] += 1
                        session.add(
                            Event(
                                kind="send_halted",
                                payload={"send_id": send.id, "reason": verdict.reason},
                            )
                        )
                        await notifier.send(
                            f"🛑 send #{send.id} (lead #{send.lead_id}) blocked: {verdict.reason}"
                        )
                    await session.commit()
                    continue

                # Atomic claim: exactly one of {this cycle, a bot-side cancel} wins.
                # The commit lands BEFORE the API call, so a crash after posting
                # leaves an 'executing' marker — never a re-postable 'queued' row.
                claimed = await session.execute(
                    update(Send)
                    .where(Send.id == send.id, Send.status == "queued")
                    .values(status="executing")
                )
                if claimed.rowcount != 1:
                    await session.rollback()  # cancelled at the last instant
                    continue
                await session.commit()
                send.status = "executing"

                try:
                    ok, ext_id, error = await (execute_fn or _execute)(send, client)
                except Exception as exc:  # outcome unknown — same policy as a crash
                    log.exception("send %s executor raised", send.id)
                    ok, ext_id, error = (
                        False,
                        None,
                        f"execution error — the reply MAY be live; check the thread ({exc})",
                    )
                if ok:
                    send.status = "sent"
                    send.sent_at = datetime.now(UTC)
                    send.external_result_id = ext_id
                    lead = await session.get(Lead, send.lead_id)
                    if lead and lead.status == "drafted":
                        transition(lead, "sent")
                    post = await session.get(RawPost, lead.raw_post_id) if lead else None
                    summary["executed"] += 1
                    session.add(
                        Event(
                            kind="send_executed",
                            payload={
                                "send_id": send.id,
                                "lead_id": send.lead_id,
                                "external_result_id": ext_id,
                                "approval_event_id": send.approval_event_id,
                            },
                        )
                    )
                    await notifier.send(
                        f"✅ posted {send.channel} for lead #{send.lead_id}"
                        f"{f' — {post.url}' if post else ''}"
                    )
                else:
                    send.status = "failed"
                    send.error = error
                    summary["failed"] += 1
                    session.add(
                        Event(
                            kind="send_failed",
                            payload={"send_id": send.id, "lead_id": send.lead_id, "error": error},
                        )
                    )
                    await notifier.send(
                        f"⚠️ send failed for lead #{send.lead_id}: {error} — re-approve to retry"
                    )
                await session.commit()

    if any(summary.values()):
        log.info("send cycle done %s", summary)
    return summary
