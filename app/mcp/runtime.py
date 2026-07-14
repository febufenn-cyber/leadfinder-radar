"""M6 tool runtime: bounded execution, rate limiting, and redacted audit evidence."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypeVar

from mcp.server.fastmcp.exceptions import ToolError

from app.db.session import get_session_factory
from app.models.event import Event

log = logging.getLogger(__name__)
T = TypeVar("T")
_SECRET_MARKERS = ("token", "secret", "password", "authorization", "auth", "code")
_MAX_AUDIT_STRING = 200


class RateLimitExceeded(Exception):
    pass


class SlidingWindowRateLimiter:
    """Small per-process limiter; network deployments still need proxy-level limits."""

    def __init__(self, max_calls: int, *, window_seconds: float = 60.0, clock=None) -> None:
        if max_calls < 1:
            raise ValueError("max_calls must be positive")
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self.clock = clock or time.monotonic
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = self.clock()
            cutoff = now - self.window_seconds
            while self._calls and self._calls[0] <= cutoff:
                self._calls.popleft()
            if len(self._calls) >= self.max_calls:
                raise RateLimitExceeded
            self._calls.append(now)


def redact_arguments(value: Any, *, key: str = "") -> Any:
    """Return JSON-safe bounded audit data with all secret-like fields removed."""
    lowered = key.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        return "[redacted]"
    if isinstance(value, Mapping):
        return {str(k)[:80]: redact_arguments(v, key=str(k)) for k, v in list(value.items())[:30]}
    if isinstance(value, (list, tuple, set)):
        return [redact_arguments(item) for item in list(value)[:30]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value)
    return text if len(text) <= _MAX_AUDIT_STRING else text[: _MAX_AUDIT_STRING - 3] + "..."


class ToolRuntime:
    def __init__(
        self,
        *,
        session_factory=None,
        max_calls_per_minute: int = 60,
        timeout_seconds: float = 30,
        limiter: SlidingWindowRateLimiter | None = None,
    ) -> None:
        self.session_factory = session_factory or get_session_factory()
        self.timeout_seconds = timeout_seconds
        self.limiter = limiter or SlidingWindowRateLimiter(max_calls_per_minute)

    async def _audit(
        self,
        *,
        tool: str,
        arguments: dict[str, Any],
        success: bool,
        duration_ms: int,
        error_category: str | None,
    ) -> None:
        try:
            async with self.session_factory() as session:
                session.add(
                    Event(
                        kind="mcp_tool_call",
                        payload={
                            "tool": tool,
                            "success": success,
                            "duration_ms": duration_ms,
                            "arguments": redact_arguments(arguments),
                            "error_category": error_category,
                        },
                    )
                )
                await session.commit()
        except Exception:
            # An audit outage must be visible in service logs but must not leak
            # database details to the MCP client.
            log.exception("failed to persist MCP audit event for tool=%s", tool)

    async def execute(
        self,
        tool: str,
        arguments: dict[str, Any],
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        started = time.monotonic()
        success = False
        category: str | None = None
        try:
            try:
                await self.limiter.acquire()
            except RateLimitExceeded as exc:
                category = "rate_limited"
                raise ToolError("MCP call rate limit exceeded; retry shortly") from exc

            try:
                async with asyncio.timeout(self.timeout_seconds):
                    result = await operation()
            except TimeoutError as exc:
                category = "timeout"
                raise ToolError("LeadFinder tool timed out") from exc
            except ToolError:
                category = category or "tool_error"
                raise
            except ValueError as exc:
                category = "invalid_request"
                raise ToolError(str(exc)) from exc
            except Exception as exc:
                category = "internal_error"
                log.exception("MCP tool failed tool=%s", tool)
                raise ToolError("LeadFinder tool failed safely; inspect server logs") from exc

            success = True
            return result
        finally:
            await self._audit(
                tool=tool,
                arguments=arguments,
                success=success,
                duration_ms=int((time.monotonic() - started) * 1000),
                error_category=None if success else (category or "unknown"),
            )
