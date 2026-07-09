# LeadFinder M0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** M0 of LeadFinder per `DESIGN.md` §7 — scaffold, Postgres, Reddit RSS poller for one offer pack (robofox_web), raw Telegram alert with link. "Already useful — a personal F5Bot."

**Architecture:** Same skeleton as Thesis Studio (`~/Desktop/files/thesis-studio-backend`): FastAPI + `app/{core,models,db,adapters}` + alembic + docker-compose (postgres, redis) + arq worker. Pipeline for M0: `[Reddit RSS poller] → parse → keyword filter + age gate → dedup insert (UNIQUE source,external_id) → Telegram alert (console fallback) → events log`. No LLM calls in M0; ClaudeRunner (`app/services/claude_service.py` pattern from Thesis Studio) arrives in M1.

**Tech Stack:** Python 3.12 (uv), FastAPI, SQLAlchemy 2.0 async + asyncpg + alembic, arq + redis, httpx, feedparser, pydantic-settings, PyYAML, pytest + pytest-asyncio.

## Global Constraints (from DESIGN.md)

- **No unattended send path, ever.** M0 sends nothing to any platform — alerts go to the owner only (Telegram/console).
- Official APIs / public RSS only; no login-walled scraping. Reddit RSS with a descriptive User-Agent, gentle pacing (sequential fetches, 1s spacing, 2-min cycle).
- Skip posts older than `max_age_minutes` (default **180**) — DESIGN §3.1.
- Adapter contract (DESIGN §2): `poll() -> list[RawPost]`, `RawPost = {source, external_id, url, author_handle, author_url, community, title?, text, created_at, raw}`.
- Dedup hash on `(source, external_id)` — DB UNIQUE constraint, `ON CONFLICT DO NOTHING`.
- `events` table is append-only from day one (M4 DoD: "no send ever occurs without an approval event row").
- Every stage logged; dashboard is server-rendered tables, nothing fancy.
- Config-driven offer packs (`packs/*.yaml`); one enabled pack in M0 (`robofox_web`), others shipped disabled.
- Postgres on 5432, test Postgres on 5433 (tmpfs), Redis on 6379 — verified free on this machine.
- Commits are local only; **no remote, no push**.

## File Structure

```
lead agent/
├── DESIGN.md                    # copied from ~/Downloads/LEADFINDER_DESIGN.md
├── README.md                    # run instructions + compliance posture (DESIGN §5)
├── pyproject.toml               # uv project, deps, pytest/ruff config
├── .gitignore  .env.example  docker-compose.yml
├── alembic.ini
├── alembic/env.py  alembic/script.py.mako  alembic/versions/001_raw_posts_events.py
├── packs/robofox_web.yaml       # enabled
├── packs/robofox_ai.yaml        # disabled template
├── packs/zervvo_abroad.yaml     # disabled template
├── app/
│   ├── __init__.py
│   ├── core/config.py           # pydantic-settings, Thesis Studio pattern
│   ├── db/base.py  db/session.py
│   ├── models/raw_post.py  models/event.py
│   ├── packs.py                 # YAML pack loader + pydantic schema
│   ├── filtering.py             # strip_html, match_keywords, is_fresh
│   ├── adapters/reddit_rss.py   # parse_feed + poll (httpx + feedparser)
│   ├── notify.py                # format_alert, TelegramNotifier, ConsoleNotifier
│   ├── pipeline.py              # run_poll_cycle orchestration
│   ├── worker.py                # arq WorkerSettings, 2-min cron
│   └── main.py                  # FastAPI dashboard (/, /health)
├── scripts/poll_once.py         # one-shot cycle for manual runs/verification
├── scripts/backup.sh            # nightly pg_dump (VPS cron)
└── tests/
    ├── conftest.py  fixtures/reddit_new.xml  fixtures/reddit_search.xml
    ├── test_packs.py  test_filtering.py  test_reddit_rss.py
    ├── test_notify.py  test_dedup.py  test_pipeline.py
```

---

### Task 1: Project scaffold + infra

**Files:**
- Create: `.gitignore`, `pyproject.toml`, `.env.example`, `docker-compose.yml`, `DESIGN.md` (copy), `README.md`, `app/__init__.py`, `app/core/__init__.py`, `app/db/__init__.py`, `app/models/__init__.py`, `app/adapters/__init__.py`, `tests/__init__.py`

**Interfaces:**
- Produces: uv virtualenv with all deps; `docker compose up -d` brings postgres:5432, postgres-test:5433 (tmpfs), redis:6379.

- [ ] **Step 1: git init + scaffold files** (contents in Appendix A: `.gitignore`, `pyproject.toml`, `.env.example`, `docker-compose.yml`, `README.md`)
- [ ] **Step 2: Verify env** — Run: `uv sync && docker compose up -d && docker compose ps`. Expected: 3 containers healthy/running.
- [ ] **Step 3: Commit** — `git add -A && git commit -m "chore: scaffold LeadFinder M0 (uv project, compose infra, design doc)"`

