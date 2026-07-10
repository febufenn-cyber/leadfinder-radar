"""Telegram approval bot (DESIGN §3.6) — thin adapter over app/approval.py.

Run: uv run python -m app.bot

Copy-mode only (§3.7): approving a variant sends the reply text back as a
copyable message plus the thread link; the owner posts manually from his own
account. The bot obeys exactly one chat — TELEGRAM_CHAT_ID — and ignores
everyone else.

Callback data grammar (≤64 bytes): "a:<action>:<arg>:<lead_id>"
  a:send:A:12    approve variant A of lead 12
  a:edit:_:12    pick which variant to edit (second keyboard: a:ed2:<variant>:12)
  a:ed2:B:12     ForceReply prompt for an edited variant-B text
  a:skip:_:12    skip lead 12
  a:mutekw:_:12  choose which matched keyword to mute (second keyboard: a:mk:<idx>:12)
  a:mutecomm:_:12  mute the lead's community for its pack
"""

from __future__ import annotations

import html
import logging
import re

from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.approval import ApprovalError, add_mute, approve, save_edit, skip
from app.core.config import get_settings
from app.db.session import get_session_factory
from app.models.lead import Lead
from app.models.raw_post import RawPost

log = logging.getLogger(__name__)
logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

# must NOT match copy-confirmation or skip messages — only the ed2 ForceReply prompt
_EDIT_PROMPT_RE = re.compile(r"edited text for lead #(\d+) variant ([ABC])")


def _authorized(update: Update) -> bool:
    chat = update.effective_chat
    return chat is not None and str(chat.id) == get_settings().TELEGRAM_CHAT_ID


async def _lead_post(session, lead_id: int) -> tuple[Lead | None, RawPost | None]:
    lead = await session.get(Lead, lead_id)
    post = await session.get(RawPost, lead.raw_post_id) if lead else None
    return lead, post


def _copy_message(payload) -> str:
    """The §3.7 clipboard flow: thread link + reply text in a copyable block."""
    return (
        f"✅ Approved <b>{payload.variant}</b> for lead #{payload.lead_id} — "
        f"long-press the block to copy, then post it yourself:\n"
        f"{html.escape(payload.url)}\n\n"
        f"<pre>{html.escape(payload.text)}</pre>"
    )


async def _say(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs) -> None:
    """query.message can be None for aged/inaccessible messages — always send
    via the chat id instead of replying to the card."""
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text, **kwargs)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        log.warning("ignoring callback from unauthorized chat %s", update.effective_chat)
        return
    query = update.callback_query
    await query.answer()
    parts = (query.data or "").split(":")
    if len(parts) != 4 or parts[0] not in {"a"}:
        return
    _, action, arg, lead_id_s = parts
    lead_id = int(lead_id_s)
    factory = get_session_factory()

    try:
        async with factory() as session:
            if action == "send":
                payload = await approve(session, lead_id, arg)
                await _say(update, context, _copy_message(payload), parse_mode="HTML")
            elif action == "edit":
                from sqlalchemy import select

                from app.models.draft import Draft

                variants = (
                    await session.execute(
                        select(Draft.variant)
                        .where(Draft.lead_id == lead_id)
                        .order_by(Draft.variant)
                    )
                ).scalars().all()
                if not variants:
                    await _say(update, context, f"lead #{lead_id} has no drafts to edit.")
                    return
                buttons = [
                    [
                        InlineKeyboardButton(f"Edit {v}", callback_data=f"a:ed2:{v}:{lead_id}")
                        for v in variants
                    ]
                ]
                await _say(
                    update, context, "Edit which variant?",
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
            elif action == "ed2":
                await _say(
                    update, context,
                    f"Reply to THIS message with your edited text for lead #{lead_id} variant {arg}.",
                    reply_markup=ForceReply(selective=True),
                )
            elif action == "skip":
                await skip(session, lead_id)
                await _say(update, context, f"⏭ Skipped lead #{lead_id}.")
            elif action == "mutekw":
                _, post = await _lead_post(session, lead_id)
                keywords = (post.matched_keywords or []) if post else []
                if not keywords:
                    await _say(update, context, "No matched keywords on this lead.")
                    return
                buttons = [
                    [InlineKeyboardButton(kw, callback_data=f"a:mk:{i}:{lead_id}")]
                    for i, kw in enumerate(keywords[:8])
                ]
                await _say(
                    update, context, "Mute which keyword?",
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
            elif action == "mk":
                lead, post = await _lead_post(session, lead_id)
                keywords = (post.matched_keywords or []) if post else []
                idx = int(arg)
                if lead and 0 <= idx < len(keywords):
                    added = await add_mute(session, "keyword", keywords[idx], lead.pack)
                    note = "muted" if added else "was already muted"
                    await _say(update, context, f"🔇 keyword “{keywords[idx]}” {note}.")
            elif action == "mutecomm":
                lead, post = await _lead_post(session, lead_id)
                if lead and post and post.community:
                    added = await add_mute(session, "community", post.community, lead.pack)
                    note = "muted" if added else "was already muted"
                    await _say(update, context, f"🔇 r/{post.community} {note} for {lead.pack}.")
    except ApprovalError as exc:
        await _say(update, context, f"⚠️ {exc}")
    except Exception:
        log.exception("callback handler failed (data=%s)", query.data)
        await _say(update, context, "⚠️ something broke — check the bot logs.")


async def on_edit_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The owner replied to a ForceReply edit prompt: store gold sample + approve."""
    if not _authorized(update):
        return
    msg = update.effective_message
    if not (msg and msg.reply_to_message and msg.reply_to_message.text):
        return
    m = _EDIT_PROMPT_RE.search(msg.reply_to_message.text)
    if not m:
        return
    lead_id, variant = int(m.group(1)), m.group(2)
    factory = get_session_factory()
    try:
        async with factory() as session:
            payload = await save_edit(session, lead_id, msg.text or "", variant=variant)
        await msg.reply_html(_copy_message(payload))
    except ApprovalError as exc:
        await msg.reply_text(f"⚠️ {exc}")
    except Exception:
        log.exception("edit handler failed (lead=%s)", lead_id)
        await msg.reply_text("⚠️ something broke — check the bot logs.")


def main() -> None:
    settings = get_settings()
    if not (settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID):
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — the approval bot needs both. "
            "See README 'Telegram setup'."
        )
    app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.REPLY & filters.TEXT, on_edit_reply))
    log.info("approval bot polling (chat %s only)", settings.TELEGRAM_CHAT_ID)
    app.run_polling(allowed_updates=["callback_query", "message"])


if __name__ == "__main__":
    main()
