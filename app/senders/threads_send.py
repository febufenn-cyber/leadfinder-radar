"""Threads Reply Management (M4) — replies AS THE OWNER via the official API,
only ever called by the send cycle for an approved sends row.

Two-step: create the reply container, then publish it. Requires the token to
carry threads_manage_replies permission (set up in the Meta app).
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

_API = "https://graph.threads.net/v1.0"


async def post_reply(
    client: httpx.AsyncClient, token: str, reply_to_id: str, text: str
) -> tuple[bool, str | None, str | None]:
    """Returns (ok, published media id, error)."""
    try:
        create = await client.post(
            f"{_API}/me/threads",
            params={
                "media_type": "TEXT",
                "text": text,
                "reply_to_id": reply_to_id,
                "access_token": token,
            },
        )
        create.raise_for_status()
        creation_id = create.json().get("id")
        if not creation_id:
            return False, None, f"no creation id: {create.text[:200]}"
        publish = await client.post(
            f"{_API}/me/threads_publish",
            params={"creation_id": creation_id, "access_token": token},
        )
        publish.raise_for_status()
        media_id = publish.json().get("id")
        return True, media_id, None
    except httpx.HTTPError as exc:
        body = getattr(getattr(exc, "response", None), "text", "")[:200]
        return False, None, f"threads reply failed: {exc} {body}"


async def fetch_replies(client: httpx.AsyncClient, token: str, media_id: str) -> list[dict]:
    """Replies to one of our published replies — for the watcher."""
    try:
        resp = await client.get(
            f"{_API}/{media_id}/replies",
            params={"fields": "id,username,text,timestamp", "access_token": token},
        )
        resp.raise_for_status()
        return resp.json().get("data", [])
    except httpx.HTTPError as exc:
        log.warning("threads replies fetch failed media=%s: %s", media_id, exc)
        return []
