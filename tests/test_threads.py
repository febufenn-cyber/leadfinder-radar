"""Threads adapter: official keyword-search API + DB-backed quota budgeter (DESIGN §2)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.adapters.threads import ThreadsAdapter, parse_threads
from app.models.event import Event
from app.packs import OfferPack

FIXTURES = Path(__file__).resolve().parent / "fixtures"

PACK = OfferPack(
    name="zervvo_abroad",
    threads={"search_queries": ["study in germany", "ielts coaching"]},
    keywords={"include": ["study in germany", "ielts"]},
)


def test_parse_threads_maps_contract():
    posts = parse_threads(json.loads((FIXTURES / "threads_search.json").read_text()))
    assert len(posts) == 2
    p = posts[0]
    assert p.source == "threads"
    assert p.external_id == "17900001"
    assert p.url == "https://www.threads.net/@priya.dreams/post/C9xYz123"
    assert p.author_handle == "@priya.dreams"
    assert p.author_url == "https://www.threads.net/@priya.dreams"
    assert p.community is None
    assert "study abroad consultant" in p.text
    assert p.created_at == datetime(2026, 7, 10, 5, 45, tzinfo=UTC)


def test_parse_garbage():
    assert parse_threads({}) == []
    assert parse_threads({"data": [{"id": "x"}]}) == []


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


def make_adapter(db_factory, budget=48, min_interval=15):
    adapter = ThreadsAdapter(
        access_token="tok123", daily_budget=budget, min_interval_minutes=min_interval
    )
    adapter.session_factory = db_factory
    return adapter


async def test_poll_queries_and_logs_budget_events(db_factory, monkeypatch):
    from app.adapters import threads as mod

    monkeypatch.setattr(mod, "_FETCH_SPACING_SECONDS", 0)
    adapter = make_adapter(db_factory)
    client = FakeClient(json.loads((FIXTURES / "threads_search.json").read_text()))
    posts = await adapter.poll(PACK, client)
    assert len(posts) == 2
    assert len(client.calls) == 2
    assert "access_token=tok123" in client.calls[0]
    async with db_factory() as session:
        events = (
            await session.execute(select(Event).where(Event.kind == "threads_query"))
        ).scalars().all()
        assert len(events) == 2  # one per API call — the durable budget ledger


async def test_budget_exhausted_skips_all_calls(db_factory):
    adapter = make_adapter(db_factory, budget=2)
    async with db_factory() as session:
        session.add(Event(kind="threads_query", payload={"q": "a"}))
        session.add(Event(kind="threads_query", payload={"q": "b"}))
        await session.commit()
    client = FakeClient({"data": []})
    posts = await adapter.poll(PACK, client)
    assert posts == []
    assert client.calls == []


async def test_min_interval_skips_poll(db_factory):
    adapter = make_adapter(db_factory, min_interval=15)
    async with db_factory() as session:
        session.add(Event(kind="threads_query", payload={"q": "a"}))
        await session.commit()  # ts=now -> within the 15-min window
    client = FakeClient({"data": []})
    posts = await adapter.poll(PACK, client)
    assert client.calls == []
    assert posts == []


async def test_stale_last_query_allows_poll(db_factory):
    adapter = make_adapter(db_factory, min_interval=15)
    async with db_factory() as session:
        session.add(
            Event(
                kind="threads_query",
                ts=datetime.now(UTC) - timedelta(minutes=30),
                payload={"q": "a"},
            )
        )
        await session.commit()
    client = FakeClient({"data": []})
    await adapter.poll(PACK, client)
    assert len(client.calls) == 2
