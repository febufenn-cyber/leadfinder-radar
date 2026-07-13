"""Telegram approval + M5 review bot.

Run: uv run python -m app.bot

The bot obeys exactly one chat — TELEGRAM_CHAT_ID — and ignores everyone else.

Approval callback grammar (≤64 bytes): "a:<action>:<arg>:<lead_id>"
Review callback grammar: "r:<label>:<raw_post_id>"
  r:demand:42      classifier missed a real demand lead
  r:not_demand:42  classifier correctly suppressed it
  r:skip:42        leave an explicit skipped review label
"""

from __future__ import annotations

import html
import logging
import re

from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.approval import (
    ApprovalError,
    add_mute,
    approve,
    cancel_send,
    queue_send,
    save_edit,
    skip,
)
from app.core.config import get_settings
from app.db.session import get_session_factory
from app.models.lead import Lead
from app.models.raw_post import RawPost
from app.review import (
    ReviewError,
    format_review_card,
    load_review_packs,
    record_review,
    review_candidates,
)

log = logging.getLogger(__name__)
logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

_EDIT_PROMPT_RE = re.compile(r"edited text for lead #(\d+) variant ([ABC])")


def _authorized(update: Update) -> bool:
    chat = update.effective_chat
    return chat is not None and str(chat.id) == get_settings().TELEGRAM_CHAT_ID


async def _lead_post(session, lead_id: int) -> tuple[Lead | None, RawPost | None]:
    lead = await session.get(Lead, lead_id)
    post = await session.get(RawPost, lead.raw_post_id) if lead else None
    return lead, post


def _copy_message(payload) -> str:
    return (
        f"✅ Approved <b>{payload.variant}</b> for lead #{payload.lead_id} — "
        f"long-press the block to copy, then post it yourself:\n"
        f"{html.escape(payload.url)}\n\n"
        f"<pre>{html.escape(payload.text)}</pre>"
    )


async def _say(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs) -> None:
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text, **kwargs)


async def on_review_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send up to ten unlabeled sub-threshold posts for owner review."""
    if not _authorized(update):
        log.warning("ignoring /review10 from unauthorized chat %s", update.effective_chat)
        return
    packs = load_review_packs()
    pack_name = context.args[0] if context.args else None
    thresholds = {pack.name: pack.threshold for pack in packs}
    if pack_name and pack_name not in thresholds:
        await _say(
            update,
            context,
            f"Unknown pack {pack_name!r}. Available: {', '.join(sorted(thresholds))}",
        )
        return

    factory = get_session_factory()
    async with factory() as session:
        posts = await review_candidates(session, packs, limit=10, pack_name=pack_name)

    if not posts:
        await _say(update, context, "✅ No unlabeled sub-threshold posts are waiting for review.")
        return

    for post in posts:
        card, raw_buttons = format_review_card(post, thresholds[post.pack])
        buttons = [[InlineKeyboardButton(**button) for button in row] for row in raw_buttons]
        await _say(
            update,
            context,
            card,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(buttons),
        )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        log.warning("ignoring callback from unauthorized chat %s", update.effective_chat)
        return
    query = update.callback_query
    await query.answer()
    parts = (query.data or "").split(":")

    if len(parts) == 3 and parts[0] == "r":
        _, label, raw_post_id_s = parts
        raw_post_id = int(raw_post_id_s)
        packs = load_review_packs()
        thresholds = {pack.name: pack.threshold for pack in packs}
        factory = get_session_factory()
        try:
            async with factory() as session:
                post = await session.get(RawPost, raw_post_id)
                if post is None or post.pack not in thresholds:
                    raise ReviewError(f"raw post #{raw_post_id} has no active pack")
                review = await record_review(
                    session,
                    raw_post_id,
                    label,
                    threshold=thresholds[post.pack],
                )
            label_text = {
                "demand": "✅ demand lead",
                "not_demand": "❌ not a lead",
                "skip": "⏭ skipped",
            }[review.label]
            await _say(update, context, f"Recorded post #{raw_post_id}: {label_text}.")
        except ReviewError as exc:
            await _say(update, context, f"⚠️ {exc}")
        except Exception:
            log.exception("review callback failed (data=%s)", query.data)
            await _say(update, context, "⚠️ review failed — check the bot logs.")
        return

    if len(parts) != 4 or parts[0] != "a":
        return
    _, action, arg, lead_id_s = parts
    lead_id = int(lead_id_s)
    factory = get_session_factory()

    try:
        async with factory() as session:
            if action == "send":
                if get_settings().SEND_MODE == "api":
                    send = await queue_send(session, lead_id, arg)
                    from zoneinfo import ZoneInfo

                    eta = send.scheduled_at.astimezone(
                        ZoneInfo(get_settings().OWNER_TZ)
                    ).strftime("%H:%M")
                    await _say(
                        update, context,
                        f"⏱ Queued variant {arg} for lead #{lead_id} — posts ~{eta} "
                        f"(guardrails re-check at send time).",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton(
                                "✖️ Cancel", callback_data=f"a:cxl:{send.id}:{lead_id}"
                            )
                        ]]),
                    )
                else:
                    payload = await approve(session, lead_id, arg)
                    await _say(update, context, _copy_message(payload), parse_mode="HTML")
            elif action == "cxl":
                if await cancel_send(session, int(arg)):
                    await _say(
                        update, context,
                        f"✖️ Cancelled send for lead #{lead_id} — it stays approvable.",
                    )
                else:
                    await _say(update, context, "Too late — that send already executed.")
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
                buttons = [[
                    InlineKeyboardButton(f"Edit {v}", callback_data=f"a:ed2:{v}:{lead_id}")
                    for v in variants
                ]]
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
    app.add_handler(CommandHandler("review10", on_review_command))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.REPLY & filters.TEXT, on_edit_reply))
    log.info("approval bot polling (chat %s only)", settings.TELEGRAM_CHAT_ID)
    app.run_polling(allowed_updates=["callback_query", "message"])


if __name__ == "__main__":
    main()
