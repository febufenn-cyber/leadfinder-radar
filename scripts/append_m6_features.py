#!/usr/bin/env python3
"""Append the M6 section to docs/FEATURES.md exactly once."""

from __future__ import annotations

from pathlib import Path

MARKER = "## 9. M6 — secure MCP control server"
SECTION = r'''

---

## 9. M6 — secure MCP control server

M6 adds an owner-only Model Context Protocol surface without bypassing the human send gate.

### Runtime and tools

- Stable Python MCP SDK v1 line, with stdio as the default transport.
- Typed tools: `health`, `search_leads`, `get_lead`, `stats`, `redraft`, `mute`, `request_approval_code`, and `approve`.
- Bounded pagination/text/history; no raw platform payloads.
- Explicit authenticated streamable HTTP, loopback by default, with a separate remote-binding opt-in.
- Per-process rate limit, tool timeout, sanitized errors, and redacted `mcp_tool_call` events.

### Safe mutations

- `redraft` accepts bounded untrusted guidance, archives previous active drafts in `draft_revisions`, rechecks concurrent state, and never approves or sends.
- `mute` accepts only keyword/community values and reuses existing normalization and deduplication.

### Telegram-gated approval

- `request_approval_code` delivers a short-lived six-digit value only to the configured owner Telegram chat.
- Only an HMAC and random salt are stored; the challenge binds lead, draft, variant, and exact draft-text digest.
- Expiry, bounded attempts, replacement, single use, changed-draft invalidation, and concurrent replay protection are enforced.
- `approve` reuses the existing copy-mode or guarded API-send service, so approval-event ordering, jitter, cancellation, caps, cooldowns, quiet hours, and halts remain unchanged.
- No batch approval, direct platform-post, arbitrary SQL, shell, filesystem-write, or prompt-edit tool exists.

### Operations

- Migration `008`: `draft_revisions`.
- Migration `009`: `mcp_approval_challenges`.
- `scripts/mcp_smoke.py` verifies exact tool inventory and database health through in-memory or stdio transport.
- `deploy/leadfinder-mcp.service` is an optional loopback HTTP unit; stdio requires no daemon.
- Full setup, deployment, rollback, and troubleshooting are documented in `docs/M6.md`.
'''


def append_m6(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return False
    path.write_text(text.rstrip() + SECTION + "\n", encoding="utf-8")
    return True


def main() -> None:
    append_m6(Path("docs/FEATURES.md"))


if __name__ == "__main__":
    main()
