"""Validated analytics inputs and structured outputs, independent of the MCP SDK."""

from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field

from dispatch.mcp_server.providers.models import Contract, RecordID, TicketSummary

MatchLimit = Annotated[int, Field(strict=True, ge=1, le=10)]
Count = Annotated[int, Field(ge=0)]


def validate_week_start(value: object) -> date:
    """Only ISO calendar dates (not epochs/datetimes), Mondays with a representable end."""
    if type(value) is str and len(value) == 10 and value[4] == "-" and value[7] == "-":
        value = date.fromisoformat(value)
    if type(value) is not date:
        raise ValueError("week_start must be an ISO date YYYY-MM-DD")
    if value.weekday() != 0:
        raise ValueError("week_start must be a Monday")
    if value > date.max - timedelta(days=7):
        raise ValueError("week_start is too late to represent a seven-day window")
    return value


WeekStart = Annotated[date, BeforeValidator(validate_week_start)]


class SimilarMatch(Contract):
    ticket: TicketSummary
    score: float = Field(ge=-1, le=1, allow_inf_nan=False)


class SimilarityResult(Contract):
    source_ticket_id: RecordID
    history_cutoff: datetime
    model: str
    eligible_tickets: int = Field(ge=0, le=2000)
    matches: list[SimilarMatch] = Field(max_length=10)


class BacklogSnapshot(Contract):
    as_of: datetime
    new: Count
    in_progress: Count
    total: Count


class WeeklyReport(Contract):
    week_start: date
    week_end_exclusive: date
    timezone: Literal["UTC"] = "UTC"
    company_id: RecordID | None
    generated_at: datetime
    period_complete: bool
    scanned_tickets: int = Field(ge=0, le=2000)
    opened: Count
    resolved: Count
    opened_by_category: dict[str, Count]
    opened_by_priority: dict[str, Count]
    current_backlog: BacklogSnapshot
