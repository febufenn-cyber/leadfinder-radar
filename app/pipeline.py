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


class ClassifierBreaker:
    """Stops UNSCORED-alert spam during a sustained classifier outage (e.g. the
    Claude Max 5-hour window). After `threshold` consecutive failures the breaker
    opens: new leads are stored unclassified with NO per-post alert, one probe
    classification runs per cycle, and on recovery the backlog is drained."""

    def __init__(self, threshold: int = 3) -> None:
        self.threshold = threshold
        self.consecutive_failures = 0
        self._open = False

    def is_open(self) -> bool:
        return self._open

    def record_failure(self) -> bool:
        """Returns True iff this failure just opened the breaker."""
        self.consecutive_failures += 1
        if not self._open and self.consecutive_failures >= self.threshold:
            self._open = True
            return True
        return False

    def record_success(self) -> bool:
        """Returns True iff this success just closed the breaker."""
        was_open = self._open
        self._open = False
        self.consecutive_failures = 0
        return was_open


_breaker = ClassifierBreaker()

_BACKLOG_BATCH = 20


async def _fetch_backlog(session, pack_name: str, limit: int):
    from sqlalchemy import select

    from app.models.raw_post import RawPost

    result = await session.execute(
        select(RawPost)
        .where(
            RawPost.pack == pack_name,
            RawPost.classified_at.is_(None),
            RawPost.alerted_at.is_(None),
        )
        .order_by(RawPost.id)
        .limit(limit)
    )
    return list(result.scalars().all())


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


async def _send_and_mark(session, notifier, np, pack_name: str, card: str, summary: dict) -> None:
    if await notifier.send(card):
        np.alerted_at = datetime.now(UTC)
        summary["alerted"] += 1
        session.add(Event(kind="alert_sent", payload={"raw_post_id": np.id, "pack": pack_name}))
    else:
        session.add(Event(kind="alert_failed", payload={"raw_post_id": np.id, "pack": pack_name}))


async def run_poll_cycle(
    *,
    session_factory=None,
    notifier=None,
    packs=None,
    poll_fn=None,
    classify_fn=None,
    breaker: ClassifierBreaker | None = None,
) -> dict:
    """Poll every enabled pack once. Returns summary counts."""
    settings = get_settings()
    session_factory = session_factory or get_session_factory()
    notifier = notifier or get_notifier(settings)
    packs = packs if packs is not None else load_packs(_resolve_packs_dir(settings.PACKS_DIR))
    poll_fn = poll_fn or select_poll_fn(settings)
    classify_fn = classify_fn or _default_classify
    breaker = breaker or _breaker
    if not packs:
        log.warning("no enabled packs loaded from %s — nothing to poll", settings.PACKS_DIR)

    started = time.monotonic()
    summary = {
        "fetched": 0, "matched": 0, "new": 0,
        "classified": 0, "surfaced": 0, "suppressed": 0, "deferred": 0, "alerted": 0,
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

                # Work queue: fresh posts, plus (breaker closed) any outage backlog,
                # or (breaker open, nothing new) one backlog item as the probe.
                queue = list(new_posts)
                if not breaker.is_open():
                    seen_ids = {np.id for np in queue}
                    queue += [
                        p
                        for p in await _fetch_backlog(session, pack.name, _BACKLOG_BATCH)
                        if p.id not in seen_ids
                    ]
                elif not queue:
                    queue = await _fetch_backlog(session, pack.name, 1)

                probed_while_open = False
                for np in queue:
                    # open breaker: exactly one probe classification per cycle
                    if breaker.is_open() and probed_while_open:
                        summary["deferred"] += 1
                        continue
                    probed_while_open = probed_while_open or breaker.is_open()

                    row = {
                        "community": np.community,
                        "author_handle": np.author_handle,
                        "title": np.title,
                        "text": np.text,
                    }
                    try:
                        score = await classify_fn(session, pack, row, np.id)
                    except Exception:
                        log.exception("classify_fn raised (raw_post_id=%s)", np.id)
                        score = None

                    if score is None:
                        if breaker.record_failure():
                            # just opened: defer the REST of this cycle too — probing
                            # again seconds into the same outage is wasted spend
                            probed_while_open = True
                            session.add(
                                Event(
                                    kind="classifier_breaker_open",
                                    payload={"pack": pack.name, "raw_post_id": np.id},
                                )
                            )
                            await notifier.send(
                                "⚠️ LeadFinder: classifier appears down (rate limit?). "
                                "New leads are stored and will be scored on recovery."
                            )
                        if breaker.is_open():
                            summary["deferred"] += 1
                            continue
                        # sporadic failure -> surface UNSCORED rather than lose the lead
                        summary["surfaced"] += 1
                        card = format_alert(
                            np, pack.name, np.matched_keywords, score=None, unscored=True
                        )
                        await _send_and_mark(session, notifier, np, pack.name, card, summary)
                        continue

                    if breaker.record_success():
                        session.add(
                            Event(kind="classifier_breaker_closed", payload={"pack": pack.name})
                        )
                        log.info("classifier recovered — backlog drains over next cycles")
                    summary["classified"] += 1
                    np.classified_at = datetime.now(UTC)
                    np.fit_score = score.fit_score
                    np.score = score.model_dump()

                    # threshold gate: sub-threshold is stored, not surfaced (DESIGN §3.3)
                    if score.fit_score < pack.threshold:
                        summary["suppressed"] += 1
                        continue
                    summary["surfaced"] += 1
                    card = format_alert(np, pack.name, np.matched_keywords, score=score)
                    await _send_and_mark(session, notifier, np, pack.name, card, summary)
                await session.commit()

    duration_ms = int((time.monotonic() - started) * 1000)
    async with session_factory() as session:
        session.add(Event(kind="poll_cycle", payload=summary | {"duration_ms": duration_ms}))
        await session.commit()

    log.info("poll cycle done %s (%dms)", summary, duration_ms)
    return summary
