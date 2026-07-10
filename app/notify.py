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


def format_alert(
    post: RawPostData,
    pack_name: str,
    matched: list[str],
    score=None,
    unscored: bool = False,
) -> str:
    """Telegram-HTML alert card (scored form since M1): pack, community, age,
    fit score, title, one-line summary, intent chips, preview, keywords, link.

    The link is load-bearing (M0 = "raw Telegram alert with link"), so the body
    preview gets whatever budget remains after the fixed parts — never the reverse.
    `score` is a LeadScore-shaped object; `unscored=True` marks classifier failure.
    """
    community = f"r/{post.community}" if post.community else post.source
    badge = ""
    if score is not None:
        badge = f" · ⭐ {score.fit_score}"
    elif unscored:
        badge = " · ⚠️ UNSCORED"
    header = (
        f"🔔 <b>[{html.escape(pack_name)}]</b> {html.escape(community)}"
        f" · {_age_str(post.created_at)}{badge}"
    )
    title = f"<b>{html.escape((post.title or '(no title)')[:300])}</b>"

    score_lines = []
    if score is not None:
        score_lines.append(f"<i>{html.escape(score.one_line_summary[:200])}</i>")
        chips = f"{score.intent} · {score.urgency} · budget: {score.budget_signal}"
        if score.disqualifiers:
            chips += f" · ⚠️ {', '.join(score.disqualifiers)}"
        score_lines.append(html.escape(chips))

    footer = f"matched: {html.escape(', '.join(matched))}\n{html.escape(post.url)}"

    fixed_len = len(header) + len(title) + sum(len(s) for s in score_lines) + len(footer)
    budget = _TELEGRAM_LIMIT - fixed_len - 6  # newlines
    preview = ""
    if post.text and budget > 20:
        preview = html.escape(post.text[:_BODY_PREVIEW_CHARS])[:budget]

    lines = [header, title, *score_lines] + ([preview] if preview else []) + [footer]
    return "\n".join(lines)


def format_approval_card(
    post, pack_name: str, matched: list[str], score, variants, lead_id: int
) -> tuple[str, list[list[dict]]]:
    """Approval card (DESIGN §3.6): scored header + full variants + inline buttons.

    Variant texts are what the owner will actually post — they are only trimmed
    at 900 chars each (with a marker); the full text always comes back on approve.
    """
    community = f"r/{post.community}" if post.community else post.source
    fit = f" · ⭐ {score.fit_score}" if score is not None else ""
    lines = [
        f"🎯 <b>[{html.escape(pack_name)}]</b> {html.escape(community)}"
        f" · {_age_str(post.created_at)}{fit}",
        f"<b>{html.escape((post.title or '(no title)')[:300])}</b>",
    ]
    if score is not None:
        lines.append(f"<i>{html.escape(score.one_line_summary[:200])}</i>")
        lines.append(html.escape(f"{score.intent} · {score.urgency} · budget: {score.budget_signal}"))
    lines.append(html.escape(post.url))
    for v in variants:
        flags = f" ⚠️ {', '.join(v.risk_flags)}" if v.risk_flags else ""
        text = v.text if len(v.text) <= 900 else v.text[:900] + "… (trimmed — full text on send)"
        lines.append(f"\n<b>— {v.variant} ({html.escape(v.channel)}){html.escape(flags)}</b>")
        lines.append(html.escape(text))
    card = "\n".join(lines)[:_TELEGRAM_LIMIT]

    send_row = [
        {"text": f"Send {v.variant}", "callback_data": f"a:send:{v.variant}:{lead_id}"}
        for v in variants
    ]
    buttons = [
        send_row,
        [
            {"text": "✏️ Edit", "callback_data": f"a:edit:_:{lead_id}"},
            {"text": "⏭ Skip", "callback_data": f"a:skip:_:{lead_id}"},
        ],
        [
            {"text": "🔇 Mute keyword", "callback_data": f"a:mutekw:_:{lead_id}"},
            {"text": "🔇 Mute community", "callback_data": f"a:mutecomm:_:{lead_id}"},
        ],
    ]
    return card, buttons


class ConsoleNotifier:
    """Fallback when Telegram isn't configured — alert lands in the worker log."""

    async def send(self, text: str) -> bool:
        log.info("ALERT (console fallback):\n%s", text)
        return True

    async def send_with_buttons(self, text: str, buttons: list[list[dict]]) -> bool:
        labels = [[b["text"] for b in row] for row in buttons]
        log.info("APPROVAL CARD (console fallback):\n%s\nbuttons=%s", text, labels)
        return True


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str) -> None:
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self._chat_id = chat_id

    async def send(self, text: str) -> bool:
        """Send one alert. Never raises — a dead Telegram must not kill the poll cycle."""
        return await self._post(
            {
                "chat_id": self._chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            }
        )

    async def send_with_buttons(self, text: str, buttons: list[list[dict]]) -> bool:
        """Approval card with an inline keyboard (DESIGN §3.6). Never raises."""
        return await self._post(
            {
                "chat_id": self._chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": {"inline_keyboard": buttons},
            }
        )

    async def _post(self, payload: dict) -> bool:
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
