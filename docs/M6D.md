# M6D — Telegram-gated single-use approvals

M6D adds a two-step approval flow while preserving the existing human send gate:

1. `request_approval_code(lead_id, variant)` validates the exact active draft and sends a six-digit value only to the configured Telegram owner chat.
2. `approve(lead_id, variant, verification_value)` validates that value, consumes the challenge, and calls the existing copy-mode `approve()` or API-mode `queue_send()` service.

## Security properties

- The plaintext value is never returned by MCP.
- Only an HMAC-SHA256 value and random salt are stored.
- Each challenge is bound to lead ID, draft ID, variant, and the exact draft-text SHA-256.
- A draft edit or redraft invalidates the challenge.
- Challenges expire, have bounded failed attempts, and are single-use.
- A new challenge consumes any earlier active challenge for the same lead and variant.
- Concurrent replay attempts can produce at most one approval.
- Failed Telegram delivery rolls back challenge creation.
- Tool-call audits redact the submitted verification value.

Migration `009` creates `mcp_approval_challenges` and a partial unique index allowing only one unused challenge per lead and variant.

## Required environment

```text
MCP_APPROVAL_SECRET=<at least 32 random characters>
MCP_APPROVAL_CODE_TTL_SECONDS=300
MCP_APPROVAL_MAX_ATTEMPTS=5
```

The Telegram bot token and owner chat ID must already be configured. The approval secret must be separate from Telegram and platform credentials.
