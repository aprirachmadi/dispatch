"""Bounded provider scans; never silently compute analytics over an incomplete list."""

from datetime import datetime

from dispatch.domain import TicketStatus
from dispatch.mcp_server.providers.base import ProviderError, TicketProvider
from dispatch.mcp_server.providers.models import TicketSearch, TicketSummary

MAX_SCAN_TICKETS = 2000


class AnalyticsError(ProviderError):
    """A safe analytics failure message for the caller."""


def require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise AnalyticsError("PSA timestamps must be timezone-aware for analytics.")


async def collect_tickets(
    provider: TicketProvider,
    company_id: int | None = None,
    statuses: tuple[TicketStatus | None, ...] = (None,),
) -> list[TicketSummary]:
    """Fetch complete stable-ID pages with a combined 2000-ticket cap and lookahead."""
    if company_id is not None:
        await provider.get_company_context(
            company_id
        )  # Unknown company is not a zero-count report.
    tickets: list[TicketSummary] = []
    seen: set[int] = set()
    for status in statuses:
        offset = 0
        last_id = 0
        while True:
            limit = min(100, MAX_SCAN_TICKETS - len(tickets) + 1)
            result = await provider.search_tickets(
                TicketSearch(company_id=company_id, status=status, limit=limit, offset=offset)
            )
            if len(result.tickets) > limit:
                raise AnalyticsError("PSA exceeded the requested analytics page limit.")
            for ticket in result.tickets:
                if ticket.id <= last_id or ticket.id in seen:
                    raise AnalyticsError("PSA changed or returned unstable pages; retry the scan.")
                if company_id is not None and ticket.company_id != company_id:
                    raise AnalyticsError("PSA returned a ticket outside the requested company.")
                if status is not None and ticket.status != status:
                    raise AnalyticsError("PSA returned a ticket outside the requested status.")
                require_aware(ticket.created_at)
                require_aware(ticket.updated_at)
                if ticket.resolved_at is not None:
                    require_aware(ticket.resolved_at)
                    if ticket.resolved_at < ticket.created_at:
                        raise AnalyticsError("PSA resolution precedes ticket creation.")
                seen.add(ticket.id)
                tickets.append(ticket)
                last_id = ticket.id
                if len(tickets) > MAX_SCAN_TICKETS:
                    raise AnalyticsError(
                        "Analytics scan exceeds 2000 tickets; use a company filter."
                    )
            if len(result.tickets) < limit:
                break
            offset += len(result.tickets)
    return sorted(tickets, key=lambda ticket: ticket.id)
