"""Admin dashboard (DESIGN §4): server-rendered tables, nothing fancy.

Run: uv run uvicorn app.main:app --port 8100
"""

from __future__ import annotations

import html
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from sqlalchemy import desc, select, text

from app.db.session import get_session_factory
from app.models.event import Event
from app.models.raw_post import RawPost

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
<p class="meta">last poll: {last} · newest 100 · auto-refreshes every 60s</p>
<table>
<tr><th>fetched</th><th>fit</th><th>pack</th><th>community</th><th>title</th><th>matched</th><th>alerted</th></tr>
{rows}
</table>
</body></html>"""
