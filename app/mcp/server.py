"""Secure typed LeadFinder MCP server (M6A-M6C)."""

from __future__ import annotations

from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from app.core.config import Settings, get_settings
from app.mcp.auth import MCPLaunchConfig, resolve_launch_config
from app.mcp.mutations import LeadMutationService
from app.mcp.runtime import ToolRuntime
from app.mcp.schemas import (
    HealthResult,
    LeadDetail,
    LeadSearchResult,
    MuteResult,
    RedraftResult,
    StatsResult,
)
from app.mcp.service import LeadReadService

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_MUTATING = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)


def _fastmcp_kwargs(launch: MCPLaunchConfig) -> dict:
    kwargs = {
        "host": launch.host,
        "port": launch.port,
        "json_response": True,
        "stateless_http": True,
    }
    if launch.transport == "streamable-http":
        kwargs["token_verifier"] = launch.token_verifier
        kwargs["auth"] = AuthSettings(
            issuer_url=launch.issuer_url,
            required_scopes=["leadfinder:owner"],
            resource_server_url=launch.resource_server_url,
        )
    return kwargs


def create_server(
    service: LeadReadService | None = None,
    *,
    mutation_service: LeadMutationService | None = None,
    settings: Settings | None = None,
    runtime: ToolRuntime | None = None,
) -> FastMCP:
    settings = settings or get_settings()
    launch = resolve_launch_config(settings)
    service = service or LeadReadService()
    session_factory = service.session_factory
    mutation_service = mutation_service or LeadMutationService(session_factory=session_factory)
    runtime = runtime or ToolRuntime(
        session_factory=session_factory,
        max_calls_per_minute=settings.MCP_MAX_CALLS_PER_MINUTE,
        timeout_seconds=settings.MCP_TOOL_TIMEOUT_SECONDS,
    )
    mcp = FastMCP(
        "LeadFinder",
        instructions=(
            "Owner-only LeadFinder controls. Every call is bounded and audited. Redraft and mute "
            "never approve a reply or send to a platform."
        ),
        **_fastmcp_kwargs(launch),
    )

    @mcp.tool(annotations=_READ_ONLY)
    async def health() -> HealthResult:
        """Check the MCP process and database connection."""

        async def operation() -> HealthResult:
            try:
                return await service.health()
            except Exception as exc:
                raise ToolError("LeadFinder database is unavailable") from exc

        return await runtime.execute("health", {}, operation)

    @mcp.tool(annotations=_READ_ONLY)
    async def search_leads(
        status: str | None = None,
        pack: str | None = None,
        source: str | None = None,
        min_fit_score: int | None = None,
        cursor: int | None = None,
        limit: int = 20,
    ) -> LeadSearchResult:
        """Search leads with bounded newest-first cursor pagination."""
        arguments = {
            "status": status,
            "pack": pack,
            "source": source,
            "min_fit_score": min_fit_score,
            "cursor": cursor,
            "limit": limit,
        }
        return await runtime.execute(
            "search_leads", arguments, lambda: service.search_leads(**arguments)
        )

    @mcp.tool(annotations=_READ_ONLY)
    async def get_lead(lead_id: int) -> LeadDetail:
        """Get one lead with bounded post text, drafts, and send history."""

        async def operation() -> LeadDetail:
            result = await service.get_lead(lead_id)
            if result is None:
                raise ToolError(f"lead #{lead_id} not found")
            return result

        return await runtime.execute("get_lead", {"lead_id": lead_id}, operation)

    @mcp.tool(annotations=_READ_ONLY)
    async def stats(period_days: int = 30, pack: str | None = None) -> StatsResult:
        """Return lead outcome and LLM-cost statistics for a bounded period."""
        arguments = {"period_days": period_days, "pack": pack}
        return await runtime.execute("stats", arguments, lambda: service.stats(**arguments))

    @mcp.tool(annotations=_MUTATING)
    async def redraft(lead_id: int, guidance: str) -> RedraftResult:
        """Replace active draft variants while archiving every previous draft snapshot."""
        arguments = {"lead_id": lead_id, "guidance": guidance}
        return await runtime.execute(
            "redraft", arguments, lambda: mutation_service.redraft(**arguments)
        )

    @mcp.tool(annotations=_MUTATING)
    async def mute(kind: str, value: str, pack: str | None = None) -> MuteResult:
        """Add an idempotent keyword or community mute using existing normalization."""
        arguments = {"kind": kind, "value": value, "pack": pack}
        return await runtime.execute("mute", arguments, lambda: mutation_service.mute(**arguments))

    return mcp


mcp = create_server()


def main() -> None:
    settings = get_settings()
    launch = resolve_launch_config(settings)
    create_server(settings=settings).run(transport=launch.transport)


if __name__ == "__main__":
    main()
