# M6B — secure MCP runtime

M6B wraps every MCP tool call in a shared runtime with:

- a bounded per-process sliding-window rate limit
- a bounded execution timeout
- one append-only `mcp_tool_call` event for success and failure
- recursively redacted and length-bounded arguments
- sanitized client errors with no stack traces or credential text

## Transport policy

`MCP_TRANSPORT=stdio` remains the default and does not require a bearer token. To enable local HTTP explicitly:

```text
MCP_TRANSPORT=streamable-http
MCP_BIND_HOST=127.0.0.1
MCP_BIND_PORT=8101
MCP_AUTH_TOKEN=<long random secret>
```

Binding to a non-loopback host additionally requires `MCP_ALLOW_REMOTE=true`. That switch does not provide TLS; a remote deployment must still sit behind a TLS reverse proxy and network access controls.

Other runtime controls:

```text
MCP_MAX_CALLS_PER_MINUTE=60
MCP_TOOL_TIMEOUT_SECONDS=30
```

The static verifier compares tokens in constant time and returns a redacted authentication object. The configured token is never written to an event or tool result.
