"""Shared CLI/backend composition: trusted configuration owns all subprocess settings."""

import os
import sys
from collections.abc import Callable
from dataclasses import replace

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from dispatch.agent.config import ModelSettings
from dispatch.agent.llm import OpenAICompatibleClient
from dispatch.agent.models import TriageResult
from dispatch.agent.workflow import MCPGateway, WorkflowError, run_batch
from dispatch.mcp_server.config import MCPSettings


async def run_connected(
    model_settings: ModelSettings,
    mcp_settings: MCPSettings,
    *,
    apply: bool = False,
    limit: int = 5,
    ticket_id: int | None = None,
    progress: Callable[[TriageResult], None] | None = None,
) -> list[TriageResult]:
    """Own an MCP subprocess and model connection; preview always forces read-only tools."""
    if apply and mcp_settings.read_only:
        raise WorkflowError("Apply requires explicitly enabled writes.")
    config = replace(mcp_settings, read_only=not apply)
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "dispatch.mcp_server.server"],
        env={
            **os.environ,
            "DISPATCH_READ_ONLY": "true" if config.read_only else "false",
            "DISPATCH_PSA_BASE_URL": config.psa_base_url,
            "DISPATCH_AUDIT_PATH": str(config.audit_path),
            "DISPATCH_EMBEDDING_CACHE_PATH": str(config.embedding_cache_path),
            "DISPATCH_MODEL_CACHE_PATH": str(config.model_cache_path),
        },
    )
    async with (
        OpenAICompatibleClient(model_settings) as model,
        Client(parameters, read_timeout_seconds=180) as client,
    ):
        return await run_batch(
            MCPGateway(client, writes_allowed=apply),
            model,
            limit=limit,
            ticket_id=ticket_id,
            apply=apply,
            progress=progress,
        )
