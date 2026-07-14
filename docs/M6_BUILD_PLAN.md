# LeadFinder M6 Build Contract and Autonomous Implementation Plan

**Repository:** `febufenn-cyber/leadfinder-radar`  
**Planning baseline:** `main` at `47fe2c39cdec2c0fcafda0f4ed20dc0df10d6f66`  
**Roadmap status:** M0-M5 completed; **one official phase remains: M6**  
**Build trigger:** the owner sends exactly **`build`**

## 1. Purpose

This is the implementation contract for the final phase in the current LeadFinder roadmap. It must be re-read from current `main` and verified against current code before implementation begins. After the owner says `build`, M6 must be completed autonomously in one continuous execution except for a genuine external blocker such as revoked GitHub access or an unavailable required credential.

M6 is delivered as small sequential slices. Every slice is committed, pushed to a feature branch, opened as a pull request, validated, merged to `main`, and verified on `main` before the next slice begins.

This plan does not authorize unattended posting, batch approval, automatic prompt changes, arbitrary database access, or weakening any M0-M5 guardrail.

## 2. How many phases remain

The authoritative roadmap in `DESIGN.md` lists M0 through M6. M0-M5 are implemented.

- **Official phases remaining: 1**
- **Remaining phase: M6 - secure MCP control server**
- M6 is delivered through **five merge units (M6A-M6E)**. These are implementation slices, not additional product phases.

Productization, multi-tenant SaaS, WhatsApp UI, and new source adapters are outside this build and require a later roadmap decision.

## 3. M6 objective

Add a secure Model Context Protocol control surface so the owner can inspect and operate LeadFinder conversationally from an MCP-compatible client without bypassing existing services, state machines, approval records, send guardrails, or audit history.

Target tools:

- `search_leads(filters)`
- `get_lead(id)`
- `stats(period, pack)`
- `redraft(id, guidance)`
- `mute(kind, value, pack)`
- `request_approval_code(id, variant)`
- `approve(id, variant, code)`

The final `approve` operation must reuse the existing approval and send code paths. It must never directly construct a send, directly post to a platform, or bypass guardrails.

## 4. Non-negotiable invariants

1. Human send gate remains structural: every send originates from one explicit owner action and an approval event.
2. No batch approval, approve-all, autonomous queue draining, scheduled approval, or automatic outreach.
3. MCP approval requires a short-lived one-time code delivered only to the configured Telegram owner chat.
4. MCP tools reuse existing application services instead of duplicating approval, send, drafting, mute, or metrics rules.
5. Remote transport is disabled by default; local/stdio operation ships first.
6. No arbitrary SQL, shell, filesystem-write, or direct platform-post tool is exposed.
7. Every mutation and failed mutation is audited with redacted arguments and duration.
8. Secrets never appear in tool results, exceptions, events, logs, tests, or committed configuration.
9. Prompt-tuning proposals remain proposals; MCP cannot modify prompts, personas, packs, or few-shots.
10. M5 evaluation and CI remain intact.

## 5. Pre-build verification gate

Immediately after the owner says `build`, complete this gate before creating a branch:

- Read this file from current `main`, not from memory.
- Confirm the repository, default branch, current `main` SHA, and open-PR state.
- Read current `DESIGN.md`, `README.md`, `docs/M5.md`, `docs/FEATURES.md`, and relevant implementation modules.
- Search for existing or partially implemented MCP work.
- Verify the current official Python MCP SDK, supported transports, annotations, and authentication recommendations from primary documentation.
- Run or inspect baseline CI. Fix a red `main` in a separate PR before M6.
- Verify the current migration head dynamically; do not assume revision `007` if the repository changed.
- Reconfirm Telegram owner authorization, approval-event ordering, queue-send safety, jitter, caps, quiet hours, cooldowns, and halts.
- Reconcile minor file-path changes without asking. Stop and report a material invariant conflict.

## 6. Implementation slices

### M6A - MCP foundation and read-only tools

**Goal:** establish a typed MCP server with useful read access before mutations.

