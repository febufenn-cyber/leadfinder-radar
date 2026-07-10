"""Admin dashboard (DESIGN §4): server-rendered tables, nothing fancy.

Run: uv run uvicorn app.main:app --port 8100
"""

from __future__ import annotations

import html
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from sqlalchemy import desc, func, select, text

from app.db.session import get_session_factory
from app.models.draft import Draft
from app.models.event import Event
from app.models.lead import Lead
from app.models.raw_post import RawPost

_FUNNEL_ORDER = [
    "surfaced", "drafted", "sent", "replied", "conversation",
    "won", "lost", "no_response", "skipped",
]

app = FastAPI(title="LeadFinder", docs_url=None, redoc_url=None)


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


def _row_html(p: RawPost) -> str:
    age_min = int((datetime.now(UTC) - p.fetched_at).total_seconds() // 60)
    community = f"r/{p.community}" if p.community else p.source
    keywords = ", ".join(p.matched_keywords or [])
    alerted = "✅" if p.alerted_at else "—"
    fit = str(p.fit_score) if p.fit_score is not None else ("?" if p.classified_at else "—")
    summary = (p.score or {}).get("one_line_summary", "")
    return (
        f"<tr><td>{age_min}m</td><td><b>{html.escape(fit)}</b></td>"
        f"<td>{html.escape(p.pack)}</td>"
        f"<td>{html.escape(community)}</td>"
        f'<td><a href="{html.escape(p.url)}" target="_blank" rel="noopener">'
        f"{html.escape(p.title or '(no title)')}</a>"
        f"{'<br><i>' + html.escape(summary) + '</i>' if summary else ''}</td>"
        f"<td>{html.escape(keywords)}</td><td>{alerted}</td></tr>"
    )


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    factory = get_session_factory()
    async with factory() as session:
        posts = (
            (
                await session.execute(
                    select(RawPost).order_by(desc(RawPost.fetched_at)).limit(100)
                )
            )
            .scalars()
            .all()
        )
        last_poll = await session.scalar(
            select(Event.ts).where(Event.kind == "poll_cycle").order_by(desc(Event.ts)).limit(1)
        )

    async with factory() as session:
        counts = dict(
            (
                await session.execute(
                    select(Lead.status, func.count()).group_by(Lead.status)
                )
            ).all()
        )
    funnel = " · ".join(f"{s}: <b>{counts.get(s, 0)}</b>" for s in _FUNNEL_ORDER)

    rows = "\n".join(_row_html(p) for p in posts) or (
        '<tr><td colspan="7">No matched posts yet — the poller runs every 2 minutes.</td></tr>'
    )
    last = last_poll.strftime("%Y-%m-%d %H:%M UTC") if last_poll else "never"
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>LeadFinder</title>
<meta http-equiv="refresh" content="60">
<style>
  body {{ font: 14px/1.5 -apple-system, sans-serif; margin: 2rem; color: #1a1a1a; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ text-align: left; padding: 6px 10px; border-bottom: 1px solid #e2e2e2; }}
  th {{ background: #f6f6f6; }}
  .meta {{ color: #666; margin-bottom: 1rem; }}
</style></head>
<body>
<h2>LeadFinder — matched posts</h2>
<p class="meta">last poll: {last} · newest 100 · auto-refreshes every 60s · <a href="/leads">leads</a></p>
<p class="meta">funnel — {funnel}</p>
<table>
<tr><th>fetched</th><th>fit</th><th>pack</th><th>community</th><th>title</th><th>matched</th><th>alerted</th></tr>
{rows}
</table>
</body></html>"""


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
        chosen = {
            d.id: d.variant
            for d in (
                await session.execute(
                    select(Draft).where(
                        Draft.id.in_([ld.chosen_draft_id for ld, _ in rows if ld.chosen_draft_id])
                    )
                )
            ).scalars()
        }

    body = "\n".join(
        f"<tr><td>{lead.id}</td><td><b>{html.escape(lead.status)}</b></td>"
        f"<td>{html.escape(lead.pack)}</td>"
        f"<td>{post.fit_score if post.fit_score is not None else '—'}</td>"
        f'<td><a href="{html.escape(post.url)}" target="_blank" rel="noopener">'
        f"{html.escape(post.title or '(no title)')}</a></td>"
        f"<td>{chosen.get(lead.chosen_draft_id, '—')}</td>"
        f"<td>{lead.created_at:%m-%d %H:%M}</td></tr>"
        for lead, post in rows
    ) or '<tr><td colspan="7">No leads yet.</td></tr>'
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>LeadFinder — leads</title>
<style>
  body {{ font: 14px/1.5 -apple-system, sans-serif; margin: 2rem; color: #1a1a1a; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ text-align: left; padding: 6px 10px; border-bottom: 1px solid #e2e2e2; }}
  th {{ background: #f6f6f6; }}
</style></head>
<body>
<h2>Leads</h2>
<p><a href="/">← matched posts</a></p>
<table>
<tr><th>#</th><th>status</th><th>pack</th><th>fit</th><th>post</th><th>sent variant</th><th>created</th></tr>
{body}
</table>
</body></html>"""
