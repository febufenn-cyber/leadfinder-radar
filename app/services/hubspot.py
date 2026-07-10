"""HubSpot sync (M4, DESIGN §3.8): when a lead reaches `replied`, push a
contact + note so the conversation exists in the CRM. Best-effort — a dead
HubSpot must never break reply detection. Disabled when no token is set.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from app.core.config import get_settings
from app.models.event import Event

log = logging.getLogger(__name__)

_API = "https://api.hubapi.com"
_NOTE_TO_CONTACT = 202  # HubSpot-defined association typeId


async def _create_contact_and_note(
    client: httpx.AsyncClient, token: str, handle: str, note_body: str
) -> str:
    """Returns the contact id. Raises httpx.HTTPError on failure."""
    headers = {"Authorization": f"Bearer {token}"}
    contact = await client.post(
        f"{_API}/crm/v3/objects/contacts",
        headers=headers,
        json={"properties": {"firstname": handle, "lifecyclestage": "lead"}},
    )
    contact.raise_for_status()
    contact_id = contact.json()["id"]
    note = await client.post(
        f"{_API}/crm/v3/objects/notes",
        headers=headers,
        json={
            "properties": {
                "hs_note_body": note_body[:5000],
                "hs_timestamp": datetime.now(UTC).isoformat(),
            },
            "associations": [
                {
                    "to": {"id": contact_id},
                    "types": [
                        {
                            "associationCategory": "HUBSPOT_DEFINED",
                            "associationTypeId": _NOTE_TO_CONTACT,
                        }
                    ],
                }
            ],
        },
    )
    note.raise_for_status()
    return contact_id


async def hubspot_sync_reply(session, lead, post, author: str, reply_preview: str) -> bool:
    """Called by the watcher on reply detection. Never raises; logs an Event
    either way so the dashboard can show sync health."""
    settings = get_settings()
    if not settings.HUBSPOT_ACCESS_TOKEN:
        return False
    note_body = (
        f"LeadFinder [{lead.pack}] — {author} replied on {post.url if post else '?'}\n\n"
        f"Their reply:\n{reply_preview[:1000]}"
    )
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            contact_id = await _create_contact_and_note(
                client, settings.HUBSPOT_ACCESS_TOKEN, author, note_body
            )
        session.add(
            Event(
                kind="hubspot_synced",
                payload={"lead_id": lead.id, "contact_id": contact_id, "author": author},
            )
        )
        return True
    except httpx.HTTPError as exc:
        body = getattr(getattr(exc, "response", None), "text", "")[:200]
        log.error("hubspot sync failed lead=%s: %s %s", lead.id, exc, body)
        session.add(
            Event(
                kind="hubspot_failed",
                payload={"lead_id": lead.id, "error": f"{exc} {body}"},
            )
        )
        return False
