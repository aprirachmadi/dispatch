"""Deterministic UTC weekly counts; no SLA claims, historical backlog, or AI narrative."""

from collections import Counter
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta

from pydantic import TypeAdapter

from dispatch.domain import TicketCategory, TicketPriority, TicketStatus
from dispatch.mcp_server.providers.base import TicketProvider
from dispatch.mcp_server.providers.models import RecordID
from dispatch.mcp_server.services.common import collect_tickets, require_aware
from dispatch.mcp_server.services.models import BacklogSnapshot, WeeklyReport, WeekStart


class ReportService:
    def __init__(self, clock: Callable[[], datetime] | None = None):
        self._clock = clock or (lambda: datetime.now(UTC))

    async def weekly(
        self, provider: TicketProvider, week_start: date | str, company_id: int | None = None
    ) -> WeeklyReport:
        monday = TypeAdapter(WeekStart).validate_python(week_start)
        if company_id is not None:
            TypeAdapter(RecordID).validate_python(company_id)
        tickets = await collect_tickets(provider, company_id)
        now = self._clock()
        require_aware(now)
        now = now.astimezone(UTC)
        start = datetime.combine(monday, time.min, tzinfo=UTC)
        end = start + timedelta(days=7)
        opened = [ticket for ticket in tickets if start <= ticket.created_at < end]
        resolved = [
            ticket
            for ticket in tickets
            if ticket.resolved_at is not None and start <= ticket.resolved_at < end
        ]
        categories = Counter(
            ticket.category.value if ticket.category else "unclassified" for ticket in opened
        )
        priorities = Counter(ticket.priority.value for ticket in opened)
        backlog = Counter(
            ticket.status
            for ticket in tickets
            if ticket.created_at <= now
            and ticket.status in (TicketStatus.NEW, TicketStatus.IN_PROGRESS)
        )
        new, active = backlog[TicketStatus.NEW], backlog[TicketStatus.IN_PROGRESS]
        return WeeklyReport(
            week_start=monday,
            week_end_exclusive=end.date(),
            company_id=company_id,
            generated_at=now,
            period_complete=now >= end,
            scanned_tickets=len(tickets),
            opened=len(opened),
            resolved=len(resolved),
            opened_by_category={
                **{category.value: categories[category.value] for category in TicketCategory},
                "unclassified": categories["unclassified"],
            },
            opened_by_priority={
                priority.value: priorities[priority.value] for priority in TicketPriority
            },
            current_backlog=BacklogSnapshot(
                as_of=now, new=new, in_progress=active, total=new + active
            ),
        )
