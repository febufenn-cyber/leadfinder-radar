"""Reddit RSS adapter: Atom feed -> RawPostData per the DESIGN §2 contract."""

from datetime import UTC, datetime
from pathlib import Path

from app.adapters.reddit_rss import _feed_urls, parse_feed
from app.packs import OfferPack

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_feed_urls_batch_into_two_requests():
    """Unauthenticated Reddit 429s fast — one multireddit feed + one OR search, not N feeds."""
    pack = OfferPack(
        name="x",
        reddit={
            "subreddits": ["smallbusiness", "Entrepreneur", "forhire"],
            "search_queries": ["need a website", "build me a website"],
        },
        keywords={"include": ["need a website"]},
    )
    urls = _feed_urls(pack)
    assert len(urls) == 2
    assert "/r/smallbusiness+Entrepreneur+forhire/new/.rss" in urls[0]
    assert "%22need%20a%20website%22%20OR%20%22build%20me%20a%20website%22" in urls[1]


def test_feed_urls_empty_sections_omitted():
    pack = OfferPack(name="x", keywords={"include": ["a"]})
    assert _feed_urls(pack) == []


def test_parse_new_feed():
    posts = parse_feed((FIXTURES / "reddit_new.xml").read_bytes())
    assert len(posts) == 2
    p = posts[0]
    assert p.source == "reddit"
    assert p.external_id == "t3_1abc23"
    assert p.url.endswith("/need_a_website_for_my_bakery/")
    assert p.author_handle == "/u/shopowner42"
    assert p.author_url == "https://www.reddit.com/user/shopowner42"
    assert p.community == "smallbusiness"
    assert p.title == "Need a website for my bakery"
    assert "budget around $500" in p.text
    assert "<p>" not in p.text  # html stripped
    assert p.created_at == datetime(2026, 7, 10, 0, 10, tzinfo=UTC)
    assert p.raw["id"] == "t3_1abc23"


def test_parse_search_feed_extracts_community_from_link():
    (p,) = parse_feed((FIXTURES / "reddit_search.xml").read_bytes())
    assert p.community == "Entrepreneur"
    assert p.external_id == "t3_1xyz99"


def test_parse_garbage_returns_empty():
    assert parse_feed(b"not xml at all") == []


def test_parse_rejects_non_http_links():
    """A hostile feed entry must not smuggle javascript: URIs into alert/dashboard hrefs."""
    evil = (
        (FIXTURES / "reddit_new.xml")
        .read_text()
        .replace(
            "https://www.reddit.com/r/smallbusiness/comments/1abc23/need_a_website_for_my_bakery/",
            "javascript:alert(1)//",
        )
    )
    posts = parse_feed(evil)
    assert [p.external_id for p in posts] == ["t3_1abc24"]  # evil entry dropped


class FakeResponse:
    def __init__(self, status_code: int, content: bytes = b"", headers: dict | None = None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self):
        import httpx

        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=httpx.Request("GET", "http://x"), response=self
            )


class FakeClient:
    """Returns a queued response per URL and counts hits."""

    def __init__(self, responses: dict[str, FakeResponse]):
        self.responses = responses
        self.calls: list[str] = []

    async def get(self, url: str) -> FakeResponse:
        self.calls.append(url)
        return self.responses[url]


async def test_429_puts_feed_on_cooldown(monkeypatch):
    from app.adapters import reddit_rss

    monkeypatch.setattr(reddit_rss, "_FETCH_SPACING_SECONDS", 0)
    reddit_rss._cooldown_until.clear()
    pack = OfferPack(
        name="x",
        reddit={"subreddits": ["smallbusiness"], "search_queries": ["need a website"]},
        keywords={"include": ["need a website"]},
    )
    sub_url, search_url = reddit_rss._feed_urls(pack)
    ok_feed = (FIXTURES / "reddit_new.xml").read_bytes()
    client = FakeClient(
        {sub_url: FakeResponse(200, ok_feed), search_url: FakeResponse(429)}
    )

    posts = await reddit_rss.poll(pack, client)
    assert len(posts) == 2  # sub feed still parsed
    assert search_url in reddit_rss._cooldown_until

    # second cycle: search feed skipped entirely while cooling down
    await reddit_rss.poll(pack, client)
    assert client.calls.count(search_url) == 1
    assert client.calls.count(sub_url) == 2
    reddit_rss._cooldown_until.clear()
