"""One poll cycle (DESIGN §3): pollers -> age gate + keyword filter -> dedup insert -> alert.

Every stage is injectable for tests; defaults wire the real Reddit adapter,
the configured notifier, and the app database.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.adapters import reddit_rss
from app.core.config import get_settings
from app.db.session import get_session_factory, insert_new_posts
from app.filtering import is_fresh, match_keywords
from app.models.event import Event
from app.notify import format_alert, get_notifier
from app.packs import load_packs

log = logging.getLogger(__name__)


async def run_poll_cycle(
    *,
    session_factory=None,
    notifier=None,
    packs=None,
    poll_fn=None,
) -> dict:
    """Poll every enabled pack once. Returns summary counts."""
    settings = get_settings()
    session_factory = session_factory or get_session_factory()
    notifier = notifier or get_notifier(settings)
    packs = packs if packs is not None else load_packs(Path(settings.PACKS_DIR))
    poll_fn = poll_fn or reddit_rss.poll

    started = time.monotonic()
    summary = {"fetched": 0, "matched": 0, "new": 0, "alerted": 0}

    async with httpx.AsyncClient(
        headers={"User-Agent": settings.REDDIT_USER_AGENT},
        timeout=20.0,
        follow_redirects=True,
    ) as client:
        for pack in packs:
            posts = await poll_fn(pack, client)
            summary["fetched"] += len(posts)

            rows = []
            for post in posts:
                if not is_fresh(post.created_at, pack.max_age_minutes):
                    continue
                matched = match_keywords(
                    f"{post.title or ''}\n{post.text}",
                    pack.keywords.include,
                    pack.keywords.exclude,
                )
                if not matched:
                    continue
                summary["matched"] += 1
                rows.append(asdict(post) | {"pack": pack.name, "matched_keywords": matched})

            async with session_factory() as session:
                new_posts = await insert_new_posts(session, rows)
                summary["new"] += len(new_posts)
                for np in new_posts:
                    if await notifier.send(format_alert(np, pack.name, np.matched_keywords)):
                        np.alerted_at = datetime.now(UTC)
                        summary["alerted"] += 1
                        session.add(
                            Event(kind="alert_sent", payload={"raw_post_id": np.id, "pack": pack.name})
                        )
                    else:
                        session.add(
                            Event(kind="alert_failed", payload={"raw_post_id": np.id, "pack": pack.name})
                        )
                await session.commit()

    duration_ms = int((time.monotonic() - started) * 1000)
    async with session_factory() as session:
        session.add(Event(kind="poll_cycle", payload=summary | {"duration_ms": duration_ms}))
        await session.commit()

    log.info("poll cycle done %s (%dms)", summary, duration_ms)
    return summary
