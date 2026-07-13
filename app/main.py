"""Owner dashboard: operations, leads, sends, and M5 evaluation evidence."""

from __future__ import annotations

import html
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from sqlalchemy import desc, func, select, text

from app.db.session import get_session_factory
from app.evals import build_eval_snapshot
from app.models.draft import Draft
from app.models.event import Event
from app.models.halt import Halt
from app.models.lead import Lead
from app.models.raw_post import RawPost
from app.models.send import Send
from app.review import load_review_packs

_FUNNEL_ORDER = [
    "surfaced", "drafted", "sent", "replied", "conversation",
    "won", "lost", "no_response", "skipped",
]

app = FastAPI(title="LeadFinder", docs_url=None, redoc_url=None)

_STYLE = """
body { font: 14px/1.5 -apple-system, BlinkMacSystemFont, sans-serif; margin: 2rem; color: #1a1a1a; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0 2rem; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid #e2e2e2; vertical-align: top; }
th { background: #f6f6f6; }
.meta { color: #666; margin-bottom: 1rem; }
.halt { background: #fde8e8; border: 1px solid #f5b5b5; padding: 8px 12px; }
.cards { display: flex; flex-wrap: wrap; gap: 12px; margin: 1rem 0; }
.card { min-width: 160px; padding: 12px 16px; border: 1px solid #ddd; border-radius: 8px; }
.card b { display: block; font-size: 1.35rem; }
.good { color: #176b36; }
.warn { color: #9b5b00; }
"""


def _nav(current: str) -> str:
    links = [("/", "matched posts"), ("/leads", "leads"), ("/sends", "sends"), ("/evals", "evals")]
    return " · ".join(
        html.escape(label) if path == current else f'<a href="{path}">{html.escape(label)}</a>'
        for path, label in links
    )


def _page(title: str, body: str, *, current: str, refresh: bool = False) -> str:
    refresh_tag = '<meta http-equiv="refresh" content="60">' if refresh else ""
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{html.escape(title)}</title>{refresh_tag}
<style>{_STYLE}</style></head>
<body><p>{_nav(current)}</p>{body}</body></html>"""


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _number(value, suffix: str = "") -> str:
    return "—" if value is None else f"{value}{suffix}"


@app.get("/health")
async def health() -> dict:
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text("SELECT 1"))
        last_poll = await session.scalar(
            select(Event.ts).where(Event.kind == "poll_cycle").order_by(desc(Event.ts)).limit(1)
        )
    return {
        "status": "ok",
        "db": True,
        "last_poll": last_poll.isoformat() if last_poll else None,
    }


def _row_html(post: RawPost) -> str:
    age_min = int((datetime.now(UTC) - post.fetched_at).total_seconds() // 60)
    community = f"r/{post.community}" if post.community else post.source
    keywords = ", ".join(post.matched_keywords or [])
    alerted = "✅" if post.alerted_at else "—"
    fit = str(post.fit_score) if post.fit_score is not None else ("?" if post.classified_at else "—")
    summary = (post.score or {}).get("one_line_summary", "")
    return (
        f"<tr><td>{age_min}m</td><td><b>{html.escape(fit)}</b></td>"
        f"<td>{html.escape(post.pack)}</td><td>{html.escape(community)}</td>"
        f'<td><a href="{html.escape(post.url)}" target="_blank" rel="noopener">'
        f"{html.escape(post.title or '(no title)')}</a>"
        f"{'<br><i>' + html.escape(summary) + '</i>' if summary else ''}</td>"
        f"<td>{html.escape(keywords)}</td><td>{alerted}</td></tr>"
    )


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    factory = get_session_factory()
    async with factory() as session:
        posts = (
            await session.execute(select(RawPost).order_by(desc(RawPost.fetched_at)).limit(100))
        ).scalars().all()
        last_poll = await session.scalar(
            select(Event.ts).where(Event.kind == "poll_cycle").order_by(desc(Event.ts)).limit(1)
        )
        counts = dict(
            (await session.execute(select(Lead.status, func.count()).group_by(Lead.status))).all()
        )
    funnel = " · ".join(f"{status}: <b>{counts.get(status, 0)}</b>" for status in _FUNNEL_ORDER)
    rows = "\n".join(_row_html(post) for post in posts) or (
        '<tr><td colspan="7">No matched posts yet — the poller runs every 2 minutes.</td></tr>'
    )
    last = last_poll.strftime("%Y-%m-%d %H:%M UTC") if last_poll else "never"
    body = f"""
