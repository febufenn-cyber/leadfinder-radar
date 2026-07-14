"""M6B authentication, transport, rate-limit, timeout, and audit tests."""

import asyncio

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from sqlalchemy import select

from app.core.config import Settings
from app.mcp.auth import StaticOwnerTokenVerifier, resolve_launch_config
from app.mcp.runtime import (
    RateLimitExceeded,
    SlidingWindowRateLimiter,
    ToolRuntime,
    redact_arguments,
)
from app.models.event import Event


def _settings(**overrides) -> Settings:
    values = {
        "MCP_TRANSPORT": "stdio",
        "MCP_BIND_HOST": "127.0.0.1",
        "MCP_BIND_PORT": 8101,
        "MCP_AUTH_TOKEN": "",
        "MCP_ALLOW_REMOTE": False,
        "MCP_MAX_CALLS_PER_MINUTE": 60,
        "MCP_TOOL_TIMEOUT_SECONDS": 30,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


async def test_static_owner_token_verifier_is_constant_surface():
    verifier = StaticOwnerTokenVerifier("super-secret-token")
    assert await verifier.verify_token("wrong") is None
    accepted = await verifier.verify_token("super-secret-token")
    assert accepted is not None
    assert accepted.token == "[redacted]"
    assert accepted.client_id == "leadfinder-owner"
    assert accepted.scopes == ["leadfinder:owner"]


def test_transport_is_local_by_default_and_http_requires_explicit_security():
    launch = resolve_launch_config(_settings())
    assert launch.transport == "stdio"
    assert launch.token_verifier is None

    with pytest.raises(ValueError, match="MCP_AUTH_TOKEN"):
        resolve_launch_config(_settings(MCP_TRANSPORT="streamable-http"))

    with pytest.raises(ValueError, match="MCP_ALLOW_REMOTE"):
        resolve_launch_config(
            _settings(
                MCP_TRANSPORT="streamable-http",
                MCP_AUTH_TOKEN="secret",
                MCP_BIND_HOST="0.0.0.0",
            )
        )

    network = resolve_launch_config(
        _settings(MCP_TRANSPORT="streamable-http", MCP_AUTH_TOKEN="secret")
    )
    assert network.transport == "streamable-http"
    assert str(network.resource_server_url).endswith(":8101/mcp")


async def test_sliding_window_rate_limiter():
    now = [100.0]
    limiter = SlidingWindowRateLimiter(2, window_seconds=60, clock=lambda: now[0])
    await limiter.acquire()
    await limiter.acquire()
    with pytest.raises(RateLimitExceeded):
        await limiter.acquire()
    now[0] = 161.0
    await limiter.acquire()


def test_redact_arguments_bounds_and_removes_secret_fields():
    safe = redact_arguments(
        {
            "lead_id": 4,
            "auth_token": "top-secret",
            "nested": {"password": "bad", "note": "x" * 400},
            "approval_code": "123456",
        }
    )
    assert safe["lead_id"] == 4
    assert safe["auth_token"] == "[redacted]"
    assert safe["nested"]["password"] == "[redacted]"
    assert safe["approval_code"] == "[redacted]"
    assert len(safe["nested"]["note"]) == 200


async def test_runtime_audits_success_and_redacts_arguments(db_factory):
    runtime = ToolRuntime(
        session_factory=db_factory,
        limiter=SlidingWindowRateLimiter(10),
        timeout_seconds=1,
    )
    result = await runtime.execute(
        "demo",
        {"lead_id": 8, "access_token": "never-store-me"},
        lambda: asyncio.sleep(0, result={"ok": True}),
    )
    assert result == {"ok": True}

    async with db_factory() as session:
        event = (await session.execute(select(Event))).scalars().one()
    assert event.kind == "mcp_tool_call"
    assert event.payload["success"] is True
    assert event.payload["arguments"]["access_token"] == "[redacted]"
    assert "never-store-me" not in str(event.payload)


async def test_runtime_sanitizes_failure_and_timeout(db_factory):
    runtime = ToolRuntime(
        session_factory=db_factory,
        limiter=SlidingWindowRateLimiter(10),
        timeout_seconds=0.01,
    )

    async def explode():
        raise RuntimeError("database password=should-not-reach-client")

    with pytest.raises(ToolError, match="failed safely") as failure:
        await runtime.execute("explode", {"secret": "hidden"}, explode)
    assert "password" not in str(failure.value)

    with pytest.raises(ToolError, match="timed out"):
        await runtime.execute("slow", {}, lambda: asyncio.sleep(0.05))

    async with db_factory() as session:
        events = (await session.execute(select(Event).order_by(Event.id))).scalars().all()
    assert [event.payload["error_category"] for event in events] == ["internal_error", "timeout"]
    assert events[0].payload["arguments"]["secret"] == "[redacted]"


async def test_runtime_rate_limit_is_audited(db_factory):
    runtime = ToolRuntime(
        session_factory=db_factory,
        limiter=SlidingWindowRateLimiter(1),
        timeout_seconds=1,
    )
    await runtime.execute("first", {}, lambda: asyncio.sleep(0, result=True))
    with pytest.raises(ToolError, match="rate limit"):
        await runtime.execute("second", {}, lambda: asyncio.sleep(0, result=True))

    async with db_factory() as session:
        events = (await session.execute(select(Event).order_by(Event.id))).scalars().all()
    assert len(events) == 2
    assert events[1].payload["error_category"] == "rate_limited"
