"""Reddit user-context client (M4) — posts AS THE OWNER, only ever called by
the send cycle for an approved sends row.

Auth: script-app password grant (client id/secret + the owner's username and
password). Note: accounts with 2FA need the "password:2FAcode" form or an app
password — see README. Token cached ~50 min.
"""

from __future__ import annotations

import logging
import time

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)

_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_API = "https://oauth.reddit.com"


class RedditUserClient:
    def __init__(self, client_id: str, client_secret: str, username: str, password: str):
        self._client_id = client_id
        self._client_secret = client_secret
        self._username = username
        self._password = password
        self._token: str | None = None
        self._expires_at = 0.0

    async def _get_token(self, client: httpx.AsyncClient) -> str | None:
        if self._token and time.monotonic() < self._expires_at:
            return self._token
        try:
            resp = await client.post(
                _TOKEN_URL,
                data={
                    "grant_type": "password",
                    "username": self._username,
                    "password": self._password,
                },
                auth=(self._client_id, self._client_secret),
            )
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPError as exc:
            log.error("reddit user token failed: %s", exc)
            return None
        if "access_token" not in payload:
            log.error("reddit user token rejected: %s", str(payload)[:200])
            return None
        self._token = payload["access_token"]
        self._expires_at = time.monotonic() + float(payload.get("expires_in", 3600)) - 600
        return self._token

    async def _api_post(
        self, client: httpx.AsyncClient, path: str, data: dict
    ) -> tuple[bool, dict | None, str | None]:
        token = await self._get_token(client)
        if token is None:
            return False, None, "no user token (check REDDIT_USERNAME/PASSWORD)"
        try:
            resp = await client.post(
                f"{_API}{path}",
                data=data | {"api_type": "json"},
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPError as exc:
            return False, None, f"reddit api error: {exc}"
        errors = (payload.get("json") or {}).get("errors") or []
        if errors:
            return False, payload, "; ".join(":".join(map(str, e)) for e in errors)
        return True, payload, None

    async def post_comment(
        self, client: httpx.AsyncClient, thing_id: str, text: str
    ) -> tuple[bool, str | None, str | None]:
        """Reply to a post/comment. Returns (ok, comment fullname t1_xxx, error)."""
        ok, payload, error = await self._api_post(
            client, "/api/comment", {"thing_id": thing_id, "text": text}
        )
        if not ok:
            return False, None, error
        try:
            things = payload["json"]["data"]["things"]
            return True, things[0]["data"]["name"], None
        except (KeyError, IndexError, TypeError):
            return True, None, None  # posted but couldn't parse the id

    async def send_dm(
        self, client: httpx.AsyncClient, to: str, subject: str, text: str
    ) -> tuple[bool, str | None, str | None]:
        ok, _, error = await self._api_post(
            client, "/api/compose", {"to": to, "subject": subject[:100], "text": text}
        )
        return ok, None, error

    async def fetch_inbox(self, client: httpx.AsyncClient, limit: int = 25) -> list[dict]:
        """Recent inbox items (comment replies carry parent_id) — for the watcher."""
        token = await self._get_token(client)
        if token is None:
            return []
        try:
            resp = await client.get(
                f"{_API}/message/inbox?limit={limit}",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            children = resp.json().get("data", {}).get("children", [])
            return [c.get("data", {}) for c in children]
        except httpx.HTTPError as exc:
            log.warning("inbox fetch failed: %s", exc)
            return []

    async def fetch_things(self, client: httpx.AsyncClient, fullnames: list[str]) -> list[dict]:
        """Own posted comments by fullname — for removal/auto-halt detection."""
        if not fullnames:
            return []
        token = await self._get_token(client)
        if token is None:
            return []
        try:
            resp = await client.get(
                f"{_API}/api/info?id={','.join(fullnames)}",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            children = resp.json().get("data", {}).get("children", [])
            return [c.get("data", {}) for c in children]
        except httpx.HTTPError as exc:
            log.warning("api/info fetch failed: %s", exc)
            return []


_client: RedditUserClient | None = None


def get_reddit_user_client() -> RedditUserClient | None:
    """None when the owner hasn't provided user credentials."""
    global _client
    settings = get_settings()
    if not (
        settings.REDDIT_CLIENT_ID
        and settings.REDDIT_CLIENT_SECRET
        and settings.REDDIT_USERNAME
        and settings.REDDIT_PASSWORD
    ):
        return None
    if _client is None:
        _client = RedditUserClient(
            settings.REDDIT_CLIENT_ID,
            settings.REDDIT_CLIENT_SECRET,
            settings.REDDIT_USERNAME,
            settings.REDDIT_PASSWORD,
        )
    return _client
