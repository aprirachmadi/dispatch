import httpx2
import pytest
from pydantic import ValidationError

from dispatch.domain import TicketPriority
from dispatch.mcp_server.providers.base import ProviderError, RecordNotFound
from dispatch.mcp_server.providers.mock_psa import MAX_RESPONSE_BYTES, MockPSAProvider
from dispatch.mcp_server.providers.models import TicketSearch

pytestmark = pytest.mark.anyio


@pytest.fixture
async def provider(client):
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=client.app), base_url="http://psa.test/"
    ) as http:
        yield MockPSAProvider(http)


async def test_provider_reads_real_api(provider):
    results = await provider.search_tickets(TicketSearch(query="PASSWORD", status="new"))
    assert len(results.tickets) == 1
    assert results.limit == 25 and results.offset == 0
    ticket = await provider.get_ticket(1)
    assert ticket.notes[0].body == "Investigating sign-in logs."
    assert ticket.notes_truncated is False
    context = await provider.get_company_context(1)
    assert context.company.name == "Synthetic Maple"
    assert context.company.sla_tier == "gold"
    assert [contact.company_id for contact in context.contacts] == [1]
    assert context.contacts_truncated is False


async def test_provider_combined_search_and_pagination(provider, client):
    for subject in ("Printer A", "Printer B"):
        client.post(
            "/tickets",
            json={
                "company_id": 1,
                "subject": subject,
                "description": "Synthetic",
                "priority": "high",
                "category": "hardware",
            },
        )
    results = await provider.search_tickets(
        TicketSearch(
            query="printer",
            company_id=1,
            category="hardware",
            priority=TicketPriority.HIGH,
            status="new",
            limit=1,
            offset=1,
        )
    )
    assert [ticket.subject for ticket in results.tickets] == ["Printer B"]


@pytest.mark.parametrize("method", ["get_ticket", "get_company_context"])
async def test_provider_missing_records(provider, method):
    with pytest.raises(RecordNotFound, match="not found"):
        await getattr(provider, method)(999)


@pytest.mark.parametrize("value", [0, -1, True, "1", 2**63])
async def test_provider_rejects_invalid_ids_before_http(provider, value):
    with pytest.raises(ValidationError):
        await provider.get_ticket(value)


@pytest.mark.parametrize(
    "values",
    [
        {"query": "   "},
        {"query": "x" * 201},
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
        {"limit": "10"},
        {"offset": -1},
        {"offset": 10001},
        {"priority": "critical"},
        {"status": "done"},
        {"category": "billing"},
        {"company_id": 0},
        {"unexpected": "field"},
    ],
)
async def test_search_contract_validation(values):
    with pytest.raises(ValidationError):
        TicketSearch(**values)


@pytest.mark.parametrize("status", [302, 400, 401, 422, 500])
async def test_http_errors_are_sanitized_and_redirects_not_followed(status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx2.Response(
            status,
            text="SECRET RAW BACKEND ERROR",
            headers={"Location": "http://other.test/private"},
        )

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler), base_url="http://psa.test/"
    ) as http:
        with pytest.raises(ProviderError) as error:
            await MockPSAProvider(http).search_tickets(TicketSearch())
    assert "SECRET" not in str(error.value)
    assert len(requests) == 1 and requests[0].method == "GET"


@pytest.mark.parametrize(
    "exception,message",
    [
        (httpx2.ReadTimeout, "timed out"),
        (httpx2.ConnectError, "Cannot reach"),
    ],
)
async def test_network_errors_are_sanitized(exception, message):
    def handler(request):
        raise exception("SECRET URL OR STACK TRACE", request=request)

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler), base_url="http://psa.test/"
    ) as http:
        with pytest.raises(ProviderError, match=message) as error:
            await MockPSAProvider(http).get_ticket(1)
    assert "SECRET" not in str(error.value)


@pytest.mark.parametrize("body", [b"not json", b"null", b'{"wrong":"schema"}'])
async def test_malformed_response_is_safe(body):
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, content=body)),
        base_url="http://psa.test/",
    ) as http:
        with pytest.raises(ProviderError, match="invalid"):
            await MockPSAProvider(http).search_tickets(TicketSearch())


async def test_response_size_limit():
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(
            lambda request: httpx2.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1))
        ),
        base_url="http://psa.test/",
    ) as http:
        with pytest.raises(ProviderError, match="5 MiB"):
            await MockPSAProvider(http).get_ticket(1)


async def test_notes_are_bounded_with_explicit_truncation(client):
    original = client.get("/tickets/1").json()
    note = original["notes"][0]
    original["notes"] = [{**note, "id": index + 1} for index in range(101)]
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, json=original)),
        base_url="http://psa.test/",
    ) as http:
        ticket = await MockPSAProvider(http).get_ticket(1)
    assert len(ticket.notes) == 100 and ticket.notes_truncated


async def test_contact_truncation_and_get_only():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/companies/1":
            return httpx2.Response(200, json={"id": 1, "name": "Synthetic", "sla_tier": "gold"})
        assert request.url.path == "/contacts"
        assert request.url.params["company_id"] == "1"
        count = 1 if request.url.params.get("offset") else 100
        return httpx2.Response(
            200,
            json=[
                {"id": i + 1, "company_id": 1, "name": "Synthetic", "email": f"{i}@example.test"}
                for i in range(count)
            ],
        )

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler), base_url="http://psa.test/"
    ) as http:
        context = await MockPSAProvider(http).get_company_context(1)
    assert len(context.contacts) == 100 and context.contacts_truncated
    assert len(requests) == 3 and all(request.method == "GET" for request in requests)


async def test_adapter_rejects_over_limit_results(client):
    ticket = client.get("/tickets").json()[0]
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, json=[ticket, ticket])),
        base_url="http://psa.test/",
    ) as http:
        with pytest.raises(ProviderError, match="invalid ticket search"):
            await MockPSAProvider(http).search_tickets(TicketSearch(limit=1))


async def test_adapter_rejects_cross_company_contact():
    def handler(request):
        if request.url.path == "/companies/1":
            return httpx2.Response(200, json={"id": 1, "name": "Synthetic", "sla_tier": "silver"})
        return httpx2.Response(
            200, json=[{"id": 1, "company_id": 2, "name": "Other", "email": "other@example.test"}]
        )

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler), base_url="http://psa.test/"
    ) as http:
        with pytest.raises(ProviderError, match="invalid company context"):
            await MockPSAProvider(http).get_company_context(1)
