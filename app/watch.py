"""Watch cycle (M4, DESIGN §3.8 + §3.7): reply detection and removal auto-halt.

Reply detection is the ONLY code path that advances a lead past `sent`
(sent → replied); everything further is the owner's job. Removal detection is
the auto-halt tripwire: if one of our posted comments was removed by a mod,
ALL sending stops until the owner clears the halt.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.models.event import Event
from app.models.halt import Halt
from app.models.lead import Lead, transition
from app.models.raw_post import RawPost
from app.models.send import Send
from app.notify import get_notifier
from app.services.hubspot import hubspot_sync_reply

log = logging.getLogger(__name__)

_WATCH_WINDOW_DAYS = 7  # stop watching a send after a week of silence


def _reddit_removed(thing: dict) -> str | None:
    """Human-readable removal reason for one of our own comments, or None."""
    if thing.get("banned_by"):
        return f"removed by moderator ({thing.get('banned_by')})"
    if thing.get("removed_by_category"):
        return f"removed ({thing['removed_by_category']})"
    if thing.get("body") == "[removed]":
        return "removed"
    return None


async def _active_halt(session, platform: str) -> bool:
    return (
        await session.execute(
            select(Halt).where(
                Halt.platform.in_([platform, "all"]), Halt.cleared_at.is_(None)
            )
        )
    ).scalars().first() is not None


async def _mark_replied(session, notifier, send: Send, author: str, preview: str) -> None:
    lead = await session.get(Lead, send.lead_id)
    if lead is None or lead.status != "sent":
        return
    transition(lead, "replied")
    session.add(
        Event(
            kind="reply_detected",
            payload={
                "lead_id": lead.id,
                "send_id": send.id,
                "author": author,
                "platform": send.platform,
            },
        )
    )
    post = await session.get(RawPost, lead.raw_post_id)
    await notifier.send(
        f"🎉 {author} replied to your {send.platform} {send.channel} "
        f"(lead #{lead.id}):\n{preview[:300]}\n{post.url if post else ''}"
    )
    await hubspot_sync_reply(session, lead, post, author, preview)


async def _auto_halt(session, notifier, platform: str, reason: str, source: dict) -> None:
    """Insert a halt (once — don't stack duplicates) and alert the owner."""
    if await _active_halt(session, platform):
        return
    session.add(Halt(platform=platform, reason=reason, source=source))
    session.add(Event(kind="auto_halt", payload={"platform": platform, "reason": reason}))
    await notifier.send(
        f"🛑 AUTO-HALT ({platform}): {reason}\n"
        f"All {platform} sending is stopped until you clear it: "
        f"uv run python scripts/clear_halt.py"
    )


async def run_watch_cycle(*, session_factory=None, notifier=None) -> dict:
    settings = get_settings()
    session_factory = session_factory or get_session_factory()
    notifier = notifier or get_notifier(settings)
    summary = {"replies": 0, "halts": 0, "watched": 0}
    cutoff = datetime.now(UTC) - timedelta(days=_WATCH_WINDOW_DAYS)

    async with session_factory() as session:
        watched = (
            (
                await session.execute(
                    select(Send)
                    .join(Lead, Lead.id == Send.lead_id)
                    .where(
                        Send.status == "sent",
                        Send.sent_at >= cutoff,
                        Send.external_result_id.is_not(None),
                        Lead.status == "sent",
                    )
                )
            )
            .scalars()
            .all()
        )
        summary["watched"] = len(watched)
        reddit_sends = [s for s in watched if s.platform == "reddit"]
        threads_sends = [s for s in watched if s.platform == "threads"]

        async with httpx.AsyncClient(
            headers={"User-Agent": settings.REDDIT_USER_AGENT}, timeout=30.0
        ) as client:
            # ---- reddit: one inbox fetch covers replies to all our comments ----
            if reddit_sends:
                from app.senders.reddit_user import get_reddit_user_client

                reddit = get_reddit_user_client()
                if reddit is not None:
                    inbox = await reddit.fetch_inbox(client)
                    by_parent: dict[str, dict] = {}
                    for item in inbox:
                        parent = item.get("parent_id")
                        if parent and parent not in by_parent:
                            by_parent[parent] = item
                    for send in reddit_sends:
                        hit = by_parent.get(send.external_result_id)
                        if hit is not None:
                            await _mark_replied(
                                session, notifier, send,
                                f"u/{hit.get('author', '?')}", hit.get("body", ""),
                            )
                            summary["replies"] += 1

                    # removal tripwire on our own posted comments
                    things = await reddit.fetch_things(
                        client, [s.external_result_id for s in reddit_sends]
                    )
                    by_name = {t.get("name"): t for t in things}
                    for send in reddit_sends:
                        thing = by_name.get(send.external_result_id)
                        reason = _reddit_removed(thing) if thing else None
                        if reason is not None:
                            await _auto_halt(
                                session, notifier, "reddit",
                                f"our comment {send.external_result_id} in "
                                f"r/{send.community} was {reason}",
                                {"send_id": send.id, "lead_id": send.lead_id},
                            )
                            summary["halts"] += 1

            # ---- threads: per-send replies fetch (no inbox API) ----
            if threads_sends and settings.THREADS_ACCESS_TOKEN:
                from app.senders.threads_send import fetch_replies

                for send in threads_sends:
                    replies = await fetch_replies(
                        client, settings.THREADS_ACCESS_TOKEN, send.external_result_id
                    )
                    if replies:
                        first = replies[0]
                        await _mark_replied(
                            session, notifier, send,
                            f"@{first.get('username', '?')}", first.get("text", ""),
                        )
                        summary["replies"] += 1

        await session.commit()

    if summary["replies"] or summary["halts"]:
        log.info("watch cycle done %s", summary)
    return summary
