# LeadFinder

Demand-post radar → scored leads → drafted replies → **one-tap owner-approved send**.
Watches public streams (Reddit RSS in M0; Threads API + HN later) for buying-intent posts
matching configured *offer packs*, dedupes them, and alerts the owner's phone with a link.

Full design: [DESIGN.md](DESIGN.md). Current milestone: **M0** — "a personal F5Bot":
Reddit RSS poller for one pack → raw Telegram alert with link.

## The one rule that matters

**Discovery, dedup, scoring, enrichment, drafting are autonomous. Sending is owner-approved,
always.** There is no unattended send path in this codebase and none may be added — see
DESIGN.md §0. M0 sends nothing to any platform; alerts go to the owner only.

## Compliance posture (DESIGN.md §5)

- Official APIs / public RSS only. No login-walled scraping, no headless-browser session
  harvesting, no fake accounts. One real account per platform — the owner's.
- Reddit RSS is polled gently: descriptive `REDDIT_USER_AGENT`, sequential fetches with
  spacing, 2-minute cycle, posts older than `max_age_minutes` skipped.
- Respect community self-promotion rules (packs carry per-community notes from M2).
- Volume stays boutique by design: the goal is 3–8 excellent engagements/day, not hundreds.
- Disclose honestly if asked whether AI helped draft.

## Quick start

```bash
# infra: postgres :5442, test-postgres :5433 (tmpfs), redis :6380
# (5432/6379 are taken by probexa containers on the dev Mac)
docker compose up -d

uv sync
cp .env.example .env          # fill TELEGRAM_* when ready; console fallback otherwise
uv run alembic upgrade head

uv run python scripts/poll_once.py           # one manual poll cycle
uv run arq app.worker.WorkerSettings         # continuous: polls every 2 min
uv run uvicorn app.main:app --port 8100      # dashboard: http://localhost:8100
uv run python -m app.bot                     # approval bot (needs TELEGRAM_* set)
```

## The approval flow (M2, copy-mode)

A surfaced lead (fit ≥ pack threshold) is drafted by Sonnet into 2–3 variants and
lands on your phone as a card with buttons: `Send A/B/C · Edit · Skip · Mute keyword ·
Mute community`. **Send returns the text + thread link for you to copy and post
manually from your own account** — nothing is ever posted automatically (API-send
with guardrails is M4). Edits you make become gold samples for prompt tuning.

Before working real leads, fill in the owner-truth files (drafts stay claim-free
until you do): `packs/personas/robofox_web.yaml` (true facts only) and
`packs/fewshots/robofox_web.yaml` (replace starter examples with ~10 real
positives + ~10 near-misses).

## Telegram setup (2 minutes)

1. Message **@BotFather** → `/newbot` → copy token into `TELEGRAM_BOT_TOKEN`.
2. Send your new bot any message, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy
   `result[].message.chat.id` into `TELEGRAM_CHAT_ID`.

## Sources (M3)

Reddit (OAuth or RSS fallback, 2-min cadence) + Hacker News (Algolia, no auth,
2-min cadence) + Threads (**official API only** — set `THREADS_ACCESS_TOKEN`
from your Meta app; polling is budgeted: max `THREADS_DAILY_QUERY_BUDGET`
keyword-searches/day, ≥`THREADS_MIN_INTERVAL_MINUTES` between polls, ledger in
the events table so restarts can't double-spend the quota).

## Offer packs

`packs/*.yaml` — each pack owns subreddits, search queries, include/exclude keywords,
`max_age_minutes`, and `enabled`. M0 ships `robofox_web` enabled; `robofox_ai` and
`zervvo_abroad` are included as disabled templates.

## Tests

```bash
docker compose up -d postgres-test
uv run pytest -v
```

## Ops

- Nightly backup (VPS cron): `scripts/backup.sh` → `pg_dump` to `~/backups/leadfinder/`.
- Dashboard is server-rendered tables only; the phone is the real UI.
