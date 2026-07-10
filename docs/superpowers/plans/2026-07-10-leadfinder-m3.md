# LeadFinder M3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (inline, same session as author). Each task commits.

**Goal:** M3 per DESIGN §7 — Threads API adapter (official API only, keyword-search quota budgeter), HN adapter (Algolia, no auth), zervvo_abroad pack enabled.

**Architecture:** Packs become multi-source: `reddit` + `hn` + `threads` sections, each mapped by its own adapter to the same `RawPostData` contract (DESIGN §2). The default poller composes all sources per pack; injected `poll_fn` still overrides everything (test compatibility). Threads is gated twice: absent `THREADS_ACCESS_TOKEN` disables the adapter entirely; with a token, a **DB-backed budgeter** (one `threads_query` event per API call) enforces a daily query budget + minimum poll interval — durable across worker restarts, per DESIGN §2 "budget queries per pack".

## Global Constraints (from DESIGN.md)

- §2 Threads: **official Threads API only** (Meta app + access token, keyword-search endpoint). No scraping — Threads is robots-disallowed. Cadence ~10–15 min per keyword set, NOT the 2-min reddit cadence. Verify current quotas at developers.facebook.com/docs/threads (README note).
- §2 HN: Algolia search_by_date, free/no auth, 2-min cadence fine.
- §2 adapter contract: `poll() -> list[RawPostData]` unchanged; dedup key `(source, external_id)` already unique per source.
- zervvo_abroad (§1): "may be the highest-value pack" — enable it; ships with starter few-shots + empty persona (owner replaces, same as robofox_web).
- Read-only everywhere. No write/post calls to any platform (still copy-mode until M4).

## Tasks

1. **Pack schema**: `OfferPack.hn: SourceQueries` + `OfferPack.threads: SourceQueries` (`search_queries: []`); robofox_web gains 2 HN queries; update pack tests.
2. **HN adapter** — `app/adapters/hn.py`: Algolia `search_by_date?query=&tags=story`, map hits (objectID → external_id, HN item URL as post URL, community `hackernews`, story_text HTML-stripped). Fixture tests + live verification (no auth needed).
3. **Threads adapter + budgeter** — `app/adapters/threads.py`: `keyword_search` endpoint (`search_type=RECENT`, fields id,text,username,permalink,timestamp), token from `THREADS_ACCESS_TOKEN`. Budgeter: count today's `threads_query` events (UTC) vs `THREADS_DAILY_QUERY_BUDGET` (default 48) and enforce `THREADS_MIN_INTERVAL_MINUTES` (default 15) since the last query event; one event row per API call. Clear log message on token expiry (OAuth error). Tests: budget exhausted → zero API calls; min-interval skip; event written per call; parse fixture.
4. **Pipeline composition** — `select_poll_fn` returns a per-pack composite (reddit + hn + threads-if-token); `.env.example`/config additions.
5. **zervvo pack live** — `enabled: true`, threads queries, community_rules; `packs/fewshots/zervvo_abroad.yaml` + `packs/personas/zervvo_abroad.yaml` starters (owner-TODO marked).
6. **README** M3 section (Meta app setup pointer, quota note).
7. **Live verification** — real HN poll; full suite; worker restart.
8. **Adversarial review** — fleet when quota allows (M2 fleet died on usage limit; M2 was self-reviewed by Fable instead — re-queue M2+M3 together).

## Self-Review Notes
- Threads posting (Reply Management endpoints) is deliberately ABSENT — that's M4's API-send, still owner-approved only.
- Budgeter durability: events-table count survives restarts (in-memory would double-spend the daily quota on every deploy).
- Type chain: all adapters emit `RawPostData`; the classifier/drafter path is source-agnostic already.
