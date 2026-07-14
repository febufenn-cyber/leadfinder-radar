# LEADFINDER — DESIGN.md
### Demand-post radar → scored leads → drafted replies → one-tap approved send
Version 1.1 · Owner: Febin · Infra: existing Oracle Cloud VPS

---

## 0. WHAT THIS IS, AND THE ONE DESIGN DECISION THAT MATTERS

People post buying intent in public every hour: "need a website for my shop," "anyone know someone who builds AI chatbots," "recommend a study-abroad consultant." LeadFinder watches those streams, scores them, drafts a standout reply, and pushes it to the owner's phone for a one-tap send.

**The send gate is human. This is the load-bearing decision — for performance reasons, not just policy ones:**

1. **Unattended auto-replying kills the channel.** Repetitive or unsolicited automation risks account sanctions and community blacklisting.
2. **Bot-smelling replies lose deals.** The winning reply is helpful-first, context-specific, and clearly from a person.
3. **Speed is preserved anyway.** Discovery and drafting remain autonomous; the only added latency is one explicit owner action.

So: **discovery, dedup, scoring, enrichment, drafting, evaluation, and MCP inspection = autonomous. Sending = owner-approved, always.** No unattended posting, mass DMs, fake accounts, batch approval, or automatic follow-up.

---

## 1. OFFER PACKS

A lead is only a lead relative to an offer. Config-driven offer packs own keywords, communities, qualifiers, reply persona, rules, threshold, and CRM destination:

- **robofox_web** — websites, landing pages, small-business web presence.
- **robofox_ai** — chatbots, WhatsApp automation, AI workflows/agents, Meta ads setup.
- **zervvo_abroad** — study-abroad intent, IELTS, visa counseling.
- A future **thesis_service** pack must remain comment-disabled/DM-draft-only where community rules require it.

---

## 2. SOURCES

| Source | Method | Notes |
|---|---|---|
| Reddit | Official OAuth API, with public RSS fallback | Read-only polling is separate from owner-authenticated send/watch. |
| Threads | Official Threads API keyword search and reply APIs | Durable daily quota ledger and minimum polling interval. |
| Threads CSE bridge | Google Programmable Search restricted to `threads.com` | Temporary copy-mode discovery bridge while public keyword search awaits approval. |
| Hacker News | Official Algolia search API | Story search by date. |
| Excluded | X, LinkedIn automation, Facebook Groups, Upwork automation | No compliant or proportionate v1 path. |

Adapters normalize to one `RawPostData` contract. Only one Threads discovery adapter should own a pack's queries in production.

---

## 3. PIPELINE

```text
[Pollers] → age/mute/keyword gates → exact dedup → classify + score
→ threshold → draft variants → owner approval
→ copy-mode or guarded API-send → reply watch + CRM + evaluation
```

### 3.1 Dedup and freshness

Exact identity is `(source, external_id)`. Posts older than each pack's `max_age_minutes` are rejected before model spend.

### 3.2 Zero-cost prefilter

Per-pack include and exclude rules remove obvious non-leads before classification. Mutes are applied by pack or globally.

### 3.3 Classification

Fast-tier classification returns structured demand, intent, buyer, budget, urgency, disqualifiers, fit score, and summary. Sub-threshold posts are retained for evaluation but not surfaced.

### 3.4 Drafting

Standard-tier drafting produces helpful public, short DM, and optional comment+DM variants. Hard rules prohibit false claims, fake urgency, template openers, and community-rule violations. Unknown communities default to no promotion.

### 3.5 Approval and send

Telegram cards support one-item send, edit, skip, and mute. Copy mode returns text and link. API mode queues a single approved reply after jitter. Code-enforced guardrails include active halts, quiet hours, daily platform/DM caps, community cooldown, cancellation, and crash-safe `queued → executing` claiming. There is no auto-approve or batch-approve path.

### 3.6 CRM and watch

Lead state is `surfaced → drafted → sent → replied → conversation → won|lost|no_response`, plus `skipped`. Reply detection alerts the owner and may sync to HubSpot. Follow-ups remain human.

---

## 4. ARCHITECTURE

```text
FastAPI dashboard
PostgreSQL: raw_posts, leads, drafts, revisions, sends, events, mutes, reviews, MCP challenges
Redis + arq worker
Telegram owner bot
ClaudeRunner fast/standard tiers
FastMCP owner-control surface
Docker/systemd on the existing VPS
```

Every LLM call is audited. Every MCP call is bounded, rate-limited, timed out, and recorded with redacted arguments. Dashboard and MCP are control/inspection surfaces; the phone remains the primary approval UI.

### M6 MCP layer — implemented

Tools:

- `health`
- `search_leads(filters)`
- `get_lead(id)`
- `stats(period, pack)`
- `redraft(id, guidance)`
- `mute(kind, value, pack)`
- `request_approval_code(id, variant)`
- `approve(id, variant, verification_value)`

Stdio/local transport is the default. Streamable HTTP is explicit, bearer-authenticated, and loopback by default. Read tools return bounded typed objects. Redraft archives prior drafts and cannot approve/send. Mute reuses existing normalization and deduplication.

MCP approval is two-step: request a short-lived value delivered only to owner Telegram, then consume it for exactly one draft. Only an HMAC is persisted; challenges bind to lead, draft ID, variant, and draft-text digest. Approval reuses the existing copy/API service, preserving approval-event ordering and all send guardrails.

No arbitrary SQL, shell, filesystem-write, prompt-edit, direct platform-post, batch approval, or lead-state mutation tool exists.

---

## 5. COMPLIANCE POSTURE

Official APIs and public RSS only. No login-walled scraping, browser-session harvesting, fake accounts, account rotation, or engagement manipulation. One real owner account per platform. Respect community rules and disclose AI assistance honestly when asked. Target a few excellent engagements per day, not mass outreach.

---

## 6. EVALS AND LEARNING LOOP

- Balanced weekly review samples surfaced and suppressed classifier decisions for precision and recall.
- `/evals` reports confusion metrics, pack/variant outcomes, edit magnitude, latency, and cost.
- Owner edits are gold samples.
- Monthly analysis proposes narrow prompt changes but never applies them automatically.
- North-star test remains paying conversations, not alert volume.

---

## 7. MILESTONES

- **M0:** scaffold, PostgreSQL, Reddit RSS, raw owner alerts.
- **M1:** dedup, keyword prefilter, classifier, scored alerts.
- **M2:** drafting, approval cards, copy-mode, lead state machine.
- **M3:** Threads, Hacker News, additional packs.
- **M4:** guarded API-send, reply detection, auto-halt, HubSpot sync.
- **M5:** balanced review, evaluation dashboard, edit-diff proposals.
- **M6:** secure MCP read/mutation tools, audited runtime, Telegram-gated single-use approvals, deployment and smoke tooling. **Implemented.**

Definition of done per phase: lint/tests/migrations pass, changes are committed and merged, no send occurs without approval evidence, and production deployment is separately confirmed. The 48-hour unattended soak remains an operational validation, not something a single build session can prove.

## 8. NON-GOALS

Unattended posting/DMs, batch approvals, fake accounts, account rotation, automatic follow-ups, prompt auto-application, public unauthenticated MCP hosting, arbitrary database/shell access, multi-tenant SaaS, LinkedIn/FB-group automation, or scraping past access controls.
