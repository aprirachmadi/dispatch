"""Enrich MCP SDK tool spans; never create a second observation for a tool call."""

import json
import logging
from typing import Any

from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from opentelemetry.trace import get_current_span
from pydantic import BaseModel

from dispatch.mcp_server.state import ServerState

logger = logging.getLogger(__name__)
TOOL_ARGUMENTS = {
    "search_tickets": {"query", "status", "category", "priority", "company_id", "limit", "offset"},
    "get_ticket": {"ticket_id"},
    "get_company_context": {"company_id"},
    "find_similar_tickets": {"ticket_id", "limit", "company_id"},
    "weekly_report": {"week_start", "company_id"},
    "update_ticket": {"ticket_id", "category", "priority"},
    "draft_reply": {"ticket_id", "body"},
    "add_internal_note": {"ticket_id", "body"},
}


def update_tool_span(span, **attributes):
    """Telemetry must not turn a successful tool operation into a failure."""
    try:
        span.set_attributes(attributes)
    except Exception:
        logger.warning("MCP trace enrichment failed.")


class ToolTraceMiddleware:
    async def __call__(
        self, ctx: ServerRequestContext[ServerState, Any], call_next: CallNext
    ) -> HandlerResult:
        if ctx.method != "tools/call":
            return await call_next(ctx)
        span = get_current_span()  # The SDK's outer OpenTelemetryMiddleware owns this span.
        params = ctx.params or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        allowed = TOOL_ARGUMENTS.get(name, set()) if isinstance(name, str) else set()
        inputs = (
            {
                key: value if type(value) in (str, int, float, bool, type(None)) else "invalid_type"
                for key, value in arguments.items()
                if key in allowed
            }
            if isinstance(arguments, dict)
            else {"invalid_arguments": True}
        )
        update_tool_span(
            span,
            **{
                "langfuse.observation.type": "tool",
                "langfuse.observation.input": json.dumps(inputs),
            },
        )
        try:
            result = await call_next(ctx)
        except BaseException as error:
            update_tool_span(
                span,
                **{
                    "langfuse.observation.level": "ERROR",
                    "langfuse.observation.status_message": type(error).__name__,
                    "langfuse.observation.output": json.dumps({"error": type(error).__name__}),
                },
            )
            raise
        if isinstance(result, BaseModel):
            payload = result.model_dump(mode="json", by_alias=True)
        else:
            payload = result
        failed = not isinstance(payload, dict) or bool(payload.get("isError", False))
        # Tool error content may contain provider bodies. Successful structured records are data.
        output = (
            {"isError": True, "error": "MCP tool failed"}
            if failed
            else payload.get("structuredContent", payload)
        )
        update_tool_span(
            span,
            **{
                "langfuse.observation.output": json.dumps(output),
                "langfuse.observation.level": "ERROR" if failed else "DEFAULT",
                "langfuse.observation.status_message": "MCP tool failed" if failed else "",
            },
        )
        return result
