"""MCP payload capture enriches existing SDK spans without extra tool observations."""

import json
from types import SimpleNamespace

import httpx2
import pytest
from langfuse import Langfuse
from mcp import Client
from mcp.shared import _otel
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from dispatch import observability
from dispatch.mcp_server.config import MCPSettings
from dispatch.mcp_server.providers.mock_psa import MockPSAProvider
from dispatch.mcp_server.server import create_server
from dispatch.mcp_server.tracing import ToolTraceMiddleware


@pytest.mark.anyio
async def test_existing_sdk_spans_capture_reads_writes_errors_and_shared_trace(
    monkeypatch, write_client, tmp_path
):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    langfuse = Langfuse(
        base_url="http://localhost:3210",
        public_key="pk-synthetic-mcp-trace",
        secret_key="sk-synthetic",
        tracer_provider=provider,
        span_exporter=exporter,
    )
    monkeypatch.setattr(observability, "get_langfuse", lambda: langfuse)
    monkeypatch.setattr(_otel, "_tracer", provider.get_tracer("mcp-python-sdk"))
    try:
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=write_client.app), base_url="http://psa.test/"
        ) as http:
            server = create_server(
                provider=MockPSAProvider(http, read_only=False),
                settings=MCPSettings(read_only=False, audit_path=tmp_path / "audit.jsonl"),
            )
            with observability.observation("mcp-trace-test") as root:
                async with Client(server) as connection:
                    ticket = await connection.call_tool("get_ticket", {"ticket_id": 1})
                    draft = await connection.call_tool(
                        "draft_reply",
                        {
                            "ticket_id": 1,
                            "body": "Synthetic unsent draft",
                        },
                    )
                    note = await connection.call_tool(
                        "add_internal_note",
                        {
                            "ticket_id": 1,
                            "body": "Synthetic internal note",
                        },
                    )
                    error = await connection.call_tool("get_ticket", {"ticket_id": 9999})
                    assert error.is_error
        langfuse.flush()
        tools = [
            span
            for span in exporter.get_finished_spans()
            if span.attributes.get("gen_ai.operation.name") == "execute_tool"
        ]
        assert len(tools) == 4  # Exactly one SDK tool span per call, including the error.
        assert all(format(span.context.trace_id, "032x") == root.trace_id for span in tools)
        successes = [span for span in tools if span.status.status_code != StatusCode.ERROR]
        for span, result in zip(successes, [ticket, draft, note], strict=True):
            attrs = span.attributes
            assert json.loads(attrs["langfuse.observation.input"])["ticket_id"] == 1
            assert json.loads(attrs["langfuse.observation.output"]) == result.structured_content
            assert span.end_time >= span.start_time
        failure = next(span for span in tools if span.status.status_code == StatusCode.ERROR)
        assert json.loads(failure.attributes["langfuse.observation.input"]) == {"ticket_id": 9999}
        assert json.loads(failure.attributes["langfuse.observation.output"])["isError"] is True
        assert failure.attributes["langfuse.observation.level"] == "ERROR"
    finally:
        langfuse.shutdown()


@pytest.mark.anyio
async def test_exception_output_and_unknown_input_fields_are_redacted(monkeypatch):
    attributes = {}
    span = SimpleNamespace(set_attributes=attributes.update)
    monkeypatch.setattr("dispatch.mcp_server.tracing.get_current_span", lambda: span)
    ctx = SimpleNamespace(
        method="tools/call",
        params={
            "name": "get_ticket",
            "arguments": {"ticket_id": 1, "api_key": "SYNTHETIC-SECRET"},
        },
    )

    async def fail(ctx):
        raise RuntimeError("SYNTHETIC-SECRET provider body")

    with pytest.raises(RuntimeError):
        await ToolTraceMiddleware()(ctx, fail)
    assert "SYNTHETIC-SECRET" not in json.dumps(attributes)
    assert json.loads(attributes["langfuse.observation.input"]) == {"ticket_id": 1}
    assert attributes["langfuse.observation.status_message"] == "RuntimeError"


@pytest.mark.anyio
async def test_capture_failure_preserves_result_and_sanitizes_tool_error(monkeypatch):
    def broken(attributes):
        raise RuntimeError("SYNTHETIC-SECRET exporter failure")

    monkeypatch.setattr(
        "dispatch.mcp_server.tracing.get_current_span",
        lambda: SimpleNamespace(set_attributes=broken),
    )
    ctx = SimpleNamespace(
        method="tools/call", params={"name": "get_ticket", "arguments": {"ticket_id": 1}}
    )
    result = {"isError": True, "content": [{"type": "text", "text": "SYNTHETIC-SECRET"}]}

    async def next_call(ctx):
        return result

    assert await ToolTraceMiddleware()(ctx, next_call) is result


@pytest.mark.anyio
async def test_blocked_tool_output_is_safe_and_analytics_inputs_complete(monkeypatch):
    attributes = {}
    monkeypatch.setattr(
        "dispatch.mcp_server.tracing.get_current_span",
        lambda: SimpleNamespace(set_attributes=attributes.update),
    )
    ctx = SimpleNamespace(
        method="tools/call",
        params={
            "name": "find_similar_tickets",
            "arguments": {"ticket_id": 1, "company_id": 2, "limit": 3},
        },
    )

    async def blocked(ctx):
        return {"isError": True, "content": [{"type": "text", "text": "SYNTHETIC-SECRET"}]}

    await ToolTraceMiddleware()(ctx, blocked)
    assert json.loads(attributes["langfuse.observation.input"]) == ctx.params["arguments"]
    assert attributes["langfuse.observation.level"] == "ERROR"
    assert "SYNTHETIC-SECRET" not in attributes["langfuse.observation.output"]