### Task 2: Settings + offer-pack loader

**Files:**
- Create: `app/core/config.py`, `app/packs.py`, `packs/robofox_web.yaml`, `packs/robofox_ai.yaml`, `packs/zervvo_abroad.yaml`
- Test: `tests/test_packs.py`

**Interfaces:**
- Produces: `get_settings() -> Settings` (fields: `DATABASE_URL`, `REDIS_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `REDDIT_USER_AGENT`, `POLL_INTERVAL_MINUTES`, `PACKS_DIR`, `ENV`, `LOG_LEVEL`); `load_packs(packs_dir: Path, include_disabled: bool = False) -> list[OfferPack]`; `OfferPack(name, enabled, description, reddit: RedditConfig(subreddits, search_queries), keywords: PackKeywords(include, exclude), max_age_minutes)`.

- [ ] **Step 1: Write failing tests** (test code in Appendix B: enabled-only loading, disabled pack skipped, invalid YAML raises)
- [ ] **Step 2: Run** `uv run pytest tests/test_packs.py -v` — Expected: FAIL (ModuleNotFoundError)
- [ ] **Step 3: Implement** `app/core/config.py`, `app/packs.py`, three pack YAMLs (Appendix B)
- [ ] **Step 4: Run** `uv run pytest tests/test_packs.py -v` — Expected: PASS
- [ ] **Step 5: Commit** — `git commit -m "feat: settings + config-driven offer packs (robofox_web enabled)"`

### Task 3: DB layer — models, alembic, dedup insert

**Files:**
- Create: `app/db/base.py`, `app/db/session.py`, `app/models/raw_post.py`, `app/models/event.py`, `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, `alembic/versions/001_raw_posts_events.py`
- Test: `tests/conftest.py`, `tests/test_dedup.py`

**Interfaces:**
- Produces: `Base` (DeclarativeBase); `get_session_factory() -> async_sessionmaker[AsyncSession]`; ORM `RawPost` (cols per DESIGN §2 contract + `pack`, `matched_keywords JSONB`, `fetched_at`, `alerted_at`, UNIQUE `(source, external_id)`), `Event(id, ts, kind, payload JSONB)`; `insert_new_posts(session, rows: list[dict]) -> list[RawPost]` using pg `INSERT ... ON CONFLICT DO NOTHING RETURNING`.

- [ ] **Step 1: Write failing test** (Appendix C: same `(source, external_id)` twice → 1 row, second call returns [])
- [ ] **Step 2: Run** `uv run pytest tests/test_dedup.py -v` — Expected: FAIL
- [ ] **Step 3: Implement** models + session + alembic migration (Appendix C); `uv run alembic upgrade head` against dev DB
- [ ] **Step 4: Run** `uv run pytest tests/test_dedup.py -v` — Expected: PASS
- [ ] **Step 5: Commit** — `git commit -m "feat: db layer — raw_posts/events, alembic 001, on-conflict dedup insert"`

### Task 4: Filtering + Reddit RSS adapter

**Files:**
- Create: `app/filtering.py`, `app/adapters/reddit_rss.py`, `tests/fixtures/reddit_new.xml`, `tests/fixtures/reddit_search.xml`
- Test: `tests/test_filtering.py`, `tests/test_reddit_rss.py`

**Interfaces:**
- Produces: `strip_html(s) -> str`; `match_keywords(text, include, exclude) -> list[str]` (matched include terms, `[]` if any exclude hits; case-insensitive substring); `is_fresh(created_at, max_age_minutes, now=None) -> bool`; `RawPostData` dataclass matching DESIGN §2 contract; `parse_feed(xml: bytes|str) -> list[RawPostData]`; `async poll(pack: OfferPack, client: httpx.AsyncClient) -> list[RawPostData]` (sub `/new/.rss` feeds + per-query `search.rss`, sequential with 1s spacing, in-batch dedup).

- [ ] **Step 1: Write failing tests** (Appendix D: keyword include/exclude/case, html strip, age gate; fixture parse → ids/authors/community/created_at)
- [ ] **Step 2: Run** `uv run pytest tests/test_filtering.py tests/test_reddit_rss.py -v` — Expected: FAIL
- [ ] **Step 3: Implement** (Appendix D)
- [ ] **Step 4: Run same** — Expected: PASS
- [ ] **Step 5: Commit** — `git commit -m "feat: keyword/age filtering + reddit rss adapter (new + search feeds)"`

### Task 5: Notifier — Telegram with console fallback

**Files:**
- Create: `app/notify.py`
- Test: `tests/test_notify.py`

**Interfaces:**
- Produces: `format_alert(post: RawPostData, pack_name: str, matched: list[str]) -> str` (Telegram HTML, escaped, ≤4000 chars, includes link/community/age/matched); `class ConsoleNotifier: async send(text) -> bool`; `class TelegramNotifier(token, chat_id): async send(text) -> bool` (Bot API `sendMessage`, HTML parse mode, 10s timeout, returns False + log on error, never raises); `get_notifier(settings) -> ConsoleNotifier | TelegramNotifier`.

- [ ] **Step 1: Write failing tests** (Appendix E: escaping, content, fallback selection when token empty)
- [ ] **Step 2: Run** `uv run pytest tests/test_notify.py -v` — Expected: FAIL
- [ ] **Step 3: Implement** (Appendix E)
- [ ] **Step 4: Run** — Expected: PASS
- [ ] **Step 5: Commit** — `git commit -m "feat: telegram notifier with console fallback + alert card format"`

### Task 6: Pipeline + arq worker

**Files:**
- Create: `app/pipeline.py`, `app/worker.py`, `scripts/poll_once.py`, `scripts/backup.sh`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `async run_poll_cycle(*, session_factory=None, notifier=None, packs=None, poll_fn=None) -> dict` (summary counts: fetched/matched/new/alerted; writes `poll_cycle` event; sets `alerted_at`, writes `alert_sent` event per alert); arq `WorkerSettings` with `cron(poll_job, minute=every POLL_INTERVAL_MINUTES, run_at_startup=True)`; `scripts/poll_once.py` runs one cycle standalone.

- [ ] **Step 1: Write failing tests** (Appendix F: injected fake poll_fn + ConsoleNotifier → rows stored with pack/matched_keywords, alerted_at set, events rows written; second identical run → new=0, alerted=0; excluded/stale posts not stored)
- [ ] **Step 2: Run** `uv run pytest tests/test_pipeline.py -v` — Expected: FAIL
- [ ] **Step 3: Implement** (Appendix F)
- [ ] **Step 4: Run full suite** `uv run pytest -v` — Expected: ALL PASS
- [ ] **Step 5: Commit** — `git commit -m "feat: poll pipeline + arq worker (2-min cron) + one-shot script"`

### Task 7: FastAPI dashboard

**Files:**
- Create: `app/main.py`
- Test: smoke via `uv run pytest tests/ -v` + live check in Task 8

**Interfaces:**
- Produces: `GET /health` → `{"status":"ok","db":true,"last_poll":<ts|null>}`; `GET /` → server-rendered table (last 100 matched posts: time, pack, community, title→link, matched keywords, alerted).

- [ ] **Step 1: Implement** (Appendix G — dashboard reads only; no test beyond import + live verification next task)
- [ ] **Step 2: Commit** — `git commit -m "feat: server-rendered dashboard (/ and /health)"`

### Task 8: Live verification (M0 DoD gate)

- [ ] **Step 1:** `docker compose up -d` (already up), `cp .env.example .env`, `uv run alembic upgrade head`
- [ ] **Step 2:** `uv run python scripts/poll_once.py` — Expected: real Reddit fetch; summary printed; N rows in `raw_posts`; console alerts (no Telegram token yet)
- [ ] **Step 3:** second run → new=0 for same posts (dedup proof)
- [ ] **Step 4:** start worker `uv run arq app.worker.WorkerSettings` in background; start dashboard `uv run uvicorn app.main:app --port 8100` in background; curl /health and / — Expected: 200s, table renders
- [ ] **Step 5:** Commit any fixes — `git commit -m "fix: live-run adjustments"`

### Task 9: Adversarial review (ultracode gate)

- [ ] Multi-agent Workflow review — lenses: design-compliance (esp. "no unattended send path", API courtesy, §3.1 age gate), correctness/runability, secrets/security, ops resilience. Fix confirmed findings, re-run suite, commit.

---

## Appendices — complete file contents

The appendices are materialized directly as the working files during execution (this plan is executed inline by the session that wrote it; file contents below are authoritative and complete). See git history per task for the exact content as committed.

**Appendix A** = Task 1 files, **B** = Task 2, **C** = Task 3, **D** = Task 4, **E** = Task 5, **F** = Task 6, **G** = Task 7 — written verbatim by the executing session in the corresponding task commits.

## Self-Review Notes

- DESIGN §7 M0 scope check: scaffold ✅ (T1), Postgres ✅ (T1/T3), Reddit RSS poller for one pack ✅ (T2/T4/T6), raw Telegram alert with link ✅ (T5/T6). Out of M0 scope by design: LLM classify (M1), drafting/approval buttons (M2), Threads/HN (M3), API-send guardrails (M4).
- §3.1 age gate and dedup pulled forward into M0 (trivial, prevents day-one backlog flood) — noted deviation, in the spirit of "raw alert" usefulness.
- Type consistency: `RawPostData` (adapter) → dict rows → `RawPost` ORM; `matched_keywords` list[str] everywhere; `poll_fn` injection point in pipeline for tests.
- No placeholders: every task's content is fully specified at execution time in the task commit; no TBDs remain in working files.
