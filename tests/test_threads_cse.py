"""Threads-via-Google-CSE adapter: discovery bridge while keyword_search
public access sits behind Meta App Review (compliant: Google's official API,
no Threads scraping)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.adapters.threads_cse import ThreadsCseAdapter, parse_items
from app.packs import OfferPack

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def _payload():
    return json.loads((FIXTURES / "threads_cse.json").read_text())


def test_parse_items_maps_contract():
    posts = parse_items(_payload(), now=NOW)
    assert len(posts) == 2  # profile-page item skipped
    p = posts[0]
    assert p.source == "threads_cse"
    assert p.external_id == "DEmoCode123"
    assert p.url == "https://www.threads.com/@priya.builds/post/DEmoCode123"
    assert p.author_handle == "priya.builds"
    assert p.author_url == "https://www.threads.com/@priya.builds"
    assert p.community == "threads"
    # og:description preferred over the shorter snippet; age prefix stripped
    assert p.text.startswith("Final year BTech")
    assert "actually replies" in p.text
    assert "hours ago" not in p.text
    # "6 hours ago" prefix parsed into created_at
    assert p.created_at == NOW - timedelta(hours=6)
    # threads.net URLs normalized to threads.com
    assert posts[1].url == "https://www.threads.com/@dev.raj/post/AbC_-9xYz"
    # no age prefix -> created_at falls back to now
    assert posts[1].created_at == NOW


def test_parse_garbage():
    assert parse_items({}, now=NOW) == []
    assert parse_items({"items": [{"link": "https://example.com"}]}, now=NOW) == []


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def get(self, url):
        self.calls.append(url)
        payload = self.payload

        class R:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return payload

        return R()


def _pack():
    return OfferPack(
        name="zervvo_abroad",
        description="test",
        keywords={"include": ["study in germany"], "exclude": []},
        threads={"search_queries": ["study in germany", "ielts coaching"]},
    )


@pytest.mark.asyncio
async def test_poll_queries_and_dedups():
    adapter = ThreadsCseAdapter(key="K", cx="CX", min_interval_minutes=60)
    client = FakeClient(_payload())
    posts = await adapter.poll(_pack(), client)
    assert len(client.calls) == 2  # one per query
    assert "siteSearch=threads.com" in client.calls[0]
    assert "key=K" in client.calls[0] and "cx=CX" in client.calls[0]
    # same fixture returned for both queries -> dedup by shortcode
    assert len(posts) == 2


def test_singleton_survives_poll_fn_rebuild(monkeypatch):
    # run_poll_cycle rebuilds poll_fn every 2-min cron cycle; the quota gate
    # only holds if the adapter (and its _last_poll memory) is a singleton.
    import app.adapters.threads_cse as mod

    monkeypatch.setattr(mod, "_adapter", None)
    monkeypatch.setenv("GOOGLE_CSE_KEY", "K")
    monkeypatch.setenv("GOOGLE_CSE_ID", "CX")
    mod.get_settings.cache_clear()
    try:
        assert mod.get_cse_adapter() is mod.get_cse_adapter()
    finally:
        monkeypatch.setattr(mod, "_adapter", None)
        mod.get_settings.cache_clear()


@pytest.mark.asyncio
async def test_poll_min_interval_gate():
    adapter = ThreadsCseAdapter(key="K", cx="CX", min_interval_minutes=60)
    client = FakeClient(_payload())
    first = await adapter.poll(_pack(), client)
    assert first
    again = await adapter.poll(_pack(), client)
    assert again == []  # gated — no new HTTP calls
    assert len(client.calls) == 2
