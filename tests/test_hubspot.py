"""HubSpot reply sync (M4): best-effort, disabled without a token, and its
failures never propagate into the watch cycle."""

import json
from types import SimpleNamespace

import httpx
from sqlalchemy import select

from app.core.config import Settings
from app.models.event import Event
from app.services.hubspot import _create_contact_and_note, hubspot_sync_reply

LEAD = SimpleNamespace(id=7, pack="robofox_web")
POST = SimpleNamespace(url="https://www.reddit.com/r/x/comments/1/")


async def test_create_contact_and_note_two_calls_with_association():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/crm/v3/objects/contacts":
            return httpx.Response(201, json={"id": "301"})
        if request.url.path == "/crm/v3/objects/notes":
            return httpx.Response(201, json={"id": "901"})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        contact_id = await _create_contact_and_note(client, "tok", "u/shopowner42", "note body")

    assert contact_id == "301"
    assert len(calls) == 2
    note_payload = json.loads(calls[1].content)
    assert note_payload["associations"][0]["to"]["id"] == "301"
    assert note_payload["associations"][0]["types"][0]["associationTypeId"] == 202
    assert calls[0].headers["Authorization"] == "Bearer tok"


async def test_sync_disabled_without_token(db_session, monkeypatch):
    monkeypatch.setattr("app.services.hubspot.get_settings", lambda: Settings(_env_file=None))
    ok = await hubspot_sync_reply(db_session, LEAD, POST, "u/x", "hello")
    assert ok is False
    events = (await db_session.execute(select(Event))).scalars().all()
    assert events == []  # fully inert when unconfigured


async def test_sync_failure_writes_event_not_exception(db_session, monkeypatch):
    monkeypatch.setattr(
        "app.services.hubspot.get_settings",
        lambda: Settings(_env_file=None, HUBSPOT_ACCESS_TOKEN="tok"),
    )

    async def boom(client, token, handle, note_body):
        raise httpx.ConnectError("hubspot is down")

    monkeypatch.setattr("app.services.hubspot._create_contact_and_note", boom)
    ok = await hubspot_sync_reply(db_session, LEAD, POST, "u/x", "hello")
    assert ok is False
    event = (
        (await db_session.execute(select(Event).where(Event.kind == "hubspot_failed")))
        .scalars()
        .one()
    )
    assert event.payload["lead_id"] == 7


async def test_sync_success_writes_synced_event(db_session, monkeypatch):
    monkeypatch.setattr(
        "app.services.hubspot.get_settings",
        lambda: Settings(_env_file=None, HUBSPOT_ACCESS_TOKEN="tok"),
    )

    async def fake_create(client, token, handle, note_body):
        assert "u/shopowner42 replied" in note_body
        return "301"

    monkeypatch.setattr("app.services.hubspot._create_contact_and_note", fake_create)
    ok = await hubspot_sync_reply(db_session, LEAD, POST, "u/shopowner42", "yes please")
    assert ok is True
    event = (
        (await db_session.execute(select(Event).where(Event.kind == "hubspot_synced")))
        .scalars()
        .one()
    )
    assert event.payload["contact_id"] == "301"
