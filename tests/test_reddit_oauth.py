"""Reddit OAuth adapter: token lifecycle + JSON listing parsing + adapter selection."""

import json
import time
from pathlib import Path

from app.adapters.reddit_oauth import RedditOAuth, parse_listing
from app.core.config import Settings
from app.packs import OfferPack
from app.pipeline import _reddit_poll_fn, select_poll_fn

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_parse_listing_maps_contract_fields():
    posts = parse_listing(json.loads((FIXTURES / "reddit_listing.json").read_text()))
    assert len(posts) == 2
    p = posts[0]
    assert p.source == "reddit"
    assert p.external_id == "t3_1oauth1"
    assert p.url == "https://www.reddit.com/r/smallbusiness/comments/1oauth1/need_a_website_for_my_salon/"
    assert p.author_handle == "/u/salonlady"
    assert p.author_url == "https://www.reddit.com/user/salonlady"
    assert p.community == "smallbusiness"
    assert "booking website" in p.text
    assert p.created_at.timestamp() == 1783978200
    assert p.raw["link_flair_text"] is None
    assert posts[1].raw["link_flair_text"] == "For Hire"


def test_parse_listing_garbage_returns_empty():
    assert parse_listing({}) == []
    assert parse_listing({"data": {"children": [{"kind": "t3", "data": {}}]}}) == []


class FakeTokenClient:
    """Serves the token endpoint then listing endpoints; counts token fetches."""

    def __init__(self, listing: dict):
        self.listing = listing
        self.token_fetches = 0
        self.listing_calls: list[str] = []

    async def post(self, url, data=None, auth=None, headers=None):
        self.token_fetches += 1
        return _Resp(200, {"access_token": f"tok{self.token_fetches}", "expires_in": 3600})

    async def get(self, url, headers=None, params=None):
        self.listing_calls.append(url)
        return _Resp(200, self.listing)


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.headers = {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        import httpx

        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                str(self.status_code), request=httpx.Request("GET", "http://x"), response=self
            )


async def test_token_cached_until_expiry(monkeypatch):
    listing = json.loads((FIXTURES / "reddit_listing.json").read_text())
    client = FakeTokenClient(listing)
    adapter = RedditOAuth(client_id="cid", client_secret="sec")
    monkeypatch.setattr("app.adapters.reddit_oauth._FETCH_SPACING_SECONDS", 0)
    pack = OfferPack(
        name="x",
        reddit={"subreddits": ["smallbusiness"], "search_queries": ["need a website"]},
        keywords={"include": ["need a website"]},
    )
    posts = await adapter.poll(pack, client)
    assert client.token_fetches == 1
    assert {p.external_id for p in posts} == {"t3_1oauth1", "t3_1oauth2"}
    assert len(client.listing_calls) == 2  # multireddit /new + one search

    await adapter.poll(pack, client)
    assert client.token_fetches == 1  # cached

    adapter._token_expires_at = time.monotonic() - 1  # force expiry
    await adapter.poll(pack, client)
    assert client.token_fetches == 2  # refreshed


def test_reddit_poll_fn_prefers_oauth_when_creds_present():
    with_creds = Settings(REDDIT_CLIENT_ID="cid", REDDIT_CLIENT_SECRET="sec", _env_file=None)
    without = Settings(REDDIT_CLIENT_ID="", REDDIT_CLIENT_SECRET="", _env_file=None)
    assert _reddit_poll_fn(with_creds).__qualname__.startswith("RedditOAuth")
    assert _reddit_poll_fn(without).__module__ == "app.adapters.reddit_rss"


async def test_composite_poll_merges_sources(monkeypatch):
    from app.adapters import hn
    from app.adapters.reddit_rss import RawPostData
    from datetime import UTC, datetime

    def fake_source(source, external_id):
        return RawPostData(
            source=source, external_id=external_id, url="https://x/",
            author_handle=None, author_url=None, community=None, title="t",
            text="", created_at=datetime.now(UTC),
        )

    async def fake_reddit(pack, client):
        return [fake_source("reddit", "r1")]

    async def fake_hn(pack, client):
        return [fake_source("hn", "h1")]

    monkeypatch.setattr("app.pipeline._reddit_poll_fn", lambda s: fake_reddit)
    monkeypatch.setattr(hn, "poll", fake_hn)
    settings = Settings(THREADS_ACCESS_TOKEN="", _env_file=None)
    pack = OfferPack(
        name="x",
        reddit={"subreddits": ["a"]},
        hn={"search_queries": ["q"]},
        threads={"search_queries": ["q"]},  # no token -> must NOT be polled
        keywords={"include": ["k"]},
    )
    posts = await select_poll_fn(settings)(pack, client=None)
    assert {p.source for p in posts} == {"reddit", "hn"}
