"""The read-only provider boundary; future adapters translate into these contracts."""

from typing import Protocol

from dispatch.mcp_server.providers.models import (
    CompanyContext,
    TicketDetail,
    TicketSearch,
    TicketSearchResult,
)


class ProviderError(Exception):
    """A safe, deliberate provider failure whose message may be shown to the client."""


class RecordNotFound(ProviderError):
    """The requested company or ticket does not exist."""


class TicketProvider(Protocol):
    """Read-only contract, with no database, HTTP, or MCP-specific return types."""

    async def search_tickets(self, filters: TicketSearch) -> TicketSearchResult:
        """Search subject/description and intersect filters before stable-ID pagination."""
        ...

    async def get_ticket(self, ticket_id: int) -> TicketDetail:
        """Read one ticket with at most 100 notes, or raise RecordNotFound."""
        ...

    async def get_company_context(self, company_id: int) -> CompanyContext:
        """Read a company, SLA tier, and at most 100 contacts, or raise RecordNotFound."""
        ...