Deliverables:

- Add the official Python MCP SDK selected during build-time verification.
- Add a clear MCP module structure and shared typed schemas.
- Implement `health`, `search_leads`, `get_lead`, and `stats`.
- Bound pagination, list sizes, and returned text.
- Return stable structured objects, not raw ORM objects.
- Exclude secrets and unnecessary raw platform payloads.
- Add read-only annotations where supported.

Tests:

- Empty/populated database behavior
- Filters, cursor pagination, invalid limits, and missing leads
- Serialization and redaction
- Proof that read tools perform no mutation

Merge gate: Ruff, migration verification, and the full PostgreSQL test suite pass. Commit, push, PR, CI inspection, merge, and `main` SHA confirmation are required before M6B.

### M6B - Authentication, transport, rate limits, and audit

**Goal:** make the server safe to run continuously for owner-only clients.

Deliverables:

- Ship stdio/local transport as default.
- Keep network transport disabled unless explicit settings are supplied.
- Require token authentication for any network transport.
- Add bounded timeouts and per-process rate limiting.
- Audit every tool call with tool name, success/failure, duration, redacted arguments, and error category.
- Use sanitized structured errors; never expose stack traces or credentials.

Expected settings include `MCP_TRANSPORT`, `MCP_BIND_HOST`, `MCP_BIND_PORT`, `MCP_AUTH_TOKEN`, and `MCP_MAX_CALLS_PER_MINUTE`.

Tests:

- Missing/invalid authentication
- Network mode disabled by default
- Rate-limit and timeout behavior
- Audit success/failure events
- Secret redaction

Merge gate: same sequential commit, push, PR, CI, merge, and `main` verification process.

### M6C - Safe workflow mutations: redraft and mute

**Goal:** allow useful conversational operation while preserving reviewability and reversibility.

Deliverables:

- `redraft(lead_id, guidance)` accepts bounded owner guidance, treats it as untrusted data, uses existing persona/community rules, preserves old drafts, records provenance, and never approves or sends.
- `mute(kind, value, pack)` supports only keyword/community mutes, reuses existing normalization and deduplication, and returns whether a new mute was created.
- Audit both operations.
- Do not expose arbitrary lead-state mutation.

Tests:

- Redraft success, invalid state, missing lead, LLM failure, injection text, and preservation of old drafts
- Mute validation, normalization, idempotency, and pack scoping
- Proof that neither tool creates a send or approval event

Merge gate: M6D begins only after M6C is confirmed on `main`.

### M6D - Telegram-gated approval and replay protection

**Goal:** support conversational approval without allowing a model or MCP client to become the human approver.

Approval is two-step:

1. `request_approval_code(lead_id, variant)` validates the target, creates a short-lived one-time challenge, and sends the code only to the configured owner Telegram chat.
2. `approve(lead_id, variant, code)` verifies the challenge and calls the existing `approve()` or `queue_send()` path according to `SEND_MODE`.

The code must never be returned by the MCP tool that creates it.

Deliverables:

- Add the next Alembic migration for approval challenges with lead/draft binding, hashed code, draft digest, expiry, attempts, and used timestamp.
- Store only a cryptographic code hash.
- Use a short expiry, bounded attempts, one active challenge per lead/variant, and single use.
- Bind the challenge to the exact draft text digest so editing invalidates an old code.
- Preserve approval-event ordering, copy/API modes, jitter, cancellation, quiet hours, caps, cooldowns, and halts by reusing existing services.
- No batch endpoint.

Tests:

- Valid code in copy and API modes
- Wrong, expired, reused, and over-attempt codes
- No plaintext code in DB, events, logs, or tool output
- Draft changed after challenge
- Lead already approved/skipped
- Exactly one approval event and no duplicate send
- Concurrent/replayed attempts
- Clean-schema and previous-head migration upgrades

### M6E - Deployment, client configuration, smoke tests, and documentation

**Goal:** make M6 operable on the existing VPS and straightforward to connect from owner clients.

Deliverables:

