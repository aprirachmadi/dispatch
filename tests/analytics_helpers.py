"""Synthetic DTO fixtures and a paginated in-memory provider, with no model downloads."""

from datetime import UTC, datetime, timedelta

from dispatch.mcp_server.providers.base import RecordNotFound
from dispatch.mcp_server.providers.models import TicketDetail, TicketSearchResult, TicketSummary

NOW = datetime(2026, 10, 12, tzinfo=UTC)


def ticket(ticket_id, subject="Printer offline", company_id=1, status="closed", resolved=None):
    return TicketSummary(
        id=ticket_id,
        company_id=company_id,
        contact_id=None,
        subject=subject,
        description="Synthetic helpdesk request",
        category="hardware",
        priority="medium",
        status=status,
        created_at=NOW - timedelta(days=10),
        updated_at=NOW - timedelta(days=1),
        resolved_at=resolved if resolved is not None else NOW - timedelta(days=1),
    )


class MemoryProvider:
    def __init__(self, records):
        self.records = records
        self.source = TicketDetail(**ticket(100, status="new").model_dump(), notes=[]).model_copy(
            update={"created_at": NOW, "updated_at": NOW, "resolved_at": None}
        )
        self.calls = []

    async def get_ticket(self, ticket_id):
        if ticket_id != self.source.id:
            raise RecordNotFound("Ticket not found")
        return self.source

    async def get_company_context(self, company_id):
        if company_id not in (1, 2):
            raise RecordNotFound("Company not found")

    async def search_tickets(self, filters):
        self.calls.append(filters)
        selected = [
            record
            for record in self.records
            if (filters.company_id is None or record.company_id == filters.company_id)
            and (filters.status is None or record.status == filters.status)
        ]
        selected.sort(key=lambda record: record.id)
        return TicketSearchResult(
            tickets=selected[filters.offset : filters.offset + filters.limit],
            limit=filters.limit,
            offset=filters.offset,
        )


class SemanticStub:
    identity = "semantic-test-model"
    dimensions = 2

    def __init__(self):
        self.calls = 0

    def encode(self, texts):
        self.calls += 1
        return [[0, 1] if "Email" in text else [1, 0] for text in texts]
