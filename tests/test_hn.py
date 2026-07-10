"""HN adapter: Algolia search_by_date -> RawPostData (DESIGN §2)."""

import json
from pathlib import Path

from app.adapters.hn import parse_hits, poll
from app.packs import OfferPack

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_parse_hits_maps_contract():
    posts = parse_hits(json.loads((FIXTURES / "hn_search.json").read_text()))
    assert len(posts) == 2
    p = posts[0]
    assert p.source == "hn"
    assert p.external_id == "41999001"
    assert p.url == "https://news.ycombinator.com/item?id=41999001"
    assert p.author_handle == "founder123"
    assert p.author_url == "https://news.ycombinator.com/user?id=founder123"
    assert p.community == "hackernews"
    assert "Budget ~$2k" in p.text
    assert "<p>" not in p.text  # html stripped
    assert p.created_at.timestamp() == 1783978200
    # second hit has no story_text -> empty text, still valid
    assert posts[1].text == ""


def test_parse_garbage():
    assert parse_hits({}) == []
    assert parse_hits({"hits": [{"author": None}]}) == []


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def get(self, url):
        self.calls.append(url)

        class R:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(inner):
                return self.payload

        return R()


async def test_poll_queries_each_term(monkeypatch):
    from app.adapters import hn

    monkeypatch.setattr(hn, "_FETCH_SPACING_SECONDS", 0)
    pack = OfferPack(
        name="x",
        hn={"search_queries": ["need a website", "recommend an agency"]},
        keywords={"include": ["need a website"]},
    )
    client = FakeClient(json.loads((FIXTURES / "hn_search.json").read_text()))
    posts = await poll(pack, client)
    assert len(client.calls) == 2
    assert "hn.algolia.com" in client.calls[0]
    assert len(posts) == 2  # in-batch dedup across queries
