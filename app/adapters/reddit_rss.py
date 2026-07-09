"""Reddit RSS adapter (DESIGN §2) — no OAuth, fine for v1 polling.

Feeds: /r/{sub}/new/.rss per target sub, plus /search.rss?q="phrase"&sort=new per
pack search query. Politeness: descriptive User-Agent, sequential fetches with
spacing, caller polls on a 2-minute cycle.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import feedparser
import httpx

from app.filtering import strip_html
from app.packs import OfferPack

log = logging.getLogger(__name__)

# Unauthenticated Reddit rate limits are tight (~10 req/min/IP and it 429s fast),
# so batch everything: ONE multireddit feed for all subs, ONE OR-combined search.
SUB_FEED = "https://www.reddit.com/r/{subs}/new/.rss?limit=50"
SEARCH_FEED = "https://www.reddit.com/search.rss?q={query}&sort=new&limit=50"
_FETCH_SPACING_SECONDS = 2.0

# Per-URL cooldown after a 429: retrying every cycle keeps the throttle hot.
# search.rss in particular is limited far harder than sub feeds.
_cooldown_until: dict[str, datetime] = {}
_DEFAULT_COOLDOWN = timedelta(minutes=15)


@dataclass
class RawPostData:
    """DESIGN §2 adapter contract."""

    source: str
    external_id: str
    url: str
    author_handle: str | None
    author_url: str | None
    community: str | None
    title: str | None
    text: str
    created_at: datetime
    raw: dict[str, Any] = field(default_factory=dict)


def _community_from_link(link: str) -> str | None:
    # https://www.reddit.com/r/<sub>/comments/... -> <sub>
    parts = link.split("/r/", 1)
    if len(parts) == 2 and "/" in parts[1]:
        return parts[1].split("/", 1)[0]
    return None


def parse_feed(xml: bytes | str) -> list[RawPostData]:
    """Parse a Reddit Atom feed into RawPostData items. Malformed feeds -> []."""
    parsed = feedparser.parse(xml)
    posts: list[RawPostData] = []
    for entry in parsed.entries:
        external_id = entry.get("id", "")
        link = entry.get("link", "")
        published = entry.get("published_parsed") or entry.get("updated_parsed")
        if not (external_id and link and published):
            log.warning("skipping malformed feed entry: id=%r link=%r", external_id, link)
            continue
        content = entry.get("content")
        body_html = content[0].get("value", "") if content else entry.get("summary", "")
        author_detail = entry.get("author_detail") or {}
        posts.append(
            RawPostData(
                source="reddit",
                external_id=external_id,
                url=link,
                author_handle=entry.get("author"),
                author_url=author_detail.get("href"),
                community=_community_from_link(link),
                title=entry.get("title"),
                text=strip_html(body_html),
                created_at=datetime(*published[:6], tzinfo=UTC),
                raw={
                    "id": external_id,
                    "link": link,
                    "title": entry.get("title"),
                    "published": entry.get("published") or entry.get("updated"),
                    "author": entry.get("author"),
                },
            )
        )
    return posts


def _feed_urls(pack: OfferPack) -> list[str]:
    urls = []
    if pack.reddit.subreddits:
        urls.append(SUB_FEED.format(subs="+".join(pack.reddit.subreddits)))
    if pack.reddit.search_queries:
        combined = " OR ".join(f'"{q}"' for q in pack.reddit.search_queries)
        urls.append(SEARCH_FEED.format(query=quote(combined)))
    return urls


async def poll(pack: OfferPack, client: httpx.AsyncClient) -> list[RawPostData]:
    """Fetch all feeds for a pack; in-batch dedup by external_id; failures are logged."""
    seen: dict[str, RawPostData] = {}
    first_fetch = True
    for url in _feed_urls(pack):
        now = datetime.now(UTC)
        if url in _cooldown_until and now < _cooldown_until[url]:
            log.info("feed on 429 cooldown until %s: %s", _cooldown_until[url], url)
            continue
        if not first_fetch:
            await asyncio.sleep(_FETCH_SPACING_SECONDS)
        first_fetch = False
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                retry_after = exc.response.headers.get("retry-after", "")
                cooldown = (
                    timedelta(seconds=int(retry_after))
                    if retry_after.isdigit()
                    else _DEFAULT_COOLDOWN
                )
                _cooldown_until[url] = now + max(cooldown, _DEFAULT_COOLDOWN)
                log.warning("429 — cooling %s down until %s", url, _cooldown_until[url])
            else:
                log.warning("feed fetch failed url=%s err=%s", url, exc)
            continue
        except httpx.HTTPError as exc:
            log.warning("feed fetch failed url=%s err=%s", url, exc)
            continue
        _cooldown_until.pop(url, None)
        for post in parse_feed(resp.content):
            seen.setdefault(post.external_id, post)
    return list(seen.values())
