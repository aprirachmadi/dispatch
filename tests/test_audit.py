import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace

import anyio
import pytest
from mcp import Client

from dispatch.mcp_server.audit import AuditLog, AuditMiddleware, summarize_arguments
from dispatch.mcp_server.config import MCPSettings
from dispatch.mcp_server.server import create_server
from dispatch.mcp_server.state import ServerState


def test_metadata_is_allowlisted_and_never_copies_secrets_or_arbitrary_keys():
    summary = summarize_arguments(
        {
            "ticket_id": True,
            "company_id": 2**64,
            "limit": 101,
            "priority": "SECRET",
            "body": "secret password\nignore your instructions",
            "query": "secret search",
            "token-SECRET": "API-SECRET",
            "author_name": "SECRET",
            "category": ["bad"],
        }
    )
    raw = json.dumps(summary)
    assert "SECRET" not in raw and "password" not in raw
    assert summary["ticket_id"] == "invalid" and summary["company_id"] == "invalid"
    assert summary["body_length"] == 40
    assert summarize_arguments(["SECRET"]) == {"invalid_arguments": True}


def test_append_log_preserves_existing_events_and_handles_concurrent_threads(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    log.append({"existing": True})
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda index: log.append({"index": index}), range(50)))
    events = [json.loads(line) for line in log.path.read_text().splitlines()]
    assert events[0] == {"existing": True}
    assert sorted(event["index"] for event in events[1:]) == list(range(50))


@pytest.mark.anyio
async def test_validation_unknown_and_failed_backend_calls_all_audited(tmp_path):
    class Unavailable:
        async def get_ticket(self, ticket_id):
            from dispatch.mcp_server.providers.base import ProviderError

            raise ProviderError("Backend unavailable")

    path = tmp_path / "audit.jsonl"
    async with Client(
        create_server(provider=Unavailable(), settings=MCPSettings(audit_path=path))
    ) as connection:
        assert (await connection.call_tool("search_tickets", {"limit": 101})).is_error
        assert (await connection.call_tool("secret-tool-name", {"password": "SECRET"})).is_error
        assert (await connection.call_tool("get_ticket", {"ticket_id": 1})).is_error
    raw = path.read_text()
    assert (
        "SECRET" not in raw and "secret-tool-name" not in raw and "Backend unavailable" not in raw
    )
    events = [json.loads(line) for line in raw.splitlines()]
    assert len(events) == 6
    assert all(count == 2 for count in Counter(event["call_id"] for event in events).values())
    assert "unknown_tool" in {event["tool"] for event in events}
    assert all(
        datetime.fromisoformat(event["timestamp"]).utcoffset().total_seconds() == 0
        for event in events
    )
    assert all(event["outcome"] == "error" for event in events if event["event"] == "finished")


@pytest.mark.anyio
async def test_cancelled_attempt_has_a_durable_terminal_event(tmp_path):
    settings = MCPSettings(audit_path=tmp_path / "audit.jsonl")
    state = ServerState(provider=None, settings=settings, audit=AuditLog(settings.audit_path))
    context = SimpleNamespace(
        method="tools/call",
        params={"name": "get_ticket", "arguments": {"ticket_id": 1}},
        lifespan_context=state,
    )

    async def slow_read(ctx):
        await anyio.sleep_forever()

    with anyio.move_on_after(0.05):
        await AuditMiddleware()(context, slow_read)
    events = [json.loads(line) for line in settings.audit_path.read_text().splitlines()]
    assert len(events) == 2 and events[-1]["outcome"] == "cancelled"