<h2>LeadFinder — matched posts</h2>
<p class="meta">last poll: {last} · newest 100 · auto-refreshes every 60s</p>
<p class="meta">funnel — {funnel}</p>
<table><tr><th>fetched</th><th>fit</th><th>pack</th><th>community</th><th>title</th><th>matched</th><th>alerted</th></tr>{rows}</table>
"""
    return _page("LeadFinder", body, current="/", refresh=True)


@app.get("/leads", response_class=HTMLResponse)
async def leads_view() -> str:
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                select(Lead, RawPost)
                .join(RawPost, RawPost.id == Lead.raw_post_id)
                .order_by(desc(Lead.created_at))
                .limit(100)
            )
        ).all()
        chosen_ids = [lead.chosen_draft_id for lead, _ in rows if lead.chosen_draft_id]
        chosen = {}
        if chosen_ids:
            chosen = {
                draft.id: draft.variant
                for draft in (
                    await session.execute(select(Draft).where(Draft.id.in_(chosen_ids)))
                ).scalars()
            }
    table_rows = "\n".join(
        f"<tr><td>{lead.id}</td><td><b>{html.escape(lead.status)}</b></td>"
        f"<td>{html.escape(lead.pack)}</td>"
        f"<td>{post.fit_score if post.fit_score is not None else '—'}</td>"
        f'<td><a href="{html.escape(post.url)}" target="_blank" rel="noopener">'
        f"{html.escape(post.title or '(no title)')}</a></td>"
        f"<td>{'✅' if lead.approval_pushed_at else ('⏳' if lead.status == 'drafted' else '—')}</td>"
        f"<td>{chosen.get(lead.chosen_draft_id, '—')}</td>"
        f"<td>{lead.created_at:%m-%d %H:%M}</td></tr>"
        for lead, post in rows
    ) or '<tr><td colspan="8">No leads yet.</td></tr>'
    body = f"""
<h2>Leads</h2>
<table><tr><th>#</th><th>status</th><th>pack</th><th>fit</th><th>post</th><th>card pushed</th><th>sent variant</th><th>created</th></tr>{table_rows}</table>
"""
    return _page("LeadFinder — leads", body, current="/leads")


@app.get("/sends", response_class=HTMLResponse)
async def sends_view() -> str:
    factory = get_session_factory()
    async with factory() as session:
        sends = (
            await session.execute(select(Send).order_by(desc(Send.created_at)).limit(50))
        ).scalars().all()
        halts = (
            await session.execute(
                select(Halt).where(Halt.cleared_at.is_(None)).order_by(desc(Halt.created_at))
            )
        ).scalars().all()
    halt_banner = "".join(
        f'<p class="halt">🛑 HALT [{html.escape(halt.platform)}] {html.escape(halt.reason)} '
        f"(since {halt.created_at:%m-%d %H:%M} — clear with scripts/clear_halt.py)</p>"
        for halt in halts
    )
    icons = {"queued": "⏱", "executing": "🚀", "sent": "✅", "failed": "⚠️", "halted": "🛑", "cancelled": "✖️"}
    table_rows = "\n".join(
        f"<tr><td>{send.id}</td><td>{icons.get(send.status, '')} {html.escape(send.status)}</td>"
        f"<td>{send.lead_id}</td><td>{html.escape(send.platform)}/{html.escape(send.channel)}</td>"
        f"<td>{html.escape(send.community or '—')}</td><td>{send.scheduled_at:%m-%d %H:%M}</td>"
        f"<td>{f'{send.sent_at:%m-%d %H:%M}' if send.sent_at else '—'}</td>"
        f"<td>{html.escape(send.error or send.external_result_id or '—')}</td></tr>"
        for send in sends
    ) or '<tr><td colspan="8">No sends yet — sends appear when SEND_MODE=api.</td></tr>'
    body = f"""
