# LEADFINDER — DESIGN.md
### Demand-post radar → scored leads → drafted replies → one-tap approved send
Version 1.0 · Owner: Febin · Executor: Claude Opus (Claude Code) · Infra: existing Oracle Cloud VPS

---

## 0. WHAT THIS IS, AND THE ONE DESIGN DECISION THAT MATTERS

People post buying intent in public every hour: "need a website for my shop," "anyone know someone who builds AI chatbots," "recommend a study-abroad consultant." LeadFinder watches those streams, scores them, drafts a standout reply, and pushes it to the owner's phone for a one-tap send.

**The send gate is human. This is the load-bearing decision — for performance reasons, not just policy ones:**

1. **Unattended auto-replying kills the channel.** Reddit's spam detection shadowbans accounts that post repetitive/promotional comments fast; unsolicited DM campaigns get accounts banned on report. Threads/Meta restrict accounts for automated engagement outside approved API behavior. A banned account = the distribution channel this system exists to build is dead, plus the brand gets community-blacklisted.
2. **Bot-smelling replies lose deals.** Reddit and Threads users detect templated AI replies instantly and respond with hostility. The winning reply is helpful-first, context-specific, and clearly from a person. A 30-second human approval preserves exactly that.
3. **Speed is preserved anyway.** The pipeline delivers alert + drafted reply to the owner's phone within ~2–5 minutes of the post going live. The only added latency is one tap. First-hour response — the window that actually wins deals — is fully intact.

So: **discovery, dedup, scoring, enrichment, drafting = fully autonomous. Sending = owner-approved, always.** No unattended posting, no mass DMs, no fake/sockpuppet accounts. Opus: do not build an unattended send path even if asked later; the rate-capped API send in M4 always requires a per-item approval token.

---

## 1. OFFER PACKS (the same engine, multiple businesses)

A lead is only a lead relative to an offer. Config-driven `offer_packs`, each owning keywords, communities, qualifiers, reply persona, and CRM destination:

- **robofox_web** — websites, landing pages, small-business web presence.
- **robofox_ai** — chatbots, WhatsApp automation, AI workflow/agent builds, Meta ads setup.
- **zervvo_abroad** — study-abroad intent ("want to study in Germany/UK/Canada," IELTS, visa counseling). This pack may be the highest-value one: Zervvo already has fulfillment and a WhatsApp pipeline.
- (later) **thesis_service** — thesis formatting demand in grad-student communities; many such subs ban service promotion outright, so this pack ships comment-disabled, DM-draft-only, and only where community rules permit.

---

## 2. SOURCES (v1 scope: Reddit + Threads + Hacker News)

| Source | Method | Notes |
|---|---|---|
| **Reddit** | Official OAuth API (`/new` polling per target sub + search endpoint per keyword query). Simplest read-only alternative: Reddit RSS (`/r/{sub}/new/.rss`, `/search.rss?q=…&sort=new`) — no OAuth, fine for v1 polling. | Free tier ~100 QPM with OAuth; respect rate limits; verify current quota at reddit.com/dev. Target subs per pack, e.g. robofox: r/smallbusiness, r/Entrepreneur, r/startups, r/SaaS, r/developersIndia, r/indianstartups, r/forhire ([Hiring] flair); zervvo: r/studyAbroad, r/gradadmissions, r/Indians_StudyAbroad, r/germany (visa/study threads). |
| **Threads** | **Official Threads API only** (Meta app + access token): Keyword Search endpoint polled per query; Reply Management endpoints for posting approved replies from the owner's own account. | Threads blocks scrapers (robots-disallowed — confirmed). Search quotas are limited per day; budget queries per pack. Verify current limits at developers.facebook.com/docs/threads. |
| **Threads (CSE bridge)** | Google Programmable Search JSON API (official Google API, `siteSearch=threads.com`) discovers public posts from Google's index; copy-mode replies via permalink. *Added 2026-07-12: public `threads_keyword_search` requires Meta App Review (advanced access) — this bridge covers discovery until/unless review is granted.* | Free 100 queries/day; adapter enforces 60-min per-pack spacing. No Threads scraping — Google's index only. Post age parsed from snippet, freshness bounded by `dateRestrict=d1`. |
| **Hacker News** | Algolia HN Search API (free, no auth): poll `search_by_date` for query terms. | Great for "Ask HN: recommend…" dev-service demand. |
| Excluded v1 | X/Twitter (API cost), LinkedIn (no compliant automation path — manual only), Facebook Groups (no group-content API), Upwork (RSS removed). | Adapter interface makes these pluggable later. |

