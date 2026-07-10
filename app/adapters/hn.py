"""Hacker News adapter (DESIGN §2) — Algolia HN Search API, free, no auth.

Great for "Ask HN: recommend …" dev-service demand. Stories only in v1;
comments are high-noise. Reply URL is always the HN thread.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from app.adapters.reddit_rss import RawPostData
from app.filtering import strip_html
from app.packs import OfferPack

log = logging.getLogger(__name__)

SEARCH = "https://hn.algolia.com/api/v1/search_by_date?query={q}&tags=story&hitsPerPage=50"
_FETCH_SPACING_SECONDS = 0.3


def parse_hits(payload: dict) -> list[RawPostData]:
    posts: list[RawPostData] = []
    for hit in payload.get("hits", []):
        object_id = hit.get("objectID")
        author = hit.get("author")
        created = hit.get("created_at_i")
        if not (object_id and author and created):
            continue
        posts.append(
            RawPostData(
                source="hn",
                external_id=str(object_id),
                url=f"https://news.ycombinator.com/item?id={object_id}",
                author_handle=author,
                author_url=f"https://news.ycombinator.com/user?id={author}",
                community="hackernews",
                title=hit.get("title"),
                text=strip_html(hit.get("story_text") or ""),
                created_at=datetime.fromtimestamp(float(created), tz=UTC),
                raw={
                    "id": str(object_id),
                    "title": hit.get("title"),
                    "author": author,
                    "external_url": hit.get("url"),
                    "tags": hit.get("_tags", []),
                },
            )
        )
    return posts


async def poll(pack: OfferPack, client: httpx.AsyncClient) -> list[RawPostData]:
    seen: dict[str, RawPostData] = {}
    for i, q in enumerate(pack.hn.search_queries):
        if i:
            await asyncio.sleep(_FETCH_SPACING_SECONDS)
        url = SEARCH.format(q=quote(q))
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("hn fetch failed url=%s err=%s", url, exc)
            continue
        posts = parse_hits(resp.json())
        log.info("hn feed ok entries=%d q=%r", len(posts), q)
        for post in posts:
            seen.setdefault(post.external_id, post)
    return list(seen.values())
