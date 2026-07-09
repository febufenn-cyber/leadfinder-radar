# LeadFinder M1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (inline, same session as author). Checkbox steps track progress; each task commits.

**Goal:** M1 per DESIGN §7 — Haiku classifier + threshold + scored alert cards (dedup/prefilter shipped early in M0). Plus the two things M0's live run demanded: the ClaudeRunner layer (Thesis Studio port, every LLM call logged with tokens/cost per DoD) and a Reddit OAuth adapter (search.rss is throttled for unauthenticated clients; RSS remains the fallback).

**Architecture:** `pipeline: poll -> prefilter -> dedup insert -> [NEW] classify (tier fast/Haiku via ClaudeRunner subprocess) -> threshold gate -> scored alert`. Sub-threshold posts stored with their score but not surfaced (DESIGN §3.3). Classifier failure = alert anyway flagged UNSCORED (boutique volume: over-alerting beats silently losing a lead) + `classify_failed` event.

**Tech:** `claude -p` subprocess (Max OAuth, per DESIGN §4 auth note) with `--strict-mcp-config`/empty MCP config/`--no-session-persistence` — ported from `~/Desktop/files/thesis-studio-backend/app/services/claude_service.py` (call_compile pattern). New `llm_calls` table for token/cost audit. Reddit OAuth application-only (client_credentials) grant; JSON endpoints at oauth.reddit.com.

## Global Constraints (from DESIGN.md)

- §3.3 `LeadScore` schema verbatim: `is_demand_post, offer_pack, intent(explicit_request|problem_statement|recommendation_ask), buyer_type(business_owner|founder|student|individual|unclear), budget_signal(stated|implied|none), urgency(now|soon|exploring), disqualifiers[], fit_score 0-100, one_line_summary`.
- Threshold default **65**, per-pack overridable. Below threshold: stored, not surfaced.
- Few-shots are pack config (`packs/fewshots/<pack>.yaml`) — STARTER examples only; owner replaces with his real positives/negatives (design wants 10+10 owner-supplied).
- DoD: **every LLM call logged with tokens/cost** (`llm_calls` row per call, success or failure).
- No unattended send path. Classifier/enrichment autonomous; sending stays owner-approved (M2+).
- OAuth creds absent -> RSS adapter fallback keeps M0 behavior working.

## Tasks

1. **ClaudeRunner port + llm_calls** — `app/services/claude_runner.py` (`run_json(purpose, system_prompt, user_prompt, tier, session, raw_post_id=None) -> (dict|None, LlmCallMeta)`), `app/services/empty_mcp_config.json`, `app/models/llm_call.py`, migration `002`, config: `CLAUDE_CLI_PATH/CLAUDE_FAST_MODEL/CLAUDE_STANDARD_MODEL/CLASSIFY_TIMEOUT_SECONDS`. CLI invocation cloned from Thesis call_compile (`--tools "" --disable-slash-commands --no-session-persistence --strict-mcp-config --mcp-config <empty> --system-prompt-file <tmp> --output-format json`). Typed errors: rate-limit vs subprocess. Tests: stub `_run_cli`, assert JSON extraction, fence-stripping, usage row written on success AND failure.
2. **Migration 002 also adds raw_posts score columns** — `fit_score INT NULL`, `score JSONB NULL`, `classified_at timestamptz NULL` (+ ORM fields).
3. **LeadScore + classifier** — `app/classify.py`: pydantic `LeadScore` (enums + 0-100 bound), `build_prompts(pack, fewshots, post) -> (system, user)`, `classify_post(runner, session, pack, row) -> LeadScore|None`. `packs/fewshots/robofox_web.yaml` starter set. Pack schema gains `threshold: int = 65`. Tests: fake runner returns good/malformed/over-bound JSON.
4. **Pipeline integration** — classify each NEW post; store score fields; alert iff `score is None` (UNSCORED flag) or `fit_score >= pack.threshold`; events `classified`/`classify_failed`; summary gains `classified/surfaced/suppressed`. Scored alert card: `⭐ fit` + one-line summary + intent/urgency line. Tests: above/below threshold, failure fallback.
5. **Reddit OAuth adapter** — `app/adapters/reddit_oauth.py`: token cache (client_credentials, refresh 5 min early), `poll(pack, client)` hitting `oauth.reddit.com/r/a+b+c/new` + `/search?q=...` per query, JSON->RawPostData, 429 cooldown map like RSS. Pipeline selects OAuth when `REDDIT_CLIENT_ID/SECRET` set else RSS. `.env.example` documents creating the script app at reddit.com/prefs/apps. Tests: JSON listing fixture parse, token refresh, adapter selection.
6. **Dashboard** — score + summary columns, surfaced (alerted) indicator.
7. **Live verification** — real classify call via claude CLI on a synthetic post (checks Max OAuth path + llm_calls row + cost), then a real cycle; worker restart.
8. **Adversarial review** — Sonnet workflow (standing model preference), Fable advises, fix confirmed findings.

## Self-Review Notes
- §3.3 covered (T3/T4), §7 M1 "scored alert cards" (T4), DoD token/cost logging (T1), threshold overridable per pack (T3).
- Type chain: RawPostData -> row dict -> RawPost ORM -> classify(row) -> LeadScore -> `score` JSONB + `fit_score` int; alert card takes (post, pack, matched, score|None, unscored flag).
- Enrichment (§3.4) is explicitly optional and deferred; drafting is M2. Not scope creep — skipped.
