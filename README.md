# LeadFinder

Demand-post radar → scored leads → drafted replies → **one-tap owner-approved send**.

LeadFinder watches compliant public sources for buying intent, applies cheap filters and a classifier, drafts context-specific replies, and sends each candidate to the owner's Telegram. Discovery, scoring, drafting, evaluation, prompt analysis, and MCP inspection are autonomous. **Sending remains per-reply owner-approved, always.**

Full design: [DESIGN.md](DESIGN.md). Current milestone: **M6 — secure MCP control server complete**.

## The one rule that matters

There is no unattended send path, no batch approval, no account rotation, and no automatic follow-up campaign. API-send is optional and still starts from one explicit owner action on one draft. Telegram approval cards and MCP approvals both reuse the same approval-event and send-guardrail services.

## Compliance posture

- Official APIs and public RSS only; no login-walled scraping or browser-session harvesting.
- One real account per platform: the owner's.
- Community rules are injected into drafting; unknown communities default to no promotion.
- Volume stays boutique: a few high-quality engagements per day, not mass outreach.
- Threads discovery uses the official API when approved; the Google CSE bridge is a temporary, copy-mode-only discovery source while public keyword search is under Meta App Review.

## Quick start

```bash
# Postgres :5442, test Postgres :5433, Redis :6380
docker compose up -d
uv sync --frozen
cp .env.example .env
uv run alembic upgrade head

uv run python scripts/poll_once.py
uv run arq app.worker.WorkerSettings
uv run uvicorn app.main:app --port 8100
uv run python -m app.bot

# M6 verification
uv run python scripts/mcp_smoke.py --in-memory
uv run python scripts/mcp_smoke.py
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

The classifier circuit breaker stores work during a sustained Claude outage and probes for recovery without flooding the owner with UNSCORED alerts. Drafting has its own attempt cap and outbox retry so polling remains fresh.

## Approval and sending

Approval cards offer `Send A/B/C · Edit · Skip · Mute keyword · Mute community`.

- `SEND_MODE=copy` returns the chosen text and thread link for manual posting.
- `SEND_MODE=api` queues the approved reply after mandatory jitter and exposes Cancel until the send executes.
- Execution-time guardrails: active halt, quiet hours, platform/DM caps, and subreddit cooldown.
- A committed `queued → executing` claim happens before the outbound API call, preventing an uncertain crash from posting twice.
- A Reddit mod removal auto-halts that platform until `scripts/clear_halt.py` is run manually.

## Sources

- **Reddit:** OAuth application-only polling when configured, otherwise RSS fallback.
- **Hacker News:** official Algolia search API.
- **Threads:** official keyword-search API with durable quota ledger and interval budget.
- **Threads CSE bridge:** Google Programmable Search restricted to public Threads permalinks; discovery only and structurally copy-mode.

Only one Threads discovery adapter should own a pack's queries in production to avoid duplicate cards. Disable the CSE credentials after official public keyword search is approved.

## M5: evaluation and learning loop

### Weekly classifier review

Every Monday the worker sends one reminder when unlabeled classifier decisions are available. The review queue alternates surfaced and suppressed predictions across packs, so both precision and recall can be measured.

```text
/review10
/review10 robofox_web
```

Each card records `Demand`, `Not lead`, or `Skip` in `review_labels`. Labels are evidence only: they do not automatically change thresholds, prompts, or send behavior.

### Evaluation dashboard

Open `/evals` for classifier confusion metrics, reply/conversation/win outcomes, selected variant performance, owner-edit magnitude, post-to-alert p50, and LLM cost per surfaced lead.

### Monthly edit-diff proposal

Packs with enough owner edits receive a conservative monthly proposal stored as `prompt_tuning_proposal`. **Nothing is applied automatically.**

```bash
uv run python scripts/propose_prompt_tuning.py
```

## M6: secure MCP control server

LeadFinder now exposes eight typed owner tools:

```text
health
search_leads
get_lead
stats
redraft
mute
request_approval_code
approve
```

### Safe default: stdio

An MCP client launches LeadFinder locally as a subprocess. Use the secret-free template at `config/mcp/leadfinder-stdio.json.example`.

```bash
uv run python -m app.mcp.server
```

### MCP safety boundaries

- Read tools return bounded Pydantic structures and omit raw platform payloads.
- Every call has a timeout, process rate limit, sanitized error, and redacted `mcp_tool_call` audit event.
- HTTP transport is opt-in, bearer-authenticated, and loopback by default.
- `redraft` preserves prior drafts in `draft_revisions` and never approves or sends.
- `mute` accepts only normalized keyword/community values.
- MCP approval requires a short-lived single-use value delivered only to the owner Telegram chat.
- The value is never returned by MCP or stored in plaintext and is bound to the exact draft digest.
- `approve` reuses the existing copy/API approval path; no direct platform-post or batch tool exists.

Complete setup, deployment, rollback, and troubleshooting: [docs/M6.md](docs/M6.md).

## Telegram setup

1. Create a bot with BotFather and set `TELEGRAM_BOT_TOKEN`.
2. Send it a message and set the resulting chat id as `TELEGRAM_CHAT_ID`.
3. The bot ignores callbacks, replies, and review commands from every other chat.
4. Set an independent `MCP_APPROVAL_SECRET` before using MCP approval tools.

## Configuration highlights

- Storage: `DATABASE_URL`, `REDIS_URL`
- Polling: `POLL_INTERVAL_MINUTES`, Reddit credentials, Threads token/budgets, CSE credentials
- Approval/send: `SEND_MODE`, `OWNER_TZ`, quiet hours, daily caps, jitter, watch interval
- Alerts/CRM: Telegram credentials, optional `HUBSPOT_ACCESS_TOKEN`
- Models: `CLAUDE_FAST_MODEL`, `CLAUDE_STANDARD_MODEL`, classify/draft timeouts
- MCP: transport/bind/auth/rate/timeout settings and separate approval secret

See `.env.example`, `docs/M6.md`, and `docs/FEATURES.md` for the complete operational reference.

## Tests

```bash
docker compose up -d postgres-test
uv run ruff check .
uv run alembic upgrade head
uv run pytest -v
uv run python scripts/mcp_smoke.py --in-memory
```

CI retains a JUnit report and verifies both a clean migration and the `008 → 009` upgrade.

## Operations

- Dashboard: `/`, `/leads`, `/sends`, `/evals`
- Nightly database backup: `scripts/backup.sh`
- One-shot pipeline: `scripts/poll_once.py`
- Halt control: `scripts/clear_halt.py`
- Manual M5 proposal: `scripts/propose_prompt_tuning.py`
- MCP smoke: `scripts/mcp_smoke.py`
- Optional loopback MCP unit: `deploy/leadfinder-mcp.service`
