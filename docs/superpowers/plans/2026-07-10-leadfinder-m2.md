# LeadFinder M2 Implementation Plan

> **Status: BUILT 2026-07-10, review in flight.** Tasks 1-9 executed and committed.
> Live-verified: real Sonnet draft (C+B variants, community rules respected, risk
> flags meaningful, ~190s/$0.73 nominal). Bot wiring awaits the owner's Telegram
> token for live verification. Task 10 (adversarial review) running.

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (inline, same session as author). Each task commits.

**Goal:** M2 per DESIGN §7 — Sonnet drafting (A/B/C variants), Telegram approval buttons, copy-mode send, leads state machine. "Start working leads for real here."

**Architecture:** After the threshold gate, surfaced posts become `leads` (state machine §3.8). Tier-standard drafting (§3.5) produces 2–3 variants into `drafts`. Approval cards are delivered via a transactional-outbox pattern (lead committed as `drafted` first, `approval_pushed_at` marked after the Telegram send succeeds; unpushed cards retry next cycle — this is the structural fix for the M1 review's duplicate-alert finding). A separate bot process (python-telegram-bot, long-polling, owner-chat-only) handles button callbacks: send = **copy-mode** (§3.7 — bot returns the text for copy + the thread deep link, marks `sent`, writes the approval event), edit (ForceReply; edited text stored as gold sample), skip, mute keyword/community (a `mutes` table consulted by the prefilter).

**Tech:** python-telegram-bot ≥21 (bot process only — the worker keeps raw Bot-API HTTP with `reply_markup`), ClaudeRunner tier `standard` (Sonnet), migration 003.

## Global Constraints (from DESIGN.md)

- **§0/§3.7: copy-mode ONLY. No code path posts to Reddit/Threads. The owner posts manually.** API-send with guardrails is M4 and is NOT built here.
- §3.5 hard rules in the draft prompt: persona block contains ONLY owner-written true facts (ships EMPTY → prompt forbids any self-claims); no fake urgency; banned openers ("Great question!", "I came across your post"); match the post's language; ≤120 words public comment / ≤80 words DM (enforced code-side → `over_length` risk flag); per-community `rules_note` respected (default: assume no self-promotion allowed).
- §3.5 variant contract: each `{channel, text, risk_flags[]}`; A = helpful-first comment (default), B = short DM only where invited, C = comment+DM combo for promo-banning subs.
- §3.6: card shows fit score, one-line summary, link, variants; buttons `[Send A] [Send B] [Edit] [Skip] [Mute keyword] [Mute community]`. Inline edits become gold samples.
- §3.8: statuses `surfaced → drafted → sent → replied → conversation → won|lost|no_response` (+ `skipped` for the Skip button — pragmatic addition, noted deviation). No auto follow-ups.
- DoD: **no send ever occurs without an approval event row** — every Send/Edit-send tap writes `Event(kind="approval", ...)` BEFORE the copy-mode text is returned.
- Bot only obeys `TELEGRAM_CHAT_ID`; all other chats ignored.
- Every LLM call still audited via llm_calls (drafting uses the same runner).

## Tasks

1. **Leads/drafts/mutes schema** — migration 003; `app/models/lead.py` (status machine: `ALLOWED_TRANSITIONS` + `transition(lead, to)` raising on illegal moves), `app/models/draft.py`, `app/models/mute.py`. Tests: legal/illegal transitions, unique raw_post_id.
2. **Pack persona + community rules** — `OfferPack.community_rules: dict[str,str]`, `packs/personas/robofox_web.yaml` (empty `facts: []`, marked owner-TODO), loader `load_persona(pack_name)`. Tests.
3. **Drafting service** — `app/draft.py`: `DraftVariant`/`DraftSet` models, `build_draft_prompts(pack, persona, post_row, score)`, `draft_lead(runner, session, pack, post_row, score, lead_id) -> list[DraftVariant] | None`, word-limit + banned-opener enforcement adds risk flags. Tier `standard`, purpose `draft`. Tests with fake runner.
4. **Mutes in prefilter** — pipeline loads active mutes per cycle; community-muted posts skipped, keyword mutes strip matched keywords (post dropped if none left). Tests.
5. **Pipeline: leads + outbox delivery** — surfaced post ⇒ `Lead(surfaced)` ⇒ drafts ⇒ `drafted` (commit) ⇒ push approval card (raw sendMessage + `reply_markup`) ⇒ `approval_pushed_at`. Draft failure ⇒ plain scored alert fallback + `draft_failed` event (lead stays `surfaced`). Unpushed `drafted` leads retry each cycle. Tests: happy path, draft-failure fallback, push-failure retry, no duplicate push.
6. **Approval actions** — `app/approval.py` pure functions: `approve(session, lead_id, variant) -> CopyPayload` (approval event row FIRST, then status `sent`), `save_edit(session, lead_id, text)` (gold sample + approve), `skip(session, lead_id)`, `mute(session, kind, value, pack)`. Tests cover the DoD ordering (event exists even if later steps fail).
7. **Bot adapter** — `app/bot.py` (PTB Application, CallbackQueryHandler + ForceReply edit flow, owner-chat auth gate), compose `bot` service in the app profile, README instructions. Thin layer over app/approval.py; logic covered by task-6 tests, bot wiring verified live once the owner supplies the token.
8. **Dashboard** — funnel counts by status + `/leads` table (status, score, variant chosen, links).
9. **Live verification** — real Sonnet draft on a synthetic high-score lead (llm_calls row, variants land in drafts), full suite, worker restart.
10. **Adversarial review** — Sonnet workflow, Fable advises, fixes committed.

## Self-Review Notes
- §3.5/§3.6/§3.7/§3.8 all covered (T3/T5/T6/T7/T1). Copy-mode enforced by absence: no reddit/threads write client exists anywhere in the codebase.
- Outbox delivery resolves M1 finding #8 structurally, as promised in that review.
- Reply detection, HubSpot sync, API-send, jitter/caps: M4. Weekly evals: M5. Explicitly out.
- Type chain: RawPost → Lead(raw_post_id) → Draft(lead_id) → approval CopyPayload; callback data `a:<action>:<lead_id>` ≤64 bytes.
