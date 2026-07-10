"""Reddit official OAuth adapter (DESIGN §2 primary path) — application-only grant.

Why this exists: unauthenticated search.rss is throttled to uselessness, while
the OAuth free tier allows ~100 QPM. Uses the owner's script app credentials
(reddit.com/prefs/apps) with the client_credentials grant — read-only access to
public listings, no user login involved.

Same politeness rules as the RSS adapter: descriptive UA, sequential fetches
with spacing, per-URL cooldown on 429.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import httpx

from app.adapters.reddit_rss import RawPostData
from app.core.config import get_settings
from app.packs import OfferPack

log = logging.getLogger(__name__)

_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_API = "https://oauth.reddit.com"
_FETCH_SPACING_SECONDS = 1.0
_TOKEN_SAFETY_SECONDS = 300  # refresh 5 min before expiry
_DEFAULT_COOLDOWN = timedelta(minutes=15)


def parse_listing(payload: dict) -> list[RawPostData]:
    """Reddit JSON Listing -> RawPostData items (DESIGN §2 contract)."""
    posts: list[RawPostData] = []
    for child in (payload.get("data") or {}).get("children", []):
        d = child.get("data") or {}
        name, permalink, created = d.get("name"), d.get("permalink"), d.get("created_utc")
        author = d.get("author")
        if not (name and permalink and created and author):
            continue
        posts.append(
            RawPostData(
                source="reddit",
                external_id=name,
                url=f"https://www.reddit.com{permalink}",
                author_handle=f"/u/{author}",
                author_url=f"https://www.reddit.com/user/{author}",
                community=d.get("subreddit"),
                title=d.get("title"),
                text=(d.get("selftext") or "")[:10_000],
                created_at=datetime.fromtimestamp(float(created), tz=UTC),
                raw={
                    "id": name,
                    "permalink": permalink,
                    "title": d.get("title"),
                    "author": author,
                    "link_flair_text": d.get("link_flair_text"),
                    "over_18": d.get("over_18"),
                },
            )
        )
    return posts


class RedditOAuth:
    """Token-caching poller. One instance lives for the worker's lifetime."""

    def __init__(self, client_id: str, client_secret: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._cooldown_until: dict[str, datetime] = {}

    async def _get_token(self, client: httpx.AsyncClient) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        resp = await client.post(
            _TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(self._client_id, self._client_secret),
        )
        resp.raise_for_status()
        payload = resp.json()
        self._token = payload["access_token"]
        self._token_expires_at = (
            time.monotonic() + float(payload.get("expires_in", 3600)) - _TOKEN_SAFETY_SECONDS
        )
        log.info("reddit oauth token refreshed (expires_in=%ss)", payload.get("expires_in"))
        return self._token

    def _urls(self, pack: OfferPack) -> list[str]:
        urls = []
        if pack.reddit.subreddits:
            subs = "+".join(pack.reddit.subreddits)
            urls.append(f"{_API}/r/{subs}/new?limit=50&raw_json=1")
        for q in pack.reddit.search_queries:
            urls.append(
                f"{_API}/search?q={quote(f'\"{q}\"')}&sort=new&limit=50&type=link&raw_json=1"
            )
        return urls

    async def poll(self, pack: OfferPack, client: httpx.AsyncClient) -> list[RawPostData]:
        token = await self._get_token(client)
        headers = {"Authorization": f"Bearer {token}"}
        seen: dict[str, RawPostData] = {}
        first = True
        for url in self._urls(pack):
            now = datetime.now(UTC)
            if url in self._cooldown_until and now < self._cooldown_until[url]:
                log.info("oauth endpoint on cooldown until %s: %s", self._cooldown_until[url], url)
                continue
            if not first:
                await asyncio.sleep(_FETCH_SPACING_SECONDS)
            first = False
            try:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    self._cooldown_until[url] = now + _DEFAULT_COOLDOWN
                    log.warning("429 — cooling %s until %s", url, self._cooldown_until[url])
                elif exc.response.status_code == 401:
                    self._token = None
                    log.warning("401 from reddit oauth — token invalidated, refresh next cycle")
                    break  # every remaining URL would fail with the same dead token
                else:
                    log.warning("oauth fetch failed url=%s err=%s", url, exc)
                continue
            except httpx.HTTPError as exc:
                log.warning("oauth fetch failed url=%s err=%s", url, exc)
                continue
            self._cooldown_until.pop(url, None)
            posts = parse_listing(resp.json())
            log.info("oauth feed ok entries=%d url=%s", len(posts), url)
            for post in posts:
                seen.setdefault(post.external_id, post)
        return list(seen.values())


_adapter: RedditOAuth | None = None


def get_oauth_adapter() -> RedditOAuth:
    global _adapter
    if _adapter is None:
        settings = get_settings()
        _adapter = RedditOAuth(settings.REDDIT_CLIENT_ID, settings.REDDIT_CLIENT_SECRET)
    return _adapter
