"""Opt-in artifact/inference smoke tests; never downloaded during the ordinary suite."""

import json
import math
import os
from pathlib import Path

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from test_mcp_stdio import records, stdio_parameters
from test_mcp_stdio import running_psa as running_psa

from dispatch.mcp_server.services.embeddings import MODEL_IDENTITY, FastEmbedder, normalize_vector

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        os.getenv("DISPATCH_TEST_REAL_MODEL") != "true",
        reason="Opt-in: set DISPATCH_TEST_REAL_MODEL=true to download/use the model",
    ),
]


def model_cache():
    # Only public model artifacts are reused; every PSA/vector/audit test path is temporary.
    return Path(
        os.getenv(
            "DISPATCH_TEST_MODEL_CACHE_PATH",
            str(Path(__file__).resolve().parents[1] / "data" / "models"),
        )
    )


async def test_real_model_prefers_related_synthetic_problem():
    model = FastEmbedder(model_cache())
    vectors = model.encode(
        [
            "Front desk printer is offline, nobody can print appointment sheets.",
            "Reception network printer offline; restored its print queue and verified a test page.",
            "New employee needs a mailbox account and a software license for Monday.",
        ]
    )
    assert len(vectors) == 3 and all(len(vector) == 384 for vector in vectors)
    vectors = [normalize_vector(vector, 384) for vector in vectors]
    related = math.fsum(a * b for a, b in zip(vectors[0], vectors[1], strict=True))
    unrelated = math.fsum(a * b for a, b in zip(vectors[0], vectors[2], strict=True))
    assert related > unrelated


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_real_stdio_similarity_reports_audits_and_preserves_psa(
    running_psa, engine, tmp_path, mode
):
    before = records(engine)
    parameters = stdio_parameters(running_psa, tmp_path)
    environment = {**parameters.env, "DISPATCH_MODEL_CACHE_PATH": str(model_cache())}
    parameters = StdioServerParameters(**{**parameters.model_dump(), "env": environment})
    async with Client(parameters, read_timeout_seconds=180, mode=mode) as client:
        report = await client.call_tool("weekly_report", {"week_start": "2026-10-12"})
        assert not report.is_error
        assert report.structured_content["scanned_tickets"] == 60
        assert report.structured_content["opened"] == 18
        assert report.structured_content["resolved"] == 0
        assert report.structured_content["current_backlog"]["total"] == 36
        assert not (tmp_path / "embeddings.json").exists()
        result = await client.call_tool("find_similar_tickets", {"ticket_id": 21, "limit": 3})
        assert not result.is_error
        matches = result.structured_content["matches"]
        assert len(matches) == 3
        assert all(match["ticket"]["id"] != 21 for match in matches)
        assert all(match["ticket"]["status"] in ("resolved", "closed") for match in matches)
        assert any(match["ticket"]["id"] == 29 for match in matches)
        assert result.structured_content["model"] == MODEL_IDENTITY
        assert (tmp_path / "embeddings.json").exists()
        again = await client.call_tool("find_similar_tickets", {"ticket_id": 21, "limit": 3})
        assert not again.is_error and again.structured_content == result.structured_content
        assert (await client.call_tool("weekly_report", {"week_start": "2026-10-13"})).is_error
    assert records(engine) == before
    events = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert len(events) == 8
    assert {event["tool"] for event in events} == {"weekly_report", "find_similar_tickets"}
    assert events[-1]["outcome"] == "error"
