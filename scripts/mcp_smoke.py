#!/usr/bin/env python3
"""Deterministic MCP smoke client for CI and owner deployment checks."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.memory import create_connected_server_and_client_session

from app.mcp.server import create_server

_EXPECTED_TOOLS = {
    "health",
    "search_leads",
    "get_lead",
    "stats",
    "redraft",
    "mute",
    "request_approval_code",
    "approve",
}


async def _verify(session) -> dict:
    tools = await session.list_tools()
    names = {tool.name for tool in tools.tools}
    missing = sorted(_EXPECTED_TOOLS - names)
    extra = sorted(names - _EXPECTED_TOOLS)
    if missing:
        raise RuntimeError(f"missing MCP tools: {', '.join(missing)}")

    health = await session.call_tool("health", {})
    if health.isError:
        raise RuntimeError("health tool returned an MCP error")
    payload = health.structuredContent or {}
    if payload.get("status") != "ok" or payload.get("database") is not True:
        raise RuntimeError(f"unexpected health result: {payload!r}")
    return {
        "status": "ok",
        "tools": sorted(names),
        "extra_tools": extra,
        "database": True,
    }


async def _run_in_memory() -> dict:
    server = create_server()
    async with create_connected_server_and_client_session(
        server,
        raise_exceptions=True,
    ) as session:
        return await _verify(session)


async def _run_stdio() -> dict:
    environment = dict(os.environ)
    environment["MCP_TRANSPORT"] = "stdio"
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp.server"],
        env=environment,
    )
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            return await _verify(session)


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--in-memory",
        action="store_true",
        help="Use the SDK's in-memory transport instead of spawning the stdio server.",
    )
    args = parser.parse_args()
    result = await (_run_in_memory() if args.in_memory else _run_stdio())
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(_main())
