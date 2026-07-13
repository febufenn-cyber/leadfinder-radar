# LeadFinder

Demand-post radar → scored leads → drafted replies → **one-tap owner-approved send**.

LeadFinder watches compliant public sources for buying intent, applies cheap filters and a
classifier, drafts context-specific replies, and sends each candidate to the owner's Telegram.
Discovery, scoring, drafting, evaluation, and prompt analysis are autonomous. **Sending remains
per-reply owner-approved, always.**

Full design: [DESIGN.md](DESIGN.md). Current milestone: **M5 — evaluation and learning loop**.

## The one rule that matters

There is no unattended send path, no batch approval, no account rotation, and no automatic
follow-up campaign. API-send is optional and still starts from one owner tap on one draft.
Guardrails are re-checked at execution time.

## Compliance posture

- Official APIs and public RSS only; no login-walled scraping or browser-session harvesting.
- One real account per platform: the owner's.
- Community rules are injected into drafting; unknown communities default to no promotion.
- Volume stays boutique: a few high-quality engagements per day, not mass outreach.
- Threads discovery uses the official API when approved; the Google CSE bridge is a temporary,
  copy-mode-only discovery source while public keyword search is under Meta App Review.

## Quick start

```bash
# Postgres :5442, test Postgres :5433, Redis :6380
docker compose up -d
uv sync
cp .env.example .env
uv run alembic upgrade head

uv run python scripts/poll_once.py
uv run arq app.worker.WorkerSettings
uv run uvicorn app.main:app --port 8100
uv run python -m app.bot
```

## Pipeline

```text
Reddit / Hacker News / Threads
→ freshness + mute + keyword gates
→ exact dedup
→ Haiku classify + threshold
→ Sonnet draft A/B/C
→ Telegram owner approval
→ copy-mode or guarded API-send
→ reply watch + CRM outcome tracking
```

The classifier circuit breaker stores work during a sustained Claude outage and probes for
recovery without flooding the owner with UNSCORED alerts. Drafting has its own attempt cap and
outbox retry so polling remains fresh.

## Approval and sending

Approval cards offer `Send A/B/C · Edit · Skip · Mute keyword · Mute community`.

- `SEND_MODE=copy` returns the chosen text and thread link for manual posting.
- `SEND_MODE=api` queues the approved reply after mandatory jitter and exposes Cancel until the
  send executes.
- Execution-time guardrails: active halt, quiet hours, platform/DM caps, and subreddit cooldown.
- A committed `queued → executing` claim happens before the outbound API call, preventing an
  uncertain crash from posting twice.
- A Reddit mod removal auto-halts that platform until `scripts/clear_halt.py` is run manually.

## Sources

- **Reddit:** OAuth application-only polling when configured, otherwise RSS fallback.
- **Hacker News:** official Algolia search API.
- **Threads:** official keyword-search API with durable quota ledger and interval budget.
- **Threads CSE bridge:** Google Programmable Search restricted to public Threads permalinks;
  discovery only and structurally copy-mode.

Only one Threads discovery adapter should own a pack's queries in production to avoid duplicate
cards. Disable the CSE credentials after official public keyword search is approved.

## M5: evaluation and learning loop

### Weekly classifier review

Every Monday the worker sends one reminder when unlabeled sub-threshold posts are available.
Run this at any time from the owner chat:

```text
/review10
/review10 robofox_web
```

Each review card records `Demand`, `Not lead`, or `Skip` in `review_labels`. Labels are evidence
only: they do not automatically change thresholds, prompts, or send behavior.

### Evaluation dashboard

Open `/evals` to see:

- per-pack TP / FP / FN / TN, precision, and recall from human labels
- reply, conversation, and win performance by pack
- performance of the chosen A/B/C draft variants
- number and average change magnitude of owner-edited gold drafts
- post-to-alert p50, total LLM spend, and cost per surfaced lead

### Monthly edit-diff proposal

On the first day of each month, packs with at least three recent gold edits receive a conservative
Sonnet analysis. The proposal is stored as a `prompt_tuning_proposal` event and summarized in
Telegram. **Nothing is applied automatically.** A human must review the evidence and intentionally
edit prompts, personas, or few-shots.

Manual run:

```bash
uv run python scripts/propose_prompt_tuning.py
```

## Telegram setup

1. Create a bot with BotFather and set `TELEGRAM_BOT_TOKEN`.
2. Send it a message and set the resulting chat id as `TELEGRAM_CHAT_ID`.
3. The bot ignores callbacks, replies, and review commands from every other chat.

## Configuration highlights

- Storage: `DATABASE_URL`, `REDIS_URL`
- Polling: `POLL_INTERVAL_MINUTES`, Reddit credentials, Threads token/budgets, CSE credentials
- Approval/send: `SEND_MODE`, `OWNER_TZ`, quiet hours, daily caps, jitter, watch interval
- Alerts/CRM: Telegram credentials, optional `HUBSPOT_ACCESS_TOKEN`
- Models: `CLAUDE_FAST_MODEL`, `CLAUDE_STANDARD_MODEL`, classify/draft timeouts

See `.env.example` and `docs/FEATURES.md` for the complete operational reference.

## Tests

```bash
docker compose up -d postgres-test
uv run pytest -v
```

## Operations

- Dashboard: `/`, `/leads`, `/sends`, `/evals`
- Nightly database backup: `scripts/backup.sh`
- One-shot pipeline: `scripts/poll_once.py`
- Halt control: `scripts/clear_halt.py`
- Manual M5 proposal: `scripts/propose_prompt_tuning.py`