- Add a production entry point and systemd service if required.
- Keep the default owner-only route local/stdio; do not expose a public unauthenticated endpoint.
- Add secret-free client configuration examples and `.env.example` entries.
- Add a deterministic MCP smoke script and SDK test client.
- Add `docs/M6.md` covering architecture, tool contracts, security, approval codes, client setup, deployment, rollback, and troubleshooting.
- Update `README.md`, `DESIGN.md`, and `docs/FEATURES.md` to mark M6 implemented.
- Extend CI for MCP tests and migration verification.

Validation:

- Start/stop lifecycle and database-session cleanup
- Call every read tool through a test client
- Execute redraft and mute against test data
- Complete approval-code flow with a fake notifier
- Malformed and unauthorized calls fail safely

Final merge gate:

- Final PR merged to `main`
- No open M6 PRs
- Compare final `main` against the pre-build SHA
- Confirm CI and inspect failure logs if any
- Produce deployment commands; do not claim deployment unless deployment access was actually used

## 7. Autonomous Git workflow after `build`

For each M6 slice:

1. Confirm current `main` SHA and previous-slice merge.
2. Create `agent/m6-<slice>` from current `main`.
3. Implement only that slice plus required tests/docs.
4. Run static checks, Alembic upgrade verification, and the full PostgreSQL test suite.
5. Create an intentional scoped implementation commit.
6. Push the branch.
7. Open a PR to `main` with scope, safety analysis, migrations, and test evidence.
8. Inspect CI. Fix failures on the same branch, commit, push, and re-check.
9. Merge only when mergeable and green. If GitHub Actions is externally unavailable, run equivalent verification and explicitly disclose the missing remote status.
10. Confirm the merge commit on `main` and verify the feature branch is not ahead.
11. Record PR number, implementation SHA, merge SHA, tests, and migration.
12. Begin the next slice from updated `main`.

No direct force-push to `main`. No unreviewable mega-commit. No claiming a merge or test passed without querying GitHub or running the test.

## 8. Final confirmation format

At completion, report:

| Slice | PR | Implementation commit | Merge commit | CI/tests | Migration |
|---|---:|---|---|---|---|
| M6A | | | | | |
| M6B | | | | | |
| M6C | | | | | |
| M6D | | | | | |
| M6E | | | | | |

Also confirm:

- final `main` SHA
- no open M6 PRs
- exact MCP tools shipped
- send-gate invariants retained
- tests and CI status
- migration head
- deployment status: deployed, not deployed, or blocked
- any remaining 48-hour soak requirement

## 9. Definition of done

- All planned tools exist with bounded typed schemas.
- Read tools do not mutate data.
- Redraft and mute reuse existing rules and are audited.
- Approval requires a Telegram-delivered one-time code and reuses the existing approval/send path.
- No batch or unattended send path exists.
- No secret appears in outputs or audit data.
- Migration chain validates from a clean database and previous head.
- Full PostgreSQL test suite and lint pass.
- Every slice is committed, pushed, merged, and confirmed on `main`.
- Documentation and deployment artifacts are complete.

The roadmap's 48-hour unattended operational requirement cannot honestly be proven in one interactive build session. The final report must mark the soak as pending unless production telemetry already demonstrates it. Code completion and merge completion may be reported separately.

## 10. Rollback

- Each slice is independently revertible by reverting its merge commit.
- Network transport remains disabled by default, so M0-M5 continue if MCP is disabled.
- The approval-challenge migration affects only MCP challenges; existing approvals and sends remain untouched.
- If MCP mutations behave unexpectedly, disable the MCP service first; worker, bot, dashboard, send, and watch remain independent.

## 11. Authorization boundary

The command `build` authorizes M6 exactly as constrained here. It does not authorize:

- new social-network adapters
- automatic outreach or follow-ups
- batch approvals
- changes to platform caps or quiet hours
- automatic application of prompt-tuning proposals
- public unauthenticated MCP hosting
- multi-user or multi-tenant productization
- production deployment without an available deployment capability or credential

Material deviations must be reported rather than silently invented.
