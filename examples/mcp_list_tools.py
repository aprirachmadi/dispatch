"""Discover Dispatch's MCP tools without an AI service or a running REST backend.

Run from the repository root: poetry run python examples/mcp_list_tools.py
This script starts the MCP child process and stops it automatically on exit.
"""

import asyncio
import os
import sys
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters


async def main() -> None:
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "dispatch.mcp_server.server"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "DISPATCH_READ_ONLY": "true"},
    )
    async with Client(server) as client:
        result = await client.list_tools()
        print(f"\nAvailable tools: {len(result.tools)}\n")
        for tool in result.tools:
            print(f"- {tool.name}")


if __name__ == "__main__":
    asyncio.run(main())