<h2>Sends</h2>{halt_banner}
<table><tr><th>#</th><th>status</th><th>lead</th><th>via</th><th>community</th><th>scheduled</th><th>sent</th><th>result/error</th></tr>{table_rows}</table>
"""
    return _page("LeadFinder — sends", body, current="/sends", refresh=True)


@app.get("/evals", response_class=HTMLResponse)
async def evals_view() -> str:
    factory = get_session_factory()
    async with factory() as session:
        snapshot = await build_eval_snapshot(session, load_review_packs())

    review_rows = "\n".join(
        f"<tr><td>{html.escape(pack)}</td><td>{row['reviewed']}</td><td>{row['skipped']}</td>"
        f"<td>{row['tp']}</td><td>{row['fp']}</td><td>{row['fn']}</td><td>{row['tn']}</td>"
        f"<td>{_pct(row['precision'])}</td><td>{_pct(row['recall'])}</td></tr>"
        for pack, row in snapshot["reviews"].items()
    ) or '<tr><td colspan="9">No review labels yet. Send /review10 to the bot.</td></tr>'

    outcome_rows = "\n".join(
        f"<tr><td>{html.escape(pack)}</td><td>{row['leads']}</td><td>{row['worked']}</td>"
        f"<td>{row['replied']}</td><td>{row['conversations']}</td><td>{row['won']}</td>"
        f"<td>{_pct(row['reply_rate'])}</td><td>{_pct(row['conversation_rate'])}</td><td>{_pct(row['win_rate'])}</td></tr>"
        for pack, row in snapshot["outcomes"].items()
    ) or '<tr><td colspan="9">No leads yet.</td></tr>'

    variant_rows = "\n".join(
        f"<tr><td>{html.escape(row['pack'])}</td><td>{html.escape(row['variant'])}</td>"
        f"<td>{html.escape(row['channel'])}</td><td>{row['worked']}</td><td>{row['replied']}</td>"
        f"<td>{row['conversations']}</td><td>{row['won']}</td><td>{_pct(row['reply_rate'])}</td></tr>"
        for row in snapshot["variants"]
    ) or '<tr><td colspan="8">No chosen variants yet.</td></tr>'

    ops = snapshot["ops"]
    edits = snapshot["edits"]
    body = f"""
<h2>M5 evaluation dashboard</h2>
<p class="meta">All-time, database-backed evidence. Review labels and edit analysis never auto-change prompts or thresholds.</p>
<div class="cards">
  <div class="card">Post → alert p50<b>{_number(ops['post_to_alert_p50_minutes'], ' min')}</b></div>
  <div class="card">LLM spend<b>${ops['llm_cost_usd']:.6f}</b></div>
  <div class="card">Cost / surfaced lead<b>{'$' + format(ops['cost_per_surfaced_lead_usd'], '.6f') if ops['cost_per_surfaced_lead_usd'] is not None else '—'}</b></div>
  <div class="card">Gold edits<b>{edits['gold_samples']}</b></div>
  <div class="card">Average draft change<b>{_pct(edits['average_change'])}</b></div>
</div>
<h3>Classifier review</h3>
<table><tr><th>pack</th><th>reviewed</th><th>skipped</th><th>TP</th><th>FP</th><th>FN</th><th>TN</th><th>precision</th><th>recall</th></tr>{review_rows}</table>
<h3>Lead outcomes</h3>
<table><tr><th>pack</th><th>leads</th><th>worked</th><th>replied</th><th>conversation</th><th>won</th><th>reply rate</th><th>conversation rate</th><th>win rate</th></tr>{outcome_rows}</table>
<h3>Chosen variant performance</h3>
<table><tr><th>pack</th><th>variant</th><th>channel</th><th>worked</th><th>replied</th><th>conversation</th><th>won</th><th>reply rate</th></tr>{variant_rows}</table>
"""
    return _page("LeadFinder — M5 evals", body, current="/evals", refresh=True)
