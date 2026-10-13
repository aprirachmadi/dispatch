"""The provider boundary; safe writes are opt-in, with no approval or sending methods."""

from typing import Protocol

from dispatch.mcp_server.providers.models import (
    CompanyContext,
    DraftReplyRecord,
    NoteRecord,
    TicketChanges,
    TicketDetail,
    TicketSearch,
    TicketSearchResult,
    TicketSummary,
)


class ProviderError(Exception):
    """A safe, deliberate provider failure whose message may be shown to the client."""


class RecordNotFound(ProviderError):
    """The requested company or ticket does not exist."""


class ReadOnlyError(ProviderError):
    """A mutation was refused by the provider or backend read-only policy."""


class TicketProvider(Protocol):
    """Typed contract with no database, HTTP, or MCP-specific return types."""

    async def search_tickets(self, filters: TicketSearch) -> TicketSearchResult:
        """Search subject/description and intersect filters before stable-ID pagination."""
        ...

    async def get_ticket(self, ticket_id: int) -> TicketDetail:
        """Read one ticket with at most 100 notes, or raise RecordNotFound."""
        ...

    async def get_company_context(self, company_id: int) -> CompanyContext:
        """Read a company, SLA tier, and at most 100 contacts, or raise RecordNotFound."""
        ...

    async def draft_reply(self, ticket_id: int, body: str) -> DraftReplyRecord:
        """Store supplied text as an unsent draft; never generate, approve, or send it."""
        ...

    async def add_internal_note(self, ticket_id: int, body: str) -> NoteRecord:
        """Append an internal note; validate ticket/body and never expose it externally."""
        ...

    async def update_ticket(self, ticket_id: int, changes: TicketChanges) -> TicketSummary:
        """Update category/priority only; do not change status or arbitrary ticket fields."""
        ...
