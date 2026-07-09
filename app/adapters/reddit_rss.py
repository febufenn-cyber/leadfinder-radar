"""Reddit RSS adapter (DESIGN §2) — no OAuth, fine for v1 polling.

Feeds: /r/{sub}/new/.rss per target sub, plus /search.rss?q="phrase"&sort=new per
pack search query. Politeness: descriptive User-Agent, sequential fetches with
spacing, caller polls on a 2-minute cycle.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import feedparser
import httpx

from app.filtering import strip_html
from app.packs import OfferPack

log = logging.getLogger(__name__)

SUB_FEED = "https://www.reddit.com/r/{sub}/new/.rss?limit=50"
SEARCH_FEED = "https://www.reddit.com/search.rss?q={query}&sort=new&limit=50"
_FETCH_SPACING_SECONDS = 1.0


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
    urls = [SUB_FEED.format(sub=sub) for sub in pack.reddit.subreddits]
    urls += [
        SEARCH_FEED.format(query=quote(f'"{q}"')) for q in pack.reddit.search_queries
    ]
    return urls


async def poll(pack: OfferPack, client: httpx.AsyncClient) -> list[RawPostData]:
    """Fetch all feeds for a pack; in-batch dedup by external_id; failures are logged."""
    seen: dict[str, RawPostData] = {}
    for i, url in enumerate(_feed_urls(pack)):
        if i:
            await asyncio.sleep(_FETCH_SPACING_SECONDS)
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("feed fetch failed url=%s err=%s", url, exc)
            continue
        for post in parse_feed(resp.content):
            seen.setdefault(post.external_id, post)
    return list(seen.values())
