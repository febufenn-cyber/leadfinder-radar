"""Threads adapter (DESIGN §2) — official Threads API keyword search ONLY.

Threads blocks scrapers (robots-disallowed); this uses the Meta-app access
token the owner creates. Search quotas are limited per day, so every API call
is written to the events table (kind="threads_query") and the budgeter reads
that ledger — durable across worker restarts, unlike an in-memory counter.

Cadence guidance from the design: ~10-15 min between polls, budget queries per
pack. Verify current quotas at developers.facebook.com/docs/threads.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import httpx
from sqlalchemy import func, select

from app.adapters.reddit_rss import RawPostData
from app.core.config import get_settings
from app.models.event import Event
from app.packs import OfferPack

log = logging.getLogger(__name__)

KEYWORD_SEARCH = (
    "https://graph.threads.net/v1.0/keyword_search"
    "?q={q}&search_type=RECENT&fields=id,text,username,permalink,timestamp"
    "&access_token={token}"
)
_FETCH_SPACING_SECONDS = 1.0


def _parse_ts(value: str) -> datetime | None:
    for fmt in ("%Y-%m-%dT%H:%M:%S%z",):
        try:
            return datetime.strptime(value, fmt)
        except (ValueError, TypeError):
            continue
    return None


def parse_threads(payload: dict) -> list[RawPostData]:
    posts: list[RawPostData] = []
    for item in payload.get("data", []):
        post_id = item.get("id")
        username = item.get("username")
        permalink = item.get("permalink")
        created = _parse_ts(item.get("timestamp", ""))
        if not (post_id and username and permalink and created):
            continue
        posts.append(
            RawPostData(
                source="threads",
                external_id=str(post_id),
                url=permalink,
                author_handle=f"@{username}",
                author_url=f"https://www.threads.net/@{username}",
                community=None,
                title=None,
                text=(item.get("text") or "")[:10_000],
                created_at=created.astimezone(UTC),
                raw={"id": str(post_id), "username": username, "permalink": permalink},
            )
        )
    return posts


class ThreadsAdapter:
    def __init__(self, access_token: str, daily_budget: int, min_interval_minutes: int) -> None:
        self._token = access_token
        self._daily_budget = daily_budget
        self._min_interval = timedelta(minutes=min_interval_minutes)
        self.session_factory = None  # set lazily; tests inject the test factory

    def _factory(self):
        if self.session_factory is None:
            from app.db.session import get_session_factory

            self.session_factory = get_session_factory()
        return self.session_factory

    async def _budget_state(self) -> tuple[int, datetime | None]:
        """(queries used today UTC, ts of the most recent query)."""
        async with self._factory()() as session:
            today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            used = await session.scalar(
                select(func.count())
                .select_from(Event)
                .where(Event.kind == "threads_query", Event.ts >= today_start)
            )
            last = await session.scalar(
                select(func.max(Event.ts)).where(Event.kind == "threads_query")
            )
        return int(used or 0), last

    async def _record_query(self, q: str, pack_name: str) -> None:
        async with self._factory()() as session:
            session.add(Event(kind="threads_query", payload={"q": q, "pack": pack_name}))
            await session.commit()

    async def poll(self, pack: OfferPack, client: httpx.AsyncClient) -> list[RawPostData]:
        queries = pack.threads.search_queries
        if not queries:
            return []
        used, last = await self._budget_state()
        now = datetime.now(UTC)
        if last is not None and now - last < self._min_interval:
            log.info(
                "threads poll skipped: last query %s, min interval %s",
                last, self._min_interval,
            )
            return []
        if used + len(queries) > self._daily_budget:
            log.warning(
                "threads daily budget reached (%d/%d) — skipping until tomorrow UTC",
                used, self._daily_budget,
            )
            return []

        seen: dict[str, RawPostData] = {}
        for i, q in enumerate(queries):
            if i:
                await asyncio.sleep(_FETCH_SPACING_SECONDS)
            url = KEYWORD_SEARCH.format(q=quote(q), token=self._token)
            await self._record_query(q, pack.name)  # ledger first: crash-safe budget
            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                body = getattr(exc.response, "text", "")[:300]
                if exc.response.status_code in (400, 401, 403) and "OAuth" in str(body):
                    log.error(
                        "threads token rejected — regenerate the long-lived token in the "
                        "Meta app (developers.facebook.com). body=%s", body,
                    )
                else:
                    log.warning("threads fetch failed q=%r status=%s", q, exc.response.status_code)
                continue
            except httpx.HTTPError as exc:
                log.warning("threads fetch failed q=%r err=%s", q, exc)
                continue
            posts = parse_threads(resp.json())
            log.info("threads feed ok entries=%d q=%r", len(posts), q)
            for post in posts:
                seen.setdefault(post.external_id, post)
        return list(seen.values())


_adapter: ThreadsAdapter | None = None


def get_threads_adapter() -> ThreadsAdapter:
    global _adapter
    if _adapter is None:
        settings = get_settings()
        _adapter = ThreadsAdapter(
            access_token=settings.THREADS_ACCESS_TOKEN,
            daily_budget=settings.THREADS_DAILY_QUERY_BUDGET,
            min_interval_minutes=settings.THREADS_MIN_INTERVAL_MINUTES,
        )
    return _adapter
