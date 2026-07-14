"""Typed LeadFinder MCP server. M6A exposes read-only tools over stdio."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from app.mcp.schemas import HealthResult, LeadDetail, LeadSearchResult, StatsResult
from app.mcp.service import LeadReadService

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def create_server(service: LeadReadService | None = None) -> FastMCP:
    service = service or LeadReadService()
    mcp = FastMCP(
        "LeadFinder",
        instructions=(
            "Owner-only lead inspection tools. Read tools never mutate LeadFinder, approve a reply, "
            "or send to a platform."
        ),
        json_response=True,
    )

    @mcp.tool(annotations=_READ_ONLY)
    async def health() -> HealthResult:
        """Check the MCP process and database connection."""
        try:
            return await service.health()
        except Exception as exc:
            raise ToolError("LeadFinder database is unavailable") from exc

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
        try:
            return await service.search_leads(
                status=status,
                pack=pack,
                source=source,
                min_fit_score=min_fit_score,
                cursor=cursor,
                limit=limit,
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(annotations=_READ_ONLY)
    async def get_lead(lead_id: int) -> LeadDetail:
        """Get one lead with bounded post text, drafts, and send history."""
        try:
            result = await service.get_lead(lead_id)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        if result is None:
            raise ToolError(f"lead #{lead_id} not found")
        return result

    @mcp.tool(annotations=_READ_ONLY)
    async def stats(period_days: int = 30, pack: str | None = None) -> StatsResult:
        """Return lead outcome and LLM-cost statistics for a bounded period."""
        try:
            return await service.stats(period_days=period_days, pack=pack)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    return mcp


mcp = create_server()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