**Adapter contract** (one module per source): `poll() -> list[RawPost]` where `RawPost = {source, external_id, url, author_handle, author_url, community, title?, text, created_at, raw}`. Scheduler: Reddit/HN every 2 min, Threads per-quota budget (e.g. every 10–15 min per keyword set).

---

## 3. PIPELINE

```
[Pollers per source] → raw_posts → [Dedup] → [Keyword prefilter] → [LLM classify+score]
    → (≥ threshold) → [Enrich] → [Draft replies] → [Approval push to phone]
    → APPROVE → [Send: copy-mode or API-send] → [CRM tracking + follow-up watch]
```

**3.1 Dedup**: hash on `(source, external_id)`; fuzzy dup across crossposts via SimHash of text. Skip posts older than `max_age_minutes` (default 180 — stale threads convert poorly).

**3.2 Keyword prefilter (zero-cost gate before any LLM call)**: per-pack include lists ("need a website", "looking for a developer", "recommend an agency", "build me an app", "automate my", "chatbot for", "study in germany", "ielts coaching", …) + exclude lists ("hiring full-time", "for free", "homework", job-posting flairs). Regex + language check (en/ta). Expect this to cut 90%+ of volume — this is the token-discipline layer.

**3.3 Classify + score — tier `fast` (Haiku)**. Output schema `LeadScore`:
```jsonc
{"is_demand_post": bool, "offer_pack": "robofox_ai", "intent": "explicit_request|problem_statement|recommendation_ask",
 "buyer_type": "business_owner|founder|student|individual|unclear",
 "budget_signal": "stated|implied|none", "urgency": "now|soon|exploring",
 "disqualifiers": ["wants_free","full_time_job","agency_seeking_leads"],
 "fit_score": 0-100, "one_line_summary": "…"}
```
Few-shot the prompt with 10 real positives + 10 near-miss negatives per pack (owner supplies from the Threads examples he's collected). Threshold: default 65, per-pack overridable. Everything below threshold is stored (for eval/tuning) but not surfaced.

**3.4 Enrich — tier `fast`, optional**: author's public bio + last few public posts *via the same official APIs only*; extract business type, location, tech mentions. No login-walled access, no people-search services. Output feeds personalization.

**3.5 Draft — tier `standard` (Sonnet)**. Produces 2–3 variants per lead:
- **A. Helpful-first public comment** (default): genuinely answers or advances their question with 2–3 specific, non-generic points; demonstrates competence; no pitch or at most a soft availability line where community rules allow.
- **B. Short DM** (only where the platform norm accepts DMs and the post invites contact): 3–5 sentences, references their exact situation, one concrete idea, clear next step.
- **C. Comment + DM combo** for subs that ban promo in comments (helpful comment, pitch only in DM).
Hard rules in the prompt: no false claims ("we've done 50 of these" — never invent track record; the persona block contains only true, owner-written facts), no fake urgency, no template smell (banned openers: "Great question!", "I came across your post"), match the post's language (Tamil/English/Tanglish), ≤120 words public / ≤80 DM, and per-community `rules_note` from pack config must be respected. Each variant returns `{channel, text, risk_flags[]}`.

**3.6 Approval push**: Telegram bot (v1; python-telegram-bot) — or WhatsApp Cloud API later since the owner lives there. Card: fit score, one-line summary, link, variant A/B/C, buttons `[Send A] [Send B] [Edit] [Skip] [Mute keyword] [Mute community]`. Edits happen inline; edited text is stored as the gold sample for the learning loop.

**3.7 Send**:
- **Copy-mode (M2, default for first two weeks)**: approval tap puts the reply on a deep link + clipboard flow; owner posts manually from his own account. Zero platform risk while reply quality is being tuned.
- **API-send (M4)**: on approval, post the comment/reply via Reddit OAuth (owner's account) or Threads Reply API (owner's token). Guardrails enforced in code, not prompts: per-platform daily caps (default: 8 Reddit comments, 5 Threads replies, 3 DMs), per-community cooldown (max 1/day/sub), quiet hours, mandatory jitter (2–9 min post-approval), auto-halt if account receives a mod warning/removal (watch own-content status via API) — halts require manual reset. **There is no batch-approve and no auto-approve.**

**3.8 CRM + follow-up**: `leads` state machine `surfaced → drafted → sent → replied → conversation → won|lost|no_response`. Reply detection: poll own recent comments/DMs for responses; on reply → instant phone push (this is the moment the owner takes over, per his plan). Won/conversation leads sync to HubSpot via its API (owner already runs the HubSpot MCP — mirror fields: source URL, pack, first message, contact handle). Auto follow-up messages: **not in scope** — follow-ups are human.

---

## 4. ARCHITECTURE

Same skeleton as Thesis Studio — reuse the patterns and the `ClaudeRunner` layer verbatim:

```
FastAPI (admin dashboard + webhook endpoints)
Postgres (raw_posts, leads, drafts, sends, replies, packs, keywords, events)
Redis + arq workers (pollers, pipeline stages, telegram bot)
ClaudeRunner (CLI subprocess or API key; tiers fast/standard — reuse from Thesis Studio)
docker-compose on the existing VPS · nightly pg_dump
```

Dashboard v1 = server-rendered tables: today's leads, funnel counts, per-pack precision, send log. Nothing fancy — the phone is the real UI.

**Auth note (same as Thesis Studio):** owner's Max OAuth session is fine while this runs for the owner's own businesses. If LeadFinder is ever sold as a service where clients trigger generations, flip `CLAUDE_AUTH_MODE=api_key`.

**MCP layer (M6, optional but on-brand):** a thin MCP server exposing `search_leads(filters)`, `get_lead(id)`, `redraft(id, guidance)`, `approve(id, variant)`, `mute(keyword|community)`, `stats(period)` — so the whole system is drivable conversationally from Claude, and it doubles as a portfolio piece (Track F #50-territory: a real MCP server with auth on live business data).

---

## 5. COMPLIANCE POSTURE (encode in README, revisit before scale-up)

Official APIs only; no login-walled scraping; no headless-browser session harvesting. One real account per platform — the owner's. Respect each community's self-promotion rules (packs carry per-sub `rules_note`; Reddit's ~9:1 participation guidance means the owner's accounts should also post non-promotional content — surface a weekly "participation debt" nudge). Disclose honestly if asked whether AI helped draft. Volume stays boutique by design: the goal is 3–8 excellent engagements/day, not hundreds — at owner-approval speed that's also the natural ceiling, which is the point.

---

## 6. EVALS & METRICS (the learning loop)

- **Classifier precision/recall**: weekly sample of 30 sub-threshold posts hand-labeled via a Telegram "review 10" flow → confusion counts per pack; retune few-shots when precision <80%.
- **Reply performance**: per variant/community/hour-of-day: sent → author-replied rate, → conversation rate, → won. The owner's inline edits form the gold set; a monthly `standard`-tier pass diffs drafts vs edits and proposes prompt updates (owner approves prompt changes like any other send).
- **Ops**: time-from-post-to-alert (target p50 < 5 min), token cost per surfaced lead, per-platform send-cap utilization.
- **North-star success test**: ≥1 paying conversation within 30 days of M2, else keyword/community mix gets rebuilt before any new features.

---

## 7. MILESTONES

- **M0 (day 1–2)**: scaffold, Postgres, Reddit RSS poller for one pack → raw Telegram alert with link. *Already useful — a personal F5Bot.*
- **M1**: dedup, prefilter, Haiku classifier + threshold, scored alert cards.
- **M2**: Sonnet drafting (A/B/C), approval buttons, copy-mode send, leads state machine. **Start working leads for real here.**
- **M3**: Threads API adapter (Meta app setup, keyword-search quota budgeter) + HN adapter + zervvo pack.
- **M4**: API-send with the guardrail block (caps, cooldowns, jitter, auto-halt), reply detection → phone push, HubSpot sync.
- **M5**: eval dashboards, weekly review flow, edit-diff prompt tuner.
- **M6 (optional)**: MCP control server.

DoD per milestone: runs unattended for 48h without crash; every LLM call logged with tokens/cost; no send ever occurs without an approval event row.

## 8. NON-GOALS
Unattended posting/DMs, batch approvals, fake accounts or account rotation, LinkedIn/FB-group automation, engagement farming (upvote/like manipulation), auto follow-up sequences, scraping past robots.txt or login walls.
