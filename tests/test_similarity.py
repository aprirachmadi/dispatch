from datetime import timedelta

import pytest
from analytics_helpers import NOW, MemoryProvider, SemanticStub, ticket

from dispatch.mcp_server.providers.base import RecordNotFound
from dispatch.mcp_server.services.common import AnalyticsError, collect_tickets
from dispatch.mcp_server.services.embeddings import CachedEmbeddings
from dispatch.mcp_server.services.similarity import SimilarityService

pytestmark = pytest.mark.anyio


def service(tmp_path, embedder=None):
    return SimilarityService(
        CachedEmbeddings(embedder or SemanticStub(), tmp_path / "vectors.json")
    )


async def test_ranking_history_cutoff_self_exclusion_and_stable_ties(tmp_path):
    provider = MemoryProvider(
        [
            ticket(1),
            ticket(2, company_id=2),
            ticket(3, subject="Email password"),
            ticket(4, resolved=NOW),
            ticket(5, resolved=NOW + timedelta(hours=1)),
            ticket(6, status="new"),
            ticket(100),
        ]
    )
    result = await service(tmp_path).find(provider, 100, limit=2)
    assert [match.ticket.id for match in result.matches] == [1, 2]
    assert all(match.score == 1.0 for match in result.matches)
    assert result.eligible_tickets == 3
    assert result.history_cutoff == NOW


async def test_company_filter_and_empty_results_do_not_load_model(tmp_path):
    model = SemanticStub()
    provider = MemoryProvider([ticket(1, company_id=2)])
    result = await service(tmp_path, model).find(provider, 100, company_id=1)
    assert result.matches == [] and model.calls == 0
    with pytest.raises(RecordNotFound):
        await service(tmp_path).find(provider, 100, company_id=999)


async def test_complete_pagination_not_first_page_only():
    provider = MemoryProvider([ticket(index) for index in range(1, 251)])
    records = await collect_tickets(provider)
    assert len(records) == 250
    assert [filters.offset for filters in provider.calls] == [0, 100, 200]


async def test_scan_limit_fails_instead_of_partial_report():
    provider = MemoryProvider([ticket(index) for index in range(1, 2002)])
    with pytest.raises(AnalyticsError, match="exceeds 2000"):
        await collect_tickets(provider)


async def test_exact_scan_limit_is_allowed():
    provider = MemoryProvider([ticket(index) for index in range(1, 2001)])
    assert len(await collect_tickets(provider)) == 2000
    assert provider.calls[-1].limit == 1


async def test_naive_or_impossible_timestamps_are_rejected():
    provider = MemoryProvider(
        [ticket(1).model_copy(update={"created_at": NOW.replace(tzinfo=None)})]
    )
    with pytest.raises(AnalyticsError, match="timezone-aware"):
        await collect_tickets(provider)
    provider.records = [ticket(1, resolved=NOW - timedelta(days=20))]
    with pytest.raises(AnalyticsError, match="precedes"):
        await collect_tickets(provider)
