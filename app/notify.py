"""Owner alerts (DESIGN §3.6, M0 form): Telegram Bot API with console fallback.

M0 sends plain alert cards. Inline approve/edit/skip buttons and the
python-telegram-bot approval bot arrive in M2. Nothing here posts to any
platform — alerts go to the owner only.
"""

from __future__ import annotations

import html
import logging
from datetime import UTC, datetime

import httpx

from app.adapters.reddit_rss import RawPostData
from app.core.config import Settings

log = logging.getLogger(__name__)

_TELEGRAM_LIMIT = 4000  # hard API cap is 4096; keep headroom
_BODY_PREVIEW_CHARS = 400


def _age_str(created_at: datetime) -> str:
    minutes = max(0, int((datetime.now(UTC) - created_at).total_seconds() // 60))
    return f"{minutes}m ago" if minutes < 120 else f"{minutes // 60}h ago"


def format_alert(post: RawPostData, pack_name: str, matched: list[str]) -> str:
    """Telegram-HTML alert card: pack, community, age, title, preview, keywords, link."""
    community = f"r/{post.community}" if post.community else post.source
    preview = post.text[:_BODY_PREVIEW_CHARS]
    lines = [
        f"🔔 <b>[{html.escape(pack_name)}]</b> {html.escape(community)} · {_age_str(post.created_at)}",
        f"<b>{html.escape(post.title or '(no title)')}</b>",
    ]
    if preview:
        lines.append(html.escape(preview))
    lines.append(f"matched: {html.escape(', '.join(matched))}")
    lines.append(post.url)
    return "\n".join(lines)[:_TELEGRAM_LIMIT]


class ConsoleNotifier:
    """Fallback when Telegram isn't configured — alert lands in the worker log."""

    async def send(self, text: str) -> bool:
        log.info("ALERT (console fallback):\n%s", text)
        return True


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str) -> None:
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self._chat_id = chat_id

    async def send(self, text: str) -> bool:
        """Send one alert. Never raises — a dead Telegram must not kill the poll cycle."""
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(self._url, json=payload)
            if resp.status_code != 200:
                log.error("telegram send failed status=%s body=%s", resp.status_code, resp.text)
                return False
            return True
        except httpx.HTTPError as exc:
            log.error("telegram send error: %s", exc)
            return False


def get_notifier(settings: Settings) -> ConsoleNotifier | TelegramNotifier:
    if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID:
        return TelegramNotifier(settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID)
    log.warning("TELEGRAM_BOT_TOKEN/CHAT_ID not set — alerts fall back to console")
    return ConsoleNotifier()
