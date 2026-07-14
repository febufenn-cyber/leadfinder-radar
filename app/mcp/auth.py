"""M6 transport validation and fixed owner bearer-token verification."""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from mcp.server.auth.provider import AccessToken
from pydantic import AnyHttpUrl

from app.core.config import Settings

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_OWNER_SCOPE = "leadfinder:owner"


class StaticOwnerTokenVerifier:
    """Constant-time verifier for an explicitly configured owner bearer token.

    The accepted secret is never returned. AccessToken.token is deliberately a
    redacted sentinel because middleware needs identity/scopes, not the secret.
    """

    def __init__(self, expected_token: str) -> None:
        if not expected_token:
            raise ValueError("MCP_AUTH_TOKEN is required for network transport")
        self._expected = expected_token

    async def verify_token(self, token: str) -> AccessToken | None:
        if not hmac.compare_digest(token, self._expected):
            return None
        return AccessToken(
            token="[redacted]",
            client_id="leadfinder-owner",
            scopes=[_OWNER_SCOPE],
            subject="owner",
        )


@dataclass(frozen=True)
class MCPLaunchConfig:
    transport: str
    host: str
    port: int
    token_verifier: StaticOwnerTokenVerifier | None
    issuer_url: AnyHttpUrl | None
    resource_server_url: AnyHttpUrl | None


def resolve_launch_config(settings: Settings) -> MCPLaunchConfig:
    """Validate that network exposure is intentional and authenticated."""
    if settings.MCP_MAX_CALLS_PER_MINUTE < 1:
        raise ValueError("MCP_MAX_CALLS_PER_MINUTE must be positive")
    if settings.MCP_TOOL_TIMEOUT_SECONDS < 1:
        raise ValueError("MCP_TOOL_TIMEOUT_SECONDS must be positive")
    if not 1 <= settings.MCP_BIND_PORT <= 65535:
        raise ValueError("MCP_BIND_PORT must be between 1 and 65535")

    if settings.MCP_TRANSPORT == "stdio":
        return MCPLaunchConfig(
            transport="stdio",
            host=settings.MCP_BIND_HOST,
            port=settings.MCP_BIND_PORT,
            token_verifier=None,
            issuer_url=None,
            resource_server_url=None,
        )

    if not settings.MCP_AUTH_TOKEN:
        raise ValueError("MCP_AUTH_TOKEN is required for streamable-http")
    if settings.MCP_BIND_HOST not in _LOOPBACK and not settings.MCP_ALLOW_REMOTE:
        raise ValueError(
            "non-loopback MCP binding requires MCP_ALLOW_REMOTE=true; keep it behind TLS/reverse proxy"
        )

    public_host = "localhost" if settings.MCP_BIND_HOST in {"127.0.0.1", "::1"} else settings.MCP_BIND_HOST
    base = f"http://{public_host}:{settings.MCP_BIND_PORT}"
    return MCPLaunchConfig(
        transport="streamable-http",
        host=settings.MCP_BIND_HOST,
        port=settings.MCP_BIND_PORT,
        token_verifier=StaticOwnerTokenVerifier(settings.MCP_AUTH_TOKEN),
        issuer_url=AnyHttpUrl(base),
        resource_server_url=AnyHttpUrl(f"{base}/mcp"),
    )
