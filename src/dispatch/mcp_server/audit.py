"""Durable, redacted JSONL audit events around every dispatched MCP tool attempt."""

import json
import os
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import anyio
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.shared.exceptions import MCPError

from dispatch.domain import TicketCategory, TicketPriority, TicketStatus
from dispatch.mcp_server.state import ServerState

READ_TOOLS = frozenset({"search_tickets", "get_ticket", "get_company_context"})
WRITE_TOOLS = frozenset({"draft_reply", "add_internal_note", "update_ticket"})
KNOWN_TOOLS = READ_TOOLS | WRITE_TOOLS


def summarize_arguments(arguments: Any) -> dict[str, Any]:
    """Allowlist metadata only: never persist arbitrary keys, free text, or error bodies."""
    if not isinstance(arguments, Mapping):
        return {"invalid_arguments": True}
    summary: dict[str, Any] = {"argument_count": min(len(arguments), 1000)}
    for name in ("ticket_id", "company_id", "limit", "offset"):
        if name in arguments:
            value = arguments[name]
            summary[name] = value if type(value) is int and 0 <= value < 2**63 else "invalid"
    for name, values in (
        ("category", set(TicketCategory)),
        ("priority", set(TicketPriority)),
        ("status", set(TicketStatus)),
    ):
        if name in arguments:
            value = arguments[name]
            summary[name] = value if isinstance(value, str) and value in values else "invalid"
    for name in ("query", "body"):
        if name in arguments:
            value = arguments[name]
            summary[f"{name}_length"] = (
                min(len(value), 100_000) if isinstance(value, str) else "invalid"
            )
    return summary


class AuditLog:
    """Append one bounded JSON object per write, flush to disk, and never truncate a file.

    A process-local lock serializes this sink's writes; O_APPEND prevents overwriting
    old records. This is a local demo log, not a tamper-proof/multi-host audit service.
    """

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def append(self, event: dict[str, Any]) -> None:
        payload = (json.dumps(event, ensure_ascii=True, separators=(",", ":")) + "\n").encode()
        if len(payload) > 4096:
            raise OSError("Audit metadata exceeded the event limit")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                if os.write(descriptor, payload) != len(payload):
                    raise OSError("Incomplete audit append")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)


def blocked_result(message: str) -> dict[str, Any]:
    """Complete tool-error envelope usable by modern and legacy SDK transports."""
    return {
        "resultType": "complete",
        "isError": True,
        "content": [{"type": "text", "text": message}],
    }


class AuditMiddleware:
    """Audit before SDK argument validation; deny writes when the trusted policy says so."""

    async def __call__(
        self, ctx: ServerRequestContext[ServerState, Any], call_next: CallNext
    ) -> HandlerResult:
        if ctx.method != "tools/call":
            return await call_next(ctx)
        params = ctx.params or {}
        name = params.get("name")
        tool = name if isinstance(name, str) and name in KNOWN_TOOLS else "unknown_tool"
        config = ctx.lifespan_context.settings
        sink = ctx.lifespan_context.audit
        common = {"call_id": str(uuid4()), "tool": tool, "read_only": config.read_only}
        start = {
            **common,
            "event": "started",
            "timestamp": datetime.now(UTC).isoformat(),
            "arguments": summarize_arguments(params.get("arguments", {})),
        }
        try:
            # Once decoded/accepted at this boundary, cancellation must not skip
            # the durable start record while the worker is queued or writing.
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(sink.append, start)
        except OSError:
            return blocked_result("Audit unavailable; this tool call was not executed.")
        started = time.monotonic()
        outcome = "error"
        try:
            if tool in WRITE_TOOLS and config.read_only:
                outcome = "blocked"
                return blocked_result("Tool writes are disabled in read-only mode.")
            result = await call_next(ctx)
            error = (
                result.get("isError", False)
                if isinstance(result, dict)
                else getattr(result, "is_error", False)
            )
            outcome = "error" if error else "success"
            return result
        except BaseException as error:
            if isinstance(error, anyio.get_cancelled_exc_class()):
                outcome = "cancelled"
            raise
        finally:
            finish = {
                **common,
                "event": "finished",
                "timestamp": datetime.now(UTC).isoformat(),
                "outcome": outcome,
                "duration_ms": round((time.monotonic() - started) * 1000),
            }
            with anyio.CancelScope(shield=True):
                try:
                    await anyio.to_thread.run_sync(sink.append, finish)
                except OSError:
                    # Started was persisted; never claim rollback of a possibly committed write.
                    raise MCPError(
                        code=-32603,
                        message="Audit completion failed; the call may have executed. "
                        "Do not automatically retry writes.",
                    ) from None
