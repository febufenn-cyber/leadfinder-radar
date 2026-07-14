# M6A — read-only MCP foundation

M6A adds the stable Python MCP SDK (`mcp>=1.28,<2`) and a local stdio server with four bounded, typed tools:

- `health`
- `search_leads`
- `get_lead`
- `stats`

All tools are marked read-only and non-destructive. They expose stable Pydantic output objects, omit raw platform payloads, cap text and result sizes, and use cursor pagination. M6A contains no approval, send, arbitrary SQL, shell, or filesystem-write tool.

The PostgreSQL-backed tests call the services directly and through the SDK's in-memory MCP transport. They also assert that read operations create no `events` or `sends` rows.
