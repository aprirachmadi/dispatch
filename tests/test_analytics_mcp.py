import json

import pytest
from analytics_helpers import MemoryProvider, SemanticStub, ticket
from mcp import Client

from dispatch.mcp_server.config import MCPSettings
from dispatch.mcp_server.server import create_server

pytestmark = pytest.mark.anyio


@pytest.fixture
async def analytics_client(tmp_path):
    model = SemanticStub()
    provider = MemoryProvider([ticket(1), ticket(2, subject="Email issue", company_id=2)])
    settings = MCPSettings(
        audit_path=tmp_path / "audit.jsonl",
        embedding_cache_path=tmp_path / "vectors.json",
        model_cache_path=tmp_path / "models",
    )
    async with Client(
        create_server(provider=provider, settings=settings, embedder=model)
    ) as client:
        yield client, provider, model, settings


async def test_analytics_discovery_has_precise_read_only_schemas(analytics_client):
    client, _, _, settings = analytics_client
    tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert len(tools) == 8
    for name in ("find_similar_tickets", "weekly_report"):
        tool = tools[name]
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert "untrusted" in tool.description
        assert "ctx" not in tool.input_schema["properties"]
        assert tool.output_schema is not None
    limit = tools["find_similar_tickets"].input_schema["properties"]["limit"]
    assert limit["minimum"] == 1 and limit["maximum"] == 10 and limit["default"] == 5
    assert tools["weekly_report"].input_schema["required"] == ["week_start"]
    assert tools["weekly_report"].input_schema["properties"]["week_start"]["format"] == "date"
    assert not settings.model_cache_path.exists()


async def test_similarity_structured_output_cache_and_audit_in_read_only(analytics_client):
    client, provider, model, settings = analytics_client
    before = list(provider.records)
    result = await client.call_tool("find_similar_tickets", {"ticket_id": 100, "limit": 2})
    assert not result.is_error
    assert [match["ticket"]["id"] for match in result.structured_content["matches"]] == [1, 2]
    assert result.structured_content["model"] == "semantic-test-model"
    assert result.structured_content["eligible_tickets"] == 2
    assert (await client.call_tool("find_similar_tickets", {"ticket_id": 100})).is_error is False
    assert model.calls == 1  # Second call uses the vector cache.
    assert provider.records == before
    events = [json.loads(line) for line in settings.audit_path.read_text().splitlines()]
    assert len(events) == 4
    assert all(event["tool"] == "find_similar_tickets" for event in events)
    assert events[-1]["outcome"] == "success"
    assert "Printer offline" not in settings.embedding_cache_path.read_text()


async def test_weekly_report_never_loads_embedding_model_and_audits_date(analytics_client):
    client, _, model, settings = analytics_client
    result = await client.call_tool("weekly_report", {"week_start": "2026-10-12", "company_id": 1})
    assert not result.is_error
    assert result.structured_content["timezone"] == "UTC"
    assert result.structured_content["week_end_exclusive"] == "2026-10-19"
    assert result.structured_content["scanned_tickets"] == 1
    assert model.calls == 0
    assert not settings.embedding_cache_path.exists() and not settings.model_cache_path.exists()
    events = [json.loads(line) for line in settings.audit_path.read_text().splitlines()]
    assert events[0]["arguments"]["week_start"] == "2026-10-12"
    assert events[1]["outcome"] == "success"


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("find_similar_tickets", {"ticket_id": 0}),
        ("find_similar_tickets", {"ticket_id": True}),
        ("find_similar_tickets", {"ticket_id": 100, "limit": 0}),
        ("find_similar_tickets", {"ticket_id": 100, "limit": 11}),
        ("find_similar_tickets", {"ticket_id": 100, "limit": "5"}),
        ("find_similar_tickets", {"ticket_id": 100, "company_id": 0}),
        ("find_similar_tickets", {"ticket_id": 100, "model_url": "http://untrusted.invalid"}),
        ("find_similar_tickets", {"ticket_id": 999}),
        ("find_similar_tickets", {"ticket_id": 100, "company_id": 999}),
        ("weekly_report", {}),
        ("weekly_report", {"week_start": "2026-10-13"}),
        ("weekly_report", {"week_start": "SECRET-NOT-A-DATE"}),
        ("weekly_report", {"week_start": 1791763200}),
        ("weekly_report", {"week_start": "2026-10-12", "company_id": True}),
        ("weekly_report", {"week_start": "2026-10-12", "company_id": 999}),
        ("weekly_report", {"week_start": "2026-10-12", "sla_compliance": True}),
    ],
)
async def test_invalid_analytics_calls_are_audited_without_model_loading(
    analytics_client, name, arguments
):
    client, _, model, settings = analytics_client
    result = await client.call_tool(name, arguments)
    assert result.is_error
    assert model.calls == 0
    raw = settings.audit_path.read_text()
    assert "SECRET" not in raw and "untrusted.invalid" not in raw
    events = [json.loads(line) for line in raw.splitlines()]
    assert len(events) == 2
    assert events[-1]["outcome"] == "error"


async def test_changed_problem_text_invalidates_cached_source(analytics_client):
    client, provider, model, _ = analytics_client
    assert not (await client.call_tool("find_similar_tickets", {"ticket_id": 100})).is_error
    provider.source = provider.source.model_copy(
        update={"description": "Changed synthetic problem"}
    )
    assert not (await client.call_tool("find_similar_tickets", {"ticket_id": 100})).is_error
    assert model.calls == 2
