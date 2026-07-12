"""Threads discovery via Google Programmable Search (CSE) — the compliant
bridge while threads_keyword_search public access sits behind Meta App Review.

Google's Custom Search JSON API is an official Google API over Google's own
index of public threads.com posts — no Threads scraping (DESIGN §2 rules that
out). Discovery-only: CSE gives the post permalink + text snippet, not the
Threads media id, so these leads are copy-mode (approval.py already refuses
api-send for unknown sources).

Freshness: results are bounded by dateRestrict (default d1); post age is
parsed from the "N hours ago …" snippet prefix Google adds, falling back to
fetch time. Free tier is 100 queries/day — the min-interval gate keeps a
4-query pack under that at the default 60 min spacing.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import httpx

from app.adapters.reddit_rss import RawPostData
from app.core.config import get_settings
from app.packs import OfferPack

log = logging.getLogger(__name__)

_adapter: "ThreadsCseAdapter | None" = None

SEARCH = (
    "https://www.googleapis.com/customsearch/v1?key={key}&cx={cx}&q={q}"
    "&siteSearch=threads.com&siteSearchFilter=i&dateRestrict={fresh}&num=10"
)
_FETCH_SPACING_SECONDS = 0.3
_POST_URL = re.compile(r"https://(?:www\.)?threads\.(?:com|net)/@([^/]+)/post/([A-Za-z0-9_-]+)")
_AGE_PREFIX = re.compile(r"^(\d+)\s+(minute|hour|day)s?\s+ago", re.IGNORECASE)
_AGE_UNITS = {"minute": 60, "hour": 3600, "day": 86400}


def _created_at(snippet: str, now: datetime) -> datetime:
    m = _AGE_PREFIX.match(snippet.strip())
    if not m:
        return now
    return now - timedelta(seconds=int(m.group(1)) * _AGE_UNITS[m.group(2).lower()])


def parse_items(payload: dict, now: datetime | None = None) -> list[RawPostData]:
    now = now or datetime.now(UTC)
    posts: list[RawPostData] = []
    for item in payload.get("items", []):
        m = _POST_URL.match(item.get("link") or "")
        if not m:
            continue  # profile pages, tag pages — not posts
        handle, shortcode = m.group(1), m.group(2)
        snippet = item.get("snippet") or ""
        # og:description usually carries more of the post text than the snippet
        meta = (item.get("pagemap") or {}).get("metatags") or [{}]
        og_desc = meta[0].get("og:description") or ""
        text = og_desc if len(og_desc) > len(snippet) else snippet
        posts.append(
            RawPostData(
                source="threads_cse",
                external_id=shortcode,
                url=f"https://www.threads.com/@{handle}/post/{shortcode}",
                author_handle=handle,
                author_url=f"https://www.threads.com/@{handle}",
                community="threads",
                title=None,
                text=_AGE_PREFIX.sub("", text.strip()).lstrip(" .·—-"),
                created_at=_created_at(snippet, now),
                raw={
                    "shortcode": shortcode,
                    "handle": handle,
                    "title": item.get("title"),
                    "snippet": snippet,
                },
            )
        )
    return posts


class ThreadsCseAdapter:
    """Per-pack min-interval gate keeps the free 100-queries/day tier intact."""

    def __init__(self, key: str, cx: str, min_interval_minutes: int, fresh: str = "d1") -> None:
        self._key = key
        self._cx = cx
        self._min_interval = timedelta(minutes=min_interval_minutes)
        self._fresh = fresh
        self._last_poll: dict[str, datetime] = {}

    async def poll(self, pack: OfferPack, client: httpx.AsyncClient) -> list[RawPostData]:
        now = datetime.now(UTC)
        last = self._last_poll.get(pack.name)
        if last is not None and now - last < self._min_interval:
            return []
        self._last_poll[pack.name] = now

        seen: dict[str, RawPostData] = {}
        for i, q in enumerate(pack.threads.search_queries):
            if i:
                await asyncio.sleep(_FETCH_SPACING_SECONDS)
            url = SEARCH.format(key=self._key, cx=self._cx, q=quote(q), fresh=self._fresh)
            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                log.warning("threads_cse fetch failed q=%r err=%s", q, exc)
                continue
            posts = parse_items(resp.json(), now=now)
            log.info("threads_cse ok results=%d q=%r", len(posts), q)
            for post in posts:
                seen.setdefault(post.external_id, post)
        return list(seen.values())


def get_cse_adapter() -> ThreadsCseAdapter:
    """Singleton — run_poll_cycle rebuilds poll_fn every cron cycle, and the
    min-interval gate only works if _last_poll survives across cycles."""
    global _adapter
    if _adapter is None:
        settings = get_settings()
        _adapter = ThreadsCseAdapter(
            key=settings.GOOGLE_CSE_KEY,
            cx=settings.GOOGLE_CSE_ID,
            min_interval_minutes=settings.GOOGLE_CSE_MIN_INTERVAL_MINUTES,
        )
    return _adapter
