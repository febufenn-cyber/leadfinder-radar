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

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _resolve_packs_dir(configured: str) -> Path:
    """Relative PACKS_DIR must not depend on the process cwd — anchor it to the repo."""
    path = Path(configured)
    return path if path.is_absolute() else _REPO_ROOT / path


def select_poll_fn(settings):
    """OAuth adapter when script-app creds exist, else the RSS fallback (DESIGN §2)."""
    if settings.REDDIT_CLIENT_ID and settings.REDDIT_CLIENT_SECRET:
        from app.adapters.reddit_oauth import get_oauth_adapter

        return get_oauth_adapter().poll
    return reddit_rss.poll


async def _default_classify(session, pack, row, raw_post_id):
    from app.classify import classify_post
    from app.services.claude_runner import get_runner

    return await classify_post(get_runner(), session, pack, row, raw_post_id=raw_post_id)


async def run_poll_cycle(
    *,
    session_factory=None,
    notifier=None,
    packs=None,
    poll_fn=None,
    classify_fn=None,
) -> dict:
    """Poll every enabled pack once. Returns summary counts."""
    settings = get_settings()
    session_factory = session_factory or get_session_factory()
    notifier = notifier or get_notifier(settings)
    packs = packs if packs is not None else load_packs(_resolve_packs_dir(settings.PACKS_DIR))
    poll_fn = poll_fn or select_poll_fn(settings)
    classify_fn = classify_fn or _default_classify
    if not packs:
        log.warning("no enabled packs loaded from %s — nothing to poll", settings.PACKS_DIR)

    started = time.monotonic()
    summary = {
        "fetched": 0, "matched": 0, "new": 0,
        "classified": 0, "surfaced": 0, "suppressed": 0, "alerted": 0,
    }

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
                    # classify + score, tier fast (DESIGN §3.3); dupes never get here
                    row = {
                        "community": np.community,
                        "author_handle": np.author_handle,
                        "title": np.title,
                        "text": np.text,
                    }
                    score = await classify_fn(session, pack, row, np.id)
                    np.classified_at = datetime.now(UTC)
                    if score is not None:
                        summary["classified"] += 1
                        np.fit_score = score.fit_score
                        np.score = score.model_dump()

                    # threshold gate: sub-threshold is stored, not surfaced;
                    # classifier failure surfaces UNSCORED (don't lose leads)
                    if score is not None and score.fit_score < pack.threshold:
                        summary["suppressed"] += 1
                        continue
                    summary["surfaced"] += 1

                    card = format_alert(
                        np, pack.name, np.matched_keywords,
                        score=score, unscored=score is None,
                    )
                    if await notifier.send(card):
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
