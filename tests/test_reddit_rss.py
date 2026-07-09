"""Reddit RSS adapter: Atom feed -> RawPostData per the DESIGN §2 contract."""

from datetime import UTC, datetime
from pathlib import Path

from app.adapters.reddit_rss import parse_feed

FIXTURES = Path(__file__).resolve().parent / "fixtures"


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
