# LeadFinder M4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (inline, same session as author). Each task commits.

**Goal:** M4 per DESIGN §7 — API-send with the guardrail block (caps, cooldowns, jitter, auto-halt), reply detection → phone push, HubSpot sync.

**Architecture:** Approval taps in `SEND_MODE=api` no longer return copy-paste text — they write the approval Event (unchanged DoD), then queue a `sends` row scheduled `now + jitter(2–9 min)`. A 1-minute send cron picks due sends, re-checks EVERY guardrail at execution time, posts via the owner's own credentials (Reddit user-token / Threads reply API), and only then advances the lead to `sent`. A watch cron (5-min) polls the owner's inbox/replies: author replied → lead `replied` + instant phone push + HubSpot contact sync; own content removed/modded → **auto-halt** (all sends blocked until manually cleared). `SEND_MODE=copy` (default) keeps M2 behavior byte-for-byte.

## Global Constraints (from DESIGN.md)

- §0/§3.7: **per-item approval always; no batch-approve; no auto-approve.** The sends row must reference its approval Event id. Guardrails enforced in code, not prompts:
  - per-platform daily caps: **8 reddit comments, 5 threads replies, 3 DMs** (defaults, config-overridable)
  - per-community cooldown: **max 1 send/day/subreddit**
  - quiet hours (owner-local TZ, default Asia/Kolkata 23:00–07:00): due sends wait for morning
  - mandatory jitter: **2–9 min** post-approval, never immediate
  - **auto-halt** on mod warning/removal of own content — halts require manual reset (`scripts/clear_halt.py`)
- Cancel while queued is allowed (the jitter window makes it natural); cancelling writes an event.
- Lead state: approval in api mode does NOT mark `sent` — only a successful post does (copy mode keeps M2 semantics: tap = sent).
- §3.8: reply detection → push; won/conversation sync to HubSpot — implemented as sync-on-`replied` (the owner-takeover moment per §3.8) with the design's field mirror: source URL, pack, first message, contact handle. Noted deviation: earlier sync gets the contact into CRM when the owner actually takes over.
- No auto follow-ups. Ever.
- All credentials optional: reddit send needs `REDDIT_USERNAME/PASSWORD` (script-app user grant), threads send reuses `THREADS_ACCESS_TOKEN`, HubSpot needs `HUBSPOT_ACCESS_TOKEN`. Missing creds = that capability disabled with a clear log line.

## Tasks

1. **Schema** — migration 006: `sends` (lead/draft/approval_event ids, platform, channel, target, recipient, text, community, status queued|sent|failed|halted|cancelled, scheduled_at, sent_at, external_result_id, error) + `halts` (platform, reason, source JSONB, cleared_at). Models + tests.
2. **Guardrails** — `app/guardrails.py`: `check_send(session, send, settings, now) -> Verdict(allowed, reason, retry_at)` covering halt / quiet hours (zoneinfo TZ) / platform+channel daily cap (owner-TZ day) / community cooldown; `jitter_delay(rng)`. Exhaustive tests — this is the safety core.
3. **Senders** — `app/senders/reddit_user.py` (password-grant token manager + `/api/comment` + `/api/compose` DM), `app/senders/threads_send.py` (create-reply + publish two-step). Fake-client tests; never raise.
4. **Approval api-mode** — `approval.queue_send()` (approval Event first → sends row w/ jitter), `cancel_send()`; bot: api-mode reply "⏱ queued, posts in ~N min" + `[Cancel]` button; copy mode untouched. Tests incl. DoD ordering + cancel.
5. **Send cron** — `run_send_cycle` (1-min): due sends → guardrail re-check → execute → lead `drafted→sent`, owner push with permalink; deferrals reschedule (quiet/caps), halt blocks; events for every outcome. Tests: cap deferral, quiet-hours reschedule, halt block, success path, failure marks failed + notifies.
6. **Watcher** — `run_watch_cycle` (5-min): reddit inbox parent-id matching + threads `/replies` → lead `sent→replied` + push; own-content removal (`banned_by`/`[removed]` best-effort) → insert halt + push. `scripts/clear_halt.py`. Fixture tests.
7. **HubSpot** — `app/services/hubspot.py`: on `replied`, create contact + note (source URL, pack, first message, contact handle); disabled without token; fake-client tests.
8. **Dashboard** — `/sends` log page (status, platform, scheduled/sent, permalink, error).
9. **Docs/env** — README M4 section (guardrails table, halt reset, cred setup); `.env.example`.
10. **Verification + review** — full suite; worker restart (send/watch crons registered, disabled paths logged); fleet review when quota allows.

## Self-Review Notes
- The send path is the FIRST platform-write code in the repo. Triple-gated: SEND_MODE flag + per-item approval event + code guardrails re-checked at execution time. Halt beats everything.
- Send counting for caps uses status='sent' rows in the owner-TZ day — deferrals/cancels/fails don't consume budget.
- Jitter is applied at queue time; guardrail re-check at execution catches anything that changed during the wait (cap consumed by a parallel send, new halt, quiet hours reached).
- Reply detection only advances `sent→replied` — `conversation/won/lost` stay human decisions (§3.8).
